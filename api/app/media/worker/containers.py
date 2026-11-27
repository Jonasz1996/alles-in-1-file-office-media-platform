"""Eigen lezers in de worker voor de keyframe-index (Keuze 17): Matroska-Cues en MP4
stss/stts/ctts/elst, begrensd. Van M4.

Beide lezen met pread op de fd van de bron (de positie van de fd blijft staan, spike punt 1),
hooguit READ_MAX bytes samen, en nooit het hele bestand:

- **Matroska**: de EBML-kop, het begin van Segment, dan de elementen op niveau 1 tot het eerste
  Cluster (alleen hun koppen), en via de SeekHead de Cues, Info en Tracks die verderop staan.
  Een kleine EBML-lezer met grenzen op diepte, grootte en aantal. De tijden zijn CueTime ×
  TimecodeScale; de grootte van een GOP is het verschil tussen twee CueClusterPositions (plus
  CueRelativePosition), dus met het geluid erbij;
- **MP4**: de koppen op het hoogste niveau tot de moov (achteraan kost dat twee keer lezen), de
  moov zelf (hooguit READ_MAX), en in de trak van het videospoor stts, ctts, stss, stsz en de
  eerste edit van de elst. De tijden zijn de pts van de sync samples, zoals ffmpeg ze met de
  edit list geeft; de grootte van een GOP is de som van zijn samples (alleen video).

Welk spoor: video_index is de index van ffprobe. Bij MP4 is dat de volgorde van de traks; bij
Matroska de volgorde van de TrackEntries die ffmpeg een stream geeft (video, audio, ondertitel
en metadata, met een CodecID). Is dat spoor geen video, dan geeft de lezer niets en volgt de
terugval.

Alles is in ms in de tijd van de container (de pts zoals ffprobe hem geeft); de app trekt de
start van de probe eraf voor de mediatijd. Een lezer geeft None als hij niets bruikbaars vindt
(geen Cues, geen moov, een gefragmenteerde MP4, een fout in de bytes): dan de terugval met
ffprobe (op de achtergrond).

Dit bestand importeert niets van de app (de worker draait zonder de rest).
"""

from __future__ import annotations

import array
import base64
import math
import os
import struct
import sys
import zlib
from dataclasses import dataclass, field

KiB = 1024
MiB = 1024 * KiB
READ_MAX = 4 * MiB  # samen, per bron (Keuze 17)
SPARSE_GAP_MS = 20_000  # Cues met een gat hierboven: de terugval is nodig (Keuze 17)
HEAD = 64 * KiB

# --- Matroska ----------------------------------------------------------------------------------

EBML = 0x1A45DFA3
DOCTYPE = 0x4282
SEGMENT = 0x18538067
SEEKHEAD = 0x114D9B74
SEEK = 0x4DBB
SEEK_ID = 0x53AB
SEEK_POSITION = 0x53AC
INFO = 0x1549A966
TIMECODE_SCALE = 0x2AD7B1
DURATION = 0x4489
TRACKS = 0x1654AE6B
TRACK_ENTRY = 0xAE
TRACK_NUMBER = 0xD7
TRACK_TYPE = 0x83
CODEC_ID = 0x86
CUES = 0x1C53BB6B
CUE_POINT = 0xBB
CUE_TIME = 0xB3
CUE_TRACK_POSITIONS = 0xB7
CUE_TRACK = 0xF7
CUE_CLUSTER_POSITION = 0xF1
CUE_RELATIVE_POSITION = 0xF0
CLUSTER = 0x1F43B675
# De soorten sporen waar ffmpeg een stream van maakt (matroskadec: video, audio, ondertitel,
# metadata); de rest slaat hij over, en telt dus niet mee in de index van ffprobe
STREAM_TYPES = frozenset({1, 2, 0x11, 0x21})
VIDEO_TYPE = 1
UNKNOWN = -1  # een grootte met alleen enen: onbekend (live, een pipe)
LEVEL1_MAX = 512  # koppen op niveau 1 vóór het eerste Cluster
SEEKHEADS_MAX = 4
SMALL_MAX = 256 * KiB  # SeekHead, Info
TRACKS_MAX = 1 * MiB
CUE_POINTS_MAX = 2_000_000
ELEMENTS_MAX = 4_000_000  # elementen per lijst die we doorlopen

# --- MP4 ---------------------------------------------------------------------------------------

TOP_MAX = 64  # dozen op het hoogste niveau tot de moov
TRAKS_MAX = 64
SAMPLES_MAX = 4_000_000  # een film van 18 uur op 60 fps
CONTAINERS = frozenset({"moov", "trak", "mdia", "minf", "stbl", "edts", "mvex"})


class ReadError(ValueError):
    """De bytes kloppen niet, of een grens is bereikt."""


@dataclass
class Index:
    """De keyframes van één videospoor: pts in ms (container), oplopend, en de grootte van de
    GOP die erop begint in bytes. sparse: Cues met gaten boven SPARSE_GAP_MS. end_ms: waar het
    spoor ophoudt (de duur), als de container het zegt."""

    times_ms: list[int]
    sizes: list[int]
    source: str  # cues | stss | packets
    sparse: bool = False
    end_ms: int | None = None

    def max_gop_ms(self) -> int | None:
        if not self.times_ms:
            return None
        gaps = [b - a for a, b in zip(self.times_ms, self.times_ms[1:], strict=False)]
        if self.end_ms is not None and self.end_ms > self.times_ms[-1]:
            gaps.append(self.end_ms - self.times_ms[-1])
        return max(gaps, default=0)


@dataclass
class Reader:
    """pread met een grens op het totaal."""

    fd: int
    budget: int = READ_MAX
    used: int = 0
    size: int = field(init=False)

    def __post_init__(self) -> None:
        self.size = os.fstat(self.fd).st_size

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            raise ReadError("negatieve positie")
        length = min(length, max(0, self.size - offset))
        if self.used + length > self.budget:
            raise ReadError(f"meer dan {self.budget} bytes lezen")
        self.used += length
        data = os.pread(self.fd, length, offset)
        if len(data) != length:
            raise ReadError("afgekapt")
        return data


def finish(times: list[int], sizes: list[int]) -> tuple[list[int], list[int]]:
    """Oplopend en zonder dubbele tijden (de groottes van een dubbele tellen bij de eerste)."""
    pairs = sorted(zip(times, sizes, strict=True))
    out_t: list[int] = []
    out_s: list[int] = []
    for t, s in pairs:
        if out_t and t == out_t[-1]:
            out_s[-1] += s
            continue
        out_t.append(t)
        out_s.append(max(0, s))
    return out_t, out_s


def sparse(times: list[int], end_ms: int | None) -> bool:
    if not times:
        return True
    edges = [0, *times] if times[0] > SPARSE_GAP_MS else list(times)
    if end_ms is not None:
        edges.append(end_ms)
    return any(b - a > SPARSE_GAP_MS for a, b in zip(edges, edges[1:], strict=False))


# --- EBML --------------------------------------------------------------------------------------


def _vint(buf: bytes, pos: int, *, keep_marker: bool) -> tuple[int, int]:
    """(waarde, lengte) van een EBML-vint op pos; een ID houdt zijn markeerbit."""
    if pos >= len(buf):
        raise ReadError("vint voorbij het eind")
    first = buf[pos]
    if first == 0:
        raise ReadError("vint langer dan 8 bytes")
    length = 1
    mask = 0x80
    while not first & mask:
        mask >>= 1
        length += 1
    if keep_marker and length > 4:
        raise ReadError("ID langer dan 4 bytes")
    if pos + length > len(buf):
        raise ReadError("vint afgekapt")
    value = first if keep_marker else first & (mask - 1)
    all_ones = (first & (mask - 1)) == mask - 1
    for b in buf[pos + 1 : pos + length]:
        value = (value << 8) | b
        all_ones = all_ones and b == 0xFF
    if not keep_marker and all_ones:
        return UNKNOWN, length
    return value, length


def _element(buf: bytes, pos: int) -> tuple[int, int, int]:
    """(id, begin van de data, grootte of UNKNOWN) van het element op pos."""
    eid, n = _vint(buf, pos, keep_marker=True)
    size, m = _vint(buf, pos + n, keep_marker=False)
    return eid, pos + n + m, size


def _children(buf: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    """De kinderen (id, data, grootte) van een element met bekende grootte in buf."""
    out = []
    pos = start
    while pos < end:
        if len(out) >= ELEMENTS_MAX:
            raise ReadError("te veel elementen")
        eid, data, size = _element(buf, pos)
        if size == UNKNOWN or data + size > end:
            raise ReadError("een kind loopt voorbij zijn ouder")
        out.append((eid, data, size))
        pos = data + size
    return out


def _uint(buf: bytes, data: int, size: int) -> int:
    if size > 8:
        raise ReadError("een getal van meer dan 8 bytes")
    return int.from_bytes(buf[data : data + size], "big") if size else 0


def _float(buf: bytes, data: int, size: int) -> float | None:
    if size == 4:
        return struct.unpack(">f", buf[data : data + 4])[0]
    if size == 8:
        return struct.unpack(">d", buf[data : data + 8])[0]
    return None


def _header_at(reader: Reader, pos: int) -> tuple[int, int, int]:
    """De kop van het element op pos in het bestand (hooguit 12 bytes lezen)."""
    head = reader.read(pos, 12)
    eid, data, size = _element(head, 0)
    return eid, pos + data, size


def _body(reader: Reader, data: int, size: int, limit: int) -> bytes:
    if size == UNKNOWN or size > limit:
        raise ReadError("een element zonder grootte of te groot")
    return reader.read(data, size)


@dataclass
class _Mkv:
    seg_data: int
    seg_end: int
    scale: int = 1_000_000
    duration: float | None = None
    track: int | None = None
    cues: bytes | None = None
    seen: set[int] = field(default_factory=set)
    seeks: dict[int, list[int]] = field(default_factory=dict)


def _track_number(body: bytes, video_index: int) -> int | None:
    """Het nummer van het spoor met deze index van ffprobe, als het video is."""
    count = 0
    for eid, data, size in _children(body, 0, len(body)):
        if eid != TRACK_ENTRY:
            continue
        number = kind = None
        codec = False
        for cid, cdata, csize in _children(body, data, data + size):
            if cid == TRACK_NUMBER:
                number = _uint(body, cdata, csize)
            elif cid == TRACK_TYPE:
                kind = _uint(body, cdata, csize)
            elif cid == CODEC_ID:
                codec = csize > 0
        if kind not in STREAM_TYPES or not codec:
            continue
        if count == video_index:
            return number if kind == VIDEO_TYPE else None
        count += 1
    return None


def _take(state: _Mkv, reader: Reader, eid: int, data: int, size: int, video_index: int) -> None:
    """Een element op niveau 1 dat we willen: lezen en onthouden."""
    if eid in state.seen and eid != SEEKHEAD:
        return
    state.seen.add(eid)
    if eid == SEEKHEAD:
        body = _body(reader, data, size, SMALL_MAX)
        for sid, sdata, ssize in _children(body, 0, len(body)):
            if sid != SEEK:
                continue
            target = position = None
            for cid, cdata, csize in _children(body, sdata, sdata + ssize):
                if cid == SEEK_ID:
                    target, _ = _vint(body[cdata : cdata + csize], 0, keep_marker=True)
                elif cid == SEEK_POSITION:
                    position = _uint(body, cdata, csize)
            if target is not None and position is not None:
                state.seeks.setdefault(target, []).append(state.seg_data + position)
    elif eid == INFO:
        body = _body(reader, data, size, SMALL_MAX)
        for cid, cdata, csize in _children(body, 0, len(body)):
            if cid == TIMECODE_SCALE:
                state.scale = _uint(body, cdata, csize) or 1_000_000
            elif cid == DURATION:
                value = _float(body, cdata, csize)
                # NaN, ±Inf en ≤ 0 zijn geen duur: dan is het eind onbekend (review golf 2, S2;
                # round(NaN) gaf eerst een ValueError die niemand ving)
                ok = value is not None and math.isfinite(value) and value > 0
                state.duration = value if ok else None
    elif eid == TRACKS:
        body = _body(reader, data, size, TRACKS_MAX)
        state.track = _track_number(body, video_index)
        if state.track is None:
            raise ReadError("het spoor is geen video (of onbekend)")
    elif eid == CUES:
        state.cues = _body(reader, data, size, reader.budget)


def _cue_times(state: _Mkv, end: int) -> Index:
    assert state.cues is not None and state.track is not None
    cues = state.cues
    times: list[int] = []
    positions: list[int] = []
    for eid, data, size in _children(cues, 0, len(cues)):
        if eid != CUE_POINT:
            continue
        if len(times) >= CUE_POINTS_MAX:
            raise ReadError("te veel CuePoints")
        when: int | None = None
        where: int | None = None
        for cid, cdata, csize in _children(cues, data, data + size):
            if cid == CUE_TIME:
                when = _uint(cues, cdata, csize)
            elif cid == CUE_TRACK_POSITIONS and where is None:
                track = cluster = None
                relative = 0
                for pid, pdata, psize in _children(cues, cdata, cdata + csize):
                    if pid == CUE_TRACK:
                        track = _uint(cues, pdata, psize)
                    elif pid == CUE_CLUSTER_POSITION:
                        cluster = _uint(cues, pdata, psize)
                    elif pid == CUE_RELATIVE_POSITION:
                        relative = _uint(cues, pdata, psize)
                if track == state.track and cluster is not None:
                    where = state.seg_data + cluster + relative
        if when is not None and where is not None:
            times.append(round(when * state.scale / 1_000_000))
            positions.append(where)
    if not times:
        raise ReadError("geen Cues voor het videospoor")
    order = sorted(range(len(times)), key=lambda i: (positions[i], times[i]))
    pos_sorted = [positions[i] for i in order]
    sizes_by = {}
    for k, i in enumerate(order):
        nxt = pos_sorted[k + 1] if k + 1 < len(order) else end
        sizes_by[i] = max(0, nxt - positions[i])
    t, s = finish(times, [sizes_by[i] for i in range(len(times))])
    end_ms = round(state.duration * state.scale / 1_000_000) if state.duration is not None else None
    return Index(t, s, "cues", sparse=sparse(t, end_ms), end_ms=end_ms)


def matroska(fd: int, video_index: int, *, budget: int = READ_MAX) -> Index | None:
    """De keyframes uit de Cues, of None (geen Cues, geen video, of bytes die niet kloppen)."""
    try:
        return _matroska(Reader(fd, budget), video_index)
    except (ReadError, struct.error, OverflowError, ValueError):
        return None


def _matroska(reader: Reader, video_index: int) -> Index:
    head = reader.read(0, HEAD)
    eid, data, size = _element(head, 0)
    if eid != EBML or size == UNKNOWN or data + size > len(head):
        raise ReadError("geen EBML-kop")
    pos = data + size
    eid, seg_data, seg_size = _element(head, pos)
    if eid != SEGMENT:
        raise ReadError("geen Segment")
    seg_end = reader.size if seg_size == UNKNOWN else min(reader.size, seg_data + seg_size)
    state = _Mkv(seg_data, seg_end)
    pos = seg_data
    for _ in range(LEVEL1_MAX):
        if pos >= seg_end:
            break
        eid, data, size = _header_at(reader, pos)
        if eid == CLUSTER:
            break
        if eid in (SEEKHEAD, INFO, TRACKS, CUES):
            _take(state, reader, eid, data, size, video_index)
        if size == UNKNOWN:
            break
        pos = data + size
    # Wat er nog mist via de SeekHead (ook een tweede SeekHead, zoals mkvmerge die schrijft)
    done: set[int] = set()
    for _ in range(SEEKHEADS_MAX):
        more = [p for p in state.seeks.get(SEEKHEAD, []) if p not in done]
        for target in (INFO, TRACKS, CUES):
            if target in state.seen:
                continue
            for where in state.seeks.get(target, []):
                if where in done or not seg_data <= where < seg_end:
                    continue
                done.add(where)
                eid, data, size = _header_at(reader, where)
                if eid == target:
                    _take(state, reader, eid, data, size, video_index)
                    break
        if not more or all(t in state.seen for t in (TRACKS, CUES)):
            break
        for where in more:
            done.add(where)
            if seg_data <= where < seg_end:
                eid, data, size = _header_at(reader, where)
                if eid == SEEKHEAD:
                    _take(state, reader, eid, data, size, video_index)
    if state.track is None:
        raise ReadError("geen Tracks")
    if state.cues is None:
        raise ReadError("geen Cues")
    return _cue_times(state, seg_end)


# --- MP4 ---------------------------------------------------------------------------------------


def _box(buf: bytes, pos: int, end: int) -> tuple[str, int, int]:
    """(soort, begin van de inhoud, eind) van de doos op pos."""
    if pos + 8 > end:
        raise ReadError("doos afgekapt")
    size, kind = struct.unpack(">I4s", buf[pos : pos + 8])
    head = 8
    if size == 1:
        if pos + 16 > end:
            raise ReadError("doos afgekapt")
        size = struct.unpack(">Q", buf[pos + 8 : pos + 16])[0]
        head = 16
    elif size == 0:
        size = end - pos
    if size < head or pos + size > end:
        raise ReadError("een doos loopt voorbij zijn ouder")
    return kind.decode("latin-1"), pos + head, pos + size


def _boxes(buf: bytes, start: int, end: int) -> list[tuple[str, int, int]]:
    out = []
    pos = start
    while pos + 8 <= end:
        if len(out) >= ELEMENTS_MAX:
            raise ReadError("te veel dozen")
        kind, data, stop = _box(buf, pos, end)
        out.append((kind, data, stop))
        pos = stop
    return out


def _find(buf: bytes, start: int, end: int, *path: str) -> tuple[int, int] | None:
    for kind, data, stop in _boxes(buf, start, end):
        if kind == path[0]:
            if len(path) == 1:
                return data, stop
            return _find(buf, data, stop, *path[1:])
    return None


def _full(buf: bytes, data: int, stop: int, need: int) -> tuple[int, int]:
    """(versie, begin na versie en vlaggen) van een full box met minstens need bytes erna."""
    if data + 4 + need > stop:
        raise ReadError("full box afgekapt")
    return buf[data], data + 4


def _table(buf: bytes, data: int, stop: int, width: int, fmt: str) -> list[tuple[int, ...]]:
    """De rijen van een tabel: versie/vlaggen, aantal (u32), dan aantal × width bytes."""
    _, pos = _full(buf, data, stop, 4)
    (count,) = struct.unpack(">I", buf[pos : pos + 4])
    pos += 4
    if count * width > stop - pos:
        raise ReadError("tabel langer dan zijn doos")
    return list(struct.iter_unpack(fmt, buf[pos : pos + count * width]))


def _moov(reader: Reader) -> bytes:
    pos = 0
    for _ in range(TOP_MAX):
        if pos + 8 > reader.size:
            break
        head = reader.read(pos, 16)
        size, kind = struct.unpack(">I4s", head[:8])
        header = 8
        if size == 1:
            if len(head) < 16:
                raise ReadError("doos afgekapt")
            size = struct.unpack(">Q", head[8:16])[0]
            header = 16
        elif size == 0:
            size = reader.size - pos
        if size < header:
            raise ReadError("doos te klein")
        if kind == b"moov":
            return reader.read(pos, min(size, reader.size - pos))
        pos += size
    raise ReadError("geen moov")


def _at(
    runs: list[tuple[int, ...]], indexes: list[int], *, cumulative: bool, count: int
) -> list[int]:
    """De waarde bij elke index (oplopend) uit runs van (aantal, waarde): bij stts de som van
    de delta's ervoor (de dts), bij ctts de offset zelf."""
    out: list[int] = []
    run = 0
    first = 0  # de index van het eerste sample van deze run
    base = 0  # de dts van dat sample
    for i in indexes:
        while run < len(runs) and i >= first + runs[run][0]:
            n, value = runs[run][0], runs[run][1]
            base += n * value if cumulative else 0
            first += n
            run += 1
        if run >= len(runs):
            if cumulative and i < count:
                raise ReadError("stts telt minder samples dan stsz")
            out.append(base if cumulative else 0)
            continue
        value = runs[run][1]
        out.append(base + (i - first) * value if cumulative else value)
    return out


def _elst_shift(buf: bytes, trak: tuple[int, int], movie_scale: int, media_scale: int) -> float:
    """De verschuiving in mediatijd (eenheden van media_scale) uit de eerste edits: een lege
    edit vooraan (vertraging) en de media_time van de eerste echte edit."""
    found = _find(buf, trak[0], trak[1], "edts", "elst")
    if found is None:
        return 0.0
    version, pos = _full(buf, found[0], found[1], 4)
    (count,) = struct.unpack(">I", buf[pos : pos + 4])
    pos += 4
    width = 20 if version == 1 else 12
    fmt = ">QqHH" if version == 1 else ">IiHH"
    if count * width > found[1] - pos:
        raise ReadError("elst langer dan zijn doos")
    delay = 0.0
    for duration, media_time, _rate, _frac in struct.iter_unpack(
        fmt, buf[pos : pos + count * width]
    ):
        if media_time == -1:
            delay += duration * media_scale / max(1, movie_scale)
            continue
        return media_time - delay
    return -delay


def mp4(fd: int, video_index: int, *, budget: int = READ_MAX) -> Index | None:
    """De keyframes uit stss (met stts, ctts, elst en stsz), of None."""
    try:
        return _mp4(Reader(fd, budget), video_index)
    except (ReadError, struct.error, OverflowError, ZeroDivisionError, ValueError):
        return None


def _mp4(reader: Reader, video_index: int) -> Index:
    buf = _moov(reader)
    _, mdata, mstop = _box(buf, 0, len(buf))
    if _find(buf, mdata, mstop, "mvex") is not None:
        raise ReadError("gefragmenteerde MP4: de samples staan in de moof's")
    mvhd = _find(buf, mdata, mstop, "mvhd")
    if mvhd is None:
        raise ReadError("geen mvhd")
    version, pos = _full(buf, mvhd[0], mvhd[1], 20)
    movie_scale = struct.unpack(">I", buf[pos + (16 if version == 1 else 8) :][:4])[0]
    traks = [(d, s) for k, d, s in _boxes(buf, mdata, mstop) if k == "trak"][:TRAKS_MAX]
    if video_index >= len(traks):
        raise ReadError("geen trak met deze index")
    trak = traks[video_index]
    hdlr = _find(buf, trak[0], trak[1], "mdia", "hdlr")
    if hdlr is None or buf[hdlr[0] + 8 : hdlr[0] + 12] != b"vide":
        raise ReadError("de trak is geen video")
    mdhd = _find(buf, trak[0], trak[1], "mdia", "mdhd")
    if mdhd is None:
        raise ReadError("geen mdhd")
    version, pos = _full(buf, mdhd[0], mdhd[1], 20)
    if version == 1:
        scale, duration = struct.unpack(">IQ", buf[pos + 16 : pos + 28])
    else:
        scale, duration = struct.unpack(">II", buf[pos + 8 : pos + 16])
    if scale == 0:
        raise ReadError("timescale 0")
    stbl = _find(buf, trak[0], trak[1], "mdia", "minf", "stbl")
    if stbl is None:
        raise ReadError("geen stbl")
    tables = {k: (d, s) for k, d, s in _boxes(buf, *stbl)}
    if "stts" not in tables or "stsz" not in tables:
        raise ReadError("geen stts of stsz")
    # De grootte per sample (als array: geen Python-getal per sample)
    _, pos = _full(buf, *tables["stsz"], 8)
    fixed, count = struct.unpack(">II", buf[pos : pos + 8])
    if count > SAMPLES_MAX:
        raise ReadError("te veel samples")
    sizes = array.array("I")
    if not fixed:
        if count * 4 > tables["stsz"][1] - pos - 8:
            raise ReadError("stsz langer dan zijn doos")
        sizes.frombytes(buf[pos + 8 : pos + 8 + count * 4])
        if sys.byteorder == "little":
            sizes.byteswap()
    if "stss" in tables:
        sync = sorted({row[0] - 1 for row in _table(buf, *tables["stss"], 4, ">I")})
        sync = [s for s in sync if 0 <= s < count]
    else:
        sync = list(range(count))  # zonder stss is elk sample een sync sample
    if not sync:
        raise ReadError("geen sync samples")
    # De decodeertijd en de compositie-offset (getekend, zoals ffmpeg hem leest) per sync
    # sample, door de runs van stts en ctts te lopen
    dts = _at(_table(buf, *tables["stts"], 8, ">II"), sync, cumulative=True, count=count)
    offsets = (
        _at(_table(buf, *tables["ctts"], 8, ">Ii"), sync, cumulative=False, count=count)
        if "ctts" in tables
        else [0] * len(sync)
    )
    shift = _elst_shift(buf, trak, movie_scale, scale)
    times = [round((d + o - shift) * 1000 / scale) for d, o in zip(dts, offsets, strict=True)]
    bounds = [*sync, count]
    gop_sizes = [
        fixed * (b - a) if fixed else sum(sizes[a:b])
        for a, b in zip(bounds, bounds[1:], strict=False)
    ]
    t, s = finish(times, gop_sizes)
    end_ms = round((duration - shift) * 1000 / scale) if duration else None
    return Index(t, s, "stss", sparse=False, end_ms=end_ms)


def read(fd: int, demuxer: str, video_index: int, *, budget: int = READ_MAX) -> Index | None:
    """De eigen lezer bij deze demuxer, of None (geen lezer, of niets gevonden)."""
    if demuxer == "matroska":
        return matroska(fd, video_index, budget=budget)
    if demuxer == "mov":
        return mp4(fd, video_index, budget=budget)
    return None


# --- de opgeslagen vorm (media_probe.keyframes) -------------------------------------------------

WIRE_MAX = 48 * KiB  # base64 in het antwoord; de boodschap zelf is hooguit 64 KB (Keuze 12)
THIN_STEPS_MS = (0, 1000, 2000, 3000, 4000, 6000)
BLOB_MAX = 1 * MiB  # uitgepakt: een grens bij het lezen van een opgeslagen blob
KEYFRAMES_MAX = 4_000_000


def _put(out: bytearray, value: int) -> None:
    if value < 0:
        raise ValueError("varint < 0")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def _get(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(buf) or shift > 63:
            raise ValueError("varint afgekapt of te lang")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def _zigzag(v: int) -> int:
    return v * 2 if v >= 0 else -v * 2 - 1


def _unzigzag(v: int) -> int:
    return v // 2 if v % 2 == 0 else -(v + 1) // 2


def encode(times_ms: list[int], sizes: list[int]) -> bytes:
    """zlib(varint n, de eerste tijd (zigzag), de verschillen, dan n groottes in KiB)."""
    if len(times_ms) != len(sizes):
        raise ValueError("even veel tijden als groottes")
    raw = bytearray()
    _put(raw, len(times_ms))
    previous = 0
    for i, t in enumerate(times_ms):
        if i == 0:
            _put(raw, _zigzag(t))
        else:
            if t < previous:
                raise ValueError("de tijden lopen niet op")
            _put(raw, t - previous)
        previous = t
    for size in sizes:
        _put(raw, -(-max(0, size) // KiB))
    return zlib.compress(bytes(raw), 9)


def decode(blob: bytes) -> tuple[list[int], list[int]]:
    """(tijden in ms, groottes in bytes, afgerond op KiB) uit een opgeslagen blob. ValueError als
    hij niet klopt."""
    try:
        unzip = zlib.decompressobj()
        raw = unzip.decompress(blob, BLOB_MAX)
    except zlib.error as exc:
        raise ValueError(f"geen zlib: {exc}") from None
    if unzip.unconsumed_tail:
        raise ValueError("de blob is uitgepakt te groot")
    n, pos = _get(raw, 0)
    if n > KEYFRAMES_MAX:
        raise ValueError("te veel keyframes")
    times: list[int] = []
    now = 0
    for i in range(n):
        value, pos = _get(raw, pos)
        now = _unzigzag(value) if i == 0 else now + value
        times.append(now)
    sizes: list[int] = []
    for _ in range(n):
        value, pos = _get(raw, pos)
        sizes.append(value * KiB)
    if pos != len(raw):
        raise ValueError("bytes over na de blob")
    return times, sizes


def decode_b64(text: str) -> tuple[list[int], list[int]]:
    """decode van de vorm in het antwoord (base64). ValueError als hij niet klopt."""
    try:
        blob = base64.b64decode(text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"geen base64: {exc}") from None
    return decode(blob)


def thin(times_ms: list[int], sizes: list[int], spacing_ms: int) -> tuple[list[int], list[int]]:
    """Hooguit één keyframe per spacing_ms: de eerste blijft, een te vroege valt weg en zijn
    grootte telt bij de vorige die blijft."""
    if spacing_ms <= 0 or not times_ms:
        return list(times_ms), list(sizes)
    out_t = [times_ms[0]]
    out_s = [sizes[0]]
    for t, s in zip(times_ms[1:], sizes[1:], strict=True):
        if t - out_t[-1] >= spacing_ms:
            out_t.append(t)
            out_s.append(s)
        else:
            out_s[-1] += s
    return out_t, out_s


def for_wire(index: Index) -> dict:
    """Het antwoord van keyframes: de blob als base64 (hooguit WIRE_MAX), zo nodig uitgedund.
    Een uitgedunde index blijft ready (genoeg voor de regel van 6 s, Keuze 23); max_gop_ms komt
    uit de volle index."""
    for step in THIN_STEPS_MS:
        times, sizes = thin(index.times_ms, index.sizes, step)
        text = base64.b64encode(encode(times, sizes)).decode("ascii")
        if len(text) <= WIRE_MAX:
            return {
                "state": "sparse" if index.sparse else "ready",
                "keyframes": text,
                "count": len(times),
                "total": len(index.times_ms),
                "thinned_ms": step,
                "max_gop_ms": index.max_gop_ms(),
                "end_ms": index.end_ms,
                "source": index.source,
            }
    raise ValueError("de index past ook uitgedund niet")
