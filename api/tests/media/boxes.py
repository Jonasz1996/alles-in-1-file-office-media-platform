"""Een kleine lezer van fMP4 voor de tests van media (fase 5, §3.6).

Niet `app/media/fmp4.py`: die knipper bouwt M4 voor de app. Deze lezer is voor het harnas: de
nep-worker en de nep-stream knippen er hun vooraf gemaakte fMP4 mee in een init en fragmenten,
en de contracten (`worker_contract.py`, `media_contract.py`) lezen er de tijden van een
fragment of segment mee na. Alleen de standaardbibliotheek; begrensd (de grootte van elke box
moet in de buffer passen), en een kapotte stroom geeft BoxError, nooit iets anders.

Tijden (Keuze 22): `tfdt` = mediatijd + 1 s. De pts van een sample is `tfdt` plus de duren
ervoor plus de compositie-offset, getekend bij `trun` versie 1 (`+negative_cts_offsets`).
`media_ms` rekent dat om naar ms mediatijd (afgerond op een hele ms).
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field

MAX_BOX = 256 * 1024 * 1024
NON_SYNC = 0x00010000  # sample_is_non_sync_sample
OFFSET_S = 1.0  # tfdt = mediatijd + 1 s (-output_ts_offset 1 − S)
VIDEO_ENTRIES = frozenset({"avc1", "avc3", "hvc1", "hev1", "vp09", "av01"})


class BoxError(ValueError):
    """De bytes zijn geen geldige reeks boxen."""


def boxes(buf: bytes | memoryview, start: int = 0, end: int | None = None) -> Iterator[tuple]:
    """(type, begin, begin van de inhoud, eind) per box in buf[start:end]. BoxError als een box
    niet past."""
    end = len(buf) if end is None else end
    pos = start
    while pos < end:
        if pos + 8 > end:
            raise BoxError(f"halve kop op {pos}")
        size, kind = struct.unpack_from(">I4s", buf, pos)
        head = 8
        if size == 1:
            if pos + 16 > end:
                raise BoxError(f"halve kop op {pos}")
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or size > MAX_BOX or pos + size > end:
            raise BoxError(f"box {kind!r} van {size} bytes op {pos} past niet")
        yield kind.decode("latin-1"), pos, pos + head, pos + size
        pos += size


def find(buf: bytes, start: int, end: int, *path: str) -> tuple[int, int, int] | None:
    """De eerste box langs het pad: (begin, inhoud, eind), of None."""
    found = None
    for name in path:
        for kind, b, s, e in boxes(buf, start, end):
            if kind == name:
                found = (b, s, e)
                start, end = s, e
                break
        else:
            return None
    return found


def top_level(data: bytes) -> list[tuple[str, bytes]]:
    """De boxen op het hoogste niveau, met hun bytes."""
    return [(kind, data[b:e]) for kind, b, _s, e in boxes(data)]


@dataclass
class Track:
    id: int
    handler: str = ""  # vide | soun
    timescale: int = 0
    entry: str = ""  # avc1, vp09, mp4a, Opus, …
    width: int = 0
    height: int = 0
    stsd: bytes = b""
    codec: str | None = None  # RFC 6381
    trex: tuple[int, int, int] = (0, 0, 0)  # standaard duur, grootte, vlaggen


def _u32(buf: bytes, pos: int) -> int:
    return struct.unpack_from(">I", buf, pos)[0]


def _codec(entry: str, buf: bytes, start: int, end: int) -> str | None:
    """De codec-string uit de configuratie in de sample entry (Keuze 16, in het klein)."""
    for kind, _b, s, _e in boxes(buf, start, end):
        if kind == "avcC" and entry in ("avc1", "avc3"):
            return f"{entry}.{buf[s + 1]:02x}{buf[s + 2]:02x}{buf[s + 3]:02x}"
        if kind == "vpcC":
            profile, level, depth = buf[s + 4], buf[s + 5], buf[s + 6] >> 4
            return f"vp09.{profile:02d}.{level:02d}.{depth:02d}"
        if kind == "esds":
            return "mp4a.40.2"  # de AAC-encoder van ffmpeg: AAC-LC
        if kind == "dOps":
            return "opus"
    return None


def read_init(data: bytes) -> dict[int, Track]:
    """De sporen uit een init (`ftyp` + `moov`)."""
    moov = find(data, 0, len(data), "moov")
    if moov is None:
        raise BoxError("geen moov")
    _b, s, e = moov
    tracks: dict[int, Track] = {}
    for kind, _tb, ts, te in boxes(data, s, e):
        if kind != "trak":
            continue
        tkhd = find(data, ts, te, "tkhd")
        if tkhd is None:
            raise BoxError("trak zonder tkhd")
        v = data[tkhd[1]]
        track = Track(id=_u32(data, tkhd[1] + 4 + (16 if v == 1 else 8)))
        mdhd = find(data, ts, te, "mdia", "mdhd")
        if mdhd:
            v = data[mdhd[1]]
            track.timescale = _u32(data, mdhd[1] + 4 + (16 if v == 1 else 8))
        hdlr = find(data, ts, te, "mdia", "hdlr")
        if hdlr:
            track.handler = data[hdlr[1] + 8 : hdlr[1] + 12].decode("latin-1")
        stsd = find(data, ts, te, "mdia", "minf", "stbl", "stsd")
        if stsd:
            track.stsd = bytes(data[stsd[0] : stsd[2]])
            for entry, _eb, es, ee in boxes(data, stsd[1] + 8, stsd[2]):
                track.entry = entry
                fixed = 78 if entry in VIDEO_ENTRIES else 28
                if entry in VIDEO_ENTRIES:
                    track.width, track.height = struct.unpack_from(">HH", data, es + 24)
                track.codec = _codec(entry, data, es + fixed, ee)
                break
        tracks[track.id] = track
    mvex = find(data, s, e, "mvex")
    if mvex:
        for kind, _b2, ks, _ke in boxes(data, mvex[1], mvex[2]):
            if kind == "trex":
                tid = _u32(data, ks + 4)
                if tid in tracks:
                    tracks[tid].trex = struct.unpack_from(">III", data, ks + 12)
    return tracks


@dataclass
class Run:
    """Eén `traf`: het spoor, `tfdt`, de versie van `trun`, en per sample (duur, vlaggen, cto)."""

    track: int
    tfdt: int = 0
    trun_version: int = 0
    samples: list[tuple[int, int, int]] = field(default_factory=list)

    def pts(self) -> list[int]:
        """De pts van elk sample in de tijdschaal van het spoor."""
        out, dts = [], self.tfdt
        for duration, _flags, cto in self.samples:
            out.append(dts + cto)
            dts += duration
        return out

    @property
    def end(self) -> int:
        """Het eind van het laatste sample (dts), in de tijdschaal."""
        return self.tfdt + sum(d for d, _f, _c in self.samples)

    @property
    def sync(self) -> bool:
        return bool(self.samples) and not (self.samples[0][1] & NON_SYNC)


def read_moof(data: bytes, tracks: dict[int, Track]) -> list[Run]:
    """De `traf`'s van één `moof` (met kop)."""
    out: list[Run] = []
    top = next(boxes(data), None)
    if top is None or top[0] != "moof":
        raise BoxError("geen moof")
    for kind, _b, ts, te in boxes(data, top[2], top[3]):
        if kind != "traf":
            continue
        tfhd = find(data, ts, te, "tfhd")
        if tfhd is None:
            raise BoxError("traf zonder tfhd")
        p = tfhd[1]
        flags = int.from_bytes(data[p + 1 : p + 4], "big")
        run = Run(track=_u32(data, p + 4))
        trex = tracks[run.track].trex if run.track in tracks else (0, 0, 0)
        d_dur, _d_size, d_flags = trex
        p += 8
        if flags & 0x1:
            p += 8
        if flags & 0x2:
            p += 4
        if flags & 0x8:
            d_dur = _u32(data, p)
            p += 4
        if flags & 0x10:
            p += 4
        if flags & 0x20:
            d_flags = _u32(data, p)
        for sub, _sb, ss, _se in boxes(data, ts, te):
            if sub == "tfdt":
                v = data[ss]
                run.tfdt = (
                    struct.unpack_from(">Q", data, ss + 4)[0] if v == 1 else _u32(data, ss + 4)
                )
            elif sub == "trun":
                v = data[ss]
                run.trun_version = v
                tflags = int.from_bytes(data[ss + 1 : ss + 4], "big")
                n = _u32(data, ss + 4)
                q = ss + 8
                if tflags & 0x1:
                    q += 4
                first = None
                if tflags & 0x4:
                    first = _u32(data, q)
                    q += 4
                for i in range(n):
                    duration, sflags, cto = d_dur, d_flags, 0
                    if tflags & 0x100:
                        duration = _u32(data, q)
                        q += 4
                    if tflags & 0x200:
                        q += 4
                    if tflags & 0x400:
                        sflags = _u32(data, q)
                        q += 4
                    elif i == 0 and first is not None:
                        sflags = first
                    if tflags & 0x800:
                        cto = struct.unpack_from(">i" if v == 1 else ">I", data, q)[0]
                        q += 4
                    run.samples.append((duration, sflags, cto))
        out.append(run)
    return out


def media_ms(value: int, timescale: int) -> int:
    """Een tijd uit `tfdt` of een pts als ms mediatijd (zonder de 1 s), afgerond."""
    return round(value * 1000 / timescale - OFFSET_S * 1000)


@dataclass
class Fragment:
    """Eén `moof` + `mdat` uit de uitvoer van stream, met de tijden van het beeld (ms mediatijd)."""

    data: bytes
    video_start_ms: int  # pts van het eerste videosample
    video_last_ms: int  # pts van het laatste (grootste) videosample
    end_ms: int  # het eind van het laatste videosample (dts + duur)
    sync: bool  # het eerste videosample is een keyframe
    audio_start_ms: int | None = None


@dataclass
class Parsed:
    init: bytes
    tracks: dict[int, Track]
    fragments: list[Fragment]

    @property
    def video(self) -> Track:
        return next(t for t in self.tracks.values() if t.handler == "vide")

    @property
    def audio(self) -> Track | None:
        return next((t for t in self.tracks.values() if t.handler == "soun"), None)

    def stsd(self) -> dict[str, bytes]:
        """De `stsd` per soort spoor: de vergelijking van Keuze 22 (spike S5)."""
        return {t.handler: t.stsd for t in self.tracks.values()}

    def mime(self) -> str:
        codecs = [self.video.codec or "?"]
        if self.audio is not None:
            codecs.append(self.audio.codec or "?")
        return f'video/mp4; codecs="{",".join(codecs)}"'


def parse(data: bytes) -> Parsed:
    """Een hele uitvoer van stream: `ftyp` `moov` (`moof` `mdat`)×N. BoxError als de vorm niet
    klopt (D2)."""
    parts = top_level(data)
    kinds = [k for k, _ in parts]
    if kinds[:2] != ["ftyp", "moov"]:
        raise BoxError(f"begint met {kinds[:2]}, niet ftyp moov")
    init = parts[0][1] + parts[1][1]
    tracks = read_init(init)
    if not any(t.handler == "vide" for t in tracks.values()):
        raise BoxError("geen videospoor")
    rest = parts[2:]
    fragments: list[Fragment] = []
    for i in range(0, len(rest), 2):
        if rest[i][0] != "moof" or i + 1 >= len(rest) or rest[i + 1][0] != "mdat":
            raise BoxError(f"fragment {i // 2}: geen moof gevolgd door mdat")
        moof, mdat = rest[i][1], rest[i + 1][1]
        fragments.append(fragment(moof, mdat, tracks))
    return Parsed(init, tracks, fragments)


def fragment(moof: bytes, mdat: bytes, tracks: dict[int, Track]) -> Fragment:
    runs = read_moof(moof, tracks)
    video = next((r for r in runs if tracks.get(r.track, Track(0)).handler == "vide"), None)
    if video is None or not video.samples:
        raise BoxError("fragment zonder beeld")
    scale = tracks[video.track].timescale
    pts = video.pts()
    audio = next((r for r in runs if tracks.get(r.track, Track(0)).handler == "soun"), None)
    audio_start = None
    if audio is not None and audio.samples:
        audio_start = media_ms(audio.pts()[0], tracks[audio.track].timescale)
    return Fragment(
        data=moof + mdat,
        video_start_ms=media_ms(pts[0], scale),
        video_last_ms=media_ms(max(pts), scale),
        end_ms=media_ms(video.end, scale),
        sync=video.sync,
        audio_start_ms=audio_start,
    )
