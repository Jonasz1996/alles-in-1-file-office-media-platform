"""De fMP4-uitvoer van de worker (ops/live.py) knippen: eerst de init (ftyp + moov), dan
fragmenten (moof + mdat). Met `+frag_keyframe` begint elk fragment met een keyframe: een nieuwe
kijker krijgt de init en dan het laatste fragment, en speelt meteen.

codecs() leest uit de init de codecs voor MediaSource (`avc1.64001f`, `mp4a.40.2`), en
video_codec() de soort beeld (avc1, hev1, mp4v …): is dat geen H.264, dan zet de tuner het beeld
om (een browser speelt MPEG-2 en vaak ook HEVC niet).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAX_BOX = 64 * 1024 * 1024
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
VIDEO = {b"avc1", b"avc3", b"hvc1", b"hev1", b"mp4v", b"vp09", b"av01"}
AUDIO = {b"mp4a", b"Opus", b"ac-3", b"ec-3"}


class BoxError(ValueError):
    pass


@dataclass
class Splitter:
    """Bytes erin (feed), en eruit: de init één keer, daarna fragmenten."""

    buffer: bytearray = field(default_factory=bytearray)
    init: bytes | None = None
    _init_parts: list[bytes] = field(default_factory=list)
    _moof: bytes | None = None

    def feed(self, data: bytes) -> list[bytes]:
        """Geeft de nieuwe fragmenten; zet init zodra de moov binnen is."""
        self.buffer += data
        out: list[bytes] = []
        while True:
            box = self._next()
            if box is None:
                return out
            kind = box[4:8]
            if self.init is None:
                self._init_parts.append(box)
                if kind == b"moov":
                    self.init = b"".join(self._init_parts)
                    self._init_parts = []
                continue
            if kind == b"moof":
                self._moof = box
            elif kind == b"mdat" and self._moof is not None:
                out.append(self._moof + box)
                self._moof = None
            # styp, sidx, free …: overslaan

    def _next(self) -> bytes | None:
        if len(self.buffer) < 8:
            return None
        size, kind = struct.unpack(">I4s", self.buffer[:8])
        header = 8
        if size == 1:
            if len(self.buffer) < 16:
                return None
            size = struct.unpack(">Q", self.buffer[8:16])[0]
            header = 16
        if size == 0 or size < header or size > MAX_BOX:
            raise BoxError(f"ongeldige box {kind!r} ({size} bytes)")
        if len(self.buffer) < size:
            return None
        box = bytes(self.buffer[:size])
        del self.buffer[:size]
        return box


def _boxes(data: bytes, start: int, end: int):
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack(">I4s", data[i : i + 8])
        header = 8
        if size == 1 and i + 16 <= end:
            size = struct.unpack(">Q", data[i + 8 : i + 16])[0]
            header = 16
        if size < header or i + size > end:
            return
        yield kind, i + header, i + size
        i += size


def _sample_entries(init: bytes):
    """(4cc, begin, eind) van elke sample entry in elke stsd."""

    def walk(start: int, end: int):
        for kind, body, stop in _boxes(init, start, end):
            if kind in _CONTAINERS:
                yield from walk(body, stop)
            elif kind == b"stsd":
                # versie+vlaggen (4), aantal (4), dan de entries
                yield from _boxes(init, body + 8, stop)

    yield from walk(0, len(init))


def video_codec(init: bytes) -> str | None:
    for kind, _, _ in _sample_entries(init):
        if kind in VIDEO:
            return kind.decode("latin-1")
    return None


def _avc(init: bytes, body: int, end: int, kind: bytes) -> str:
    # VisualSampleEntry: 78 bytes na de kop, dan de onderliggende boxen (avcC)
    for child, cbody, _ in _boxes(init, body + 78, end):
        if child == b"avcC" and cbody + 4 <= end:
            profile, compat, level = init[cbody + 1], init[cbody + 2], init[cbody + 3]
            return f"{kind.decode()}.{profile:02x}{compat:02x}{level:02x}"
    return f"{kind.decode()}.640028"


def _mp4a(init: bytes, body: int, end: int) -> str:
    # AudioSampleEntry: 28 bytes, dan esds; het object type staat in de DecoderConfigDescriptor
    for child, cbody, cend in _boxes(init, body + 28, end):
        if child != b"esds":
            continue
        data = init[cbody + 4 : cend]
        oti = None
        aot = None
        i = 0
        while i < len(data):
            tag = data[i]
            i += 1
            length = 0
            for _ in range(4):
                if i >= len(data):
                    break
                b = data[i]
                i += 1
                length = (length << 7) | (b & 0x7F)
                if not b & 0x80:
                    break
            if tag == 0x03:
                i += 3  # ES_ID (2) en vlaggen (1); geen URL of afhankelijkheid bij ffmpeg
                continue
            if tag == 0x04:
                oti = data[i] if i < len(data) else None
                i += 13
                continue
            if tag == 0x05 and i < len(data):
                aot = data[i] >> 3
                break
            i += length
        if oti == 0x40:
            return f"mp4a.40.{aot or 2}"
        if oti is not None:
            return f"mp4a.{oti:02x}"
    return "mp4a.40.2"


def codecs(init: bytes) -> str:
    """De codecs voor `video/mp4; codecs="…"`."""
    out: list[str] = []
    for kind, body, end in _sample_entries(init):
        if kind in (b"avc1", b"avc3"):
            out.append(_avc(init, body, end, kind))
        elif kind == b"mp4a":
            out.append(_mp4a(init, body, end))
        elif kind == b"Opus":
            out.append("opus")
        elif kind in VIDEO or kind in AUDIO:
            out.append(kind.decode("latin-1"))
    return ",".join(out)
