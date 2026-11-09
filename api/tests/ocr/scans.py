"""Gemaakte scans voor de OCR van X1 (bouwontwerp fase 6, §3.6): een A4 op 300 dpi met tekst in
DejaVu Sans, zoals een scanner hem geeft, en wat daarvan afgeleid is (scheef, een kwartslag, een
foto met EXIF-draaiing 6, een HEIC, een PDF zonder tekstlaag).

Alles is vast: hetzelfde papier (ruis uit een vast zaad), dezelfde regels, dezelfde plekken.
Per woord weet de fixture waar het midden staat (`words`), in het beeld zoals de viewer het
toont; zo kan een test de vakjes van de OCR terugrekenen en vergelijken.

DejaVu Sans is nodig (Debian en Ubuntu: fonts-dejavu-core; de job ocr installeert hem). Zonder
faalt `font` met een duidelijke melding, en niet stil met een ander lettertype: de getallen van
de herkenning (recall per woord) horen bij dit lettertype.
"""

from __future__ import annotations

import io
import math
import random
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DPI = 300
A4 = (2480, 3508)  # 210 × 297 mm op 300 dpi
PAPER = 236
INK = 28
SIZE = 46  # ±11 punt op 300 dpi
LEFT, TOP, LINE = 240, 360, 110
DEJAVU = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/local/share/fonts/DejaVuSans.ttf",
)
# Nederlands en Engels door elkaar, zoals een brief met een Engelse kop (nld+eng)
LINES = (
    "Gemeente Utrecht afdeling belastingen",
    "Factuur nummer 2024 117 voor parkeren",
    "Datum drie november tweeduizend zesentwintig",
    "Omschrijving jaarlijkse parkeervergunning binnenstad",
    "Totaal inclusief btw honderddrieentwintig euro",
    "Betaal binnen veertien dagen op rekening",
    "Kenmerk ocrwoordx bij elke betaling vermelden",
    "City of Utrecht tax department invoice",
    "Description annual parking permit city centre",
    "Total including value added tax due",
    "Please pay within fourteen days of receipt",
    "Questions about this invoice call our office",
)
WORDS = tuple(w for line in LINES for w in line.split())


class NoFont(RuntimeError):
    """DejaVu Sans ontbreekt."""


@cache
def font(size: int = SIZE) -> ImageFont.FreeTypeFont:
    for path in DEJAVU:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise NoFont(
        "DejaVu Sans ontbreekt (apt install fonts-dejavu-core): de scans van de OCR-tests "
        f"(tests/ocr/scans.py) hebben hem nodig; gezocht in {', '.join(DEJAVU)}"
    )


@dataclass(frozen=True)
class Word:
    text: str
    x: float  # het midden, in pixels van het beeld zoals de viewer het toont
    y: float


def _paper(size: tuple[int, int], seed: str) -> Image.Image:
    """Papier van grijs PAPER met een beetje ruis (vast per zaad)."""
    rng = random.Random(f"x1-ocr-{seed}")
    width, height = size
    small = Image.frombytes("L", (width // 8, height // 8), rng.randbytes(width * height // 64))
    grain = small.resize(size, Image.BILINEAR).point(lambda v: PAPER - 6 + v * 12 // 255)
    return grain


@cache
def _upright() -> tuple[Image.Image, tuple[Word, ...], tuple[tuple, ...]]:
    return _draw(0)


def _draw(variant: int) -> tuple[Image.Image, tuple[Word, ...], tuple[tuple, ...]]:
    """De brief rechtop; variant 0 is die van _upright, een andere variant hetzelfde papier met
    andere ruis (voor de poort van §4.5: 100 pagina's die niet bit voor bit gelijk zijn)."""
    page = _paper(A4, "a4" if variant == 0 else f"a4-{variant}")
    draw = ImageDraw.Draw(page)
    f = font()
    space = draw.textlength(" ", font=f)
    words = []
    found = []
    for i, line in enumerate(LINES):
        x, y = float(LEFT), TOP + i * LINE
        for word in line.split():
            left, top, right, bottom = draw.textbbox((x, y), word, font=f)
            draw.text((x, y), word, fill=INK, font=f)
            words.append(Word(word, (left + right) / 2, (top + bottom) / 2))
            found.append((word, left, top, right - left, bottom - top, i))
            x += draw.textlength(word, font=f) + space
    return page, tuple(words), tuple(found)


def page(angle: float = 0.0, variant: int = 0) -> Image.Image:
    """De brief (grijs, A4 op 300 dpi), over angle graden tegen de klok in gedraaid: scheef
    (|angle| < 45, de pagina blijft A4) of een kwartslag (90, 180, 270: liggend of op zijn kop).
    variant: andere ruis op het papier (alleen variant 0 blijft in het geheugen)."""
    upright, _, _ = _upright() if variant == 0 else _draw(variant)
    if angle % 90 == 0:
        return upright.rotate(angle, expand=True) if angle % 360 else upright.copy()
    return upright.rotate(angle, resample=Image.BICUBIC, fillcolor=PAPER)


def words(angle: float = 0.0) -> tuple[Word, ...]:
    """Waar elk woord van page(angle) staat (het midden)."""
    _, upright, _ = _upright()
    if angle % 360 == 0:
        return upright
    width, height = A4
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    if angle % 90 == 0:
        quarter = {
            90: lambda x, y: (y, width - x),
            180: lambda x, y: (width - x, height - y),
            270: lambda x, y: (height - y, x),
        }[round(angle) % 360]
        return tuple(Word(w.text, *quarter(w.x, w.y)) for w in upright)
    cx, cy = width / 2, height / 2
    return tuple(
        Word(w.text, (w.x - cx) * c + (w.y - cy) * s + cx, -(w.x - cx) * s + (w.y - cy) * c + cy)
        for w in upright
    )


def boxes() -> list[list]:
    """De woorden van page(0) zoals de worker ze geeft (ops/ocr.keep): [tekst, links, boven,
    breedte, hoogte, regel] in hele pixels. Voor de nep-worker, die geen tesseract heeft."""
    _, _, found = _upright()
    return [[t, round(x), round(y), round(w), round(h), n] for t, x, y, w, h, n in found]


def unique() -> set[str]:
    """De woorden die maar één keer in de brief staan: hun plek is eenduidig."""
    return {w for w in WORDS if WORDS.count(w) == 1}


def png(angle: float = 0.0) -> bytes:
    buf = io.BytesIO()
    page(angle).save(buf, "PNG", compress_level=1, dpi=(DPI, DPI))
    return buf.getvalue()


def jpeg_exif6() -> bytes:
    """Een foto van de brief zoals een telefoon hem opslaat: de pixels een kwartslag tegen de
    klok in (liggend), met EXIF-draaiing 6 (de viewer draait 90° met de klok mee). Rechtop
    gezien is het page(0); de woorden staan dus op words(0)."""
    stored = page(90)
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    stored.convert("RGB").save(buf, "JPEG", quality=88, exif=exif, dpi=(DPI, DPI))
    return buf.getvalue()


def heic(angle: float = 0.0) -> bytes:
    """De brief als HEIC (pillow-heif, zoals een iPhone)."""
    import pillow_heif

    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    page(angle).convert("RGB").save(buf, "HEIF", quality=90)
    return buf.getvalue()


def pdf(angle: float = 0.0, variant: int = 0) -> bytes:
    """De brief als PDF zonder tekstlaag: één pagina A4 met de scan als beeld (300 dpi)."""
    buf = io.BytesIO()
    page(angle, variant).save(buf, "PDF", resolution=float(DPI))
    return buf.getvalue()


# --- een bon, met een telefoon gefotografeerd (de e2e van X1) ---

BON = (1000, 1500)  # rechtop, zoals de viewer hem toont
BON_SIZE = 44
BON_LEFT, BON_TOP, BON_LINE = 90, 140, 96
BON_LINES = (
    "Bakkerij De Korenaar",
    "Kassabon nummer 0412",
    "Volkorenbrood 3,10",
    "Krentenbollen 2,45",
    "Totaal 5,55",
    "Spaarcode {woord}",
    "Tot ziens",
)


def bon(woord: str, angle: float = 2.0) -> tuple[bytes, dict[str, tuple[float, float]]]:
    """Een foto van een kassabon zoals een telefoon hem opslaat: wit papier met ruis, iets
    scheef (angle graden tegen de klok in), de pixels een kwartslag tegen de klok in (liggend)
    met EXIF-draaiing 6. woord staat alleen in het beeld (de regel Spaarcode).

    Geeft de JPEG, en per woord het midden als deel (0..1) van de breedte en de hoogte van de
    bon rechtop, zoals de viewer hem toont: daar moet de markering liggen."""
    width, height = BON
    blad = _paper(BON, f"bon-{woord}").point(lambda v: min(255, v + 12))
    draw = ImageDraw.Draw(blad)
    f = font(BON_SIZE)
    space = draw.textlength(" ", font=f)
    midden: dict[str, tuple[float, float]] = {}
    for i, line in enumerate(BON_LINES):
        x, y = float(BON_LEFT), BON_TOP + i * BON_LINE
        for w in line.format(woord=woord).split():
            left, top, right, bottom = draw.textbbox((x, y), w, font=f)
            draw.text((x, y), w, fill=INK, font=f)
            midden.setdefault(w, ((left + right) / 2, (top + bottom) / 2))
            x += draw.textlength(w, font=f) + space
    scheef = blad.rotate(angle, resample=Image.BICUBIC, fillcolor=PAPER + 12)
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    cx, cy = width / 2, height / 2
    deel = {
        w: (
            ((x - cx) * c + (y - cy) * s + cx) / width,
            (-(x - cx) * s + (y - cy) * c + cy) / height,
        )
        for w, (x, y) in midden.items()
    }
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    scheef.rotate(90, expand=True).convert("RGB").save(buf, "JPEG", quality=90, exif=exif)
    return buf.getvalue(), deel


def tokens(text: str) -> list[str]:
    """De woorden van een tekst, zonder leestekens eromheen, in kleine letters."""
    return [t for t in (re.sub(r"^\W+|\W+$", "", w).lower() for w in text.split()) if t]


def recall(found: list[str]) -> float:
    """Welk deel van WORDS de herkenning vond (elk woord zo vaak als het erin staat)."""
    pool: dict[str, int] = {}
    for t in found:
        pool[t] = pool.get(t, 0) + 1
    hit = 0
    for w in WORDS:
        t = w.lower()
        if pool.get(t, 0) > 0:
            pool[t] -= 1
            hit += 1
    return hit / len(WORDS)
