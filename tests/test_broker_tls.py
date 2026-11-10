"""The broker connection with TLS against a real broker: the options as the
bridge loads them, its TLS context, and the checks of certificate and name.

Needs the broker of tests/mosquitto.conf with certificates from
tests/make-certs.sh, as scripts/check.sh and CI start it:
    MQTT_TEST_TLS_PORT=8883 MQTT_TEST_CERTS=<dir> pytest tests/test_broker_tls.py
"""

import json
import os
import uuid
from pathlib import Path

import aiomqtt
import pytest

from upaq_bridge.config import load, tls_context

PORT = int(os.environ.get("MQTT_TEST_TLS_PORT") or 0)
CERTS = Path(os.environ.get("MQTT_TEST_CERTS") or ".")
pytestmark = pytest.mark.skipif(not PORT, reason="needs a TLS broker in MQTT_TEST_TLS_PORT")


def options(tmp_path, host="localhost", **extra):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"protect_host": "10.0.0.1", "protect_username": "bridge",
                                "protect_password": "pw", "mqtt_host": host,
                                "mqtt_port": PORT, "mqtt_tls": True, **extra}))
    return load(path, ssl_dir=CERTS)


async def round_trip(cfg):
    """Publishes and reads back one message the way the bridge connects."""
    topic = f"upaq-test/{uuid.uuid4().hex}"
    async with aiomqtt.Client(cfg.mqtt_host, cfg.mqtt_port, tls_context=tls_context(cfg),
                              identifier=f"tls-{uuid.uuid4().hex[:8]}", timeout=5) as client:
        await client.subscribe(topic, qos=1)
        await client.publish(topic, "hello", qos=1)
        async for message in client.messages:
            return message.payload.decode()


async def test_tls_with_the_ca_from_the_ssl_folder(tmp_path):
    assert await round_trip(options(tmp_path, mqtt_ca="ca.pem")) == "hello"


async def test_a_certificate_no_trusted_ca_signed_is_refused(tmp_path):
    # Without mqtt_ca only public CAs count, and the test CA is none
    with pytest.raises(aiomqtt.MqttError, match="CERTIFICATE_VERIFY_FAILED"):
        await round_trip(options(tmp_path))


async def test_a_certificate_for_another_name_is_refused(tmp_path):
    # The certificate names localhost, not 127.0.0.1
    with pytest.raises(aiomqtt.MqttError, match="mismatch"):
        await round_trip(options(tmp_path, host="127.0.0.1", mqtt_ca="ca.pem"))
