"""De cache van 60 s van een beeldvraag (bouwontwerp fase 6, X3; Keuze 78; X3b).

De gerangschikte lijst van rank ([num, s], beste eerst) blijft TTL_S seconden in het geheugen van
de app, zodat "Meer laden" en een ander filter de container niet opnieuw vragen. De sleutel is
(gebruiker, soort, vraag of num, gen): een nieuw bestand (een hogere gen) is een nieuwe sleutel.
Hooguit MAX lijsten; de oudste gaat eruit. Nooit op schijf, en de vraag staat alleen in de sleutel
(nooit in een log, K1-16).
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict

TTL_S = 60.0
MAX = 200

Key = tuple[uuid.UUID, str, str, int]
_items: OrderedDict[Key, tuple[float, list[tuple[int, int]]]] = OrderedDict()


def key(user_id: uuid.UUID, text: str | None, num: int | None, gen: int) -> Key:
    return (user_id, "text", text, gen) if text is not None else (user_id, "like", str(num), gen)


def get(k: Key) -> list[tuple[int, int]] | None:
    found = _items.get(k)
    if found is None:
        return None
    if time.monotonic() - found[0] > TTL_S:
        _items.pop(k, None)
        return None
    return found[1]


def put(k: Key, hits: list[tuple[int, int]]) -> None:
    _items[k] = (time.monotonic(), hits)
    _items.move_to_end(k)
    while len(_items) > MAX:
        _items.popitem(last=False)


def forget(user_id: uuid.UUID | None = None) -> None:
    """Alles van deze gebruiker weg (uit, wissen); zonder gebruiker alles."""
    for k in [k for k in _items if user_id is None or k[0] == user_id]:
        _items.pop(k, None)
