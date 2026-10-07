"""The bridge against a real MQTT broker: device discovery, the hand-over of
single entity topics, retained states, commands and the last will.

Needs a broker without authentication, e.g. from tests/mosquitto.conf:
    docker run -d -p 1883:1883 -v "$PWD/tests/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" eclipse-mosquitto:2.0.22
    MQTT_TEST_HOST=localhost pytest tests/test_broker.py
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import aiomqtt
import pytest

from conftest import FakeProtect, sensor
from upaq_bridge import discovery as d
from upaq_bridge.__main__ import Router
from upaq_bridge.bridge import Bridge

HOST = os.environ.get("MQTT_TEST_HOST")
pytestmark = pytest.mark.skipif(not HOST, reason="needs a broker in MQTT_TEST_HOST")
LIB = Path(__file__).resolve().parents[1] / "upaq_mqtt/rootfs/usr/lib"


def new_mac():
    """A sensor of its own per test, so tests do not see each other's topics."""
    raw = uuid.uuid4().hex[:12]
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2)), raw


async def retained(topic, wait=1.0):
    """Retained payload of a topic, None when there is none."""
    async with aiomqtt.Client(HOST, identifier=f"check-{uuid.uuid4().hex[:8]}") as client:
        await client.subscribe(topic, qos=1)
        try:
            async with asyncio.timeout(wait):
                async for message in client.messages:
                    if message.retain:
                        return message.payload.decode()
        except TimeoutError:
            return None
    return None


async def publish_retained(topic, payload):
    async with aiomqtt.Client(HOST, identifier=f"seed-{uuid.uuid4().hex[:8]}") as client:
        await client.publish(topic, payload, qos=1, retain=True)


async def run_bridge(ctx, tmp_path, device, controls=False, protect=None):
    """Connects a bridge as __main__ does; returns (client, bridge, router task)."""
    client = aiomqtt.Client(HOST, identifier=f"bridge-{uuid.uuid4().hex[:8]}")
    await client.__aenter__()
    router = Router(client)

    async def publish(topic, value, retain):
        await client.publish(topic, value, qos=1, retain=retain)

    bridge = Bridge(publish, protect or FakeProtect(), ctx, controls=controls, min_interval=0,
                    store=tmp_path / "sensors.json", retained=router.retained, migrate_wait=0.5)
    task = asyncio.create_task(router.run(bridge, controls))
    await asyncio.sleep(0.2)
    await bridge.setup({"sensors": [device], "lastUpdateId": "u1"})
    return client, bridge, task


async def stop(client, task):
    task.cancel()
    await client.__aexit__(None, None, None)


async def test_hand_over_and_retained_states(ctx, tmp_path):
    mac, key = new_mac()
    legacy = f"homeassistant/sensor/protect_air_quality_{key}/co2/config"
    # As versions before 2.1.0 left it, with a trailing newline
    await publish_retained(legacy, json.dumps({"unique_id": f"up_aq_{key}_co2"}) + "\n")
    client, _bridge, task = await run_bridge(ctx, tmp_path, sensor(mac=mac))
    try:
        device = json.loads(await retained(d.device_topic("homeassistant", key)))
        assert device["components"]["co2"]["unique_id"] == f"up_aq_{key}_co2"
        assert await retained(legacy) is None
        assert await retained(f"up_airquality/{key}/co2") == "655"
        assert await retained(f"up_airquality/{key}/availability") == "online"
    finally:
        await stop(client, task)


async def test_commands_reach_protect(ctx, tmp_path):
    mac, key = new_mac()
    protect = FakeProtect()
    client, _bridge, task = await run_bridge(ctx, tmp_path, sensor(mac=mac), controls=True, protect=protect)
    try:
        async with aiomqtt.Client(HOST, identifier=f"ha-{uuid.uuid4().hex[:8]}") as ha:
            await ha.publish(f"up_airquality/{key}/led_brightness/set", "55", qos=1)
        for _ in range(50):
            if protect.patches:
                break
            await asyncio.sleep(0.1)
        assert protect.patches == [(sensor()["id"], {"airQualitySettings": {"ringLedBrightness": 55}})]
        assert await retained(f"up_airquality/{key}/led_brightness/state") == "55"
    finally:
        await stop(client, task)


async def test_last_will_when_the_bridge_dies():
    # A process that connects with the bridge's last will and dies without
    # disconnecting
    code = (
        "import asyncio, os, aiomqtt\n"
        "from upaq_bridge.__main__ import last_will\n"
        "async def main():\n"
        f"    client = aiomqtt.Client({HOST!r}, will=last_will(), identifier='dying-bridge')\n"
        "    await client.__aenter__()\n"
        "    await client.publish(last_will().topic, 'online', qos=1, retain=True)\n"
        "    os._exit(1)\n"
        "asyncio.run(main())\n"
    )
    env = {**os.environ, "PYTHONPATH": str(LIB)}
    subprocess.run([sys.executable, "-c", code], env=env, check=False, timeout=30)
    for _ in range(20):
        if await retained(d.BRIDGE_AVAILABILITY) == "offline":
            break
        await asyncio.sleep(0.2)
    assert await retained(d.BRIDGE_AVAILABILITY) == "offline"
