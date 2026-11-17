"""Plaatsen en landen met de echte bronnen (bouwontwerp fase 6, Keuze 29; marker geo).

De bronnen komen uit de map in PLATFORM_GEO_TEST_SRC (standaard ~/.cache/platform-geo); de job
api haalt ze daar eerst met tools/geo/haal.py, met sha256. Zonder de bronnen faalt de test met de
reden: hij slaat nooit stil over. Niet in de gewone run (addopts: not geo); draai hem met
`uv run pytest -q -m geo tests/geo/test_places.py`.

- De punten van tests/search/fixtures.py (Z0): de grensparen aan de goede kant van de grens, de
  punten op de Noordzee op zee, en PLAATS_ID van de wereld is de stad Utrecht.
- De plaats tegen brute-force haversine op 1.000 punten: over alle plaatsen van hetzelfde land,
  zonder de R*Tree.
- 180°: punten aan beide kanten, ook tegen brute force.
"""

import math
import os
import random
import time
from collections import defaultdict
from pathlib import Path

import pytest

from app.geo import load, places
from tests.search import fixtures as F
from tests.search import wereld as W

pytestmark = pytest.mark.geo

BRON = Path(os.environ.get("PLATFORM_GEO_TEST_SRC") or Path.home() / ".cache" / "platform-geo")


@pytest.fixture(scope="module")
def echt(tmp_path_factory) -> places.Places:
    """places.sqlite uit de echte bronnen, één keer voor dit bestand."""
    for bron in (load.LANDEN, load.PLAATSEN):
        pad = BRON / bron.naam
        assert pad.is_file(), (
            f"{pad} ontbreekt: haal de bronnen met "
            f"python3 -I tools/geo/haal.py --naar {BRON} (of zet PLATFORM_GEO_TEST_SRC)"
        )
    uit = tmp_path_factory.mktemp("geo") / "places.sqlite"
    t = time.monotonic()
    n = load.build(BRON, uit)
    print(f"\ngeo-load: {time.monotonic() - t:.1f} s, {uit.stat().st_size} bytes, {n}")
    p = places.Places(uit)
    yield p
    p.close()


def test_de_aantallen_liggen_vast(echt):
    """Vaste bronnen (sha256), dus vaste aantallen."""
    assert echt.meta["plaatsen"] == "144521"
    assert echt.meta["landen"] == "258"
    assert echt.meta["vlakken"] == "4274"


def test_plaats_id_van_de_wereld_is_utrecht(echt):
    utrecht = echt.lookup(*F.UTRECHT)
    assert (utrecht.id, utrecht.name, utrecht.admin1, utrecht.cc) == (
        W.PLAATS_ID, "Utrecht", "Utrecht", "NL"
    )  # fmt: skip
    assert utrecht.km < 1
    assert echt.countries(["NL"]) == {"NL": "Nederland"}


@pytest.mark.parametrize("paar", F.GRENSPAREN, ids=[a[0] for a, _b in F.GRENSPAREN])
def test_de_grensparen(echt, paar):
    for naam, lat, lon, cc in paar:
        plek = echt.lookup(lat, lon)
        assert plek.cc == cc, (naam, plek)
        # De plaats ligt in hetzelfde land (de gebieden overzee tellen bij hun land)
        if plek.id is not None:
            rij = echt.names([plek.id])[plek.id]
            assert places.GEBIED.get(rij.cc, rij.cc) == cc, (naam, rij)


@pytest.mark.parametrize("punt", F.ZEE, ids=[z[0] for z in F.ZEE])
def test_op_de_noordzee_geen_land_en_geen_plaats(echt, punt):
    _naam, lat, lon = punt
    assert echt.lookup(lat, lon) == places.NIETS


def _alle_plaatsen(echt) -> dict[str, list[tuple[int, float, float]]]:
    per_land: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for pid, cc, lat, lon in echt._db.execute("SELECT id, cc, lat, lon FROM place"):
        per_land[places.GEBIED.get(cc, cc)].append((pid, lat, lon))
    return per_land


def _brute(per_land, lat, lon, cc) -> tuple[int, float] | None:
    beste = None
    for pid, plat, plon in per_land.get(cc, ()):
        d = places.haversine_km(lat, lon, plat, plon)
        if d <= places.KM and (beste is None or (d, pid) < beste):
            beste = (d, pid)
    return None if beste is None else (beste[1], beste[0])


def _vergelijk(echt, per_land, punten) -> tuple[int, int]:
    """Elk punt: de plaats van lookup() is die van brute force (zelfde land). Geeft (op land,
    met plaats)."""
    op_land = met_plaats = 0
    for lat, lon in punten:
        plek = echt.lookup(lat, lon)
        if plek.cc is None:
            assert plek == places.NIETS
            continue
        op_land += 1
        verwacht = _brute(per_land, lat, lon, plek.cc)
        if verwacht is None:
            assert (plek.id, plek.name, plek.km) == (None, None, None), (lat, lon, plek)
            continue
        met_plaats += 1
        assert plek.id == verwacht[0], (lat, lon, plek, verwacht)
        assert math.isclose(plek.km, verwacht[1], abs_tol=1e-9)
    return op_land, met_plaats


def test_de_plaats_tegen_brute_force_op_1000_punten(echt):
    """750 punten tot ±25 km van een willekeurige plaats (de grens van 30 km doet ertoe) en 250
    willekeurig over de wereld; vaste zaad."""
    per_land = _alle_plaatsen(echt)
    rnd = random.Random(29)
    alle = [p for lijst in per_land.values() for p in lijst]
    punten = []
    for _ in range(750):
        _pid, lat, lon = rnd.choice(alle)
        punten.append((max(-89.9, min(89.9, lat + rnd.uniform(-0.22, 0.22))),
                       ((lon + rnd.uniform(-0.3, 0.3) + 180) % 360) - 180))  # fmt: skip
    punten += [(rnd.uniform(-60, 75), rnd.uniform(-180, 180)) for _ in range(250)]
    t = time.monotonic()
    for lat, lon in punten:
        echt.lookup(lat, lon)
    ms = (time.monotonic() - t) * 1000 / len(punten)
    op_land, met_plaats = _vergelijk(echt, per_land, punten)
    print(f"\n1000 punten: {op_land} op land, {met_plaats} met plaats, {ms:.2f} ms per lookup")
    assert op_land > 600 and met_plaats > 400


def test_180_graden(echt):
    """Aan beide kanten van 180°: Fiji (Taveuni ligt erop), Tsjoekotka, Tuvalu en Wallis."""
    per_land = _alle_plaatsen(echt)
    assert echt.lookup(-16.85, 179.98).cc == "FJ"
    assert echt.lookup(-16.85, -179.98).cc == "FJ"
    assert echt.lookup(65.5, 179.9).cc == "RU"
    assert echt.lookup(65.5, -179.9).cc == "RU"
    lambasa = echt.lookup(-16.42, 179.4)
    assert (lambasa.name, lambasa.cc) == ("Lambasa", "FJ")
    rnd = random.Random(180)
    punten = [(lat + rnd.uniform(-0.5, 0.5), lon + rnd.uniform(-0.6, 0.6))
              for lat, lon in ((-16.6, 179.7), (-16.6, -179.9), (65.0, 179.6), (66.3, -179.4),
                               (-14.3, -178.1), (-8.5, 179.2))
              for _ in range(30)]  # fmt: skip
    punten = [(la, ((lo + 180) % 360) - 180) for la, lo in punten]
    op_land, _met = _vergelijk(echt, per_land, punten)
    assert op_land > 30
