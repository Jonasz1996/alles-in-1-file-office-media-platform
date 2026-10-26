"""De tunnel voor de WebSocket van de editor (Keuze 6). Kern van O0; O1 maakt hem af.

Uvicorn krijgt ws=Tunnel op de publieke luisteraars (app/serve.py). Bij een upgrade geeft
uvicorn de herbouwde request (request-regel en koppen) als bytes aan data_received [gemeten,
M3]. De tunnel leest alleen die kop (hooguit 16 KB), beslist, opent TCP naar DS en stuurt de
herschreven upgrade door. Daarna pompt hij alleen bytes, in beide richtingen, met tegendruk
(lezen pauzeren boven 1 MB in de schrijfbuffer van de andere kant, verder onder 256 KB) en
dicht na IDLE_SECONDS zonder bytes. Dicht aan de ene kant is dicht aan de andere.

Weigeren gaat met een kale HTTP-regel en sluiten. De kern laat alleen door met
office_dev_ungated (en office ingesteld), en alleen /office/<versie>/doc/<key>/c/; al het
andere krijgt 404. De app zelf heeft dus nog steeds geen WebSockets (B17). Nog niet in de
kern (O1): Origin, het poort-cookie, het recht van nu, de StreamGuard, de grenzen per
gebruiker en per key, de regel voor poort 80 en de controle elke 15 s.
"""

import asyncio
import logging
import re
import time
from urllib.parse import urlsplit

from app.office import keys, runtime

log = logging.getLogger("platform.office")

HEAD_LIMIT = 16 * 1024
HIGH_WATER = 1024 * 1024
LOW_WATER = 256 * 1024
IDLE_SECONDS = 120.0
CONNECT_SECONDS = 10.0
PREFIX = b"/office"
VERSION = r"\d+\.\d+\.\d+-[0-9a-f]{8,64}"
SESSION_PATH = re.compile(rf"^/office/{VERSION}/doc/([0-9A-Za-z._=-]{{1,128}})/c/(?:\?.*)?$")

# Koppen van de browser die naar DS mogen; de rest (Cookie, Authorization, X-Forwarded-*,
# Forwarded, Sec-WebSocket-Extensions) nooit. Zonder extensies is er geen compressie, zodat de
# StreamGuard (O1) de frames kan lezen.
PASS_HEADERS = {
    b"upgrade",
    b"connection",
    b"sec-websocket-key",
    b"sec-websocket-version",
    b"sec-websocket-protocol",
    b"origin",
    b"user-agent",
    b"accept-language",
    b"pragma",
    b"cache-control",
}

STATUS = {
    400: b"Bad Request",
    401: b"Unauthorized",
    403: b"Forbidden",
    404: b"Not Found",
    429: b"Too Many Requests",
    502: b"Bad Gateway",
    503: b"Service Unavailable",
}

_open: set["Tunnel"] = set()


def refusal(status: int) -> bytes:
    reason = STATUS.get(status, b"Error")
    return b"HTTP/1.1 %d %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n" % (status, reason)


def parse_head(head: bytes) -> tuple[bytes, bytes, list[tuple[bytes, bytes]]]:
    """(methode, doel, koppen) van een request-kop zonder de lege regel. ValueError als het
    geen geldige kop is."""
    lines = head.split(b"\r\n")
    parts = lines[0].split(b" ")
    if len(parts) != 3 or not parts[2].startswith(b"HTTP/1."):
        raise ValueError("geen request-regel")
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(b":")
        if not sep or not name or name != name.strip():
            raise ValueError("geen kop")
        headers.append((name.lower(), value.strip()))
    return parts[0], parts[1], headers


def session_key(target: bytes) -> str | None:
    """De key in /office/<versie>/doc/<key>/c/, of None."""
    try:
        text = target.decode("ascii")
    except UnicodeDecodeError:
        return None
    match = SESSION_PATH.match(text)
    if match is None or not keys.valid(match.group(1)):
        return None
    return match.group(1)


def rewrite_upgrade(
    head: bytes, *, ds_host: str, host: str, scheme: str, client: str | None
) -> bytes:
    """De upgrade-request zoals DS hem krijgt: zonder het prefix /office, alleen de vaste
    koppen, Host van DS en de X-Forwarded-koppen (DS bouwt daarmee zijn adressen)."""
    method, target, headers = parse_head(head)
    if not target.startswith(PREFIX + b"/"):
        raise ValueError("geen pad onder /office/")
    out = [method + b" " + target[len(PREFIX) :] + b" HTTP/1.1", b"Host: " + ds_host.encode()]
    for name, value in headers:
        if name in PASS_HEADERS:
            out.append(name + b": " + value)
    out += [
        b"X-Forwarded-Prefix: /office",
        b"X-Forwarded-Host: " + host.encode("latin-1"),
        b"X-Forwarded-Proto: " + scheme.encode(),
    ]
    if client:
        out.append(b"X-Forwarded-For: " + client.encode())
    return b"\r\n".join(out) + b"\r\n\r\n"


class _Upstream(asyncio.Protocol):
    """De kant naar DS."""

    def __init__(self, tunnel: "Tunnel") -> None:
        self.tunnel = tunnel
        self.transport: asyncio.Transport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport
        transport.set_write_buffer_limits(high=HIGH_WATER, low=LOW_WATER)

    def data_received(self, data: bytes) -> None:
        self.tunnel.from_upstream(data)

    def connection_lost(self, exc: Exception | None) -> None:
        self.tunnel.close()

    def pause_writing(self) -> None:  # de buffer naar DS is vol: de browser even niet lezen
        self.tunnel.pause_browser(True)

    def resume_writing(self) -> None:
        self.tunnel.pause_browser(False)


class Tunnel(asyncio.Protocol):
    """Eén WebSocket van een browser naar DS. Wordt door uvicorn gemaakt met
    (config, server_state, app_state)."""

    def __init__(self, config=None, server_state=None, app_state=None, _loop=None) -> None:
        self.config = config
        self.server_state = server_state
        self.app_state = app_state
        self.transport: asyncio.Transport | None = None
        self.upstream: _Upstream | None = None
        self.head = bytearray()
        self.pending: list[bytes] = []  # van de browser terwijl DS nog verbindt
        self.state = "head"  # head | connecting | open | closed
        self.key: str | None = None
        self.last = time.monotonic()
        self.idle: asyncio.TimerHandle | None = None
        self.connecting: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = _loop

    # --- de kant van de browser ---

    def connection_made(self, transport) -> None:
        self.transport = transport
        self.loop = self.loop or asyncio.get_running_loop()
        transport.set_write_buffer_limits(high=HIGH_WATER, low=LOW_WATER)
        if self.server_state is not None:
            self.server_state.connections.add(self)
        _open.add(self)

    def data_received(self, data: bytes) -> None:
        self.last = time.monotonic()
        if self.state == "open":
            self.upstream.transport.write(data)
            return
        if self.state == "connecting":
            self.pending.append(data)
            return
        if self.state != "head":
            return
        self.head += data
        end = self.head.find(b"\r\n\r\n")
        if end < 0:
            if len(self.head) > HEAD_LIMIT:
                self.refuse(400)
            return
        if end > HEAD_LIMIT:
            self.refuse(400)
            return
        head, rest = bytes(self.head[:end]), bytes(self.head[end + 4 :])
        self.head.clear()
        self.decide(head, rest)

    def decide(self, head: bytes, rest: bytes) -> None:
        try:
            method, target, headers = parse_head(head)
        except ValueError:
            self.refuse(400)
            return
        settings = runtime.settings()
        key = session_key(target)
        if (
            method != b"GET"
            or key is None
            or not settings.office_configured
            or not settings.office_dev_ungated
        ):
            self.refuse(404)
            return
        self.key = key
        host = next((v for k, v in headers if k == b"host"), b"").decode("latin-1")
        peer = self.transport.get_extra_info("peername")
        secure = self.transport.get_extra_info("sslcontext") is not None
        office = urlsplit(settings.office_url)
        try:
            request = rewrite_upgrade(
                head,
                ds_host=office.netloc,
                host=host,
                scheme="https" if secure else "http",
                client=peer[0] if isinstance(peer, tuple) else None,
            )
        except ValueError:
            self.refuse(400)
            return
        if rest:
            self.pending.append(rest)
        self.state = "connecting"
        self.connecting = self.loop.create_task(self._connect(office, request))

    async def _connect(self, office, request: bytes) -> None:
        loop = asyncio.get_running_loop()
        port = office.port or (443 if office.scheme == "https" else 80)
        try:
            async with asyncio.timeout(CONNECT_SECONDS):
                _, upstream = await loop.create_connection(
                    lambda: _Upstream(self),
                    office.hostname,
                    port,
                    ssl=office.scheme == "https" or None,
                )
        except (OSError, TimeoutError):
            log.warning("office: tunnel naar de document server lukt niet")
            self.refuse(502)
            return
        if self.state != "connecting":  # intussen dicht
            upstream.transport.close()
            return
        self.upstream = upstream
        upstream.transport.write(request)
        for data in self.pending:
            upstream.transport.write(data)
        self.pending.clear()
        self.state = "open"
        self._arm_idle()

    def refuse(self, status: int) -> None:
        if self.transport is not None and not self.transport.is_closing():
            self.transport.write(refusal(status))
        self.close()

    def connection_lost(self, exc: Exception | None) -> None:
        self.close()

    def pause_writing(self) -> None:  # de buffer naar de browser is vol: DS even niet lezen
        if self.upstream is not None and self.upstream.transport is not None:
            self.upstream.transport.pause_reading()

    def resume_writing(self) -> None:
        if self.upstream is not None and self.upstream.transport is not None:
            self.upstream.transport.resume_reading()

    # --- de kant van DS ---

    def from_upstream(self, data: bytes) -> None:
        self.last = time.monotonic()
        if self.transport is not None and not self.transport.is_closing():
            self.transport.write(data)

    def pause_browser(self, paused: bool) -> None:
        if self.transport is None or self.transport.is_closing():
            return
        if paused:
            self.transport.pause_reading()
        else:
            self.transport.resume_reading()

    # --- beide ---

    def _arm_idle(self) -> None:
        self.idle = self.loop.call_later(IDLE_SECONDS, self._check_idle)

    def _check_idle(self) -> None:
        if self.state != "open":
            return
        quiet = time.monotonic() - self.last
        if quiet >= IDLE_SECONDS:
            self.close()
        else:
            self.idle = self.loop.call_later(IDLE_SECONDS - quiet, self._check_idle)

    def close(self) -> None:
        if self.state == "closed":
            return
        self.state = "closed"
        _open.discard(self)
        if self.server_state is not None:
            self.server_state.connections.discard(self)
        if self.idle is not None:
            self.idle.cancel()
        if self.connecting is not None and not self.connecting.done():
            self.connecting.cancel()
        if self.upstream is not None and self.upstream.transport is not None:
            self.upstream.transport.close()
        if self.transport is not None and not self.transport.is_closing():
            self.transport.close()

    def shutdown(self) -> None:
        """Uvicorn roept dit bij het stoppen aan op elke verbinding."""
        self.close()


def counts() -> dict[str, int]:
    """Het aantal open tunnels (O1 voegt per key en per gebruiker toe)."""
    return {"total": sum(1 for t in _open if t.state == "open")}


def close_all() -> int:
    """Alle tunnels dicht (bij het stoppen van de app, vóór events.hub.close()). Het aantal."""
    tunnels = list(_open)
    for tunnel in tunnels:
        tunnel.close()
    return len(tunnels)
