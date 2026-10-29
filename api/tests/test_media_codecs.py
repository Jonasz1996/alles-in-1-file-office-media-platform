"""De codec-strings (Keuze 16, MED-7): een tabel van bekende avcC, hvcC, av1C, vpcC en dvcC naar
strings, Annex-B (H.264 uit a.ts en HEVC uit a.m2ts, met emulatiebytes), de terugval op wat
ffprobe weet, en een fuzz met een vaste seed (regel 10).

Met marker media: de strings van alle fixtures via de echte lokale worker.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from app.config import Settings
from app.media import client, codecs, local
from app.media.types import codec_string_ok
from tests.media import fixtures as F

# Echte extradata van de fixtures (ffprobe -show_data), ingekort waar dat kan
A_TS = bytes.fromhex(
    "0000016764001eacd940a02ff97011000003000100000300300f162d960000000168ef8fcb"
)  # H.264 Annex-B: SPS en PPS
A_M2TS = bytes.fromhex(
    "00000140010c01ffff01600000030090000003000003003f9594090000000142010101600000030090000003"
    "000003003fa005020171f2e595952930bc05a02000000300200000030301000000014401"
)  # HEVC Annex-B: VPS, SPS en het begin van de PPS; de PTL heeft 00 00 03
HVCC_MAIN10 = bytes.fromhex("0102200000009000000000005df000fcfdfafa00000f04")
AVCC_HIGH = bytes.fromhex("0164001fffe1001a6764001facd9405005bb011000000300100000030300f18319")
AVCC_HI10P = bytes.fromhex("016e001effe1001b676e001ea6cd940a02ff97011000000300100000030300f162")
AV1C = bytes.fromhex("81010c000a0b0000000cc4ff6700be0010")


def ptl(first: int, compat: int, constraints: bytes, level: int) -> bytes:
    return bytes([first]) + compat.to_bytes(4, "big") + constraints.ljust(6, b"\0") + bytes([level])


def dvcc(profile: int, level: int, compat: int, *, rpu=1, el=0, bl=1) -> bytes:
    bits = (profile << 17) | (level << 11) | (rpu << 10) | (el << 9) | (bl << 8) | (compat << 4)
    return b"\x01\x00" + bits.to_bytes(3, "big") + b"\0" * 19


# (stream van ffprobe, extradata, verwachte string)
TABLE: list[tuple[dict, bytes | None, str | None]] = [
    # H.264 uit de avcC
    ({"codec_name": "h264"}, AVCC_HIGH, "avc1.64001f"),
    ({"codec_name": "h264"}, AVCC_HI10P, "avc1.6e001e"),
    ({"codec_name": "h264"}, bytes.fromhex("0142e01effe1"), "avc1.42e01e"),
    ({"codec_name": "h264"}, bytes.fromhex("014d4028"), "avc1.4d4028"),
    ({"codec_name": "h264"}, bytes.fromhex("01640033"), "avc1.640033"),
    # H.264 Annex-B (MPEG-TS): de eerste SPS, ook met een startcode van 3 bytes
    ({"codec_name": "h264"}, A_TS, "avc1.64001e"),
    ({"codec_name": "h264"}, b"\x00\x00\x01\x67\x4d\x40\x1f\xec", "avc1.4d401f"),
    (
        {"codec_name": "h264"},
        b"\x00\x00\x01\x68\xee\x3c\x80" + b"\x00\x00\x01\x67\x64\x00\x29",
        "avc1.640029",
    ),
    # HEVC uit de hvcC
    ({"codec_name": "hevc"}, HVCC_MAIN10, "hvc1.2.4.L93.90"),
    (
        {"codec_name": "hevc"},
        b"\x01" + ptl(0x01, 0x60000000, b"\xb0", 120) + b"\0" * 10,
        "hvc1.1.6.L120.B0",
    ),
    (
        {"codec_name": "hevc"},
        b"\x01" + ptl(0x22, 0x20000000, b"\x90", 153) + b"\0" * 10,
        "hvc1.2.4.H153.90",
    ),
    (
        {"codec_name": "hevc"},
        b"\x01" + ptl(0x41, 0x60000000, b"\x90\x00\x00\x00\x00\x01", 93),
        "hvc1.A1.6.L93.90.0.0.0.0.1",
    ),
    (
        {"codec_name": "hevc"},
        b"\x01" + ptl(0x04, 0x08000000, b"", 150) + b"\0" * 10,
        "hvc1.4.10.L150",
    ),
    # HEVC Annex-B (m2ts): de PTL uit de SPS, na het weghalen van 00 00 03
    ({"codec_name": "hevc"}, A_M2TS, "hvc1.1.6.L63.90"),
    # alleen een VPS: dan daaruit
    ({"codec_name": "hevc"}, A_M2TS[:26], "hvc1.1.6.L63.90"),
    # Dolby Vision profiel 5 (ffprobe zet het DOVI-record in side_data_list)
    (
        {
            "codec_name": "hevc",
            "side_data_list": [
                {"side_data_type": "DOVI configuration record", "dv_profile": 5, "dv_level": 6}
            ],
        },
        HVCC_MAIN10,
        "dvh1.05.06",
    ),
    # profiel 8: de basislaag
    (
        {
            "codec_name": "hevc",
            "side_data_list": [
                {"side_data_type": "DOVI configuration record", "dv_profile": 8, "dv_level": 6}
            ],
        },
        HVCC_MAIN10,
        "hvc1.2.4.L93.90",
    ),
    # AV1 uit de av1C
    ({"codec_name": "av1"}, AV1C, "av01.0.01M.08"),
    ({"codec_name": "av1"}, bytes.fromhex("81080c00"), "av01.0.08M.08"),
    ({"codec_name": "av1"}, bytes.fromhex("81084c00"), "av01.0.08M.10"),
    ({"codec_name": "av1"}, bytes.fromhex("810d8c00"), "av01.0.13H.08"),
    ({"codec_name": "av1"}, bytes.fromhex("81486c00"), "av01.2.08M.12"),
    ({"codec_name": "av1"}, bytes.fromhex("81284c00"), "av01.1.08M.10"),
    # VP9 uit de vpcC en uit de kenmerken van Matroska
    ({"codec_name": "vp9"}, bytes.fromhex("01000000001f8002020200"), "vp09.00.31.08"),
    ({"codec_name": "vp9"}, bytes.fromhex("010000000228a0090910"), "vp09.02.40.10"),
    ({"codec_name": "vp9"}, bytes.fromhex("010100020129030108"), "vp09.00.41.08"),
    ({"codec_name": "vp9"}, bytes.fromhex("01010202013303010a"), "vp09.02.51.10"),
    # audio
    ({"codec_name": "aac", "profile": "LC"}, None, "mp4a.40.2"),
    ({"codec_name": "aac", "profile": "HE-AAC"}, None, "mp4a.40.5"),
    ({"codec_name": "aac", "profile": "HE-AACv2"}, None, "mp4a.40.29"),
    ({"codec_name": "aac"}, None, "mp4a.40.2"),
    ({"codec_name": "ac3"}, None, "ac-3"),
    ({"codec_name": "eac3"}, None, "ec-3"),
    ({"codec_name": "opus"}, None, "opus"),
    ({"codec_name": "flac"}, None, "flac"),
    ({"codec_name": "mp3"}, None, "mp3"),
    ({"codec_name": "vorbis"}, None, "vorbis"),
    ({"codec_name": "pcm_s16le"}, None, None),
    ({"codec_name": "truehd"}, None, None),
    ({"codec_name": "dts"}, None, None),
    # wat geen string heeft
    ({"codec_name": "mpeg4"}, None, None),
    ({"codec_name": "mpeg2video"}, None, None),
    ({}, None, None),
]


@pytest.mark.parametrize(("stream", "extradata", "want"), TABLE)
def test_bekende_extradata_geven_hun_string(stream, extradata, want):
    got = codecs.strings(stream, extradata)
    assert got == want
    if got is not None:
        assert codec_string_ok(got), got


FALLBACK: list[tuple[dict, str | None]] = [
    ({"codec_name": "h264", "profile": "High", "level": 31}, "avc1.64001f"),
    ({"codec_name": "h264", "profile": "Constrained Baseline", "level": 30}, "avc1.42e01e"),
    ({"codec_name": "h264", "profile": "High 10", "level": 30}, "avc1.6e001e"),
    ({"codec_name": "h264", "profile": "High", "level": -99}, None),
    ({"codec_name": "h264", "profile": "Onbekend", "level": 31}, None),
    ({"codec_name": "hevc", "profile": "Main", "level": 120}, "hvc1.1.6.L120.B0"),
    ({"codec_name": "hevc", "profile": "Main 10", "level": 150}, "hvc1.2.4.L150.B0"),
    ({"codec_name": "hevc", "profile": "Rext", "level": 150}, None),
    # VP9 zonder extradata (zo geeft ffmpeg 6.1 en 7.1 hem altijd): level uit grootte en fps
    (
        {
            "codec_name": "vp9",
            "profile": "Profile 0",
            "width": 640,
            "height": 360,
            "avg_frame_rate": "24/1",
        },
        "vp09.00.21.08",
    ),
    (
        {
            "codec_name": "vp9",
            "profile": "Profile 0",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "30/1",
        },
        "vp09.00.40.08",
    ),
    (
        {
            "codec_name": "vp9",
            "profile": "Profile 0",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "60/1",
        },
        "vp09.00.41.08",
    ),
    (
        {
            "codec_name": "vp9",
            "profile": "Profile 2",
            "pix_fmt": "yuv420p10le",
            "width": 3840,
            "height": 2160,
            "avg_frame_rate": "24000/1001",
        },
        "vp09.02.50.10",
    ),
    ({"codec_name": "vp9", "profile": "Profile 0"}, "vp09.00.40.08"),
    ({"codec_name": "vp9", "width": 99999, "height": 99999}, "vp09.00.62.08"),
    ({"codec_name": "av1", "profile": "Main", "level": 8}, "av01.0.08M.08"),
    ({"codec_name": "av1", "profile": "Main", "pix_fmt": "yuv420p10le"}, "av01.0.08M.10"),
    ({"codec_name": "av1", "profile": "High", "level": 13}, "av01.1.13M.08"),
]


@pytest.mark.parametrize(("stream", "want"), FALLBACK)
def test_zonder_extradata_wat_ffprobe_weet(stream, want):
    assert codecs.strings(stream, None) == want


def test_kapotte_extradata_valt_terug():
    high = {"codec_name": "h264", "profile": "High", "level": 40}
    # een avcC van een andere versie, een lege, en een Annex-B zonder SPS
    assert codecs.strings(high, b"\x02\x64\x00\x1f") == "avc1.640028"
    assert codecs.strings(high, b"") == "avc1.640028"
    assert codecs.strings(high, b"\x00\x00\x01\x68\xee") == "avc1.640028"
    # een SPS die te kort is
    assert codecs.strings(high, b"\x00\x00\x01\x67\x64") == "avc1.640028"
    main10 = {"codec_name": "hevc", "profile": "Main 10", "level": 93}
    assert codecs.strings(main10, b"\x01\x02") == "hvc1.2.4.L93.B0"
    assert codecs.strings(main10, b"\x00\x00\x01\x42\x01\x01") == "hvc1.2.4.L93.B0"
    # een profiel van 0 in de hvcC: terugval
    assert codecs.strings(main10, b"\x01" + b"\0" * 22) == "hvc1.2.4.L93.B0"
    assert codecs.strings({"codec_name": "av1"}, b"\x81\xe0\x00") == "av01.0.08M.08"
    assert codecs.strings({"codec_name": "vp9"}, b"\x01\x05") == "vp09.00.40.08"


def test_annexb_onderdelen():
    assert codecs.is_annexb(b"\x00\x00\x01\x67") and codecs.is_annexb(b"\x00\x00\x00\x01\x67")
    assert not codecs.is_annexb(b"\x01\x64\x00\x1f")
    units = codecs.nal_units(A_M2TS)
    assert [(u[0] >> 1) & 0x3F for u in units] == [32, 33, 34]
    assert codecs.unescape(b"\x00\x00\x03\x01\x00\x00\x03\x00", 8) == b"\x00\x00\x01\x00\x00\x00"
    assert codecs.unescape(b"\x12\x34\x56", 2) == b"\x12\x34"
    # hooguit MAX_NALS units
    many = b"".join(b"\x00\x00\x01\x09\xf0" for _ in range(500))
    assert len(codecs.nal_units(many)) == codecs.MAX_NALS


def test_dovi_record():
    assert codecs.dovi_record(dvcc(5, 6, 0)) == (5, 6, 0)
    assert codecs.dovi_record(dvcc(8, 9, 1)) == (8, 9, 1)
    assert codecs.dovi_record(dvcc(7, 13, 6, el=1)) == (7, 13, 6)
    assert codecs.dovi_record(b"\x01\x00") is None
    assert codecs.dovi_string(5, 6) == "dvh1.05.06"
    assert codecs.dovi_string(0, 6) is None


def test_vp9_level():
    assert codecs.vp9_level(320, 180, 30) == 11
    assert codecs.vp9_level(1280, 720, 30) == 31
    assert codecs.vp9_level(1280, 720, 60) == 40
    assert codecs.vp9_level(None, 720, 30) == 40


def test_fuzz_gooit_nooit():
    """Willekeurige bytes van een gebruiker: nooit een uitzondering, en een string die er
    uitkomt, past op het patroon van Keuze 20 (of is None)."""
    rng = random.Random(0x16)
    kinds = ["h264", "hevc", "av1", "vp9", "aac", "opus", "mpeg4", "", "flac"]
    prefixes = [b"", b"\x01", b"\x81", b"\x00\x00\x01", b"\x00\x00\x00\x01", b"\x01\x00\x00\x00"]
    for _ in range(4000):
        stream = {
            "codec_name": rng.choice(kinds),
            "profile": rng.choice(["High", "Main 10", "Profile 2", "LC", None, 7, "x" * 300]),
            "level": rng.choice([None, -99, 0, 31, 255, 10**20, "abc", 3.5]),
            "width": rng.choice([None, 0, -1, 640, 10**9, "w"]),
            "height": rng.choice([None, 0, 360, 10**9]),
            "avg_frame_rate": rng.choice(["0/0", "24/1", "1/0", "x/y", None, "inf"]),
            "pix_fmt": rng.choice([None, "yuv420p10le", "yuv420p12le", 5]),
        }
        if rng.random() < 0.2:
            stream["side_data_list"] = [
                {
                    "side_data_type": "DOVI configuration record",
                    "dv_profile": rng.choice([5, 8, "x", None, 10**30]),
                    "dv_level": rng.choice([0, 6, -1, None]),
                }
            ]
        size = rng.choice([0, 1, 3, 5, 13, 23, 64, 400])
        data = rng.choice(prefixes) + rng.randbytes(size)
        got = codecs.strings(stream, data)
        assert got is None or codec_string_ok(got), (stream, data.hex(), got)
    assert codecs.strings("geen dict", b"\x01") is None  # type: ignore[arg-type]
    assert codecs.strings({"codec_name": "h264"}, b"\x00\x00\x01" * 100_000) is None


# --- de echte lokale worker ------------------------------------------------------------------

# De string van V en van elk audiospoor, zoals de worker hem nu maakt (M3)
WANT = {
    "h264_aac.mp4": ("avc1.64001f", "mp4a.40.2"),
    "h264_aac_moov_end.mp4": ("avc1.64001f", "mp4a.40.2"),
    "vp9_opus.webm": ("vp09.00.21.08", "opus"),
    "vp9_opus.mkv": ("vp09.00.21.08", "opus", "opus"),
    "av1_opus.mp4": ("av01.0.01M.08", "opus"),
    "hevc10_hdr10.mkv": ("hvc1.2.4.L93.90", "ac-3", "mp4a.40.2"),
    "h264_ac3_51.mkv": ("avc1.64001e", "ac-3"),
    "interlaced.ts": ("avc1.64001e", "mp4a.40.2"),
    "multi_audio.mkv": ("avc1.64001e", "mp4a.40.2", "mp4a.40.2"),
    "noaudio.mp4": ("avc1.64001e",),
    "h264_hi10p.mkv": ("avc1.6e001e",),
    "a.ts": ("avc1.64001e", "mp4a.40.2"),
    "a.m2ts": ("hvc1.1.6.L63.90", "mp4a.40.2"),
    "hevc2160_hdr10.mkv": ("hvc1.2.4.L150.90",),
    "pgs.mkv": ("avc1.64001f",),
    "album/flac/01.flac": ("flac",),
    "album/mp3/02.mp3": ("mp3",),
    "album/opus/03.opus": ("opus",),
    "album/aac/01.m4a": ("mp4a.40.2",),
}


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def worker(tmp_path_factory):
    for name in WANT:
        F.make(name.partition("/")[0])
    base = tmp_path_factory.mktemp("codecs-worker")
    settings = Settings(
        data_dir=base, web_dist=base / "web", jwt_secret="t" * 32, media_max_procs=3
    )
    w = await local.start(settings, base / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        yield w
    finally:
        await local.stop(w)
        client.bind(None)


def fixture_path(name: str) -> Path:
    first, _, rest = name.partition("/")
    base = F.make(first)
    return base / rest if rest else base


@pytest.mark.media
@pytest.mark.anyio
@pytest.mark.parametrize("name", list(WANT))
async def test_de_strings_van_elke_fixture(worker, name):
    probe = await client.probe(fixture_path(name))
    video = probe.video()
    got = ((video.codec_string,) if video else ()) + tuple(
        s.codec_string for s in probe.audio_streams()
    )
    assert got == WANT[name]
