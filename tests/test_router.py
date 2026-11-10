"""The router: the one reader of the MQTT messages."""

import asyncio
from types import SimpleNamespace

from conftest import KEY, FakeProtect, Recorder, sensor
from upaq_bridge.__main__ import Router
from upaq_bridge.bridge import Bridge

BASE = f"up_airquality/{KEY}"


class FakeMqtt:
    """Hands out queued messages like aiomqtt's client."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.subscribed = []

    async def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    async def unsubscribe(self, topic):
        pass

    def put(self, topic, payload, retain=False):
        self.queue.put_nowait(SimpleNamespace(topic=topic, payload=payload.encode(), retain=retain))

    @property
    async def messages(self):
        while True:
            yield await self.queue.get()


class SlowProtect(FakeProtect):
    """A PATCH that takes its time, as on a console that answers late."""

    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()

    async def patch_sensor(self, sensor_id, changes):
        await self.release.wait()
        await super().patch_sensor(sensor_id, changes)


async def started(protect, mqtt, tmp_path, ctx):
    router = Router(mqtt)
    bridge = Bridge(Recorder(), protect, ctx, controls=True, min_interval=0,
                    store=tmp_path / "sensors.json", migrate_wait=0, command_gap=0)
    await bridge.setup({"sensors": [sensor()], "lastUpdateId": "u1"})
    tasks = [asyncio.create_task(router.run(bridge, True)), asyncio.create_task(bridge.command_loop())]
    return router, tasks


async def test_a_retained_command_is_not_applied(ctx, tmp_path):
    protect, mqtt = FakeProtect(), FakeMqtt()
    _router, tasks = await started(protect, mqtt, tmp_path, ctx)
    try:
        mqtt.put(f"{BASE}/led_brightness/set", "10", retain=True)
        mqtt.put(f"{BASE}/led_brightness/set", "20")
        await asyncio.sleep(0.05)
        assert protect.patches == [(sensor()["id"], {"airQualitySettings": {"ringLedBrightness": 20}})]
    finally:
        for task in tasks:
            task.cancel()


async def test_retained_messages_arrive_while_a_command_waits_for_protect(ctx, tmp_path):
    protect, mqtt = SlowProtect(), FakeMqtt()
    router, tasks = await started(protect, mqtt, tmp_path, ctx)
    try:
        mqtt.put(f"{BASE}/led_brightness/set", "10")
        await asyncio.sleep(0.01)                # the PATCH hangs now
        collect = asyncio.create_task(router.retained(["homeassistant/device/#"], wait=0.05, quiet=0.05))
        await asyncio.sleep(0.01)
        mqtt.put("homeassistant/device/x/config", "{}", retain=True)
        assert await collect == {"homeassistant/device/x/config": "{}"}
        protect.release.set()
    finally:
        for task in tasks:
            task.cancel()
