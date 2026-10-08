"""Runs the bridge: one MQTT connection with a last will, one Protect session."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import aiohttp
import aiomqtt

from . import config as cfg_module
from . import discovery as d
from .bridge import BACKOFF, Bridge
from .protect import PinStore, Protect

LOGGER = logging.getLogger("upaq_bridge")
STORE = Path("/data/sensors.json")
PINNED = Path("/data/certificate.json")


def last_will() -> aiomqtt.Will:
    """Marks every entity unavailable when the bridge dies without a goodbye."""
    return aiomqtt.Will(d.BRIDGE_AVAILABILITY, "offline", qos=1, retain=True)


async def run(cfg: cfg_module.Config) -> None:
    slug = os.environ.get("HOSTNAME", "upaq-mqtt").replace("-", "_")
    ctx = d.Context(prefix=cfg.discovery_prefix, version=os.environ.get("APP_VERSION", "unknown"),
                    config_url=f"homeassistant://hassio/addon/{slug}/info"
                    if os.environ.get("SUPERVISOR_TOKEN") else None)
    will = last_will()
    failures = 0
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
        protect = Protect(http, cfg.protect_host, cfg.protect_username, cfg.protect_password,
                          cfg.certificate_check, cfg.certificate_fingerprint, PinStore(PINNED))
        while True:
            try:
                async with aiomqtt.Client(cfg.mqtt_host, cfg.mqtt_port, username=cfg.mqtt_username,
                                          password=cfg.mqtt_password, will=will,
                                          identifier=f"{slug}-bridge") as mqtt:
                    LOGGER.info("Connected to MQTT %s:%d", cfg.mqtt_host, cfg.mqtt_port)
                    failures = 0

                    router = Router(mqtt)

                    async def publish(topic: str, value: str, retain: bool) -> None:
                        await mqtt.publish(topic, value, qos=1, retain=retain)

                    bridge = Bridge(publish, protect, ctx, controls=cfg.enable_controls,
                                    min_interval=cfg.min_interval, store=STORE,
                                    retained=router.retained)
                    await publish(d.BRIDGE_AVAILABILITY, "online", True)
                    await bridge.announce_bridge()
                    try:
                        async with asyncio.TaskGroup() as tasks:
                            tasks.create_task(router.run(bridge, cfg.enable_controls))
                            tasks.create_task(bridge.follow_protect())
                            tasks.create_task(bridge.flush_loop())
                    finally:
                        # A regular stop: the last will only covers a dying bridge
                        try:
                            await asyncio.shield(publish(d.BRIDGE_AVAILABILITY, "offline", True))
                        except aiomqtt.MqttError:
                            pass
            except* aiomqtt.MqttError as group:
                delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
                failures += 1
                LOGGER.warning("MQTT: %s; connecting again in %d s", group.exceptions[0], delay)
                await asyncio.sleep(delay)


class Router:
    """The one reader of the MQTT messages: commands go to the bridge,
    retained discovery messages to a running retained() call."""

    def __init__(self, mqtt: aiomqtt.Client) -> None:
        self._mqtt = mqtt
        self._collect: dict[str, str] | None = None

    async def retained(self, filters: list[str], wait: float = 2.0) -> dict[str, str]:
        collected: dict[str, str] = {}
        self._collect = collected
        try:
            for topic_filter in filters:
                await self._mqtt.subscribe(topic_filter, qos=1)
            await asyncio.sleep(wait)
            for topic_filter in filters:
                await self._mqtt.unsubscribe(topic_filter)
        finally:
            self._collect = None
        return collected

    async def run(self, bridge: Bridge, controls: bool) -> None:
        if controls:
            await self._mqtt.subscribe("up_airquality/+/+/set", qos=1)
            await self._mqtt.subscribe("up_airquality/+/thresh/+/+/set", qos=1)
            await self._mqtt.subscribe("up_airquality/+/events/+/set", qos=1)
        if bridge.can_accept:
            await self._mqtt.subscribe(d.ACCEPT_CERTIFICATE, qos=1)
        async for message in self._mqtt.messages:
            topic = str(message.topic)
            raw = message.payload.decode(errors="replace") if isinstance(message.payload, bytes) \
                else str(message.payload or "")
            if topic.endswith("/config"):
                if self._collect is not None and message.retain:
                    self._collect[topic] = raw
            elif topic == d.ACCEPT_CERTIFICATE:
                if bridge.can_accept and raw == "PRESS":
                    await bridge.accept_certificate()
            elif controls and topic.endswith("/set"):
                await bridge.handle_command(topic, raw)


def main() -> None:
    try:
        cfg = cfg_module.load()
    except cfg_module.ConfigError as err:
        logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
        LOGGER.error("%s", err)
        sys.exit(1)
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("aiomqtt").setLevel(logging.WARNING)

    async def main_task() -> None:
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await run(cfg)
        except asyncio.CancelledError:
            LOGGER.info("Stopped")

    LOGGER.info("UP-AirQuality MQTT Bridge %s", os.environ.get("APP_VERSION", ""))
    asyncio.run(main_task())


if __name__ == "__main__":
    main()
