"""Tegels van Webmercator op zoom 16 (photo_geo.x16 en y16) en het venster van de kaart
(bouwontwerp fase 6, Keuzes 32 en 4a). Alleen de standaardbibliotheek.

Een cel op zoom z (0–14) is (x16 >> s, y16 >> s) met s = 14 − z: vier cellen per tegelbreedte.
Het venster (bbox) is "west,zuid,oost,noord" in graden; west > oost gaat over 180° heen en
wordt twee stukken in x.
"""

from __future__ import annotations

import math

N = 1 << 16
MAX_LAT = 85.05112878  # de rand van Webmercator


def tile16(lat: float, lon: float) -> tuple[int, int]:
    """De tegel op zoom 16, zoals app/search/bench.tegel16 (een test vergelijkt ze)."""
    x = int((lon + 180.0) / 360.0 * N)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * N)
    return min(max(x, 0), N - 1), min(max(y, 0), N - 1)


def _y(lat: float) -> int:
    lat = min(max(lat, -MAX_LAT), MAX_LAT)
    return tile16(lat, 0.0)[1]


def lon_of(x: float) -> float:
    return x / N * 360.0 - 180.0


def lat_of(y: float) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / N))))


def shift(z: int) -> int:
    return 14 - z


class BadBox(ValueError):
    """Het venster klopt niet (de route maakt er 422 invalid_input van)."""


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """ "west,zuid,oost,noord" als vier eindige getallen binnen de wereld, zuid ≤ noord."""
    parts = text.split(",")
    if len(parts) != 4:
        raise BadBox("vier getallen")
    try:
        w, s, e, n = (float(p) for p in parts)
    except ValueError:
        raise BadBox("geen getal") from None
    if not all(math.isfinite(v) for v in (w, s, e, n)):
        raise BadBox("niet eindig")
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= n <= 90):
        raise BadBox("buiten de wereld")
    return w, s, e, n


def ranges(w: float, s: float, e: float, n: float) -> tuple[list[tuple[int, int]], int, int]:
    """De stukken in x (één, of twee over 180° heen) en y0..y1 op zoom 16. Noord is de kleinste
    y."""
    y0, y1 = _y(n), _y(s)
    if w <= e:
        xs = [(tile16(0.0, w)[0], tile16(0.0, e)[0])]
    else:
        xs = [(tile16(0.0, w)[0], N - 1), (0, tile16(0.0, e)[0])]
    return xs, y0, y1
