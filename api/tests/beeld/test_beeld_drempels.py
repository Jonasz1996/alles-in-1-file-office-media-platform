"""De drempels van zoeken op beeld: MIN_TEKST en MIN_FOTO (fase 6, X3; BZ4, bouwontwerp §3.9
"Een drempel": "BZ4 stelt die grenzen vast op de set van Keuze 83, en een test houdt ze vast").

BZ4 gaf de verdeling van de scores op de 120 foto's van beeld.tsv, en geen enkele grens scheidt
daar goed van niet goed:

- MIN_TEKST (de kans sigmoid(scale · cos + bias) van een vraag in woorden tegen een foto): van de
  goede treffers is p5 0,0 en van de andere p95 0,0002 (x3a.md, "CI op 3a9b07d") en 0,0 (VERSIE 3,
  x3a.md "CI op 669f03c"). Een grens boven 0 gooit dus meer dan 5 % van de goede treffers weg;
- MIN_FOTO ("Meer zoals deze", een foto tegen een foto): de cosinus in dezelfde categorie heeft
  p5 0,557 en tussen categorieën p95 0,7832 (0,4757 en 0,6394 met VERSIE 3). De twee verdelingen
  overlappen, dus ook de kans die _keep ervan maakt (sigmoid is stijgend).

Dus blijven beide 0: elke treffer telt, en "onder de grens toont de app niets" doet niets (een
Afwijking van het ontwerp, in x3b.md). Een andere grens vraagt eerst een nieuwe BZ4 die er een
laat zien; dan gaat deze test mee.
"""

from __future__ import annotations

import math

import pytest

from app.analyse import monitor, wire
from app.beeld import search

# De rapporten van BZ4 (test_beeld_kwaliteit.py, de regels "BZ4 MIN_TEKST" en "BZ4 MIN_FOTO")
BZ4_TEKST_GOED_P5 = 0.0  # de kans; beide runs
BZ4_FOTO_ZELFDE_P5 = (0.557, 0.4757)  # cos; 3a9b07d en VERSIE 3
BZ4_FOTO_ANDERS_P95 = (0.7832, 0.6394)


def _met_beeld(scale: float = 112.0, bias: float = -16.5) -> None:
    """De monitor met een beeldmodel in caps (scale en bias als voorbeeld, niet die van het
    echte model)."""
    beeld = wire.BeeldCaps(model="siglip2-b32-256", dim=768, scale=scale, bias=bias)
    monitor._set_quiet(monitor.AnalyseStatus("ok", caps=wire.CapsData(beeld=beeld)))


@pytest.fixture(autouse=True)
def _monitor_terug():
    yield
    monitor._set_quiet(monitor.OFF)


def test_de_drempels_zijn_die_van_bz4():
    assert search.MIN_TEKST == 0.0
    assert search.MIN_FOTO == 0.0
    # Geen grens boven p5 van de goede treffers (dan valt > 5 % van de goede weg)
    assert search.MIN_TEKST <= BZ4_TEKST_GOED_P5
    # De cosinus overlapt: geen grens scheidt zelfde categorie van anders
    assert all(z < a for z, a in zip(BZ4_FOTO_ZELFDE_P5, BZ4_FOTO_ANDERS_P95, strict=True))


def test_met_0_telt_elke_treffer_ook_met_een_kans_van_bijna_0():
    """De laagste cosinus (−1, score −10.000) geeft een kans van e^−60: met 0 blijft hij staan,
    voor een vraag in woorden en voor "Meer zoals deze"."""
    _met_beeld()
    hits = [(1, 9_000, "a"), (2, 0, "b"), (3, -10_000, "c")]
    assert search._keep(list(hits), "rood") == hits
    assert search._keep(list(hits), None) == hits


def test_een_grens_boven_0_zou_treffers_weghalen(monkeypatch):
    """Dat de grens iets doet (anders zegt de test hierboven niets): met 0,5 valt alles onder
    cos = −bias / scale weg, en met MIN_FOTO 0,5 ook bij "Meer zoals deze"."""
    _met_beeld()
    hits = [(1, 9_000, "a"), (2, 0, "b"), (3, -10_000, "c")]
    grens = 16.5 / 112.0  # de cosinus waar de kans 0,5 is
    assert 0.9 > grens > 0.0
    assert 1 / (1 + math.exp(-(112.0 * 0.9 - 16.5))) > 0.5
    monkeypatch.setattr(search, "MIN_TEKST", 0.5)
    assert search._keep(list(hits), "rood") == hits[:1]
    assert search._keep(list(hits), None) == hits
    monkeypatch.setattr(search, "MIN_FOTO", 0.5)
    assert search._keep(list(hits), None) == hits[:1]
