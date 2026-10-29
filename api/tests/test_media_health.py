"""De gezondheid van de mediaworker (bouwontwerp fase 5, Keuze 12, 13, 46 en 47; M1).

health.check vraagt caps over een eigen verbinding die niet meetelt in de plaatsen, en zet
MediaState.worker: ok, down of ffmpeg_missing (een worker met een lege PATH). busy is ok; een
andere PROTOCOL is down met de reden "versie". Met 6 verbindingen open (en een volle soort)
blijft het ok. De lus kijkt vaker als de worker down is.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import types
from pathlib import Path

import pytest

from app.config import Settings
from app.media import client, health, local, wire
from app.media.state import media_state
from app.media.types import WorkerCaps
from app.media.wire import Op
from app.media.worker import args as A
from app.media.worker import server
from app.media.worker.server import Config, Worker

pytestmark = pytest.mark.anyio

KEY = "de-sleutel-van-de-gezondheid-" * 2
CAPS = {"protocol": 1, "ffmpeg": "6.1", "ffprobe": "6.1", "mode": "local", "sandbox": "off"}


def new_app():
    return types.SimpleNamespace(state=types.SimpleNamespace())


def media_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32, media_max_procs=3
    )


@pytest.fixture
async def worker(tmp_path):
    config = Config(socket=tmp_path / "run" / "w.sock", max_procs=6, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        yield w
    finally:
        await w.stop()
        client.bind(None)


async def test_ok_en_de_caps_in_de_cache(worker):
    app = new_app()
    assert await health.check(app) == "ok"
    state = media_state(app)
    assert state.caps is not None and state.caps.ffmpeg == "6.1"
    assert state.reason == ""


async def test_down_zonder_worker(tmp_path, caplog):
    client.bind(tmp_path / "weg.sock", KEY)
    try:
        app = new_app()
        with caplog.at_level(logging.WARNING, logger="platform.media"):
            assert await health.check(app) == "down"
            assert await health.check(app) == "down"
        assert "geen verbinding" in media_state(app).reason
        # één regel in het log per overgang, niet per keer
        assert caplog.text.count("mediaworker niet bereikbaar") == 1
    finally:
        client.bind(None)


async def test_ffmpeg_missing_als_caps_het_zegt(worker):
    worker.caps.update(ffmpeg=None, ffprobe=None)
    app = new_app()
    assert await health.check(app) == "ffmpeg_missing"
    assert media_state(app).reason == "ffmpeg en ffprobe"
    worker.caps.update(ffmpeg="6.1", ffprobe="6.1", missing=["zscale"])
    assert await health.check(app) == "ffmpeg_missing"
    assert media_state(app).reason == "zscale"
    worker.caps.update(missing=[])
    assert await health.check(app) == "ok"


async def test_busy_is_ok(monkeypatch):
    async def busy(**_kw):
        raise client.WorkerBusy("short")

    monkeypatch.setattr(client, "caps", busy)
    app = new_app()
    assert await health.check(app) == "ok"
    assert media_state(app).reason == "druk"


async def test_een_andere_protocol_is_down_met_versie(monkeypatch):
    async def other(**_kw):
        raise client.WorkerDown("andere PROTOCOL: 2")

    monkeypatch.setattr(client, "caps", other)
    app = new_app()
    assert await health.check(app) == "down"
    assert media_state(app).reason.startswith("versie:")
    assert f"de app spreekt {wire.PROTOCOL}" in media_state(app).reason


async def test_geen_antwoord_is_down(monkeypatch):
    async def slow(**_kw):
        raise TimeoutError

    monkeypatch.setattr(client, "caps", slow)
    app = new_app()
    assert await health.check(app) == "down"
    assert media_state(app).reason == "geen antwoord"


async def test_met_6_verbindingen_open_en_een_volle_soort_blijft_het_ok(
    worker, monkeypatch, tmp_path
):
    real = server.handler

    async def run(ctx: server.Context) -> dict:
        child = ctx.spawn(["sleep", "60"])
        await child.wait()
        return {}

    monkeypatch.setattr(server, "handler", lambda op: real(op) if op == Op.CAPS else run)
    source = tmp_path / "bron.mkv"
    source.write_bytes(b"\x1aE\xdf\xa3" + b"\0" * 1020)
    open_socks: list[socket.socket] = []
    jobs = []
    try:
        for _ in range(4):  # verbindingen die niets vragen
            open_socks.append(await client.connect())
        for _ in range(2):  # en twee die de soort kort vullen
            jobs.append((await client.call(Op.PROBE, A.ProbeArgs(), source=source))[0])
        with pytest.raises(client.WorkerBusy):
            await client.call(Op.PROBE, A.ProbeArgs(), source=source)
        app = new_app()
        assert await health.check(app) == "ok"
        assert media_state(app).caps.in_use["short"] == 2
    finally:
        for sock in open_socks:
            sock.close()
        for job in jobs:
            await job.cancel()


async def test_de_lus_kijkt_vaker_als_de_worker_down_is(monkeypatch):
    answers = iter(["down", "down", "down", "ok", "down", "ok"])
    pauses: list[float] = []

    async def check(_app) -> str:
        return next(answers)

    async def pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) > 6:
            raise asyncio.CancelledError

    monkeypatch.setattr(health, "check", check)
    monkeypatch.setattr(health, "_pause", pause)
    with pytest.raises(asyncio.CancelledError):
        await health._loop(new_app(), None)
    assert pauses == [health.FIRST, 1.0, 2.0, 4.0, health.INTERVAL, 1.0, health.INTERVAL]


async def test_een_fout_in_de_lus_stopt_hem_niet(monkeypatch):
    pauses: list[float] = []

    async def check(_app) -> str:
        raise ValueError("stuk")

    async def pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(health, "check", check)
    monkeypatch.setattr(health, "_pause", pause)
    app = new_app()
    media_state(app).set_worker("ok")
    with pytest.raises(asyncio.CancelledError):
        await health._loop(app, None)
    assert pauses == [health.FIRST, 1.0, 2.0]  # als down
    # review golf 1, C3: ook de staat zegt down (play en /beheer lezen die)
    assert media_state(app).worker == "down" and media_state(app).reason == "stuk"


async def test_caps_die_niet_valideert_is_down(monkeypatch):
    """Review golf 1, C3: caps van een worker die iets onbekends zegt (zoals sandbox
    "partial" van een nieuwere versie) valideert niet. Dat is geen fout die check kent; de lus
    vangt hem en de staat gaat naar down, niet blijven staan op ok met de oude caps."""
    pauses: list[float] = []

    async def caps():
        return WorkerCaps.model_validate({"protocol": wire.PROTOCOL, "sandbox": "partial"})

    async def pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(client, "caps", caps)
    monkeypatch.setattr(health, "_pause", pause)
    app = new_app()
    state = media_state(app)
    state.set_worker("ok")
    with pytest.raises(asyncio.CancelledError):
        await health._loop(app, None)
    assert pauses == [health.FIRST, 1.0, 2.0]
    assert state.worker == "down" and "sandbox" in state.reason


@pytest.mark.media
async def test_een_echte_worker_met_een_lege_path_is_ffmpeg_missing(tmp_path, monkeypatch):
    empty = tmp_path / "leeg-pad"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    w = await local.start(media_settings(tmp_path), tmp_path / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        app = new_app()
        assert await health.check(app) == "ffmpeg_missing"
        assert media_state(app).caps.ffmpeg is None
    finally:
        await local.stop(w)
        client.bind(None)


@pytest.mark.media
async def test_een_echte_worker_is_ok_ook_met_6_verbindingen(tmp_path):
    w = await local.start(media_settings(tmp_path), tmp_path / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    socks: list[socket.socket] = []
    try:
        for _ in range(6):
            socks.append(await client.connect())
        app = new_app()
        assert await health.check(app) == "ok"
        assert media_state(app).caps.mode == "local"
        assert media_state(app).caps.ffmpeg
    finally:
        for sock in socks:
            sock.close()
        await local.stop(w)
        client.bind(None)
