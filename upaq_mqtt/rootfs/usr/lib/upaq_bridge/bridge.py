"""Follows the UP-AirQuality sensors in Protect and mirrors them to MQTT.

Each reading has its own retained state topic and is only sent when it
changed, at most once per min_interval per reading; the latest value is
always the one sent. Settings, firmware and connection state are sent when
they change. Commands from Home Assistant are written back to Protect.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import discovery as d
from .protect import AuthError, Protect, ProtectError, decode_update

LOGGER = logging.getLogger(__name__)

SENSOR_TYPE = "UP-AirQuality"
FIRMWARE_FIELDS = ("firmwareVersion", "latestFirmwareVersion", "fwUpdateState")
# Retry delays after errors, in seconds
BACKOFF = (5, 15, 60, 300)

Publish = Callable[[str, str, bool], Awaitable[None]]
# Reads the retained messages on topic filters: topic -> payload
Retained = Callable[[list[str]], Awaitable[dict[str, str]]]
MIGRATE = json.dumps({"migrate_discovery": True})


class Resync(Exception):
    """Sensors were added or removed in Protect: read the bootstrap again."""


def deep_merge(base: dict, changes: dict) -> None:
    for field, value in changes.items():
        if isinstance(value, dict) and isinstance(base.get(field), dict):
            deep_merge(base[field], value)
        else:
            base[field] = value


def payload(value: Any) -> str:
    """A reading as MQTT payload: 4.59, 656, or the text itself."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, str) else json.dumps(value)


def display_names(devices: list[dict]) -> dict[str, str]:
    """Device names by sensor key; equal names get the end of the MAC."""
    names = {d.sensor_key(dev): dev.get("name") or "Protect Air Quality" for dev in devices}
    seen: dict[str, int] = {}
    for name in names.values():
        seen[name] = seen.get(name, 0) + 1
    return {key: f"{name} ({key[-6:]})" if seen[name] > 1 else name for key, name in names.items()}


def is_connected(device: dict) -> bool:
    if "isConnected" in device:
        return bool(device["isConnected"])
    return device.get("state", "CONNECTED") == "CONNECTED"


class Bridge:
    def __init__(self, publish: Publish, protect: Protect, ctx: d.Context, *,
                 controls: bool, min_interval: int, store: Path,
                 retained: Retained | None = None, migrate_wait: float = 2.0) -> None:
        self._publish = publish
        self._retained = retained
        self._migrate_wait = migrate_wait
        self._protect = protect
        self._ctx = ctx
        self._controls = controls
        self._min_interval = min_interval
        self._store = store
        self.sensors: dict[str, dict] = {}       # sensor key -> Protect device
        self._ids: dict[str, str] = {}           # Protect id -> sensor key
        self._names: dict[str, str] = {}
        self._sent: dict[str, str] = {}          # topic -> last payload sent
        self._last: dict[str, float] = {}        # reading topic -> time sent
        self._pending: dict[str, str] = {}       # reading topic -> held payload
        self._wake = asyncio.Event()
        self._components: dict[str, set[str]] = {}   # sensor key -> announced entities

    # --- MQTT ----------------------------------------------------------------
    async def _send(self, topic: str, value: str, retain: bool = True) -> None:
        await self._publish(topic, value, retain)
        self._sent[topic] = value
        LOGGER.debug("%s = %s", topic, value)

    async def _changed(self, topic: str, value: str) -> None:
        if self._sent.get(topic) != value:
            await self._send(topic, value)

    async def _reading(self, topic: str, value: str, now: float) -> None:
        """Sends a changed reading, or holds it until min_interval has passed."""
        if self._sent.get(topic) == value:
            self._pending.pop(topic, None)
            return
        if now >= self._last.get(topic, -math.inf) + self._min_interval:
            self._pending.pop(topic, None)
            self._last[topic] = now
            await self._send(topic, value)
        else:
            self._pending[topic] = value
            self._wake.set()

    async def flush_loop(self) -> None:
        """Sends held readings once their interval has passed."""
        loop = asyncio.get_running_loop()
        while True:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
            due = min(self._last[t] + self._min_interval for t in self._pending)
            await asyncio.sleep(max(0.0, due - loop.time()))
            now = loop.time()
            for topic, value in list(self._pending.items()):
                if now >= self._last[topic] + self._min_interval:
                    del self._pending[topic]
                    self._last[topic] = now
                    await self._send(topic, value)

    # --- Sensors ---------------------------------------------------------------
    def _known(self) -> set[str]:
        try:
            return set(json.loads(self._store.read_text()))
        except (OSError, ValueError):
            return set()

    async def setup(self, bootstrap: dict) -> None:
        devices = [dev for dev in bootstrap.get("sensors") or []
                   if isinstance(dev, dict) and dev.get("type") == SENSOR_TYPE
                   and dev.get("id") and d.sensor_key(dev)]
        self.sensors = {d.sensor_key(dev): dev for dev in devices}
        self._ids = {dev["id"]: key for key, dev in self.sensors.items()}
        self._names = display_names(devices)
        LOGGER.info("Following %d UP-AirQuality sensor(s): %s", len(self.sensors),
                    ", ".join(self._names.values()) or "none")

        # What the broker holds from earlier runs: the device messages, and
        # the single entity topics of the versions before 2.1.0
        prefix = self._ctx.prefix
        known = self._known() | set(self.sensors)
        filters = [f for key in known for f in (d.device_topic(prefix, key), d.legacy_filter(prefix, key))]
        held = await self._retained(filters) if self._retained and filters else {}

        def singles(key: str) -> list[str]:
            node = f"protect_air_quality_{key}"
            return [t for t, v in held.items() if v and t.split("/")[2:3] == [node]]

        # Sensors removed from Protect since the last run: remove their entities
        for key in known - set(self.sensors):
            LOGGER.info("Sensor %s is gone from Protect, removing its entities", key)
            for topic in [d.device_topic(prefix, key), *singles(key), *d.state_topics(key)]:
                await self._send(topic, "")
        try:
            self._store.write_text(json.dumps(sorted(self.sensors)))
        except OSError as err:
            LOGGER.warning("Could not save the sensor list: %s", err)

        # Entities announced one by one before: Home Assistant hands them over
        # to the device message, with entity IDs and history
        moved = [t for key in self.sensors for t in singles(key)]
        for topic in moved:
            await self._send(topic, MIGRATE)
        for key in self.sensors:
            await self._announce(key, held.get(d.device_topic(prefix, key)))
        if moved:
            await asyncio.sleep(self._migrate_wait)
            for topic in moved:
                await self._send(topic, "")
            LOGGER.info("%d entities moved to device discovery", len(moved))

        now = asyncio.get_running_loop().time()
        for key in self.sensors:
            await self._update(key, self.sensors[key], now, initial=True)

    async def _announce(self, key: str, before: str | None = None) -> None:
        """Sends the sensor's device message when it changed.

        Entities of the previous message that are gone (controls switched
        off) are first sent with their platform only, which removes them.
        """
        payload = d.device_payload(self.sensors[key], self._names[key], self._ctx, self._controls)
        self._components[key] = set(payload["components"])
        topic = d.device_topic(self._ctx.prefix, key)
        try:
            old = (json.loads(before) if before else {}).get("components") or {}
        except (ValueError, AttributeError):
            old = {}
        gone = {k: {"platform": v.get("platform") or v.get("p")} for k, v in old.items()
                if k not in payload["components"] and isinstance(v, dict)}
        if gone:
            removal = {**payload, "components": {**payload["components"], **gone}}
            await self._send(topic, json.dumps(removal))
        await self._changed(topic, json.dumps(payload))

    async def _update(self, key: str, changes: dict, now: float, initial: bool = False) -> None:
        """Publishes what changed on one sensor."""
        device = self.sensors[key]
        base = d.base_topic(key)
        if initial or "isConnected" in changes or "state" in changes:
            await self._changed(d.availability_topic(key), "online" if is_connected(device) else "offline")
        if "name" in changes and not initial:
            self._names = display_names(list(self.sensors.values()))
            await self._announce(key)
        if isinstance(changes.get("airQuality"), dict):
            if not initial and any(m in d.METRICS and m not in self._components.get(key, ())
                                   for m in changes["airQuality"]):
                await self._announce(key)   # a reading the sensor did not report before
            for metric, entry in changes["airQuality"].items():
                if metric not in d.METRICS or not isinstance(entry, dict):
                    continue
                full = device["airQuality"].get(metric) or {}
                if full.get("value") is not None:
                    await self._reading(f"{base}/{metric}", payload(full["value"]), now)
                if full.get("status") is not None:
                    await self._changed(f"{base}/{metric}/attributes",
                                        json.dumps({"status": full["status"]}))
        if initial or any(f in changes for f in FIRMWARE_FIELDS):
            await self._firmware(key)
        if self._controls and (initial or "airQualitySettings" in changes or "ledSettings" in changes):
            await self._settings(key)

    async def _firmware(self, key: str) -> None:
        device = self.sensors[key]
        base = d.base_topic(key)
        fw, latest = device.get("firmwareVersion"), device.get("latestFirmwareVersion")
        if fw:
            await self._changed(f"{base}/firmware_version/state", str(fw))
        update = device.get("fwUpdateState") not in (None, "upToDate") or bool(latest and fw and latest != fw)
        await self._changed(f"{base}/firmware_update/state", "ON" if update else "OFF")

    async def _settings(self, key: str) -> None:
        device = self.sensors[key]
        base = d.base_topic(key)
        aqs = device.get("airQualitySettings") or {}
        led = device.get("ledSettings") or {}
        reverse = {v: k for k, v in d.LED_METRICS.items()}
        values = {
            "led_brightness": aqs.get("ringLedBrightness"),
            "led_metric": reverse.get(aqs.get("ringLedMetric")),
            "status_light": None if led.get("isEnabled") is None else ("ON" if led["isEnabled"] else "OFF"),
            "night_mode": None if aqs.get("nightModeEnabled") is None else ("ON" if aqs["nightModeEnabled"] else "OFF"),
            "night_brightness": aqs.get("nightModeBrightness"),
        }
        for name, value in values.items():
            if value is not None:
                await self._changed(f"{base}/{name}/state", payload(value))
        for metric in d.threshold_metrics(device):
            limits = aqs.get(f"{metric}Settings") or {}
            for bound, field in d.BOUNDS.items():
                if limits.get(field) is not None:
                    await self._changed(f"{base}/thresh/{metric}/{bound}/state", payload(limits[field]))

    async def handle_packet(self, data: bytes) -> None:
        try:
            decoded = decode_update(data, "sensor", self._ids)
        except ValueError as err:
            LOGGER.debug("Undecodable update packet: %s", err)
            return
        if not decoded:
            return
        action, changes = decoded
        if action.get("action") in ("add", "remove"):
            raise Resync
        key = self._ids.get(action.get("id"))
        if key is None or not changes:
            return
        deep_merge(self.sensors[key], changes)
        await self._update(key, changes, asyncio.get_running_loop().time())

    async def offline(self) -> None:
        """Protect is out of reach: the readings are no longer current."""
        for key in self.sensors:
            await self._changed(d.availability_topic(key), "offline")

    # --- Commands --------------------------------------------------------------
    async def handle_command(self, topic: str, raw: str) -> None:
        """up_airquality/<key>/<control>/set or .../thresh/<metric>/<bound>/set"""
        parts = topic.split("/")
        key = parts[1] if len(parts) > 2 else None
        if not self._controls or key not in self.sensors or parts[-1] != "set":
            return
        device = self.sensors[key]
        value = raw.strip()
        try:
            changes, state_topic, state = self._command(parts[2:-1], value, device)
        except (ValueError, KeyError) as err:
            LOGGER.warning("Ignoring %s = %r: %s", topic, value, err)
            return
        try:
            await self._protect.patch_sensor(device["id"], changes)
        except ProtectError as err:
            LOGGER.error("Could not change %s: %s", self._names[key], err)
            return
        deep_merge(device, changes)
        await self._changed(f"{d.base_topic(key)}/{state_topic}", state)
        LOGGER.info("%s: %s set to %s", self._names[key], "/".join(parts[2:-1]), state)

    @staticmethod
    def _command(path: list[str], value: str, device: dict) -> tuple[dict, str, str]:
        def percent() -> int:
            number = float(value)
            if not 0 <= number <= 100:
                raise ValueError("not within 0 to 100")
            return int(round(number))

        def on_off() -> bool:
            if value.upper() not in ("ON", "OFF"):
                raise ValueError("not ON or OFF")
            return value.upper() == "ON"

        match path:
            case ["led_brightness"]:
                v = percent()
                return {"airQualitySettings": {"ringLedBrightness": v}}, "led_brightness/state", str(v)
            case ["led_metric"]:
                return ({"airQualitySettings": {"ringLedMetric": d.LED_METRICS[value]}},
                        "led_metric/state", value)
            case ["status_light"]:
                on = on_off()
                return {"ledSettings": {"isEnabled": on}}, "status_light/state", "ON" if on else "OFF"
            case ["night_mode"]:
                on = on_off()
                return ({"airQualitySettings": {"nightModeEnabled": on}}, "night_mode/state",
                        "ON" if on else "OFF")
            case ["night_brightness"]:
                v = percent()
                return {"airQualitySettings": {"nightModeBrightness": v}}, "night_brightness/state", str(v)
            case ["thresh", metric, bound]:
                if metric not in d.threshold_metrics(device) or bound not in d.BOUNDS:
                    raise ValueError("no such threshold")
                low, high = d.RANGES[metric]
                number = float(value)
                if not low <= number <= high:
                    raise ValueError(f"not within {low} to {high}")
                v: int | float = int(number) if number.is_integer() else number
                return ({"airQualitySettings": {f"{metric}Settings": {d.BOUNDS[bound]: v}}},
                        f"thresh/{metric}/{bound}/state", payload(v))
        raise ValueError("unknown control")

    # --- Protect session ---------------------------------------------------------
    async def follow_protect(self) -> None:
        """Logs in, reads the bootstrap and follows the updates; never returns."""
        failures = 0
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self._protect.login()
                bootstrap = await self._protect.bootstrap()
                await self.setup(bootstrap)
                if not self.sensors:
                    LOGGER.warning("Protect has no UP-AirQuality sensor; checking again later")
                async with contextlib.aclosing(
                        self._protect.updates(str(bootstrap.get("lastUpdateId", "")))) as packets:
                    async for packet in packets:
                        await self.handle_packet(packet)
                LOGGER.info("Protect closed the update stream")
            except Resync:
                LOGGER.info("Sensors changed in Protect, reading them again")
                continue
            except AuthError as err:
                LOGGER.error("Protect: %s", err)
                await self.offline()
                failures = len(BACKOFF) - 1   # do not hammer the login
            except ProtectError as err:
                LOGGER.warning("Protect: %s", err)
                await self.offline()
            except (KeyError, TypeError, ValueError, AttributeError):
                # Data Protect sent in an unexpected shape: start over instead of
                # stopping the bridge
                LOGGER.exception("Unexpected data from Protect")
                await self.offline()
            # A connection that held for a while resets the backoff
            if asyncio.get_running_loop().time() - started > 120:
                failures = 0
            delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
            failures += 1
            LOGGER.info("Connecting to Protect again in %d s", delay)
            await asyncio.sleep(delay)
