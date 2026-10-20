"""GET /api/v1/events: de stroom met gebeurtenissen. De tests praten rechtstreeks met de
ASGI-app (de testclient van httpx wacht op het hele antwoord, en een stroom eindigt niet),
en via serve.run en een kale uvicorn: stoppen mag niet wachten op open stromen."""

import asyncio
import contextlib
import signal
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

from app import events
from app.config import get_settings
from app.db import get_engine
from app.main import app
from app.routers import events as live
from app.security import SecurityConfig, SecurityPolicy, _encode
from tests.conftest import ADMIN
from tests.test_serve import free_port, platform


@pytest.fixture
def hub(monkeypatch):
    fresh = events.Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


class Stream:
    """Eén verzoek naar de app, zoals uvicorn het doorgeeft; disconnect() is de client die
    weggaat."""

    def __init__(self, headers: dict[str, str]):
        self.headers = [(b"host", b"test")] + [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ]
        self.messages: asyncio.Queue[dict] = asyncio.Queue()
        self.gone = asyncio.Event()
        self.requested = False
        self.text = ""
        self.ended = False

    async def receive(self) -> dict:
        if not self.requested:
            self.requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        await self.messages.put(message)

    async def open(self) -> dict:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/events",
            "raw_path": b"/api/v1/events",
            "query_string": b"",
            "root_path": "",
            "headers": self.headers,
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
        }
        self.task = asyncio.create_task(app(scope, self.receive, self.send))
        start = await asyncio.wait_for(self.messages.get(), 5)
        assert start["type"] == "http.response.start"
        return start

    async def until(self, text: str, seconds: float = 5) -> str:
        """Leest tot `text` binnen is; geeft alles tot en met dat stuk."""
        async with asyncio.timeout(seconds):
            while text not in self.text:
                message = await self.messages.get()
                self.text += message.get("body", b"").decode()
                if not message.get("more_body", False):
                    self.ended = True
                    break
        head, found, rest = self.text.partition(text)
        self.text = rest
        return head + found

    async def end(self, seconds: float = 5) -> None:
        async with asyncio.timeout(seconds):
            while not self.ended:
                message = await self.messages.get()
                self.text += message.get("body", b"").decode()
                self.ended = not message.get("more_body", False)
        await asyncio.wait_for(self.task, seconds)

    async def disconnect(self) -> None:
        self.gone.set()
        await asyncio.wait_for(self.task, 5)


def user_id(tokens) -> uuid.UUID:
    return uuid.UUID(tokens["user"]["id"])


@pytest.mark.anyio
async def test_gebeurtenissen_komen_aan(client, admin, hub):
    headers, tokens = admin
    stream = Stream(headers)
    start = await stream.open()
    assert start["status"] == 200
    response_headers = dict(start["headers"])
    assert response_headers[b"content-type"].startswith(b"text/event-stream")
    assert response_headers[b"cache-control"] == b"no-store"
    assert await stream.until("\n\n") == "retry: 5000\n\n"
    await asyncio.sleep(0)
    assert hub.listeners(user_id(tokens)) == 1

    hub.publish([uuid.uuid4()], "node.created", {"id": "van-een-ander"})  # niet voor ons
    hub.publish([user_id(tokens)], "node.created", {"id": "a", "parent_id": "p"})
    hub.publish([user_id(tokens)], "preview.ready", {"sha": "s", "node_ids": ["a"]})
    assert await stream.until("\n\n") == 'event: node.created\ndata: {"id":"a","parent_id":"p"}\n\n'
    assert await stream.until("\n\n") == (
        'event: preview.ready\ndata: {"sha":"s","node_ids":["a"]}\n\n'
    )
    await stream.disconnect()
    assert hub.listeners(user_id(tokens)) == 0  # de client ging weg: inschrijving weg


@pytest.mark.anyio
async def test_ping_en_daarna_nog_steeds_gebeurtenissen(client, admin, hub, monkeypatch):
    monkeypatch.setattr(live, "PING", 0.1)
    headers, tokens = admin
    stream = Stream(headers)
    await stream.open()
    await stream.until("retry: 5000\n\n")
    assert await stream.until("\n\n") == ": ping\n\n"
    assert await stream.until("\n\n") == ": ping\n\n"
    hub.publish([user_id(tokens)], "node.deleted", {"id": "x"})
    assert "event: node.deleted" in await stream.until("event: node.deleted")
    await stream.disconnect()


@pytest.mark.anyio
async def test_geen_databaseverbinding_tijdens_de_stroom(client, admin, hub, settings):
    headers, _ = admin
    streams = [Stream(headers) for _ in range(3)]
    for stream in streams:
        await stream.open()
        await stream.until("retry: 5000\n\n")
    assert get_engine(settings).pool.checkedout() == 0
    for stream in streams:
        await stream.disconnect()


@pytest.mark.anyio
async def test_sluiten_van_de_hub_rondt_stromen_af(client, admin, hub):
    headers, tokens = admin
    stream = Stream(headers)
    await stream.open()
    await stream.until("retry: 5000\n\n")
    hub.close()
    await stream.end()
    assert hub.listeners(user_id(tokens)) == 0


@pytest.mark.anyio
async def test_stroom_stopt_als_het_token_verloopt(client, admin, hub, settings):
    _, tokens = admin
    short = _encode(
        settings,
        {"sub": tokens["user"]["id"], "role": "admin", "typ": "access", "fam": str(uuid.uuid4())},
        minutes=2 / 60,  # twee seconden (exp is in hele seconden)
    )
    stream = Stream({"Authorization": f"Bearer {short}"})
    started = time.monotonic()
    await stream.open()
    await stream.end(seconds=5)
    assert 0.9 < time.monotonic() - started < 5
    assert stream.text.startswith("retry: 5000")


@pytest.mark.anyio
async def test_stroom_stopt_als_de_login_is_ingetrokken(client, admin, hub, monkeypatch):
    """Uitgelogd (hier: deze sessie zelf beëindigd): binnen één ping geen gebeurtenissen meer,
    niet pas als het access-token verloopt."""
    monkeypatch.setattr(live, "PING", 0.1)
    headers, tokens = admin
    stream = Stream(headers)
    await stream.open()
    await stream.until("retry: 5000\n\n")
    assert await stream.until("\n\n") == ": ping\n\n"  # nog ingelogd: de stroom loopt
    sessions = (await client.get("/api/v1/auth/sessions", headers=headers)).json()
    mine = next(s["id"] for s in sessions if s["current"])
    assert (
        await client.delete(f"/api/v1/auth/sessions/{mine}", headers=headers)
    ).status_code == 204
    await stream.end(seconds=2)
    assert hub.listeners(user_id(tokens)) == 0
    hub.publish([user_id(tokens)], "node.created", {"id": "te-laat"})
    assert "te-laat" not in stream.text


@pytest.mark.anyio
async def test_zonder_token_geen_stroom(client, hub):
    assert (await client.get("/api/v1/events")).status_code == 401
    r = await client.get("/api/v1/events", headers={"Authorization": "Bearer onzin"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_stoppen_wacht_niet_op_open_stromen(tmp_path, monkeypatch):
    """python -m app.serve moet op SIGTERM meteen stoppen, ook met open stromen: uvicorn
    wacht op lopende antwoorden, en een stroom eindigt anders nooit."""
    opened: list[asyncio.Event] = [asyncio.Event(), asyncio.Event()]
    ended: list[bool] = []
    tasks = []
    before = signal.getsignal(signal.SIGINT)
    try:
        async with platform(tmp_path, monkeypatch, free_port()) as c:
            app.state.security = SecurityPolicy(SecurityConfig(require_2fa=False))
            r = await c.post("/api/v1/auth/setup", json=ADMIN)
            assert r.status_code == 201, r.text
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

            async def watch(ready: asyncio.Event) -> None:
                async with (
                    httpx.AsyncClient(base_url=c.base_url, trust_env=False, timeout=30) as s,
                    s.stream("GET", "/api/v1/events", headers=headers) as response,
                ):
                    assert response.status_code == 200
                    async for chunk in response.aiter_text():
                        if "retry:" in chunk:
                            ready.set()
                ended.append(True)

            tasks = [asyncio.create_task(watch(e)) for e in opened]
            for event in opened:
                await asyncio.wait_for(event.wait(), 5)
            start = time.monotonic()
        # Hier is serve.run gestopt (platform wacht erop)
        assert time.monotonic() - start < 5
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        assert ended == [True, True]
        assert not events.hub.closed  # een volgende start in dit proces kan weer
        assert signal.getsignal(signal.SIGINT) is before  # de lifespan zette hem terug
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(AttributeError):
            del app.state.security


@contextlib.asynccontextmanager
async def plain_uvicorn(tmp_path, monkeypatch):
    """Het platform zoals `uvicorn app.main:app` het draait (de README met --reload, de image
    van het cluster): met uvicorns eigen signaalafhandeling en zonder serve.run. Geeft de
    server en een http-client."""
    port = free_port()
    for name, value in {
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_WEB_DIST": str(tmp_path / "web"),
        "PLATFORM_JWT_SECRET": "test" * 8,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="on", log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5
        ) as c:
            for _ in range(200):
                if server.started:
                    break
                assert not task.done(), "uvicorn stopte"
                await asyncio.sleep(0.05)
            yield server, task, c
    finally:
        events.hub.close()  # ook als de test faalt: niet blijven hangen
        server.should_exit = True
        await asyncio.wait_for(task, 20)
        get_settings.cache_clear()
        for name in ("certs", "security"):
            with contextlib.suppress(AttributeError):
                delattr(app.state, name)


@pytest.mark.anyio
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
async def test_kale_uvicorn_stopt_ook_met_open_stromen(tmp_path, monkeypatch, sig):
    """Ook zonder serve.run: uvicorn rondt de lifespan pas af als alle antwoorden klaar zijn,
    dus moet een stroom al eindigen zodra het stoppen begint. Anders bleef de oude worker van
    `uvicorn --reload` hangen tot het token verliep."""
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("Signalen kan alleen de hoofdthread opvangen")

    # Uvicorn stuurt het signaal na het stoppen opnieuw naar de handler van vóór hem: die mag
    # pytest niet stoppen
    def harmless(*_) -> None:
        pass

    previous = signal.signal(sig, harmless)
    try:
        async with plain_uvicorn(tmp_path, monkeypatch) as (server, task, c):
            app.state.security = SecurityPolicy(SecurityConfig(require_2fa=False))
            r = await c.post("/api/v1/auth/setup", json=ADMIN)
            assert r.status_code == 201, r.text
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            ready, ended = asyncio.Event(), asyncio.Event()

            async def watch() -> None:
                async with (
                    httpx.AsyncClient(base_url=c.base_url, trust_env=False, timeout=30) as s,
                    s.stream("GET", "/api/v1/events", headers=headers) as response,
                ):
                    assert response.status_code == 200
                    async for chunk in response.aiter_text():
                        if "retry:" in chunk:
                            ready.set()
                ended.set()

            watcher = asyncio.create_task(watch())
            try:
                await asyncio.wait_for(ready.wait(), 5)
                start = time.monotonic()
                signal.raise_signal(sig)
                done, _ = await asyncio.wait({task}, timeout=5)
                assert done, "uvicorn wachtte op de open stroom"
                assert time.monotonic() - start < 5
                await asyncio.wait_for(ended.wait(), 5)
                await watcher
            finally:
                watcher.cancel()
        assert signal.getsignal(sig) is harmless  # niets van de lifespan achtergebleven
    finally:
        signal.signal(sig, previous)
