"""Option loading."""

import json
import ssl

import pytest

from upaq_bridge.config import ConfigError, load, normalize_host, tls_context

BASE = {"protect_host": "10.0.0.1", "protect_username": "bridge", "protect_password": "pw",
        "certificate_check": "pin", "certificate_fingerprint": "", "discovery_prefix": "homeassistant",
        "enable_controls": False, "min_interval": 60, "log_level": "info"}

# A throwaway self-signed CA, only ever loaded, never trusted for anything
CA_PEM = """\
-----BEGIN CERTIFICATE-----
MIIBcDCCARagAwIBAgIUGk2A1Y9zLRuDhbCpJJsaXb/5gkswCgYIKoZIzj0EAwIw
HjEcMBoGA1UEAwwTVVBBUSBjb25maWcgdGVzdCBDQTAeFw0yNjExMTAxOTQxMTha
Fw0zNjExMDcxOTQxMThaMB4xHDAaBgNVBAMME1VQQVEgY29uZmlnIHRlc3QgQ0Ew
WTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAS56wey4AvJJzsDGBqWAXoMSYvPJrvc
6qmC5bkR5qo633VFQqIH4jLYByV/+GTz3/tlN1K7Sc0z2zIwW9kO+6m+ozIwMDAd
BgNVHQ4EFgQUoCq4wE0ctmhHVWOvZoxuge+ha5QwDwYDVR0TAQH/BAUwAwEB/zAK
BggqhkjOPQQDAgNIADBFAiBvLIKU9oeoXc5FcljG0T0F+kkTNh2BkEx8dwtQlkOZ
hwIhAJYcFl0tUMvwgRkFlgxUTJ/9x+YIJnvww8R6uw++AHIB
-----END CERTIFICATE-----
"""


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


def test_tls_of_the_supervisor_broker_is_used(tmp_path):
    cfg = load(write(tmp_path), lambda: {**service(), "port": 8883, "ssl": True})
    assert (cfg.mqtt_port, cfg.mqtt_tls) == (8883, True)
    assert load(write(tmp_path), service).mqtt_tls is False


def test_tls_for_an_own_broker(tmp_path):
    cfg = load(write(tmp_path, mqtt_host="broker.lan", mqtt_tls=True), service)
    assert (cfg.mqtt_port, cfg.mqtt_tls, cfg.mqtt_ca) == (8883, True, None)
    assert tls_context(cfg).verify_mode == ssl.CERT_REQUIRED
    assert tls_context(cfg).check_hostname
    cfg = load(write(tmp_path, mqtt_host="broker.lan", mqtt_port=1884, mqtt_tls=True), service)
    assert cfg.mqtt_port == 1884
    plain = load(write(tmp_path, mqtt_host="broker.lan"), service)
    assert (plain.mqtt_port, plain.mqtt_tls, tls_context(plain)) == (1883, False, None)


def test_mqtt_tls_does_not_change_the_supervisor_broker(tmp_path):
    assert load(write(tmp_path, mqtt_tls=True), service).mqtt_tls is False


def test_ca_from_the_ssl_folder(tmp_path):
    ssl_dir = tmp_path / "ssl"
    ssl_dir.mkdir()
    (ssl_dir / "ca.pem").write_text(CA_PEM)
    cfg = load(write(tmp_path, mqtt_host="broker.lan", mqtt_tls=True, mqtt_ca=" ca.pem "),
               service, ssl_dir)
    assert cfg.mqtt_ca == (ssl_dir / "ca.pem").resolve()
    assert len(tls_context(cfg).get_ca_certs()) == 1
    # Also for the Supervisor's broker when it reports TLS
    cfg = load(write(tmp_path, mqtt_ca="ca.pem"), lambda: {**service(), "ssl": True}, ssl_dir)
    assert cfg.mqtt_ca is not None


@pytest.mark.parametrize("name, match", [
    ("missing.pem", "not found"),
    ("../secret.pem", "must be a file in"),
    ("/etc/passwd", "must be a file in"),
    ("broken.pem", "no readable PEM"),
])
def test_bad_ca_is_named(tmp_path, name, match):
    ssl_dir = tmp_path / "ssl"
    ssl_dir.mkdir()
    (ssl_dir / "broken.pem").write_text("not a certificate")
    (tmp_path / "secret.pem").write_text(CA_PEM)
    with pytest.raises(ConfigError, match=match):
        load(write(tmp_path, mqtt_host="broker.lan", mqtt_tls=True, mqtt_ca=name), service, ssl_dir)


def test_ca_without_tls_is_named(tmp_path):
    with pytest.raises(ConfigError, match="only used with TLS"):
        load(write(tmp_path, mqtt_host="broker.lan", mqtt_ca="ca.pem"), service)
