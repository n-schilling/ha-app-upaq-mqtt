"""Option loading."""

import json

import pytest

from upaq_bridge.config import ConfigError, load, normalize_host

BASE = {"protect_host": "10.0.0.1", "protect_username": "bridge", "protect_password": "pw",
        "certificate_check": "pin", "certificate_fingerprint": "", "mqtt_host": "", "mqtt_port": 1883,
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


@pytest.mark.parametrize(("raw", "host"), [
    ("192.168.1.1", "192.168.1.1"),
    (" unifi.local ", "unifi.local"),
    ("https://192.168.1.1/", "192.168.1.1"),
    ("https://192.168.1.1/protect/dashboard", "192.168.1.1"),
    ("http://unifi.local", "unifi.local"),
    ("192.168.1.1:8443", "192.168.1.1:8443"),
    ("https://192.168.1.1:443", "192.168.1.1"),
    ("fd00::1", "[fd00::1]"),
    ("https://[fd00::1]:8443/", "[fd00::1]:8443"),
])
def test_host_is_taken_as_people_type_it(raw, host):
    assert normalize_host(raw) == host


@pytest.mark.parametrize("raw", ["https://", "a b", "host:99999", "user@host"])
def test_nonsense_host_is_named(raw):
    with pytest.raises(ConfigError, match="protect_host"):
        normalize_host(raw)


def test_pin_is_the_default(tmp_path):
    options = dict(BASE)
    del options["certificate_check"]
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    assert load(path, service).certificate_check == "pin"


def test_verify_ssl_of_older_versions_means_public_ca(tmp_path):
    cfg = load(write(tmp_path, protect_host="unifi.example.com", verify_ssl=True), service)
    assert cfg.certificate_check == "public_ca"
    assert load(write(tmp_path, verify_ssl=False), service).certificate_check == "pin"


def test_a_fingerprint_always_pins(tmp_path):
    good = ":".join(["ab"] * 32)
    cfg = load(write(tmp_path, certificate_check="accept_any", certificate_fingerprint=good), service)
    assert (cfg.certificate_check, cfg.certificate_fingerprint) == ("pin", good.upper())


def test_public_ca_needs_a_name_not_an_ip_address(tmp_path):
    with pytest.raises(ConfigError, match="not an IP address"):
        load(write(tmp_path, certificate_check="public_ca"), service)
    assert load(write(tmp_path, certificate_check="public_ca", protect_host="https://unifi.example.com/"),
                service).protect_host == "unifi.example.com"


def test_unknown_check_is_named(tmp_path):
    with pytest.raises(ConfigError, match="certificate_check"):
        load(write(tmp_path, certificate_check="maybe"), service)
