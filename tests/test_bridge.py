"""The bridge between Protect updates, MQTT and Home Assistant commands."""

import asyncio
import json

import pytest

from conftest import KEY, FakeProtect, Recorder, packet, sensor
from upaq_bridge.bridge import Bridge, Resync
from upaq_bridge.protect import AuthError, CertificateMismatch, ProtectError

BASE = f"up_airquality/{KEY}"
ID = "5f0c1e2d3a4b5c6d7e8f9a0b"
DEVICE = f"homeassistant/device/up_airquality_{KEY}/config"


def components(mqtt, topic=DEVICE):
    return json.loads(mqtt.last(topic))["components"]


def update(changes, action="update", sensor_id=ID):
    return packet({"action": action, "modelKey": "sensor", "id": sensor_id}, changes)


@pytest.fixture
def make(ctx, tmp_path):
    def build(controls=False, min_interval=60, devices=None, held=None):
        mqtt, protect = Recorder(), FakeProtect()
        asked = []

        async def retained(filters):
            asked.extend(filters)
            return dict(held or {})

        bridge = Bridge(mqtt, protect, ctx, controls=controls, min_interval=min_interval,
                        store=tmp_path / "sensors.json", retained=retained, migrate_wait=0)
        bridge.asked = asked
        return bridge, mqtt, protect, {"sensors": devices if devices is not None else [sensor()],
                                       "lastUpdateId": "u1"}
    return build


async def test_setup_announces_and_publishes_everything(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    assert mqtt.last(f"{BASE}/availability") == "online"
    assert mqtt.last(f"{BASE}/co2") == "655"
    assert mqtt.last(f"{BASE}/pm2p5") == "4.59"
    assert json.loads(mqtt.last(f"{BASE}/co2/attributes")) == {"status": "good"}
    assert mqtt.last(f"{BASE}/firmware_version/state") == "1.0.16"
    assert mqtt.last(f"{BASE}/firmware_update/state") == "OFF"
    assert components(mqtt)["co2"]["unique_id"] == f"up_aq_{KEY}_co2"
    assert [t for t in mqtt.topics() if t.endswith("/config")] == [DEVICE]
    # Controls off: no control entities, no settings published
    assert "led_brightness" not in components(mqtt)
    assert mqtt.last(f"{BASE}/led_brightness/state") is None
    assert all(retain for _, _, retain in mqtt.messages)


async def test_only_changed_readings_are_sent(make):
    bridge, mqtt, _, boot = make(min_interval=0)
    await bridge.setup(boot)
    mqtt.clear()
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": 655}, "aqi": {"value": 20}}}))
    assert mqtt.topics() == [f"{BASE}/aqi"]


async def test_throttle_holds_and_sends_the_latest(make):
    bridge, mqtt, _, boot = make(min_interval=1)
    await bridge.setup(boot)
    flusher = asyncio.create_task(bridge.flush_loop())
    mqtt.clear()
    for value in (660, 670, 680):
        await bridge.handle_packet(update({"airQuality": {"co2": {"value": value}}}))
    assert mqtt.last(f"{BASE}/co2") is None
    await asyncio.sleep(1.2)
    assert [v for t, v, _ in mqtt.messages if t == f"{BASE}/co2"] == ["680"]
    # Back to the value already sent: nothing held, nothing sent
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": 680}}}))
    flusher.cancel()


async def test_status_change_is_sent_at_once(make):
    bridge, mqtt, _, boot = make(min_interval=3600)
    await bridge.setup(boot)
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": 1500, "status": "poor"}}}))
    assert json.loads(mqtt.last(f"{BASE}/co2/attributes")) == {"status": "poor"}


async def test_new_reading_announces_its_entity(make):
    bridge, mqtt, _, boot = make(min_interval=0)
    await bridge.setup(boot)
    await bridge.handle_packet(update({"airQuality": {"voc": {"value": 107, "status": "good"}}}))
    assert components(mqtt)["voc"]["name"] == "VOC Index"
    assert mqtt.last(f"{BASE}/voc") == "107"


async def test_disconnected_sensor_turns_unavailable(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    await bridge.handle_packet(update({"isConnected": False, "state": "DISCONNECTED"}))
    assert mqtt.last(f"{BASE}/availability") == "offline"


async def test_rename_updates_the_device(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    await bridge.handle_packet(update({"name": "Office"}))
    assert json.loads(mqtt.last(DEVICE))["device"]["name"] == "Office"


async def test_firmware_update_available(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    await bridge.handle_packet(update({"latestFirmwareVersion": "1.0.17"}))
    assert mqtt.last(f"{BASE}/firmware_update/state") == "ON"


async def test_added_sensor_reads_the_bootstrap_again(make):
    bridge, _, _, boot = make()
    await bridge.setup(boot)
    with pytest.raises(Resync):
        await bridge.handle_packet(update({}, action="add", sensor_id="other"))


async def test_other_devices_are_ignored(make):
    bridge, mqtt, _, boot = make(min_interval=0)
    await bridge.setup(boot)
    mqtt.clear()
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": 999}}}, sensor_id="other"))
    assert mqtt.messages == []


async def test_gone_sensor_is_removed(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    bridge2, mqtt2, _, boot2 = make(devices=[])
    await bridge2.setup(boot2)
    assert mqtt2.last(DEVICE) == ""
    assert mqtt2.last(f"{BASE}/co2") == ""
    assert mqtt2.last(f"{BASE}/availability") == ""


async def test_equal_names_get_the_mac(make):
    other = sensor(id="x2", mac="00:00:5E:00:53:02")
    bridge, mqtt, _, boot = make(devices=[sensor(), other])
    await bridge.setup(boot)
    cfg = json.loads(mqtt.last("homeassistant/device/up_airquality_00005e005302/config"))
    assert cfg["device"]["name"] == "Living room (005302)"


async def test_single_entity_topics_are_handed_over(make):
    legacy = f"homeassistant/sensor/protect_air_quality_{KEY}/co2/config"
    bridge, mqtt, _, boot = make(held={legacy: '{"unique_id": "x"}', "homeassistant/sensor/other/co2/config": "{}"})
    await bridge.setup(boot)
    assert DEVICE in bridge.asked and f"homeassistant/+/protect_air_quality_{KEY}/+/config" in bridge.asked
    sent = [(t, v) for t, v, _ in mqtt.messages if t in (legacy, DEVICE)]
    assert [t for t, _ in sent] == [legacy, DEVICE, legacy]
    assert json.loads(sent[0][1]) == {"migrate_discovery": True}
    assert sent[2][1] == ""
    assert mqtt.last("homeassistant/sensor/other/co2/config") is None


async def test_switched_off_controls_are_removed(make):
    on, mqtt_on, _, boot = make(controls=True)
    await on.setup(boot)
    before = mqtt_on.last(DEVICE)
    bridge, mqtt, _, boot = make(controls=False, held={DEVICE: before})
    await bridge.setup(boot)
    sent = [json.loads(v) for t, v, _ in mqtt.messages if t == DEVICE]
    assert sent[0]["components"]["led_brightness"] == {"platform": "number"}
    assert "led_brightness" not in sent[1]["components"]
    assert "co2" in sent[1]["components"]


async def test_unchanged_device_message_is_not_sent_again(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    mqtt.clear()
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": 700}}}))
    assert DEVICE not in mqtt.topics()


async def test_offline_when_protect_is_gone(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    await bridge.offline()
    assert mqtt.last(f"{BASE}/availability") == "offline"


# --- Controls ------------------------------------------------------------------

async def test_controls_publish_settings(make):
    bridge, mqtt, _, boot = make(controls=True)
    await bridge.setup(boot)
    assert mqtt.last(f"{BASE}/led_brightness/state") == "40"
    assert mqtt.last(f"{BASE}/led_metric/state") == "Air Quality"
    assert mqtt.last(f"{BASE}/status_light/state") == "ON"
    assert mqtt.last(f"{BASE}/thresh/co2/high/state") == "1400"


@pytest.mark.parametrize(("topic", "value", "changes", "state"), [
    ("led_brightness", "55", {"airQualitySettings": {"ringLedBrightness": 55}}, "55"),
    ("led_metric", "CO2", {"airQualitySettings": {"ringLedMetric": 0}}, "CO2"),
    ("status_light", "off", {"ledSettings": {"isEnabled": False}}, "OFF"),
    ("night_mode", "ON", {"airQualitySettings": {"nightModeEnabled": True}}, "ON"),
    ("thresh/co2/high", "1500", {"airQualitySettings": {"co2Settings": {"highThreshold": 1500}}}, "1500"),
])
async def test_commands_patch_protect(make, topic, value, changes, state):
    bridge, mqtt, protect, boot = make(controls=True)
    await bridge.setup(boot)
    await bridge.handle_command(f"{BASE}/{topic}/set", value)
    assert protect.patches == [(ID, changes)]
    assert mqtt.last(f"{BASE}/{topic}/state") == state


@pytest.mark.parametrize(("topic", "value"), [
    ("led_brightness", "150"), ("led_metric", "PM2.5"), ("status_light", "maybe"),
    ("thresh/co2/high", "99999"), ("thresh/nope/high", "1"), ("unknown", "1"),
    ("thresh/temperature/high", "25"),  # the fixture's sensor offers no temperature threshold
])
async def test_invalid_commands_are_ignored(make, topic, value):
    bridge, _, protect, boot = make(controls=True)
    await bridge.setup(boot)
    await bridge.handle_command(f"{BASE}/{topic}/set", value)
    assert protect.patches == []


async def test_commands_need_controls(make):
    bridge, _, protect, boot = make(controls=False)
    await bridge.setup(boot)
    await bridge.handle_command(f"{BASE}/led_brightness/set", "55")
    assert protect.patches == []


async def test_failed_patch_keeps_the_state(make):
    bridge, mqtt, protect, boot = make(controls=True)
    await bridge.setup(boot)
    protect.fail = ProtectError("offline")
    await bridge.handle_command(f"{BASE}/led_brightness/set", "55")
    assert mqtt.last(f"{BASE}/led_brightness/state") == "40"


# --- Vape detection and safe zones ---------------------------------------------

def vape_sensor(status="safe", **extra):
    device = sensor(**extra)
    device["airQuality"] = {**device["airQuality"], "vape": {"value": 0, "status": status}}
    return device


async def test_vape_detected_follows_the_protect_status(make):
    bridge, mqtt, _, boot = make(devices=[vape_sensor()])
    await bridge.setup(boot)
    assert mqtt.last(f"{BASE}/vape_detected") == "OFF"
    await bridge.handle_packet(update({"airQuality": {"vape": {"value": 80, "status": "detected"}}}))
    assert mqtt.last(f"{BASE}/vape_detected") == "ON"


async def test_outside_safe_zone_at_once_without_throttle(make):
    bridge, mqtt, _, boot = make(min_interval=3600)
    await bridge.setup(boot)
    # The fixture's CO2 safe zone is 800 to 1400; 655 is below it
    assert mqtt.last(f"{BASE}/co2/outside_safe_zone") == "ON"
    assert json.loads(mqtt.last(f"{BASE}/co2/safe_zone")) == {"low": 800, "high": 1400}
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": 1000}}}))
    assert mqtt.last(f"{BASE}/co2/outside_safe_zone") == "OFF"
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": 1500}}}))
    assert mqtt.last(f"{BASE}/co2/outside_safe_zone") == "ON"
    assert mqtt.last(f"{BASE}/co2") == "655"   # the reading itself waits for min_interval


async def test_safe_zone_removed_in_protect_removes_its_sensor(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    assert "co2_outside_safe_zone" in components(mqtt)
    mqtt.clear()
    await bridge.handle_packet(update({"airQualitySettings": {"co2Settings": {"lowThreshold": None, "highThreshold": None}}}))
    sent = [json.loads(v) for t, v, _ in mqtt.messages if t == DEVICE]
    assert sent[0]["components"]["co2_outside_safe_zone"] == {"platform": "binary_sensor"}
    assert "co2_outside_safe_zone" not in sent[-1]["components"]


async def test_new_safe_zone_adds_its_sensor(make):
    bridge, mqtt, _, boot = make()
    await bridge.setup(boot)
    await bridge.handle_packet(update({"airQualitySettings": {"pm2p5Settings": {"lowThreshold": None, "highThreshold": 10}}}))
    assert "pm2p5_outside_safe_zone" in components(mqtt)
    assert mqtt.last(f"{BASE}/pm2p5/outside_safe_zone") == "OFF"


async def test_event_switch_and_vape_sensitivity_commands(make):
    device = vape_sensor()
    device["airQualitySettings"]["vapeSensitivitySettings"] = {"isEnabled": True, "sensitivity": 50}
    device["airQualitySettings"]["co2Settings"]["isEnabled"] = True
    bridge, mqtt, protect, boot = make(controls=True, devices=[device])
    await bridge.setup(boot)
    assert mqtt.last(f"{BASE}/events/co2/state") == "ON"
    assert mqtt.last(f"{BASE}/vape_sensitivity/state") == "50"
    await bridge.handle_command(f"{BASE}/events/co2/set", "OFF")
    await bridge.handle_command(f"{BASE}/vape_sensitivity/set", "70")
    assert protect.patches == [
        (ID, {"airQualitySettings": {"co2Settings": {"isEnabled": False}}}),
        (ID, {"airQualitySettings": {"vapeSensitivitySettings": {"sensitivity": 70}}}),
    ]
    assert mqtt.last(f"{BASE}/events/co2/state") == "OFF"


# --- Readings switched off in Protect -------------------------------------------

async def test_switched_off_reading_turns_unknown_at_once(make):
    bridge, mqtt, _, boot = make(min_interval=3600)
    await bridge.setup(boot)
    # What Protect sends after unticking CO2 under Events to Capture
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": None, "status": "unknown"}}}))
    assert mqtt.last(f"{BASE}/co2") == "None"
    assert json.loads(mqtt.last(f"{BASE}/co2/attributes")) == {"status": "unknown"}
    assert mqtt.last(f"{BASE}/co2/outside_safe_zone") == "None"


async def test_switched_on_again_the_value_returns(make):
    bridge, mqtt, _, boot = make(min_interval=0)
    await bridge.setup(boot)
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": None, "status": "unknown"}}}))
    await bridge.handle_packet(update({"airQuality": {"co2": {"value": 900, "status": "neutral"}}}))
    assert mqtt.last(f"{BASE}/co2") == "900"
    assert mqtt.last(f"{BASE}/co2/outside_safe_zone") == "OFF"


async def test_switched_off_vape_is_unknown_not_detected(make):
    bridge, mqtt, _, boot = make(devices=[vape_sensor()])
    await bridge.setup(boot)
    await bridge.handle_packet(update({"airQuality": {"vape": {"value": None, "status": "unknown"}}}))
    assert mqtt.last(f"{BASE}/vape_detected") == "None"


# --- The bridge's own device and the certificate ---------------------------------

BRIDGE_DEVICE = "homeassistant/device/up_airquality_bridge/config"
CONNECTED = "up_airquality/bridge/protect"
PINNED, FOUND = "AA:" * 31 + "AA", "BB:" * 31 + "BB"


class SessionProtect(FakeProtect):
    """Logs in (or fails with error), then keeps the update stream open."""

    def __init__(self, error=None):
        super().__init__()
        self.error = error
        self.connected = asyncio.Event()

    async def login(self):
        if self.error:
            raise self.error

    async def bootstrap(self):
        return {"sensors": [sensor()], "lastUpdateId": "u1"}

    async def updates(self, _last):
        self.connected.set()
        await asyncio.Event().wait()
        yield b""


def session_bridge(ctx, tmp_path, protect):
    mqtt = Recorder()

    async def retained(_filters):
        return {}

    return Bridge(mqtt, protect, ctx, controls=False, min_interval=0, store=tmp_path / "sensors.json",
                  retained=retained, migrate_wait=0), mqtt


async def test_the_bridge_device_has_the_protect_connection(ctx, tmp_path):
    bridge, mqtt = session_bridge(ctx, tmp_path, SessionProtect())
    await bridge.announce_bridge()
    comps = components(mqtt, BRIDGE_DEVICE)
    assert list(comps) == ["protect_connection"]
    assert comps["protect_connection"]["device_class"] == "connectivity"
    # Starting the app says nothing about Protect
    assert mqtt.last(CONNECTED) is None


async def test_the_connection_is_on_while_following_protect(ctx, tmp_path):
    protect = SessionProtect()
    bridge, mqtt = session_bridge(ctx, tmp_path, protect)
    task = asyncio.create_task(bridge.follow_protect())
    try:
        async with asyncio.timeout(2):
            await protect.connected.wait()
        assert mqtt.last(CONNECTED) == "ON"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_certificate_mismatch_logs_both_fingerprints_and_stops(ctx, tmp_path, caplog):
    protect = SessionProtect(CertificateMismatch(PINNED, FOUND))
    bridge, mqtt = session_bridge(ctx, tmp_path, protect)
    with pytest.raises(CertificateMismatch):
        async with asyncio.timeout(2):
            await bridge.follow_protect()
    assert f"configured fingerprint: {PINNED} found fingerprint: {FOUND}" in caplog.text
    assert mqtt.last(CONNECTED) is None       # Protect answered; it is not out of reach
    assert not protect.connected.is_set()


async def follow_until_retry(bridge, mqtt, monkeypatch):
    """Runs follow_protect until it would wait to connect again."""
    waited = asyncio.Event()

    async def sleep(_delay):
        waited.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("upaq_bridge.bridge.asyncio.sleep", sleep)
    task = asyncio.create_task(bridge.follow_protect())
    async with asyncio.timeout(2):
        await waited.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_an_unreachable_protect_turns_the_connection_off(ctx, tmp_path, monkeypatch):
    bridge, mqtt = session_bridge(ctx, tmp_path, SessionProtect(ProtectError("console not reachable")))
    await follow_until_retry(bridge, mqtt, monkeypatch)
    assert mqtt.last(CONNECTED) == "OFF"


async def test_a_refused_login_leaves_the_connection_alone(ctx, tmp_path, monkeypatch):
    bridge, mqtt = session_bridge(ctx, tmp_path, SessionProtect(AuthError("login refused (401)")))
    await follow_until_retry(bridge, mqtt, monkeypatch)
    assert mqtt.last(CONNECTED) is None
