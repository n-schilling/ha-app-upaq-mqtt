"""UniFi Protect client: login, bootstrap, update stream and sensor settings.

Protect sends its updates over a WebSocket as binary packets of two frames,
each an 8-byte header (packet type, payload format, deflated flag, reserved,
payload size as big-endian uint32) and the payload. Frame 1 names the action
(``{"action": "update", "modelKey": "sensor", "id": ...}``), frame 2 holds the
changed fields. Only frames of the sensors the bridge follows are inflated and
parsed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
import struct
import zlib
from collections.abc import AsyncIterator, Container
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

LOGGER = logging.getLogger(__name__)

HEADER = struct.Struct(">BBBxI")
FORMAT_JSON = 1
# Largest frame accepted after inflating; sensor updates are a few hundred bytes
MAX_INFLATED = 1024 * 1024
TIMEOUT = aiohttp.ClientTimeout(total=30)


class ProtectError(Exception):
    """Protect could not be reached or answered with an error."""


class AuthError(ProtectError):
    """Protect refused the login."""


class CertificateMismatch(AuthError):
    """The console presented another certificate than the pinned one."""

    def __init__(self, pinned: str, presented: str) -> None:
        super().__init__(f"the console presented another certificate ({presented}) than the "
                         f"pinned one ({pinned})")
        self.pinned = pinned
        self.presented = presented


class CertificateUntrusted(AuthError):
    """public_ca: no trusted CA vouches for the console's certificate."""


class PinStore:
    """The fingerprint pinned on first use, kept in the app's private data."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> str | None:
        try:
            value = json.loads(self._path.read_text()).get("fingerprint")
        except (OSError, ValueError, AttributeError):
            return None
        return value if isinstance(value, str) and value else None

    def save(self, fingerprint: str) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fingerprint": fingerprint,
                                   "pinned_at": datetime.now(UTC).isoformat(timespec="seconds")}))
        tmp.replace(self._path)


def _frame(data: bytes, offset: int) -> tuple[bytes, int, int]:
    """Returns (payload, format, next offset) of the frame at offset."""
    if offset + HEADER.size > len(data):
        raise ValueError("truncated frame header")
    _type, fmt, deflated, size = HEADER.unpack_from(data, offset)
    start = offset + HEADER.size
    end = start + size
    if end > len(data):
        raise ValueError("truncated frame payload")
    payload = data[start:end]
    if deflated:
        inflater = zlib.decompressobj()
        try:
            payload = inflater.decompress(payload, MAX_INFLATED)
        except zlib.error as err:
            raise ValueError(f"frame does not inflate: {err}") from err
        if inflater.unconsumed_tail:
            raise ValueError("frame inflates beyond the limit")
    return payload, fmt, end


def decode_update(data: bytes, model_key: str, ids: Container[str]) -> tuple[dict, dict] | None:
    """Decodes an update packet if it concerns one of ids of model_key.

    Returns (action, changes), or None for packets of other devices, which
    are dropped after reading only their small action frame.
    """
    payload, fmt, offset = _frame(data, 0)
    if fmt != FORMAT_JSON:
        return None
    action = json.loads(payload)
    if not isinstance(action, dict) or action.get("modelKey") != model_key:
        return None
    if action.get("action") == "update" and action.get("id") not in ids:
        return None
    payload, fmt, _ = _frame(data, offset)
    changes = json.loads(payload) if fmt == FORMAT_JSON else {}
    return action, changes if isinstance(changes, dict) else {}


def ssl_check(check: str, fingerprint: str = "") -> ssl.SSLContext | aiohttp.Fingerprint | bool:
    """How the console's certificate is checked.

    UniFi consoles mostly use self-signed certificates, which a CA check
    rejects. A pinned SHA-256 fingerprint protects against a man in the middle
    without a CA; accept_any keeps the connection encrypted but not
    authenticated.
    """
    if check == "pin" and fingerprint:
        return aiohttp.Fingerprint(bytes.fromhex(fingerprint.replace(":", "")))
    if check == "public_ca":
        return True
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def format_digest(digest: bytes) -> str:
    text = digest.hex().upper()
    return ":".join(text[i:i + 2] for i in range(0, len(text), 2))


def format_fingerprint(der: bytes) -> str:
    return format_digest(hashlib.sha256(der).digest())


async def server_fingerprint(base_url: str) -> str | None:
    """SHA-256 fingerprint of the certificate the console presents."""
    url = urlsplit(base_url)
    if url.scheme != "https" or not url.hostname:
        return None
    try:
        pem = await asyncio.to_thread(ssl.get_server_certificate, (url.hostname, url.port or 443), timeout=10)
    except (OSError, ssl.SSLError):
        return None
    return format_fingerprint(ssl.PEM_cert_to_DER_cert(pem))


class Protect:
    """A logged-in session with the Protect application of a UniFi console."""

    def __init__(self, session: aiohttp.ClientSession, host: str, username: str,
                 password: str, check: str = "accept_any", fingerprint: str = "",
                 store: PinStore | None = None, *, base_url: str | None = None) -> None:
        self._session = session
        # base_url only for the tests, which run a fake console
        self._base = base_url or f"https://{host}"
        self._username = username
        self._password = password
        self.check = check
        # A fingerprint from the options is fixed; otherwise pin mode keeps the
        # one seen first in the store
        self.manual_fingerprint = fingerprint
        self._store = store
        self.pinned = fingerprint or (store.load() if store and check == "pin" else None)
        self._ssl = ssl_check(check, self.pinned or "")
        self._csrf: str | None = None
        self._warned = False

    @property
    def can_accept(self) -> bool:
        """A changed certificate can be accepted from Home Assistant."""
        return self.check == "pin" and not self.manual_fingerprint and self._store is not None

    def accept(self, fingerprint: str) -> None:
        """Pins fingerprint from now on (a new certificate of the console)."""
        if not self.can_accept:
            raise ProtectError("the fingerprint is set in the options; change it there")
        assert self._store is not None
        self._store.save(fingerprint)
        self.pinned = fingerprint
        self._ssl = ssl_check(self.check, fingerprint)
        LOGGER.info("Pinned the console's certificate %s", fingerprint)

    async def _pin_first_use(self) -> None:
        """pin without a fingerprint yet: trust the certificate seen now."""
        if self.check != "pin" or self.pinned:
            return
        fingerprint = await server_fingerprint(self._base)
        if not fingerprint:
            raise ProtectError("could not read the console's certificate to pin it")
        if self._store:
            self._store.save(fingerprint)
        self.pinned = fingerprint
        self._ssl = ssl_check(self.check, fingerprint)
        LOGGER.info("Pinned the console's certificate on first use: %s", fingerprint)

    def _certificate_error(self, err: aiohttp.ClientError) -> AuthError | None:
        if isinstance(err, aiohttp.ServerFingerprintMismatch):
            return CertificateMismatch(self.pinned or "", format_digest(err.got))
        if isinstance(err, aiohttp.ClientConnectorCertificateError):
            return CertificateUntrusted(
                f"no trusted CA vouches for the console's certificate ({err.certificate_error}); "
                "certificate_check public_ca needs one, e.g. from Let's Encrypt; choose pin otherwise")
        return None

    async def login(self) -> None:
        await self._pin_first_use()
        body = {"username": self._username, "password": self._password, "rememberMe": True}
        try:
            async with self._session.post(f"{self._base}/api/auth/login", json=body,
                                          ssl=self._ssl, timeout=TIMEOUT) as resp:
                if resp.status in (401, 403):
                    raise AuthError(f"login refused ({resp.status}): check user and password")
                if resp.status >= 400:
                    raise ProtectError(f"login failed with HTTP {resp.status}")
                self._csrf = resp.headers.get("X-CSRF-Token") or self._csrf
                await resp.read()
        except aiohttp.ClientError as err:
            raise self._certificate_error(err) or ProtectError(f"console not reachable: {err}") from err
        if not any(cookie.key == "TOKEN" for cookie in self._session.cookie_jar):
            raise AuthError("login answered without a session cookie")
        if self.check == "accept_any" and not self._warned:
            self._warned = True
            LOGGER.warning("The console's certificate is not checked (certificate_check accept_any); "
                           "pin protects the password: choose it, or set certificate_fingerprint to %s",
                           await server_fingerprint(self._base) or "its SHA-256 fingerprint")

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"X-CSRF-Token": self._csrf} if self._csrf and method != "GET" else None
        try:
            async with self._session.request(method, f"{self._base}/proxy/protect/api{path}",
                                             json=body, headers=headers, ssl=self._ssl,
                                             timeout=TIMEOUT) as resp:
                self._csrf = resp.headers.get("X-Updated-CSRF-Token") or self._csrf
                if resp.status == 401:
                    raise AuthError("session expired")
                if resp.status >= 400:
                    raise ProtectError(f"{method} {path} failed with HTTP {resp.status}")
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise self._certificate_error(err) or ProtectError(f"{method} {path} failed: {err}") from err

    async def bootstrap(self) -> dict:
        data = await self._request("GET", "/bootstrap")
        if not isinstance(data, dict):
            raise ProtectError("bootstrap is no object")
        return data

    async def patch_sensor(self, sensor_id: str, changes: dict) -> None:
        """Writes changed settings; logs in again once if the session expired."""
        try:
            await self._request("PATCH", f"/sensors/{sensor_id}", changes)
        except AuthError:
            await self.login()
            await self._request("PATCH", f"/sensors/{sensor_id}", changes)

    async def updates(self, last_update_id: str) -> AsyncIterator[bytes]:
        """Yields the binary update packets until the connection closes."""
        url = f"{self._base.replace('http', 'ws', 1)}/proxy/protect/ws/updates"
        try:
            async with self._session.ws_connect(url, params={"lastUpdateId": last_update_id},
                                                ssl=self._ssl, heartbeat=30,
                                                max_msg_size=4 * 1024 * 1024) as ws:
                LOGGER.info("Following Protect updates")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        yield msg.data
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break
        except aiohttp.ClientError as err:
            raise self._certificate_error(err) or ProtectError(f"update stream failed: {err}") from err
