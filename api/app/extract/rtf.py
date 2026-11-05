"""rtf (bouwontwerp fase 6, Keuze 17): een eigen stripper, alleen de standaardbibliotheek.

Eén keer door het bestand (een mmap, hooguit RTF_BYTES), met een stapel per groep. Wat geen
tekst is, slaat hij over: de tabellen van lettertypen, kleuren en stijlen, plaatjes en objecten
(ook de hex-data erin), de instructie van een veld (wel het resultaat), en elke groep met \\*.
Uit \\info komen alleen titel, auteur en onderwerp, als meta. Kop- en voetteksten en voetnoten
zijn tekst. \\'hh is een byte in de codetabel van \\ansicpg (anders cp1252); \\uN is een teken,
gevolgd door \\ucN tekens die een oude lezer zou tonen (die vallen weg). \\bin N slaat N bytes
over.
"""

from __future__ import annotations

import codecs
import mmap
import re
from typing import BinaryIO

from app.extract.collect import Collector
from app.extract.normalize import fold_meta

RTF_BYTES = 128 * 1024 * 1024
FLUSH_CHARS = 16 * 1024
META_FIELD_CHARS = 500

_TOKEN = re.compile(
    rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?"  # 1, 2: een stuurwoord met getal
    rb"|\\'([0-9a-fA-F]{2})"  # 3: een byte in hex
    rb"|\\([^a-zA-Z'])"  # 4: een stuurteken (\\, \{, \}, \~, \-, \_, \*, …)
    rb"|([{}])"  # 5: een groep open of dicht
    rb"|([^\\{}\r\n]+)"  # 6: gewone tekst
    rb"|[\r\n]+"  # regeleinden in het bestand zelf tellen niet
)

# Groepen die nooit tekst zijn, ook zonder \*
SKIP = {
    "fonttbl", "colortbl", "stylesheet", "listtable", "listoverridetable", "revtbl", "rsidtbl",
    "generator", "pict", "object", "objdata", "fldinst", "themedata", "colorschememapping",
    "datastore", "latentstyles", "xmlnstbl", "filetbl", "pgdsctbl", "mmathPr", "bkmkstart",
    "bkmkend", "shpinst", "nonshppict", "private", "xe", "tc", "pn", "listtext", "atnid",
    "atnauthor", "annotation", "userprops", "docvar", "wgrffmtfilter", "panose", "falt",
    "fontemb", "fontfile", "blipuid", "sp", "sn", "sv",
}  # fmt: skip
META = {"title", "author", "subject"}
NEWLINE = {"par", "line", "sect", "page", "row", "pard"}
SPACE = {"tab", "cell", "nestcell"}
CHARS = {
    "emdash": "\u2014", "endash": "\u2013", "lquote": "\u2018", "rquote": "\u2019",
    "ldblquote": "\u201c", "rdblquote": "\u201d", "bullet": "\u2022", "emspace": " ",
    "enspace": " ", "qmspace": " ",
}  # fmt: skip
SYMBOLS = {b"\\": "\\", b"{": "{", b"}": "}", b"~": " ", b"_": "-", b"-": ""}


class _Group:
    __slots__ = ("dest", "uc", "first")

    def __init__(self, dest: str, uc: int):
        self.dest = dest  # "body", "skip", "info" of "meta:<veld>"
        self.uc = uc
        self.first = True  # nog geen stuurwoord in deze groep gezien


class _State:
    def __init__(self, out: Collector):
        self.out = out
        self.buffer: list[str] = []
        self.size = 0
        self.raw = bytearray()  # \'hh-bytes die nog samen gedecodeerd moeten worden
        self.codec = "cp1252"
        self.meta: dict[str, list[str]] = {name: [] for name in META}

    def emit(self, dest: str, text: str) -> None:
        if not text:
            return
        if dest == "body":
            self.buffer.append(text)
            self.size += len(text)
            if self.size >= FLUSH_CHARS:
                self.flush()
        elif dest.startswith("meta:"):
            parts = self.meta[dest[5:]]
            if sum(map(len, parts)) < META_FIELD_CHARS:
                parts.append(text)

    def bytes_out(self, dest: str) -> None:
        if self.raw:
            text = self.raw.decode(self.codec, "replace")
            self.raw.clear()
            self.emit(dest, text)

    def flush(self) -> None:
        if not self.buffer:
            return
        text = "".join(self.buffer)
        self.buffer.clear()
        self.size = 0
        # \u-paren (surrogaten) weer samen
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        self.out.add(text)

    def folded_meta(self) -> str:
        return fold_meta(*("".join(self.meta[k]) for k in ("title", "author", "subject")))


def _codec(number: int) -> str:
    try:
        return codecs.lookup(f"cp{number}").name
    except LookupError:
        return "cp1252"


def read(f: BinaryIO, out: Collector) -> None:
    f.seek(0, 2)
    size = f.tell()
    if size == 0:
        return
    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        end = min(size, RTF_BYTES)
        if size > RTF_BYTES:
            out.truncate()
        state = _State(out)
        try:
            _scan(data, end, state)
        finally:
            state.bytes_out("body")
            out.meta = state.folded_meta()
            state.flush()


def _scan(data: mmap.mmap, end: int, state: _State) -> None:
    stack: list[_Group] = []
    group = _Group("body", 1)
    skip_fallback = 0  # zoveel tekens na \uN vallen weg
    pos = 0
    steps = 0
    while pos < end:
        m = _TOKEN.match(data, pos, end)
        if m is None:  # een losse \ aan het eind
            break
        pos = m.end()
        steps += 1
        if steps % 4096 == 0:
            state.out.check()
        word, number, hexbyte, symbol, brace, text = m.groups()
        if word is None and hexbyte is None and symbol is None and brace is None and text is None:
            continue  # een regeleinde in het bestand zelf
        if hexbyte is not None:
            if skip_fallback:
                skip_fallback -= 1
                continue
            if group.dest != "skip":
                state.raw.append(int(hexbyte, 16))
            continue
        # Alles behalve \'hh: eerst de verzamelde bytes decoderen
        state.bytes_out(group.dest)
        if brace is not None:
            skip_fallback = 0
            if brace == b"{":
                stack.append(group)
                # Binnen \info beslist het eerste stuurwoord: titel, auteur, onderwerp of niets
                group = _Group("info-child" if group.dest == "info" else group.dest, group.uc)
            else:
                if stack:
                    group = stack.pop()
            continue
        if text is not None:
            if skip_fallback:
                cut = min(skip_fallback, len(text))
                skip_fallback -= cut
                text = text[cut:]
            if group.dest not in ("skip", "info", "info-child"):
                state.emit(group.dest, text.decode(state.codec, "replace"))
            continue
        if symbol is not None:
            if symbol == b"*":
                group.dest = "skip"
                continue
            if skip_fallback:
                skip_fallback -= 1
                continue
            if group.dest not in ("skip", "info", "info-child"):
                state.emit(group.dest, SYMBOLS.get(symbol, ""))
            continue
        name = word.decode("ascii")
        value = int(number) if number is not None else None
        first, group.first = group.first, False
        if name == "bin":
            pos += max(0, value or 0)  # binaire data: overslaan
            continue
        if first and group.dest == "info-child":
            group.dest = f"meta:{name}" if name in META else "skip"
            continue
        if first and name in SKIP:
            group.dest = "skip"
            continue
        if name == "info":
            group.dest = "info"
            continue
        if name == "ansicpg" and value is not None:
            state.codec = _codec(value)
            continue
        if name == "uc" and value is not None:
            group.uc = max(0, min(value, 10))
            continue
        if name == "u" and value is not None:
            point = value + 65536 if value < 0 else value
            if group.dest not in ("skip", "info", "info-child") and 0 <= point <= 0x10FFFF:
                state.emit(group.dest, chr(point))
            skip_fallback = group.uc
            continue
        skip_fallback = 0
        if group.dest in ("skip", "info", "info-child"):
            continue
        if name in NEWLINE:
            state.emit(group.dest, "\n")
        elif name in SPACE:
            state.emit(group.dest, " ")
        elif name in CHARS:
            state.emit(group.dest, CHARS[name])
