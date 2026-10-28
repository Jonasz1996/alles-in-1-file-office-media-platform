"""De probe (bouwontwerp fase 5, Keuze 15 en 16; §3.8).

- Zonder ffmpeg: sniff (de demuxer uit de eerste bytes), id3_size, hexdump, clean (de grenzen,
  too_large, mediatijd, rare waarden) en fit (past altijd in MAX_MESSAGE).
- Met marker media, via de echte lokale worker: elke fixture van §3.6, de nette fouten
  (afgekapt, willekeurig, leeg, losse ondertitels) en een fuzz met een vaste seed. Na elkaar,
  één ffprobe tegelijk; daarna draait er niets meer onder de worker."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.config import Settings
from app.media import client, local, wire
from app.media.client import WorkerFailed
from app.media.types import Probe, SegmentGrid
from app.media.wire import Code
from app.media.worker.ops import probe as P
from tests.media import fixtures as F

# --- sniff -----------------------------------------------------------------------------------


def ftyp(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x02\x00" + brand + b"isom"


def ebml(doctype: bytes) -> bytes:
    body = b"\x42\x86\x81\x01" + b"\x42\x82" + bytes([0x80 | len(doctype)]) + doctype
    return b"\x1aE\xdf\xa3" + bytes([0x80 | len(body)]) + body


def ts(packet: int, offset: int = 0) -> bytes:
    out = bytearray()
    for _ in range(4):
        out += b"\x00" * offset + b"\x47" + b"\x00" * (packet - offset - 1)
    return bytes(out)


@pytest.mark.parametrize(
    "head,want",
    [
        (ftyp(b"isom"), ("mov", "mp4")),
        (ftyp(b"qt  "), ("mov", "mov")),
        (ftyp(b"M4A "), ("mov", "m4a")),
        (ftyp(b"M4V "), ("mov", "m4v")),
        (ftyp(b"3gp5"), ("mov", "3gp")),
        (b"\x00\x00\x00\x08moov" + b"\x00" * 8, ("mov", "mov")),
        (ebml(b"webm"), ("matroska", "webm")),
        (ebml(b"matroska"), ("matroska", "matroska")),
        (b"RIFF\x00\x00\x00\x00AVI LIST", ("avi", "avi")),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", ("wav", "wav")),
        (b"FLV\x01\x05", ("flv", "flv")),
        (b"OggS\x00\x02", ("ogg", "ogg")),
        (bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c") + b"\0" * 8, ("asf", "asf")),
        (ts(188), ("mpegts", "mpegts")),
        (ts(192, 4), ("mpegts", "m2ts")),
        (b"\x00\x00\x01\xba\x44", ("mpeg", "mpeg")),
        (b"fLaC\x00\x00\x00\x22", ("flac", "flac")),
        (b"\xff\xf1\x50\x80", ("aac", "aac")),
        (b"\xff\xfb\x90\x64", ("mp3", "mp3")),
    ],
)
def test_sniff(head, want):
    assert P.sniff(head) == want


@pytest.mark.parametrize(
    "head", [b"", b"1\r\n00:00:01,000 --> ", b"[Script Info]", b"\x00" * 64, b"<?xml version"]
)
def test_sniff_onbekend(head):
    assert P.sniff(head) is None


def test_id3_en_daarna_de_audio():
    tag = b"ID3\x04\x00\x00" + bytes([0, 0, 0x01, 0x7F]) + b"\0" * 255
    assert P.id3_size(tag) == 10 + 255
    assert P.sniff(tag, b"\xff\xfb\x90\x64") == ("mp3", "mp3")
    assert P.sniff(tag, b"fLaC") == ("flac", "flac")
    assert P.sniff(tag, None) is None
    with_footer = b"ID3\x04\x00\x10" + bytes([0, 0, 0, 10])
    assert P.id3_size(with_footer) == 10 + 10 + 10
    assert P.id3_size(b"ID3\x04\x00\x00\x80\x00\x00\x00") is None  # geen syncsafe getal
    assert P.id3_size(b"geen tag") is None


def test_hexdump():
    text = "\n00000000: 0164 001f ffe1 0019 6764 001f acd9 4050  .d......gd....@P\n"
    text += "00000010: 05bb 0110                                ....\n"
    assert P.hexdump(text) == bytes.fromhex("0164001fffe100196764001facd9405005bb0110")
    assert P.hexdump(None) is None
    assert P.hexdump("geen hex") is None
    big = "\n".join(f"{i * 16:08x}: " + " ".join(["abcd"] * 8) for i in range(5000))
    assert P.hexdump(big) is None  # meer dan 64 KB extradata


def test_argv_van_ffprobe():
    """Alleen fd: en de witte lijst, nooit raden, en de grenzen van spike punt 6."""
    argv = P._argv("matroska", "-show_format")
    assert argv[0] == "ffprobe" and argv[-2:] == ["-i", "fd:"]
    assert argv[argv.index("-f") + 1] == "matroska" and argv[argv.index("-fd") + 1] == "3"
    assert argv[argv.index("-protocol_whitelist") + 1] == "fd,pipe"
    assert "lavfi" not in argv[argv.index("-format_whitelist") + 1].split(",")
    assert argv[argv.index("-threads") + 1] == "4"
    assert int(argv[argv.index("-max_pixels") + 1]) == P.MAX_PIXELS


# --- clean en fit ----------------------------------------------------------------------------


def raw_video(index: int = 0, **over) -> dict:
    base = {
        "index": index,
        "codec_type": "video",
        "codec_name": "h264",
        "profile": "High",
        "level": 40,
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
        "avg_frame_rate": "24000/1001",
        "r_frame_rate": "24000/1001",
        "start_time": "1.400000",
        "duration": "60.0",
        "field_order": "progressive",
        "disposition": {"default": 1},
    }
    base.update(over)
    return base


def raw_audio(index: int, **over) -> dict:
    base = {
        "index": index,
        "codec_type": "audio",
        "codec_name": "aac",
        "profile": "LC",
        "channels": 2,
        "channel_layout": "stereo",
        "sample_rate": "48000",
        "start_time": "1.400000",
        "tags": {"language": "nld", "title": "Nederlands"},
    }
    base.update(over)
    return base


def raw(streams: list[dict], **fmt) -> dict:
    f = {"start_time": "1.400000", "duration": "60.000", "bit_rate": "4000000"}
    f.update(fmt)
    return {"format": f, "streams": streams, "chapters": []}


def test_clean_in_mediatijd():
    data = raw([raw_video(), raw_audio(1, start_time="1.421")])
    data["chapters"] = [{"start_time": "1.4", "end_time": "11.4", "tags": {"title": "Een"}}]
    out = Probe.model_validate(P.clean(data, "mpegts", last_frame_s={0: 61.358}))
    assert out.start_ms == 1400
    video = out.video()
    assert video is not None
    assert video.start_ms == 0 and video.last_frame_ms == 59958
    assert video.fps == 23.976 and video.bit_depth == 8 and video.codec_string == "avc1.640028"
    assert out.audio_streams()[0].start_ms == 21
    assert out.audio_streams()[0].lang == "nld" and out.audio_streams()[0].title == "Nederlands"
    assert out.chapters[0].start_ms == 0 and out.chapters[0].end_ms == 10000
    assert out.state == "ready" and out.error is None


def test_clean_too_large_maar_niet_voor_een_hoes():
    out = P.clean(raw([raw_video(width=8192, height=4321)]), "matroska")
    assert out["state"] == "failed" and out["error"] == "too_large"
    out = P.clean(raw([raw_video(width=8192, height=4320)]), "matroska")
    assert out["state"] == "ready"
    cover = raw_video(1, codec_name="mjpeg", width=10000, height=10000)
    cover["disposition"] = {"attached_pic": 1}
    out = P.clean(raw([raw_audio(0), cover]), "mp3")
    assert out["state"] == "ready"


def test_clean_grenzen_op_streams_hoofdstukken_en_bijlagen():
    streams = [raw_video(0)] + [raw_audio(i) for i in range(1, 80)]
    streams += [{"index": 80 + i, "codec_type": "attachment"} for i in range(3)]
    data = raw(streams)
    data["chapters"] = [
        {"start_time": str(i), "end_time": str(i + 1), "tags": {"title": f"H{i}"}}
        for i in range(100)
    ]
    out = P.clean(data, "matroska")
    assert len(out["streams"]) == P.MAX_STREAMS
    assert len(out["chapters"]) == P.MAX_CHAPTERS
    assert out["truncated"] == {"attachments": 3, "streams": 80 - 64, "chapters": 100 - 64}
    assert all(s["type"] != "attachment" for s in out["streams"])


def test_clean_rare_waarden():
    video = raw_video(
        width="N/A",
        height=None,
        avg_frame_rate="0/0",
        r_frame_rate="25/1",
        duration="nan",
        level=-99,
        start_time="inf",
        tags={"TITLE": "a\x00b" + "x" * 300, "": "leeg"},
    )
    out = Probe.model_validate(P.clean({"format": {"duration": "N/A"}, "streams": [video]}, None))
    v = out.video()
    assert v is not None
    assert v.width is None and v.height is None and v.fps == 25.0
    assert v.duration_ms is None and v.level is None and v.start_ms is None
    assert v.title is not None and "\x00" not in v.title and len(v.title) == P.TEXT
    assert out.duration_ms is None and out.start_ms == 0
    # Geen dict of lijst waar die horen: geen fout, gewoon leeg
    assert P.clean({"format": "x", "streams": "y", "chapters": None}, None)["streams"] == []


def test_clean_hdr_en_interlaced():
    pq = raw_video(color_transfer="smpte2084", pix_fmt="yuv420p10le", codec_name="hevc",
                   profile="Main 10", level=120)  # fmt: skip
    frames = {0: [{"side_data_list": [
        {"side_data_type": "Mastering display metadata", "max_luminance": "10000000/10000"},
        {"side_data_type": "Content light level metadata", "max_content": 1000, "max_average": 400},
    ]}]}  # fmt: skip
    out = Probe.model_validate(P.clean(raw([pq]), "matroska", frames=frames))
    v = out.video()
    assert v is not None and v.hdr == "hdr10" and v.bit_depth == 10
    assert v.codec_string == "hvc1.2.4.L120.B0"
    assert v.hdr_meta == {
        "mastering": {"max_luminance": "10000000/10000"},
        "cll": {"max_content": 1000, "max_average": 400},
    }
    hlg = P.clean(raw([raw_video(color_transfer="arib-std-b67")]), "matroska")
    assert hlg["streams"][0]["hdr"] == "hlg"
    dovi = {"side_data_type": "DOVI configuration record", "dv_profile": 5, "dv_level": 6}
    dv5 = raw_video(codec_name="hevc", side_data_list=[dovi])
    out5 = P.clean(raw([dv5]), "mp4")["streams"][0]
    assert out5["hdr"] == "dv5" and out5["dv_profile"] == 5
    assert out5["codec_string"] == "dvh1.05.06"
    tt = P.clean(raw([raw_video(field_order="tt")]), "mpegts")
    assert tt["streams"][0]["interlaced"] is True


def test_clean_tags_alleen_bij_ogg_van_de_stream():
    audio = raw_audio(0, tags={"TITLE": "Nummer 1", "ARTIST": "Fase Vijf", "language": "eng"})
    ogg = P.clean(raw([audio]), "ogg")
    assert ogg["tags"]["title"] == "Nummer 1" and ogg["tags"]["artist"] == "Fase Vijf"
    mkv = P.clean(raw([audio], tags={"ENCODER": "Lavf"}), "matroska")
    assert mkv["tags"] == {"encoder": "Lavf"}
    many = {f"k{i}": "v" * 900 for i in range(200)}
    tags = P.clean(raw([], tags=many), "mp4")["tags"]
    assert len(tags) == P.MAX_TAGS and all(len(v) == P.TAG_TEXT for v in tags.values())


def test_fit_past_altijd():
    streams = [raw_audio(i, tags={"title": "t" * 200, "language": "nld"}) for i in range(64)]
    data = raw(streams, tags={f"k{i}": "v" * 500 for i in range(48)})
    data["chapters"] = [
        {"start_time": str(i), "end_time": str(i + 1), "tags": {"title": "h" * 200}}
        for i in range(64)
    ]
    cleaned = P.clean(data, "matroska")
    big = len(json.dumps({"ev": "result", "data": cleaned}))
    budget = 16 * 1024
    assert big > budget
    out = P.fit(cleaned, budget)
    assert len(json.dumps({"ev": "result", "data": out}, separators=(",", ":"))) <= budget
    assert out["truncated"].get("chapters", 0) > 0 or out["truncated"].get("streams", 0) > 0
    Probe.model_validate(out)
    # Het echte budget: altijd onder MAX_MESSAGE
    full = P.fit(P.clean(data, "matroska"))
    assert len(wire.encode({"ev": "result", "data": full})) <= wire.MAX_MESSAGE


# --- de echte lokale worker ------------------------------------------------------------------


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def built() -> dict[str, Path]:
    """Alle fixtures (zonder loadtest), na elkaar, vóór de worker start."""
    return F.make_all()


@pytest.fixture(scope="module")
async def worker(tmp_path_factory, built):
    base = tmp_path_factory.mktemp("probe-worker")
    settings = Settings(
        data_dir=base, web_dist=base / "web", jwt_secret="t" * 32, media_max_procs=3
    )
    w = await local.start(settings, base / "run" / "worker.sock")
    client.bind(w.socket)
    try:
        yield w
    finally:
        await local.stop(w)
        client.bind(None)


def path(name: str) -> Path:
    first, _, rest = name.partition("/")
    base = F.make(first)
    return base / rest if rest else base


@dataclass
class Want:
    container: str
    video: str | None = None  # de codec van V
    size: tuple[int, int] | None = None
    codec_string: str | None = None  # het begin van de codec-string van V
    audio: tuple[str, ...] = ()
    subtitles: tuple[str, ...] = ()
    duration_ms: int | None = None  # ±60 ms
    fps: float | None = None
    hdr: str = "none"
    bit_depth: int | None = None
    interlaced: bool = False
    chapters: tuple[str, ...] = ()
    langs: tuple[str | None, ...] = ()  # van de audiosporen
    tags: dict[str, str] = field(default_factory=dict)
    cover: bool = False
    start_ms: int | None = None  # ±60 ms


WANT: dict[str, Want] = {
    "h264_aac.mp4": Want("mp4", "h264", (1280, 720), "avc1.6400", ("aac",), duration_ms=10000,
                         fps=24),
    "h264_aac_moov_end.mp4": Want("mp4", "h264", (1280, 720), "avc1.6400", ("aac",),
                                  duration_ms=10000),
    "vp9_opus.webm": Want("webm", "vp9", (640, 360), "vp09.00.", ("opus",), duration_ms=6000),
    "vp9_opus.mkv": Want("matroska", "vp9", (640, 360), "vp09.00.", ("opus", "opus"),
                         langs=("nld", "eng")),
    "av1_opus.mp4": Want("mp4", "av1", (640, 360), "av01.0.", ("opus",), duration_ms=4000),
    "hevc10_hdr10.mkv": Want("matroska", "hevc", (1280, 720), "hvc1.2.4.", ("ac3", "aac"),
                             ("subrip", "ass"), hdr="hdr10", bit_depth=10,
                             chapters=("Begin", "Midden", "Einde"), langs=("eng", "nld")),
    "h264_ac3_51.mkv": Want("matroska", "h264", (640, 360), "avc1.", ("ac3",)),
    "xvid_mp3.avi": Want("avi", "mpeg4", (640, 360), None, ("mp3",), fps=25),
    "interlaced.ts": Want("mpegts", "h264", (720, 576), "avc1.", ("aac",), interlaced=True,
                          start_ms=1460),
    "fps23976.mkv": Want("matroska", "h264", (640, 360), "avc1.", ("aac",), fps=23.976),
    "late_video.mkv": Want("matroska", "h264", (320, 240), "avc1.", ("aac",), fps=24),
    "stream_src.mkv": Want("matroska", "h264", (640, 360), "avc1.", ("aac",), duration_ms=20000,
                           fps=24),
    "multi_audio.mkv": Want("matroska", "h264", (640, 360), "avc1.", ("aac", "aac"),
                            ("subrip",), langs=("nld", "eng")),
    "subs_text.mkv": Want("matroska", "h264", (320, 180), "avc1.", (), ("subrip", "ass")),
    "pgs.mkv": Want("matroska", "h264", (1280, 720), "avc1.", (), ("hdmv_pgs_subtitle",)),
    "vobsub.mkv": Want("matroska", "h264", (1280, 720), "avc1.", (), ("dvd_subtitle",)),
    "fps240.mp4": Want("mp4", "h264", (640, 360), "avc1.", (), fps=240),
    "noaudio.mp4": Want("mp4", "h264", (640, 360), "avc1.", ()),
    "h264_hi10p.mkv": Want("matroska", "h264", (640, 360), "avc1.6e", (), bit_depth=10),
    "a.ts": Want("mpegts", "h264", (640, 360), "avc1.", ("aac",)),
    "a.m2ts": Want("m2ts", "hevc", (640, 360), "hvc1.1.6.", ("aac",)),
    "hevc2160_hdr10.mkv": Want("matroska", "hevc", (3840, 2160), "hvc1.2.4.", (), hdr="hdr10",
                               bit_depth=10, duration_ms=2000),
    "long_gop.mkv": Want("matroska", "h264", (320, 180), "avc1.", (), duration_ms=60000),
    "irregular_gop.mkv": Want("matroska", "h264", (320, 180), "avc1.", (), duration_ms=20000),
    "no_cues.mkv": Want("matroska", "h264", (320, 180), "avc1.", ("aac",)),
    "chapters.mkv": Want("matroska", "h264", (320, 180), "avc1.", ("aac",),
                         chapters=("Intro", "Deel 1", "End Credits"), duration_ms=30000),
    "album/flac/01.flac": Want("flac", None, None, None, ("flac",), cover=True,
                               tags={"title": "Nummer 1", "album": "Sinus (flac)"}),
    "album/mp3/02.mp3": Want("mp3", None, None, None, ("mp3",), cover=True,
                             tags={"title": "Nummer 2", "artist": "Fase Vijf"}),
    "album/opus/03.opus": Want("ogg", None, None, None, ("opus",),
                               tags={"title": "Nummer 3", "track": "3/3"}),
    "album/aac/01.m4a": Want("m4a", None, None, None, ("aac",), duration_ms=4000,
                             tags={"title": "Nummer 1"}),
}  # fmt: skip


def test_elke_fixture_staat_in_de_tabel():
    """Elke fixture van §3.6 wordt geprobed: in WANT, of een nette fout, of zwaar."""
    covered = {n.partition("/")[0] for n in WANT} | F.UNREADABLE | F.LOOSE_SUBTITLES | F.HEAVY
    covered |= F.WORKER_OUTPUT  # de uitvoer van stream, geen bron
    assert covered == set(F.NAMES)


def near(value: int | None, want: int, slack: int = 60) -> bool:
    return value is not None and abs(value - want) <= slack


@pytest.mark.media
@pytest.mark.anyio
@pytest.mark.parametrize("name", list(WANT))
async def test_probe_van_elke_fixture(worker, name):
    want = WANT[name]
    started = time.monotonic()
    probe = await client.probe(path(name))
    assert time.monotonic() - started < 5
    assert probe.state == "ready" and probe.error is None
    assert probe.container == want.container
    video = probe.video()
    if want.video is None:
        assert video is None
    else:
        assert video is not None and video.codec == want.video
        assert (video.width, video.height) == want.size
        if want.codec_string:
            assert video.codec_string and video.codec_string.startswith(want.codec_string), (
                video.codec_string
            )
        assert video.hdr == want.hdr
        assert video.interlaced is want.interlaced
        if want.fps is not None:
            assert video.fps == pytest.approx(want.fps, abs=0.001)
        if want.bit_depth is not None:
            assert video.bit_depth == want.bit_depth
        if want.hdr == "hdr10":
            assert video.hdr_meta and "mastering" in video.hdr_meta and "cll" in video.hdr_meta
        if probe.duration_ms:
            # het laatste beeld ligt binnen de duur, in mediatijd (spike punt 10)
            assert video.last_frame_ms is not None
            assert 0 < video.last_frame_ms <= probe.duration_ms
    assert tuple(s.codec for s in probe.audio_streams()) == want.audio
    assert all(s.codec_string for s in probe.audio_streams())
    assert tuple(s.codec for s in probe.subtitles()) == want.subtitles
    if want.langs:
        assert tuple(s.lang for s in probe.audio_streams()) == want.langs
    if want.duration_ms is not None:
        assert near(probe.duration_ms, want.duration_ms)
    if want.start_ms is not None:
        assert near(probe.start_ms, want.start_ms)
    assert tuple(c.title for c in probe.chapters) == want.chapters
    for key, value in want.tags.items():
        assert probe.tags.get(key) == value
    assert any(s.attached_pic for s in probe.streams) is want.cover
    assert probe.truncated == {}


@pytest.mark.media
@pytest.mark.anyio
async def test_probe_details(worker):
    multi = await client.probe(path("multi_audio.mkv"))
    a0, a1 = multi.audio_streams()
    assert a0.default and not a1.default
    assert multi.subtitles()[0].forced and multi.subtitles()[0].lang == "nld"
    surround = await client.probe(path("h264_ac3_51.mkv"))
    assert surround.audio_streams()[0].channels == 6
    assert surround.audio_streams()[0].codec_string == "ac-3"
    credits = await client.probe(path("chapters.mkv"))
    last = credits.chapters[-1]
    assert last.title == "End Credits" and near(last.end_ms, 30000)
    gapless = await client.probe(path("album/aac/01.m4a"))
    assert "itunsmpb" in gapless.tags
    plain = await client.probe(path("album/aac/03.m4a"))
    assert "itunsmpb" not in plain.tags
    # mediatijd: de start S van de bron eraf (spike punt 12)
    ts = await client.probe(path("a.ts"))
    v = ts.video()
    assert v is not None and v.start_ms is not None and 0 <= v.start_ms < 100
    assert ts.start_ms > 1000
    # Een late videostart: het rooster hangt aan het eerste videobeeld (Keuze 22), origin =
    # de start van het videospoor − S, hier 0,5 s + 0,021 s (spike.md, "Extra")
    late = await client.probe(path("late_video.mkv"))
    v = late.video()
    assert v is not None and v.start_ms is not None and near(v.start_ms, 521, 30)
    assert v.last_frame_ms is not None
    grid = SegmentGrid(
        origin_ms=v.start_ms,
        count=SegmentGrid.count_for(v.start_ms, v.last_frame_ms),
        last_frame_ms=v.last_frame_ms,
    )
    assert grid.count == 5 and grid.start_of(1) == v.start_ms + 2000
    cues = await client.probe(path("no_cues.mkv"))  # naar een pipe gemuxt: geen duur
    assert cues.duration_ms is None and cues.video().last_frame_ms is None


@pytest.mark.media
@pytest.mark.anyio
@pytest.mark.parametrize(
    "name,codes",
    [
        ("broken.mp4", {Code.UNREADABLE}),
        ("random.mkv", {Code.UNKNOWN_FORMAT, Code.UNREADABLE}),
        ("empty.mkv", {Code.UNREADABLE}),
        ("nl.srt", {Code.UNKNOWN_FORMAT}),
        ("en.ass", {Code.UNKNOWN_FORMAT}),
        ("film.nl.forced.srt", {Code.UNKNOWN_FORMAT}),
        ("bom.srt", {Code.UNKNOWN_FORMAT}),
    ],
)
async def test_nette_fout(worker, name, codes):
    source = path(name)
    with pytest.raises(WorkerFailed) as exc:
        await client.probe(source)
    assert exc.value.code in codes
    assert len(exc.value.detail) <= wire.DETAIL_CHARS
    assert str(source.parent) not in exc.value.detail  # ffprobe kent alleen fd:
    assert (await client.caps()).procs == 0


FUZZ_SOURCES = (
    "subs_text.mkv",
    "a.ts",
    "noaudio.mp4",
    "vp9_opus.webm",
    "xvid_mp3.avi",
    "album/flac/02.flac",
    "album/mp3/01.mp3",
    "album/aac/02.m4a",
    "album/opus/01.opus",
)
FUZZ_CASES = 36
FUZZ_SEED = 20251028


def mutate(rng: random.Random, data: bytes) -> bytes:
    b = bytearray(data)
    kind = rng.choice(("flip", "truncate", "zero", "cut", "head"))
    if kind == "flip":  # bytes in de koppen
        for _ in range(rng.randint(1, 64)):
            b[rng.randrange(min(len(b), 65536))] = rng.randrange(256)
    elif kind == "truncate":
        del b[rng.randrange(1, len(b)) :]
    elif kind == "zero":
        at = rng.randrange(len(b))
        n = min(rng.randint(1, 8192), len(b) - at)
        b[at : at + n] = bytes(n)
    elif kind == "cut":
        i, j = sorted(rng.randrange(len(b)) for _ in range(2))
        del b[i:j]
    else:
        del b[rng.randint(4, 4096) :]
    return bytes(b) or b"\x00"


@pytest.mark.media
@pytest.mark.anyio
async def test_fuzz_met_vaste_seed(worker, tmp_path):
    """Kapotte varianten van echte bestanden: een probe of een nette fout, nooit internal,
    nooit een vastgelopen of verdwenen worker, en geen proces over."""
    rng = random.Random(FUZZ_SEED)
    sources = [path(n).read_bytes() for n in FUZZ_SOURCES]
    outcome: dict[str, int] = {}
    started = time.monotonic()
    for i in range(FUZZ_CASES):
        data = mutate(rng, sources[i % len(sources)])
        target = tmp_path / f"fuzz{i:02d}.bin"
        target.write_bytes(data)
        try:
            probe = await client.probe(target, limit_s=90)
        except WorkerFailed as exc:
            assert exc.code in {Code.UNREADABLE, Code.UNKNOWN_FORMAT, Code.TIMEOUT}, (
                i,
                exc.code,
                exc.detail[:200],
            )
            key = exc.code
        else:
            assert probe.state in ("ready", "failed")
            assert len(probe.streams) <= 64 and len(probe.chapters) <= 64
            key = f"probe:{probe.state}"
        outcome[key] = outcome.get(key, 0) + 1
        target.unlink()
    assert time.monotonic() - started < 120
    assert sum(outcome.values()) == FUZZ_CASES
    caps = await client.caps()
    assert caps.procs == 0
    assert not children(worker.pid)


def children(ppid: int) -> list[int]:
    """De levende kinderen van ppid (uit /proc/<pid>/stat)."""
    found = []
    for name in filter(str.isdigit, os.listdir("/proc")):
        try:
            text = Path(f"/proc/{name}/stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            continue
        state, parent = text[text.rindex(")") + 2 :].split()[:2]
        if int(parent) == ppid and state != "Z":
            found.append(int(name))
    return found
