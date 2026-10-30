"""De previews van video's via de mediaworker (bouwontwerp fase 5, M1).

- media aan: het renderproces krijgt socket en sleutel van de worker van de app (alleen via
  stdin), vraagt daar meta en frame, en maakt de thumbnails zoals bij een foto. Zonder ffmpeg in
  het eigen PATH lukt het dus toch: het beeld komt van de worker;
- de worker weg: de preview zet zichzelf opnieuw klaar (jobs.Defer(300)), zonder een poging te
  tellen; druk: na 30 s;
- media uit (zoals 0.6.0): het renderproces start een eigen worker, en die is daarna weg;
- de metadata van video's in één renderproces (photometa): met de worker weg een fout per
  video, de foto's gaan door.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app import jobs, previews, render, render_meta
from app.media import local
from app.media.worker import guard
from app.media.worker.server import Config, Worker
from app.models import Blob, Job
from tests.test_previews import make_video, picture, preview_of, upload

pytestmark = pytest.mark.anyio

needs_ffmpeg = pytest.mark.skipif(
    not render.installed(), reason="ffmpeg en ffprobe niet geïnstalleerd"
)
# Een mp4-kop: genoeg voor de soort (video/mp4), niet voor ffmpeg
FAKE_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 1000
CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}


def media_on(settings):
    settings.media_enabled = True
    return settings


def aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)  # SQLite bewaart zonder


async def only_job(maker) -> Job:
    async with maker() as session:
        return (await session.scalars(select(Job).where(Job.type == previews.TYPE))).one()


@pytest.fixture
def maker(settings):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import get_engine

    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def no_ffmpeg_path(tmp_path: Path, monkeypatch) -> None:
    """PATH zonder ffmpeg voor dit proces en zijn kinderen (het renderproces)."""
    empty = tmp_path / "leeg-pad"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


@pytest.mark.media
async def test_preview_van_een_video_via_de_worker_van_de_app(
    client, admin, settings, tmp_path, monkeypatch, maker
):
    headers, _ = admin
    media_on(settings)
    worker = await local.start(settings)  # op settings.media_socket, met settings.media_key()
    try:
        film = make_video(tmp_path / "film.mp4", "1280x720", seconds=3).read_bytes()
        node = await upload(client, headers, "film.mp4", film)
        no_ffmpeg_path(tmp_path, monkeypatch)  # het renderproces kan het niet zelf
        assert not render.installed()
        assert await jobs.run_pending(settings) == 1
        info = (await preview_of(client, headers, node["id"]))[node["id"]]
        assert info["state"] == "ready" and info["thumb"] and info["large"]
        assert info["meta"] == {"duration": 3.0, "width": 1280, "height": 720,
                                "sizes": [256, 1024]}  # fmt: skip
        r = await client.get(f"/api/v1/files/{node['id']}/preview", headers=headers)
        detail = r.json()["meta"]
        assert detail["video"]["codec"] in ("h264", "mpeg4")
        assert detail["container"].startswith("mov")
        assert not guard.children(worker.pid)  # de worker heeft niets meer lopen
    finally:
        await local.stop(worker)


async def test_de_worker_weg_dan_later_opnieuw_zonder_poging(client, admin, settings, maker):
    headers, _ = admin
    media_on(settings)
    assert not settings.media_socket.exists()
    await upload(client, headers, "film.mp4", FAKE_MP4)
    before = jobs.now()
    assert await jobs.run_pending(settings) == 1
    job = await only_job(maker)
    assert job.status == "queued" and job.attempts == 0 and job.error is None
    assert aware(job.run_after) >= before + timedelta(seconds=290)
    async with maker() as session:
        blob = (await session.scalars(select(Blob))).one()
        assert blob.preview == "pending"
    # nog eens, nu de tijd om is: weer terug, nog steeds geen poging
    async with maker() as session:
        row = await session.get(Job, job.id)
        row.run_after = jobs.now()
        await session.commit()
    assert await jobs.run_pending(settings) == 1
    job = await only_job(maker)
    assert job.status == "queued" and job.attempts == 0


async def test_de_worker_druk_dan_na_30_s(client, admin, settings, maker):
    headers, _ = admin
    media_on(settings)
    # Een worker zonder plaats: caps werkt, elke andere vraag krijgt busy
    config = Config(socket=settings.media_socket, max_procs=0, key=settings.media_key())
    worker = Worker(config, dict(CAPS))
    await worker.start()
    try:
        await upload(client, headers, "film.mp4", FAKE_MP4)
        before = jobs.now()
        assert await jobs.run_pending(settings) == 1
        job = await only_job(maker)
        assert job.status == "queued" and job.attempts == 0
        assert before + timedelta(seconds=25) <= aware(job.run_after)
        assert aware(job.run_after) <= before + timedelta(seconds=60)
    finally:
        await worker.stop()


async def test_een_probleem_met_de_sleutel_is_ook_weg(client, admin, settings, maker):
    headers, _ = admin
    media_on(settings)
    settings.media_worker = str(settings.media_dir / "elders.sock")  # container, geen sleutel
    assert settings.media_problem()
    await upload(client, headers, "film.mp4", FAKE_MP4)
    assert await jobs.run_pending(settings) == 1
    job = await only_job(maker)
    assert job.status == "queued" and job.attempts == 0


async def test_de_sleutel_alleen_via_stdin_en_alleen_voor_video(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_JWT_SECRET", "zeer-geheim")
    media_on(settings)
    seen = tmp_path / "gezien.json"
    child = tmp_path / "kind.py"
    child.write_text(
        "import json, os, sys\n"
        "request = json.loads(sys.stdin.read())\n"
        f"with open({str(seen)!r}, 'w') as f:\n"
        "    json.dump({'env': dict(os.environ), 'request': request}, f)\n"
        "print(json.dumps({'state': 'none', 'meta': {}, 'sizes': []}))\n"
    )
    monkeypatch.setattr(previews, "CHILD", f"exec(open({str(child)!r}).read())")
    key = settings.media_key()
    await previews.render(settings, {"mime": "video/mp4"}, tmp_path)
    got = json.loads(seen.read_text())
    assert got["request"]["worker"] == {"socket": str(settings.media_socket), "key": key}
    assert key not in json.dumps(got["env"]) and not any(
        k.startswith("PLATFORM_") for k in got["env"]
    )
    for request in (
        {"mime": "image/jpeg"},
        {"mime": "application/pdf"},
        {"mode": "page"},
        {"pdf": "x.pdf", "mime": "video/mp4"},
        {"mode": "meta", "items": [{"mime": "image/jpeg"}]},
    ):
        await previews.render(settings, request, tmp_path)
        assert "worker" not in json.loads(seen.read_text())["request"], request
    for request in (
        {"mime": ""},
        {"mime": "application/octet-stream"},
        {"mode": "meta", "items": [{"mime": "image/jpeg"}, {"mime": "video/quicktime"}]},
    ):
        await previews.render(settings, request, tmp_path)
        assert "worker" in json.loads(seen.read_text())["request"], request
    settings.media_enabled = False
    await previews.render(settings, {"mime": "video/mp4"}, tmp_path)
    assert "worker" not in json.loads(seen.read_text())["request"]


async def test_defer_uit_het_renderproces_wordt_begrensd(settings, tmp_path, monkeypatch):
    for seconds, wanted in ((300, 300), (10**9, previews.DEFER_MAX), (-5, 1.0), ("x", 300)):
        answer = json.dumps({"state": "defer", "seconds": seconds})
        monkeypatch.setattr(previews, "CHILD", f"print({answer!r})")
        with pytest.raises(jobs.Defer) as exc:
            await previews.render(settings, {"mime": "video/mp4"}, tmp_path)
        assert exc.value.seconds == wanted


def _children_of_me() -> set[int]:
    return {pid for pid, _comm in guard.children(os.getpid())}


@needs_ffmpeg
def test_media_uit_een_eigen_worker_die_daarna_weg_is(tmp_path):
    film = make_video(tmp_path / "film.mp4", "640x360")
    out, work = tmp_path / "out", tmp_path / "work"
    out.mkdir()
    work.mkdir()
    before = _children_of_me()
    request = {
        "source": str(film), "out": str(out), "work": str(work), "mime": "video/mp4",
        "name": "film.mp4", "max_pixels": 10**8,
    }  # fmt: skip
    result = render.render(request)
    assert result["state"] == "ready" and 256 in result["sizes"]
    assert result["meta"]["width"] == 640 and result["meta"]["duration"] == 2.0
    assert _children_of_me() == before  # de eigen worker is gestopt en gewacht
    assert not [p for p in work.iterdir() if p.name.startswith(".mediaworker-")]
    assert (out / "256.webp").is_file()


@needs_ffmpeg
def test_media_uit_een_kapotte_video_geeft_geen_preview(tmp_path):
    out, work = tmp_path / "out", tmp_path / "work"
    out.mkdir()
    work.mkdir()
    source = tmp_path / "kapot.mp4"
    source.write_bytes(FAKE_MP4)
    request = {"source": str(source), "out": str(out), "work": str(work), "mime": "video/mp4"}
    assert render.render({**request, "max_pixels": 10**8})["state"] == "none"


def test_media_uit_zonder_ffmpeg_start_er_geen_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda _name: None)
    monkeypatch.setattr(local, "oneshot", lambda *_a: pytest.fail("geen worker zonder ffmpeg"))
    head = FAKE_MP4
    assert render.video(tmp_path / "x", head, tmp_path, tmp_path, 10**8) == render.NONE


def test_photometa_met_de_worker_weg_een_fout_per_video(tmp_path):
    foto = tmp_path / "a.jpg"
    foto.write_bytes(picture("JPEG"))
    film = tmp_path / "b.mp4"
    film.write_bytes(FAKE_MP4)
    gone = {"socket": str(tmp_path / "weg.sock"), "key": "k" * 64}
    request = {
        "mode": "meta",
        "worker": gone,
        "items": [
            {"sha": "a", "source": str(foto), "mime": "image/jpeg"},
            {"sha": "b", "source": str(film), "mime": "video/mp4"},
            {"sha": "c", "source": str(film), "mime": "video/mp4"},
        ],
    }
    results = render_meta.run_meta(request)["results"]
    assert "error" not in results["a"]
    assert results["b"]["error"].startswith("mediaworker weg")
    assert results["c"]["error"].startswith("mediaworker weg")


def test_photometa_media_uit_zonder_ffmpeg_geen_fout(tmp_path, monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda _name: None)
    film = tmp_path / "b.mp4"
    film.write_bytes(FAKE_MP4)
    request = {"mode": "meta", "items": [{"sha": "b", "source": str(film), "mime": "video/mp4"}]}
    assert render_meta.run_meta(request)["results"] == {"b": {}}  # zoals 0.6.0 zonder ffprobe


@needs_ffmpeg
def test_photometa_media_uit_start_de_worker_een_keer(tmp_path, monkeypatch):
    started = []
    real = local.oneshot

    def counting(directory=None):
        started.append(directory)
        return real(directory)

    monkeypatch.setattr(local, "oneshot", counting)
    monkeypatch.chdir(tmp_path)
    films = [make_video(tmp_path / f"{i}.mp4", "64x48", seconds=1) for i in range(3)]
    items = [{"sha": str(i), "source": str(f), "mime": "video/mp4"} for i, f in enumerate(films)]
    results = render_meta.run_meta({"mode": "meta", "items": items})["results"]
    assert set(results) == {"0", "1", "2"} and all("error" not in r for r in results.values())
    assert len(started) == 1


def test_het_eigen_ffmpeg_deel_van_render_is_weg():
    """M1: geen _run en geen subprocess meer in render.py (shutil blijft, voor shutil.which)."""
    assert not hasattr(render, "_run") and not hasattr(render, "subprocess")
    assert render.shutil is shutil
