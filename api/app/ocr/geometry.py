"""Een punt uit het rechte beeld van OCR terug naar het beeld zoals de viewer het toont
(bouwontwerp fase 6, Keuze 22: "de app draait de middens van de vakjes terug").

Kind 1 (app.render ocr_page) zet de bron recht (deskew.py): het grijze beeld is de bron over
−skew graden gedraaid om zijn midden en groter gemaakt. De worker geeft de vakjes in dat rechte
beeld (width × height), met skew en de maat van de bron (source: zoals de viewer hem toont, na de
EXIF-draaiing, eventueel kleiner gemaakt). Alleen het midden van een vak gaat terug: een recht
vak wordt na het draaien een scheef vak, en de viewer tekent rechte markeringen.
"""

from __future__ import annotations

import math


def back(
    x: float, y: float, skew: float, size: tuple[int, int], source: tuple[int, int]
) -> tuple[float, float]:
    """(x, y) in het rechte beeld (size) → dezelfde plek in de bron (source)."""
    width, height = size
    dx, dy = x - width / 2, y - height / 2
    a = math.radians(skew)
    c, s = math.cos(a), math.sin(a)
    return dx * c + dy * s + source[0] / 2, -dx * s + dy * c + source[1] / 2


def centre(
    box: tuple[float, float, float, float],
    skew: float,
    size: tuple[int, int],
    source: tuple[int, int],
) -> tuple[float, float]:
    """Het midden van een vak (links, boven, breedte, hoogte) in het rechte beeld, in de bron."""
    left, top, w, h = box
    return back(left + w / 2, top + h / 2, skew, size, source)
