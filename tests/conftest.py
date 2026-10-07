"""Puts the bridge package on the path and provides the shared fakes."""

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "upaq_mqtt/rootfs/usr/lib"))

from upaq_bridge import discovery as d  # noqa: E402

KEY = "00005e005301"


def sensor(**extra):
    device = {
        "id": "5f0c1e2d3a4b5c6d7e8f9a0b",
        "mac": "00:00:5E:00:53:01",
        "name": "Living room",
        "type": "UP-AirQuality",
        "state": "CONNECTED",
        "isConnected": True,
        "firmwareVersion": "1.0.16",
        "latestFirmwareVersion": "1.0.16",
        "fwUpdateState": "upToDate",
        "airQuality": {
            "aqi": {"value": 19, "status": "good"},
            "co2": {"value": 655, "status": "good"},
            "pm2p5": {"value": 4.59, "status": "good"},
        },
        "airQualitySettings": {"ringLedBrightness": 40, "ringLedMetric": 1,
                               "nightModeEnabled": False, "nightModeBrightness": 10,
                               "co2Settings": {"lowThreshold": 800, "highThreshold": 1400}},
        "ledSettings": {"isEnabled": True},
    }
    device.update(extra)
    return device


def frame(obj, deflate=True):
    data = json.dumps(obj).encode()
    if deflate:
        data = zlib.compress(data)
    return struct.pack(">BBBxI", 1, 1, int(deflate), len(data)) + data


def packet(action, changes, deflate=True):
    return frame(action, deflate) + frame(changes, deflate)


class Recorder:
    """Stands in for the MQTT client: records (topic, payload, retain)."""

    def __init__(self):
        self.messages = []

    async def __call__(self, topic, value, retain):
        self.messages.append((topic, value, retain))

    def last(self, topic):
        for t, v, _ in reversed(self.messages):
            if t == topic:
                return v
        return None

    def topics(self):
        return [t for t, _, _ in self.messages]

    def clear(self):
        self.messages.clear()


class FakeProtect:
    def __init__(self):
        self.patches = []
        self.fail = None

    async def patch_sensor(self, sensor_id, changes):
        if self.fail:
            raise self.fail
        self.patches.append((sensor_id, changes))


@pytest.fixture
def ctx():
    return d.Context(prefix="homeassistant", version="2.0.0",
                     config_url="homeassistant://hassio/addon/7047f973_upaq_mqtt/info")
