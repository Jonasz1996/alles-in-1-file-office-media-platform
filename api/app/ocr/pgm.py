"""Het grijze beeld voor OCR: een binaire PGM (P5, 8 bits) (bouwontwerp fase 6, Keuze 22).

Kind 1 (app.render ocr_page) schrijft hem op een pipe naar de worker; de worker leest eerst de
kop (read_header) en pas als die klopt start kind 2. Alleen de standaardbibliotheek: de worker
en kind 1 gebruiken dit allebei.

De kop is precies wat header() schrijft: "P5\\n<breedte> <hoogte>\\n255\\n", zonder
commentaar en zonder extra witruimte. Alles anders (een andere magie, een ander maximum, een
nul, een maat boven de grens, een kop zonder einde) is BadImage: de worker zegt dan
ocr_bad_image en start kind 2 niet.

Draaien (rotate) in stappen van 90° met de klok mee, op de bytes zelf (slices, geen Pillow): voor
de OSD-ronde van de worker. unrotate_box zet een vak uit het gedraaide beeld terug in het beeld
van kind 1.
"""

from __future__ import annotations

import re

# Hooguit zoveel pixels: een A4 op 300 dpi is 8,7 MP, een foto van 48 MP wordt kleiner gemaakt.
# Gemeten: de herkenning van 50 MP blijft onder 300 MB (RLIMIT_DATA 512 MB van kind 2)
OCR_PIXELS = 40_000_000
MAX_SIDE = 20_000  # ook een heel lange strook blijft binnen de grens van de herkenning
HEADER_MAX = 32  # "P5\n20000 20000\n255\n" is 19 bytes
_HEADER = re.compile(rb"P5\n([1-9][0-9]{0,4}) ([1-9][0-9]{0,4})\n255\n")


class BadImage(ValueError):
    """De PGM klopt niet (de kop, de maat of het aantal bytes)."""


def header(width: int, height: int) -> bytes:
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE and width * height <= OCR_PIXELS):
        raise BadImage("maat")
    return b"P5\n%d %d\n255\n" % (width, height)


def read_header(data: bytes) -> tuple[int, int, int]:
    """(breedte, hoogte, lengte van de kop) uit het begin van data, of BadImage. data mag
    langer zijn dan de kop. Wie van een pipe leest, leest tot er drie keer "\\n" in staat of
    HEADER_MAX bytes (header_done), en vraagt het dan pas."""
    m = _HEADER.match(data)
    if m is None:
        raise BadImage("kop")
    width, height = int(m.group(1)), int(m.group(2))
    if width > MAX_SIDE or height > MAX_SIDE or width * height > OCR_PIXELS:
        raise BadImage("maat")
    return width, height, m.end()


def header_done(data: bytes) -> bool:
    """Is er genoeg gelezen om over de kop te beslissen?"""
    return data.count(b"\n") >= 3 or len(data) >= HEADER_MAX


def rotate(pixels: bytes, width: int, height: int, cw: int) -> tuple[bytes, int, int]:
    """Het beeld cw graden met de klok mee gedraaid (0, 90, 180 of 270): (pixels, breedte,
    hoogte). Rij r van 90° is kolom r van onder naar boven; van 270° kolom breedte−1−r van boven
    naar onder; 180° is alles omgekeerd."""
    if len(pixels) != width * height:
        raise BadImage("lengte")
    if cw == 0:
        return pixels, width, height
    if cw == 180:
        return pixels[::-1], width, height
    if cw == 90:
        return b"".join(pixels[r::width][::-1] for r in range(width)), height, width
    if cw == 270:
        return b"".join(pixels[width - 1 - r :: width] for r in range(width)), height, width
    raise ValueError(cw)


def unrotate_box(
    box: tuple[int, int, int, int], width: int, height: int, cw: int
) -> tuple[int, int, int, int]:
    """Een vak (links, boven, breedte, hoogte) uit het beeld dat cw graden met de klok mee
    gedraaid is, terug in het beeld van vóór het draaien (width × height)."""
    left, top, w, h = box
    if cw == 0:
        return box
    if cw == 90:  # (x, y) → (hoogte − y, x)
        return top, height - left - w, h, w
    if cw == 270:  # (x, y) → (y, breedte − x)
        return width - top - h, left, h, w
    if cw == 180:
        return width - left - w, height - top - h, w, h
    raise ValueError(cw)
