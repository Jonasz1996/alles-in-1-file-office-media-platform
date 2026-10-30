"""De gapless-gegevens (M9, Keuze 18): de eigen lezers van audio_info en de opdracht remux_audio.

- Zonder marker: kleine bestanden die de test zelf bouwt (FLAC STREAMINFO, MP3 met een Xing- of
  Info-kop en een LAME- of Lavc-tag, Ogg-pagina's met de CRC van Ogg voor Opus en Vorbis, WAV en
  MP4-dozen met iTunSMPB en elst), de randgevallen, wat er gelezen wordt (alleen de kop en de
  laatste pagina van Ogg), en fuzz met een vast zaad per lezer: een lezer geeft de vijf velden
  binnen hun grenzen of {skip}, nooit een uitzondering, en leest hooguit READ_MAX bytes.
- De argv van remux_audio tegen §3.3.
- Met marker media: bestanden van ffmpeg uit één sinus (FLAC en MP3 met 882.000 samples, Opus
  met pre-skip, AAC met en zonder iTunSMPB, WAV en Vorbis) en de fixture album/, en de opdrachten
  via de echte worker: het aantal samples na remux_audio, gedecodeerd, klopt op het sample.
  (spike S8) MP3: delay = enc_delay, zonder +529; Opus: samples = laatste granule − pre_skip, en
  delay 0 na FLAC.
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
import struct
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.media import client
from app.media.types import Stream
from app.media.wire import Op
from app.media.worker import args as A
from app.media.worker.ops import audio_info as AI
from app.media.worker.ops import remux_audio as RA
from app.media.worker.server import Config, Worker
from app.routers import stream_audio
from tests.media import fixtures as F

FIELDS = {"rate", "samples", "delay", "padding", "source"}


def memfd(data: bytes) -> int:
    fd = os.memfd_create("audio", os.MFD_CLOEXEC)
    os.write(fd, data)
    return fd


def info(data: bytes, demuxer: str | None, index: int = 0) -> dict:
    fd = memfd(data)
    try:
        return AI.read(fd, demuxer, index)
    finally:
        os.close(fd)


def want(rate: int, samples: int, delay: int, padding: int, source: str) -> dict:
    return {"rate": rate, "samples": samples, "delay": delay, "padding": padding, "source": source}


# --- bouwen: ID3, FLAC, MP3, WAV ------------------------------------------------------------------


def id3(payload: int) -> bytes:
    """Een ID3v2.3-tag met payload bytes (syncsafe grootte)."""
    size = bytes(
        [(payload >> 21) & 0x7F, (payload >> 14) & 0x7F, (payload >> 7) & 0x7F, payload & 0x7F]
    )
    return b"ID3\x03\x00\x00" + size + b"\0" * payload


def flac_file(total: int = 882_000, rate: int = 44100, channels: int = 2, bps: int = 16) -> bytes:
    info = bytearray(34)
    struct.pack_into(">HH", info, 0, 4096, 4096)
    value = (rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total
    info[10:18] = value.to_bytes(8, "big")
    return b"fLaC" + bytes([0x80, 0, 0, 34]) + bytes(info) + b"\xff\xf8" + b"\0" * 200


def mp3_frame_header(version: int = 3, mono: bool = False, bitrate: int = 9) -> bytes:
    b2 = 0xE0 | (version << 3) | (1 << 1) | 1  # Layer III, zonder CRC
    return bytes([0xFF, b2, bitrate << 4, (3 << 6) if mono else 0])


def mp3_file(
    frames: int,
    delay: int,
    padding: int,
    *,
    encoder: bytes = b"LAME3.100",
    version: int = 3,
    mono: bool = False,
    flags: int = 0x0F,
    tag: bytes = b"Info",
    lame: bool = True,
    before: bytes = b"",
) -> bytes:
    side = (17 if mono else 32) if version == 3 else (9 if mono else 17)
    body = bytearray(mp3_frame_header(version, mono) + b"\0" * side)
    body += tag + struct.pack(">I", flags)
    if flags & 1:
        body += struct.pack(">I", frames)
    if flags & 2:
        body += struct.pack(">I", 123_456)
    if flags & 4:
        body += bytes(range(100))
    if flags & 8:
        body += struct.pack(">I", 57)
    if lame:
        ext = bytearray(36)
        ext[:9] = encoder.ljust(9, b" ")[:9]
        ext[21:24] = ((delay << 12) | padding).to_bytes(3, "big")
        body += ext
    body += b"\0" * (417 - len(body))
    audio = (mp3_frame_header(version, mono) + b"\x55" * 413) * 3
    return before + bytes(body) + audio


def wav_file(samples: int, rate: int = 44100, channels: int = 2, claim: int | None = None) -> bytes:
    block = 2 * channels
    data = b"\0" * (samples * block)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, 16)
    extra = b"LIST" + struct.pack("<I", 5) + b"INFO!" + b"\0"  # een oneven chunk met opvulling
    size = len(data) if claim is None else claim
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + extra
    body += b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


# --- bouwen: Ogg ----------------------------------------------------------------------------------


def ogg_page(granule: int, serial: int, seq: int, packets: list[bytes], flags: int = 0) -> bytes:
    lacing: list[int] = []
    for packet in packets:
        lacing += [255] * (len(packet) // 255) + [len(packet) % 255]
    head = b"OggS" + bytes([0, flags]) + struct.pack("<qIIIB", granule, serial, seq, 0, len(lacing))
    raw = head + bytes(lacing) + b"".join(packets)
    return raw[:22] + struct.pack("<I", AI.ogg_crc(raw)) + raw[26:]


def opus_head(pre_skip: int = 312, channels: int = 2) -> bytes:
    return b"OpusHead" + bytes([1, channels]) + struct.pack("<HIhB", pre_skip, 48000, 0, 0)


def vorbis_head(rate: int = 44100) -> bytes:
    return b"\x01vorbis" + struct.pack("<IBIiiiB", 0, 2, rate, 0, 128_000, 0, 0xB8) + b"\x01"


def opus_file(last: int, pre_skip: int = 312, serial: int = 0x1234, pages: int = 4) -> bytes:
    out = ogg_page(0, serial, 0, [opus_head(pre_skip)], flags=2)
    out += ogg_page(0, serial, 1, [b"OpusTags" + b"\0" * 16])
    step = last // pages
    for i in range(pages - 1):
        out += ogg_page(step * (i + 1), serial, i + 2, [bytes([0xFC]) * 200] * 5)
    return out + ogg_page(last, serial, pages + 1, [bytes([0xFC]) * 120], flags=4)


def ogg_worst(serial: int = 0x5151) -> bytes:
    """Review golf 4, S5: 16 BOS-pagina's van elk 65.306 bytes met een goede CRC (OpusHead),
    en een staartje met 64 keer "OggS" van dit spoor, met een granule, waarvan de pagina tot het
    eind loopt en de CRC niet klopt. Met de CRC per byte in Python kostte dit 0,6 s per bron."""
    body = opus_head() + b"\0" * (255 * 255 - 1 - len(opus_head()))  # 255 segmenten
    heads = b"".join(ogg_page(0, serial + i, 0, [body], flags=2) for i in range(16))
    tail = bytearray(65_000)
    for i in range(64):
        at = i * 1000
        rest = len(tail) - at - 27
        count = -(-rest // 256)
        data = rest - count
        lacing = [255] * (data // 255) + [data % 255]
        lacing += [0] * (count - len(lacing))
        head = b"OggS" + bytes([0, 0])
        head += struct.pack("<qIIIB", 960 * (i + 1), serial, i + 1, 0, count)
        tail[at : at + 27 + count] = head + bytes(lacing)
    return heads + bytes(tail)


# --- bouwen: MP4 ----------------------------------------------------------------------------------


def box(kind: bytes, *payload: bytes) -> bytes:
    data = b"".join(payload)
    return struct.pack(">I4s", 8 + len(data), kind) + data


def full(kind: bytes, version: int, *payload: bytes) -> bytes:
    return box(kind, bytes([version, 0, 0, 0]), *payload)


def mdhd(scale: int, duration: int, version: int = 0) -> bytes:
    if version == 1:
        return full(b"mdhd", 1, struct.pack(">QQIQHH", 0, 0, scale, duration, 0x55C4, 0))
    return full(b"mdhd", 0, struct.pack(">IIIIHH", 0, 0, scale, duration, 0x55C4, 0))


def hdlr(handler: bytes) -> bytes:
    return full(b"hdlr", 0, b"\0" * 4, handler, b"\0" * 12, b"\0")


def stsd(codec: bytes, rate: int) -> bytes:
    entry = box(
        codec,
        b"\0" * 6,
        struct.pack(">H", 1),
        b"\0" * 8,
        struct.pack(">HHHH", 2, 16, 0, 0),
        struct.pack(">I", rate << 16),
    )
    return full(b"stsd", 0, struct.pack(">I", 1), entry)


def elst(rows: list[tuple[int, int]], version: int = 0) -> bytes:
    fmt = ">QqHH" if version == 1 else ">IiHH"
    body = b"".join(struct.pack(fmt, seg, media, 1, 0) for seg, media in rows)
    return box(b"edts", full(b"elst", version, struct.pack(">I", len(rows)), body))


def trak(
    handler: bytes = b"soun",
    *,
    codec: bytes = b"mp4a",
    rate: int = 44100,
    scale: int = 44100,
    duration: int = 883_024,
    edits: list[tuple[int, int]] | None = None,
    elst_version: int = 0,
    mdhd_version: int = 0,
) -> bytes:
    stbl = box(b"stbl", stsd(codec, rate))
    mdia = box(b"mdia", mdhd(scale, duration, mdhd_version), hdlr(handler), box(b"minf", stbl))
    parts = [box(b"tkhd", b"\0" * 84)]
    if edits is not None:
        parts.append(elst(edits, elst_version))
    return box(b"trak", *parts, mdia)


def smpb(delay: int, padding: int, length: int) -> str:
    words = ["00000000", f"{delay:08X}", f"{padding:08X}", f"{length:016X}"]
    return " " + " ".join(words + ["00000000"] * 8)


def udta(text: str | None, full_meta: bool = True) -> bytes:
    items = []
    if text is not None:
        items.append(
            box(
                b"----",
                full(b"mean", 0, b"com.apple.iTunes"),
                full(b"name", 0, b"iTunSMPB"),
                box(b"data", struct.pack(">II", 1, 0), text.encode()),
            )
        )
    items.append(box(b"\xa9nam", box(b"data", struct.pack(">II", 1, 0), b"Nummer")))
    inner = hdlr(b"mdir") + box(b"ilst", *items)
    meta = full(b"meta", 0, inner) if full_meta else box(b"meta", inner)
    return box(b"udta", meta)


def mp4_file(*traks: bytes, movie_scale: int = 1000, extra: bytes = b"", end: bool = True) -> bytes:
    mvhd = full(b"mvhd", 0, struct.pack(">IIII", 0, 0, movie_scale, 20_000), b"\0" * 80)
    moov = box(b"moov", mvhd, *traks, extra)
    ftyp = box(b"ftyp", b"M4A ", b"\0\0\0\0", b"M4A mp42isom")
    mdat = box(b"mdat", b"\x21" * 4000)
    return ftyp + (mdat + moov if end else moov + mdat)


# --- FLAC -----------------------------------------------------------------------------------------


def test_flac_streaminfo():
    assert info(flac_file(), "flac") == want(44100, 882_000, 0, 0, "streaminfo")
    big = flac_file(total=2**35 + 7, rate=96000, channels=6, bps=24)
    assert info(big, "flac") == want(96000, 2**35 + 7, 0, 0, "streaminfo")
    # Een ID3-tag vooraan (komt voor bij FLAC); zonder demuxer proeft read zelf
    assert info(id3(3000) + flac_file(), "flac")["samples"] == 882_000
    assert info(flac_file(), None)["source"] == "streaminfo"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (flac_file(total=0), "zegt het aantal samples niet"),
        (b"fLaC" + bytes([0x84, 0, 0, 34]) + b"\0" * 34, "geen STREAMINFO"),
        (b"fLaC" + bytes([0x80, 0, 0, 33]) + b"\0" * 34, "geen STREAMINFO"),
        (b"fLaX" + b"\0" * 60, "geen fLaC"),
        (b"fLaC\x80\0\0", "geen fLaC"),
        (flac_file(rate=0), "samplefrequentie"),
    ],
)
def test_flac_skip(data, reason):
    found = info(data, "flac")
    assert set(found) == {"skip"} and reason in found["skip"]


def test_een_tweede_spoor_bestaat_niet_bij_flac_mp3_en_wav():
    for data, demuxer in ((flac_file(), "flac"), (wav_file(10), "wav")):
        assert "één audiospoor" in info(data, demuxer, 1)["skip"]


# --- MP3 ------------------------------------------------------------------------------------------


def test_mp3_lame_zonder_529():
    """(spike S8) delay = enc_delay, zonder de 529 van de decoder."""
    frames = (882_000 + 576 + 1008) // 1152
    assert frames * 1152 == 882_000 + 576 + 1008
    data = mp3_file(frames, 576, 1008)
    assert info(data, "mp3") == want(44100, 882_000, 576, 1008, "lame")
    # Met een ID3-tag ervoor en nullen tussen de tag en het eerste frame
    assert info(id3(5000) + b"\0" * 300 + data, "mp3")["samples"] == 882_000
    assert info(id3(5000) + data, None)["delay"] == 576


def test_mp3_lavc_en_xing():
    data = mp3_file(767, 576, 1008, encoder=b"Lavc60.31", tag=b"Xing")
    assert info(data, "mp3") == want(44100, 882_000, 576, 1008, "lavc")


def test_mp3_vlaggen_mono_en_mpeg2():
    # Alleen het aantal frames in de Xing-kop (geen bytes, geen TOC, geen kwaliteit)
    assert info(mp3_file(100, 576, 0, flags=1), "mp3")["samples"] == 100 * 1152 - 576
    # Mono: de Xing-kop staat 17 bytes na de kop van het frame
    assert info(mp3_file(100, 576, 24, mono=True), "mp3")["samples"] == 100 * 1152 - 600
    # MPEG-2 (22,05 kHz): 576 samples per frame, de Xing-kop na 17 bytes
    found = info(mp3_file(200, 576, 100, version=2), "mp3")
    assert found == want(22050, 200 * 576 - 676, 576, 100, "lame")
    found = info(mp3_file(200, 576, 100, version=0, mono=True), "mp3")
    assert found["rate"] == 11025 and found["samples"] == 200 * 576 - 676


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (mp3_file(100, 576, 0, tag=b"VBRI"), "geen Xing"),
        (mp3_file(100, 576, 0, flags=0x0E), "aantal frames niet"),
        (mp3_file(0, 576, 0), "aantal frames niet"),
        (mp3_file(100, 576, 0, lame=False), "geen LAME"),
        (mp3_file(100, 576, 0, encoder=b"FhG 1.0  "), "geen LAME"),
        (mp3_file(1, 2000, 2000), "aantal samples"),
        (b"\0" * 70_000 + mp3_file(100, 576, 0), "geen MP3-frame"),
        (b"\xff\xff\xff\xff" * 10, "geen MP3-frame"),
    ],
)
def test_mp3_skip(data, reason):
    found = info(data, "mp3")
    assert set(found) == {"skip"} and reason in found["skip"], found


# --- WAV ------------------------------------------------------------------------------------------


def test_wav():
    assert info(wav_file(22050), "wav") == want(44100, 22050, 0, 0, "wav")
    assert info(wav_file(1000, rate=48000, channels=1), None)["rate"] == 48000
    # Een data-chunk die meer belooft dan er is (een WAV uit een pipe): tot het eind
    assert info(wav_file(1000, claim=0xFFFFFFFF), "wav")["samples"] == 1000


@pytest.mark.parametrize(
    "data",
    [
        b"RIFF\0\0\0\0WAVE" + b"data" + struct.pack("<I", 8) + b"\0" * 8,
        b"RIFF\0\0\0\0WAVE" + b"fmt " + struct.pack("<I", 4) + b"\0" * 4,
        b"RIFF\0\0\0\0AVI " + b"\0" * 40,
        b"RIFF\0\0\0\0WAVE",
        wav_file(0),
    ],
)
def test_wav_skip(data):
    assert set(info(data, "wav")) == {"skip"}


# --- Ogg ------------------------------------------------------------------------------------------


def test_ogg_crc_van_een_lege_pagina():
    # De CRC staat zelf in de pagina; met hem op 0 gerekend klopt hij bij het nakijken
    page = ogg_page(0, 1, 0, [b"x"], flags=2)
    assert AI.ogg_page(page, 0) is not None
    broken = bytearray(page)
    broken[-1] ^= 1
    assert AI.ogg_page(bytes(broken), 0) is None
    assert AI.ogg_crc(b"") == 0


def slow_crc(data: bytes) -> int:
    """De CRC van Ogg per byte, zoals de spec hem geeft: de maat voor de snelle."""
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
            crc &= 0xFFFFFFFF
    return crc


def test_ogg_crc_is_die_van_de_spec():
    """S5: ogg_crc rekent via zlib (in C); hij is gelijk aan de CRC per byte van de spec."""
    assert AI.ogg_crc(b"123456789") == 0x89A1897F  # CRC-32/POSIX zonder de xor op het eind
    rng = random.Random(20261030)
    for n in range(200):
        data = rng.randbytes(rng.randrange(0, 300))
        assert AI.ogg_crc(data) == slow_crc(data), n
        assert AI.ogg_crc(bytearray(data)) == slow_crc(data), n


def test_een_valse_ogg_blokkeert_de_worker_niet():
    """Review golf 4, S5: audio_info draait in de event loop van de worker. Een bron vol grote
    BOS-pagina's en valse pagina's in het staartje is binnen 0,1 s klaar (eerst de kop, dan pas
    de CRC; de CRC in C; en hooguit CRC_MAX bytes per bron nagekeken)."""
    data = ogg_worst()
    assert len(data) > 1_100_000
    for which in ("ogg", None):
        start = time.perf_counter()
        found = info(data, which)
        took = time.perf_counter() - start
        assert found == {"skip": "te veel Ogg-pagina's om na te kijken"}, found
        assert took < 0.1, (which, took)
    # Met één kleine kop: de valse pagina's in het staartje kosten elk een CRC, tot CRC_MAX
    small = ogg_page(0, 0x5151, 0, [opus_head()], flags=2) + ogg_worst()[16 * 65_306 :]
    start = time.perf_counter()
    assert info(small, "ogg") == {"skip": "te veel Ogg-pagina's om na te kijken"}
    assert time.perf_counter() - start < 0.1


def test_opus_laatste_granule_min_pre_skip():
    """(spike S8) samples = laatste granule − pre_skip; delay 0, want Opus gaat via FLAC."""
    data = opus_file(960_312, pre_skip=312)
    assert info(data, "ogg") == want(48000, 960_000, 0, 0, "opus")
    assert info(data, None)["samples"] == 960_000
    assert info(opus_file(48_000 + 3840, pre_skip=3840), "ogg")["samples"] == 48_000


def test_vorbis_en_een_tweede_spoor():
    a, v = 0x0A, 0x0B
    data = ogg_page(0, a, 0, [opus_head(312)], flags=2)
    data += ogg_page(0, v, 0, [vorbis_head(44100)], flags=2)
    data += ogg_page(0, a, 1, [b"OpusTags"]) + ogg_page(0, v, 1, [b"\x03vorbis"])
    data += ogg_page(48_312, a, 2, [b"\xfc" * 50], flags=4)
    data += ogg_page(44_100, v, 2, [b"\x00" * 50], flags=4)
    data += ogg_page(-1, v, 3, [b"\x00" * 10])  # een pagina zonder granule telt niet
    assert info(data, "ogg", 0) == want(48000, 48_000, 0, 0, "opus")
    assert info(data, "ogg", 1) == want(44100, 44_100, 0, 0, "vorbis")
    assert "geen Opus- of Vorbis-spoor" in info(data, "ogg", 2)["skip"]


def test_ogg_kapotte_laatste_pagina():
    data = bytearray(opus_file(96_312))
    data[-5] ^= 0xFF  # de CRC van de laatste pagina klopt niet meer: de pagina ervoor telt
    found = info(bytes(data), "ogg")
    assert found["samples"] == (96_312 // 4) * 3 - 312
    # Zonder pagina van dit spoor in het staartje: skip
    alone = ogg_page(0, 7, 0, [opus_head()], flags=2) + ogg_page(0, 7, 1, [b"OpusTags"])
    assert set(info(alone, "ogg")) == {"skip"}  # alleen de koppen: granule 0
    other = b"".join(ogg_page(960 * i, 8, i, [b"\xfc" * 250] * 40) for i in range(1, 10))
    assert "laatste Ogg-pagina" in info(alone + other, "ogg")["skip"]
    # Geen Opus en geen Vorbis (Ogg FLAC): skip
    assert "geen Opus" in info(ogg_page(0, 1, 0, [b"\x7fFLAC"], flags=2), "ogg")["skip"]


def test_ogg_leest_alleen_de_kop_en_de_laatste_pagina(monkeypatch):
    """Keuze 18: het staartje van hooguit één pagina, en vooraan de BOS-pagina's (met wat erna
    komt in één leesactie). Het midden van een groot bestand wordt nooit gelezen."""
    serial = 99
    data = ogg_page(0, serial, 0, [opus_head()], flags=2) + ogg_page(0, serial, 1, [b"OpusTags"])
    middle_start = len(data) + 2 * AI.OGG_PAGE_MAX
    granule = 0
    seq = 2
    while len(data) < 3_000_000:
        granule += 960 * 50
        data += ogg_page(granule, serial, seq, [b"\xfc" * 250] * 50)
        seq += 1
    data += ogg_page(granule + 960, serial, seq, [b"\xfc" * 20], flags=4)
    reads: list[tuple[int, int]] = []
    real = os.pread

    def pread(fd: int, length: int, offset: int) -> bytes:
        reads.append((offset, length))
        return real(fd, length, offset)

    monkeypatch.setattr(os, "pread", pread)
    found = info(data, "ogg")
    assert found["samples"] == granule + 960 - 312
    tail = len(data) - AI.OGG_PAGE_MAX
    for offset, length in reads:
        assert offset + length <= middle_start or offset >= tail, (offset, length)
    assert sum(length for _, length in reads) <= 3 * AI.OGG_PAGE_MAX + 20


# --- MP4 ------------------------------------------------------------------------------------------


def test_aac_met_itunsmpb():
    data = mp4_file(trak(edits=[(20_000, 1024)]), extra=udta(smpb(2112, 1088, 882_000)))
    assert info(data, "mov") == want(44100, 882_000, 2112, 1088, "itunsmpb")
    assert info(data, None)["source"] == "itunsmpb"
    # meta zonder versie en vlaggen (QuickTime), en moov vooraan
    data = mp4_file(trak(), extra=udta(smpb(1024, 0, 44_100), full_meta=False), end=False)
    assert info(data, "mov") == want(44100, 44_100, 1024, 0, "itunsmpb")


def test_aac_zonder_itunsmpb_de_elst():
    # Zoals ffmpeg hem schrijft: media_time 1024 (de voorloop), de duur van de edit in de tijd
    # van de film (882.000 samples = 20.000 ms)
    data = mp4_file(trak(edits=[(20_000, 1024)]), extra=udta(None))
    assert info(data, "mov") == want(44100, 882_000, 1024, 0, "elst")
    # Een lege edit vooraan telt niet; versie 1 van elst en mdhd
    data = mp4_file(
        trak(edits=[(500, -1), (20_000, 2112)], elst_version=1, mdhd_version=1, duration=886_000)
    )
    assert info(data, "mov") == want(44100, 882_000, 2112, 1888, "elst")
    # Een andere tijdschaal van de media dan de samplefrequentie
    data = mp4_file(trak(scale=90_000, duration=1_801_800, edits=[(20_000, 2090)]))
    assert info(data, "mov")["delay"] == round(2090 * 44100 / 90_000)
    # Kapotte iTunSMPB (te weinig woorden of geen hex): dan de elst
    for text in (" 00000000 00000400", " 00000000 zz 00 11"):
        data = mp4_file(trak(edits=[(20_000, 1024)]), extra=udta(text))
        assert info(data, "mov")["source"] == "elst"


def test_mp4_zonder_elst_en_andere_codecs():
    assert info(mp4_file(trak(duration=44_100)), "mov") == want(44100, 44_100, 0, 0, "mdhd")
    # ALAC heeft geen voorloop; iTunSMPB geldt alleen voor AAC
    data = mp4_file(trak(codec=b"alac", duration=44_100), extra=udta(smpb(2112, 0, 10)))
    assert info(data, "mov") == want(44100, 44_100, 0, 0, "mdhd")


def test_mp4_welk_spoor():
    data = mp4_file(
        trak(b"vide"), trak(duration=1000), trak(duration=2000, rate=48000, scale=48000)
    )
    assert info(data, "mov", 0)["samples"] == 1000
    assert info(data, "mov", 1) == want(48000, 2000, 0, 0, "mdhd")
    assert "geen audiospoor" in info(data, "mov", 2)["skip"]
    assert "geen audiospoor" in info(mp4_file(trak(b"vide")), "mov")["skip"]


def test_mp4_skip():
    mvex = box(b"mvex", box(b"trex", b"\0" * 24))
    assert "gefragmenteerde" in info(mp4_file(trak(), extra=mvex), "mov")["skip"]
    assert set(info(mp4_file(trak(duration=0)), "mov")) == {"skip"}
    assert set(info(box(b"ftyp", b"M4A ") + box(b"mdat", b"\0" * 10), "mov")) == {"skip"}


def test_geen_lezer():
    assert "geen lezer voor matroska" in info(b"\x1aE\xdf\xa3" + b"\0" * 60, "matroska")["skip"]
    assert "geen lezer voor dit formaat" in info(b"\0" * 100, None)["skip"]
    assert set(info(b"", None)) == {"skip"}


# --- fuzz met een vast zaad per lezer -------------------------------------------------------------


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
            buf[pos : pos + 4] = rng.choice(
                [b"\xff" * 4, b"\x00" * 4, b"\x01\xff\xff\xff", b"OggS", b"Xing", b"fLaC"]
            )
    if rng.random() < 0.1:
        buf = buf[: rng.randrange(len(buf) + 1)]
    return bytes(buf)


FUZZ: dict[str, tuple[str, list[Callable[[], bytes]]]] = {
    "flac": ("flac", [flac_file, lambda: id3(100) + flac_file(total=12345)]),
    "mp3": (
        "mp3",
        [
            lambda: mp3_file(767, 576, 1008),
            lambda: id3(64) + mp3_file(50, 576, 0, mono=True, version=2, flags=1),
        ],
    ),
    "wav": ("wav", [lambda: wav_file(500), lambda: wav_file(10, claim=0xFFFFFFFF)]),
    "ogg": ("ogg", [lambda: opus_file(96_312), lambda: opus_file(48_000, pages=2), ogg_worst]),
    "mov": (
        "mov",
        [
            lambda: mp4_file(trak(edits=[(20_000, 1024)]), extra=udta(smpb(1024, 0, 9))),
            lambda: mp4_file(trak(b"vide"), trak(edits=[(500, -1), (10, 5)], elst_version=1)),
        ],
    ),
}


@pytest.mark.parametrize(("reader", "seed"), [(name, 20261030 + i) for i, name in enumerate(FUZZ)])
def test_fuzz_per_lezer(monkeypatch, reader, seed):
    """Vast zaad per lezer (regel 10): de bytes komen van buiten. Elk antwoord heeft de vijf
    velden binnen hun grenzen, of alleen skip; nooit een uitzondering; nooit meer dan READ_MAX
    bytes gelezen (plus het proeven van 8 KB); en elke bron binnen 0,1 s (de lezers draaien in
    de event loop van de worker; review golf 4, S5)."""
    rng = random.Random(seed)
    demuxer, bases = FUZZ[reader]
    seeds = [make() for make in bases]
    real = os.pread
    total = [0]

    def pread(fd: int, length: int, offset: int) -> bytes:
        total[0] += length
        return real(fd, length, offset)

    monkeypatch.setattr(os, "pread", pread)
    for n in range(400):
        data = mutate(rng, rng.choice(seeds))
        for which in (demuxer, None):
            total[0] = 0
            start = time.perf_counter()
            found = info(data, which, rng.choice([0, 0, 1]))
            took = time.perf_counter() - start
            assert total[0] <= AI.READ_MAX + 8192, (n, total[0])
            assert took < 0.1, (n, took)
            if "skip" in found:
                assert set(found) == {"skip"} and isinstance(found["skip"], str)
                continue
            assert set(found) == FIELDS, found
            assert 0 < found["rate"] <= AI.RATE_MAX
            assert 0 < found["samples"] <= AI.SAMPLES_MAX
            assert 0 <= found["delay"] <= AI.SAMPLES_MAX
            assert 0 <= found["padding"] <= AI.SAMPLES_MAX


# --- remux_audio: de argv van §3.3 ----------------------------------------------------------------


def test_remux_audio_argv():
    args = A.RemuxAudioArgs(demuxer="ogg", audio=0, codec="flac")
    assert RA.argv(args) == [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2",
        "-nostats", "-max_alloc", "536870912", "-protocol_whitelist", "fd,pipe",
        "-format_whitelist", "mov,matroska,avi,wav,flv,ogg,asf,mpegts,mpeg,mp3,flac,aac",
        "-max_pixels", "35389440", "-f", "ogg", "-fd", "3", "-i", "fd:",
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
        "-c:a", "flac", "-f", "mp4", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-frag_duration", "10000000", "-use_editlist", "0", "pipe:1",
    ]  # fmt: skip
    argv = RA.argv(A.RemuxAudioArgs(demuxer="mov", audio=2, codec="copy"))
    assert argv[argv.index("-map") + 1] == "0:a:2" and argv[argv.index("-c:a") + 1] == "copy"
    assert A.niceness(Op.REMUX_AUDIO, A.RemuxAudioArgs(demuxer="mov")) == ()
    assert A.niceness(Op.REMUX_AUDIO, A.RemuxAudioArgs(demuxer="mov", background=True))


# --- echte bestanden van ffmpeg (marker media) ----------------------------------------------------

FF = ("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y")
SINE = "sine=frequency=440:sample_rate={rate}:duration={seconds}"


def ffmpeg(*argv: str) -> None:
    subprocess.run([*FF, *argv], check=True, timeout=120, stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def made(tmp_path_factory) -> dict[str, Path]:
    """Twintig seconden sinus per formaat, na elkaar gemaakt (één ffmpeg tegelijk, regel 13)."""
    if not F.available():
        pytest.skip("ffmpeg ontbreekt")
    base = tmp_path_factory.mktemp("gapless")
    out: dict[str, Path] = {}
    cd = ("-f", "lavfi", "-i")
    recipes = {
        "a.flac": (SINE.format(rate=44100, seconds=20), ("-ac", "2", "-c:a", "flac")),
        "a.mp3": (SINE.format(rate=44100, seconds=20), ("-ac", "2", "-c:a", "libmp3lame")),
        "a.opus": (SINE.format(rate=48000, seconds=20), ("-ac", "2", "-c:a", "libopus")),
        "a.m4a": (SINE.format(rate=44100, seconds=20), ("-ac", "2", "-c:a", "aac")),
        "a.wav": (SINE.format(rate=44100, seconds=20), ("-ac", "2", "-c:a", "pcm_s16le")),
        "a.ogg": (SINE.format(rate=44100, seconds=20), ("-ac", "2", "-c:a", "libvorbis")),
    }
    for name, (source, codec) in recipes.items():
        out[name] = base / name
        ffmpeg(*cd, source, *codec, str(out[name]))
    # AAC met iTunSMPB (zoals de fixture album/): dezelfde bytes met de tag erbij
    tagged = base / "smpb.m4a"
    shutil.copy(out["a.m4a"], tagged)
    from mutagen.mp4 import MP4, MP4FreeForm

    tags = MP4(tagged)
    tags["----:com.apple.iTunes:iTunSMPB"] = [MP4FreeForm(smpb(1024, 688, 882_000).encode())]
    tags.save()
    out["smpb.m4a"] = tagged
    return out


def read_path(path: Path, demuxer: str | None) -> dict:
    fd = os.open(path, os.O_RDONLY)
    try:
        return AI.read(fd, demuxer)
    finally:
        os.close(fd)


@pytest.mark.media
@pytest.mark.parametrize(
    ("name", "demuxer", "expected"),
    [
        ("a.flac", "flac", want(44100, 882_000, 0, 0, "streaminfo")),
        # ffmpeg schrijft de LAME-tag zelf, met "Lavc" als encoder; LAME zet 576 voorloop
        ("a.mp3", "mp3", want(44100, 882_000, 576, 1008, "lavc")),
        ("a.opus", "ogg", want(48000, 960_000, 0, 0, "opus")),
        ("a.m4a", "mov", want(44100, 882_000, 1024, 0, "elst")),
        ("smpb.m4a", "mov", want(44100, 882_000, 1024, 688, "itunsmpb")),
        ("a.wav", "wav", want(44100, 882_000, 0, 0, "wav")),
        ("a.ogg", "ogg", want(44100, 882_000, 0, 0, "vorbis")),
    ],
)
def test_echt_van_ffmpeg(made, name, demuxer, expected):
    assert read_path(made[name], demuxer) == expected
    assert read_path(made[name], None) == expected


@pytest.mark.media
def test_echt_de_fixture_album():
    album = F.make("album")
    for n in (1, 2, 3):
        assert read_path(album / "flac" / f"{n:02d}.flac", "flac")["samples"] == F.ALBUM_TRACK
        mp3 = read_path(album / "mp3" / f"{n:02d}.mp3", "mp3")
        assert (mp3["samples"], mp3["delay"]) == (F.ALBUM_TRACK, 576)
        opus = read_path(album / "opus" / f"{n:02d}.opus", "ogg")
        assert (opus["rate"], opus["samples"], opus["delay"]) == (48000, 4 * 48000, 0)
        aac = read_path(album / "aac" / f"{n:02d}.m4a", "mov")
        assert aac["samples"] == F.ALBUM_TRACK and aac["delay"] == 1024
        assert aac["source"] == ("itunsmpb" if n < 3 else "elst")


KEY = "de-sleutel-van-gapless-in-de-test-" * 2
CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}


async def _drain(fd: int) -> bytes:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(fd, "rb", buffering=0)
    )
    try:
        return await reader.read()
    finally:
        transport.close()


def decoded(data: bytes, tmp: Path) -> tuple[int, list[str]]:
    """(het aantal samples na decoderen, de codecs volgens ffprobe) van deze fMP4."""
    path = tmp / "uit.mp4"
    path.write_bytes(data)
    done = subprocess.run(
        [*FF, "-i", str(path), "-f", "s16le", "-ac", "2", "-"],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
         str(path)],
        check=True, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    return len(done.stdout) // 4, probe.stdout.split()


@pytest.mark.media
@pytest.mark.anyio
async def test_echt_via_de_worker(made, tmp_path):
    """De opdrachten audio_info en remux_audio van begin tot eind. Na remux_audio heeft de fMP4
    precies de samples (FLAC, Opus via FLAC en WAV via FLAC), of de voorloop, de samples en de
    opvulling van het laatste frame (AAC, gekopieerd zonder edit list)."""
    config = Config(socket=tmp_path / "w.sock", max_procs=3, max_transcodes=1, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        cases = [
            ("a.flac", "flac", "copy", 882_000, "flac"),
            ("a.opus", "ogg", "flac", 960_000, "flac"),
            ("a.wav", "wav", "flac", 882_000, "flac"),
            ("smpb.m4a", "mov", "copy", 1024 + 882_000 + 688, "aac"),
        ]
        for name, demuxer, codec, samples, out_codec in cases:
            job, _ = await client.call(
                Op.AUDIO_INFO, A.AudioInfoArgs(demuxer=demuxer), source=made[name]
            )
            async with job:
                got = await job.result()
            assert set(got) == FIELDS
            args = A.RemuxAudioArgs(demuxer=demuxer, codec=codec)
            job, (out,) = await client.call(Op.REMUX_AUDIO, args, source=made[name], outputs=1)
            async with job:
                data = await _drain(out)
                result = await job.result()
            assert result["cpu_s"] is not None
            assert data[4:8] == b"ftyp" and b"moof" in data and b"elst" not in data
            assert decoded(data, tmp_path) == (samples, [out_codec]), name
            # Het budget van audio.mp4 (review golf 4, S1) is een bovengrens, ook zonder de
            # marge voor de kop: het echte bestand blijft eronder
            rate = 48000 if name.endswith(".opus") else 44100
            stream = Stream(index=0, type="audio", codec=out_codec, sample_rate=rate, channels=2)
            size = made[name].stat().st_size
            bound = stream_audio.expected_bytes(size, 20_000, stream, codec)
            assert bound is not None and len(data) <= bound - stream_audio.SLACK, (name, len(data))
    finally:
        await w.stop()
        client.bind(None)
        await asyncio.sleep(0)
