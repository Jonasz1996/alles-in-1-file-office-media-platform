"""De upgrade naar 0.8.0 met de echte bronnen (I1, M9; marker geo).

tests/geo/test_places_later.py laat met de kleine bronnen zien dat foto's die photometa las vóór
places.sqlite er was, na de bouw hun plaats krijgen (de job geo.places, A4). Hier hetzelfde met
Natural Earth en GeoNames: een paar honderd foto's met GPS (bij echte plaatsen, rond de grens van
30 km, willekeurig over de wereld, de grensparen en de Noordzee van Z0, en 180°), gelezen zonder
places.sqlite; dan de bouw zoals bij de start, en de keten in stukken. Daarna heeft elke rij
precies wat places.lookup() op zijn eigen lat en lon zegt, opgezocht in een places.sqlite die
apart uit dezelfde bronnen gebouwd is: plaats, land en provincie, of NULL op zee.

De bronnen komen uit PLATFORM_GEO_TEST_SRC (standaard ~/.cache/platform-geo), zoals
test_places.py; zonder de bronnen faalt de test met de reden. Draai hem met
`uv run pytest -q -m geo tests/geo/test_places_later_echt.py`.
"""

import io
import os
import random
import shutil
import sqlite3
import time
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app import jobs, photo_rows
from app.geo import load, places
from app.models import PhotoGeo
from tests.factories import maker
from tests.geo.hulp import verwerk
from tests.geo.test_places_later import gestart, jobs_van
from tests.search import fixtures as F
from tests.test_previews import upload

pytestmark = [pytest.mark.geo, pytest.mark.anyio]

BRON = Path(os.environ.get("PLATFORM_GEO_TEST_SRC") or Path.home() / ".cache" / "platform-geo")
BIJ_PLAATSEN = 180  # tot ±25 km van een echte plaats: de grens van 30 km doet ertoe
WERELD = 50  # willekeurig over de wereld, ook op zee
OVER_180 = ((-16.85, 179.98), (-16.85, -179.98), (65.5, 179.9), (65.5, -179.9))
STUK = 37  # PLACES_CHUNK in deze test: de keten loopt over meer dan zes stukken


def _bronnen_er() -> None:
    for bron in (load.LANDEN, load.PLAATSEN):
        pad = BRON / bron.naam
        assert pad.is_file(), (
            f"{pad} ontbreekt: haal de bronnen met "
            f"python3 -I tools/geo/haal.py --naar {BRON} (of zet PLATFORM_GEO_TEST_SRC)"
        )


@pytest.fixture(scope="module")
def referentie(tmp_path_factory) -> places.Places:
    """Een eigen places.sqlite uit de echte bronnen, los van die van de app."""
    _bronnen_er()
    uit = tmp_path_factory.mktemp("geo-ref") / "places.sqlite"
    load.build(BRON, uit)
    p = places.Places(uit)
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _vergeten():
    places.forget()
    yield
    places.forget()


def _punten(ref: places.Places) -> list[tuple[float, float]]:
    """De plekken, met een vast zaad."""
    db = sqlite3.connect(f"file:{ref.pad}?mode=ro", uri=True)
    try:
        alle = db.execute("SELECT lat, lon FROM place ORDER BY id").fetchall()
    finally:
        db.close()
    rnd = random.Random(2026_11_11)
    uit = []
    for _ in range(BIJ_PLAATSEN):
        lat, lon = rnd.choice(alle)
        uit.append((max(-89.9, min(89.9, lat + rnd.uniform(-0.22, 0.22))),
                    ((lon + rnd.uniform(-0.3, 0.3) + 180) % 360) - 180))  # fmt: skip
    uit += [(rnd.uniform(-60, 75), rnd.uniform(-180, 180)) for _ in range(WERELD)]
    uit += [(lat, lon) for paar in F.GRENSPAREN for (_n, lat, lon, _cc) in paar]
    uit += [(lat, lon) for (_n, lat, lon) in F.ZEE]
    uit += [F.UTRECHT, *OVER_180]
    return [(round(lat, 6), round(lon, 6)) for lat, lon in uit]


def _in_geo_src(settings) -> None:
    """De echte bronnen in geo_src, zoals install.sh ze neerzet (Z7)."""
    naar = Path(settings.geo_src)
    naar.mkdir(parents=True, exist_ok=True)
    for bron in (load.LANDEN, load.PLAATSEN):
        shutil.copyfile(BRON / bron.naam, naar / bron.naam)


def _jpeg(i: int, lat: float, lon: float) -> bytes:
    """Een JPEG van 16 × 16 met EXIF en GPS; elk punt een eigen inhoud."""
    img = Image.new("RGB", (16, 16), (i * 37 % 256, i * 91 % 256, i * 151 % 256))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80, exif=F.exif(lat, lon).tobytes())
    return buf.getvalue()


async def _rijen(settings) -> dict[str, tuple[float, float, int | None, str | None, str | None]]:
    async with maker(settings)() as db:
        rows = await db.execute(
            select(PhotoGeo.node_id, PhotoGeo.lat, PhotoGeo.lon, PhotoGeo.place_id,
                   PhotoGeo.country, PhotoGeo.admin1)
        )  # fmt: skip
        return {str(n): (la, lo, p, c, a) for n, la, lo, p, c, a in rows}


def _verwacht(ref: places.Places, lat: float, lon: float):
    """Wat photometa schrijft als places.sqlite er bij het lezen al was (photo_rows.write)."""
    plek = ref.lookup(lat, lon)
    if plek.cc is None:
        return (None, None, None)
    return (plek.id, plek.cc, plek.admin1)


async def test_upgrade_met_de_echte_bronnen(
    client, admin, settings, monkeypatch, referentie, capsys
):
    h, _tokens = admin
    punten = _punten(referentie)
    assert places.get(settings) is None  # de bouw loopt nog: photometa leest zonder plaatsen
    t = time.monotonic()
    node_plek = {}
    for i, (lat, lon) in enumerate(punten):
        node = (await upload(client, h, f"plek-{i:04d}.jpg", _jpeg(i, lat, lon)))["id"]
        node_plek[node] = (lat, lon)
    await verwerk(settings)
    gelezen = await _rijen(settings)
    assert set(gelezen) == set(node_plek), [node_plek[n] for n in set(node_plek) - set(gelezen)]
    for node, (lat, lon, *plaats) in gelezen.items():
        verwacht_lat, verwacht_lon = node_plek[node]
        assert abs(lat - verwacht_lat) < 1e-5 and abs(lon - verwacht_lon) < 1e-5, node
        assert plaats == [None, None, None], node
    gelezen_s = time.monotonic() - t

    # De bouw zoals bij de start: de echte bronnen in geo_src, load.start, dan de keten
    _in_geo_src(settings)
    monkeypatch.setattr(photo_rows, "PLACES_CHUNK", STUK)
    t = time.monotonic()
    await gestart(settings)
    gebouwd_s = time.monotonic() - t
    app_places = places.get(settings)
    assert app_places is not None
    for sleutel in ("plaatsen", "landen", "vlakken"):
        assert app_places.meta[sleutel] == referentie.meta[sleutel], sleutel
    t = time.monotonic()
    await jobs.run_pending(settings)
    keten_s = time.monotonic() - t
    stukken = await jobs_van(settings, photo_rows.PLACES_TYPE)
    assert stukken and set(stukken) == {"done"}
    assert len(stukken) == len(punten) // STUK + 1

    na = await _rijen(settings)
    assert set(na) == set(node_plek)
    fout = {
        node: (rij, _verwacht(referentie, rij[0], rij[1]))
        for node, rij in na.items()
        if tuple(rij[2:]) != _verwacht(referentie, rij[0], rij[1])
    }
    assert not fout, fout
    op_land = sum(1 for r in na.values() if r[3] is not None)
    met_plaats = sum(1 for r in na.values() if r[2] is not None)
    op_zee = len(na) - op_land
    for _n, lat, lon in F.ZEE:
        rij = next(r for n, r in na.items() if node_plek[n] == (round(lat, 6), round(lon, 6)))
        assert rij[2:] == (None, None, None), (lat, lon)
    for paar in F.GRENSPAREN:
        for _naam, lat, lon, cc in paar:
            rij = next(r for n, r in na.items() if node_plek[n] == (round(lat, 6), round(lon, 6)))
            assert rij[3] == cc, (lat, lon, rij)
    for lat, lon in OVER_180:
        rij = next(r for n, r in na.items() if node_plek[n] == (lat, lon))
        assert rij[3] in {"FJ", "RU"}, (lat, lon, rij)
    assert op_land > BIJ_PLAATSEN and met_plaats > BIJ_PLAATSEN * 0.8, (op_land, met_plaats)
    # Een tweede start zonder nieuwe bouw zet niets meer klaar
    await gestart(settings)
    assert len(await jobs_van(settings, photo_rows.PLACES_TYPE)) == len(stukken)
    with capsys.disabled():
        print(
            f"\nM9: {len(punten)} foto's ({op_land} op land, {met_plaats} met plaats, "
            f"{op_zee} op zee of zonder land); lezen {gelezen_s:.1f} s, bouw {gebouwd_s:.1f} s, "
            f"keten {keten_s:.2f} s in {len(stukken)} stukken van {STUK}"
        )
