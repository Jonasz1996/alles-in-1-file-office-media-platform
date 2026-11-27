"""Van punten naar een activiteit: afstand, tijd, bewegingstijd, snelheid, stijgen en dalen,
splits per kilometer en beste tijden (zoals Strava's "best efforts").

De ruwe kolommen (Raw) bewaart de database (pack.py); analyse() rekent er alles uit, bij het
importeren en opnieuw als je de sport of het hoogtefilter van een activiteit verandert. Zo blijft
er één weg van punten naar cijfers.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.sport import elevation
from app.sport.parse import Parsed

R_EARTH = 6_371_008.8
# Een plek die sneller dan dit heen en terug springt, is een GPS-fout (180 km/u)
SPIKE_SPEED = 50.0
# Snelheid over dit venster (seconden aan elke kant): bewegen, de grafiek en de topsnelheid
SPEED_WINDOW = 5.0

SPORTS = ("run", "ride", "walk", "hike", "swim", "ski", "row", "other")
# Onder deze snelheid (m/s) sta je stil, per sport
MIN_SPEED = {"run": 0.7, "walk": 0.5, "hike": 0.4, "ride": 1.0, "ski": 0.8, "swim": 0.2}
# De afstanden van de beste tijden, per sport (meter)
EFFORTS: dict[str, tuple[float, ...]] = {
    "run": (400, 1000, 1609.344, 5000, 10000, 21097.5, 42195),
    "walk": (1000, 5000, 10000),
    "hike": (1000, 5000, 10000),
    "ride": (5000, 10000, 20000, 40000, 50000, 100000),
}

_HINTS = {
    "run": ("run", "running", "hardlo", "lopen", "trail", "jog", "treadmill"),
    "ride": ("ride", "bike", "biking", "cycl", "fiets", "wielren", "mtb", "gravel", "e-bike"),
    "hike": ("hike", "hiking", "wander", "berg", "mountaineer"),
    "walk": ("walk", "wandel", "stap"),
    "swim": ("swim", "zwem"),
    "ski": ("ski", "snowboard", "langlauf"),
    "row": ("row", "roei", "kayak", "kajak", "canoe", "kano", "sup", "paddle"),
}
# Strava zet een getal in <type> (GPX van Strava): 1 rit, 9 loop, 10 wandeling, 11 hike
_STRAVA_NUM = {"1": "ride", "4": "hike", "9": "run", "10": "walk", "11": "hike", "16": "swim"}


@dataclass
class Raw:
    """De ruwe kolommen zoals ze in de database staan (pack.py). t: seconden sinds de start
    (None zonder tijd), seg: 1 waar een nieuw stuk begint (de pauzeknop)."""

    t: list[float | None]
    lat: list[float]
    lon: list[float]
    ele: list[float | None]
    hr: list[float | None]
    cad: list[float | None]
    power: list[float | None]
    seg: list[int]

    def __len__(self) -> int:
        return len(self.lat)


@dataclass
class Split:
    km: int
    distance: float
    seconds: float
    elev: float
    hr: float | None


@dataclass
class Analysis:
    distance: float = 0.0
    elapsed: float = 0.0
    moving: float = 0.0
    gain: float = 0.0
    loss: float = 0.0
    ele_min: float | None = None
    ele_max: float | None = None
    max_speed: float = 0.0
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_cad: float | None = None
    avg_power: float | None = None
    fixed: int = 0
    has_ele: bool = False
    has_time: bool = False
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # zuid, west, noord, oost
    dist: list[float] = field(default_factory=list)
    ele: list[float] = field(default_factory=list)
    speed: list[float | None] = field(default_factory=list)
    splits: list[Split] = field(default_factory=list)
    # afstand (m, als tekst voor JSON) -> seconden
    efforts: dict[str, float] = field(default_factory=dict)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def from_parsed(parsed: Parsed) -> tuple[Raw, datetime | None]:
    """De punten als kolommen, met de tijd in seconden sinds het eerste punt met een tijd.
    Punten met een tijd die terugloopt (een fout van het apparaat) gaan eruit, net als losse
    GPS-sprongen (heen en terug sneller dan SPIKE_SPEED)."""
    pts = parsed.points
    start = next((p.time for p in pts if p.time is not None), None)
    keep = []
    last_t: datetime | None = None
    for p in pts:
        if p.time is not None and last_t is not None and p.time < last_t:
            continue
        if p.time is not None:
            last_t = p.time
        keep.append(p)
    pts = _drop_spikes(keep)
    raw = Raw(
        t=[(p.time - start).total_seconds() if p.time and start else None for p in pts],
        lat=[p.lat for p in pts],
        lon=[p.lon for p in pts],
        ele=[p.ele for p in pts],
        hr=[p.hr for p in pts],
        cad=[p.cad for p in pts],
        power=[p.power for p in pts],
        seg=[1 if p.seg_start else 0 for p in pts],
    )
    return raw, start


def _drop_spikes(pts: list) -> list:
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        if a.time and b.time and c.time:
            t1 = (b.time - a.time).total_seconds()
            t2 = (c.time - b.time).total_seconds()
            t3 = (c.time - a.time).total_seconds()
            if t1 > 0 and t2 > 0 and t3 > 0:
                v1 = haversine(a.lat, a.lon, b.lat, b.lon) / t1
                v2 = haversine(b.lat, b.lon, c.lat, c.lon) / t2
                v3 = haversine(a.lat, a.lon, c.lat, c.lon) / t3
                if v1 > SPIKE_SPEED and v2 > SPIKE_SPEED and v3 <= SPIKE_SPEED:
                    continue
        out.append(b)
    out.append(pts[-1])
    return out


def guess_sport(hint: str | None, raw: Raw | None = None, a: Analysis | None = None) -> str:
    """De sport uit wat het bestand zegt; anders uit de snelheid."""
    if hint:
        h = hint.strip().lower()
        if h in _STRAVA_NUM:
            return _STRAVA_NUM[h]
        for sport, words in _HINTS.items():
            if any(w in h for w in words):
                return sport
    if a is not None and a.moving > 0:
        v = a.distance / a.moving
        if v > 4.5:
            return "ride"
        if v > 2.1:
            return "run"
        if a.distance > 0 and a.gain / max(a.distance, 1.0) > 0.04:
            return "hike"
        return "walk"
    return "other"


def _window_speed(t: Sequence[float | None], dist: Sequence[float]) -> list[float | None]:
    """Snelheid rond elk punt over ±SPEED_WINDOW seconden (None zonder tijd)."""
    n = len(t)
    out: list[float | None] = [None] * n
    if n < 2 or any(x is None for x in t):
        return out
    tt: list[float] = t  # type: ignore[assignment]
    lo = hi = 0
    for i in range(n):
        while tt[i] - tt[lo] > SPEED_WINDOW:
            lo += 1
        while hi + 1 < n and tt[hi + 1] - tt[i] <= SPEED_WINDOW:
            hi += 1
        a, b = lo, hi
        if a == b:
            a, b = max(0, i - 1), min(n - 1, i + 1)
        dt = tt[b] - tt[a]
        out[i] = (dist[b] - dist[a]) / dt if dt > 0 else 0.0
    return out


def _mean(xs: Sequence[float | None], *, skip_zero: bool = False) -> float | None:
    vals = [x for x in xs if x is not None and (x > 0 or not skip_zero)]
    return sum(vals) / len(vals) if vals else None


def _interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """ys op x (xs stijgend), lineair; buiten het bereik het eerste of laatste."""
    lo, hi = 0, len(xs) - 1
    if x <= xs[0]:
        return ys[0]
    if x >= xs[hi]:
        return ys[hi]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    f = (x - xs[lo]) / span if span > 0 else 0.0
    return ys[lo] + f * (ys[hi] - ys[lo])


def analyse(raw: Raw, sport: str, *, ele_filter: bool = True) -> Analysis:
    n = len(raw)
    a = Analysis()
    if n == 0:
        return a
    dist = [0.0] * n
    for i in range(1, n):
        d = haversine(raw.lat[i - 1], raw.lon[i - 1], raw.lat[i], raw.lon[i])
        dist[i] = dist[i - 1] + d
    a.dist = dist
    a.distance = dist[-1]
    a.bbox = (min(raw.lat), min(raw.lon), max(raw.lat), max(raw.lon))
    a.has_time = all(x is not None for x in raw.t) and n > 1
    filt = elevation.filter_elevation(raw.ele, dist, raw.t, enabled=ele_filter)
    a.ele = filt.ele
    a.fixed = filt.fixed
    a.has_ele = filt.has_ele
    if filt.has_ele:
        a.gain, a.loss = elevation.gain_loss(filt.ele)
        a.ele_min, a.ele_max = min(filt.ele), max(filt.ele)

    # Tijd en bewegen
    moving_cum = [0.0] * n
    if a.has_time:
        t: list[float] = raw.t  # type: ignore[assignment]
        a.elapsed = t[-1] - t[0]
        speed = _window_speed(t, dist)
        a.speed = speed
        vmin = MIN_SPEED.get(sport, 0.5)
        for i in range(1, n):
            dt = t[i] - t[i - 1]
            moving = (
                not raw.seg[i]
                and dt > 0
                and (speed[i] or 0.0) >= vmin
                and (dist[i] - dist[i - 1]) / dt >= vmin * 0.5
            )
            moving_cum[i] = moving_cum[i - 1] + (dt if moving else 0.0)
        a.moving = moving_cum[-1]
        a.max_speed = max((s for s in speed if s is not None), default=0.0)
        a.efforts = _efforts(t, dist, EFFORTS.get(sport, ()))
    else:
        a.speed = [None] * n

    a.avg_hr = _mean(raw.hr, skip_zero=True)
    hrs = [h for h in raw.hr if h]
    a.max_hr = max(hrs) if hrs else None
    a.avg_cad = _mean(raw.cad, skip_zero=True)
    a.avg_power = _mean(raw.power)
    a.splits = _splits(raw, a, moving_cum)
    return a


def _splits(raw: Raw, a: Analysis, moving_cum: list[float]) -> list[Split]:
    """Per volle kilometer (en het stuk erna) de bewegingstijd, het hoogteverschil en de
    gemiddelde hartslag."""
    out: list[Split] = []
    if a.distance < 1 or not a.has_time:
        return out
    dist = a.dist
    km = 1
    prev_d = 0.0
    prev_t = 0.0
    prev_e = a.ele[0] if a.ele else 0.0
    idx = 0
    while prev_d < a.distance - 1:
        d = min(km * 1000.0, a.distance)
        tm = _interp(dist, moving_cum, d)
        e = _interp(dist, a.ele, d) if a.ele else 0.0
        start = idx
        while idx < len(dist) and dist[idx] <= d:
            idx += 1
        hr = _mean(raw.hr[start:idx], skip_zero=True)
        if d - prev_d >= 50 or not out:
            out.append(
                Split(km=km, distance=d - prev_d, seconds=tm - prev_t, elev=e - prev_e, hr=hr)
            )
        prev_d, prev_t, prev_e = d, tm, e
        km += 1
    return out


def _efforts(t: Sequence[float], dist: Sequence[float], targets: Sequence[float]) -> dict:
    """De snelste tijd over elke afstand in targets (verstreken tijd, zoals Strava)."""
    out: dict[str, float] = {}
    n = len(t)
    for target in targets:
        if dist[-1] < target:
            continue
        best = math.inf
        i = 0
        for j in range(1, n):
            while i + 1 < j and dist[j] - dist[i + 1] >= target:
                i += 1
            span = dist[j] - dist[i]
            if span >= target:
                # Naar de precieze afstand terugschalen binnen het venster
                secs = (t[j] - t[i]) * target / span
                if secs < best:
                    best = secs
        if math.isfinite(best) and best > 0:
            out[f"{target:g}"] = round(best, 1)
    return out


def default_name(sport: str, start: datetime | None) -> str:
    """Zoals Strava: "Ochtendloop", "Middagrit", ... in de tijdzone van het platform."""
    nouns = {
        "run": "loop",
        "ride": "rit",
        "walk": "wandeling",
        "hike": "bergwandeling",
        "swim": "zwemsessie",
        "ski": "skitocht",
        "row": "roeitocht",
        "other": "activiteit",
    }
    noun = nouns.get(sport, "activiteit")
    if start is None:
        return noun.capitalize()
    h = start.hour
    part = "Nacht" if h < 5 else "Ochtend" if h < 12 else "Middag" if h < 18 else "Avond"
    return f"{part}{noun}"
