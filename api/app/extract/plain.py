"""txt, md en csv (bouwontwerp fase 6, Keuze 17): UTF-8, UTF-16 met BOM, anders cp1252.

Er wordt hooguit zoveel gelezen als de grens nodig heeft (4 bytes per teken); was het bestand
groter, dan is de tekst afgekapt.
"""

from __future__ import annotations

import codecs
from typing import BinaryIO

from app.extract.collect import Collector

CHUNK_CHARS = 64 * 1024
BYTES_PER_CHAR = 4


def codec_of(data: bytes) -> str:
    """De codec voor deze bytes: een BOM voor UTF-8 of UTF-16, anders UTF-8 als dat klopt
    (ook als het laatste teken half is), en anders cp1252."""
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        codecs.getincrementaldecoder("utf-8")("strict").decode(data, final=False)
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


def decode(data: bytes, codec: str) -> str:
    # Incrementeel: een teken dat op de grens van wat er gelezen is, is afgeknipt, valt weg
    return codecs.getincrementaldecoder(codec)(errors="replace").decode(data, final=False)


def feed(out: Collector, text: str) -> None:
    """In stukken, zodat add() tussendoor naar de klok kijkt."""
    for start in range(0, len(text), CHUNK_CHARS):
        out.add(text[start : start + CHUNK_CHARS])
        out.check()


def read(f: BinaryIO, out: Collector) -> None:
    limit = out.limit * BYTES_PER_CHAR + 4
    data = f.read(limit)
    if f.read(1):
        out.truncate()
    feed(out, decode(data, codec_of(data)))
