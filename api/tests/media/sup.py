"""Een eigen schrijver voor PGS-ondertitels (.sup), voor de fixtures (bouwontwerp fase 5, §3.6).

ffmpeg heeft geen PGS-encoder, wel een demuxer voor `.sup` die kan kopiëren naar mkv. Deze
schrijver maakt per ondertitel een display set (PCS met epoch start, WDS, PDS, ODS, END), en op
het eindtijdstip een lege set (PCS zonder objecten, WDS, END), zodat het beeld leeg wordt.

Het beeld is tekst in een eigen letter van 5×7 (geen fonts nodig): wit (index 1) met een zwarte
rand (index 2) op transparant (index 0), geschaald met een vaste factor. De bitmap gaat in de
RLE van PGS (runs per regel, 00 00 aan het eind van elke regel). Een ODS groter dan één segment
gaat in stukken (vlag eerste en laatste). Gebaseerd op supwrite.py van de spike (S9).

Muxen (spike punt 14): een `.sup` in mkv vraagt `-copyts` (anders 2 s te vroeg), en VobSub uit
deze `.sup` met `-c:s dvdsub` vraagt `-fix_sub_duration` (anders gaan de ondertitels nooit weg).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "10001", "11001", "10101", "10011", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    " ": ["00000"] * 7,
    ".": ["00000"] * 5 + ["01100", "01100"],
    "!": ["00100"] * 5 + ["00000", "00100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
}

# PDS: index → (Y, Cr, Cb, alfa), BT.709 video-range
PALETTE = {0: (16, 128, 128, 0), 1: (235, 128, 128, 255), 2: (16, 128, 128, 255)}
SEGMENT_MAX = 0xFFFF
PCS, WDS, PDS, ODS, END = 0x16, 0x17, 0x14, 0x15, 0x80


@dataclass(frozen=True)
class Placed:
    """Waar een ondertitel in beeld staat (voor tests van het inbranden)."""

    start: float
    end: float
    text: str
    x: int
    y: int
    w: int
    h: int
    rle_bytes: int


def render(text: str, scale: int = 6, border: int = 2) -> tuple[int, int, list[bytearray]]:
    """Tekst naar een bitmap met de indexen 0 (transparant), 1 (wit) en 2 (zwarte rand)."""
    text = text.upper()
    gw, gh = 5 * scale, 7 * scale
    pad = border + 1
    w = len(text) * (gw + scale) - scale + 2 * pad
    h = gh + 2 * pad
    w += w % 2  # even breedte
    rows = [bytearray(w) for _ in range(h)]
    for i, ch in enumerate(text):
        glyph = FONT.get(ch, FONT[" "])
        x0 = pad + i * (gw + scale)
        for gy, line in enumerate(glyph):
            for gx, bit in enumerate(line):
                if bit != "1":
                    continue
                for yy in range(gy * scale, (gy + 1) * scale):
                    row = rows[pad + yy]
                    for xx in range(gx * scale, (gx + 1) * scale):
                        row[x0 + xx] = 1
    # De rand: elke transparante pixel naast wit wordt zwart
    out = [bytearray(r) for r in rows]
    for y in range(h):
        for x in range(w):
            if rows[y][x] != 0:
                continue
            for dy in range(-border, border + 1):
                yy = y + dy
                if not 0 <= yy < h:
                    continue
                row = rows[yy]
                if any(0 <= x + dx < w and row[x + dx] == 1 for dx in range(-border, border + 1)):
                    out[y][x] = 2
                    break
    return w, h, out


def rle(rows: list[bytearray]) -> bytes:
    """De RLE van PGS (HDMV): per regel runs, en 00 00 aan het eind van de regel."""
    out = bytearray()
    for row in rows:
        x, w = 0, len(row)
        while x < w:
            color = row[x]
            n = 1
            while x + n < w and row[x + n] == color and n < 16383:
                n += 1
            if color == 0:
                out += bytes([0, n]) if n < 64 else bytes([0, 0x40 | (n >> 8), n & 0xFF])
            elif n < 3:
                out += bytes([color]) * n
            elif n < 64:
                out += bytes([0, 0x80 | n, color])
            else:
                out += bytes([0, 0xC0 | (n >> 8), n & 0xFF, color])
            x += n
        out += b"\x00\x00"
    return bytes(out)


def _segment(pts: float, kind: int, data: bytes) -> bytes:
    if len(data) > SEGMENT_MAX:
        raise ValueError("segment te groot")
    return b"PG" + struct.pack(">IIBH", round(pts * 90000), 0, kind, len(data)) + data


def display_set(
    pts: float, n: int, cw: int, ch: int, x: int, y: int, w: int, h: int, data: bytes
) -> bytes:
    pcs = struct.pack(">HHBHBBBB", cw, ch, 0x10, n, 0x80, 0, 0, 1)
    pcs += struct.pack(">HBBHH", 0, 0, 0, x, y)  # object 0, venster 0, niet bijgesneden
    wds = struct.pack(">B", 1) + struct.pack(">BHHHH", 0, x, y, w, h)
    pds = struct.pack(">BB", 0, 0) + b"".join(
        struct.pack(">BBBBB", i, yy, cr, cb, a) for i, (yy, cr, cb, a) in PALETTE.items()
    )
    # Het ODS, zo nodig in stukken; object_data_length telt breedte en hoogte (4 bytes) mee
    total = len(data) + 4
    first_room = SEGMENT_MAX - 11  # 2+1+1+3+2+2 bytes kop in het eerste stuk
    chunks = [data[:first_room]]
    rest = data[first_room:]
    while rest:
        chunks.append(rest[: SEGMENT_MAX - 4])
        rest = rest[SEGMENT_MAX - 4 :]
    ods = b""
    for i, chunk in enumerate(chunks):
        flag = (0x80 if i == 0 else 0) | (0x40 if i == len(chunks) - 1 else 0)
        body = struct.pack(">HBB", 0, 0, flag)
        if i == 0:
            body += total.to_bytes(3, "big") + struct.pack(">HH", w, h)
        ods += _segment(pts, ODS, body + chunk)
    return (
        _segment(pts, PCS, pcs)
        + _segment(pts, WDS, wds)
        + _segment(pts, PDS, pds)
        + ods
        + _segment(pts, END, b"")
    )


def clear_set(pts: float, n: int, cw: int, ch: int, x: int, y: int, w: int, h: int) -> bytes:
    pcs = struct.pack(">HHBHBBBB", cw, ch, 0x10, n, 0x00, 0, 0, 0)
    wds = struct.pack(">B", 1) + struct.pack(">BHHHH", 0, x, y, w, h)
    return _segment(pts, PCS, pcs) + _segment(pts, WDS, wds) + _segment(pts, END, b"")


def write_sup(
    path: Path, cw: int, ch: int, events: list[tuple[float, float, str]], scale: int = 6
) -> list[Placed]:
    """Schrijft de .sup (cw × ch) en geeft per ondertitel de plek van de bitmap."""
    buf = bytearray()
    placed = []
    for n, (start, end, text) in enumerate(events):
        w, h, rows = render(text, scale)
        x = (cw - w) // 2
        y = ch - h - ch // 12
        data = rle(rows)
        buf += display_set(start, 2 * n, cw, ch, x, y, w, h, data)
        buf += clear_set(end, 2 * n + 1, cw, ch, x, y, w, h)
        placed.append(Placed(start, end, text, x, y, w, h, len(data)))
    Path(path).write_bytes(bytes(buf))
    return placed
