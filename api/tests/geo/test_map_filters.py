"""De kaart met een filter van Keuze 43 (Keuze 4a; review golf 3, contract F1). Datum, plaats en
land gaan op photo_geo zelf (taken_at, place_id, country), zonder join op node: dat hield de kaart
met een filter op 300–450 ms bij 55.000 foto's. De uitkomst blijft die van het register op node:
dezelfde foto's als /photos/map/items en de tijdlijn /photos, ook op de grens van een dag (een
zwevende tijd, app/photodate.py). Soort, camera en map doen wel de join op node (eigen poort in
tests/photos/test_map_perf.py)."""

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event

from app.db import get_engine
from app.geo import tiles
from app.models import Node, PhotoGeo, PhotoPref, User
from app.routers import photos_map
from app.search import filters_photo
from app.search.types import SearchParams
from tests.factories import maker
from tests.geo.hulp import zonder_gps
from tests.search.plans import assert_plan
from tests.test_previews import upload

pytestmark = pytest.mark.anyio

WERELD = "-180,-85,180,85"
UTRECHT = (52.0907, 5.1214, 2745912, "NL")
PARIJS = (48.8566, 2.3522, 2988507, "FR")
# (naam, opnametijd als zwevende tijd, plek): de grenzen van 1 juli 2024 in de klok van de camera
FOTOS = (
    ("laat.jpg", datetime(2024, 7, 1, 23, 59, 59), UTRECHT),
    ("middernacht.jpg", datetime(2024, 7, 2, 0, 0, 0), UTRECHT),
    ("vroeg.jpg", datetime(2024, 7, 1, 0, 0, 0), PARIJS),
    ("ervoor.jpg", datetime(2024, 6, 30, 23, 59, 59), PARIJS),
    ("later.jpg", datetime(2025, 1, 1, 8, 0, 0), UTRECHT),
)


@pytest.fixture(autouse=True)
def _filters():
    """Een test eerder in de run kan filters.reset() gedaan hebben (test_contracts.py)."""
    filters_photo.register()


async def zet_fotos(client, admin, settings) -> dict[str, str]:
    """Elke foto van FOTOS als node met zijn opnametijd en een rij in photo_geo (de trigger zet
    taken_at van photo_geo); één video erbij, zonder plek."""
    headers, tokens = admin
    foto = await upload(client, headers, "bron.jpg", zonder_gps())
    sha = foto["etag"].strip('"')
    owner = uuid.UUID(tokens["user"]["id"])
    r = await client.get("/api/v1/files/root", headers=headers)
    root = uuid.UUID(r.json()["folder"]["id"])
    ids: dict[str, str] = {}
    async with maker(settings)() as db:
        nodes = []
        for naam, genomen, _plek in FOTOS:
            nodes.append(Node(
                id=uuid.uuid4(), parent_id=root, owner_id=owner, name=naam, kind="file",
                mime="image/jpeg", size=1, blob_id=sha, search_name=naam, taken_at=genomen,
                taken_day=int(genomen.strftime("%Y%m%d")),
            ))  # fmt: skip
        db.add_all(nodes)
        await db.flush()
        for node, (naam, _genomen, (lat, lon, plaats, land)) in zip(nodes, FOTOS, strict=True):
            x, y = tiles.tile16(lat, lon)
            db.add(PhotoGeo(node_id=node.id, owner_id=owner, lat=lat, lon=lon, x16=x, y16=y,
                            place_id=plaats, country=land, source="exif"))  # fmt: skip
            ids[naam] = str(node.id)
        await db.commit()
    return ids


async def _get(client, headers, pad: str, **params) -> dict:
    r = await client.get(f"/api/v1{pad}", params=params, headers=headers)
    assert r.status_code == 200, (pad, params, r.text)
    return r.json()


VRAGEN = (
    ({"from": "2024-07-01", "to": "2024-07-01"}, {"laat.jpg", "vroeg.jpg"}),
    ({"from": "2024-07-02"}, {"middernacht.jpg", "later.jpg"}),
    ({"to": "2024-06-30"}, {"ervoor.jpg"}),
    ({"from": "2024-07-01", "to": "2024-07-02"}, {"laat.jpg", "vroeg.jpg", "middernacht.jpg"}),
    ({"place": UTRECHT[2]}, {"laat.jpg", "middernacht.jpg", "later.jpg"}),
    ({"country": " fr "}, {"vroeg.jpg", "ervoor.jpg"}),
    ({"country": "F1"}, set()),
    ({"country": "NL", "to": "2024-12-31"}, {"laat.jpg", "middernacht.jpg"}),
    ({"place": PARIJS[2], "from": "2024-07-01", "type": "image"}, {"vroeg.jpg"}),
    ({"place": PARIJS[2], "type": "video"}, set()),
)


@pytest.mark.parametrize(("filters", "verwacht"), VRAGEN)
async def test_dezelfde_fotos_als_de_tijdlijn(client, admin, settings, filters, verwacht):
    """De cellen tellen precies de foto's die /photos/map/items en /photos geven."""
    headers, _tokens = admin
    ids = await zet_fotos(client, admin, settings)
    namen = {v: k for k, v in ids.items()}
    cellen = (await _get(client, headers, "/photos/map", bbox=WERELD, z=14, **filters))["cells"]
    assert sum(c["n"] for c in cellen) == len(verwacht), (filters, cellen)
    assert {namen[c["cover"]] for c in cellen} <= verwacht
    items = (await _get(client, headers, "/photos/map/items", bbox=WERELD, **filters))["items"]
    assert {namen[i["id"]] for i in items} == verwacht, filters
    tijdlijn = await _get(client, headers, "/photos", **filters)
    assert {namen[i["id"]] for i in tijdlijn["items"] if i["id"] in namen} == verwacht, filters


async def test_plaats_en_land_niet_als_locatie_uit_staat(client, admin, settings):
    """De kaart is leeg bij "uit" (de route), en ook de voorwaarde zelf houdt het tegen: zoals
    _with_place (photo_rows.not_off) voor de job geo.off de rijen wist."""
    headers, tokens = admin
    await zet_fotos(client, admin, settings)
    owner = uuid.UUID(tokens["user"]["id"])
    user = User(id=owner, role="admin")
    xs, y0, y1 = tiles.ranges(*tiles.parse_bbox(WERELD))
    async with maker(settings)() as db:
        p = SearchParams(place=UTRECHT[2])
        aan = await photos_map._cells(db, user, tiles.shift(14), xs, y0, y1, p)
        assert sum(c[0] for c in aan.values()) == 3
        db.add(PhotoPref(user_id=owner, location="uit"))
        await db.flush()
        uit = await photos_map._cells(db, user, tiles.shift(14), xs, y0, y1, p)
        assert uit == {}
        await db.rollback()


# filters -> of de vraag van de cellen node nodig heeft
PLANNEN = {
    "datum": ({"date_from": date(2024, 7, 1), "date_to": date(2024, 7, 31)}, False),
    "plaats": ({"place": UTRECHT[2]}, False),
    "land": ({"country": "nl"}, False),
    "plaats+land+datum": (
        {"place": UTRECHT[2], "country": "NL", "date_from": date(2024, 1, 1)},
        False,
    ),
    "soort": ({"types": ["image"]}, True),
    "camera": ({"camera": "canon"}, True),
    "land+soort": ({"country": "NL", "types": ["video"]}, True),
}


@pytest.mark.parametrize("naam", list(PLANNEN))
async def test_alleen_node_als_het_filter_hem_nodig_heeft(client, admin, settings, naam):
    """Het plan van de vraag van de cellen: datum, plaats en land langs photo_geo alleen; soort
    en camera langs photo_geo en dan per foto de node op zijn sleutel (nooit een SCAN)."""
    filters, met_node = PLANNEN[naam]
    headers, tokens = admin
    await zet_fotos(client, admin, settings)
    user = User(id=uuid.UUID(tokens["user"]["id"]), role="admin")
    xs, y0, y1 = tiles.ranges(*tiles.parse_bbox(WERELD))
    engine = get_engine(settings)
    gezien: list[tuple[str, tuple]] = []

    def bij(conn, cursor, statement, parameters, context, executemany):
        gezien.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", bij)
    try:
        async with maker(settings)() as db:
            await photos_map._cells(db, user, tiles.shift(2), xs, y0, y1, SearchParams(**filters))
            sql, args = next(g for g in reversed(gezien) if "GROUP BY" in g[0])
            plan = await db.run_sync(
                lambda s: assert_plan(s.connection(), sql, args, require_first="photo_geo")
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", bij)
    regels = [t for _, _, t in plan.regels]
    assert not any(t.startswith("SCAN") for t in regels), plan.tekst()
    op_node = [t for t in regels if " node " in f"{t} "]
    if met_node:
        assert op_node and all("(id=?)" in t for t in op_node), plan.tekst()
    else:
        assert op_node == [], plan.tekst()


def test_de_dag_is_de_zwevende_tijd():
    """from en to als begin van een dag in de klok van de camera, zoals taken_day."""
    begin = photos_map._day(date(2024, 7, 1))
    assert (begin.year, begin.month, begin.day, begin.hour, begin.minute) == (2024, 7, 1, 0, 0)
    assert begin.utcoffset() == timedelta(0)
