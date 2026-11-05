"""stream met de echte ffmpeg: herstarts, tijden en het rooster (M4, Keuze 22–25; spike S2).

De worker draait in dit proces (server.Worker) met de echte opdracht stream; de uitvoer gaat
door de knipper van de app (fmp4.Cutter). Hooguit één ffmpeg tegelijk (regel 13): elke test
start zijn runs na elkaar, en wacht op het einde van de vorige.

Wat er bewezen wordt (§2 M4, test_media_restart.py):
- een run vanaf 0 en een herstart op segment 5 geven dezelfde videotijden per segment, en het
  verschil in audio is ≤ 100 ms;
- tfdt begint op mediatijd + 1 (J4): de pts van het eerste sample is origin;
- trun v1 met B-frames (H.264 bij transcode) en bij HEVC-remux: de pts van het eerste sample
  (tfdt plus de getekende offset) is de brontijd + 1 s, binnen één beeld (V4);
- fps240.mp4: precies één IDR per 2 s en 60 fps (V5);
- een late videostart: de herstart met -ss (origin + 2k) blijft op het rooster;
- interlaced.ts: de aanpak voor MPEG-TS (eerder beginnen, trim) levert het hele segment;
- AC-3 kopiëren (delay_moov, geen elst), inbranden met twee invoeren, VP9 en de tonemap.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.media import client, fmp4
from app.media import grid as G
from app.media.types import Probe
from app.media.wire import Op
from app.media.worker import args as A
from app.media.worker.server import Config, Worker
from tests.media import boxes, record
from tests.media import fixtures as F

pytestmark = [pytest.mark.anyio, pytest.mark.media]

KEY = "de-sleutel-van-de-herstart-" * 2
CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
NON_SYNC = 0x00010000


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def worker(tmp_path_factory):
    base = tmp_path_factory.mktemp("restart-worker")
    config = Config(socket=base / "w.sock", max_procs=2, max_transcodes=1, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        yield w
    finally:
        await w.stop()
        client.bind(None)


def _read_all(fd: int) -> bytes:
    try:
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


async def stream(source: Path, args: A.StreamArgs) -> tuple[fmp4.Init, list[fmp4.Fragment], bytes]:
    """Eén run; geeft de init, de fragmenten en alle bytes. Faalt de run, dan WorkerFailed."""
    job, (r,) = await client.call(
        Op.STREAM, args, source=source, outputs=1, inputs=2 if args.burn else 1
    )
    async with job:
        data, _ = await asyncio.gather(asyncio.to_thread(_read_all, r), job.result())
    cut = fmp4.Cutter()
    items = cut.feed(data)
    cut.close()
    init = items[0]
    assert isinstance(init, fmp4.Init)
    frags = [i for i in items[1:] if isinstance(i, fmp4.Fragment)]
    return init, frags, data


def probe_of(name: str) -> Probe:
    found = record.load(name)
    assert found is not None, name
    return Probe.model_validate(found["data"])


def x264(width: int, height: int, *, level: str = "3.0", **kw) -> A.VideoEncode:
    return A.VideoEncode(codec="libx264", width=width, height=height, level=level,
                         maxrate_k=1500, **kw)  # fmt: skip


AAC = A.AudioEncode(codec="aac", channels=2, bitrate_k=160)


def transcode(probe: Probe, demuxer: str, start_s: float = 0, **kw) -> A.StreamArgs:
    video = probe.video()
    audio = probe.audio_streams()
    assert video is not None
    base = {
        "mode": "transcode",
        "demuxer": demuxer,
        "start_s": start_s,
        "source_start_s": (probe.start_ms or 0) / 1000,
        "video_index": video.index,
        "audio_index": audio[0].index if audio else None,
        "video": x264(video.width or 640, video.height or 360),
        "audio": AAC if audio else "none",
        "grid": "2s",
    }
    return A.StreamArgs(**{**base, **kw})


def by_segment(lay: G.Layout, frags: list[fmp4.Fragment]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for f in frags:
        if f.video_start_ms is None:
            continue
        out.setdefault(lay.segment_of(f.video_start_ms), []).append(f.video_start_ms)
    return out


async def test_een_herstart_op_segment_5_geeft_dezelfde_tijden(worker):
    probe = probe_of("stream_src.mkv")
    src = F.make("stream_src.mkv")
    lay = G.transcode_layout(probe)
    _, whole, _ = await stream(src, transcode(probe, "matroska"))
    _, part, _ = await stream(src, transcode(probe, "matroska", start_s=lay.start_s(5)))
    # tfdt = mediatijd + 1: het eerste beeld is origin (21 ms), en elk fragment is een segment
    grid = lay.grid
    assert grid is not None
    assert whole[0].video_start_ms == grid.origin_ms == 21
    want = [grid.origin_ms if k == 0 else lay.begin(k) for k in range(lay.count)]
    assert [f.video_start_ms for f in whole] == want
    assert all(f.sync for f in whole) and all(f.sync for f in part)
    # Dezelfde videotijden per segment vanaf 5
    a = {k: v for k, v in by_segment(lay, whole).items() if k >= 5}
    b = by_segment(lay, part)
    assert a == b and min(b) == 5
    # Het geluid na de herstart wijkt hooguit 100 ms af (D4: 32–54 ms)
    first = next(f for f in whole if f.video_start_ms == part[0].video_start_ms)
    assert first.audio_start_ms is not None and part[0].audio_start_ms is not None
    assert abs(first.audio_start_ms - part[0].audio_start_ms) <= 100


async def test_trun_v1_met_b_frames_en_de_pts_op_de_brontijd(worker):
    """V4: x264 veryfast maakt B-frames; met +negative_cts_offsets is het trun versie 1, en de
    pts van het eerste sample is tfdt (de offset van het keyframe is 0)."""
    probe = probe_of("stream_src.mkv")
    init, frags, data = await stream(F.make("stream_src.mkv"), transcode(probe, "matroska"))
    assert {f.trun_version for f in frags} == {1}
    parsed = boxes.parse(data)
    video = next(t for t in parsed.tracks.values() if t.handler == "vide")
    negative = False
    for frag in parsed.fragments:
        for run in boxes.read_moof(frag.data[: _moof_len(frag.data)], parsed.tracks):
            if run.track == video.id:
                negative = negative or any(c < 0 for _d, _f, c in run.samples)
    assert negative, "geen B-frames (negatieve offsets) gezien"
    frame_ms = 1000 / 24
    assert all(abs(f.video_cto_ms) < frame_ms for f in frags)
    assert init.video is not None and init.video.entry == "avc1"


async def test_hevc_remux_heeft_trun_v1_en_hvc1(worker):
    probe = probe_of("hevc10_hdr10.mkv")
    video = probe.video()
    assert video is not None
    args = A.StreamArgs(
        mode="remux", demuxer="matroska", source_start_s=(probe.start_ms or 0) / 1000,
        video_index=video.index, audio_index=None, video="copy", audio="none", hvc1=True,
    )  # fmt: skip
    init, frags, _ = await stream(F.make("hevc10_hdr10.mkv"), args)
    assert init.video is not None and init.video.entry == "hvc1"
    assert frags and frags[0].sync
    # De pts van het eerste sample is de brontijd van het keyframe + 1 s (binnen één beeld)
    want = (video.start_ms or 0) if video.start_ms is not None else 0
    assert abs(frags[0].video_start_ms - want) <= 1000 / (video.fps or 24)
    assert all(f.trun_version in (0, 1) for f in frags)


async def test_fps240_geeft_een_idr_per_2_s_en_60_fps(worker):
    probe = probe_of("fps240.mp4")
    video = probe.video()
    assert video is not None
    args = transcode(probe, "mov", video=x264(640, 360, level="3.1"))
    _, _, data = await stream(F.make("fps240.mp4"), args)
    lay = G.transcode_layout(probe)
    parsed = boxes.parse(data)
    track = next(t for t in parsed.tracks.values() if t.handler == "vide")
    per: dict[int, list[tuple[int, int, int]]] = {}
    for frag in parsed.fragments:
        runs = [r for r in boxes.read_moof(frag.data[: _moof_len(frag.data)], parsed.tracks)
                if r.track == track.id]  # fmt: skip
        per.setdefault(lay.segment_of(frag.video_start_ms), []).extend(
            s for r in runs for s in r.samples
        )
    # 6 s op 60 fps: drie segmenten van 2 s met elk 120 beelden en één IDR vooraan. -fpsmax
    # vult het eind aan: wat voorbij N komt, hoort bij het laatste segment (Keuze 22)
    assert sorted(per) == list(range(lay.count)) == [0, 1, 2]
    for k in range(lay.count - 1):
        samples = per[k]
        assert len(samples) == 120, k
        assert [i for i, (_d, f, _c) in enumerate(samples) if not f & NON_SYNC] == [0], k
    assert 120 <= len(per[2]) <= 124
    starts = [f.video_start_ms for f in parsed.fragments]
    assert starts[:3] == [0, 2000, 4000]


async def test_een_late_videostart_blijft_op_het_rooster(worker):
    probe = probe_of("late_video.mkv")
    lay = G.transcode_layout(probe)
    assert lay.grid is not None and lay.grid.origin_ms == 521
    src = F.make("late_video.mkv")
    args = transcode(probe, "matroska", video=x264(320, 240))
    _, whole, _ = await stream(src, args)
    _, part, _ = await stream(src, args.model_copy(update={"start_s": lay.start_s(2)}))
    assert whole[0].video_start_ms == 521
    assert part[0].video_start_ms == lay.begin(2) == 4521
    assert by_segment(lay, part) == {k: v for k, v in by_segment(lay, whole).items() if k >= 2}


async def test_interlaced_ts_herstart_op_de_grens(worker):
    """MPEG-TS zoekt niet precies (spike punt 11): zonder de aanpak begon de uitvoer 200 ms te
    laat (4221 in plaats van 4021). Met eerder beginnen en trim ligt het op de grens."""
    probe = probe_of("interlaced.ts")
    lay = G.transcode_layout(probe)
    grid = lay.grid
    assert grid is not None
    src = F.make("interlaced.ts")
    args = transcode(probe, "mpegts", video=x264(720, 576, deinterlace=True))
    _, whole, _ = await stream(src, args)
    _, part, _ = await stream(src, args.model_copy(update={"start_s": lay.start_s(2)}))
    assert part[0].video_start_ms == lay.begin(2)
    assert by_segment(lay, part) == {k: v for k, v in by_segment(lay, whole).items() if k >= 2}
    first = next(f for f in whole if f.video_start_ms == part[0].video_start_ms)
    assert abs((first.audio_start_ms or 0) - (part[0].audio_start_ms or 0)) <= 100


async def test_ts_remux_begint_niet_na_het_keyframe(worker):
    """Remux van TS (na de terugval): de worker begint 5 s eerder, de app gooit weg wat vóór
    b_k ligt. Er is altijd een fragment op b_k zelf."""
    probe = probe_of("interlaced.ts")
    video = probe.video()
    assert video is not None
    s = (probe.start_ms or 0) / 1000
    args = A.StreamArgs(
        mode="remux", demuxer="mpegts", source_start_s=s, video_index=video.index,
        audio_index=probe.audio_streams()[0].index, video="copy", audio=AAC,
    )  # fmt: skip
    src = F.make("interlaced.ts")
    _, whole, _ = await stream(src, args)
    keys = [f.video_start_ms for f in whole]
    assert len(keys) >= 4  # een GOP van 35 beelden op 25 fps: elke 1,4 s
    for b in keys[2:4]:
        _, part, _ = await stream(src, args.model_copy(update={"start_s": b / 1000 - 0.5}))
        starts = [f.video_start_ms for f in part]
        assert b in starts and starts[0] <= b
        assert starts[starts.index(b) :] == [k for k in keys if k >= b]


async def test_ac3_kopieren_met_delay_moov_en_zonder_elst(worker):
    probe = probe_of("h264_ac3_51.mkv")
    video = probe.video()
    assert video is not None
    args = A.StreamArgs(
        mode="remux", demuxer="matroska", source_start_s=(probe.start_ms or 0) / 1000,
        video_index=video.index, audio_index=probe.audio_streams()[0].index,
        video="copy", audio="copy",
    )  # fmt: skip
    init, frags, _ = await stream(F.make("h264_ac3_51.mkv"), args)
    assert init.audio is not None and init.audio.entry == "ac-3"
    assert b"elst" not in init.data
    assert frags and frags[0].audio_start_ms is not None


async def test_inbranden_met_twee_invoeren(worker):
    probe = probe_of("pgs.mkv")
    lay = G.transcode_layout(probe)
    sub = next(s for s in probe.streams if s.type == "subtitle")
    video = x264(1280, 720, level="3.1", burn_index=sub.index, burn_start_s=0)
    args = transcode(probe, "matroska", video=video)
    _, whole, _ = await stream(F.make("pgs.mkv"), args)
    assert lay.grid is not None
    assert [f.video_start_ms for f in whole][:2] == [lay.grid.origin_ms, lay.begin(1)]
    restart = args.model_copy(update={"start_s": lay.start_s(2)})
    _, part, _ = await stream(F.make("pgs.mkv"), restart)
    assert part[0].video_start_ms == lay.begin(2)


async def test_vp9_en_opus_op_het_rooster(worker):
    probe = probe_of("stream_src.mkv")
    lay = G.transcode_layout(probe)
    vp9 = A.VideoEncode(codec="libvpx-vp9", width=640, height=360, maxrate_k=1000)
    opus = A.AudioEncode(codec="libopus", channels=2, bitrate_k=128)
    args = transcode(probe, "matroska", video=vp9, audio=opus, start_s=lay.start_s(7))
    init, frags, _ = await stream(F.make("stream_src.mkv"), args)
    assert init.video is not None and init.video.entry == "vp09"
    assert init.audio is not None and init.audio.entry == "Opus"
    assert [f.video_start_ms for f in frags] == [lay.begin(k) for k in (7, 8, 9)]


async def test_tonemap_van_hdr10(worker):
    probe = probe_of("hevc10_hdr10.mkv")
    video = probe.video()
    assert video is not None
    enc = x264(1280, 720, level="3.1", tonemap="pq")
    args = transcode(probe, "matroska", video=enc, audio_index=None, audio="none")
    init, frags, _ = await stream(F.make("hevc10_hdr10.mkv"), args)
    assert init.video is not None and init.video.entry == "avc1"
    assert len(frags) == G.transcode_layout(probe).count


def _moof_len(data: bytes) -> int:
    return int.from_bytes(data[:4], "big")
