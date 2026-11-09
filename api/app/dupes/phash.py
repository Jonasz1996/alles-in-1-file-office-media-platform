"""De rekenkunde van bijna-duplicaten (bouwontwerp fase 6, Keuzes 35 en 36; Z3): pHash, dHash,
MAD64, de verhouding en het niveau van een paar. Met de hand op Pillow, zonder numpy; geen
database, geen tabellen.

- pHash: het grijsbeeld van 32 × 32, daarvan de DCT, en van de laagste 8 × 8 frequenties (ook de
  DC) per waarde of hij boven de mediaan ligt: 64 bits. Zonder teken (0 … 2⁶⁴ − 1); in blob_hash
  met teken (signed) en in vier stukken van 16 bits (pieces) om kandidaten te vinden.
- Vlak is geen hash: is de spreiding (standaardafwijking) van dat grijsbeeld < FLAT, dan geeft
  phash() None. pHash kijkt niet naar helderheid: alle bijna egale beelden hadden afstand 0 (V5).
- De tweede controle (compare en check_ok) vergelijkt twee voorbeelden: de verhouding binnen
  ASPECT, de afstand van de dHash (9 × 8 grijs, 64 bits) ≤ DHASH, MAD64 (de gemiddelde
  afwijking van 64 × 64 grijs, 0–255) ≤ MAD64, en dezelfde afwijking in elk blok van 8 × 8
  van dat beeld ≤ BLOCK64. Dat laatste staat niet in Keuze 35: op de fixtures van §3.6 hebben
  twee verschillende formulieren op één sjabloon een MAD64 van maar 0,86, lager dan een echte
  variant (JPEG q35 van een brief: 1,40); de afwijking zit dan in een paar blokken (de ingevulde
  tekst), en per blok scheidt het wel (verschillend ≥ 6,34, varianten ≤ 3,58). Beide grenzen
  zijn gemeten en liggen vast in tests/dupes/test_phash.py (docs/fase-6/z3.md).
- Tekst telt mee: hebben beide inhouden tekst en is de gevouwen tekst anders, dan is een paar
  nooit "zeker" (text_key, text_differs).

Wat in het renderproces draait (app/render_meta.py) laadt deze module pas als er een afbeelding
is; hij leest geen bestand zelf, alleen geopende Pillow-afbeeldingen.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

# Spreiding van het 32 × 32-grijsbeeld waaronder een beeld vlak heet (V5: egaal 0,0, foto's ≥ 6,5)
FLAT = 3.0
# Kandidaten: Hamming-afstand van de pHash (Keuze 35)
NEAR = 10
SURE = 6
# De tweede controle (Keuze 35)
ASPECT = 0.02
DHASH = 10
# Gemeten op de fixtures van §3.6 met de voorbeelden van 256 (tests/dupes/test_phash.py): echte
# varianten (half, kwart, JPEG q35–q75) hebben MAD64 ≤ 1,40 en per blok ≤ 3,58; verschillende
# brieven, formulieren en schermen met pHash ≤ 6 hebben per blok ≥ 6,34 (MAD64 vanaf 0,86)
MAD64 = 2.0
BLOCK64 = 5.0
# Een paar dat niet te meten was (een voorbeeld weg of kapot): dup_pair.mad is NOT NULL
NO_MAD = -1.0

MASK = (1 << 64) - 1
_SIDE = 32
_LOW = 8
# cos((2x + 1) u π / 64) voor u < 8 en x < 32: de DCT-II van een rij van 32
_COS = [
    [math.cos((2 * x + 1) * u * math.pi / (2 * _SIDE)) for x in range(_SIDE)] for u in range(_LOW)
]


def _pillow() -> Any:
    from PIL import Image

    return Image


def grey(img: Any, size: tuple[int, int]) -> Any:
    """Het grijsbeeld van img op deze maat (LANCZOS: anti-aliasing bij verkleinen)."""
    Image = _pillow()
    return img.convert("L").resize(size, Image.Resampling.LANCZOS)


def spread(g32: Any) -> float:
    """De standaardafwijking van de grijswaarden (0–255)."""
    data = g32.tobytes()
    mean = sum(data) / len(data)
    return math.sqrt(sum((v - mean) ** 2 for v in data) / len(data))


def _dct_low(pixels: list[int]) -> list[float]:
    """De 8 × 8 laagste coëfficiënten van de DCT-II van 32 × 32 waarden (rij na rij), zonder
    schaal (die doet niets voor de mediaan)."""
    rows = [pixels[y * _SIDE : (y + 1) * _SIDE] for y in range(_SIDE)]
    # Eerst elke rij naar 8 frequenties, dan elke kolom van die 8
    by_row = [[sum(c * p for c, p in zip(cu, row, strict=True)) for cu in _COS] for row in rows]
    out = []
    for v in range(_LOW):
        cv = _COS[v]
        for u in range(_LOW):
            out.append(sum(cv[y] * by_row[y][u] for y in range(_SIDE)))
    return out


def _bits(flags: list[bool]) -> int:
    value = 0
    for flag in flags:
        value = (value << 1) | (1 if flag else 0)
    return value


def phash(img: Any) -> int | None:
    """De pHash van img (64 bits, zonder teken), of None als het beeld vlak is."""
    g = grey(img, (_SIDE, _SIDE))
    if spread(g) < FLAT:
        return None
    low = _dct_low(list(g.tobytes()))
    median = sorted(low)[len(low) // 2 - 1 : len(low) // 2 + 1]
    middle = (median[0] + median[1]) / 2
    return _bits([c > middle for c in low])


def dhash(img: Any) -> int:
    """De dHash van img: 9 × 8 grijs, per rij of elke pixel lichter is dan de vorige."""
    px = grey(img, (9, 8)).tobytes()
    return _bits([px[y * 9 + x + 1] > px[y * 9 + x] for y in range(8) for x in range(8)])


def hamming(a: int, b: int) -> int:
    """Het aantal bits waarin twee hashes verschillen (met of zonder teken)."""
    return ((a ^ b) & MASK).bit_count()


def signed(h: int) -> int:
    """Een hash zonder teken als getal met teken (BigInteger van SQLite)."""
    h &= MASK
    return h - (1 << 64) if h >= 1 << 63 else h


def pieces(h: int) -> tuple[int, int, int, int]:
    """De vier stukken van 16 bits (h0 de laagste), zoals in blob_hash en app/search/bench.py."""
    h &= MASK
    return h & 0xFFFF, (h >> 16) & 0xFFFF, (h >> 32) & 0xFFFF, (h >> 48) & 0xFFFF


@dataclass(frozen=True)
class Look:
    """Wat de tweede controle van één voorbeeld nodig heeft: maat, dHash en 64 × 64 grijs."""

    width: int
    height: int
    dhash: int
    g64: bytes


def look(img: Any) -> Look:
    return Look(img.width, img.height, dhash(img), grey(img, (64, 64)).tobytes())


def aspect_ok(a: Look, b: Look) -> bool:
    """De verhouding (breedte / hoogte) van beide binnen ASPECT van elkaar."""
    if min(a.width, a.height, b.width, b.height) <= 0:
        return False
    ra, rb = a.width / a.height, b.width / b.height
    return abs(ra / rb - 1) <= ASPECT


def mad64(a: bytes, b: bytes) -> tuple[float, float]:
    """(MAD64, de grootste per blok): de gemiddelde absolute afwijking van twee grijsbeelden
    van 64 × 64 (0–255), over het geheel en in het ergste blok van 8 × 8."""
    diff = bytes(abs(x - y) for x, y in zip(a, b, strict=True))
    worst = 0
    for by in range(0, 64, 8):
        for bx in range(0, 64, 8):
            total = sum(sum(diff[y * 64 + bx : y * 64 + bx + 8]) for y in range(by, by + 8))
            worst = max(worst, total)
    return sum(diff) / len(diff), worst / 64


def compare(a: Look, b: Look) -> dict[str, Any]:
    """De metingen van de tweede controle: verhouding, dHash-afstand, MAD64 en per blok."""
    mad, block = mad64(a.g64, b.g64)
    return {
        "aspect": aspect_ok(a, b),
        "dhash": hamming(a.dhash, b.dhash),
        "mad": round(mad, 3),
        "block": round(block, 3),
    }


def check_ok(measured: dict[str, Any] | None) -> bool:
    """Slaagt de tweede controle? Niet gemeten: nee."""
    if not isinstance(measured, dict):
        return False
    mad, block, dist = measured.get("mad"), measured.get("block"), measured.get("dhash")
    numbers = (mad, block)
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in numbers):
        return False
    if not isinstance(dist, int) or isinstance(dist, bool) or mad < 0 or block < 0:
        return False
    return measured.get("aspect") is True and dist <= DHASH and mad <= MAD64 and block <= BLOCK64


def text_key(body: str | None, ocr: str | None) -> str | None:
    """Een hash van de gevouwen tekst (body en OCR, zonder de metadata); geen tekst: None."""
    from app.text import fold

    text = " ".join(fold(f"{body or ''} {ocr or ''}").split())
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_differs(a: str | None, b: str | None) -> bool:
    """Hebben beide tekst, en is die anders? (Keuze 35: dan nooit "zeker".)"""
    return a is not None and b is not None and a != b


def level(dist: int, check: bool, text_differs: bool = False) -> str | None:
    """Het niveau van een paar: "zeker" (pHash ≤ SURE, de tweede controle, en geen andere tekst),
    "lijkt" (pHash ≤ NEAR), of None (geen paar)."""
    if dist > NEAR:
        return None
    if dist <= SURE and check and not text_differs:
        return "zeker"
    return "lijkt"


def group_key(shas: list[str] | set[str] | tuple[str, ...]) -> str:
    """De sleutel van een groep: sha256 van de gesorteerde shas (dup_ignore)."""
    return hashlib.sha256("\n".join(sorted(shas)).encode("ascii")).hexdigest()
