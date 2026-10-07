"""Discovery payloads; IDs must match the bridges used before, so existing
entities and their history carry over."""

from conftest import KEY, sensor
from upaq_bridge import discovery as d


def payload(ctx, controls=False, **extra):
    return d.device_payload(sensor(**extra), "Living room", ctx, controls)


def test_one_message_per_sensor(ctx):
    assert d.device_topic("homeassistant", KEY) == f"homeassistant/device/up_airquality_{KEY}/config"
    assert d.legacy_filter("homeassistant", KEY) == f"homeassistant/+/protect_air_quality_{KEY}/+/config"


def test_ids_carry_over(ctx):
    p = payload(ctx)
    assert p["device"]["identifiers"] == [f"up_airquality_{KEY}"]
    assert p["device"]["connections"] == [["mac", "00:00:5e:00:53:01"]]
    comps = p["components"]
    assert comps["co2"]["unique_id"] == f"up_aq_{KEY}_co2"
    assert comps["co2"]["platform"] == "sensor"
    assert comps["co2"]["unit_of_measurement"] == "ppm"
    assert comps["firmware_version"]["unique_id"] == f"up_aq_{KEY}_fw_version"
    assert comps["firmware_update"]["unique_id"] == f"up_aq_{KEY}_fw_update"
    assert comps["firmware_update"]["platform"] == "binary_sensor"


def test_only_reported_readings_get_entities(ctx):
    readings = [k for k, c in payload(ctx)["components"].items()
                if c["platform"] == "sensor" and "json_attributes_topic" in c]
    assert sorted(readings) == ["aqi", "co2", "pm2p5"]


def test_shared_availability_origin_and_topics(ctx):
    p = payload(ctx)
    assert p["availability"] == [{"topic": "up_airquality/bridge/availability"},
                                 {"topic": f"up_airquality/{KEY}/availability"}]
    assert p["availability_mode"] == "all"
    assert p["origin"]["sw_version"] == "2.0.0"
    assert p["device"]["configuration_url"] == "homeassistant://hassio/addon/7047f973_upaq_mqtt/info"
    pm = p["components"]["pm2p5"]
    assert pm["state_topic"] == f"up_airquality/{KEY}/pm2p5"
    assert pm["json_attributes_topic"] == f"up_airquality/{KEY}/pm2p5/attributes"
    assert "device" not in pm and "availability" not in pm


def test_controls_only_when_enabled(ctx):
    assert "led_brightness" not in payload(ctx)["components"]
    comps = payload(ctx, controls=True)["components"]
    # readings, CO2 outside its safe zone, firmware, LED and night mode, the low
    # and high CO2 threshold
    assert len(comps) == 3 + 1 + 2 + 5 + 2
    led = comps["led_brightness"]
    assert (led["unique_id"], led["command_topic"]) == (f"up_aq_{KEY}_led_brightness",
                                                       f"up_airquality/{KEY}/led_brightness/set")
    threshold = comps["co2_high_threshold"]
    assert threshold["unique_id"] == f"up_aq_{KEY}_co2_high_thresh"
    assert threshold["enabled_by_default"] is False


def test_state_topics_of_a_gone_sensor():
    topics = d.state_topics(KEY)
    assert f"up_airquality/{KEY}/availability" in topics
    assert f"up_airquality/{KEY}/co2" in topics
    assert f"up_airquality/{KEY}/thresh/co2/high/state" in topics


def test_sensor_key_without_mac_uses_the_id():
    assert d.sensor_key({"id": "6A97-0036"}) == "6a970036"
    assert d.mac_connection({"mac": "not-a-mac"}) is None


def test_thresholds_come_from_the_sensor():
    settings = {
        "noxSettings": {"isEnabled": True, "lowThreshold": None, "highThreshold": None},
        "co2Settings": {"isEnabled": True, "lowThreshold": 800, "highThreshold": 1400},
        "vapeSensitivitySettings": {"isEnabled": True, "sensitivity": 50},
        "futureSettings": {"lowThreshold": 1, "highThreshold": 2},
        "ringLedMetric": 1,
    }
    assert d.threshold_metrics({"airQualitySettings": settings}) == ["nox", "co2"]


def test_threshold_ranges_from_the_data_sheet(ctx):
    device = sensor(airQualitySettings={"temperatureSettings": {"lowThreshold": None, "highThreshold": None},
                                        "vocSettings": {"lowThreshold": None, "highThreshold": None}})
    comps = d.device_payload(device, "Living room", ctx, True)["components"]
    temp = comps["temperature_high_threshold"]
    assert (temp["min"], temp["max"], temp["unit_of_measurement"]) == (0, 40, "°C")
    voc = comps["voc_low_threshold"]
    assert (voc["min"], voc["max"], voc["name"]) == (1, 500, "VOC Index Low Threshold")
    assert "unit_of_measurement" not in voc


def test_vape_detected_only_with_vape(ctx):
    assert "vape_detected" not in payload(ctx)["components"]
    comps = payload(ctx, airQuality={"vape": {"value": 0, "status": "safe"}})["components"]
    assert comps["vape_detected"]["platform"] == "binary_sensor"
    assert comps["vape_detected"]["unique_id"] == f"up_aq_{KEY}_vape_detected"


def test_safe_zone_sensors_only_for_zones_set_in_protect(ctx):
    settings = {"co2Settings": {"isEnabled": True, "lowThreshold": None, "highThreshold": 1000},
                "pm2p5Settings": {"isEnabled": True, "lowThreshold": None, "highThreshold": None}}
    comps = payload(ctx, airQualitySettings=settings)["components"]
    zone = comps["co2_outside_safe_zone"]
    assert (zone["device_class"], zone["name"]) == ("problem", "CO2 Outside Safe Zone")
    assert "pm2p5_outside_safe_zone" not in comps


def test_safe_zone_bounds():
    assert d.safe_zone({"airQualitySettings": {"co2Settings": {"lowThreshold": None, "highThreshold": 1000}}}, "co2") == (None, 1000)
    assert d.safe_zone({"airQualitySettings": {"co2Settings": {"lowThreshold": None, "highThreshold": None}}}, "co2") is None
    assert d.outside_safe_zone(1001, (None, 1000)) and not d.outside_safe_zone(1000, (None, 1000))
    assert d.outside_safe_zone(19, (20, None)) and not d.outside_safe_zone(25, (20, 30))


def test_event_switches_and_vape_sensitivity_with_controls(ctx):
    settings = {"co2Settings": {"isEnabled": True, "lowThreshold": None, "highThreshold": None},
                "vapeSensitivitySettings": {"isEnabled": True, "sensitivity": 50}}
    comps = payload(ctx, controls=True, airQualitySettings=settings)["components"]
    assert comps["co2_events"]["command_topic"] == f"up_airquality/{KEY}/events/co2/set"
    assert comps["vape_sensitivity"]["unit_of_measurement"] == "%"
    assert "co2_events" not in payload(ctx, airQualitySettings=settings)["components"]
