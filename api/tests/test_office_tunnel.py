"""De tunnel van O1 (app/office/tunnel.py, Keuze 6 en 7) met echte sockets, zoals
test_serve.py: de publieke luisteraar van serve.py (h11 en httptools, ook https), de echte poort
(gate.check en gate.still), de echte StreamGuard, en als DS de nep-DS van O2 of een kale DS op TCP.

- een upgrade gaat via beide luisteraars naar DS, de tekst van de één komt bij de ander, en de
  verbinding leeft langer dan HEADER_TIMEOUT (hier 0,2 s); ook over https;
- vóór er iets naar DS gaat: ander pad 404, vreemde of geen Origin 403, poort 80 met een
  certificaat 403, geen cookie of geen schrijfrecht meer 401, te veel 429 of 503;
- daarna: de StreamGuard (een fout eerste pakket gaat niet door en sluit), idle, tegendruk in
  beide richtingen, de controle elke 15 s (deling ingetrokken, uitgelogd), en het register
  (counts, close_for, close_all)."""

import asyncio
import base64
import contextlib
import dataclasses
import functools
import os
import time

import httpx
import pytest
from websockets.asyncio.client import connect as ws_connect

from app import serve
from app.certs import HttpsConfig
from app.config import Settings
from app.main import app
from app.office import engine, gate, runtime, sessions, tunnel
from app.office.state import OfficeConfig, office_policy
from tests.factories import PASSWORD
from tests.fake_docserver import client as fake_client
from tests.fake_docserver import protocol
from tests.fake_docserver.client import EditorClient, Refused
from tests.office_world import office_world, wait_until
from tests.test_certs import write_cert
from tests.test_office_stream import frame, opening
from tests.test_serve import client_context, closed_within, running

pytestmark = pytest.mark.anyio

IMPLEMENTATIONS = [serve.GuardedH11, serve.GuardedHttpTools]
HEADER = 0.2


# --- de wereld: de app met office, de nep-DS op een echte poort, een publieke luisteraar ---


def rebind(world, **changes) -> Settings:
    """Andere instellingen voor de app (zelfde database en geheimen)."""
    world.settings = Settings(**{**world.settings.model_dump(), **changes})
    runtime.bind(app, world.settings)
    engine.reset()
    return world.settings


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        rebind(w, office_url=f"http://127.0.0.1:{w.port}")
        gate.forget()
        try:
            yield w
        finally:
            tunnel.close_all()
            gate.forget()


class Clock:
    """De klok van de cache van de poort, vooruit te zetten."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.offset


@pytest.fixture
def clock(monkeypatch) -> Clock:
    found = Clock()
    monkeypatch.setattr(gate, "clock", found)
    return found


@contextlib.asynccontextmanager
async def public(monkeypatch, impl=serve.GuardedHttpTools, certs=None):
    """De publieke luisteraar zoals serve.py hem maakt (ws=Tunnel), met de echte app."""
    monkeypatch.setattr(serve, "HEADER_TIMEOUT", HEADER)
    extra = {}
    if certs is not None:
        extra["ssl_context_factory"] = lambda _config, _default: certs.server_context()
    config = serve._config(runtime.settings(), 0, lifespan="off", **extra)
    assert config.ws is tunnel.Tunnel
    config.http = impl
    async with running(config) as listener:
        try:
            yield listener
        finally:
            tunnel.close_all()


async def opened(world, user, node, **body) -> tuple[dict, str]:
    """De config en het poort-cookie, zoals de editorpagina ze krijgt (POST …/session)."""
    r = await world.open_config(user, node, **body)
    assert r.status_code == 200, r.text
    cookie = r.cookies.get("office")
    assert cookie
    world.client.cookies.clear()
    return r.json()["config"], cookie


def editor(world, listener, cfg: dict, cookie: str | None, *, https: bool = False):
    """Een editor zonder browser, via de tunnel (met de koppen die een browser stuurt)."""
    scheme = "https" if https else "http"
    origin = f"{scheme}://127.0.0.1:{listener.port}"
    headers = {"Origin": origin}
    if cookie:
        headers["Cookie"] = f"office={cookie}"
    found = EditorClient(
        f"ws{'s' if https else ''}://127.0.0.1:{listener.port}/office", cfg, headers
    )
    world.editors.append(found)
    return found


def upgrade(
    port: int,
    key: str = "k1-abc",
    *,
    path: str | None = None,
    cookie: str | None = None,
    origin: str | None = "",
    host: str | None = None,
) -> bytes:
    """Een upgrade zoals een browser hem stuurt. origin "" is dezelfde origin, None geen."""
    host = host or f"127.0.0.1:{port}"
    path = path or f"/office/{protocol.V}/doc/{key}/c/?EIO=4&transport=websocket"
    headers = {
        "Host": host,
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode(),
        "Sec-WebSocket-Version": "13",
    }
    if origin is not None:
        headers["Origin"] = origin or f"http://{host}"
    if cookie:
        headers["Cookie"] = f"office={cookie}"
    lines = [f"GET {path} HTTP/1.1", *(f"{k}: {v}" for k, v in headers.items())]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


async def head_of(reader: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)


async def status_of(port: int, request: bytes, **ssl) -> int:
    """De status van een upgrade, en de verbinding weer dicht."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port, **ssl)
    try:
        writer.write(request)
        head = await head_of(reader)
        return int(head.split(b" ", 2)[1])
    finally:
        writer.close()


class RawDS:
    """Een kale DS op TCP: antwoordt op elke upgrade met 101, telt wat de browser stuurt (en
    bewaart het begin), en stuurt desgewenst `send` bytes terug. Met read=False leest hij na de
    upgrade niets tot `reading` gezet is (de trage DS van de tegendruk)."""

    def __init__(self, send: int = 0, read: bool = True) -> None:
        self.send = send
        self.read = read
        self.reading = asyncio.Event()
        self.heads: list[bytes] = []
        self.received = bytearray()  # de eerste 64 KB van wat de browser stuurde
        self.count = 0
        self.sent = 0
        self.done = asyncio.Event()
        self.server: asyncio.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        pump = None
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            self.heads.append(head)
            writer.write(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Accept: x\r\n\r\n"
            )
            await writer.drain()
            pump = asyncio.ensure_future(self._pump(reader))
            chunk = b"d" * 65536
            while self.sent < self.send:
                piece = chunk[: min(len(chunk), self.send - self.sent)]
                writer.write(piece)
                self.sent += len(piece)
                await writer.drain()
            self.done.set()
            await pump
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if pump is not None:
                pump.cancel()
            writer.close()

    async def _pump(self, reader: asyncio.StreamReader) -> None:
        if not self.read:
            await self.reading.wait()
        with contextlib.suppress(ConnectionError):
            while data := await reader.read(65536):
                self.count += len(data)
                if len(self.received) < 65536:
                    self.received += data[: 65536 - len(self.received)]

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.sockets[0].getsockname()[1]


@contextlib.asynccontextmanager
async def raw_ds(world, **kw):
    """Een RawDS als DS van de app; daarna weer de nep-DS."""
    ds = RawDS(**kw)
    ds.server = await asyncio.start_server(ds.handle, "127.0.0.1", 0)
    rebind(world, office_url=f"http://127.0.0.1:{ds.port}")
    try:
        yield ds
    finally:
        tunnel.close_all()
        ds.server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ds.server.wait_closed(), 5)
        rebind(world, office_url=f"http://127.0.0.1:{world.port}")


async def two_writers(world):
    """Anna (eigenaar) en Bram (deling met write) op één bestand: (anna, bram, node, share)."""
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    return anna, bram, node, share


async def closed(editor_client: EditorClient, seconds: float = 5.0) -> None:
    await wait_until(lambda: not editor_client.reading, seconds)


# --- door de tunnel naar de nep-DS ---


@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda impl: impl.__name__)
async def test_twee_editors_via_de_tunnel(world, monkeypatch, impl):
    anna, bram, node, _ = await two_writers(world)
    cfg_a, cookie_a = await opened(world, anna, node)
    cfg_b, cookie_b = await opened(world, bram, node)
    key = cfg_a["document"]["key"]
    assert cfg_b["document"]["key"] == key
    async with public(monkeypatch, impl) as listener:
        first = editor(world, listener, cfg_a, cookie_a)
        # Editor.bin via de proxy van dezelfde luisteraar, met het cookie (een DATA-pad)
        async with httpx.AsyncClient(
            headers={"Cookie": f"office={cookie_a}"}, trust_env=False
        ) as http:
            first.http = http
            await first.open()
        assert first.content is not None
        second = editor(world, listener, cfg_b, cookie_b)
        await second.open(load=False)
        assert tunnel.counts() == {"total": 2, "keys": 1, "users": 2}
        assert sessions.office().tunnels == {key: {anna.id: 1, bram.id: 1}}
        # Ruim drie keer HEADER_TIMEOUT stil, en toch gaan berichten nog heen en terug
        await asyncio.sleep(HEADER * 3.5)
        await first.type("hallo van Anna")
        await wait_until(lambda: "hallo van Anna" in second.texts_of_others().values())
        await second.type("en van Bram")
        await wait_until(lambda: "en van Bram" in first.texts_of_others().values())
        # Dicht aan de ene kant is dicht aan de andere: uit het register
        await first.close()
        await wait_until(lambda: tunnel.counts()["total"] == 1)
        assert sessions.office().tunnels == {key: {bram.id: 1}}
        assert second.reading


async def test_over_https_met_een_zelfgetekend_certificaat(world, monkeypatch, certs):
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    write_cert(certs, "platform.test")
    assert certs.load_files()
    monkeypatch.setattr(fake_client, "connect", functools.partial(ws_connect, ssl=client_context()))
    async with public(monkeypatch, certs=certs) as listener:
        found = editor(world, listener, cfg, cookie, https=True)
        await found.open(load=False)
        await asyncio.sleep(HEADER * 2)
        await found.type("veilig")
        assert tunnel.counts()["total"] == 1
        # Een http-Origin op een https-verbinding hoort er niet bij
        context = {"ssl": client_context(), "server_hostname": "platform.test"}
        bad = upgrade(listener.port, cfg["document"]["key"], cookie=cookie)
        assert await status_of(listener.port, bad, **context) == 403


# --- vóór er iets naar DS gaat ---


async def test_weigeren_voor_ds_iets_krijgt(world, monkeypatch):
    anna, bram, node, share = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    _, cookie_b = await opened(world, bram, node)
    key = cfg["document"]["key"]
    other = await world.docx(anna, None, "ander.docx", ["x"])
    cfg_other, _ = await opened(world, anna, other)
    async with raw_ds(world) as ds, public(monkeypatch) as listener:
        port = listener.port

        def at(**kw) -> bytes:
            return upgrade(port, key, **kw)

        cases = [
            # Ander pad: 404
            (upgrade(port, path=f"/office/{protocol.V}/doc/{key}/x/", cookie=cookie), 404),
            (upgrade(port, path=f"/office/{protocol.V}/sdkjs/word/a.js", cookie=cookie), 404),
            (upgrade(port, path=f"/office/{protocol.V}/doc/{key}/c/x", cookie=cookie), 404),
            (upgrade(port, path=f"/office/{protocol.V}/doc/../c/", cookie=cookie), 404),
            (upgrade(port, path=f"/{protocol.V}/doc/{key}/c/", cookie=cookie), 404),
            (upgrade(port, path="/api/v1/events", cookie=cookie), 404),
            # Origin: vreemd, geen, ander schema of andere poort
            (at(cookie=cookie, origin="http://evil.example"), 403),
            (at(cookie=cookie, origin=None), 403),
            (at(cookie=cookie, origin=f"https://127.0.0.1:{port}"), 403),
            (at(cookie=cookie, origin=f"http://127.0.0.1:{port + 1}"), 403),
            (at(cookie=cookie, origin="null"), 403),
            # Geen of een kapot cookie, of het cookie van een sessie op een andere key
            (at(), 401),
            (at(cookie="kapot"), 401),
            (upgrade(port, "k1-abc", cookie=cookie), 401),
        ]
        for request, expected in cases:
            assert await status_of(port, request) == expected, request
        # Het cookie van anna hoort niet bij de sessie van het andere bestand van bram
        bram_other = upgrade(port, cfg_other["document"]["key"], cookie=cookie_b)
        assert await status_of(port, bram_other) == 401
        # Geen schrijfrecht meer: de deling is ingetrokken
        await world.revoke(anna, share)
        assert await status_of(port, at(cookie=cookie_b)) == 401
        assert ds.heads == []  # DS kreeg nooit iets
        assert tunnel.counts()["total"] == 0
        # Met het goede cookie wel
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(at(cookie=cookie))
        assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
        writer.close()
        sent = ds.heads[0].decode().lower()
        assert sent.startswith(f"get /{protocol.V}/doc/{key}/c/".lower())
        assert "cookie" not in sent and "office=" not in sent


async def test_poort_80_met_een_certificaat_geen_upgrade(world, monkeypatch, certs):
    """Dezelfde regel als Edge: is er een certificaat voor het domein en draait https, dan geen
    WebSocket over http op dat domein (403). Op een andere host of over https wel."""
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    key = cfg["document"]["key"]
    certs.config = HttpsConfig(domain="platform.test")
    write_cert(certs, "platform.test")
    assert certs.load_files()
    certs.serving = True
    assert certs.active
    async with raw_ds(world) as ds, public(monkeypatch) as listener:
        port = listener.port
        on_domain = upgrade(port, key, cookie=cookie, host="platform.test")
        assert await status_of(port, on_domain) == 403
        with_port = upgrade(port, key, cookie=cookie, host=f"PLATFORM.test:{port}")
        assert await status_of(port, with_port) == 403
        assert ds.heads == []
        assert await status_of(port, upgrade(port, key, cookie=cookie)) == 101
    async with raw_ds(world), public(monkeypatch, certs=certs) as listener:
        port = listener.port
        secure = upgrade(
            port, key, cookie=cookie, host="platform.test", origin="https://platform.test"
        )
        context = {"ssl": client_context(), "server_hostname": "platform.test"}
        assert await status_of(port, secure, **context) == 101


async def test_grens_per_gebruiker_per_key_en_in_totaal(world, monkeypatch):
    anna, bram, node, _ = await two_writers(world)
    cees = await world.user("cees@example.nl")
    folder = (await world.node(node)).parent_id
    await world.share(anna, folder, cees, write=True)
    cfg, cookie_a = await opened(world, anna, node)
    _, cookie_b = await opened(world, bram, node)
    _, cookie_c = await opened(world, cees, node)
    key = cfg["document"]["key"]
    assert world.settings.office_max_tunnels_user == 8
    async with raw_ds(world) as ds, public(monkeypatch) as listener:
        port = listener.port

        async def connected(cookie: str):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(upgrade(port, key, cookie=cookie))
            assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
            return reader, writer

        # Per gebruiker hooguit 8 (ook over meer tabbladen): de negende 429
        mine = [await connected(cookie_a) for _ in range(8)]
        assert tunnel.counts() == {"total": 8, "keys": 1, "users": 1}
        assert await status_of(port, upgrade(port, key, cookie=cookie_a)) == 429
        mine.pop()[1].close()
        await wait_until(lambda: tunnel.counts()["total"] == 7)
        mine.append(await connected(cookie_a))
        for _, writer in mine:
            writer.close()
        await wait_until(lambda: tunnel.counts()["total"] == 0)
        # Per key hooguit max_editors gebruikers: een derde gebruiker 429, een tweede tunnel van
        # iemand die er al in zit wel
        office_policy(app).config = OfficeConfig(max_editors=2)
        held = [await connected(cookie_a), await connected(cookie_b)]
        assert await status_of(port, upgrade(port, key, cookie=cookie_c)) == 429
        held.append(await connected(cookie_b))
        assert tunnel.counts() == {"total": 3, "keys": 1, "users": 2}
        for _, writer in held:
            writer.close()
        await wait_until(lambda: tunnel.counts()["total"] == 0)
        office_policy(app).config = OfficeConfig()
        # In totaal hooguit office_max_tunnels: daarboven 503
        rebind(world, office_url=f"http://127.0.0.1:{ds.port}", office_max_tunnels=2)
        held = [await connected(cookie_a), await connected(cookie_b)]
        assert await status_of(port, upgrade(port, key, cookie=cookie_c)) == 503
        for _, writer in held:
            writer.close()
        await wait_until(lambda: tunnel.counts()["total"] == 0)
    assert sessions.office().peak is not None and sessions.office().peak[0] == 8


async def test_de_poort_kapot_geeft_503(world, monkeypatch):
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)

    async def broken(*args, **kwargs):
        raise RuntimeError("database weg")

    monkeypatch.setattr(gate, "allowed", broken)
    async with raw_ds(world) as ds, public(monkeypatch) as listener:
        request = upgrade(listener.port, cfg["document"]["key"], cookie=cookie)
        assert await status_of(listener.port, request) == 503
        assert ds.heads == []


# --- na de upgrade: StreamGuard, idle, tegendruk ---


async def test_streamguard_laat_alleen_een_goed_eerste_pakket_door(world, monkeypatch):
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    other = await world.docx(anna, None, "ander.docx", ["x"])
    cfg_other, _ = await opened(world, anna, other)
    key = cfg["document"]["key"]
    async with raw_ds(world) as ds, public(monkeypatch) as listener:
        port = listener.port
        # Het token van een andere key op het pad van deze key: gaat niet door, en dicht
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(upgrade(port, key, cookie=cookie))
        assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
        connect_other, _ = opening(cfg_other)
        writer.write(frame(connect_other))
        assert await closed_within(reader, 3)
        writer.close()
        assert ds.count == 0
        await wait_until(lambda: tunnel.counts()["total"] == 0)
        # Het goede pakket gaat door, en daarna leest de tunnel niets meer
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(upgrade(port, key, cookie=cookie))
        assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
        connect, auth = opening(cfg)
        good = frame(connect) + frame(auth)
        writer.write(good)
        writer.write(b"\x00vrij")
        await wait_until(lambda: ds.count == len(good) + 5)
        assert bytes(ds.received) == good + b"\x00vrij"
        assert not await closed_within(reader, HEADER)
        writer.close()


async def test_streamguard_te_laat_gaat_dicht(world, monkeypatch):
    monkeypatch.setattr(tunnel, "GUARD_SECONDS", 0.3)
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    async with raw_ds(world) as ds, public(monkeypatch) as listener:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
        writer.write(upgrade(listener.port, cfg["document"]["key"], cookie=cookie))
        assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
        writer.write(frame("2"))  # alleen een ping: nog geen CONNECT
        assert await closed_within(reader, 3)
        writer.close()
        assert ds.count == len(frame("2"))


async def test_stil_na_de_idle_tijd_dicht(world, monkeypatch):
    monkeypatch.setattr(tunnel, "IDLE_SECONDS", 0.5)
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    async with raw_ds(world), public(monkeypatch) as listener:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
        writer.write(upgrade(listener.port, cfg["document"]["key"], cookie=cookie))
        assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
        connect, auth = opening(cfg)
        writer.write(frame(connect) + frame(auth))
        # Zolang er verkeer is, blijft hij open (ruim twee keer de idle-tijd)
        for _ in range(12):
            await asyncio.sleep(0.1)
            writer.write(frame("3"))
        assert tunnel.counts()["total"] == 1
        # Daarna stil: dicht
        assert await closed_within(reader, 3)
        writer.close()
        await wait_until(lambda: tunnel.counts()["total"] == 0)


async def test_tegendruk_met_een_trage_lezer(world, monkeypatch):
    """DS stuurt 64 MB naar een browser die even niet leest: de tunnel leest DS dan ook niet
    meer (DS blijft hangen ver onder 64 MB), en daarna komt alles aan, in volgorde."""
    total = 64 * 1024**2
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    async with raw_ds(world, send=total) as ds, public(monkeypatch) as listener:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port, limit=2**20)
        writer.write(upgrade(listener.port, cfg["document"]["key"], cookie=cookie))
        assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
        connect, auth = opening(cfg)
        writer.write(frame(connect) + frame(auth))
        stalled = await _stalls(lambda: ds.sent)
        assert stalled < total // 2, stalled
        assert not ds.done.is_set()
        got = 0
        while got < total:
            data = await asyncio.wait_for(reader.read(2**20), 10)
            assert data and set(data) == {ord("d")}
            got += len(data)
        assert got == total
        await asyncio.wait_for(ds.done.wait(), 5)
        writer.close()


async def test_tegendruk_met_een_trage_ds(world, monkeypatch):
    """De browser stuurt 64 MB naar een DS die even niet leest: de tunnel leest de browser dan
    ook niet meer, en daarna komt alles bij DS aan."""
    total = 64 * 1024**2
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    async with raw_ds(world, read=False) as ds, public(monkeypatch) as listener:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
        writer.write(upgrade(listener.port, cfg["document"]["key"], cookie=cookie))
        assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
        connect, auth = opening(cfg)
        first = frame(connect) + frame(auth)
        writer.write(first)
        sent = 0

        async def blast() -> None:
            nonlocal sent
            chunk = b"b" * 65536
            while sent < total:
                writer.write(chunk)
                sent += len(chunk)
                await writer.drain()

        task = asyncio.ensure_future(blast())
        stalled = await _stalls(lambda: sent)
        assert stalled < total // 2, stalled
        assert not task.done()
        ds.reading.set()
        await asyncio.wait_for(task, 20)
        await wait_until(lambda: ds.count == len(first) + total, 20)
        writer.close()


async def _stalls(value, seconds: float = 10.0) -> int:
    """Wachten tot value() niet meer groeit (drie keer 0,2 s gelijk); de waarde dan."""
    last, same = -1, 0
    for _ in range(int(seconds / 0.2)):
        await asyncio.sleep(0.2)
        now = value()
        same = same + 1 if now == last else 0
        last = now
        if same >= 3:
            return now
    raise AssertionError("bleef groeien")


# --- de controle elke 15 s ---


async def test_ingetrokken_deling_sluit_binnen_de_controle(world, monkeypatch, clock):
    monkeypatch.setattr(tunnel, "RECHECK_SECONDS", 0.1)
    anna, bram, node, share = await two_writers(world)
    cfg_a, cookie_a = await opened(world, anna, node)
    cfg_b, cookie_b = await opened(world, bram, node)
    async with public(monkeypatch) as listener:
        first = editor(world, listener, cfg_a, cookie_a)
        await first.open(load=False)
        second = editor(world, listener, cfg_b, cookie_b)
        await second.open(load=False)
        # De eerste controle staat 15 s in de cache
        await wait_until(lambda: any(slot[0] == bram.id for slot in gate._by_user))
        await world.revoke(anna, share)
        await asyncio.sleep(0.5)
        assert second.reading  # nog binnen de 15 s
        clock.offset += 16
        await closed(second, 3)
        assert first.reading  # de eigenaar mag nog
        assert tunnel.counts() == {"total": 1, "keys": 1, "users": 1}
        # Opnieuw verbinden lukt niet: de poort weigert vóór DS
        with pytest.raises(Refused) as refused:
            await second.reconnect()
        assert refused.value.status == 401


async def test_uitloggen_sluit_binnen_de_controle(world, monkeypatch, clock):
    monkeypatch.setattr(tunnel, "RECHECK_SECONDS", 0.1)
    anna, bram, node, _ = await two_writers(world)
    r = await world.client.post(
        "/api/v1/auth/login", json={"email": "bram@example.nl", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    world.client.cookies.clear()
    logged_in = dataclasses.replace(
        bram, headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    cfg, cookie = await opened(world, logged_in, node)
    async with public(monkeypatch) as listener:
        found = editor(world, listener, cfg, cookie)
        await found.open(load=False)
        await wait_until(lambda: any(slot[0] == bram.id for slot in gate._by_user))
        r = await world.client.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert r.status_code == 204
        clock.offset += 16
        await closed(found, 3)
        assert tunnel.counts()["total"] == 0


async def test_de_controle_kapot_is_dicht(world, monkeypatch):
    monkeypatch.setattr(tunnel, "RECHECK_SECONDS", 0.1)
    anna, _, node, _ = await two_writers(world)
    cfg, cookie = await opened(world, anna, node)
    async with public(monkeypatch) as listener:
        found = editor(world, listener, cfg, cookie)
        await found.open(load=False)

        async def broken(*args, **kwargs):
            raise RuntimeError("database weg")

        monkeypatch.setattr(gate, "still", broken)
        await closed(found, 3)


# --- het register ---


async def test_counts_close_for_en_close_all(world, monkeypatch):
    anna, bram, node, _ = await two_writers(world)
    cfg_a, cookie_a = await opened(world, anna, node)
    cfg_b, cookie_b = await opened(world, bram, node)
    key = cfg_a["document"]["key"]
    other = await world.docx(anna, None, "ander.docx", ["x"])
    cfg_o, cookie_o = await opened(world, anna, other)
    assert tunnel.counts() == {"total": 0, "keys": 0, "users": 0}
    async with public(monkeypatch) as listener:
        a = editor(world, listener, cfg_a, cookie_a)
        b = editor(world, listener, cfg_b, cookie_b)
        o = editor(world, listener, cfg_o, cookie_o)
        for found in (a, b, o):
            await found.open(load=False)
        assert tunnel.counts() == {"total": 3, "keys": 2, "users": 2}
        # De ronde (na banned): alleen die gebruiker op die key, ook met de id als tekst
        assert tunnel.close_for(key, str(bram.id)) == 1
        await closed(b)
        assert a.reading and o.reading
        assert tunnel.close_for(key, bram.id) == 0
        assert tunnel.close_for(key, "geen-id") == 0
        assert tunnel.counts() == {"total": 2, "keys": 2, "users": 1}
        assert sessions.office().tunnels == {
            key: {anna.id: 1},
            cfg_o["document"]["key"]: {anna.id: 1},
        }
        # Bij het stoppen van de app: alles dicht
        assert tunnel.close_all() == 2
        await closed(a)
        await closed(o)
        assert tunnel.counts() == {"total": 0, "keys": 0, "users": 0}
        assert sessions.office().tunnels == {}
