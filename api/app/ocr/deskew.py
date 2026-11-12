"""De eigen rechtzetting van een scan (bouwontwerp fase 6, Keuze 22; H4: vanaf ±7° breekt de
herkenning zonder). Draait in kind 1 (app.render ocr_page), met Pillow.

Projectie: een kleine kopie (hooguit SIDE pixels aan de lange kant) wordt zwart-wit (de drempel
van Otsu: inkt 255, papier 0), en voor elke hoek tussen −LIMIT en +LIMIT gedraaid. Per hoek de
som per rij (resize naar één kolom, BOX); rechte tekstregels geven scherpe pieken en dalen, dus
de som van de kwadraten van de verschillen tussen opeenvolgende rijen is dan het grootst. Eerst
in stappen van 1°, dan rond de beste 0,5°, 0,25° en 0,1°. Gemeten op een A4 van 300 dpi: binnen
0,05° van de echte hoek, ±200 ms meten en ±300 ms draaien (één core).

Wint de beste hoek niet duidelijk van 0° (MIN_GAIN), of is hij kleiner dan MIN_ANGLE, dan blijft
het beeld zoals het is: een foto zonder tekstregels wordt niet willekeurig gedraaid.

measure() geeft de draaiing die de bron recht zet (graden tegen de klok in); turn() draait om het
midden, groter gemaakt zodat er niets afvalt (expand), met de papierkleur (de mediaan) in de
hoeken. De scheefstand van de bron (skew, bij de pagina) is min die draaiing; geometry.py rekent
een punt terug.
"""

from __future__ import annotations

LIMIT = 12
SIDE = 800
MIN_ANGLE = 0.3  # kleiner stoort de herkenning niet; draaien maakt het beeld alleen zachter
MIN_GAIN = 1.1  # de beste hoek moet zoveel beter scoren dan 0°


def _otsu(hist: list[int]) -> int:
    """De drempel van Otsu op een histogram van 256 vakken."""
    total = sum(hist)
    weighted = sum(i * h for i, h in enumerate(hist))
    below = 0
    below_sum = 0.0
    best, threshold = -1.0, 127
    for i in range(256):
        below += hist[i]
        if below == 0:
            continue
        above = total - below
        if above == 0:
            break
        below_sum += i * hist[i]
        mean_below = below_sum / below
        mean_above = (weighted - below_sum) / above
        between = below * above * (mean_below - mean_above) ** 2
        if between > best:
            best, threshold = between, i
    return threshold


def _median(hist: list[int]) -> int:
    half = sum(hist) / 2
    seen = 0
    for i, h in enumerate(hist):
        seen += h
        if seen >= half:
            return i
    return 255


def _score(ink, angle: float) -> float:
    from PIL import Image

    turned = ink.rotate(angle, resample=Image.BILINEAR, expand=True, fillcolor=0)
    rows = turned.resize((1, turned.height), Image.BOX).tobytes()
    return float(sum((rows[i + 1] - rows[i]) ** 2 for i in range(len(rows) - 1)))


def measure(img) -> float:
    """De hoek (graden, tegen de klok in) waarover img gedraaid moet om recht te staan, of 0.0.
    img is een grijs beeld (mode L)."""
    from PIL import Image

    scale = SIDE / max(img.size)
    if scale < 1:
        size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        small = img.resize(size, Image.BOX)
    else:
        small = img
    threshold = _otsu(small.histogram())
    ink = small.point([255 if v <= threshold else 0 for v in range(256)])
    scores = {float(a): _score(ink, float(a)) for a in range(-LIMIT, LIMIT + 1)}
    best = max(scores, key=lambda a: (scores[a], -abs(a)))  # gelijk: de kleinste hoek
    for step in (0.5, 0.25, 0.1):
        for a in (best - step, best + step):
            if -LIMIT <= a <= LIMIT and a not in scores:
                scores[a] = _score(ink, a)
        best = max((best - step, best, best + step), key=lambda a: scores.get(a, -1.0))
    if abs(best) < MIN_ANGLE or scores[best] <= MIN_GAIN * scores[0.0]:
        return 0.0
    return round(best, 2)


def expanded(size: tuple[int, int], angle: float) -> tuple[int, int]:
    """Hoe groot het beeld na turn() hooguit wordt (de rand van Pillow, plus 1 voor afronding)."""
    import math

    width, height = size
    c, s = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
    return math.ceil(width * c + height * s) + 1, math.ceil(width * s + height * c) + 1


def turn(img, angle: float):
    """img over angle graden tegen de klok in gedraaid om zijn midden, groter gemaakt (expand),
    met de papierkleur (de mediaan) in de hoeken."""
    from PIL import Image

    paper = _median(img.histogram())
    return img.rotate(angle, resample=Image.BILINEAR, expand=True, fillcolor=paper)
