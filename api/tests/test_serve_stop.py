"""F3 (fase 5, §3.5): netjes stoppen binnen de grace-periode.

Voor de reparatie stopte serve.py eerst https en pas daarna http, zonder grens: een gepauzeerde
stream hield het stoppen voor altijd op, en Docker stopte de app na 10 s met SIGKILL voordat
de lifespan sloot (voortgang weg, taken niet terug in de rij). Nu:
- begint het stoppen met media: geen nieuwe verzoeken onder /api/v1/stream/, wie op een segment
  wacht krijgt 503 media_busy met Retry-After: 2, en de sessies gaan dicht;
- stoppen https en http tegelijk, met één deadline van serve.GRACEFUL (10 s,
  timeout_graceful_shutdown op beide); de lifespan sluit pas als https ook klaar is.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
import pytest
from fastapi import HTTPException

from app import serve
from app.config import Settings
from app.main import app
from app.media import runtime as media_runtime
from app.media import sessions as media_sessions
from app.media.state import MediaState
from tests.live import live

SIZE = 48 * 1024 * 1024  # groter dan de buffers van de sockets: de respons blijft staan
MARGIN = 3.0  # boven GRACEFUL: de lifespan zelf (de rijen, de engine, de certificaten)


class Keep(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[tuple[float, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append((time.monotonic(), record.getMessage()))


async def paused(client, url, headers, opened: list, release: asyncio.Event, outcome: dict, name):
    """Eén stuk lezen en dan stil blijven staan (de iterator blijft bewaard), tot release; dan de
    rest lezen. Een client die niet leest, merkt niet dat de server de verbinding sloot: dat
    blijkt pas bij het verder lezen ("afgebroken" in plaats van "compleet")."""
    try:
        async with client.stream("GET", url, headers=headers) as response:
            assert response.status_code == 200
            chunks = response.aiter_raw(65536)
            got = len(await anext(chunks))
            opened.append(name)
            await release.wait()
            async for chunk in chunks:
                got += len(chunk)
            outcome[name] = "compleet" if got == SIZE else f"kort ({got})"
    except httpx.HTTPError as error:
        outcome[name] = f"afgebroken ({type(error).__name__})"


@pytest.mark.anyio
async def test_stoppen_met_gepauzeerde_streams_binnen_de_deadline(tmp_path, monkeypatch):
    keep = Keep()
    # uvicorn zet bij het starten zijn eigen handlers (dictConfig): de onze komt erna
    loggers = [logging.getLogger("platform"), logging.getLogger("uvicorn.error")]
    level = loggers[0].level
    loggers[0].setLevel(logging.INFO)
    media_calls: list[float] = []
    original = media_runtime.begin_stop

    def begin_stop(app_):
        media_calls.append(time.monotonic())
        return original(app_)

    monkeypatch.setattr(media_runtime, "begin_stop", begin_stop)
    tasks: list[asyncio.Task] = []
    release = asyncio.Event()
    outcome: dict[str, str] = {}
    try:
        async with live(tmp_path, monkeypatch, https=True) as server:
            for logger in loggers:
                logger.addHandler(keep)
            headers = await server.admin()
            node = await server.upload(headers, "groot.bin", bytes(SIZE))
            url = f"/api/v1/files/{node['id']}/content"
            opened: list[str] = []
            for name, https in (("http", False), ("https", True)):
                client = server.new_client(https=https)
                task = paused(client, url, headers, opened, release, outcome, name)
                tasks.append(asyncio.create_task(task))
            deadline = time.monotonic() + 10
            while len(opened) < 2:
                assert time.monotonic() < deadline, f"geen bytes: {opened}"
                assert not any(t.done() for t in tasks), outcome
                await asyncio.sleep(0.01)

            start = time.monotonic()
            server.stop.set()
            await asyncio.wait_for(asyncio.shield(server.task), serve.GRACEFUL + 15)
            took = time.monotonic() - start
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 10)
    finally:
        for logger in loggers:
            logger.removeHandler(keep)
        loggers[0].setLevel(level)
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    stopped = [at for at, line in keep.lines if line.startswith("Platform ") and "gestopt" in line]
    cancels = [at for at, line in keep.lines if "timeout graceful shutdown exceeded" in line]
    assert stopped, keep.lines
    # Binnen één deadline voor https en http samen (na elkaar was het 2 × GRACEFUL, of nooit)
    assert took <= serve.GRACEFUL + MARGIN, took
    assert stopped[0] - start <= serve.GRACEFUL + MARGIN
    # Elke luisteraar brak zijn stream af na zijn deadline, en wel tegelijk; de lifespan
    # (de laatste regel) sloot pas daarna
    assert len(cancels) == 2, keep.lines
    assert max(cancels) - min(cancels) <= 1.0, cancels
    assert serve.GRACEFUL - 0.5 <= min(cancels) - start
    assert max(cancels) <= stopped[0]
    assert set(outcome) == {"http", "https"}
    assert all(v.startswith("afgebroken") for v in outcome.values()), outcome
    # Media ging als eerste dicht
    assert media_calls and media_calls[0] <= start + 0.5
    assert media_calls[0] <= min(cancels)


def test_de_deadline_staat_op_elke_luisteraar(tmp_path):
    settings = Settings(data_dir=tmp_path, office_url="http://ds")
    assert serve.GRACEFUL == 10
    assert serve._config(settings, 0, lifespan="on").timeout_graceful_shutdown == 10
    assert serve._config(settings, 0, lifespan="off").timeout_graceful_shutdown == 10
    assert serve._internal_config(settings).timeout_graceful_shutdown == 10


@pytest.fixture
def media_on(monkeypatch) -> MediaState:
    state = MediaState(enabled=True)
    monkeypatch.setattr(app.state, "media", state, raising=False)
    return state


@pytest.mark.anyio
async def test_wachten_op_een_segment_breekt_af_bij_het_stoppen(media_on, monkeypatch):
    monkeypatch.setattr(media_sessions, "close_all", lambda: 2)
    never = asyncio.get_running_loop().create_future()
    waiting = asyncio.create_task(media_runtime.until_stopped(app, never))
    await asyncio.sleep(0.01)
    assert not waiting.done()
    assert media_runtime.begin_stop(app) == 2
    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(waiting, 1)
    error = caught.value
    assert error.status_code == 503
    assert error.headers == {"Retry-After": "2"}
    assert error.detail["code"] == "media_busy"
    assert error.detail["params"]["reason"] == "worker"
    assert never.cancelled()
    assert not media_on.stop_waiters

    # Wie daarna nog gaat wachten, krijgt meteen hetzelfde (en de coroutine loopt niet)
    async def segment():
        raise AssertionError("mag niet lopen")

    with pytest.raises(HTTPException) as again:
        await media_runtime.until_stopped(app, segment())
    assert again.value.status_code == 503


@pytest.mark.anyio
async def test_zonder_stoppen_gewoon_het_antwoord(media_on):
    async def segment():
        await asyncio.sleep(0.01)
        return b"moof"

    assert await media_runtime.until_stopped(app, segment()) == b"moof"
    assert not media_on.stop_waiters


@pytest.mark.anyio
async def test_begin_stop_een_keer_en_niet_zonder_media(monkeypatch):
    calls = []
    monkeypatch.setattr(media_sessions, "close_all", lambda: calls.append(1) or 1)
    off = MediaState(enabled=False)
    monkeypatch.setattr(app.state, "media", off, raising=False)
    assert media_runtime.begin_stop(app) == 0
    assert not off.stopping and calls == []

    on = MediaState(enabled=True)
    monkeypatch.setattr(app.state, "media", on, raising=False)
    assert media_runtime.begin_stop(app) == 1
    assert media_runtime.begin_stop(app) == 0
    assert on.stopping and calls == [1]

    # Een volgende start in hetzelfde proces begint weer zonder stoppen
    await media_runtime.start(app, Settings(media_enabled=False))
    assert not on.stopping


@pytest.mark.anyio
async def test_een_fout_bij_het_sluiten_stopt_het_stoppen_niet(media_on, monkeypatch):
    def broken():
        raise RuntimeError("stuk")

    monkeypatch.setattr(media_sessions, "close_all", broken)
    assert media_runtime.begin_stop(app) == 0
    assert media_on.stopping


@pytest.mark.anyio
async def test_nieuwe_verzoeken_onder_stream_bij_het_stoppen(client, media_on):
    media_on.stopping = True
    for method, path in (
        ("GET", "/api/v1/stream/s1.geheim/map.json"),
        ("GET", "/api/v1/stream/s1.geheim/3.m4s"),
        ("DELETE", "/api/v1/stream/s1.geheim"),
    ):
        r = await client.request(method, path)
        assert r.status_code == 503, (path, r.text)
        assert r.headers["retry-after"] == "2"
        assert r.json()["detail"]["code"] == "media_busy"
        assert r.json()["detail"]["params"]["reason"] == "worker"
        assert r.headers["cross-origin-resource-policy"] == "same-origin"
        assert r.headers["cache-control"] == "no-store"
    # De rest van de app merkt er niets van
    assert (await client.get("/health")).status_code == 200
