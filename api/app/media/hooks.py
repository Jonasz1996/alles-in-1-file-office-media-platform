"""De haken van media in de rest van de app (bouwontwerp fase 5, §3.3). Frozen.

Dit bestand importeert geen pakketten: de scanner (M2) en de anderen melden zich aan met
register_touched en register_discard. Zo blijft de app zonder media precies 0.6.0: zonder
aanmelding doen de haken niets (behalve de sessies sluiten, en die zijn er dan niet).

- touched: vanuit files.notify en notify_many, na de commit. Geen I/O, geen database: alleen
  in het geheugen (de scanner zet zelf een taak klaar). Bij "node.deleted" (prullenbak of
  definitief weg) en "node.version" (een nieuwe versie) gaan de sessies van die nodes dicht
  (Keuze 5, 21). Hernoemen en verplaatsen sluiten niets: de guard kijkt elke keer of de node
  nog naar de gepinde sha wijst, en of de toegang er nog is.
- on_discard: vanuit previews.discard, vóór de blob weggaat. Eerst de sessies op die sha
  dicht, dan de aangemelde opruimers (de cache van M4 en M5).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable

log = logging.getLogger("platform.media")

CLOSING_TYPES = frozenset({"node.deleted", "node.version"})

Touched = Callable[[list[uuid.UUID], str], None]
Discard = Callable[[str], Awaitable[None]]

_touched: list[Touched] = []
_discard: list[Discard] = []


def register_touched(fn: Touched) -> None:
    """Een luisteraar voor touched (de scanner, M2). Twee keer dezelfde telt één keer."""
    if fn not in _touched:
        _touched.append(fn)


def register_discard(fn: Discard) -> None:
    """Een opruimer bij het weggooien van een blob (de cache, M4 en M5)."""
    if fn not in _discard:
        _discard.append(fn)


def reset() -> None:
    """Alleen voor tests: alle aanmeldingen weg."""
    _touched.clear()
    _discard.clear()


def touched(node_ids: Iterable[uuid.UUID], type: str) -> None:
    """Nodes die veranderden. Faalt nooit: een fout van een luisteraar komt in het log."""
    ids = list(node_ids)
    if not ids:
        return
    if type in CLOSING_TYPES:
        from app.media import sessions

        for node_id in ids:
            try:
                sessions.close_node(node_id)
            except Exception:
                log.exception("sessies sluiten voor %s", node_id)
    for fn in list(_touched):
        try:
            fn(ids, type)
        except Exception:
            log.exception("media.hooks.touched")


async def on_discard(sha: str) -> None:
    """Een blob gaat weg. Faalt nooit."""
    from app.media import sessions

    try:
        sessions.close_sha(sha)
    except Exception:
        log.exception("sessies sluiten voor een blob")
    for fn in list(_discard):
        try:
            await fn(sha)
        except Exception:
            log.exception("media.hooks.on_discard")
