"""Home Assistant MQTT discovery for UP-AirQuality sensors.

Unique IDs, discovery topics and device identifiers are those of the
UPAQ-MQTT bridge used before 2.0.0, so existing entities and their history
carry over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

BRIDGE_AVAILABILITY = "up_airquality/bridge/availability"
SUPPORT_URL = "https://github.com/n-schilling/ha-app-upaq-mqtt"


@dataclass(frozen=True)
class Metric:
    name: str
    device_class: str | None
    unit: str | None


# Readings under airQuality.<key>.{value,status}; units as before 2.0.0, so
# the long-term statistics keep theirs
METRICS: dict[str, Metric] = {
    "aqi": Metric("AQI", "aqi", None),
    "co2": Metric("CO2", "carbon_dioxide", "ppm"),
    "pm1p0": Metric("PM1.0", "pm1", "µg/m³"),
    "pm2p5": Metric("PM2.5", "pm25", "µg/m³"),
    "pm4p0": Metric("PM4.0", "pm4", "µg/m³"),
    "pm10p0": Metric("PM10", "pm10", "µg/m³"),
    "tvoc": Metric("TVOC Index", None, "idx"),
    "voc": Metric("VOC Index", None, "idx"),
    "nox": Metric("NOx Index", None, None),
    "vape": Metric("Vape Index", None, "%"),
    "temperature": Metric("Temperature", "temperature", "°C"),
    "humidity": Metric("Humidity", "humidity", "%"),
}

# Measurement ranges of the UP-AirQuality from Ubiquiti's data sheet
# (techspecs.ui.com); they bound the threshold controls. TVOC is not in the
# data sheet: its range is kept wide, Protect checks the value it gets.
RANGES: dict[str, tuple[float, float]] = {
    "aqi": (0, 500),
    "vape": (0, 100),
    "co2": (0, 40000),
    "tvoc": (0, 60000),
    "voc": (1, 500),
    "nox": (1, 500),
    "pm1p0": (0, 1000),
    "pm2p5": (0, 1000),
    "pm4p0": (0, 1000),
    "pm10p0": (0, 1000),
    "humidity": (0, 90),
    "temperature": (0, 40),
}
# Fields of airQualitySettings.<reading>Settings
BOUNDS = {"low": "lowThreshold", "high": "highThreshold"}

# ringLedMetric: what the LED ring shows. 1 is Air Quality: three sensors
# that show Air Quality in the Protect app report 1. 0 is the other choice,
# CO2.
LED_METRICS = {"Air Quality": 1, "CO2": 0}


def threshold_metrics(device: dict) -> list[str]:
    """Readings the sensor offers alert thresholds for, as Protect reports
    them: airQualitySettings.<reading>Settings with a low and a high threshold."""
    settings = device.get("airQualitySettings") or {}
    found = []
    for field, value in settings.items():
        reading = field.removesuffix("Settings")
        if (reading != field and reading in RANGES and isinstance(value, dict)
                and all(bound in value for bound in BOUNDS.values())):
            found.append(reading)
    return found


def sensor_key(device: dict) -> str | None:
    """The sensor's MAC (or ID) without separators, lower case."""
    for field in ("mac", "id"):
        value = "".join(ch for ch in str(device.get(field) or "").lower() if ch.isalnum())
        if value:
            return value
    return None


def mac_connection(device: dict) -> str | None:
    mac = sensor_key({"mac": device.get("mac")})
    if not mac or len(mac) != 12 or any(ch not in "0123456789abcdef" for ch in mac):
        return None
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2))


def base_topic(key: str) -> str:
    return f"up_airquality/{key}"


def availability_topic(key: str) -> str:
    return f"{base_topic(key)}/availability"


@dataclass(frozen=True)
class Context:
    prefix: str
    version: str
    config_url: str | None


def device_block(device: dict, name: str, ctx: Context) -> dict[str, Any]:
    key = sensor_key(device)
    block: dict[str, Any] = {
        "identifiers": [f"up_airquality_{key}"],
        "name": name,
        "manufacturer": "Ubiquiti",
        "model": device.get("type") or "UP-AirQuality",
        "sw_version": device.get("firmwareVersion"),
    }
    # Lets the entities join a matching device of the UniFi Protect integration
    if connection := mac_connection(device):
        block["connections"] = [["mac", connection]]
    if ctx.config_url:
        block["configuration_url"] = ctx.config_url
    return block


def device_topic(prefix: str, key: str) -> str:
    return f"{prefix}/device/up_airquality_{key}/config"


def legacy_filter(prefix: str, key: str) -> str:
    """Topics of the single entity discovery used before 2.1.0."""
    return f"{prefix}/+/protect_air_quality_{key}/+/config"


def _entity(platform: str, key: str, uid: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"platform": platform, "name": name, "unique_id": f"up_aq_{key}_{uid}", **extra}


def reading_components(device: dict) -> dict[str, dict]:
    """One sensor per reading the device reports."""
    key = sensor_key(device)
    base = base_topic(key)
    out: dict[str, dict] = {}
    readings = device.get("airQuality") or {}
    for metric_key, metric in METRICS.items():
        if not isinstance(readings.get(metric_key), dict):
            continue
        cfg = _entity("sensor", key, metric_key, metric.name,
                      state_topic=f"{base}/{metric_key}",
                      json_attributes_topic=f"{base}/{metric_key}/attributes",
                      state_class="measurement")
        if metric.device_class:
            cfg["device_class"] = metric.device_class
        if metric.unit:
            cfg["unit_of_measurement"] = metric.unit
        out[metric_key] = cfg
    return out


def diagnostic_components(device: dict) -> dict[str, dict]:
    key = sensor_key(device)
    base = base_topic(key)
    return {
        "firmware_version": _entity("sensor", key, "fw_version", "Firmware Version",
                                    state_topic=f"{base}/firmware_version/state", icon="mdi:chip",
                                    entity_category="diagnostic"),
        "firmware_update": _entity("binary_sensor", key, "fw_update", "Firmware Update Available",
                                   state_topic=f"{base}/firmware_update/state", payload_on="ON",
                                   payload_off="OFF", device_class="update",
                                   entity_category="diagnostic"),
    }


def control_components(device: dict) -> dict[str, dict]:
    """The LED and threshold controls."""
    key = sensor_key(device)
    base = base_topic(key)
    out: dict[str, dict] = {}

    def io(name: str) -> dict:
        return {"command_topic": f"{base}/{name}/set", "state_topic": f"{base}/{name}/state",
                "entity_category": "config"}

    slider = {"min": 0, "max": 100, "step": 1, "mode": "slider"}
    switch = {"payload_on": "ON", "payload_off": "OFF"}
    out["led_brightness"] = _entity("number", key, "led_brightness", "LED Brightness",
                                    **io("led_brightness"), **slider, icon="mdi:brightness-6")
    out["led_metric"] = _entity("select", key, "led_metric", "LED Metric", **io("led_metric"),
                                options=list(LED_METRICS), icon="mdi:led-on")
    out["status_light"] = _entity("switch", key, "status_light", "Status Light",
                                  **io("status_light"), **switch, icon="mdi:led-on")
    out["night_mode"] = _entity("switch", key, "night_mode", "Night Mode", **io("night_mode"),
                                **switch, icon="mdi:weather-night")
    out["night_brightness"] = _entity("number", key, "night_brightness", "Night Mode Brightness",
                                      **io("night_brightness"), **slider, icon="mdi:brightness-3")
    for metric_key in threshold_metrics(device):
        metric = METRICS.get(metric_key)
        low, high = RANGES[metric_key]
        for bound in BOUNDS:
            cfg = _entity("number", key, f"{metric_key}_{bound}_thresh",
                          f"{metric.name if metric else metric_key} {bound.capitalize()} Threshold",
                          command_topic=f"{base}/thresh/{metric_key}/{bound}/set",
                          state_topic=f"{base}/thresh/{metric_key}/{bound}/state",
                          entity_category="config", min=low, max=high, step=1, mode="box",
                          icon="mdi:tune-variant",
                          # Two per reading: available, but not in the way
                          enabled_by_default=False)
            # Physical units only; the indices have none
            if metric and metric.device_class and metric.unit:
                cfg["unit_of_measurement"] = metric.unit
            out[f"{metric_key}_{bound}_threshold"] = cfg
    return out


def device_payload(device: dict, name: str, ctx: Context, controls: bool) -> dict[str, Any]:
    """The one discovery message of a sensor with all its entities."""
    key = sensor_key(device)
    components = {**reading_components(device), **diagnostic_components(device)}
    if controls:
        components.update(control_components(device))
    return {
        "device": device_block(device, name, ctx),
        "origin": {"name": "UP-AirQuality MQTT Bridge", "sw_version": ctx.version,
                   "support_url": SUPPORT_URL},
        "availability": [{"topic": BRIDGE_AVAILABILITY}, {"topic": availability_topic(key)}],
        "availability_mode": "all",
        "qos": 1,
        "components": components,
    }


def state_topics(key: str) -> list[str]:
    """Every state topic a sensor may have, to remove a gone sensor."""
    stub = {"mac": key, "id": key, "airQuality": {m: {} for m in METRICS},
            "airQualitySettings": {f"{m}Settings": dict.fromkeys(BOUNDS.values()) for m in RANGES}}
    comps = {**reading_components(stub), **diagnostic_components(stub), **control_components(stub)}
    topics = [availability_topic(key)]
    for cfg in comps.values():
        topics += [cfg[f] for f in ("state_topic", "json_attributes_topic") if f in cfg]
    return topics
