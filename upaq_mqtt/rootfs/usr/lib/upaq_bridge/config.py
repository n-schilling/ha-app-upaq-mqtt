"""App options from /data/options.json and the MQTT broker of the Supervisor."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

OPTIONS = Path("/data/options.json")
FINGERPRINT = re.compile(r"^([0-9A-Fa-f]{2}:?){31}[0-9A-Fa-f]{2}$")
# How the console's certificate is checked
CHECKS = ("pin", "accept_any", "public_ca")


class ConfigError(Exception):
    """The options are incomplete or invalid."""


@dataclass(frozen=True)
class Config:
    protect_host: str              # host[:port], IPv6 in brackets
    protect_username: str
    protect_password: str
    certificate_check: str
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


def normalize_host(raw: str) -> str:
    """host[:port] from what people type: a name, an IP address, or a URL
    such as https://192.168.1.1/protect; scheme and path are dropped."""
    text = raw.strip()
    if not text:
        raise ConfigError("set protect_host on the app's Configuration tab")
    try:
        ipaddress.IPv6Address(text)
        text = f"[{text}]"           # a bare IPv6 address
    except ValueError:
        pass
    if "://" not in text:
        text = f"https://{text}"
    try:
        url = urlsplit(text)
        port = url.port
    except ValueError as err:
        raise ConfigError(f"protect_host {raw!r} is no host name or IP address: {err}") from err
    host = url.hostname
    if not host or url.username is not None or any(c in host for c in " /@"):
        raise ConfigError(f"protect_host {raw!r} is no host name or IP address")
    if ":" in host:          # IPv6
        host = f"[{host}]"
    return f"{host}:{port}" if port and port != 443 else host


def is_ip_address(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host[1:host.index("]")]
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def load(path: Path = OPTIONS, mqtt_service=supervisor_mqtt) -> Config:
    try:
        opts = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        raise ConfigError(f"{path} not readable: {err}") from err

    missing = [k for k in ("protect_host", "protect_username", "protect_password") if not opts.get(k)]
    if missing:
        raise ConfigError(f"set {', '.join(missing)} on the app's Configuration tab")
    fingerprint = (opts.get("certificate_fingerprint") or "").strip().upper()
    if fingerprint and not FINGERPRINT.match(fingerprint):
        raise ConfigError("certificate_fingerprint must be a SHA-256 fingerprint (64 hex digits)")
    host = normalize_host(str(opts["protect_host"]))

    check = str(opts.get("certificate_check") or "pin")
    if check not in CHECKS:
        raise ConfigError(f"certificate_check must be one of {', '.join(CHECKS)}")
    # verify_ssl before 2.4.0: true asked for a certificate from a public CA
    if opts.get("verify_ssl") is True and check == "pin" and not fingerprint:
        check = "public_ca"
    # A fingerprint set by hand is always the one pinned
    if fingerprint:
        check = "pin"
    if check == "public_ca" and is_ip_address(host):
        raise ConfigError("certificate_check public_ca needs protect_host to be the name on the "
                          "console's certificate, not an IP address; or choose pin")

    if opts.get("mqtt_host"):
        mqtt = {"host": opts["mqtt_host"], "port": opts.get("mqtt_port") or 1883,
                "username": opts.get("mqtt_username") or None,
                "password": opts.get("mqtt_password") or None}
    else:
        mqtt = mqtt_service()

    return Config(
        protect_host=host,
        protect_username=opts["protect_username"],
        protect_password=opts["protect_password"],
        certificate_check=check,
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
