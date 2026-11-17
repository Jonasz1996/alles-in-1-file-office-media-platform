"""Tellers per uur voor /beheer (bouwontwerp fase 6, Keuze 51; Z7): hoe vaak ronde B en ronde C
de tijdsgrens raakten (app/search/engine.py), en hoe vaak zoeken 429 search_busy gaf
(app/search/limits.py).

Alleen aantallen, in het geheugen van dit proces, per minuut opgeteld: nooit een vraag, een
gebruiker of een tijd per vraag. Na een herstart begint het weer bij nul. Geen imports van de
app, zodat engine.py en limits.py dit bestand zonder kring kunnen gebruiken.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

KINDS = ("deadline_b", "deadline_c", "busy")
MINUTES = 60  # het venster: het afgelopen uur

_lock = threading.Lock()
_counts: dict[str, dict[int, int]] = {kind: {} for kind in KINDS}
# De klok in minuten; tests zetten hem vast
clock: Callable[[], float] = time.monotonic


def _minute() -> int:
    return int(clock() // 60)


def _prune(buckets: dict[int, int], minute: int) -> None:
    for old in [m for m in buckets if m <= minute - MINUTES]:
        del buckets[old]


def note(kind: str) -> None:
    """Eén keer kind (deadline_b, deadline_c of busy). Faalt nooit voor een bekende soort."""
    minute = _minute()
    with _lock:
        buckets = _counts[kind]
        buckets[minute] = buckets.get(minute, 0) + 1
        if len(buckets) > MINUTES:
            _prune(buckets, minute)


def counts() -> dict[str, int]:
    """Per soort het aantal in het afgelopen uur (de lopende minuut telt mee)."""
    minute = _minute()
    with _lock:
        found = {}
        for kind, buckets in _counts.items():
            _prune(buckets, minute)
            found[kind] = sum(buckets.values())
    return found


def reset() -> None:
    """Alleen voor tests."""
    with _lock:
        for buckets in _counts.values():
            buckets.clear()
