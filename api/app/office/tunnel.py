"""De tunnel voor de WebSocket van de editor (Keuze 6 en 7, §3.9 #6).

Uvicorn krijgt ws=Tunnel op de publieke luisteraars (app/serve.py). Bij een upgrade geeft
uvicorn de herbouwde request (request-regel en koppen) als bytes aan data_received [gemeten,
M3]. De app zelf heeft verder geen WebSockets (B17): elke andere upgrade krijgt 404.

Vóór er iets naar DS gaat, in deze volgorde (een kale HTTP-regel en dicht):
- het pad: alleen een upgrade naar /office/<versie>/doc/<key>/c/ (Engine.classify met
  upgrade, de klasse SESSION; office ingesteld). Anders 404;
- Origin: moet er zijn en bij Host en het schema van deze verbinding horen. Anders 403;
- poort 80: is er een certificaat en is de Host het domein, dan geen upgrade over http (dezelfde
  regel als Edge). 403;
- de toegang (gate.check met de key): het poort-cookie, lid van de sessie, niet banned, de
  sessie niet dicht en nu schrijfrecht. Anders 401;
- grenzen: meer dan office_max_tunnels_user (8) van deze gebruiker, of al max_editors andere
  gebruikers op deze key: 429; boven office_max_tunnels (1000) in totaal: 503;
- DS niet bereikbaar binnen CONNECT_SECONDS: 502.

Daarna stuurt hij de herschreven upgrade naar DS (zonder /office, alleen vaste koppen, geen
Cookie, Authorization of Sec-WebSocket-Extensions) en pompt hij bytes, in beide richtingen,
met tegendruk (lezen pauzeren boven 1 MB in de schrijfbuffer van de andere kant, verder onder
256 KB). Terwijl DS nog verbindt, bewaart hij hooguit PENDING_LIMIT van de browser en pauzeert
hij daarna het lezen.

De bytes van de browser gaan langs de StreamGuard van de editor (Engine.stream_guard; bij
OnlyOffice eerst het CONNECT-pakket en het eerste auth-bericht, elk token over de key van het
pad). Zegt die nee, dan gaat dat stuk niet door en sluit de tunnel; is de opening na
GUARD_SECONDS nog niet goed, ook. Daarna blijft hij meelezen, zolang de tunnel open is: DS
verhuist een levende verbinding naar de key van elk nieuw auth-bericht, dus ook dat moet over
de key van het pad gaan.

Elke RECHECK_SECONDS (15 s) kijkt hij of de gebruiker er nog in mag (gate.still, met de cache
van 15 s): nee, of de controle mislukt, dan dicht. Na IDLE_SECONDS zonder bytes dicht. Dicht
aan de ene kant is dicht aan de andere.

Het register: OfficeState.tunnels (key -> gebruiker -> aantal) en OfficeState.peak (het hoogste
totaal sinds de start), counts(), close_for(key, user) (de ronde, na banned), close_login(family)
en close_user(user, keep) (routers/auth.py, een ingetrokken login) en close_all() (bij het
stoppen van de app).
"""

import asyncio
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from starlette.datastructures import Headers

from app.office import engine, gate, keys, proxy, runtime, sessions
from app.office.engine import PathClass
from app.office.state import OfficeConfig, OfficeState, office_policy

log = logging.getLogger("platform.office")

HEAD_LIMIT = 16 * 1024
HIGH_WATER = 1024 * 1024
LOW_WATER = 256 * 1024
IDLE_SECONDS = 120.0
CONNECT_SECONDS = 10.0
GUARD_SECONDS = 10.0  # tot de StreamGuard tevreden is, vanaf de verbinding met DS
RECHECK_SECONDS = 15.0
PENDING_LIMIT = 64 * 1024  # van de browser terwijl DS nog verbindt; daarna lezen pauzeren
PREFIX = b"/office"
VERSION = r"\d+\.\d+\.\d+-[0-9a-f]{8,64}"
SESSION_PATH = re.compile(rf"^/office/{VERSION}/doc/([0-9A-Za-z._=-]{{1,128}})/c/(?:\?.*)?$")
DEFAULT_PORTS = {"http": 80, "https": 443}

# Koppen van de browser die naar DS mogen; de rest (Cookie, Authorization, X-Forwarded-*,
# Forwarded, Sec-WebSocket-Extensions) nooit. Zonder extensies is er geen compressie, zodat de
# StreamGuard de frames kan lezen.
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


def _split_host(value: str, scheme: str) -> tuple[str, int] | None:
    """(host, poort) van een Host-kop of het netloc van een Origin, met de standaardpoort."""
    try:
        parts = urlsplit(f"//{value}")
        port = parts.port
    except ValueError:
        return None
    if parts.hostname is None or parts.username is not None or parts.path:
        return None
    return parts.hostname.lower(), port or DEFAULT_PORTS[scheme]


def same_origin(origin: str, host: str, scheme: str) -> bool:
    """Hoort Origin bij deze Host en dit schema (de standaardpoort telt als geen poort)?"""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme != scheme or parts.path or parts.query or parts.fragment:
        return False
    mine, theirs = _split_host(host, scheme), _split_host(parts.netloc, scheme)
    return mine is not None and mine == theirs


def _plain_host(host: str) -> str:
    """De Host zonder poort, in kleine letters (zoals Edge hem vergelijkt)."""
    host = host.lower()
    return host.rsplit(":", 1)[0] if host.rfind(":") > host.rfind("]") else host


def _https_only(host: str) -> bool:
    """De regel van Edge voor poort 80: met een certificaat voor dit domein geen http."""
    app = runtime.app()
    certs = getattr(app.state, "certs", None) if app is not None else None
    return bool(
        certs is not None
        and certs.serving
        and certs.active
        and _plain_host(host) == certs.config.domain
    )


async def _max_editors() -> int:
    app = runtime.app()
    if app is None:
        return OfficeConfig().max_editors
    policy = office_policy(app)
    if policy.config is None:
        async with sessions.maker()() as session:
            await policy.get(session)
            await session.commit()
    assert policy.config is not None
    return policy.config.max_editors


def _user_id(user: uuid.UUID | str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(user))
    except ValueError:
        return None


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
        self.held = 0  # bytes in pending
        self.held_paused = False  # lezen gepauzeerd omdat pending vol is
        # head | checking (de poort) | connecting (DS) | open | closed
        self.state = "head"
        self.key: str | None = None
        self.user: gate.GateUser | None = None
        self.office: OfficeState | None = None  # waar hij in het register staat
        self.guard = None  # de StreamGuard, zolang de tunnel open is
        self.last = time.monotonic()
        self.idle: asyncio.TimerHandle | None = None
        self.guard_timer: asyncio.TimerHandle | None = None
        self.recheck: asyncio.TimerHandle | None = None
        self.checking: asyncio.Task | None = None
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
            if self.guard is not None and not self._guarded(data):
                return
            self.upstream.transport.write(data)
            return
        if self.state in ("checking", "connecting"):
            self._hold(data)
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
        eng = engine.current() if settings.office_configured else None
        try:
            path = target.decode("ascii")
        except UnicodeDecodeError:
            path = ""
        key = eng.session_key(path) if eng is not None and path else None
        if (
            eng is None
            or method != b"GET"
            or key is None
            or eng.classify("GET", path, True) != PathClass.SESSION
        ):
            if eng is not None:
                proxy.refused("upgrade", method.decode("latin-1"), path.split("?", 1)[0])
            self.refuse(404)
            return
        host = next((v for k, v in headers if k == b"host"), b"").decode("latin-1")
        origin = next((v for k, v in headers if k == b"origin"), b"").decode("latin-1")
        secure = self.transport.get_extra_info("sslcontext") is not None
        scheme = "https" if secure else "http"
        if not same_origin(origin, host, scheme):
            self.refuse(403)
            return
        if not secure and _https_only(host):
            self.refuse(403)
            return
        peer = self.transport.get_extra_info("peername")
        office = urlsplit(settings.office_url)
        try:
            request = rewrite_upgrade(
                head,
                ds_host=office.netloc,
                host=host,
                scheme=scheme,
                client=peer[0] if isinstance(peer, tuple) else None,
            )
        except ValueError:
            self.refuse(400)
            return
        self.key = key
        if rest:
            self._hold(rest)
        self.state = "checking"
        self.connecting = self.loop.create_task(self._admit(Headers(raw=headers), office, request))

    async def _admit(self, headers: Headers, office, request: bytes) -> None:
        """De poort en de grenzen, dan DS."""
        assert self.key is not None
        try:
            user = await gate.check(headers, self.key)
            limit = await _max_editors() if user is not None else 0
        except Exception:  # noqa: BLE001 - de database: liever dicht dan open
            log.exception("office: de toegangscontrole van de tunnel mislukte")
            self.refuse(503)
            return
        if self.state != "checking":  # intussen dicht
            return
        if user is None:
            self.refuse(401)
            return
        status = _limits(self.key, user.user_id, limit)
        if status is not None:
            log.info("office: tunnel geweigerd (%d)", status)
            self.refuse(status)
            return
        self._register(user)
        self.state = "connecting"
        await self._connect(office, request)

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
        eng = engine.current()
        if eng is None:
            upstream.transport.close()
            self.refuse(404)
            return
        self.upstream = upstream
        self.guard = eng.stream_guard(self.key)
        self.state = "open"
        if self.held_paused:
            # Eerst weer lezen, dan schrijven: zo kan pause_writing van DS het nog stoppen
            self.held_paused = False
            self.pause_browser(False)
        upstream.transport.write(request)
        # Tot de opening goed is; _guarded zet hem af bij "ok"
        self.guard_timer = self.loop.call_later(GUARD_SECONDS, self._guard_late)
        held, self.pending, self.held = self.pending, [], 0
        for data in held:
            if self.guard is not None and not self._guarded(data):
                return
            upstream.transport.write(data)
        self._arm_idle()
        self._arm_recheck()

    def _guarded(self, data: bytes) -> bool:
        """Mag dit stuk door de StreamGuard? Nee: de tunnel gaat dicht."""
        verdict = self.guard.feed(data)
        if verdict == "deny":
            log.warning("office: de socket van de browser klopt niet; de tunnel gaat dicht")
            self.close()
            return False
        if verdict == "ok" and self.guard_timer is not None:  # de opening is goed
            self.guard_timer.cancel()
            self.guard_timer = None
        return True

    def _guard_late(self) -> None:
        self.guard_timer = None
        if self.state == "open":
            log.warning("office: geen geldig eerste socketpakket op tijd; de tunnel gaat dicht")
            self.close()

    def _hold(self, data: bytes) -> None:
        """Bewaren tot DS verbonden is; boven PENDING_LIMIT de browser niet meer lezen, zodat
        een DS die niet antwoordt geen onbegrensde buffer geeft."""
        self.pending.append(data)
        self.held += len(data)
        if self.held > PENDING_LIMIT and not self.held_paused:
            self.held_paused = True
            self.pause_browser(True)

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

    # --- het register ---

    def _register(self, user: gate.GateUser) -> None:
        assert self.key is not None
        self.user = user
        self.office = state = sessions.office()
        users = state.tunnels.setdefault(self.key, {})
        users[user.user_id] = users.get(user.user_id, 0) + 1
        total = sum(sum(per.values()) for per in state.tunnels.values())
        if state.peak is None or total > state.peak[0]:
            state.peak = (total, datetime.now(UTC))

    def _unregister(self) -> None:
        state, user, key = self.office, self.user, self.key
        self.office = None
        if state is None or user is None or key is None:
            return
        users = state.tunnels.get(key)
        if users is None:
            return
        left = users.get(user.user_id, 0) - 1
        if left > 0:
            users[user.user_id] = left
        else:
            users.pop(user.user_id, None)
        if not users:
            state.tunnels.pop(key, None)

    # --- tijd: idle en de controle elke 15 s ---

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

    def _arm_recheck(self) -> None:
        if self.state == "open":
            self.recheck = self.loop.call_later(RECHECK_SECONDS, self._start_recheck)

    def _start_recheck(self) -> None:
        if self.state == "open":
            self.checking = self.loop.create_task(self._recheck())

    async def _recheck(self) -> None:
        assert self.user is not None and self.key is not None
        try:
            allowed = await gate.still(self.user, self.key)
        except Exception:  # noqa: BLE001 - liever dicht dan open; de editor verbindt opnieuw
            log.exception("office: de controle van een tunnel mislukte")
            allowed = False
        if self.state != "open":
            return
        if not allowed:
            log.info("office: geen recht meer; de tunnel gaat dicht")
            self.close()
            return
        self._arm_recheck()

    # --- beide ---

    def close(self) -> None:
        if self.state == "closed":
            return
        self.state = "closed"
        _open.discard(self)
        self._unregister()
        if self.server_state is not None:
            self.server_state.connections.discard(self)
        for timer in (self.idle, self.guard_timer, self.recheck):
            if timer is not None:
                timer.cancel()
        try:
            current = asyncio.current_task()
        except RuntimeError:  # buiten de loop (tests)
            current = None
        for task in (self.connecting, self.checking):
            if task is not None and not task.done() and task is not current:
                task.cancel()
        if self.upstream is not None and self.upstream.transport is not None:
            self.upstream.transport.close()
        if self.transport is not None and not self.transport.is_closing():
            self.transport.close()

    def shutdown(self) -> None:
        """Uvicorn roept dit bij het stoppen aan op elke verbinding."""
        self.close()


def _limits(key: str, user_id: uuid.UUID, max_editors: int) -> int | None:
    """429 of 503 als er geen tunnel meer bij mag, anders None."""
    settings = runtime.settings()
    admitted = [t for t in _open if t.office is not None and t.user is not None]
    if len(admitted) >= settings.office_max_tunnels:
        return 503
    if sum(1 for t in admitted if t.user.user_id == user_id) >= settings.office_max_tunnels_user:
        return 429
    others = {t.user.user_id for t in admitted if t.key == key and t.user.user_id != user_id}
    if len(others) >= max_editors:
        return 429
    return None


def counts() -> dict[str, int]:
    """Het aantal open tunnels, en op hoeveel keys en van hoeveel gebruikers."""
    tunnels = [t for t in _open if t.state == "open" and t.user is not None]
    return {
        "total": len(tunnels),
        "keys": len({t.key for t in tunnels}),
        "users": len({t.user.user_id for t in tunnels}),
    }


def close_for(key: str, user: uuid.UUID | str) -> int:
    """De tunnels van deze gebruiker op deze key dicht (de ronde, na banned). Het aantal."""
    wanted = _user_id(user)
    found = [t for t in _open if t.key == key and t.user is not None and t.user.user_id == wanted]
    for tunnel in found:
        tunnel.close()
    return len(found)


def close_login(family: uuid.UUID) -> int:
    """Eén login is ingetrokken (uitloggen, een sessie beëindigen, een hergebruikt token;
    routers/auth.py): de poort vergeet hem, en zijn tunnels gaan meteen dicht, niet pas bij de
    volgende controle (15 s, plus de cache). Het aantal."""
    gate.forget_login(family)
    found = [t for t in _open if t.user is not None and t.user.family == family]
    for tunnel in found:
        tunnel.close()
    return len(found)


def close_user(user: uuid.UUID, keep: uuid.UUID | None = None) -> int:
    """Alle logins van een gebruiker behalve keep zijn ingetrokken (wachtwoord, 2FA, blokkeren,
    overal anders uitloggen): de poort vergeet de gebruiker (keep wordt dus opnieuw
    gecontroleerd), en de tunnels van de andere logins gaan meteen dicht. Het aantal."""
    gate.forget_user(user)
    found = [
        t
        for t in _open
        if t.user is not None and t.user.user_id == user and (keep is None or t.user.family != keep)
    ]
    for tunnel in found:
        tunnel.close()
    return len(found)


def close_all() -> int:
    """Alle tunnels dicht (bij het stoppen van de app, vóór events.hub.close()). Het aantal."""
    tunnels = list(_open)
    for tunnel in tunnels:
        tunnel.close()
    return len(tunnels)
