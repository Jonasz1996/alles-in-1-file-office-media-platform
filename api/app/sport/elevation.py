"""Het hoogtefilter: van ruwe GPS-hoogtes naar een profiel waar je stijgen en dalen uit kunt halen.

Het probleem (Jonas, fase "Sport"): valt de GPS weg, dan zakt de hoogte naar 0 (of naar een andere
onzinwaarde) en springt ze later terug. Telt een tracker dat mee, dan krijg je honderden meters
"stijgen" die er nooit waren. Daarnaast heeft GPS-hoogte ruis van een paar meter, die bij elkaar
opgeteld ook veel te veel stijgen geeft.

Wat het filter doet, in deze volgorde:

1. Ongeldig: geen hoogte, niet eindig, of buiten -450 .. 9000 m (lager dan de Dode Zee, hoger dan
   de Everest).
2. Sprongen: tussen twee opeenvolgende geldige punten een verschil dat niemand kan klimmen of
   dalen: meer dan max(JUMP_MIN, VMAX * dt) met dt de tijd ertussen (zonder tijden: meer dan
   max(JUMP_MIN, GRADE_MAX * afstand)). Op elke sprong knipt het filter het spoor in stukken.
3. Welk stuk klopt: vanaf een referentiestuk gaat het filter naar links en naar rechts. Een
   volgend stuk is goed als het aansluit op het laatste goede stuk: het verschil tussen de
   laatste goede hoogte en de eerste van dit stuk is niet groter dan max(GAP_MIN, GAP_GRADE *
   de afgelegde afstand ertussen). Zo telt na een uitval van vijf minuten een echte klim van
   40 m nog mee, maar de val naar 0 en de sprong terug niet. Een stuk dat niet aansluit, is
   fout; het laatste goede stuk blijft de maat. Als referentie kiest het filter het stuk
   waarvan die keten samen het langst duurt (een lange uitval kan zelf het langste stuk zijn;
   een stuk dat helemaal op 0 staat, telt maar half).
4. Opvullen: alles wat fout of ongeldig is, krijgt een hoogte op de rechte lijn (over de afstand)
   tussen de goede punten ervoor en erna; aan het begin of eind de eerste of laatste goede.
5. Gladstrijken: een voortschrijdend gemiddelde over de afstand (SMOOTH_M aan elke kant), twee
   keer (dat is een driehoek als gewicht): ruis weg, heuvels blijven.

Stijgen en dalen telt daarna met een drempel (hysterese, THRESHOLD): pas als de hoogte
THRESHOLD meter van het laatste ankerpunt af is, telt het verschil, en het anker schuift mee.
Zo tellen kleine golfjes van ruis niet, en een echte klim van 1 m per stap wel.

Geen data uit een hoogtemodel (DEM) zoals Strava dat doet: dat vraagt een bron van buiten.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

ELE_MIN = -450.0
ELE_MAX = 9000.0
# Een sprong: meer dan dit verticaal tussen twee punten, en sneller dan VMAX m/s
JUMP_MIN = 15.0
VMAX = 5.0
GRADE_MAX = 1.0
# Aansluiten na een gat: dit verschil mag, of een helling van GAP_GRADE over de afstand
GAP_MIN = 20.0
GAP_GRADE = 0.30
SMOOTH_M = 25.0
MAX_REFS = 40
THRESHOLD = 3.0


@dataclass
class Filtered:
    ele: list[float]
    # Zoveel punten kregen een andere hoogte dan ze hadden (uitval, sprong of geen hoogte)
    fixed: int
    # Of er überhaupt hoogtes waren
    has_ele: bool


def _valid(e: float | None) -> bool:
    return e is not None and math.isfinite(e) and ELE_MIN <= e <= ELE_MAX


def _jump(de: float, dt: float | None, dd: float) -> bool:
    de = abs(de)
    if de <= JUMP_MIN:
        return False
    if dt is not None and dt > 0:
        return de > VMAX * dt
    return de > GRADE_MAX * max(dd, 1.0)


def bad_points(
    ele: Sequence[float | None], dist: Sequence[float], times: Sequence[float | None]
) -> list[bool]:
    """Per punt: True als de hoogte niet te vertrouwen is (stap 1–3)."""
    n = len(ele)
    bad = [not _valid(e) for e in ele]
    good_idx = [i for i in range(n) if not bad[i]]
    if not good_idx:
        return [True] * n
    # Stap 2: stukken tussen sprongen (alleen over geldige punten)
    runs: list[list[int]] = [[good_idx[0]]]
    for a, b in zip(good_idx, good_idx[1:], strict=False):
        ta, tb = times[a], times[b]
        dt = tb - ta if ta is not None and tb is not None else None
        if _jump(ele[b] - ele[a], dt, dist[b] - dist[a]):  # type: ignore[operator]
            runs.append([b])
        else:
            runs[-1].append(b)
    if len(runs) == 1:
        return bad

    def weight(run: list[int]) -> float:
        ta, tb = times[run[0]], times[run[-1]]
        if ta is not None and tb is not None:
            return (tb - ta) + len(run) * 1e-6
        return float(len(run))

    def fits(last: int, first: int) -> bool:
        de = abs(ele[first] - ele[last])  # type: ignore[operator]
        return de <= max(GAP_MIN, GAP_GRADE * abs(dist[first] - dist[last]))

    def chain(ref: int) -> list[bool]:
        keep = [False] * len(runs)
        keep[ref] = True
        last = runs[ref][-1]
        for k in range(ref + 1, len(runs)):
            if fits(last, runs[k][0]):
                keep[k] = True
                last = runs[k][-1]
        last = runs[ref][0]
        for k in range(ref - 1, -1, -1):
            if fits(last, runs[k][-1]):
                keep[k] = True
                last = runs[k][0]
        return keep

    def flat_zero(run: list[int]) -> bool:
        return all(abs(ele[i]) < 0.5 for i in run)  # type: ignore[arg-type]

    # De referentie: het stuk waarvan de keten die erop aansluit het langst is (een lange
    # uitval kan zelf het langste stuk zijn). Een stuk dat helemaal op 0 staat, telt half. Bij
    # heel veel stukken alleen de langste MAX_REFS als kandidaat.
    weights = [weight(r) * (0.5 if flat_zero(r) else 1.0) for r in runs]
    order = sorted(range(len(runs)), key=lambda k: -weights[k])[:MAX_REFS]
    best: list[bool] | None = None
    best_w = -1.0
    for ref in order:
        keep = chain(ref)
        w = sum(weights[k] for k in range(len(runs)) if keep[k])
        if w > best_w:
            best, best_w = keep, w
    keep = best or [True] * len(runs)
    for k, run in enumerate(runs):
        if not keep[k]:
            for i in run:
                bad[i] = True
    return bad


def _fill(ele: Sequence[float | None], bad: list[bool], dist: Sequence[float]) -> list[float]:
    n = len(ele)
    out = [0.0] * n
    prev = -1
    i = 0
    while i < n:
        if not bad[i]:
            out[i] = float(ele[i])  # type: ignore[arg-type]
            prev = i
            i += 1
            continue
        j = i
        while j < n and bad[j]:
            j += 1
        for k in range(i, j):
            if prev < 0 and j >= n:
                out[k] = 0.0
            elif prev < 0:
                out[k] = float(ele[j])  # type: ignore[arg-type]
            elif j >= n:
                out[k] = out[prev]
            else:
                span = dist[j] - dist[prev]
                f = (dist[k] - dist[prev]) / span if span > 0 else (k - prev) / (j - prev)
                out[k] = out[prev] + f * (float(ele[j]) - out[prev])  # type: ignore[arg-type]
        i = j
    return out


def smooth(values: Sequence[float], dist: Sequence[float], half: float = SMOOTH_M) -> list[float]:
    """Gemiddelde over de punten binnen `half` meter afstand aan elke kant (O(n))."""
    n = len(values)
    if n < 3 or half <= 0:
        return list(values)
    pre = [0.0]
    for v in values:
        pre.append(pre[-1] + v)
    out = [0.0] * n
    lo = hi = 0
    for i in range(n):
        while dist[i] - dist[lo] > half:
            lo += 1
        while hi + 1 < n and dist[hi + 1] - dist[i] <= half:
            hi += 1
        out[i] = (pre[hi + 1] - pre[lo]) / (hi - lo + 1)
    return out


def filter_elevation(
    ele: Sequence[float | None],
    dist: Sequence[float],
    times: Sequence[float | None],
    *,
    enabled: bool = True,
) -> Filtered:
    """Het hele filter. enabled=False: alleen ongeldige punten opvullen (geen sprongen, geen
    gladstrijken), voor wie het ruwe profiel wil (per activiteit uit te zetten)."""
    has = any(_valid(e) for e in ele)
    if not has:
        return Filtered(ele=[0.0] * len(ele), fixed=0, has_ele=False)
    bad = bad_points(ele, dist, times) if enabled else [not _valid(e) for e in ele]
    filled = _fill(ele, bad, dist)
    out = smooth(smooth(filled, dist), dist) if enabled else filled
    return Filtered(ele=out, fixed=sum(bad), has_ele=True)


def gain_loss(ele: Sequence[float], threshold: float = THRESHOLD) -> tuple[float, float]:
    """Stijgen en dalen met hysterese (zie boven)."""
    if not ele:
        return 0.0, 0.0
    up = down = 0.0
    anchor = ele[0]
    for e in ele[1:]:
        d = e - anchor
        if d >= threshold:
            up += d
            anchor = e
        elif d <= -threshold:
            down -= d
            anchor = e
    return up, down
