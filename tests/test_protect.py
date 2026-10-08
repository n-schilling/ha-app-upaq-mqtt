"""Packet decoding and the Protect client against a fake console."""

import shutil
import ssl
import struct
import subprocess
import zlib

import aiohttp
import pytest
from aiohttp import web

from conftest import frame, packet
from upaq_bridge.protect import (AuthError, CertificateMismatch, CertificateUntrusted, PinStore, Protect,
                                 decode_update, format_fingerprint, server_fingerprint, ssl_check)

ACTION = {"action": "update", "modelKey": "sensor", "id": "s1"}


def test_decode_update_of_a_followed_sensor():
    assert decode_update(packet(ACTION, {"airQuality": {"co2": {"value": 700}}}), "sensor", {"s1"}) == \
        (ACTION, {"airQuality": {"co2": {"value": 700}}})


def test_decode_plain_frames():
    assert decode_update(packet(ACTION, {"name": "x"}, deflate=False), "sensor", {"s1"})[1] == {"name": "x"}


def test_other_devices_are_dropped_without_reading_their_changes():
    # A broken second frame proves it is never read
    broken = frame({"action": "update", "modelKey": "camera", "id": "c1"}) + b"\x01\x01\x01\x00\x00\x00\x00\x09garbage"
    assert decode_update(broken, "sensor", {"s1"}) is None
    other = frame({"action": "update", "modelKey": "sensor", "id": "s2"}) + b"junk"
    assert decode_update(other, "sensor", {"s1"}) is None


def test_add_and_remove_pass_for_any_id():
    action = {"action": "add", "modelKey": "sensor", "id": "new"}
    assert decode_update(packet(action, {}), "sensor", {"s1"})[0] == action


def test_truncated_packet_raises():
    with pytest.raises(ValueError):
        decode_update(frame(ACTION)[:-3], "sensor", {"s1"})


def test_broken_and_oversized_frames_raise_value_errors():
    broken = struct.pack(">BBBxI", 1, 1, 1, 7) + b"garbage"
    with pytest.raises(ValueError, match="inflate"):
        decode_update(broken, "sensor", {"s1"})
    bomb = zlib.compress(b"[" + b" " * (2 * 1024 * 1024) + b"]")
    with pytest.raises(ValueError, match="limit"):
        decode_update(struct.pack(">BBBxI", 1, 1, 1, len(bomb)) + bomb, "sensor", {"s1"})
    assert decode_update(frame([1, 2]), "sensor", {"s1"}) is None


def test_fingerprint_format():
    assert format_fingerprint(b"x") == ("2D:71:16:42:B7:26:B0:44:01:62:7C:A9:FB:AC:32:F5:"
                                        "C8:53:0F:B1:90:3C:C4:DB:02:25:87:17:92:1A:48:81")


async def test_no_fingerprint_without_tls():
    assert await server_fingerprint("http://127.0.0.1:1") is None


def test_fingerprint_pins_the_certificate():
    pinned = ssl_check("pin", "AB:" * 31 + "CD")
    assert isinstance(pinned, aiohttp.Fingerprint)
    assert ssl_check("public_ca") is True
    assert isinstance(ssl_check("accept_any"), ssl.SSLContext)
    assert ssl_check("accept_any").verify_mode == ssl.CERT_NONE


class FakeConsole:
    def __init__(self):
        self.app = web.Application()
        self.app.add_routes([
            web.post("/api/auth/login", self.login),
            web.get("/proxy/protect/api/bootstrap", self.bootstrap),
            web.patch("/proxy/protect/api/sensors/{id}", self.patch),
            web.get("/proxy/protect/ws/updates", self.updates),
        ])
        self.logins = 0
        self.expired = False
        self.patches = []
        self.csrf_seen = []

    async def login(self, request):
        body = await request.json()
        if body["password"] != "right":
            return web.Response(status=401)
        self.logins += 1
        resp = web.json_response({})
        resp.set_cookie("TOKEN", f"t{self.logins}")
        resp.headers["X-CSRF-Token"] = f"csrf{self.logins}"
        return resp

    async def bootstrap(self, request):
        assert request.cookies.get("TOKEN")
        return web.json_response({"lastUpdateId": "u1", "sensors": []})

    async def patch(self, request):
        if self.expired:
            self.expired = False
            return web.Response(status=401)
        self.csrf_seen.append(request.headers.get("X-CSRF-Token"))
        self.patches.append((request.match_info["id"], await request.json()))
        return web.json_response({})

    async def updates(self, request):
        assert request.query["lastUpdateId"] == "u1"
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_bytes(packet(ACTION, {"airQuality": {"co2": {"value": 701}}}))
        await ws.close()
        return ws


@pytest.fixture
async def console(aiohttp_server):
    fake = FakeConsole()
    server = await aiohttp_server(fake.app)
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
        def client(password="right"):
            return Protect(http, "unused", "bridge", password, "public_ca",
                           base_url=str(server.make_url("")).rstrip("/"))
        yield fake, client


async def test_login_bootstrap_and_updates(console):
    fake, client = console
    protect = client()
    await protect.login()
    assert (await protect.bootstrap())["lastUpdateId"] == "u1"
    packets = [p async for p in protect.updates("u1")]
    assert decode_update(packets[0], "sensor", {"s1"})[1]["airQuality"]["co2"]["value"] == 701


async def test_wrong_password_is_an_auth_error(console):
    _fake, client = console
    with pytest.raises(AuthError):
        await client("wrong").login()


async def test_patch_sends_csrf_and_logs_in_again_once_expired(console):
    fake, client = console
    protect = client()
    await protect.login()
    fake.expired = True
    await protect.patch_sensor("s1", {"ledSettings": {"isEnabled": False}})
    assert fake.logins == 2
    assert fake.patches == [("s1", {"ledSettings": {"isEnabled": False}})]
    assert fake.csrf_seen == ["csrf2"]


# --- TLS: a console with a self-signed certificate ----------------------------

def self_signed(tmp_path, name):
    key, cert = tmp_path / f"{name}.key", tmp_path / f"{name}.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=unifi.local", "-keyout", key, "-out", cert],
                   check=True, capture_output=True)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return context, format_fingerprint(der)


@pytest.fixture
async def tls_consoles(aiohttp_server, tmp_path):
    """Two consoles with different self-signed certificates, as before and
    after a certificate change; yields (make_client, url_a, fp_a, url_b, fp_b)."""
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    servers = []
    for name in ("a", "b"):
        context, fingerprint = self_signed(tmp_path, name)
        server = await aiohttp_server(FakeConsole().app, ssl=context)
        servers.append((str(server.make_url("")).rstrip("/"), fingerprint))
    store = PinStore(tmp_path / "certificate.json")
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
        def client(url, check="pin", fingerprint="", pins=store):
            return Protect(http, "unused", "bridge", "right", check, fingerprint, pins, base_url=url)
        yield client, store, servers


async def test_pin_trusts_the_first_certificate_and_keeps_it(tls_consoles):
    client, store, [(url_a, fp_a), _] = tls_consoles
    protect = client(url_a)
    await protect.login()
    assert protect.pinned == fp_a
    assert store.load() == fp_a
    # After a restart the stored pin is used
    again = client(url_a)
    assert again.pinned == fp_a
    await again.login()


async def test_a_changed_certificate_is_a_mismatch_with_both_fingerprints(tls_consoles):
    client, store, [(url_a, fp_a), (url_b, fp_b)] = tls_consoles
    await client(url_a).login()
    with pytest.raises(CertificateMismatch) as err:
        await client(url_b).login()          # same console, new certificate
    assert (err.value.pinned, err.value.presented) == (fp_a, fp_b)
    assert store.load() == fp_a              # nothing pinned on its own


async def test_the_fingerprint_from_the_options_wins_and_is_kept(tls_consoles):
    client, store, [(url_a, fp_a), (url_b, fp_b)] = tls_consoles
    await client(url_a).login()
    # The admin copies the found fingerprint into the options
    await client(url_b, fingerprint=fp_b).login()
    assert store.load() == fp_b
    # Cleared again later, the stored pin is the new one
    await client(url_b).login()
    with pytest.raises(CertificateMismatch):
        await client(url_a, fingerprint=fp_b).login()


async def test_accept_any_takes_a_self_signed_certificate(tls_consoles):
    client, store, [(url_a, _), _] = tls_consoles
    await client(url_a, check="accept_any").login()
    assert store.load() is None


async def test_public_ca_rejects_a_self_signed_certificate(tls_consoles):
    client, _store, [(url_a, _), _] = tls_consoles
    with pytest.raises(CertificateUntrusted, match="public_ca"):
        await client(url_a, check="public_ca").login()


def test_pin_store_survives_a_broken_file(tmp_path):
    path = tmp_path / "certificate.json"
    path.write_text("{broken")
    assert PinStore(path).load() is None
    PinStore(path).save("AB:CD")
    assert PinStore(path).load() == "AB:CD"
