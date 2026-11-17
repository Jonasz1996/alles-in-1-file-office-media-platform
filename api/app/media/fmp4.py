"""Een begrensde knipper van fMP4 uit de pipe van de worker (Keuze 22, 23). M4.

De app leest alleen wat de worker maakte (regel 5), maar de bron van een gebruiker stuurt de
tijden en de grootte van wat ffmpeg schrijft. Daarom is alles begrensd, en geeft kapotte invoer
altijd `Fmp4Error`, nooit een andere fout, een hang of een box die het geheugen vult:

- op het hoogste niveau alleen `ftyp`, `moov`, `moof` en `mdat` (en kleine `free`, `skip`,
  `styp`, `sidx` en `prft`, die weg gaan). De grens per box kent de knipper al uit de kop:
  init (`ftyp` + `moov`) ≤ 1 MB, `moof` ≤ 4 MB, `mdat` ≤ 64 MB;
- `size` 0 (tot het eind van het bestand) bestaat in een pipe niet: fout. `size` 1 is een
  64-bits grootte, met dezelfde grens;
- binnen een box moet elk kind passen; een `trun` met meer samples dan zijn bytes kunnen dragen
  is een fout.

De tijden (Keuze 22): `tfdt` is mediatijd + 1 s (`-output_ts_offset 1 − S`). De pts van een
sample is `tfdt` plus de duren ervoor plus de compositie-offset, getekend bij `trun` versie 1
(`+negative_cts_offsets`). Alles in ms mediatijd, afgerond op een hele ms (zoals de lezer van
de tests, `tests/media/boxes.py`).

**De generatie van een init** (Keuze 22, spike punt 13). `Init.key` is een sha256 over de
sporen: id, soort (`hdlr`), tijdschaal en de hele `stsd`. Twee runs met dezelfde sleutel geven
segmenten die met dezelfde init te decoderen zijn; een andere sleutel is een nieuwe generatie.
De andere boxen van `moov` tellen niet mee (`mvhd` en `tkhd` veranderen niets aan het
decoderen).
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field

KiB = 1024
MiB = 1024 * KiB

INIT_MAX = 1 * MiB  # ftyp + moov samen
MOOF_MAX = 4 * MiB
MDAT_MAX = 64 * MiB
SMALL_MAX = 64 * KiB  # ftyp en wat weg mag
TOP_LIMITS: dict[str, int] = {
    "ftyp": SMALL_MAX,
    "moov": INIT_MAX,
    "moof": MOOF_MAX,
    "mdat": MDAT_MAX,
}
IGNORED = frozenset({"free", "skip", "styp", "sidx", "prft"})
NON_SYNC = 0x00010000  # sample_is_non_sync_sample
OFFSET_MS = 1000  # tfdt = mediatijd + 1 s
MAX_TRACKS = 16
MAX_SAMPLES = 1_000_000  # per trun, als de samples geen eigen velden hebben
VIDEO_ENTRIES = frozenset({"avc1", "avc3", "hvc1", "hev1", "vp09", "av01", "dvh1", "dvhe"})

Buffer = bytes | bytearray | memoryview


class Fmp4Error(ValueError):
    """De bytes zijn geen geldige stroom van de worker."""


# --- boxen ------------------------------------------------------------------------------------


def _header(buf: Buffer, pos: int, end: int) -> tuple[str, int, int]:
    """(soort, grootte, kop) van de box op pos; Fmp4Error als de kop niet past."""
    if pos + 8 > end:
        raise Fmp4Error(f"halve kop op {pos}")
    size, raw = struct.unpack_from(">I4s", buf, pos)
    kind = bytes(raw).decode("latin-1")
    head = 8
    if size == 1:
        if pos + 16 > end:
            raise Fmp4Error(f"halve kop op {pos}")
        size = struct.unpack_from(">Q", buf, pos + 8)[0]
        head = 16
    elif size == 0:
        raise Fmp4Error(f"box {kind!r} zonder grootte")
    if size < head:
        raise Fmp4Error(f"box {kind!r} van {size} bytes is kleiner dan zijn kop")
    return kind, size, head


def children(buf: Buffer, start: int, end: int) -> Iterator[tuple[str, int, int]]:
    """(soort, begin van de inhoud, eind) per kind in buf[start:end]; elk kind moet passen."""
    pos = start
    while pos < end:
        kind, size, head = _header(buf, pos, end)
        if pos + size > end:
            raise Fmp4Error(f"box {kind!r} van {size} bytes past niet in zijn ouder")
        yield kind, pos + head, pos + size
        pos += size


def find(buf: Buffer, start: int, end: int, *path: str) -> tuple[int, int] | None:
    """De inhoud (begin, eind) van de eerste box langs het pad, of None."""
    found: tuple[int, int] | None = None
    for name in path:
        for kind, s, e in children(buf, start, end):
            if kind == name:
                found = (s, e)
                start, end = s, e
                break
        else:
            return None
    return found


def _need(pos: int, n: int, end: int) -> None:
    if pos + n > end:
        raise Fmp4Error(f"box te kort op {pos}")


def _u32(buf: Buffer, pos: int, end: int) -> int:
    _need(pos, 4, end)
    return struct.unpack_from(">I", buf, pos)[0]


# --- de init ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Track:
    id: int
    handler: str  # vide | soun | …
    timescale: int
    entry: str  # avc1, vp09, mp4a, Opus, …
    stsd: bytes
    width: int = 0
    height: int = 0
    trex: tuple[int, int, int] = (0, 0, 0)  # standaard duur, grootte, vlaggen


@dataclass(frozen=True)
class Init:
    data: bytes
    tracks: dict[int, Track]
    key: str  # de sleutel van de generatie

    @property
    def video(self) -> Track | None:
        return next((t for t in self.tracks.values() if t.handler == "vide"), None)

    @property
    def audio(self) -> Track | None:
        return next((t for t in self.tracks.values() if t.handler == "soun"), None)


def gen_key(tracks: dict[int, Track]) -> str:
    """De sleutel van een generatie: id, soort, tijdschaal en `stsd` per spoor."""
    digest = hashlib.sha256()
    for tid in sorted(tracks):
        t = tracks[tid]
        digest.update(struct.pack(">II", t.id, t.timescale))
        digest.update(t.handler.encode("latin-1"))
        digest.update(struct.pack(">I", len(t.stsd)))
        digest.update(t.stsd)
    return digest.hexdigest()


def _version_skip(buf: Buffer, s: int, e: int) -> int:
    """De plek van het veld na de tijden in tkhd en mdhd: versie 1 heeft 64-bits tijden."""
    _need(s, 1, e)
    return s + 4 + (16 if buf[s] == 1 else 8)


def _track(buf: bytes, start: int, end: int) -> Track:
    tkhd = find(buf, start, end, "tkhd")
    if tkhd is None:
        raise Fmp4Error("trak zonder tkhd")
    tid = _u32(buf, _version_skip(buf, *tkhd), tkhd[1])
    mdhd = find(buf, start, end, "mdia", "mdhd")
    if mdhd is None:
        raise Fmp4Error("trak zonder mdhd")
    timescale = _u32(buf, _version_skip(buf, *mdhd), mdhd[1])
    if timescale == 0:
        raise Fmp4Error("tijdschaal 0")
    hdlr = find(buf, start, end, "mdia", "hdlr")
    if hdlr is None:
        raise Fmp4Error("trak zonder hdlr")
    _need(hdlr[0], 12, hdlr[1])
    handler = buf[hdlr[0] + 8 : hdlr[0] + 12].decode("latin-1")
    stsd = find(buf, start, end, "mdia", "minf", "stbl", "stsd")
    if stsd is None:
        raise Fmp4Error("trak zonder stsd")
    s, e = stsd
    _need(s, 8, e)
    entry, width, height = "", 0, 0
    for kind, es, ee in children(buf, s + 8, e):
        entry = kind
        if kind in VIDEO_ENTRIES:
            _need(es, 28, ee)
            width, height = struct.unpack_from(">HH", buf, es + 24)
        break
    # De hele box met zijn kop (8 bytes vóór de inhoud): die bepaalt de generatie
    return Track(tid, handler, timescale, entry, buf[s - 8 : e], width, height)


def parse_init(data: bytes) -> Init:
    """`ftyp` + `moov` (en wat weg mag) als Init. Fmp4Error als de vorm niet klopt."""
    data = bytes(data)
    if len(data) > INIT_MAX:
        raise Fmp4Error("init groter dan 1 MB")
    moov: tuple[int, int] | None = None
    for kind, s, e in children(data, 0, len(data)):
        if kind == "moov" and moov is None:
            moov = (s, e)
        elif kind != "ftyp" and kind not in IGNORED:
            raise Fmp4Error(f"onverwachte box {kind!r} in de init")
    if moov is None:
        raise Fmp4Error("geen moov")
    s, e = moov
    tracks: dict[int, Track] = {}
    for kind, ts, te in children(data, s, e):
        if kind != "trak":
            continue
        if len(tracks) >= MAX_TRACKS:
            raise Fmp4Error("te veel sporen")
        t = _track(data, ts, te)
        if t.id in tracks:
            raise Fmp4Error(f"spoor {t.id} twee keer")
        tracks[t.id] = t
    if not tracks:
        raise Fmp4Error("geen sporen")
    mvex = find(data, s, e, "mvex")
    if mvex is not None:
        for kind, ks, ke in children(data, *mvex):
            if kind != "trex":
                continue
            _need(ks, 24, ke)
            tid = _u32(data, ks + 4, ke)
            if tid in tracks:
                dur, size, flags = struct.unpack_from(">III", data, ks + 12)
                t = tracks[tid]
                tracks[tid] = Track(
                    t.id, t.handler, t.timescale, t.entry, t.stsd, t.width, t.height,
                    (dur, size, flags),
                )  # fmt: skip
    return Init(data, tracks, gen_key(tracks))


# --- een fragment -----------------------------------------------------------------------------


@dataclass
class Run:
    """Eén `traf`: het spoor, `tfdt`, en wat de app van de samples wil weten."""

    track: int
    tfdt: int = 0
    versions: list[int] = field(default_factory=list)  # de versie van elke trun
    count: int = 0
    first_flags: int = 0
    first_cto: int = 0
    duration: int = 0  # som van de duren (dts-eind − tfdt)
    max_pts: int | None = None
    size: int = 0  # som van de groottes


@dataclass(frozen=True)
class Fragment:
    """Eén `moof` + `mdat`, met de tijden in ms mediatijd. Zonder beeld zijn de velden van de
    video None (een staart van alleen audio)."""

    data: bytes
    video_start_ms: int | None  # pts van het eerste videosample (tfdt + cto)
    video_end_ms: int | None  # dts-eind van het laatste videosample
    video_last_ms: int | None  # de grootste pts
    video_cto_ms: int  # de compositie-offset van het eerste videosample
    sync: bool  # het eerste videosample is een keyframe
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None
    trun_version: int = 0


def media_ms(value: int, timescale: int) -> int:
    """Een tijd in de tijdschaal als ms mediatijd (zonder de 1 s van tfdt), afgerond."""
    return round(value * 1000 / timescale) - OFFSET_MS


def _tfhd(buf: bytes, s: int, e: int, trex: tuple[int, int, int]) -> tuple[int, int, int]:
    """De standaardwaarden (duur, grootte, vlaggen) uit `tfhd`, anders uit `trex`."""
    _need(s, 8, e)
    flags = int.from_bytes(buf[s + 1 : s + 4], "big")
    d_dur, d_size, d_flags = trex
    p = s + 8
    if flags & 0x1:
        p += 8
    if flags & 0x2:
        p += 4
    if flags & 0x8:
        d_dur = _u32(buf, p, e)
        p += 4
    if flags & 0x10:
        d_size = _u32(buf, p, e)
        p += 4
    if flags & 0x20:
        d_flags = _u32(buf, p, e)
        p += 4
    if p > e:
        raise Fmp4Error("tfhd te kort")
    return d_dur, d_size, d_flags


def _trun(buf: bytes, s: int, e: int, run: Run, defaults: tuple[int, int, int]) -> None:
    d_dur, d_size, d_flags = defaults
    _need(s, 8, e)
    version = buf[s]
    tflags = int.from_bytes(buf[s + 1 : s + 4], "big")
    n = _u32(buf, s + 4, e)
    q = s + 8
    if tflags & 0x1:
        q += 4
    first: int | None = None
    if tflags & 0x4:
        first = _u32(buf, q, e)
        q += 4
    has = [bool(tflags & bit) for bit in (0x100, 0x200, 0x400, 0x800)]
    width = 4 * sum(has)
    if width:
        if q + n * width > e:
            raise Fmp4Error(f"trun met {n} samples past niet in zijn box")
    elif n > MAX_SAMPLES:
        raise Fmp4Error(f"trun met {n} samples zonder velden")
    elif q > e:
        raise Fmp4Error("trun te kort")
    run.versions.append(version)
    if n == 0:
        return
    dts = run.tfdt + run.duration
    if not width:
        # Alle samples hebben de standaardwaarden: geen lus nodig
        if run.count == 0:
            run.first_flags = first if first is not None else d_flags
            run.first_cto = 0
        last_pts = dts + (n - 1) * d_dur
        run.max_pts = last_pts if run.max_pts is None else max(run.max_pts, last_pts)
        run.duration += n * d_dur
        run.size += n * d_size
        run.count += n
        return
    has_dur, has_size, has_flags, has_cto = has
    codes = ("I" if has_dur else "") + ("I" if has_size else "") + ("I" if has_flags else "")
    codes += ("i" if version == 1 else "I") if has_cto else ""
    view = memoryview(buf)[q : q + n * width]
    for i, values in enumerate(struct.iter_unpack(">" + codes, view)):
        j = 0
        duration, size, flags, cto = d_dur, d_size, d_flags, 0
        if has_dur:
            duration = values[j]
            j += 1
        if has_size:
            size = values[j]
            j += 1
        if has_flags:
            flags = values[j]
            j += 1
        elif i == 0 and first is not None:
            flags = first
        if has_cto:
            cto = values[j]
        if run.count == 0 and i == 0:
            run.first_flags = flags
            run.first_cto = cto
        pts = dts + cto
        run.max_pts = pts if run.max_pts is None else max(run.max_pts, pts)
        dts += duration
        run.size += size
    run.duration = dts - run.tfdt
    run.count += n


def read_moof(moof: bytes, init: Init) -> list[Run]:
    """De `traf`'s van één `moof` (met kop)."""
    kind, size, head = _header(moof, 0, len(moof))
    if kind != "moof" or size != len(moof):
        raise Fmp4Error("geen moof")
    runs: list[Run] = []
    for sub, ts, te in children(moof, head, size):
        if sub != "traf":
            continue
        if len(runs) >= MAX_TRACKS:
            raise Fmp4Error("te veel traf's")
        tfhd = find(moof, ts, te, "tfhd")
        if tfhd is None:
            raise Fmp4Error("traf zonder tfhd")
        tid = _u32(moof, tfhd[0] + 4, tfhd[1])
        track = init.tracks.get(tid)
        if track is None:
            raise Fmp4Error(f"traf voor onbekend spoor {tid}")
        defaults = _tfhd(moof, *tfhd, track.trex)
        run = Run(track=tid)
        tfdt = find(moof, ts, te, "tfdt")
        if tfdt is not None:
            s, e = tfdt
            _need(s, 1, e)
            if moof[s] == 1:
                _need(s + 4, 8, e)
                run.tfdt = struct.unpack_from(">Q", moof, s + 4)[0]
            else:
                run.tfdt = _u32(moof, s + 4, e)
        for name, s, e in children(moof, ts, te):
            if name == "trun":
                _trun(moof, s, e, run, defaults)
        runs.append(run)
    return runs


def parse_fragment(moof: bytes, mdat: bytes, init: Init) -> Fragment:
    """Eén fragment (`moof` en `mdat`, elk met kop) met de tijden van beeld en geluid."""
    runs = read_moof(moof, init)
    video = audio = None
    for run in runs:
        handler = init.tracks[run.track].handler
        if handler == "vide" and video is None and run.count:
            video = run
        elif handler == "soun" and audio is None and run.count:
            audio = run
    v_start = v_end = v_last = None
    v_cto = 0
    sync = False
    version = 0
    if video is not None:
        scale = init.tracks[video.track].timescale
        v_start = media_ms(video.tfdt + video.first_cto, scale)
        v_end = media_ms(video.tfdt + video.duration, scale)
        v_last = media_ms(video.max_pts if video.max_pts is not None else video.tfdt, scale)
        v_cto = round(video.first_cto * 1000 / scale)
        sync = not (video.first_flags & NON_SYNC)
        version = max(video.versions, default=0)
    a_start = a_end = None
    if audio is not None:
        scale = init.tracks[audio.track].timescale
        a_start = media_ms(audio.tfdt + audio.first_cto, scale)
        a_end = media_ms(audio.tfdt + audio.duration, scale)
    return Fragment(
        data=moof + mdat,
        video_start_ms=v_start,
        video_end_ms=v_end,
        video_last_ms=v_last,
        video_cto_ms=v_cto,
        sync=sync,
        audio_start_ms=a_start,
        audio_end_ms=a_end,
        trun_version=version,
    )


# --- de knipper -------------------------------------------------------------------------------


class Cutter:
    """Knipt de pipe van de worker in stukken: eerst één Init, dan Fragments. Krijgt bytes in
    willekeurige stukken (`feed`), en weet bij `close` of de stroom heel eindigde. De grens
    van een box geldt al bij de kop: een box van 4 GB komt nooit in het geheugen."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._box: tuple[str, int] | None = None  # de box die nu binnenkomt: soort, grootte
        self._init_parts: list[bytes] = []
        self._init_size = 0
        self.init: Init | None = None
        self._moof: bytes | None = None
        self.received = 0

    def feed(self, data: Buffer) -> list[Init | Fragment]:
        self.received += len(data)
        self._buf += data
        out: list[Init | Fragment] = []
        while True:
            box = self._next_box()
            if box is None:
                return out
            item = self._take(*box)
            if item is not None:
                out.append(item)

    def close(self) -> None:
        """Het eind van de stroom: Fmp4Error als er een halve box, een moof zonder mdat of
        geen init overblijft."""
        if self._buf or self._box is not None:
            raise Fmp4Error("de stroom eindigt midden in een box")
        if self._moof is not None:
            raise Fmp4Error("de stroom eindigt na een moof zonder mdat")
        if self.init is None:
            raise Fmp4Error("de stroom eindigt zonder init")

    @property
    def pending(self) -> int:
        """Bytes in de buffer die nog geen hele box zijn."""
        return len(self._buf)

    def _next_box(self) -> tuple[str, bytes] | None:
        if self._box is None:
            if len(self._buf) < 8:
                return None
            if struct.unpack_from(">I", self._buf, 0)[0] == 1 and len(self._buf) < 16:
                return None
            kind, size, _head = _header(self._buf, 0, len(self._buf))
            limit = TOP_LIMITS.get(kind, SMALL_MAX if kind in IGNORED else 0)
            if limit == 0:
                raise Fmp4Error(f"onverwachte box {kind!r}")
            if size > limit:
                raise Fmp4Error(f"box {kind!r} van {size} bytes is groter dan {limit}")
            self._box = (kind, size)
        kind, size = self._box
        if len(self._buf) < size:
            return None
        data = bytes(self._buf[:size])
        del self._buf[:size]
        self._box = None
        return kind, data

    def _take(self, kind: str, data: bytes) -> Init | Fragment | None:
        if kind in IGNORED:
            return None
        if self.init is None:
            if kind not in ("ftyp", "moov"):
                raise Fmp4Error(f"{kind!r} vóór de init")
            self._init_size += len(data)
            if self._init_size > INIT_MAX:
                raise Fmp4Error("init groter dan 1 MB")
            self._init_parts.append(data)
            if kind == "moov":
                self.init = parse_init(b"".join(self._init_parts))
                self._init_parts = []
                return self.init
            return None
        if kind == "moof":
            if self._moof is not None:
                raise Fmp4Error("twee moof's na elkaar")
            self._moof = data
            return None
        if kind == "mdat":
            if self._moof is None:
                raise Fmp4Error("mdat zonder moof")
            moof, self._moof = self._moof, None
            return parse_fragment(moof, data, self.init)
        raise Fmp4Error(f"{kind!r} na de init")
