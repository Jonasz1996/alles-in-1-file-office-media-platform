"""De eigen lezers voor de keyframe-index (M4, Keuze 17): Matroska-Cues en MP4 stss.

- Zonder marker: kleine bestanden die de test zelf bouwt (EBML en MP4-dozen), de randgevallen
  (SeekHead naar Cues achteraan, een tweede SeekHead, een Segment zonder grootte, de volgorde
  van de sporen, elst met een lege edit en ctts met B-frames, de grens van 4 MB) en fuzz met
  vaste seeds: een lezer geeft een Index of None, nooit een uitzondering.
- Met marker media: elke video-fixture tegen ffprobe (de pts van de keyframe-pakketten), een
  volgorde met audio vóór video, en de opdracht keyframes via de echte worker (eigen lezer en
  terugval geven dezelfde tijden).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import struct
import subprocess
from pathlib import Path

import pytest

from app.media import client
from app.media.wire import Op
from app.media.worker import args as A
from app.media.worker import containers as C
from app.media.worker.ops import keyframes as K
from app.media.worker.server import Config, Worker
from tests.media import fixtures as F

# --- EBML bouwen ------------------------------------------------------------------------------


def vint(n: int) -> bytes:
    for length in range(1, 9):
        if n < (1 << (7 * length)) - 1:
            return (n | (1 << (7 * length))).to_bytes(length, "big")
    raise ValueError(n)


def el(eid: int, payload: bytes, *, unknown: bool = False) -> bytes:
    ident = eid.to_bytes((eid.bit_length() + 7) // 8, "big")
    size = b"\x01\xff\xff\xff\xff\xff\xff\xff" if unknown else vint(len(payload))
    return ident + size + payload


def uint(eid: int, value: int) -> bytes:
    return el(eid, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def track(number: int, kind: int, codec: bytes = b"V_MPEG4/ISO/AVC") -> bytes:
    return el(C.TRACK_ENTRY, uint(C.TRACK_NUMBER, number) + uint(C.TRACK_TYPE, kind)
              + el(C.CODEC_ID, codec))  # fmt: skip


def cue(time: int, track_no: int, cluster: int, relative: int | None = None) -> bytes:
    pos = uint(C.CUE_TRACK, track_no) + uint(C.CUE_CLUSTER_POSITION, cluster)
    if relative is not None:
        pos += uint(C.CUE_RELATIVE_POSITION, relative)
    return el(C.CUE_POINT, uint(C.CUE_TIME, time) + el(C.CUE_TRACK_POSITIONS, pos))


def seek(target: int, position: int) -> bytes:
    ident = target.to_bytes((target.bit_length() + 7) // 8, "big")
    # Vaste breedte van de positie: zo hangt de grootte van de SeekHead niet van de positie af
    return el(C.SEEK, el(C.SEEK_ID, ident) + el(C.SEEK_POSITION, position.to_bytes(8, "big")))


def mkv(
    *,
    times: list[int],
    tracks: list[bytes] | None = None,
    video_no: int = 1,
    scale: int = 1_000_000,
    duration: float | None = None,
    unknown_segment: bool = False,
    cluster_size: int = 1000,
    second_seekhead: bool = False,
) -> bytes:
    """Een Matroska met SeekHead, Info, Tracks, Clusters van cluster_size bytes en de Cues
    achteraan (zoals ffmpeg): alleen via de SeekHead te vinden."""
    info = uint(C.TIMECODE_SCALE, scale)
    if duration is not None:
        info += el(C.DURATION, struct.pack(">d", duration))
    info_el = el(C.INFO, info)
    tracks_el = el(C.TRACKS, b"".join(tracks or [track(1, 1), track(2, 2, b"A_AAC")]))
    clusters = b"".join(el(C.CLUSTER, b"\x00" * (cluster_size - 12)) for _ in times)

    # De SeekHead met vaste grootte: eerst een proef, dan de echte posities
    def build(head_size: int) -> tuple[bytes, bytes]:
        pos_info = head_size
        pos_tracks = pos_info + len(info_el)
        pos_clusters = pos_tracks + len(tracks_el)
        cluster_positions = [pos_clusters + i * len(el(C.CLUSTER, b"\x00" * (cluster_size - 12)))
                             for i in range(len(times))]  # fmt: skip
        cues_el = el(C.CUES, b"".join(cue(t, video_no, p) for t, p in
                                      zip(times, cluster_positions, strict=True)))  # fmt: skip
        pos_cues = pos_clusters + len(clusters)
        entries = seek(C.INFO, pos_info) + seek(C.TRACKS, pos_tracks)
        if second_seekhead:
            second = el(C.SEEKHEAD, seek(C.CUES, pos_cues))
            entries += seek(C.SEEKHEAD, pos_cues + len(cues_el))
            head = el(C.SEEKHEAD, entries)
            return head, info_el + tracks_el + clusters + cues_el + second
        entries += seek(C.CUES, pos_cues)
        head = el(C.SEEKHEAD, entries)
        return head, info_el + tracks_el + clusters + cues_el

    head, _ = build(0)
    head, rest = build(len(head))
    body = head + rest
    header = el(C.EBML, el(C.DOCTYPE, b"matroska"))
    return header + el(C.SEGMENT, body, unknown=unknown_segment)


def fd_of(tmp_path: Path, data: bytes, name: str = "x") -> int:
    path = tmp_path / name
    path.write_bytes(data)
    return os.open(path, os.O_RDONLY)


def read_mkv(tmp_path: Path, data: bytes, index: int = 0, **kw) -> C.Index | None:
    fd = fd_of(tmp_path, data)
    try:
        return C.matroska(fd, index, **kw)
    finally:
        os.close(fd)


def test_cues_achteraan_via_de_seekhead(tmp_path):
    data = mkv(times=[0, 2000, 4000, 6000], duration=8000.0)
    idx = read_mkv(tmp_path, data)
    assert idx is not None
    assert idx.times_ms == [0, 2000, 4000, 6000] and idx.source == "cues"
    one = len(el(C.CLUSTER, b"\x00" * (1000 - 12)))  # de afstand tussen twee clusters
    assert idx.sizes[:3] == [one, one, one] and not idx.sparse
    assert idx.end_ms == 8000 and idx.max_gop_ms() == 2000


def test_een_tweede_seekhead(tmp_path):
    idx = read_mkv(tmp_path, mkv(times=[0, 1000], second_seekhead=True))
    assert idx is not None and idx.times_ms == [0, 1000]


def test_een_segment_zonder_grootte(tmp_path):
    idx = read_mkv(tmp_path, mkv(times=[0, 2000, 4000], unknown_segment=True))
    assert idx is not None and idx.times_ms == [0, 2000, 4000]


def test_timecodescale_telt(tmp_path):
    idx = read_mkv(tmp_path, mkv(times=[0, 41708], scale=100_000))  # eenheden van 0,1 ms
    assert idx is not None and idx.times_ms == [0, 4171]


def test_gaten_boven_20_s_zijn_sparse(tmp_path):
    idx = read_mkv(tmp_path, mkv(times=[0, 25000, 30000], duration=31000.0))
    assert idx is not None and idx.sparse
    idx = read_mkv(tmp_path, mkv(times=[0, 10000], duration=40000.0))  # het eind telt mee
    assert idx is not None and idx.sparse and idx.max_gop_ms() == 30000


def test_de_index_van_ffprobe_telt_alleen_sporen_met_een_stream(tmp_path):
    """Een spoor van een soort die ffmpeg overslaat (0x12, knoppen) telt niet mee; audio vóór
    video maakt de video index 1."""
    tracks = [track(3, 0x12, b"B_VOBBTN"), track(7, 2, b"A_AAC"), track(1, 1)]
    data = mkv(times=[0, 2000], tracks=tracks, video_no=1)
    assert read_mkv(tmp_path, data, 0) is None  # index 0 is de audio
    idx = read_mkv(tmp_path, data, 1)
    assert idx is not None and idx.times_ms == [0, 2000]
    # Zonder CodecID maakt ffmpeg geen stream
    tracks = [track(7, 2, b""), track(1, 1)]
    idx = read_mkv(tmp_path, mkv(times=[0, 2000], tracks=tracks), 0)
    assert idx is not None and idx.times_ms == [0, 2000]


def test_cues_van_een_ander_spoor_tellen_niet(tmp_path):
    data = mkv(times=[0, 2000], video_no=2)  # de Cues wijzen naar spoor 2 (audio)
    assert read_mkv(tmp_path, data, 0) is None


@pytest.mark.parametrize("duration", [math.nan, math.inf, -math.inf, -5.0, 0.0])
def test_een_duur_die_geen_getal_is_is_onbekend(tmp_path, duration):
    # review golf 2, S2: round(NaN) gaf een ValueError die matroska() niet ving (de worker gaf
    # dan INTERNAL, en de terugval op ffprobe liep nooit)
    idx = read_mkv(tmp_path, mkv(times=[0, 2000, 4000], duration=duration))
    assert idx is not None
    assert idx.times_ms == [0, 2000, 4000]
    assert idx.end_ms is None


def test_de_grens_van_4_mb(tmp_path):
    data = mkv(times=[i * 100 for i in range(300)], cluster_size=20_000)
    assert read_mkv(tmp_path, data) is not None
    assert read_mkv(tmp_path, data, budget=64 * 1024) is None


def test_geen_ebml_of_afgekapt(tmp_path):
    assert read_mkv(tmp_path, b"") is None
    assert read_mkv(tmp_path, b"\x1a\x45\xdf\xa3") is None
    full = mkv(times=[0, 2000, 4000])
    assert read_mkv(tmp_path, full[: len(full) - 10]) is None  # de Cues zijn afgekapt


# --- MP4 bouwen -------------------------------------------------------------------------------


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full(kind: bytes, payload: bytes, version: int = 0) -> bytes:
    return box(kind, bytes([version, 0, 0, 0]) + payload)


def table(kind: bytes, rows: list[tuple[int, ...]], fmt: str, version: int = 0) -> bytes:
    body = struct.pack(">I", len(rows)) + b"".join(struct.pack(fmt, *r) for r in rows)
    return full(kind, body, version)


def trak(
    handler: bytes,
    *,
    scale: int = 12_288,
    count: int = 10,
    delta: int = 512,
    ctts: list[tuple[int, int]] | None = None,
    stss: list[int] | None = None,
    elst: list[tuple[int, int]] | None = None,
    sizes: list[int] | None = None,
) -> bytes:
    mdhd = full(b"mdhd", struct.pack(">IIII", 0, 0, scale, count * delta) + b"\x00" * 4)
    hdlr = full(b"hdlr", b"\x00" * 4 + handler + b"\x00" * 12 + b"x\x00")
    stbl = table(b"stts", [(count, delta)], ">II")
    if ctts is not None:
        stbl += table(b"ctts", ctts, ">Ii", version=1)
    if stss is not None:
        stbl += table(b"stss", [(s,) for s in stss], ">I")
    sizes = sizes or [100] * count
    stbl += full(b"stsz", struct.pack(">II", 0, count) + struct.pack(f">{count}I", *sizes))
    minf = box(b"minf", box(b"stbl", stbl))
    edts = b""
    if elst is not None:
        rows = [(d, m, 1, 0) for d, m in elst]
        edts = box(b"edts", table(b"elst", rows, ">IiHH"))
    return box(b"trak", full(b"tkhd", b"\x00" * 80) + edts + box(b"mdia", mdhd + hdlr + minf))


def mp4(*traks: bytes, moov_at_end: bool = False, mvex: bool = False) -> bytes:
    mvhd = full(b"mvhd", struct.pack(">IIII", 0, 0, 1000, 0) + b"\x00" * 80)
    moov = box(b"moov", mvhd + b"".join(traks) + (box(b"mvex", b"") if mvex else b""))
    ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00")
    mdat = box(b"mdat", b"\x00" * 5000)
    return ftyp + (mdat + moov if moov_at_end else moov + mdat)


def read_mp4(tmp_path: Path, data: bytes, index: int = 0, **kw) -> C.Index | None:
    fd = fd_of(tmp_path, data, "x.mp4")
    try:
        return C.mp4(fd, index, **kw)
    finally:
        os.close(fd)


def test_stss_met_de_moov_achteraan(tmp_path):
    data = mp4(trak(b"vide", stss=[1, 5, 9]), moov_at_end=True)
    idx = read_mp4(tmp_path, data)
    assert idx is not None and idx.source == "stss"
    # sample 0, 4 en 8 met delta 512 op 12.288: 0, 167 en 333 ms
    assert idx.times_ms == [0, 167, 333]
    assert idx.sizes == [400, 400, 200]
    assert idx.end_ms == round(10 * 512 * 1000 / 12_288)


def test_ctts_en_elst_zoals_ffmpeg(tmp_path):
    """B-frames: de offset van het keyframe is 1024 (83 ms), en de elst begint de media op 1024.
    De pts van het eerste keyframe is dan 0, zoals ffprobe hem geeft."""
    ctts = [(1, 1024), (1, 2560), (8, 1024)]
    data = mp4(trak(b"vide", stss=[1, 6], ctts=ctts, elst=[(10_000, 1024)]))
    idx = read_mp4(tmp_path, data)
    assert idx is not None and idx.times_ms == [0, round(5 * 512 * 1000 / 12_288)]


def test_een_lege_edit_vooraan_is_vertraging(tmp_path):
    # 500 ms leeg (in de tijdschaal van de film, 1000), dan de media vanaf 0
    data = mp4(trak(b"vide", stss=[1], elst=[(500, -1), (10_000, 0)]))
    idx = read_mp4(tmp_path, data)
    assert idx is not None and idx.times_ms == [500]


def test_zonder_stss_is_elk_sample_een_keyframe(tmp_path):
    idx = read_mp4(tmp_path, mp4(trak(b"vide", count=4)))
    assert idx is not None and len(idx.times_ms) == 4


def test_de_index_is_de_volgorde_van_de_traks(tmp_path):
    data = mp4(trak(b"soun", stss=[1]), trak(b"vide", stss=[1, 3]))
    assert read_mp4(tmp_path, data, 0) is None
    idx = read_mp4(tmp_path, data, 1)
    assert idx is not None and len(idx.times_ms) == 2
    assert read_mp4(tmp_path, data, 5) is None


def test_gefragmenteerd_geeft_niets(tmp_path):
    assert read_mp4(tmp_path, mp4(trak(b"vide", stss=[1]), mvex=True)) is None


def test_mp4_grenzen(tmp_path):
    data = mp4(trak(b"vide", stss=[1]))
    assert read_mp4(tmp_path, data, budget=64) is None
    assert read_mp4(tmp_path, data[:40]) is None
    # stss die naar samples buiten stsz wijst: die vallen weg
    idx = read_mp4(tmp_path, mp4(trak(b"vide", count=3, stss=[1, 99])))
    assert idx is not None and idx.times_ms == [0]


def test_read_kiest_de_lezer_bij_de_demuxer(tmp_path):
    fd = fd_of(tmp_path, mkv(times=[0, 1000]))
    try:
        assert C.read(fd, "matroska", 0) is not None
        assert C.read(fd, "mpegts", 0) is None  # TS: alleen de terugval
    finally:
        os.close(fd)


# --- fuzz met vaste seeds ---------------------------------------------------------------------


def mutate(rng: random.Random, data: bytes) -> bytes:
    buf = bytearray(data)
    for _ in range(rng.randint(1, 8)):
        choice = rng.random()
        pos = rng.randrange(len(buf)) if buf else 0
        if choice < 0.5 and buf:
            buf[pos] = rng.randrange(256)
        elif choice < 0.7:
            buf[pos:pos] = rng.randbytes(rng.randint(1, 16))
        elif choice < 0.85 and buf:
            del buf[pos : pos + rng.randint(1, 32)]
        else:
            buf[pos : pos + 4] = rng.choice([b"\xff" * 4, b"\x00" * 4, b"\x01\xff\xff\xff"])
    return bytes(buf)


@pytest.mark.parametrize("seed", [3, 17, 2024])
def test_fuzz_matroska_en_mp4(tmp_path, seed):
    rng = random.Random(seed)
    seeds = [
        ("matroska", mkv(times=[0, 2000, 4000], duration=6000.0)),
        ("matroska", mkv(times=[0, 1000], second_seekhead=True, unknown_segment=True)),
        ("matroska", mkv(times=[0, 2000], duration=math.nan)),
        ("matroska", mkv(times=[0, 2000], duration=-math.inf)),
        ("mov", mp4(trak(b"vide", stss=[1, 5], ctts=[(10, 512)], elst=[(1000, 512)]))),
        ("mov", mp4(trak(b"soun"), trak(b"vide", stss=[1]), moov_at_end=True)),
    ]
    path = tmp_path / "fuzz"
    for _ in range(300):
        demuxer, base = rng.choice(seeds)
        path.write_bytes(mutate(rng, base))
        fd = os.open(path, os.O_RDONLY)
        try:
            for index in (0, 1):
                found = C.read(fd, demuxer, index)
                assert found is None or isinstance(found, C.Index)
                if found is not None:
                    assert found.times_ms == sorted(set(found.times_ms))
                    assert len(found.sizes) == len(found.times_ms)
        finally:
            os.close(fd)


# --- tegen ffprobe (echte fixtures) -------------------------------------------------------------

VIDEO_FIXTURES = [
    "h264_aac.mp4", "h264_aac_moov_end.mp4", "vp9_opus.webm", "vp9_opus.mkv", "av1_opus.mp4",
    "hevc10_hdr10.mkv", "h264_ac3_51.mkv", "fps23976.mkv", "stream_src.mkv", "late_video.mkv",
    "multi_audio.mkv", "subs_text.mkv", "pgs.mkv", "vobsub.mkv", "fps240.mp4", "noaudio.mp4",
    "h264_hi10p.mkv", "hevc2160_hdr10.mkv", "long_gop.mkv", "irregular_gop.mkv",
    "chapters.mkv",
]  # fmt: skip
NOTHING = ["no_cues.mkv", "stream_h264.mp4", "random.mkv", "empty.mkv", "broken.mp4"]


def ffprobe_keyframes(path: Path, index: int) -> tuple[list[int], list[int]]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", str(index), "-show_entries",
         "packet=pts_time,size,flags", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout  # fmt: skip
    found = K.parse_packets(out.encode())
    assert found is not None
    return found.times_ms, found.sizes


def demuxer_and_video(path: Path) -> tuple[str, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name:stream=index,codec_type",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout  # fmt: skip
    info = json.loads(out)
    name = info["format"]["format_name"].split(",")[0]
    video = next(s["index"] for s in info["streams"] if s["codec_type"] == "video")
    return name, video


@pytest.mark.media
@pytest.mark.parametrize("name", VIDEO_FIXTURES)
def test_gelijk_aan_ffprobe(name):
    path = F.make(name)
    demuxer, video = demuxer_and_video(path)
    want_t, want_s = ffprobe_keyframes(path, video)
    fd = os.open(path, os.O_RDONLY)
    try:
        idx = C.read(fd, demuxer, video)
    finally:
        os.close(fd)
    assert idx is not None
    assert idx.times_ms == want_t
    if demuxer == "mov":
        assert idx.sizes == want_s  # alleen video, zoals de pakketten van ffprobe
    else:
        # Matroska: de afstand tussen clusters, dus met het geluid erbij: nooit minder
        assert sum(idx.sizes) >= sum(want_s)
    assert idx.sparse == (name == "long_gop.mkv")


@pytest.mark.media
@pytest.mark.parametrize("name", NOTHING)
def test_geen_index_zonder_cues_of_stss(name):
    path = F.make(name)
    fd = os.open(path, os.O_RDONLY)
    try:
        assert C.read(fd, "matroska" if name.endswith(".mkv") else "mov", 0) is None
    finally:
        os.close(fd)


@pytest.mark.media
@pytest.mark.parametrize("ext", ["mkv", "mp4"])
def test_audio_voor_video(tmp_path, ext):
    src = F.make("h264_aac.mp4")
    out = tmp_path / f"av.{ext}"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-map", "0:a", "-map", "0:v", "-c", "copy", str(out)],
        check=True, timeout=60,
    )  # fmt: skip
    demuxer, video = demuxer_and_video(out)
    assert video == 1
    want, _ = ffprobe_keyframes(out, video)
    fd = os.open(out, os.O_RDONLY)
    try:
        assert C.read(fd, demuxer, 0) is None
        idx = C.read(fd, demuxer, 1)
    finally:
        os.close(fd)
    assert idx is not None and idx.times_ms == want


@pytest.mark.media
@pytest.mark.parametrize("seed", [5, 41])
def test_fuzz_op_echte_bytes(tmp_path, seed):
    rng = random.Random(seed)
    sources = [("matroska", F.make("stream_src.mkv")), ("mov", F.make("h264_aac_moov_end.mp4"))]
    path = tmp_path / "fuzz"
    for _ in range(40):
        demuxer, src = rng.choice(sources)
        data = src.read_bytes()
        # Muteer vooral de kop en het eind (daar staan de SeekHead, de Cues en de moov)
        cut = rng.choice([4096, len(data) - 65536])
        head, tail = data[:cut], data[cut:]
        mutated = mutate(rng, head) + tail if rng.random() < 0.5 else head + mutate(rng, tail)
        path.write_bytes(mutated)
        fd = os.open(path, os.O_RDONLY)
        try:
            found = C.read(fd, demuxer, 0)
            assert found is None or isinstance(found, C.Index)
        finally:
            os.close(fd)


# --- de opdracht keyframes via de echte worker ---------------------------------------------------

KEY = "de-sleutel-van-de-keyframes-" * 2
CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}


@pytest.mark.media
@pytest.mark.anyio
async def test_de_opdracht_eigen_lezer_en_terugval(tmp_path):
    config = Config(socket=tmp_path / "w.sock", max_procs=2, max_transcodes=1, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:

        async def ask(name: str, fallback: bool) -> dict:
            path = F.make(name)
            demuxer, video = demuxer_and_video(path)
            args = A.KeyframesArgs(demuxer=demuxer, video_index=video, fallback=fallback)
            job, _ = await client.call(Op.KEYFRAMES, args, source=path)
            async with job:
                return await job.result()

        own = await ask("stream_src.mkv", False)
        back = await ask("stream_src.mkv", True)
        assert own["state"] == back["state"] == "ready"
        assert own["source"] == "cues" and back["source"] == "packets"
        assert C.decode_b64(own["keyframes"])[0] == C.decode_b64(back["keyframes"])[0]
        assert own["max_gop_ms"] == 2021 and own["count"] == 10
        # Zonder Cues: de eigen lezer vindt niets, de terugval wel
        none = await ask("no_cues.mkv", False)
        assert none["state"] == "none" and none["keyframes"] is None
        found = await ask("no_cues.mkv", True)
        assert found["state"] == "ready" and found["count"] >= 1
        # Lange GOP's: Cues met een gat van 25 s zijn sparse; de terugval geeft ready
        assert (await ask("long_gop.mkv", False))["state"] == "sparse"
        long = await ask("long_gop.mkv", True)
        assert long["state"] == "ready" and long["max_gop_ms"] == 25000
        # MPEG-TS heeft geen eigen lezer
        assert (await ask("interlaced.ts", False))["state"] == "none"
        ts = await ask("interlaced.ts", True)
        assert ts["state"] == "ready" and ts["count"] >= 4
    finally:
        await w.stop()
        client.bind(None)
        await asyncio.sleep(0)
