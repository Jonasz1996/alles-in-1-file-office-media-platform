"""audio_info: de gapless-gegevens van één nummer (§3.3, Keuze 18). Van M9.

Eigen lezers, begrensd, alleen in de worker (regel 5): ze lezen met pread op de fd van de bron
(de positie van de fd blijft staan, spike punt 1), hooguit READ_MAX bytes samen, nooit het hele
bestand, en zonder ffmpeg. Ze draaien in de event loop zelf, zoals de keyframes (de worker heeft
geen threads; een paar KB pread kost niets).

- **MP3**: na een ID3v2-tag het eerste frame van Layer III met de Xing- of Info-kop, en daarin
  de LAME- of Lavc-tag (`enc_delay`, `enc_padding`). delay = `enc_delay`, zonder de 529 van de
  decoder: zo is het exact in Chromium 141 (spike S8). samples = frames × 1152 (of 576 bij
  MPEG-2) − delay − padding. Zonder LAME-tag weten we de voorloop niet: dan niets (skip);
- **AAC in MP4**: `iTunSMPB` in moov/udta/meta/ilst (voorloop, opvulling, lengte); anders de
  eerste echte edit van de `elst` (media_time is de voorloop, de duur van de edit de lengte) en
  anders de duur van de mdhd. ALAC en FLAC in MP4 hebben geen voorloop;
- **Ogg**: de BOS-pagina's vooraan (OpusHead of de Vorbis-kop) en alleen de laatste pagina van
  dat spoor (uit het staartje van hooguit één pagina, met de CRC van Ogg nagekeken), voor de
  laatste granulepositie. Opus gaat via remux_audio naar FLAC (Keuze 38): dan is de delay 0, en
  samples = laatste granule − pre_skip (spike S8). Vorbis: geen voorloop, samples = de laatste
  granule;
- **FLAC**: `total_samples` uit STREAMINFO (0 is onbekend: skip). Geen voorloop;
- **WAV**: de grootte van de data-chunk gedeeld door block_align. Geen voorloop.

Welk spoor: audio_index is het hoeveelste audiospoor (0 is het eerste), zoals `-map 0:a:N` van
remux_audio. MP3, FLAC en WAV hebben er één.

Het antwoord: {rate, samples, delay, padding, source}, of {skip: reden} als de bron het niet
zegt (een andere container, een kapotte kop, een grens bereikt). Dat is geen fout van de
opdracht: de speler speelt dan zonder te knippen. Dit bestand importeert niets buiten de worker.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from app.media.worker import containers
from app.media.worker.args import AudioInfoArgs
from app.media.worker.containers import Reader, ReadError
from app.media.worker.ops.probe import id3_size, sniff
from app.media.worker.server import Context

KiB = 1024
READ_MAX = 4 * 1024 * KiB  # samen, per bron (zoals de keyframes, Keuze 17)
SYNC_WINDOW = 64 * KiB  # zo ver na de ID3-tag zoeken we het eerste MP3-frame
OGG_PAGE_MAX = 27 + 255 + 255 * 255  # de grootste Ogg-pagina
OGG_HEADS_MAX = 16  # hooguit zoveel BOS-pagina's vooraan (gemultiplexte stromen)
OGG_TRIES_MAX = 64  # hooguit zoveel keer "OggS" in het staartje nakijken (de CRC kost tijd)
WAV_CHUNKS_MAX = 64
ELST_ROWS_MAX = 16
SAMPLES_MAX = 2**40  # ruim boven 300 uur op 768 kHz; daarboven klopt de kop niet
RATE_MAX = 768_000
OPUS_RATE = 48_000  # de granule van Opus telt altijd op 48 kHz

# MP3 (Layer III): de samplefrequentie per versie (bits 19–20 van de kop; 1 is gereserveerd)
MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
LAME_TAGS = (b"LAME", b"Lavc", b"Lavf", b"GOGO")


class Skip(Exception):
    """De bron zegt het niet (geen fout van de opdracht)."""


@dataclass(frozen=True)
class Info:
    rate: int
    samples: int
    delay: int
    padding: int
    source: str

    def wire(self) -> dict:
        return {
            "rate": self.rate,
            "samples": self.samples,
            "delay": self.delay,
            "padding": self.padding,
            "source": self.source,
        }


def _check(info: Info) -> Info:
    if not 0 < info.rate <= RATE_MAX:
        raise Skip("samplefrequentie buiten de grenzen")
    if not 0 < info.samples <= SAMPLES_MAX:
        raise Skip("aantal samples buiten de grenzen")
    if not 0 <= info.delay <= SAMPLES_MAX or not 0 <= info.padding <= SAMPLES_MAX:
        raise Skip("voorloop of opvulling buiten de grenzen")
    return info


def _after_id3(reader: Reader) -> int:
    """De positie na een ID3v2-tag vooraan (of 0)."""
    size = id3_size(reader.read(0, 10))
    return size if size is not None else 0


# --- MP3 ------------------------------------------------------------------------------------------


def _mp3_frame(window: bytes) -> int:
    """De positie van het eerste geldige kopje van Layer III in window."""
    pos = 0
    while True:
        pos = window.find(b"\xff", pos)
        if pos < 0 or pos + 4 > len(window):
            raise Skip("geen MP3-frame vooraan")
        b2, b3 = window[pos + 1], window[pos + 2]
        version, layer = (b2 >> 3) & 3, (b2 >> 1) & 3
        if (
            (b2 & 0xE0) == 0xE0
            and version != 1
            and layer == 1
            and ((b3 >> 2) & 3) != 3
            and (b3 >> 4) not in (0, 15)
        ):
            return pos
        pos += 1


def mp3(fd: int) -> Info:
    reader = Reader(fd, READ_MAX)
    start = _after_id3(reader)
    window = reader.read(start, SYNC_WINDOW)
    pos = _mp3_frame(window)
    b2, b3, b4 = window[pos + 1], window[pos + 2], window[pos + 3]
    version = (b2 >> 3) & 3
    rate = MP3_RATES[version][(b3 >> 2) & 3]
    mono = (b4 >> 6) == 3
    spf = 1152 if version == 3 else 576
    side = (17 if mono else 32) if version == 3 else (9 if mono else 17)
    at = 4 + side
    frame = reader.read(start + pos, at + 8 + 4 + 4 + 100 + 4 + 24)
    if frame[at : at + 4] not in (b"Xing", b"Info"):
        raise Skip("geen Xing- of Info-kop (een MP3 zonder LAME-tag)")
    (flags,) = struct.unpack(">I", frame[at + 4 : at + 8])
    p = at + 8
    frames = 0
    if flags & 1:
        (frames,) = struct.unpack(">I", frame[p : p + 4])
        p += 4
    p += (4 if flags & 2 else 0) + (100 if flags & 4 else 0) + (4 if flags & 8 else 0)
    if frames == 0:
        raise Skip("de Xing-kop zegt het aantal frames niet")
    lame = frame[p : p + 24]
    if len(lame) < 24 or lame[:4] not in LAME_TAGS:
        raise Skip("geen LAME- of Lavc-tag na de Xing-kop")
    value = (lame[21] << 16) | (lame[22] << 8) | lame[23]
    delay, padding = value >> 12, value & 0xFFF
    source = "lavc" if lame[:4] in (b"Lavc", b"Lavf") else "lame"
    return _check(Info(rate, frames * spf - delay - padding, delay, padding, source))


# --- FLAC -----------------------------------------------------------------------------------------


def flac(fd: int) -> Info:
    reader = Reader(fd, READ_MAX)
    head = reader.read(_after_id3(reader), 4 + 4 + 34)
    if len(head) < 42 or head[:4] != b"fLaC":
        raise Skip("geen fLaC vooraan")
    if head[4] & 0x7F != 0 or int.from_bytes(head[5:8], "big") != 34:
        raise Skip("het eerste blok is geen STREAMINFO")
    info = head[8:42]
    rate = (info[10] << 12) | (info[11] << 4) | (info[12] >> 4)
    total = ((info[13] & 0x0F) << 32) | struct.unpack(">I", info[14:18])[0]
    if total == 0:
        raise Skip("STREAMINFO zegt het aantal samples niet")
    return _check(Info(rate, total, 0, 0, "streaminfo"))


# --- WAV ------------------------------------------------------------------------------------------


def wav(fd: int) -> Info:
    reader = Reader(fd, READ_MAX)
    head = reader.read(0, 12)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise Skip("geen RIFF/WAVE")
    pos = 12
    rate = block = 0
    for _ in range(WAV_CHUNKS_MAX):
        chunk = reader.read(pos, 8)
        if len(chunk) < 8:
            break
        kind, size = chunk[:4], struct.unpack("<I", chunk[4:8])[0]
        if kind == b"fmt ":
            fmt = reader.read(pos + 8, min(size, 40))
            if len(fmt) < 16:
                raise Skip("de fmt-chunk is te kort")
            _format, _channels, rate, _bytes, block = struct.unpack("<HHIIH", fmt[:14])
        elif kind == b"data":
            if not rate or not block:
                raise Skip("data vóór fmt")
            size = min(size, max(0, reader.size - pos - 8))  # een data-chunk voorbij het eind
            return _check(Info(rate, size // block, 0, 0, "wav"))
        pos += 8 + size + (size & 1)
    raise Skip("geen data-chunk")


# --- Ogg ------------------------------------------------------------------------------------------


def _crc_table() -> tuple[int, ...]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        table.append(r & 0xFFFFFFFF)
    return tuple(table)


_CRC = _crc_table()


def ogg_crc(data: bytes) -> int:
    """De CRC-32 van Ogg (polynoom 0x04C11DB7, niet gespiegeld, begin 0)."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[(crc >> 24) ^ byte]
    return crc


@dataclass(frozen=True)
class Page:
    flags: int
    granule: int
    serial: int
    length: int  # kop, segmenttabel en data
    data: bytes


def ogg_page(buf: bytes, pos: int) -> Page | None:
    """De pagina op pos als ze helemaal in buf staat en de CRC klopt, anders None."""
    if pos < 0 or pos + 27 > len(buf) or buf[pos : pos + 4] != b"OggS" or buf[pos + 4] != 0:
        return None
    flags = buf[pos + 5]
    granule, serial, _seq, crc, count = struct.unpack("<qIIIB", buf[pos + 6 : pos + 27])
    table_end = pos + 27 + count
    if table_end > len(buf):
        return None
    end = table_end + sum(buf[pos + 27 : table_end])
    if end > len(buf):
        return None
    raw = bytearray(buf[pos:end])
    raw[22:26] = b"\0\0\0\0"
    if ogg_crc(bytes(raw)) != crc:
        return None
    return Page(flags, granule, serial, end - pos, bytes(buf[table_end:end]))


def _ogg_heads(reader: Reader) -> list[tuple[int, str, int, int]]:
    """De BOS-pagina's vooraan: (serial, soort, frequentie, pre_skip) per audiospoor."""
    out: list[tuple[int, str, int, int]] = []
    pos = 0
    for _ in range(OGG_HEADS_MAX):
        page = ogg_page(reader.read(pos, OGG_PAGE_MAX), 0)
        if page is None or not page.flags & 2:
            break
        data = page.data
        if data[:8] == b"OpusHead" and len(data) >= 19:
            out.append((page.serial, "opus", OPUS_RATE, struct.unpack("<H", data[10:12])[0]))
        elif data[:7] == b"\x01vorbis" and len(data) >= 16:
            out.append((page.serial, "vorbis", struct.unpack("<I", data[12:16])[0], 0))
        pos += page.length
    return out


def _last_granule(reader: Reader, serial: int) -> int:
    """De granule van de laatste pagina van dit spoor, uit het staartje van de bron."""
    start = max(0, reader.size - OGG_PAGE_MAX)
    tail = reader.read(start, reader.size - start)
    at = len(tail)
    for _ in range(OGG_TRIES_MAX):
        at = tail.rfind(b"OggS", 0, at)
        if at < 0:
            break
        page = ogg_page(tail, at)
        if page is not None and page.serial == serial and page.granule >= 0:
            return page.granule
    raise Skip("geen laatste Ogg-pagina van dit spoor")


def ogg(fd: int, audio_index: int = 0) -> Info:
    reader = Reader(fd, READ_MAX)
    heads = _ogg_heads(reader)
    if audio_index >= len(heads):
        raise Skip("geen Opus- of Vorbis-spoor met deze index")
    serial, kind, rate, pre_skip = heads[audio_index]
    granule = _last_granule(reader, serial)
    if kind == "opus":
        # Via remux_audio naar FLAC: de decoder knipt de pre_skip al weg (spike S8)
        return _check(Info(OPUS_RATE, granule - pre_skip, 0, 0, "opus"))
    return _check(Info(rate, granule, 0, 0, "vorbis"))


# --- MP4 ------------------------------------------------------------------------------------------

_box, _boxes, _find, _full = containers._box, containers._boxes, containers._find, containers._full


def _times(buf: bytes, box: tuple[int, int] | None) -> tuple[int, int]:
    """(tijdschaal, duur) uit mvhd of mdhd."""
    if box is None:
        raise ReadError("geen mvhd of mdhd")
    version, pos = _full(buf, box[0], box[1], 16)
    if version == 1:
        if pos + 28 > box[1]:
            raise ReadError("mdhd afgekapt")
        return struct.unpack(">IQ", buf[pos + 16 : pos + 28])
    return struct.unpack(">II", buf[pos + 8 : pos + 16])


def _itunsmpb(buf: bytes, start: int, stop: int) -> tuple[int, int, int] | None:
    """(voorloop, opvulling, lengte) uit udta/meta/ilst/----:iTunSMPB onder de moov, of None."""
    meta = _find(buf, start, stop, "udta", "meta")
    if meta is None:
        return None
    begin, end = meta
    # meta is in MP4 een full box (versie en vlaggen ervoor), in QuickTime niet
    if buf[begin + 4 : begin + 8] != b"hdlr" and buf[begin + 8 : begin + 12] == b"hdlr":
        begin += 4
    ilst = _find(buf, begin, end, "ilst")
    if ilst is None:
        return None
    for kind, data, close in _boxes(buf, ilst[0], ilst[1]):
        if kind != "----":
            continue
        name = value = None
        for sub, sdata, sclose in _boxes(buf, data, close):
            if sub == "name" and sclose - sdata >= 4:
                name = buf[sdata + 4 : sclose]
            elif sub == "data" and sclose - sdata >= 8:
                value = buf[sdata + 8 : sclose]
        if name != b"iTunSMPB" or value is None:
            continue
        words = value[:400].decode("latin-1").split()
        if len(words) < 4:
            return None
        try:
            return int(words[1], 16), int(words[2], 16), int(words[3], 16)
        except ValueError:
            return None
    return None


def mp4(fd: int, audio_index: int = 0) -> Info:
    reader = Reader(fd, READ_MAX)
    buf = containers._moov(reader)
    _, mdata, mstop = _box(buf, 0, len(buf))
    if _find(buf, mdata, mstop, "mvex") is not None:
        raise Skip("een gefragmenteerde MP4")
    movie_scale, _ = _times(buf, _find(buf, mdata, mstop, "mvhd"))
    traks = [(d, s) for k, d, s in _boxes(buf, mdata, mstop) if k == "trak"]
    audio = []
    for data, stop in traks[: containers.TRAKS_MAX]:
        hdlr = _find(buf, data, stop, "mdia", "hdlr")
        if hdlr is not None and buf[hdlr[0] + 8 : hdlr[0] + 12] == b"soun":
            audio.append((data, stop))
    if audio_index >= len(audio):
        raise Skip("geen audiospoor met deze index")
    trak = audio[audio_index]
    media_scale, duration = _times(buf, _find(buf, trak[0], trak[1], "mdia", "mdhd"))
    stsd = _find(buf, trak[0], trak[1], "mdia", "minf", "stbl", "stsd")
    if stsd is None or media_scale == 0 or movie_scale == 0:
        raise Skip("geen stsd of tijdschaal")
    entry = stsd[0] + 8  # na versie, vlaggen en het aantal
    if entry + 36 > stsd[1]:
        raise Skip("stsd te kort")
    codec = buf[entry + 4 : entry + 8]
    rate = (struct.unpack(">I", buf[entry + 32 : entry + 36])[0] >> 16) or media_scale

    def samples(units: int) -> int:
        return units if media_scale == rate else round(units * rate / media_scale)

    if codec == b"mp4a":
        found = _itunsmpb(buf, mdata, mstop)
        if found is not None and found[2] > 0:
            delay, padding, length = found
            return _check(Info(rate, length, delay, padding, "itunsmpb"))
    elst = _find(buf, trak[0], trak[1], "edts", "elst")
    if elst is not None:
        version, pos = _full(buf, elst[0], elst[1], 4)
        (count,) = struct.unpack(">I", buf[pos : pos + 4])
        width, fmt = (20, ">QqHH") if version == 1 else (12, ">IiHH")
        rows = buf[pos + 4 : min(elst[1], pos + 4 + min(count, ELST_ROWS_MAX) * width)]
        rows = rows[: len(rows) // width * width]
        for segment, media_time, _r, _f in struct.iter_unpack(fmt, rows):
            if media_time < 0:
                continue  # een lege edit vooraan
            delay = samples(media_time)
            length = round(segment * rate / movie_scale)
            padding = max(0, samples(duration) - delay - length)
            return _check(Info(rate, length, delay, padding, "elst"))
    return _check(Info(rate, samples(duration), 0, 0, "mdhd"))


# --- de opdracht ----------------------------------------------------------------------------------

SINGLE = {"mp3": mp3, "flac": flac, "wav": wav}
INDEXED = {"ogg": ogg, "mov": mp4}
ERRORS = (ReadError, struct.error, IndexError, KeyError, OverflowError, ValueError, OSError)


def read(fd: int, demuxer: str | None, audio_index: int = 0) -> dict:
    """De gapless-gegevens als dict voor het antwoord, of {skip: reden}."""
    try:
        if demuxer is None:
            head = os.pread(fd, 4096, 0)
            size = id3_size(head)
            found = sniff(head, os.pread(fd, 4096, size) if size else None)
            demuxer = found[0] if found else None
        if demuxer in SINGLE:
            if audio_index != 0:
                raise Skip("dit formaat heeft één audiospoor")
            return SINGLE[demuxer](fd).wire()
        if demuxer in INDEXED:
            return INDEXED[demuxer](fd, audio_index).wire()
        raise Skip(f"geen lezer voor {demuxer or 'dit formaat'}")
    except Skip as exc:
        return {"skip": str(exc)}
    except ERRORS as exc:
        return {"skip": f"de kop klopt niet ({exc.__class__.__name__})"}


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, AudioInfoArgs)
    return read(ctx.inputs[0], ctx.args.demuxer, ctx.args.audio_index)
