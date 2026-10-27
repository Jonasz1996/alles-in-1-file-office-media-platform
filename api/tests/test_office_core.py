"""De bevroren kern van office (§3.3): keys, adressen, grants, de toestand en het slot per
bestand, OfficeConfig, de instellingen, de pure delen van de tunnel, engine.current, het
poort-cookie, de http-client naar DS en de beheercommando's."""

import asyncio
import json
import secrets
import time
import uuid

import httpx
import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from app import previews, security
from app.config import Settings
from app.office import engine, gate, grants, keys, runtime, tunnel, urls
from app.office.oo import admin, client
from app.office.state import OfficeConfig, OfficePolicy, OfficeState, office_policy, office_state
from tests.factories import maker
from tests.fake_docserver import protocol


def office(tmp_path, **extra) -> Settings:
    values = {
        "data_dir": tmp_path,
        "jwt_secret": "app-" + secrets.token_hex(16),
        "office_url": "http://office",
        "office_app_url": "http://platform:8081",
        "office_secret": secrets.token_urlsafe(32),
        **extra,
    }
    return Settings(**values)


@pytest.fixture
async def bound(tmp_path):
    """Office-instellingen via runtime, met een DocServer op een MockTransport."""
    settings = office(tmp_path)
    runtime.bind(None, settings)
    try:
        yield settings
    finally:
        await engine.close()
        client.transport = None
        runtime.unbind()


# --- keys ---


def test_new_key():
    node = uuid.uuid4()
    first, second = keys.new_key(node), keys.new_key(node)
    assert first != second  # na het sluiten altijd een nieuwe
    assert len(first) == 49 and first.startswith(node.hex + "-")
    assert keys.valid(first)


def test_convert_key():
    sha = "a" * 64
    assert keys.convert_key(sha) == f"p{keys.PDF_VERSION}-{sha}"
    assert keys.convert_key(sha, 1) == keys.convert_key(sha)
    assert keys.convert_key(sha, 3) == f"p{keys.PDF_VERSION}-{sha}-a3"
    assert keys.valid(keys.convert_key(sha, 3))


@pytest.mark.parametrize(
    ("key", "ok"),
    [
        ("k1-abc", True),
        ("a" * 128, True),
        ("A.b_c=d-e", True),
        ("a" * 129, False),
        ("", False),
        (".", False),
        ("..", False),
        (".verborgen", False),
        ("a/b", False),
        ("a b", False),
        ("a%2f", False),
        ("ä", False),
        ("a\n", False),
    ],
)
def test_key_vorm(key, ok):
    assert keys.valid(key) is ok


# --- adressen ---


def test_internal_url(tmp_path):
    settings = office(tmp_path, office_app_url="http://platform:8081/")
    assert urls.internal_url(settings, "/internal/office/doc/k") == (
        "http://platform:8081/internal/office/doc/k"
    )
    with pytest.raises(ValueError):
        urls.internal_url(settings, "internal")


@pytest.mark.parametrize(
    ("url", "kind", "expected"),
    [
        (
            "https://browser.example/office/cache/files/data/k1_12/output.docx/output.docx"
            "?md5=x&expires=1&filename=output.docx",
            "data",
            "/cache/files/data/k1_12/output.docx/output.docx?md5=x&expires=1&filename=output.docx",
        ),
        (
            "http://192.168.1.10/cache/files/data/k1/changes.zip/changes.zip?md5=y",
            "data",
            "/cache/files/data/k1/changes.zip/changes.zip?md5=y",
        ),
        ("/office/cache/files/data/conv_k1_3/output.pdf/x.pdf", "conv", None),
        ("/cache/files/forgotten/k1/output.docx/output.docx", "forgotten", None),
    ],
)
def test_rewrite_neemt_alleen_pad_en_query(tmp_path, url, kind, expected):
    settings = office(tmp_path)
    got = urls.rewrite(settings, url, key="k1", kind=kind)
    path = expected or url.removeprefix("/office")
    assert got == "http://office" + path


@pytest.mark.parametrize(
    "url",
    [
        "",
        "/cache/files/data/k2/output.docx/output.docx",  # een andere key
        "/cache/files/data/k10/output.docx/output.docx",  # begint met de key
        "/cache/files/data/k1/../k2/output.docx",
        "/cache/files/data/k1/./output.docx",
        "/cache/files/data/k1/%2e%2e/x",
        "/cache/files/data/k1/output.docx%2f..",
        "/cache/files/data/k1",  # geen bestand
        "/cache/files/data/k1/.verborgen",
        "/cache/files/forgotten/k1/output.docx",  # verkeerde soort
        "/other/cache/files/data/k1/x",
        "file:///cache/files/data/k1/x",
        "gopher://h/cache/files/data/k1/x",
        "/cache/files/data/k1/a b",
        "/cache/files/data/k1/x\r\nHost: evil",
        "\\\\evil\\cache\\files\\data\\k1\\x",
        "/cache/files/data/k1/x\x00",
    ],
)
def test_rewrite_weigert(tmp_path, url):
    with pytest.raises(ValueError):
        urls.rewrite(office(tmp_path), url, key="k1", kind="data")


def test_rewrite_onbekende_soort(tmp_path):
    with pytest.raises(ValueError):
        urls.rewrite(office(tmp_path), "/x", key="k1", kind="iets")  # type: ignore[arg-type]


def test_rewrite_met_een_key_vol_tekens(tmp_path):
    """De key gaat geëscaped in het patroon: een punt is geen jokerteken."""
    settings = office(tmp_path)
    assert urls.rewrite(settings, "/cache/files/data/a.b/x", key="a.b", kind="data")
    with pytest.raises(ValueError):
        urls.rewrite(settings, "/cache/files/data/aXb/x", key="a.b", kind="data")


# --- grants ---


def test_grants(monkeypatch):
    grants.clear()
    sha = "b" * 64
    one = grants.grant(sha)
    assert len(one) >= 32 and grants.resolve(one) == sha
    assert grants.grant(sha) != one  # elke keer een nieuwe naam
    assert grants.resolve("bestaat-niet") is None
    # Na tien minuten weg
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + grants.TTL + 1)
    assert grants.resolve(one) is None
    grants.clear()


def test_grants_begrensd(monkeypatch):
    grants.clear()
    monkeypatch.setattr(grants, "LIMIT", 3)
    made = [grants.grant(f"{n:064d}") for n in range(5)]
    assert len(grants._grants) == 3
    assert grants.resolve(made[0]) is None and grants.resolve(made[-1]) == f"{4:064d}"
    grants.clear()


# --- toestand, slot en OfficeConfig ---


def test_office_state_standaard():
    class App:
        state = type("S", (), {})()

    app = App()
    state = office_state(app)
    assert state.health == "off" and not state.healthy
    assert office_state(app) is state
    state.health = "ok"
    assert state.healthy


@pytest.mark.anyio
async def test_node_lock_is_hetzelfde_slot_zolang_het_leeft():
    state = OfficeState()
    a, b = uuid.uuid4(), uuid.uuid4()
    lock = state.node_lock(a)
    assert state.node_lock(a) is lock and state.node_lock(b) is not lock
    async with lock:
        waiter = asyncio.create_task(state.node_lock(a).acquire())
        for _ in range(100):
            if lock._waiters:
                break
            await asyncio.sleep(0)
        assert len(lock._waiters) == 1  # wacht op hetzelfde object
    await waiter
    lock.release()
    del lock, waiter
    import gc

    gc.collect()
    assert a not in state._locks  # niemand houdt hem nog: weg


def test_office_config_grenzen():
    assert OfficeConfig() == OfficeConfig(
        enabled=True, max_editors=20, checkpoint_minutes=5, version_minutes=30
    )
    for bad in (
        {"max_editors": 0},
        {"max_editors": 501},
        {"checkpoint_minutes": 0},
        {"checkpoint_minutes": 61},
        {"version_minutes": 4},
        {"version_minutes": 1441},
    ):
        with pytest.raises(ValidationError):
            OfficeConfig(**bad)


@pytest.mark.anyio
async def test_office_policy_leest_en_bewaart(settings):
    async with maker(settings)() as session:
        policy = OfficePolicy()
        assert await policy.get(session) == OfficeConfig()
        await policy.save(session, OfficeConfig(enabled=False, max_editors=5))
    async with maker(settings)() as session:
        fresh = OfficePolicy()
        got = await fresh.get(session)
        assert got.enabled is False and got.max_editors == 5
        await fresh.save(session, OfficeConfig(max_editors=7))  # bestaande rij bijwerken
    async with maker(settings)() as session:
        assert (await OfficePolicy().get(session)).max_editors == 7

    class App:
        state = type("S", (), {})()

    app = App()
    assert office_policy(app) is office_policy(app)


# --- instellingen ---


def test_office_problem(tmp_path):
    base = Settings(data_dir=tmp_path, jwt_secret="app" * 12)
    assert base.office_problem() is None and not base.office_configured
    good = office(tmp_path)
    assert good.office_problem() is None and good.office_configured
    for changes, word in (
        ({"office_url": "office:80"}, "PLATFORM_OFFICE_URL"),
        ({"office_app_url": ""}, "PLATFORM_OFFICE_APP_URL"),
        ({"office_app_url": "platform:8081"}, "PLATFORM_OFFICE_APP_URL"),
        ({"office_secret": ""}, "korter dan 32"),
        ({"office_secret": "x" * 31}, "korter dan 32"),
        ({"office_secret": "app" * 12, "jwt_secret": "app" * 12}, "gelijk"),
    ):
        settings = office(tmp_path, **changes)
        assert word in (settings.office_problem() or ""), changes
        assert not settings.office_configured


def test_office_standaardwaarden(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert settings.office_url == "" and not settings.office_dev_ungated
    assert settings.office_internal_port == 8081
    assert settings.office_ole_max_bytes == 20 * 1024**2
    assert settings.office_max_save_bytes == 512 * 1024**2


# --- de tunnel (puur) ---


def test_parse_head():
    method, target, headers = tunnel.parse_head(
        b"GET /office/x HTTP/1.1\r\nHost: a\r\nUpgrade:  websocket \r\nX-Leeg:\r\n"
    )
    assert (method, target) == (b"GET", b"/office/x")
    assert headers == [(b"host", b"a"), (b"upgrade", b"websocket"), (b"x-leeg", b"")]
    for bad in (
        b"GET /x\r\nHost: a",
        b"GET /x SPDY/3\r\nHost: a",
        b"GET /x HTTP/1.1\r\nkop zonder dubbele punt",
        b"GET /x HTTP/1.1\r\n Host: a",  # vouwen: nooit
        b"GET /x HTTP/1.1\r\n: leeg",
    ):
        with pytest.raises(ValueError):
            tunnel.parse_head(bad)


@pytest.mark.parametrize(
    ("target", "key"),
    [
        (b"/office/10.0.0-0123abcd/doc/k1-abc/c/?EIO=4&transport=websocket", "k1-abc"),
        (b"/office/10.0.0-0123abcd/doc/k1-abc/c/", "k1-abc"),
        (b"/office/10.0.0-" + b"a" * 64 + b"/doc/" + b"k" * 128 + b"/c/", "k" * 128),
        (b"/office/10.0.0-0123abc/doc/k1/c/", None),  # versie te kort
        (b"/office/10.0.0-0123ABCD/doc/k1/c/", None),
        (b"/office/10.0.0-0123abcd/doc/" + b"k" * 129 + b"/c/", None),
        (b"/office/10.0.0-0123abcd/doc/../c/", None),
        (b"/office/10.0.0-0123abcd/doc/k1/c", None),
        (b"/office/10.0.0-0123abcd/doc/k1/c/x", None),
        (b"/10.0.0-0123abcd/doc/k1/c/", None),
        (b"/office/10.0.0-0123abcd/doc/k%31/c/", None),
        ("/office/10.0.0-0123abcd/doc/kä/c/".encode(), None),
    ],
)
def test_session_key(target, key):
    assert tunnel.session_key(target) == key


def test_rewrite_upgrade():
    head = (
        b"GET /office/10.0.0-0123abcd/doc/k1/c/?EIO=4 HTTP/1.1\r\nHost: platform.test\r\n"
        b"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: abc\r\n"
        b"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Extensions: permessage-deflate\r\n"
        b"Cookie: office=geheim\r\nAuthorization: Bearer x\r\nX-Forwarded-For: 6.6.6.6\r\n"
        b"Forwarded: for=6.6.6.6\r\nOrigin: https://platform.test"
    )
    out = tunnel.rewrite_upgrade(
        head, ds_host="office", host="platform.test", scheme="https", client="10.0.0.2"
    )
    assert out.endswith(b"\r\n\r\n")
    lines = out[:-4].split(b"\r\n")
    assert lines[0] == b"GET /10.0.0-0123abcd/doc/k1/c/?EIO=4 HTTP/1.1"
    assert lines[1] == b"Host: office"
    assert set(lines[2:]) == {
        b"upgrade: websocket",
        b"connection: Upgrade",
        b"sec-websocket-key: abc",
        b"sec-websocket-version: 13",
        b"origin: https://platform.test",
        b"X-Forwarded-Prefix: /office",
        b"X-Forwarded-Host: platform.test",
        b"X-Forwarded-Proto: https",
        b"X-Forwarded-For: 10.0.0.2",
    }
    without = tunnel.rewrite_upgrade(head, ds_host="o", host="h", scheme="http", client=None)
    assert b"X-Forwarded-For" not in without
    with pytest.raises(ValueError):
        tunnel.rewrite_upgrade(
            b"GET /officex/a HTTP/1.1\r\nHost: h", ds_host="o", host="h", scheme="http", client=None
        )


def test_refusal_en_tellen():
    assert tunnel.refusal(404) == (
        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )
    assert tunnel.refusal(418).startswith(b"HTTP/1.1 418 Error\r\n")
    assert tunnel.counts() == {"total": 0, "keys": 0, "users": 0}
    assert tunnel.close_all() == 0


class Browser:
    """De transport van de browser, zoals de tunnel hem van uvicorn krijgt."""

    def __init__(self) -> None:
        self.paused = False
        self.written = bytearray()
        self.closing = False

    def pause_reading(self) -> None:
        self.paused = True

    def resume_reading(self) -> None:
        self.paused = False

    def write(self, data: bytes) -> None:
        self.written += data

    def is_closing(self) -> bool:
        return self.closing

    def close(self) -> None:
        self.closing = True

    def set_write_buffer_limits(self, high=None, low=None) -> None:
        pass

    def get_extra_info(self, name, default=None):
        return ("127.0.0.1", 50000) if name == "peername" else default


@pytest.mark.anyio
async def test_tunnel_pauzeert_de_browser_terwijl_ds_verbindt(tmp_path, monkeypatch):
    """S8: zolang DS niet antwoordt, bewaart de tunnel hooguit PENDING_LIMIT en leest hij de
    browser niet meer. Daarna komt alles aan, in volgorde, en leest hij weer. (De poort en de
    StreamGuard van O1 laten hier iedereen door: dit gaat over het wachten op DS.)"""
    from app.office.oo import stream

    async def anyone(headers, key=None):
        return gate.GateUser(uuid.uuid4(), None)

    monkeypatch.setattr(gate, "check", anyone)
    monkeypatch.setattr(stream.Guard, "feed", lambda self, data: "ok")
    received = bytearray()
    enough = asyncio.Event()
    head = (
        b"GET /office/10.0.0-0123abcd/doc/k1/c/?EIO=4 HTTP/1.1\r\nHost: test\r\n"
        b"Origin: http://test\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
    )
    chunks = [bytes([65 + i % 26]) * 1024 for i in range(100)]
    total = len(b"".join(chunks)) + 1000

    async def ds(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(65536):
            received.extend(data)
            if len(received) >= total and b"\r\n\r\n" in received:
                enough.set()
        writer.close()

    server = await asyncio.start_server(ds, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = office(tmp_path, office_url=f"http://127.0.0.1:{port}")
    runtime.bind(None, settings)
    engine.reset()
    loop = asyncio.get_running_loop()
    connect = asyncio.Event()
    real = loop.create_connection

    async def slow(*args, **kwargs):  # een DS die het verbinden laat wachten
        await connect.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(loop, "create_connection", slow)
    t = tunnel.Tunnel(_loop=loop)
    browser = Browser()
    try:
        t.connection_made(browser)
        t.data_received(head + b"x" * 1000)
        for _ in range(100):  # eerst de poort (een taak), dan het verbinden met DS
            if t.state != "checking":
                break
            await asyncio.sleep(0.01)
        assert t.state == "connecting" and not browser.paused
        for chunk in chunks:
            if browser.paused:
                break
            t.data_received(chunk)
        assert browser.paused
        assert t.held <= tunnel.PENDING_LIMIT + 1024
        sent = sum(map(len, t.pending))
        connect.set()
        await t.connecting
        assert t.state == "open" and not browser.paused and t.pending == []
        for chunk in chunks[(sent - 1000) // 1024 :]:  # de rest, nu de browser weer leest
            t.data_received(chunk)
        await asyncio.wait_for(enough.wait(), 5)
        request, _, body = bytes(received).partition(b"\r\n\r\n")
        assert request.startswith(b"GET /10.0.0-0123abcd/doc/k1/c/?EIO=4 HTTP/1.1")
        assert body == b"x" * 1000 + b"".join(chunks)
    finally:
        t.close()
        server.close()
        await server.wait_closed()
        runtime.unbind()
        engine.reset()


# --- previews met de PDF-generatie (Keuze 25) ---


def test_preview_adres_met_v():
    secret, sha, exp = b"s" * 32, "c" * 64, 2_000_000_000
    plain = previews.url(secret, sha, 256, exp)
    assert "&v=" not in plain  # zonder PDF precies het adres van fase 3
    sig = plain.rsplit("sig=", 1)[1]
    assert previews.valid(secret, sha, 256, exp, sig, at=0)
    second = previews.url(secret, sha, 256, exp, v=2)
    assert "&v=2&" in second and second != plain
    sig2 = second.rsplit("sig=", 1)[1]
    assert previews.valid(secret, sha, 256, exp, sig2, at=0, v=2)
    assert not previews.valid(secret, sha, 256, exp, sig2, at=0, v=1)  # v is mee getekend
    assert not previews.valid(secret, sha, 256, exp, sig2, at=0)
    assert not previews.valid(secret, sha, 256, exp, sig, at=0, v=-1)


def test_pdf_generation():
    class Blob:
        pdf_state, pdf_version = None, 0

    blob = Blob()
    assert previews.pdf_generation(None) == 0
    assert previews.pdf_generation(blob) == 0
    blob.pdf_state, blob.pdf_version = "ready", 3
    assert previews.pdf_generation(blob) == 3
    blob.pdf_state = "failed"
    assert previews.pdf_generation(blob) == 0


# --- engine.current ---


def test_engine_current(tmp_path):
    try:
        runtime.bind(None, Settings(data_dir=tmp_path))
        engine.reset()
        assert engine.current() is None and engine.upstream() is None
        runtime.bind(None, office(tmp_path, office_secret="kort"))
        assert engine.current() is None
        settings = office(tmp_path)
        runtime.bind(None, settings)
        first = engine.current()
        assert first is not None and first.name == "onlyoffice" and first.strip_prefix
        assert engine.current() is first  # bewaard voor deze instellingen
        assert {"docx", "xlsx", "pptx"} == set(first.edit_formats)
        assert {"doc", "odt", "rtf"} <= set(first.convert_formats)
        runtime.bind(None, office(tmp_path))
        assert engine.current() is not first  # andere instellingen: een nieuwe
        engine.reset()
        assert engine.current() is not None
    finally:
        engine.reset()
        runtime.unbind()


def test_runtime_zonder_bind_gebruikt_de_omgeving():
    from app.config import get_settings

    runtime.unbind()
    assert runtime.app() is None
    assert runtime.settings() is get_settings()


# --- het poort-cookie ---


def test_gate_cookie(tmp_path):
    settings = office(tmp_path)

    class User:
        id = uuid.uuid4()
        role = "user"

    family = uuid.uuid4()
    response = Response()
    gate.issue(response, settings, User, family, https=True)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("office=")
    lowered = cookie.lower()
    for part in ("httponly", "samesite=strict", "path=/office/", "secure", "max-age=7200"):
        assert part in lowered
    token = cookie.split(";", 1)[0].split("=", 1)[1]
    claims = security._decode(settings, token, gate.GATE_TYPE)
    assert claims["sub"] == str(User.id) and claims["fam"] == str(family)
    # Geen toegang tot de app, en niet getekend met het office-geheim
    with pytest.raises(HTTPException):
        security._decode(settings, token, "access")
    import jwt

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, settings.office_secret, algorithms=["HS256"])
    plain = Response()
    gate.issue(plain, settings, User, None, https=False)
    assert "secure" not in plain.headers["set-cookie"].lower()


@pytest.mark.anyio
async def test_gate_check_stub_laat_niemand_door():
    from starlette.datastructures import Headers

    assert await gate.check(Headers({"cookie": "office=x"})) is None


# --- de http-client naar DS ---


def mock(handler) -> None:
    client.transport = httpx.MockTransport(handler)


class Stream(httpx.AsyncByteStream):
    """Een antwoord in stukken, zoals van het netwerk (content= leest httpx meteen in)."""

    def __init__(self, data: bytes, size: int = 300) -> None:
        self.data, self.size = data, size

    async def __aiter__(self):
        for start in range(0, len(self.data), self.size):
            yield self.data[start : start + self.size]


@pytest.mark.anyio
async def test_client_instellingen(bound):
    server = client.get()
    assert client.get() is server
    assert server.client.trust_env is False and server.client.follow_redirects is False
    assert server.base == "http://office"


@pytest.mark.anyio
async def test_fetch(bound, tmp_path):
    body = b"x" * 1000

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/kapot":
            return httpx.Response(500)
        if request.url.path == "/weg":
            raise httpx.ReadError("weg")
        if request.url.path == "/gzip":
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Stream(body))
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, stream=Stream(body))

    mock(handler)
    server = client.get()
    out = tmp_path / "uit"
    sha, size = await server.fetch("http://office/cache/x", out, 1000)
    assert size == 1000 and out.read_bytes() == body
    import hashlib

    assert sha == hashlib.sha256(body).hexdigest()
    with pytest.raises(client.TooLarge):
        await server.fetch("http://office/cache/x", out, 999)
    assert not out.exists()  # niets bewaard
    with pytest.raises(client.DocServerError) as caught:
        await server.fetch("http://office/kapot", out, 1000)
    assert caught.value.status == 500 and not out.exists()
    with pytest.raises(client.DocServerError):
        await server.fetch("http://office/weg", out, 1000)
    assert not out.exists()
    with pytest.raises(client.DocServerError):
        await server.fetch("http://office/gzip", out, 1000)
    assert not out.exists()
    # Nooit een adres dat niet herschreven is
    for url in ("http://evil/cache/x", "http://office.evil/x", "http://office", "/cache/x"):
        with pytest.raises(ValueError):
            await server.fetch(url, out, 1000)


@pytest.mark.anyio
async def test_command_en_convert(bound):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if body.get("c") == "kaal":
            return httpx.Response(200, text='{"error":6}')  # zoals DS: zonder JSON-type
        if body.get("c") == "lijst":
            return httpx.Response(200, json=[1])
        if body.get("c") == "kapot":
            return httpx.Response(502)
        if body.get("c") == "tekst":
            return httpx.Response(200, text="geen json")
        return httpx.Response(200, json={"error": 0, "echo": body})

    mock(handler)
    server = client.get()
    answer = await server.command("info", key="k1/x")
    assert seen[-1].url.raw_path == b"/command?shardkey=k1%2Fx"
    claims = protocol.decode(bound.office_secret, answer["echo"]["token"])
    assert claims["c"] == "info" and claims["key"] == "k1/x"
    assert await server.command("kaal", signed=False) == {"error": 6}
    assert "token" not in json.loads(seen[-1].content)
    assert seen[-1].url.raw_path == b"/command"
    for c in ("lijst", "kapot", "tekst"):
        with pytest.raises(client.DocServerError):
            await server.command(c)
    converted = await server.convert(filetype="docx", outputtype="pdf")
    assert seen[-1].headers["accept"] == "application/json"
    assert seen[-1].url.raw_path == b"/converter"
    assert protocol.decode(bound.office_secret, converted["echo"]["token"])["filetype"] == "docx"
    # Met een key ook op /converter de shardkey (A18), net als bij /command
    await server.convert(key="conv_k1/x", filetype="docx", outputtype="pdf")
    assert seen[-1].url.raw_path == b"/converter?shardkey=conv_k1%2Fx"
    await server.convert(signed=False, key="k2")
    assert seen[-1].url.raw_path == b"/converter?shardkey=k2"


@pytest.mark.anyio
async def test_open_stuurt_precies_het_pad(bound):
    """S6: open stuurt DS het pad byte voor byte, of niets."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    mock(handler)
    server = client.get()
    for path, sent in (
        ("/", b"/"),
        ("/sdkjs/a.js", b"/sdkjs/a.js"),
        ("/a/%2e%2e/b%2F?x=1", b"/a/%2e%2e/b%2F?x=1"),
        ('/d/k1?cmd={"c":1}', b"/d/k1?cmd={%22c%22:1}"),  # de query mag httpx coderen
    ):
        response = await server.open("GET", path, [])
        await response.aclose()
        assert seen[-1].url.raw_path == sent and seen[-1].url.host == "office", path
    count = len(seen)
    for path in (
        "",
        "sdkjs/a.js",
        "//sdkjs/healthcheck",
        "//evil/x",
        "/a/../command",
        "/./a",
        "/a/.",
        "/a#b",
        "/a b",
        "/a{b}",
        "/café",
    ):
        with pytest.raises(ValueError):
            await server.open("GET", path, [])
    assert len(seen) == count


@pytest.mark.anyio
async def test_healthy_en_onbereikbaar(bound):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg")

    mock(handler)
    server = client.get()
    assert await server.healthy() is False
    with pytest.raises(client.DocServerError):
        await server.command("version")
    with pytest.raises(client.DocServerError):
        await server.convert()


@pytest.mark.anyio
async def test_nieuwe_instellingen_nieuwe_client(tmp_path):
    try:
        runtime.bind(None, office(tmp_path))
        first = client.get()
        runtime.bind(None, office(tmp_path, office_url="http://ander"))
        second = client.get()
        assert second is not first and second.base == "http://ander"
        await client.close()
        assert first.client.is_closed and second.client.is_closed
    finally:
        await engine.close()
        runtime.unbind()


# --- beheercommando's ---


@pytest.mark.anyio
async def test_admin_commandos(bound):
    answers = {
        "version": {"error": 0, "version": "10.0.0.169"},
        "license": protocol.license_answer(20),
        "getForgottenList": {"error": 0, "keys": ["k1", 5, "k2"]},
        "getForgotten": {"error": 0, "url": "http://h/cache/files/forgotten/k1/output.docx/x"},
        "deleteForgotten": {"error": 0},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        claims = protocol.decode(bound.office_secret, json.loads(request.content)["token"])
        return httpx.Response(200, json=answers[claims["c"]])

    mock(handler)
    assert await admin.version() == "10.0.0.169"
    assert await admin.license() == 20
    assert await admin.forgotten_list() == ["k1", "k2"]
    assert (await admin.forgotten_url("k1")).endswith("/forgotten/k1/output.docx/x")
    await admin.delete_forgotten("k1")
    answers["license"] = {"error": 0, "license": {}}
    assert await admin.license() is None
    answers["version"] = {"error": 0}
    with pytest.raises(client.DocServerError):
        await admin.version()
    answers["getForgotten"] = {"error": 1}
    with pytest.raises(client.DocServerError):
        await admin.forgotten_url("k9")
    answers["getForgotten"] = {"error": 0}
    with pytest.raises(client.DocServerError):
        await admin.forgotten_url("k9")


@pytest.mark.anyio
async def test_fetch_forgotten_volgt_alleen_het_herschreven_adres(bound, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/command":
            return httpx.Response(
                200,
                json={
                    "error": 0,
                    "url": "https://evil.example/office/cache/files/forgotten/k1/output.docx/a",
                },
            )
        assert request.url.host == "office"
        assert request.url.path == "/cache/files/forgotten/k1/output.docx/a"
        return httpx.Response(200, stream=Stream(b"inhoud"))

    mock(handler)
    current = engine.current()
    sha, size = await current.fetch_forgotten("k1", tmp_path / "f", 100)
    assert size == 6


# --- de contracttests (§4.4) ---


def test_contract_vorm():
    """tests/office_contract.py heeft de Driver van §4.4 en elk scenario is een coroutine."""
    import inspect as pyinspect

    from tests import office_contract

    methods = {
        name
        for name, value in vars(office_contract.Driver).items()
        if pyinspect.iscoroutinefunction(value)
    }
    assert methods == {
        "open", "type", "save", "close", "reconnect", "fail_callbacks", "crash", "restart_app"
    }  # fmt: skip
    assert len(office_contract.SCENARIOS) == len(set(office_contract.SCENARIOS)) >= 13
    for scenario in office_contract.SCENARIOS:
        assert pyinspect.iscoroutinefunction(scenario)
        assert list(pyinspect.signature(scenario).parameters) == ["driver", "app"]
