"""Direct play zonder worker: M1 en M3 samen (golf 1 van fase 5, bij het samenvoegen).

"Klaar als" van M1 zegt: met de worker weg (of een leeg ffmpeg) werkt direct play. De gezondheid
van M1 (health.py) zet MediaState.worker op down of ffmpeg_missing; play van M3 kijkt daar alleen
naar als het plan de worker nodig heeft (remux, transcode, audio). Een transcode die om een plek
vraagt (de toelating zegt nu nog altijd "worker", de stub van M4) krijgt dan de fout van de
worker, niet 503 media_busy: de speler biedt bij media_ffmpeg_missing Downloaden aan (e2e 13),
en "druk, probeer later" helpt dan niet.

Eerst met de staat met de hand gezet (de ASGI-client), dan met media echt aan: de lokale worker
van M1 zonder ffmpeg in PATH (ffmpeg_missing), en daarna gedood op pid (down), met de
gezondheid van M1 die het ziet. De probes zijn de opnames van M0, zoals in test_media_sessions.py.
"""

from __future__ import annotations

import asyncio
import os
import signal
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import MediaConfig
from app.db import get_engine
from app.main import app
from app.media import analysis, health, sessions
from app.media.state import MediaPolicy, MediaState, media_state
from app.models import Node
from app.routers import public_media
from tests.live import live
from tests.media.media_contract import failed
from tests.test_media_decide import probe as recorded
from tests.test_media_sessions import content, file, play, profile_json, video

WORKER_ERRORS = {"down": "media_worker_down", "ffmpeg_missing": "media_ffmpeg_missing"}


@pytest.fixture
def media(monkeypatch) -> MediaState:
    sessions.reset()
    public_media.plays.reset()
    state = MediaState(enabled=True, worker="ok")
    monkeypatch.setattr(app.state, "media", state, raising=False)
    monkeypatch.setattr(app.state, "media_policy", MediaPolicy(MediaConfig()), raising=False)
    yield state
    sessions.reset()
    public_media.plays.reset()


@pytest.mark.anyio
@pytest.mark.parametrize("worker", ["down", "ffmpeg_missing"])
async def test_direct_speelt_en_de_rest_noemt_de_worker(client, admin, settings, media, worker):
    headers, _ = admin
    direct = await video(client, headers, settings, "film.mp4", "h264_aac.mp4")
    remux = await video(client, headers, settings, "film.mkv", "vp9_opus.mkv", index=True)
    transcode = await video(client, headers, settings, "oud.avi", "xvid_mp3.avi", index=True)
    media.worker = worker
    out = await play(client, headers, direct["id"])
    assert out["mode"] == "direct"
    r = await file(client, out["sid"], Range="bytes=0-1023")
    assert r.status_code == 206 and r.content == direct["data"][:1024]
    code = WORKER_ERRORS[worker]
    for node, fixture in ((remux, "vp9_opus.mkv"), (transcode, "xvid_mp3.avi")):
        body = {"profile": profile_json(fixture=fixture)}
        r = await client.post(f"/api/v1/media/{node['id']}/play", json=body, headers=headers)
        failed(r, 503, code)


@pytest.mark.anyio
async def test_met_de_worker_gezond_blijft_een_transcode_zonder_plek_busy(
    client, admin, settings, media
):
    """De stub van de toelating (M4) heeft geen plek: met een gezonde worker blijft dat 503
    media_busy met reden worker, zoals M3 het bouwde."""
    headers, _ = admin
    node = await video(client, headers, settings, "oud.avi", "xvid_mp3.avi", index=True)
    out = await play(client, headers, node["id"], fixture="xvid_mp3.avi", expect=503)
    assert out["detail"]["code"] == "media_busy"
    assert out["detail"]["params"]["reason"] == "worker"


# --- met media echt aan: de lokale worker en de gezondheid van M1 ---------------------------


async def _until(check, seconds: float = 10.0) -> bool:
    for _ in range(int(seconds / 0.05)):
        if check():
            return True
        await asyncio.sleep(0.05)
    return check()


async def _upload_with_probe(server, headers, name: str, fixture: str) -> dict:
    node = await server.upload(headers, name, content(name, 300_000))
    maker = async_sessionmaker(get_engine(server.settings), expire_on_commit=False)
    async with maker() as session:
        row = await session.get(Node, uuid.UUID(node["id"]))
        assert row is not None and row.blob_id
        sha = row.blob_id
    probe = recorded(fixture)
    if fixture.endswith(".mkv"):
        probe.keyframes_state, probe.max_gop_ms = "ready", 2000
    await analysis.save_probe(maker, sha, probe)
    return node


@pytest.mark.media
@pytest.mark.anyio
async def test_direct_speelt_met_de_echte_worker_zonder_ffmpeg_en_gedood(tmp_path, monkeypatch):
    empty = tmp_path / "leeg-pad"
    empty.mkdir()
    # De lokale worker krijgt PATH van de app (local.worker_env): zonder ffmpeg en ffprobe
    monkeypatch.setenv("PATH", str(empty))
    data = tmp_path / "data"
    data.mkdir()
    async with live(
        data, monkeypatch, PLATFORM_MEDIA_ENABLED="true", PLATFORM_MEDIA_MAX_PROCS="3"
    ) as server:
        try:
            state = media_state(app)
            worker = app.state.media_local
            assert worker is not None
            # de eerste ronde van de gezondheid (na 0,2 s) ziet dat ffmpeg ontbreekt
            assert await _until(lambda: state.worker == "ffmpeg_missing"), state.worker
            headers = await server.admin()
            c = server.client
            direct = await _upload_with_probe(server, headers, "film.mp4", "h264_aac.mp4")
            remux = await _upload_with_probe(server, headers, "film.mkv", "vp9_opus.mkv")

            async def play_now(node: dict, fixture: str):
                body = {"profile": profile_json(fixture=fixture)}
                return await c.post(f"/api/v1/media/{node['id']}/play", json=body, headers=headers)

            async def plays_direct() -> None:
                r = await play_now(direct, "h264_aac.mp4")
                assert r.status_code == 200, r.text
                assert r.json()["mode"] == "direct"
                sid = r.json()["sid"]
                got = await c.get(f"/api/v1/stream/{sid}/file", headers={"Range": "bytes=0-99"})
                assert got.status_code == 206 and len(got.content) == 100
                assert (await c.delete(f"/api/v1/stream/{sid}")).status_code == 204

            await plays_direct()
            failed(await play_now(remux, "vp9_opus.mkv"), 503, "media_ffmpeg_missing")

            # De worker gedood op pid: tot de herstart (backoff 1 s) is hij down
            pid = worker.pid
            os.kill(pid, signal.SIGKILL)
            assert await _until(lambda: worker.process.returncode is not None or worker.pid != pid)
            assert await health.check(app) == "down"
            await plays_direct()
            failed(await play_now(remux, "vp9_opus.mkv"), 503, "media_worker_down")

            # Na de herstart (local.py van M1) ziet de gezondheid hem weer, nog steeds zonder ffmpeg
            assert await _until(lambda: worker.restarts >= 1 and worker.socket.exists(), 15)
            assert worker.pid != pid
            assert await health.check(app) == "ffmpeg_missing"
            await plays_direct()
        finally:
            sessions.reset()
