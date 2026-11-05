"""audio.mp4 (Keuze 38, M9): routers/stream_audio.py met de nep-worker.

- play kiest mode audio voor FLAC en Opus in Chromium 141; audio.mp4 van een sessie met een
  andere mode is 404;
- de eerste vraag volgt het groeiende bestand (één remux, ook voor twee vragen tegelijk), de
  volgende krijgt het bestand uit de cache met Range, ETag, 304 en 416;
- FLAC en AAC gaan als copy, Opus als flac; het spoor van play;
- mislukt (de worker faalt, druk, vast): niets in de cache, de volgende vraag begint opnieuw;
- een blob die weggaat (discard) haalt zijn bestanden weg, ook van een remux die nog loopt;
- trim houdt audio/ onder de grens, de oudste eerst;
- follow breekt af als de app stopt (until_stopped) en guarded_body als de toegang weg is.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.main import app
from app.media import hooks, runtime, sessions
from app.media.wire import Code, Op
from app.media.worker.args import RemuxAudioArgs
from app.routers import stream_audio
from tests.media import fake_worker as FW
from tests.media.fake_worker import OpError
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_sessions import play, session_of, video

STREAM = "/api/v1/stream"
DATA = bytes(range(256)) * 4096  # 1 MiB


@pytest.fixture(autouse=True)
def clean():
    stream_audio.reset()
    hooks.register_discard(stream_audio.discard)
    yield
    stream_audio.reset()


@pytest.fixture
async def world(media_app, client, admin):  # noqa: F811
    settings, fake = media_app
    headers, tokens = admin
    return settings, fake, headers, tokens


class Remux:
    """remux_audio van de nep: schrijft data in stukken naar de pipe. gate: na het eerste stuk
    wachten; error: daarna falen; hang: na het eerste stuk niets meer."""

    def __init__(
        self, data: bytes = DATA, *, gate: asyncio.Event | None = None,
        error: Code | None = None, hang: bool = False, pieces: int = 4,
    ) -> None:  # fmt: skip
        self.data = data
        self.gate = gate
        self.error = error
        self.hang = hang
        self.pieces = pieces
        self.calls: list[RemuxAudioArgs] = []
        self.first = asyncio.Event()

    async def __call__(self, job: FW.Job) -> dict:
        assert isinstance(job.args, RemuxAudioArgs) and len(job.outputs) == 1
        self.calls.append(job.args)
        fd = job.outputs[0]
        os.set_blocking(fd, False)  # _write wacht dan op de lus, zoals de nep zelf bij stream
        size = -(-len(self.data) // self.pieces)
        try:
            for i in range(0, len(self.data), size):
                await FW._write(fd, self.data[i : i + size])
                if i == 0:
                    self.first.set()
                    if self.hang:
                        await asyncio.Event().wait()
                    if self.gate is not None:
                        await self.gate.wait()
                    if self.error is not None:
                        raise OpError(self.error, "kapot")
        finally:
            FW._close_output(job, fd)
        return {}


async def track(client, headers, settings, fixture: str = "album__flac__01.flac", name=None):
    """Een nummer met de probe van de opname en een sessie in mode audio: (node, sid)."""
    node = await video(client, headers, settings, name or fixture, fixture)
    out = await play(client, headers, node["id"], browser="chromium141", fixture=fixture)
    assert out["mode"] == "audio", out
    assert out["urls"] == {"audio": "audio.mp4"} or out["urls"]["audio"] == "audio.mp4"
    return node, out["sid"]


def cache_dir(settings):
    return settings.media_dir / "audio"


async def until(check, what: str) -> None:
    for _ in range(500):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"wacht te lang op {what}")


@pytest.mark.anyio
async def test_eerst_groeiend_dan_uit_de_cache(world, client):
    settings, fake, headers, _ = world
    gate = asyncio.Event()
    handler = Remux(gate=gate)
    fake.on(Op.REMUX_AUDIO, handler)
    node, sid = await track(client, headers, settings)
    url = f"{STREAM}/{sid}/audio.mp4"
    first = asyncio.ensure_future(client.get(url))
    await until(lambda: handler.first.is_set(), "het eerste stuk")
    part = cache_dir(settings) / f"{node['sha']}.mp4.part"
    await until(lambda: part.exists() and part.stat().st_size > 0, "het .part-bestand")
    assert oct(part.stat().st_mode & 0o777) == "0o600"
    # een tweede vraag tijdens de remux deelt hem
    second = asyncio.ensure_future(client.get(url, headers={"Range": "bytes=0-9"}))
    await until(lambda: len(stream_audio._remuxes) == 1, "de remux")
    await asyncio.sleep(0.05)
    gate.set()
    for r in await asyncio.gather(first, second):
        assert r.status_code == 200, r.text  # groeiend: geen Range, geen lengte vooraf
        assert r.content == DATA
        assert r.headers["content-type"] == "audio/mp4"
        assert "etag" not in r.headers
    assert len(handler.calls) == 1
    assert handler.calls[0] == RemuxAudioArgs(demuxer="flac", audio=0, codec="copy")
    final = cache_dir(settings) / f"{node['sha']}.mp4"
    assert final.read_bytes() == DATA and not part.exists()
    assert stream_audio._remuxes == {}
    # uit de cache: als een bestand
    etag = f'"{node["sha"]}.a0"'
    r = await client.get(url)
    assert r.status_code == 200 and r.content == DATA
    assert r.headers["etag"] == etag and r.headers["content-length"] == str(len(DATA))
    assert r.headers["accept-ranges"] == "bytes"
    assert "max-age" in r.headers["cache-control"]
    r = await client.get(url, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == DATA[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(DATA)}"
    r = await client.get(url, headers={"Range": "bytes=-5"})
    assert r.status_code == 206 and r.content == DATA[-5:]
    r = await client.get(url, headers={"If-None-Match": etag})
    assert r.status_code == 304 and r.content == b""
    r = await client.get(url, headers={"Range": f"bytes={len(DATA) + 5}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{len(DATA)}"
    r = await client.get(url, headers={"Range": "bytes=0-0", "If-Range": '"oud"'})
    assert r.status_code == 200 and len(r.content) == len(DATA)
    assert len(handler.calls) == 1


@pytest.mark.anyio
async def test_opus_wordt_flac_en_aac_blijft_copy(world, client):
    settings, fake, headers, _ = world
    handler = Remux(b"x" * 1000)
    fake.on(Op.REMUX_AUDIO, handler)
    _, sid = await track(client, headers, settings, "album__opus__01.opus")
    r = await client.get(f"{STREAM}/{sid}/audio.mp4")
    assert r.status_code == 200 and r.content == b"x" * 1000
    assert handler.calls[-1] == RemuxAudioArgs(demuxer="ogg", audio=0, codec="flac")


@pytest.mark.anyio
async def test_alleen_voor_mode_audio(world, client):
    settings, fake, headers, _ = world
    fake.on(Op.REMUX_AUDIO, Remux())
    node = await video(client, headers, settings, "film.mp4", "h264_aac.mp4")
    out = await play(client, headers, node["id"], browser="chromium141", fixture="h264_aac.mp4")
    assert out["mode"] != "audio"
    r = await client.get(f"{STREAM}/{out['sid']}/audio.mp4")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    # een mp3 speelt Chromium 141 als bestand (direct): ook geen audio.mp4
    mp3 = await video(client, headers, settings, "a.mp3", "album__mp3__01.mp3")
    out = await play(
        client, headers, mp3["id"], browser="chromium141", fixture="album__mp3__01.mp3"
    )
    assert out["mode"] == "direct"
    r = await client.get(f"{STREAM}/{out['sid']}/audio.mp4")
    assert r.status_code == 404
    assert len(fake.requests) == 0 or all(q.op != Op.REMUX_AUDIO.value for q in fake.requests)
    # een sessie die niet bestaat: 404 van de guard
    r = await client.get(f"{STREAM}/nep.{'a' * 32}/audio.mp4")
    assert r.status_code in (403, 404, 410)


@pytest.mark.anyio
async def test_een_fout_van_de_worker_laat_niets_achter(world, client):
    settings, fake, headers, _ = world
    handler = Remux(error=Code.FAILED)
    fake.on(Op.REMUX_AUDIO, handler)
    node, sid = await track(client, headers, settings)
    url = f"{STREAM}/{sid}/audio.mp4"
    # de stroom was al begonnen: de respons breekt af
    with pytest.raises(sessions.StreamCut) as cut:
        await client.get(url)
    assert cut.value.reason == "failed"
    await until(lambda: not stream_audio._remuxes, "het einde van de remux")
    assert list(cache_dir(settings).iterdir()) == []
    # de volgende vraag begint opnieuw, en nu gaat het goed
    fake.on(Op.REMUX_AUDIO, Remux(b"y" * 10))
    r = await client.get(url)
    assert r.status_code == 200 and r.content == b"y" * 10
    assert (cache_dir(settings) / f"{node['sha']}.mp4").read_bytes() == b"y" * 10


@pytest.mark.anyio
async def test_druk_en_onleesbaar_voor_de_start(world, client, monkeypatch):
    settings, fake, headers, _ = world
    monkeypatch.setattr(stream_audio, "START_BUSY_S", 0.2)
    fake.on(Op.REMUX_AUDIO, Remux())
    _, sid = await track(client, headers, settings)
    url = f"{STREAM}/{sid}/audio.mp4"
    fake.fault("busy", op=Op.REMUX_AUDIO, times=1000)
    r = await client.get(url)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "media_busy"
    await until(lambda: not stream_audio._remuxes, "het einde van de remux")
    assert list(cache_dir(settings).iterdir()) == []
    fake.clear_faults()
    # even druk, dan een plek: gewoon door
    fake.fault("busy", op=Op.REMUX_AUDIO, times=2)
    r = await client.get(url)
    assert r.status_code == 200 and r.content == DATA


@pytest.mark.anyio
async def test_vast_breekt_af(world, client, monkeypatch):
    settings, fake, headers, _ = world
    monkeypatch.setattr(stream_audio, "STALL_S", 0.3)
    fake.on(Op.REMUX_AUDIO, Remux(hang=True))
    _, sid = await track(client, headers, settings)
    with pytest.raises(sessions.StreamCut):
        await client.get(f"{STREAM}/{sid}/audio.mp4")
    await until(lambda: not stream_audio._remuxes, "het einde van de remux")
    assert list(cache_dir(settings).iterdir()) == []
    await until(lambda: fake.procs == 0, "de opdracht in de worker weg")


@pytest.mark.anyio
async def test_discard_haalt_de_bestanden_weg(world, client):
    settings, fake, headers, _ = world
    fake.on(Op.REMUX_AUDIO, Remux(b"z" * 100))
    node, sid = await track(client, headers, settings)
    assert (await client.get(f"{STREAM}/{sid}/audio.mp4")).status_code == 200
    final = cache_dir(settings) / f"{node['sha']}.mp4"
    other = cache_dir(settings) / f"{node['sha']}.1.mp4"
    other.write_bytes(b"spoor 2")
    keep = cache_dir(settings) / f"{'f' * 64}.mp4"
    keep.write_bytes(b"van een ander")
    await hooks.on_discard(node["sha"])
    assert not final.exists() and not other.exists() and keep.exists()


@pytest.mark.anyio
async def test_discard_tijdens_de_remux(world, client):
    settings, fake, headers, _ = world
    gate = asyncio.Event()
    handler = Remux(gate=gate)
    fake.on(Op.REMUX_AUDIO, handler)
    node, sid = await track(client, headers, settings)
    task = asyncio.ensure_future(client.get(f"{STREAM}/{sid}/audio.mp4"))
    await until(lambda: handler.first.is_set(), "het eerste stuk")
    await stream_audio.discard(node["sha"])
    gate.set()
    with pytest.raises(sessions.StreamCut):
        await task
    await until(lambda: not stream_audio._remuxes, "het einde van de remux")
    assert list(cache_dir(settings).iterdir()) == []


def test_trim_de_oudste_eerst(tmp_path):
    now = time.time()
    for i, name in enumerate(("a.mp4", "b.mp4", "c.mp4", "d.mp4")):
        path = tmp_path / name
        path.write_bytes(b"x" * 100)
        os.utime(path, (now - 100 + i, now - 100 + i))
    (tmp_path / "e.mp4.part").write_bytes(b"x" * 1000)  # wordt nog gemaakt: telt niet
    # een .part zonder remux in dit proces (van vóór een crash): weg (review golf 4, S1)
    (tmp_path / "f.mp4.part").write_bytes(b"x" * 1000)
    stream_audio._remuxes["b.mp4"] = object()  # type: ignore[assignment]
    stream_audio._remuxes["e.mp4"] = object()  # type: ignore[assignment]
    try:
        removed = stream_audio.trim(tmp_path, 250, keep={"a.mp4"})
    finally:
        stream_audio._remuxes.pop("b.mp4")
        stream_audio._remuxes.pop("e.mp4")
    # a blijft (keep), b blijft (loopt nog): c en d gaan weg, dan past het (a + b = 200)
    assert removed == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.mp4", "b.mp4", "e.mp4.part"]
    assert stream_audio.trim(tmp_path / "weg", 0, keep=set()) == 0


@pytest.mark.anyio
async def test_de_cache_blijft_onder_een_kwart(world, client, monkeypatch):
    settings, fake, headers, _ = world
    fake.on(Op.REMUX_AUDIO, Remux(b"n" * 1000))
    monkeypatch.setattr(stream_audio.cache, "limit_bytes", lambda gb: 4 * 1500)
    monkeypatch.setattr(stream_audio, "TRACK_SHARE", 1)
    monkeypatch.setattr(stream_audio, "expected_bytes", lambda *a: 1000)
    old = cache_dir(settings) / f"{'0' * 64}.mp4"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"o" * 1000)
    os.utime(old, (time.time() - 1000, time.time() - 1000))
    node, sid = await track(client, headers, settings)
    assert (await client.get(f"{STREAM}/{sid}/audio.mp4")).status_code == 200
    await until(lambda: not stream_audio._remuxes, "het einde van de remux (en trim)")
    # 1000 + 1000 > 1500: de oude gaat (al vóór de start, voor het budget), de nieuwe blijft
    assert not old.exists() and (cache_dir(settings) / f"{node['sha']}.mp4").exists()


@pytest.mark.anyio
async def test_follow_stopt_als_de_app_stopt(world, client, media_app):  # noqa: F811
    settings, fake, headers, _ = world
    gate = asyncio.Event()
    handler = Remux(gate=gate)
    fake.on(Op.REMUX_AUDIO, handler)
    node, sid = await track(client, headers, settings)
    s = session_of(sid)
    remux = stream_audio.Remux(
        cache_dir(settings), "test.mp4", node["sha"], s.path,
        RemuxAudioArgs(demuxer="flac", audio=0, codec="copy"),
    )  # fmt: skip
    await remux.started
    piece = len(DATA) // handler.pieces
    await until(lambda: remux.size == piece, "het eerste stuk in het bestand")
    body = stream_audio.follow(app, remux)
    got = b""
    while len(got) < piece:
        got += await anext(body)
    assert got == DATA[:piece]
    state = runtime.media_state(app)
    waiting = asyncio.ensure_future(anext(body))
    await asyncio.sleep(0.05)
    assert not waiting.done()  # wacht op het volgende stuk
    state.stopping = True
    for waiter in list(state.stop_waiters):
        waiter.set_result(None)
    with pytest.raises(sessions.StreamCut) as cut:
        await waiting
    assert cut.value.reason == "stopping"
    state.stopping = False
    gate.set()
    await remux.task


@pytest.mark.anyio
async def test_uitloggen_breekt_de_stroom_af(world, client):
    settings, fake, headers, tokens = world
    gate = asyncio.Event()
    handler = Remux(gate=gate)
    fake.on(Op.REMUX_AUDIO, handler)
    _, sid = await track(client, headers, settings)
    task = asyncio.ensure_future(client.get(f"{STREAM}/{sid}/audio.mp4"))
    await until(lambda: handler.first.is_set(), "het eerste stuk")
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204
    gate.set()
    with pytest.raises(sessions.StreamCut):
        await task
    # de remux zelf gaat door: het volgende verzoek (met een nieuwe sessie) heeft de cache
    await until(lambda: not stream_audio._remuxes, "het einde van de remux")
    assert len(list(cache_dir(settings).glob("*.mp4"))) == 1


@pytest.mark.anyio
async def test_vier_vragen_tegelijk_een_remux(world, client, monkeypatch):
    """Review golf 4, C1: vier eerste vragen tegelijk (twee tabbladen, een nieuwe poging) delen
    één remux; geen tweede remux op hetzelfde .part."""
    settings, fake, headers, _ = world
    handler = Remux()
    fake.on(Op.REMUX_AUDIO, handler)
    node, sid = await track(client, headers, settings)
    url = f"{STREAM}/{sid}/audio.mp4"
    plain = stream_audio._stat

    def slow(path):
        time.sleep(0.05)  # de stat duurt even: elke vraag ziet nog geen remux
        return plain(path)

    monkeypatch.setattr(stream_audio, "_stat", slow)
    out = await asyncio.gather(*(client.get(url) for _ in range(4)), return_exceptions=True)
    got = [r if isinstance(r, BaseException) else (r.status_code, r.content == DATA) for r in out]
    assert got == [(200, True)] * 4, got
    assert len(handler.calls) == 1, f"{len(handler.calls)} remuxes voor één bestand"
    assert (cache_dir(settings) / f"{node['sha']}.mp4").read_bytes() == DATA


@pytest.mark.anyio
async def test_discard_voor_het_part_bestand(world, client, monkeypatch):
    """Review golf 4, C6: een discard nog vóór de remux zijn .part opent. Het .part komt pas
    daarna, dus alleen de vlag discarded houdt de inhoud uit de cache."""
    import threading

    settings, fake, headers, _ = world
    handler = Remux()
    fake.on(Op.REMUX_AUDIO, handler)
    node, sid = await track(client, headers, settings)
    opening, release = threading.Event(), threading.Event()
    plain = stream_audio.Remux._open

    def slow(self):
        opening.set()
        release.wait(5)
        return plain(self)

    monkeypatch.setattr(stream_audio.Remux, "_open", slow)
    task = asyncio.ensure_future(client.get(f"{STREAM}/{sid}/audio.mp4"))
    await until(opening.is_set, "de start van de remux")
    await stream_audio.discard(node["sha"])
    release.set()
    with pytest.raises(sessions.StreamCut):
        await task
    await until(lambda: not stream_audio._remuxes, "het einde van de remux")
    assert len(handler.calls) == 1
    assert not (cache_dir(settings) / f"{node['sha']}.mp4").exists()
    assert list(cache_dir(settings).iterdir()) == []


def test_de_bovengrens_uit_de_probe():
    """Review golf 4, S1: de bovengrens van audio.mp4. Gemeten (ffmpeg 6.1.1, de argv van
    remux_audio): 600 s witte ruis als Opus van 6 kbps, mono 48 kHz, werd 36.623.878 bytes FLAC.
    De grens moet daarboven liggen, en een uur stereo FLAC/Opus blijft onder één nummer."""
    from types import SimpleNamespace as NS

    mono = NS(sample_rate=48000, channels=1)
    assert stream_audio.expected_bytes(345_537, 600_000, mono, "flac") > 36_623_878
    stereo = NS(sample_rate=48000, channels=2)
    hour = stream_audio.expected_bytes(50_000_000, 3_600_000, stereo, "flac")
    assert hour == int(3600 * 48000 * 2 * 3 * 1.02) + stream_audio.SLACK
    assert stream_audio.expected_bytes(1000, None, stereo, "copy") == 1250 + stream_audio.SLACK
    assert stream_audio.expected_bytes(1000, None, stereo, "flac") is None
    assert stream_audio.expected_bytes(1000, 1000, NS(sample_rate=None, channels=2), "flac") is None


def _lang(sid: str, hours: float) -> None:
    """De probe van de sessie zegt nu dat het nummer zo lang duurt."""
    s = session_of(sid)
    ms = int(hours * 3600 * 1000)
    streams = [st.model_copy(update={"duration_ms": ms}) for st in s.probe.streams]
    s.probe = s.probe.model_copy(update={"duration_ms": ms, "streams": streams})


@pytest.mark.anyio
async def test_een_nummer_te_groot_voor_de_cache(world, client):
    """Review golf 4, S1: 37 uur Opus (100 MB) zou ruim 100 keer zo groot worden als FLAC: 415
    too_large, nog vóór de worker. Twee uur past wel (cache_gb 20)."""
    settings, fake, headers, _ = world
    handler = Remux(b"x" * 1000)
    fake.on(Op.REMUX_AUDIO, handler)
    _, sid = await track(client, headers, settings, "album__opus__01.opus")
    _lang(sid, 37)
    r = await client.get(f"{STREAM}/{sid}/audio.mp4")
    assert r.status_code == 415, r.text
    assert r.json()["detail"]["code"] == "media_unsupported"
    assert r.json()["detail"]["params"]["reasons"] == ["too_large"]
    assert handler.calls == [] and not stream_audio._remuxes
    _lang(sid, 2)
    r = await client.get(f"{STREAM}/{sid}/audio.mp4")
    assert r.status_code == 200 and r.content == b"x" * 1000


@pytest.mark.anyio
async def test_boven_het_budget_stopt_de_remux(world, client, monkeypatch):
    """Review golf 4, S1: zegt de probe te weinig (of liegt hij), dan stopt de remux bij zijn
    budget: het .part weg, niets in de cache, de opdracht in de worker weg."""
    settings, fake, headers, _ = world
    handler = Remux()
    fake.on(Op.REMUX_AUDIO, handler)
    monkeypatch.setattr(stream_audio, "expected_bytes", lambda *a: 1000)
    _, sid = await track(client, headers, settings, "album__opus__01.opus")
    with pytest.raises(sessions.StreamCut):
        await client.get(f"{STREAM}/{sid}/audio.mp4")
    await until(lambda: not stream_audio._remuxes, "het einde van de remux")
    assert list(cache_dir(settings).iterdir()) == []
    await until(lambda: fake.procs == 0, "de opdracht in de worker weg")
    assert len(handler.calls) == 1


@pytest.mark.anyio
async def test_weinig_schijf_geen_remux(world, client, monkeypatch):
    """Review golf 4, S1: minder dan 10 GB vrij na het budget: 503 media_busy {reason: disk},
    zoals remux en transcode van video."""
    settings, fake, headers, _ = world
    handler = Remux()
    fake.on(Op.REMUX_AUDIO, handler)
    _, sid = await track(client, headers, settings)
    monkeypatch.setattr(stream_audio.admission, "disk_free", lambda path=None: 10 * 1024**3)
    r = await client.get(f"{STREAM}/{sid}/audio.mp4")
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "media_busy"
    assert r.json()["detail"]["params"]["reason"] == "disk"
    assert r.headers["retry-after"]
    assert handler.calls == [] and not stream_audio._remuxes


@pytest.mark.anyio
async def test_wat_loopt_telt_mee_in_het_deel(world, client, monkeypatch):
    """Review golf 4, S1: de budgetten van de remuxen die nog lopen, samen met een nieuwe, niet
    boven het deel van audio/; is de eerste klaar, dan ruimt de start van de tweede hem op."""
    settings, fake, headers, _ = world
    gate = asyncio.Event()
    first_handler = Remux(b"a" * 1000, gate=gate, pieces=2)
    fake.on(Op.REMUX_AUDIO, first_handler)
    monkeypatch.setattr(stream_audio.cache, "limit_bytes", lambda gb: 4 * 1500)
    monkeypatch.setattr(stream_audio, "TRACK_SHARE", 1)
    monkeypatch.setattr(stream_audio, "expected_bytes", lambda *a: 1000)
    one, sid1 = await track(client, headers, settings, "album__flac__01.flac", "een.flac")
    two, sid2 = await track(client, headers, settings, "album__flac__02.flac", "twee.flac")
    first = asyncio.ensure_future(client.get(f"{STREAM}/{sid1}/audio.mp4"))
    await until(lambda: first_handler.first.is_set(), "het eerste stuk")
    r = await client.get(f"{STREAM}/{sid2}/audio.mp4")
    assert r.status_code == 503 and r.json()["detail"]["params"]["reason"] == "disk", r.text
    gate.set()
    assert (await first).status_code == 200
    await until(lambda: not stream_audio._remuxes, "het einde van de eerste remux")
    fake.on(Op.REMUX_AUDIO, Remux(b"b" * 1000))
    r = await client.get(f"{STREAM}/{sid2}/audio.mp4")
    assert r.status_code == 200 and r.content == b"b" * 1000
    assert not (cache_dir(settings) / f"{one['sha']}.mp4").exists()
    assert (cache_dir(settings) / f"{two['sha']}.mp4").exists()


@pytest.mark.anyio
async def test_niemand_leest_meer(world, client, monkeypatch):
    """Review golf 4, S1: de vraag ging weg (of kwam nooit bij follow): na IDLE_S zonder volger
    stopt de remux, en er blijft niets achter."""
    settings, fake, headers, _ = world
    monkeypatch.setattr(stream_audio, "IDLE_S", 0.2)
    gate = asyncio.Event()
    handler = Remux(gate=gate)
    fake.on(Op.REMUX_AUDIO, handler)
    node, sid = await track(client, headers, settings)
    s = session_of(sid)
    args = RemuxAudioArgs(demuxer="flac", audio=0, codec="copy")
    # een volger die na het eerste stuk weggaat
    remux = stream_audio.Remux(cache_dir(settings), "a.mp4", node["sha"], s.path, args)
    stream_audio._remuxes["a.mp4"] = remux
    await remux.started
    body = stream_audio.follow(app, remux)
    assert await anext(body)
    await asyncio.sleep(0.4)
    assert not remux.task.done()  # er leest nog iemand (al wacht hij)
    await body.aclose()
    await until(lambda: remux.task.done(), "het einde van de remux")
    assert remux.task.cancelled()
    # nooit een volger
    lone = stream_audio.Remux(cache_dir(settings), "b.mp4", node["sha"], s.path, args)
    stream_audio._remuxes["b.mp4"] = lone
    await lone.started
    await until(lambda: lone.task.done(), "het einde van de remux zonder volger")
    assert lone.task.cancelled() and not stream_audio._remuxes
    assert list(cache_dir(settings).iterdir()) == []
    await until(lambda: fake.procs == 0, "de opdrachten in de worker weg")
