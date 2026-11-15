"""De kaart: cellen, grotere cellen boven 500, 180°, de items per pagina (bouwontwerp fase 6,
Keuzes 4a en 32; §3.4). De foto's staan rechtstreeks in de database (nodes met de inhoud van één
upload, en hun rij in photo_geo), zodat een test duizenden punten op vaste plekken kan zetten; de
triggers van 0010 zetten dead en taken_at, zoals bij photometa."""

import math
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.geo import tiles
from app.models import Node, PhotoGeo
from app.routers import photos_map
from app.search import bench
from tests.factories import code_of, maker, params_of
from tests.geo.hulp import zonder_gps
from tests.test_previews import upload

pytestmark = pytest.mark.anyio

DAG = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
UTRECHT = (52.0907, 5.1214)
PARIJS = (48.8566, 2.3522)
EUROPA = "-10.0,35.0,30.0,60.0"


async def zet_punten(client, admin, settings, punten) -> list[str]:
    """Een foto per punt (lat, lon), van nieuw naar oud: de eerste is de nieuwste."""
    headers, tokens = admin
    foto = await upload(client, headers, f"bron{len(punten)}.jpg", zonder_gps())
    sha = foto["etag"].strip('"')
    owner = uuid.UUID(tokens["user"]["id"])
    r = await client.get("/api/v1/files/root", headers=headers)
    root = uuid.UUID(r.json()["folder"]["id"])
    ids = []
    async with maker(settings)() as db:
        for i in range(len(punten)):
            genomen = DAG - timedelta(minutes=i)
            node = Node(
                id=uuid.uuid4(), parent_id=root, owner_id=owner, name=f"p{i}.jpg", kind="file",
                mime="image/jpeg", size=1, blob_id=sha, search_name=f"p{i}.jpg",
                taken_at=genomen, taken_day=int(genomen.strftime("%Y%m%d")),
            )  # fmt: skip
            db.add(node)
            ids.append(node.id)
        await db.flush()
        for node_id, (lat, lon) in zip(ids, punten, strict=True):
            x, y = tiles.tile16(lat, lon)
            db.add(PhotoGeo(node_id=node_id, owner_id=owner, lat=lat, lon=lon, x16=x, y16=y,
                            source="exif"))  # fmt: skip
        await db.commit()
    return [str(i) for i in ids]


async def kaart(client, headers, bbox: str, z: int) -> list[dict]:
    r = await client.get("/api/v1/photos/map", params={"bbox": bbox, "z": z}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["cells"]


async def test_cellen_tellen_en_de_omslag_is_de_nieuwste(client, admin, settings):
    h, _tokens = admin
    rng = random.Random(1)
    utrecht = [(UTRECHT[0] + rng.uniform(-0.01, 0.01), UTRECHT[1] + rng.uniform(-0.01, 0.01))
               for _ in range(3)]  # fmt: skip
    ids = await zet_punten(client, admin, settings, [*utrecht, PARIJS])
    cellen = await kaart(client, h, EUROPA, 8)
    s = tiles.shift(8)
    verwacht = {}
    for node_id, (lat, lon) in zip(ids, [*utrecht, PARIJS], strict=True):
        x, y = tiles.tile16(lat, lon)
        verwacht.setdefault((x >> s, y >> s), []).append(node_id)
    assert {(c["x"], c["y"]): c["n"] for c in cellen} == {k: len(v) for k, v in verwacht.items()}
    for c in cellen:
        groep = verwacht[(c["x"], c["y"])]
        assert c["cover"] == groep[0]  # de eerste van zet_punten is de nieuwste
    # lat en lon van een cel: het midden van zijn foto's (op de tegel van zoom 16 na)
    utr = next(c for c in cellen if c["n"] == 3)
    assert math.isclose(utr["lat"], sum(p[0] for p in utrecht) / 3, abs_tol=0.01)
    assert math.isclose(utr["lon"], sum(p[1] for p in utrecht) / 3, abs_tol=0.01)
    # Een venster ernaast: niets; zoom 14: elke foto zijn eigen cel
    assert await kaart(client, h, "20.0,35.0,30.0,40.0", 8) == []
    assert sum(c["n"] for c in await kaart(client, h, EUROPA, 14)) == 4


async def test_meer_dan_500_cellen_worden_grotere_cellen(client, admin, settings, monkeypatch):
    h, _tokens = admin
    # 40 x 40 punten op een raster in Nederland: op zoom 14 elk een eigen cel (1.600 > 500)
    punten = [(51.0 + i * 0.05, 3.5 + j * 0.08) for i in range(40) for j in range(40)]
    await zet_punten(client, admin, settings, punten)
    venster = "3.0,50.5,7.0,53.5"
    for z in (14, 12, 10):
        cellen = await kaart(client, h, venster, z)
        assert 0 < len(cellen) <= photos_map.MAX_CELLS, z
        assert sum(c["n"] for c in cellen) == len(punten), z  # er valt nooit iets weg
    # Op zoom 14 is de cel groter geworden dan op die zoom hoort: een cel bevat meer dan één punt
    cellen = await kaart(client, h, venster, 14)
    assert max(c["n"] for c in cellen) > 1
    # De cellen zijn die van een grotere s: elk punt valt in precies één cel
    s = next(
        s for s in range(tiles.shift(14), 17)
        if {((x >> s), (y >> s)) for x, y in (tiles.tile16(*p) for p in punten)}
        == {(c["x"], c["y"]) for c in cellen}
    )  # fmt: skip
    assert s > tiles.shift(14)
    # De kleinste s die past: bij s - 1 waren het er te veel
    te_veel = {((x >> (s - 1)), (y >> (s - 1))) for x, y in (tiles.tile16(*p) for p in punten)}
    assert len(te_veel) > photos_map.MAX_CELLS
    # Door halveren: de hele wereld op zoom 14 kost hooguit vijf vragen (niet één per s), en
    # geeft dezelfde cellen als een s na de andere proberen
    vragen = []
    echt = photos_map._cells

    async def tel(session, user, s, *rest):
        vragen.append(s)
        return await echt(session, user, s, *rest)

    monkeypatch.setattr(photos_map, "_cells", tel)
    wereld = await kaart(client, h, "-180,-85,180,85", 14)
    assert len(vragen) <= 5, vragen
    s_w = next(
        s for s in range(17)
        if len({((x >> s), (y >> s)) for x, y in (tiles.tile16(*p) for p in punten)})
        <= photos_map.MAX_CELLS
    )  # fmt: skip
    verwacht_w = {}
    for p in punten:
        x, y = tiles.tile16(*p)
        verwacht_w[(x >> s_w, y >> s_w)] = verwacht_w.get((x >> s_w, y >> s_w), 0) + 1
    assert {(c["x"], c["y"]): c["n"] for c in wereld} == verwacht_w
    monkeypatch.undo()
    # Op zoom 6 past alles: precies de cellen van die zoom (s = 8)
    cellen = await kaart(client, h, venster, 6)
    s6 = tiles.shift(6)
    verwacht = {}
    for p in punten:
        x, y = tiles.tile16(*p)
        verwacht[(x >> s6, y >> s6)] = verwacht.get((x >> s6, y >> s6), 0) + 1
    assert {(c["x"], c["y"]): c["n"] for c in cellen} == verwacht


async def test_over_180_graden(client, admin, settings):
    h, _tokens = admin
    fiji = [(-17.0, 179.9), (-17.1, -179.9)]
    ids = await zet_punten(client, admin, settings, fiji)
    # west > oost: over 180° heen, beide kanten
    over = await kaart(client, h, "179.0,-20.0,-179.0,-15.0", 10)
    assert sum(c["n"] for c in over) == 2
    # west < oost: het venster van -179 tot 179 ligt er net naast
    assert await kaart(client, h, "-179.0,-20.0,179.0,-15.0", 10) == []
    r = await client.get(
        "/api/v1/photos/map/items", params={"bbox": "179.0,-20.0,-179.0,-15.0"}, headers=h
    )
    assert {i["id"] for i in r.json()["items"]} == set(ids)
    # Zoom 0 met een venster dat bijna de hele wereld in twee stukken pakt: een cel die in
    # beide stukken valt (x = 1: lengte -90 tot 0), telt één keer, met beide foto's
    beide = await zet_punten(client, admin, settings, [(10.0, -50.0), (10.0, -5.0)])
    cellen = await kaart(client, h, "-10.0,-60.0,-20.0,60.0", 0)
    x1 = [c for c in cellen if c["x"] == 1]
    assert len(x1) == 1 and x1[0]["n"] == 2, cellen
    assert x1[0]["cover"] == beide[0]


@pytest.mark.parametrize(
    "bbox",
    [
        "1,2,3",  # drie getallen
        "1,2,3,4,5",
        "a,b,c,d",  # geen getallen (het patroon)
        "1;2;3;4",
        "0,10,1,5",  # zuid boven noord
        "200,0,0,1",  # buiten de wereld
        "0,-91,1,0",
        "nan,0,1,1",  # niet eindig
        "inf,0,1,1",
        "1e999,0,1,1",
        "",
    ],
)
async def test_een_venster_dat_niet_klopt(client, admin, bbox):
    h, _tokens = admin
    for pad, extra in (("/photos/map", {"z": 5}), ("/photos/map/items", {})):
        r = await client.get(f"/api/v1{pad}", params={"bbox": bbox, **extra}, headers=h)
        assert r.status_code == 422, (pad, bbox, r.text)
        assert code_of(r) == "invalid_input"
        assert params_of(r)["field"] == "query.bbox", (pad, bbox)
        assert not bbox or bbox not in r.text, "de afgewezen waarde komt niet terug"


@pytest.mark.parametrize("z", [-1, 15, "x"])
async def test_een_zoom_die_niet_klopt(client, admin, z):
    h, _tokens = admin
    r = await client.get("/api/v1/photos/map", params={"bbox": EUROPA, "z": z}, headers=h)
    assert r.status_code == 422 and params_of(r)["field"] == "query.z"


@pytest.mark.parametrize("veel", [False, True], ids=["vanaf-het-venster", "langs-de-tijdlijn"])
async def test_de_items_per_pagina_met_de_cursor(client, admin, settings, monkeypatch, veel):
    """Beide wegen van /photos/map/items geven dezelfde foto's in dezelfde volgorde."""
    h, _tokens = admin
    ids = await zet_punten(client, admin, settings, [UTRECHT] * 5 + [PARIJS])
    monkeypatch.setattr(photos_map, "PAGE", 2)
    monkeypatch.setattr(photos_map, "MANY", 1 if veel else 10_000)
    gezien, cursor, paginas = [], None, 0
    while True:
        params = {"bbox": "4.0,51.0,6.0,53.0"}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/api/v1/photos/map/items", params=params, headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        gezien += [i["id"] for i in body["items"]]
        paginas += 1
        cursor = body["cursor"]
        if cursor is None:
            break
    # Nieuwste eerst, zoals de tijdlijn; Parijs ligt buiten het venster
    assert gezien == ids[:5] and paginas == 3
    r = await client.get(
        "/api/v1/photos/map/items", params={"bbox": EUROPA, "cursor": "kapot"}, headers=h
    )
    assert r.status_code == 422 and params_of(r)["field"] == "query.cursor", r.text


def test_de_tegels_zijn_die_van_de_bench():
    rng = random.Random(32)
    punten = [(rng.uniform(-85, 85), rng.uniform(-180, 180)) for _ in range(2000)]
    punten += [(0.0, 0.0), (85.0511, 180.0), (-85.0511, -180.0), (90.0, 0.0), (-90.0, 0.0),
               (52.0907, 5.1214), (0.0, 179.9999999), (0.0, -180.0)]  # fmt: skip
    for lat, lon in punten:
        assert tiles.tile16(lat, lon) == bench.tegel16(lat, lon), (lat, lon)
    # Het midden van een tegel ligt in die tegel
    for x, y in ((0, 0), (32768, 21000), (65535, 65535)):
        assert tiles.tile16(tiles.lat_of(y + 0.5), tiles.lon_of(x + 0.5)) == (x, y)


def test_de_stukken_van_een_venster():
    xs, y0, y1 = tiles.ranges(*tiles.parse_bbox("179.0,-20.0,-179.0,-15.0"))
    assert len(xs) == 2 and xs[0][1] == tiles.N - 1 and xs[1][0] == 0
    assert y0 <= y1
    xs, _y0, _y1 = tiles.ranges(*tiles.parse_bbox(EUROPA))
    assert len(xs) == 1 and xs[0][0] < xs[0][1]
    # De polen vallen op de rand van Webmercator
    _xs, y0, y1 = tiles.ranges(*tiles.parse_bbox("-180,-90,180,90"))
    assert (y0, y1) == (0, tiles.N - 1)
