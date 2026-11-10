"""Gemaakte scans voor de OCR van X1 (bouwontwerp fase 6, §3.6): een A4 op 300 dpi met tekst in
DejaVu Sans, zoals een scanner hem geeft, en wat daarvan afgeleid is (scheef, een kwartslag, een
foto met EXIF-draaiing 6, een HEIC, een PDF zonder tekstlaag).

Naast de brief (12 regels) is er een volle pagina van 44 regels in een kleinere letter
(`page(…, vol=True)`, `pdf(…, vol=True)`, `VOL_WORDS`) voor de poort van §4.5.

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

# Een volle pagina (de poort van §4.5, review golf 4, uitrol F4): 44 regels in een kleinere letter
# (34 px, ±8 punt op 300 dpi), zoals een contract. De brief hierboven heeft 12 regels; met alleen
# die zegt de poort te weinig over een echte pagina vol tekst.
VOL_SIZE = 34
VOL_LEFT, VOL_TOP, VOL_LINE = 200, 220, 70
VOL_LINES = (
    "Huurovereenkomst woonruimte tussen verhuurder en huurder voor de woning aan de Oudegracht",
    "Artikel 1 de verhuurder verhuurt aan de huurder de woning met tuin en schuur en een berging",
    "gelegen aan de Oudegracht nummer 214 te Utrecht met een oppervlak van tachtig vierkante meter",
    "Artikel 2 de huur gaat in op de eerste dag van de maand die volgt op de dag van ondertekening",
    "en wordt aangegaan voor een periode van twaalf maanden en daarna steeds voor onbepaalde tijd",
    "Artikel 3 de huurprijs bedraagt negenhonderd euro per maand exclusief de kosten van energie",
    "te voldoen voor de eerste dag van elke kalendermaand door overmaking naar de verhuurder",
    "Artikel 4 de huurder betaalt een waarborgsom van twee maanden huur bij het tekenen",
    "die na afloop wordt terugbetaald zonder rente zodra de woning in goede staat is opgeleverd",
    "Artikel 5 de huurder gebruikt de woning alleen om te wonen en niet voor een bedrijf of winkel",
    "en houdt de tuin en de schuur netjes en schoon en zet het afval op tijd aan de straat",
    "Artikel 6 kleine herstellingen komen voor rekening van de huurder zoals kranen en sloten",
    "groot onderhoud aan dak en gevel doet de verhuurder na overleg met de huurder",
    "Artikel 7 huisdieren zijn toegestaan na schriftelijke toestemming van de verhuurder vooraf",
    "Artikel 8 de huurder mag de woning niet onderverhuren of in gebruik geven aan een ander",
    "Artikel 9 opzeggen kan per aangetekende brief of per email aan het adres van de verhuurder",
    "met een opzegtermijn van ten minste een maand voor de huurder en drie voor de verhuurder",
    "Artikel 10 bij het einde van de huur volgt een inspectie en wordt een verslag gemaakt",
    "van de staat van de woning in bijzijn van beide partijen en ondertekend door allebei",
    "Artikel 11 op deze overeenkomst is Nederlands recht van toepassing en de rechter in Utrecht",
    "Kenmerk dichtwoordx bij elke betaling en brief vermelden zodat de administratie klopt",
    "Aldus opgemaakt in tweevoud en ondertekend te Utrecht door de verhuurder en de huurder",
    "Residential tenancy agreement between landlord and tenant for the dwelling at the Oudegracht",
    "Clause 1 the landlord lets to the tenant the dwelling with garden and shed and a storage room",
    "located at Oudegracht number 214 in Utrecht with a floor area of eighty square metres",
    "Clause 2 the tenancy starts on the first day of the month following the day of signing",
    "and is entered into for a period of twelve months and thereafter for an indefinite period",
    "Clause 3 the rent amounts to nine hundred euros per month excluding the cost of energy",
    "payable before the first day of every calendar month by transfer to the landlord",
    "Clause 4 the tenant pays a deposit equal to two months of rent when signing this contract",
    "which is returned without interest at the end once the dwelling is handed back in good order",
    "Clause 5 the tenant uses the dwelling only as a home and not for a business or a shop",
    "and keeps the garden and the shed tidy and clean and puts the waste out on the right days",
    "Clause 6 minor repairs are at the expense of the tenant such as taps and locks and hinges",
    "major maintenance of roof and walls is done by the landlord after agreeing with the tenant",
    "Clause 7 pets are allowed after written permission from the landlord given in advance",
    "Clause 8 the tenant may not sublet the dwelling or let anyone else make use of it",
    "Clause 9 notice is given by registered letter or by email to the address of the landlord",
    "with a notice period of at least one full month for the tenant and three for the landlord",
    "Clause 10 at the end of the tenancy an inspection follows and a written report is made",
    "of the state of the dwelling in presence of both parties and signed by both of them",
    "Clause 11 this agreement is governed by Dutch law and the court in Utrecht has jurisdiction",
    "Reference dichtwoordx must be quoted with every payment so that the records are correct",
    "Drawn up in duplicate and signed in Utrecht by the landlord and by the tenant on this day",
)
VOL_WORDS = tuple(w for line in VOL_LINES for w in line.split())


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


def _draw(
    variant: int, vol: bool = False
) -> tuple[Image.Image, tuple[Word, ...], tuple[tuple, ...]]:
    """De brief rechtop; variant 0 is die van _upright, een andere variant hetzelfde papier met
    andere ruis (voor de poort van §4.5: pagina's die niet bit voor bit gelijk zijn). vol: de
    volle pagina (VOL_LINES in VOL_SIZE) in plaats van de brief."""
    page = _paper(A4, "a4" if variant == 0 else f"a4-{variant}")
    draw = ImageDraw.Draw(page)
    f = font(VOL_SIZE if vol else SIZE)
    x0, y0, step = (VOL_LEFT, VOL_TOP, VOL_LINE) if vol else (LEFT, TOP, LINE)
    space = draw.textlength(" ", font=f)
    words = []
    found = []
    for i, line in enumerate(VOL_LINES if vol else LINES):
        x, y = float(x0), y0 + i * step
        for word in line.split():
            left, top, right, bottom = draw.textbbox((x, y), word, font=f)
            draw.text((x, y), word, fill=INK, font=f)
            words.append(Word(word, (left + right) / 2, (top + bottom) / 2))
            found.append((word, left, top, right - left, bottom - top, i))
            x += draw.textlength(word, font=f) + space
    return page, tuple(words), tuple(found)


def page(angle: float = 0.0, variant: int = 0, vol: bool = False) -> Image.Image:
    """De brief (grijs, A4 op 300 dpi), over angle graden tegen de klok in gedraaid: scheef
    (|angle| < 45, de pagina blijft A4) of een kwartslag (90, 180, 270: liggend of op zijn kop).
    variant: andere ruis op het papier (alleen variant 0 van de brief blijft in het geheugen).
    vol: de volle pagina van 44 regels in plaats van de brief."""
    upright, _, _ = _upright() if variant == 0 and not vol else _draw(variant, vol)
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


def pdf(angle: float = 0.0, variant: int = 0, vol: bool = False) -> bytes:
    """De brief (of met vol de volle pagina) als PDF zonder tekstlaag: één pagina A4 met de scan
    als beeld (300 dpi)."""
    buf = io.BytesIO()
    page(angle, variant, vol).save(buf, "PDF", resolution=float(DPI))
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


def recall(found: list[str], words: tuple[str, ...] = WORDS) -> float:
    """Welk deel van words (de brief, of VOL_WORDS) de herkenning vond (elk woord zo vaak als het
    erin staat)."""
    pool: dict[str, int] = {}
    for t in found:
        pool[t] = pool.get(t, 0) + 1
    hit = 0
    for w in words:
        t = w.lower()
        if pool.get(t, 0) > 0:
            pool[t] -= 1
            hit += 1
    return hit / len(words)
