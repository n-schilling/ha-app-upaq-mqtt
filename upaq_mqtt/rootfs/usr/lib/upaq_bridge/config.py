"""App options from /data/options.json and the MQTT broker of the Supervisor."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

OPTIONS = Path("/data/options.json")
FINGERPRINT = re.compile(r"^([0-9A-Fa-f]{2}:?){31}[0-9A-Fa-f]{2}$")


class ConfigError(Exception):
    """The options are incomplete or invalid."""


@dataclass(frozen=True)
class Config:
    protect_host: str
    protect_username: str
    protect_password: str
    verify_ssl: bool
    certificate_fingerprint: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    discovery_prefix: str
    enable_controls: bool
    min_interval: int
    log_level: str


def supervisor_mqtt() -> dict:
    """The broker the Supervisor knows, e.g. the Mosquitto broker app."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise ConfigError("no MQTT broker set and no Supervisor to ask for one")
    req = urllib.request.Request("http://supervisor/services/mqtt",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)["data"]
    except (OSError, ValueError, KeyError) as err:
        raise ConfigError(f"no MQTT broker available from the Supervisor ({err}); "
                          "install the Mosquitto broker app or set mqtt_host") from err


def load(path: Path = OPTIONS, mqtt_service=supervisor_mqtt) -> Config:
    try:
        opts = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        raise ConfigError(f"{path} not readable: {err}") from err

    missing = [k for k in ("protect_host", "protect_username", "protect_password") if not opts.get(k)]
    if missing:
        raise ConfigError(f"set {', '.join(missing)} on the app's Configuration tab")
    fingerprint = (opts.get("certificate_fingerprint") or "").strip()
    if fingerprint and not FINGERPRINT.match(fingerprint):
        raise ConfigError("certificate_fingerprint must be a SHA-256 fingerprint (64 hex digits)")

    if opts.get("mqtt_host"):
        mqtt = {"host": opts["mqtt_host"], "port": opts.get("mqtt_port") or 1883,
                "username": opts.get("mqtt_username") or None,
                "password": opts.get("mqtt_password") or None}
    else:
        mqtt = mqtt_service()

    return Config(
        protect_host=opts["protect_host"].strip(),
        protect_username=opts["protect_username"],
        protect_password=opts["protect_password"],
        verify_ssl=bool(opts.get("verify_ssl", False)),
        certificate_fingerprint=fingerprint,
        mqtt_host=mqtt["host"],
        mqtt_port=int(mqtt.get("port") or 1883),
        mqtt_username=mqtt.get("username") or None,
        mqtt_password=mqtt.get("password") or None,
        discovery_prefix=opts.get("discovery_prefix") or "homeassistant",
        enable_controls=bool(opts.get("enable_controls", False)),
        min_interval=int(opts.get("min_interval", 60)),
        log_level=str(opts.get("log_level") or "info").upper(),
    )
