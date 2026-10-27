"""De upgrade naar de tunnel in serve.py (Keuze 6 en 8, §3.8, M1): een geüpgradede verbinding
blijft open na HEADER_TIMEOUT, met h11 en met httptools; een stille gewone verbinding sluit
nog steeds; elke upgrade naar een ander pad (of zonder office) geeft 404, en zonder
poort-cookie 401, ook met de oude vlag office_dev_ungated; de interne poort kent alleen
/internal/office/…, de publieke poort geeft 404 op /internal/….

Waar het hier om serve.py gaat (de deadline, 502, een echte socket), laten de poort en de
StreamGuard van O1 iedereen door (fixture open_gate); die staan zelf in test_office_tunnel.py,
test_office_gate.py en test_office_stream.py."""

import asyncio
import contextlib
import secrets
import socket
import uuid

import pytest
import uvicorn
from websockets.asyncio.client import connect

from app import serve
from app.config import Settings
from app.office import engine, gate, runtime, tunnel
from app.office.oo import stream
from tests.fake_docserver import FakeConfig, create_app, protocol
from tests.test_serve import closed_within, echo, request, response, running

assert serve.HttpToolsProtocol is not None, "httptools hoort erbij (M1)"
IMPLEMENTATIONS = [serve.GuardedH11, serve.GuardedHttpTools]
TIMEOUT = 0.5
VERSION = "10.0.0-0123abcd"
UPGRADE_PATH = f"/office/{VERSION}/doc/k1-abc/c/?EIO=4&transport=websocket"


def upgrade(path: str = UPGRADE_PATH, **extra: str) -> bytes:
    headers = {
        "Host": "platform.test",
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
        "Origin": "http://platform.test",
        **extra,
    }
    lines = [f"GET {path} HTTP/1.1", *(f"{k}: {v}" for k, v in headers.items())]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class Echo:
    """Een nep-DS op TCP: antwoordt op de upgrade met 101 en stuurt daarna alles terug."""

    def __init__(self) -> None:
        self.heads: list[bytes] = []
        self.server: asyncio.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            self.heads.append(head)
            writer.write(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Accept: x\r\n\r\n"
            )
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    @property
    def port(self) -> int:
        return self.server.sockets[0].getsockname()[1]


@pytest.fixture
async def ds():
    fake = Echo()
    fake.server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    try:
        yield fake
    finally:
        fake.server.close()
        await fake.server.wait_closed()


def office(tmp_path, port: int, **extra) -> Settings:
    return Settings(
        data_dir=tmp_path,
        jwt_secret="app-" + secrets.token_hex(16),
        office_url=f"http://127.0.0.1:{port}",
        office_app_url="http://127.0.0.1:8081",
        office_secret=secrets.token_urlsafe(32),
        **extra,
    )


@pytest.fixture
def open_gate(monkeypatch):
    """De poort en de StreamGuard van O1 laten iedereen door."""

    async def anyone(headers, key=None):
        return gate.GateUser(uuid.uuid4(), None)

    monkeypatch.setattr(gate, "check", anyone)
    monkeypatch.setattr(stream.Guard, "feed", lambda self, data: "ok")


@contextlib.contextmanager
def bound(settings: Settings):
    runtime.bind(None, settings)
    engine.reset()
    try:
        yield settings
    finally:
        runtime.unbind()
        engine.reset()
        tunnel.close_all()


@contextlib.asynccontextmanager
async def public(settings: Settings, impl, monkeypatch, app=echo):
    monkeypatch.setattr(serve, "HEADER_TIMEOUT", TIMEOUT)
    monkeypatch.setattr(serve, "app", app)
    config = serve._config(settings, 0, lifespan="off")
    assert config.ws is tunnel.Tunnel
    config.http = impl
    with bound(settings):
        async with running(config) as listener:
            yield listener


async def head_of(reader: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)


@pytest.mark.anyio
@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda impl: impl.__name__)
async def test_tunnel_blijft_open_na_de_header_deadline(tmp_path, monkeypatch, ds, impl, open_gate):
    settings = office(tmp_path, ds.port)
    async with public(settings, impl, monkeypatch) as listener:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
        writer.write(
            upgrade(
                Cookie="platform_session=geheim",
                Authorization="Bearer geheim",
                **{"Sec-WebSocket-Extensions": "permessage-deflate", "X-Forwarded-For": "1.2.3.4"},
            )
        )
        assert (await head_of(reader)).startswith(b"HTTP/1.1 101 ")
        assert tunnel.counts()["total"] == 1
        for round in range(3):  # samen ruim drie keer HEADER_TIMEOUT stil
            await asyncio.sleep(TIMEOUT * 1.2)
            message = f"bericht {round}".encode()
            writer.write(message)
            assert await asyncio.wait_for(reader.readexactly(len(message)), 5) == message
        # Wat DS kreeg: zonder /office, Host van DS, alleen de vaste koppen
        sent = ds.heads[0].decode()
        assert sent.startswith(f"GET /{VERSION}/doc/k1-abc/c/?EIO=4&transport=websocket ")
        assert f"Host: 127.0.0.1:{ds.port}\r\n" in sent
        assert "X-Forwarded-Prefix: /office\r\n" in sent
        assert "X-Forwarded-Host: platform.test\r\n" in sent
        assert "X-Forwarded-Proto: http\r\n" in sent
        assert "X-Forwarded-For: 127.0.0.1\r\n" in sent  # de echte peer, niet de kop
        lowered = sent.lower()
        for name in ("cookie", "authorization", "sec-websocket-extensions", "1.2.3.4"):
            assert name not in lowered
        # Dicht aan de ene kant is dicht aan de andere
        writer.close()
        for _ in range(100):
            if tunnel.counts()["total"] == 0:
                break
            await asyncio.sleep(0.02)
        assert tunnel.counts()["total"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda impl: impl.__name__)
async def test_stille_gewone_verbinding_sluit_nog_steeds(tmp_path, monkeypatch, ds, impl):
    settings = office(tmp_path, ds.port)
    async with public(settings, impl, monkeypatch) as listener:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
        assert await closed_within(reader, TIMEOUT * 6)
        writer.close()
        # Ook na een gewoon antwoord (keep-alive) zonder volgende request
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
        writer.write(request())
        assert (await response(reader))[0] == 200
        assert await closed_within(reader, TIMEOUT * 6)
        writer.close()


@pytest.mark.anyio
@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda impl: impl.__name__)
async def test_zonder_cookie_of_buiten_het_pad_geen_upgrade(tmp_path, monkeypatch, ds, impl):
    """Ander pad of geen office: 404. Het goede pad zonder poort-cookie: 401, met of zonder de
    oude vlag office_dev_ungated (die opent niets meer). DS krijgt nooit iets."""
    cases = [
        (office(tmp_path, ds.port), UPGRADE_PATH, 401),  # office aan, geen cookie
        (office(tmp_path, ds.port, office_dev_ungated=True), UPGRADE_PATH, 401),  # de vlag
        (Settings(data_dir=tmp_path, office_dev_ungated=True), UPGRADE_PATH, 404),  # geen office
        (office(tmp_path, ds.port, office_dev_ungated=True), "/api/v1/events", 404),  # ander pad
        (office(tmp_path, ds.port, office_dev_ungated=True), "/office/x/doc/k1-abc/c/", 404),
        (office(tmp_path, ds.port), f"/office/{VERSION}/doc/../c/", 404),
        (office(tmp_path, ds.port), "/", 404),
    ]
    for settings, path, status in cases:
        async with public(settings, impl, monkeypatch) as listener:
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
            writer.write(upgrade(path))
            head = await head_of(reader)
            assert head.startswith(b"HTTP/1.1 %d " % status), (path, head)
            assert await closed_within(reader, 2)
            writer.close()
    assert ds.heads == []  # DS kreeg nooit iets


@pytest.mark.anyio
async def test_ds_onbereikbaar_geeft_502(tmp_path, monkeypatch, open_gate):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # na het sluiten luistert hier niemand
    settings = office(tmp_path, port)
    async with public(settings, serve.GuardedH11, monkeypatch) as listener:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
        writer.write(upgrade())
        assert (await head_of(reader)).startswith(b"HTTP/1.1 502 ")
        writer.close()


@pytest.mark.anyio
async def test_een_echte_websocket_naar_de_nep_ds(tmp_path, monkeypatch, open_gate):
    """Door de tunnel naar de nep-DS (uvicorn met websockets): het eerste pakket komt aan,
    en na HEADER_TIMEOUT gaan er nog steeds frames heen en terug."""
    fake = create_app(FakeConfig(secret=secrets.token_urlsafe(32)))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    ds_config = uvicorn.Config(fake, ws="websockets-sansio", log_level="warning")
    ds_server = uvicorn.Server(ds_config)
    ds_task = asyncio.ensure_future(ds_server.serve(sockets=[sock]))
    try:
        for _ in range(500):
            if ds_server.started:
                break
            await asyncio.sleep(0.01)
        settings = office(tmp_path, sock.getsockname()[1])
        async with public(settings, serve.GuardedHttpTools, monkeypatch) as listener:
            path = f"/office/{protocol.V}/doc/k1-abc/c/?EIO=4&transport=websocket"
            url = f"ws://127.0.0.1:{listener.port}{path}"
            origin = f"http://127.0.0.1:{listener.port}"
            async with connect(url, compression=None, open_timeout=5, origin=origin) as ws:
                first = await asyncio.wait_for(ws.recv(), 5)
                assert protocol.parse(first)[0] == "open"
                await asyncio.sleep(TIMEOUT * 2)
                await ws.send("40" + '{"token":"kapot"}')
                reply = await asyncio.wait_for(ws.recv(), 5)
                assert protocol.parse(reply)[0] in ("connect_error", "message", "event")
    finally:
        ds_server.should_exit = True
        await asyncio.wait_for(ds_task, 10)
        sock.close()


@pytest.mark.anyio
@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda impl: impl.__name__)
async def test_interne_poort_kent_alleen_internal_office(tmp_path, impl):
    settings = office(tmp_path, 9, office_internal_host="127.0.0.1", office_internal_port=0)
    config = serve._internal_config(settings)
    assert config.ws == "none" and config.lifespan == "off"
    config.http = impl
    with bound(settings):
        async with running(config) as listener:
            for method, path, status in (
                # De routes van O2: zonder token van DS 401 office_token
                ("GET", "/internal/office/doc/k1-abc", 401),
                ("POST", "/internal/office/callback", 401),
                ("GET", "/internal/office/src/abc", 401),  # O3: zonder token van DS
                ("GET", "/api/v1/health", 404),
                ("GET", "/", 404),
                ("GET", "/index.html", 404),
                ("GET", f"/office/{VERSION}/web-apps/apps/api/documents/api.js", 404),
                ("GET", "/internal/elders", 404),
            ):
                reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
                writer.write(request(method, path, 0 if method == "POST" else None))
                got, body = await response(reader)
                assert got == status, (path, body)
                writer.close()
            # Geen WebSocket op de interne poort: een upgrade is een gewoon verzoek (404)
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
            writer.write(upgrade())
            assert (await response(reader))[0] == 404
            writer.close()


@pytest.mark.anyio
@pytest.mark.parametrize("impl", IMPLEMENTATIONS, ids=lambda impl: impl.__name__)
async def test_publieke_poort_geeft_404_op_internal(tmp_path, monkeypatch, ds, impl):
    from app.main import app

    settings = office(tmp_path, ds.port)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    async with public(settings, impl, monkeypatch, app=app) as listener:
        for method, path in (
            ("GET", "/internal/office/doc/k1-abc"),
            ("POST", "/internal/office/callback"),
            ("GET", "/internal/office/src/abc"),
            ("PUT", "/internal"),
            ("GET", "/internal/"),
        ):
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
            writer.write(request(method, path, 0 if method in ("POST", "PUT") else None))
            got, body = await response(reader)
            assert got == 404, (path, body)
            assert b'"not_found"' in body
            writer.close()
