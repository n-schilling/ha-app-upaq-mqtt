"""Option loading."""

import json

import pytest

from upaq_bridge.config import ConfigError, load

BASE = {"protect_host": "10.0.0.1", "protect_username": "bridge", "protect_password": "pw",
        "verify_ssl": False, "certificate_fingerprint": "", "mqtt_host": "", "mqtt_port": 1883,
        "mqtt_username": "", "mqtt_password": "", "discovery_prefix": "homeassistant",
        "enable_controls": False, "min_interval": 60, "log_level": "info"}


def write(tmp_path, **changes):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({**BASE, **changes}))
    return path


def service():
    return {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "secret"}


def test_broker_from_the_supervisor(tmp_path):
    cfg = load(write(tmp_path), service)
    assert (cfg.mqtt_host, cfg.mqtt_username, cfg.log_level) == ("core-mosquitto", "addons", "INFO")


def test_own_broker_wins(tmp_path):
    cfg = load(write(tmp_path, mqtt_host="broker.lan", mqtt_port=8883), service)
    assert (cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_username) == ("broker.lan", 8883, None)


def test_missing_credentials_are_named(tmp_path):
    with pytest.raises(ConfigError, match="protect_password"):
        load(write(tmp_path, protect_password=""), service)


def test_fingerprint_is_checked(tmp_path):
    with pytest.raises(ConfigError, match="fingerprint"):
        load(write(tmp_path, certificate_fingerprint="AB:CD"), service)
    good = ":".join(["AB"] * 32)
    assert load(write(tmp_path, certificate_fingerprint=good), service).certificate_fingerprint == good
