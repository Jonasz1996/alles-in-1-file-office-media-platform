"""own_live: de enige vorm voor "eigen, levende bestanden" (bouwontwerp fase 6, Keuze 25a). Z0,
frozen.

Elke fotofunctie (de kaart, plaatsen, duplicaten, personen, herinneringen, OCR van foto's)
gebruikt own_live of OWN_LIVE_SQL: alleen bestanden van mij, niet in de prullenbak (ook niet via
een map erboven: dead) en geen map of album. Foto-gegevens over een plek, een persoon of tekst
in een foto zijn van de eigenaar van de node; wat met je gedeeld is, telt hier nooit mee
(tests/search/test_own_live.py).

De kaart en de plaatsen lezen photo_geo zonder join op node: own_live_geo. Een rij van photo_geo
hoort bij een bestand van de eigenaar van de node, en dead en taken_at zijn altijd die van de
node (de triggers geo_* en DEAD_SUBTREE van 0010). Dan is own_live_geo precies own_live (review
Z0, haalbaar F1 en F2; tests/search/test_triggers_geo.py).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, and_

OWN_LIVE_SQL = "{n}.owner_id = :me AND {n}.dead = 0 AND {n}.kind = 'file'"
OWN_LIVE_GEO_SQL = "{g}.owner_id = :me AND {g}.dead = 0"


def own_live(n: Any, me: uuid.UUID) -> ColumnElement[bool]:
    """n: Node, een aliased(Node) of een alias van de tabel node (met .c)."""
    cols = getattr(n, "c", n)
    return and_(cols.owner_id == me, cols.dead == 0, cols.kind == "file")


def own_live_geo(g: Any, me: uuid.UUID) -> ColumnElement[bool]:
    """g: PhotoGeo, een aliased(PhotoGeo) of een alias van de tabel photo_geo (met .c)."""
    cols = getattr(g, "c", g)
    return and_(cols.owner_id == me, cols.dead == 0)
