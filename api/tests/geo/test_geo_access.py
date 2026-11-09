"""Wie ziet een plek (bouwontwerp fase 6, Keuzes 25a en 27; §4.2): de wereld van
tests/search/wereld.py. Eva heeft drie foto's met een plek in Werk/Foto's (gedeeld met Bram, Team,
de gast Gijs en een publieke link), Bram een eigen kopie, Iris een eigen foto. Een plek is van
een bestand van één eigenaar en alleen voor hem: iedereen die de foto wel mag zien krijgt 404,
precies zoals bij een foto zonder plek (geen verschil, dus geen lek)."""

import pytest

from tests.factories import code_of
from tests.search import wereld as W

pytestmark = pytest.mark.anyio

UTRECHT = {"name": "Utrecht", "admin1": "Utrecht", "cc": "NL"}
VENSTER = "3.0,50.0,8.0,54.0"


async def kop(client, naam: str) -> dict[str, str]:
    login = {"email": W.email(naam), "password": W.WACHTWOORD}
    r = await client.post("/api/v1/auth/login", json=login)
    assert r.status_code == 200, (naam, r.text)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def get(client, headers, path, **params):
    return await client.get(f"/api/v1{path}", params=params, headers=headers)


@pytest.fixture
async def wereld(client, settings, plaatsen):
    return await W.bouw_async(settings)


async def test_alleen_de_eigenaar_ziet_de_plek(client, wereld):
    w = wereld
    eva = await kop(client, "eva")
    r = await get(client, eva, f"/files/{w.ids['foto']}/location")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    lat, lon = W.fixtures.UTRECHT
    assert r.json() == {"lat": lat, "lon": lon, "alt": None, "place": UTRECHT}
    # Zonder plek: 404, en dat is wat ieder ander ook krijgt
    geen = await get(client, eva, f"/files/{w.ids['dom']}/location")
    assert geen.status_code == 404
    for naam in W.GEBRUIKERS:
        if naam == "eva":
            continue
        h = await kop(client, naam)
        for sleutel in ("foto", "foto_kopie", "foto_klein", "dom"):
            r = await get(client, h, f"/files/{w.ids[sleutel]}/location")
            assert (r.status_code, r.json()) == (404, geen.json()), (naam, sleutel)
    # Brams eigen kopie: zijn plek, alleen voor hem
    bram = await kop(client, "bram")
    r = await get(client, bram, f"/files/{w.ids['bram_kopie']}/location")
    assert r.status_code == 200 and r.json()["place"] == UTRECHT
    r = await get(client, eva, f"/files/{w.ids['bram_kopie']}/location")
    assert (r.status_code, r.json()) == (404, geen.json())
    # Een id dat niet bestaat: hetzelfde
    r = await get(client, eva, "/files/00000000-0000-0000-0000-000000000000/location")
    assert (r.status_code, r.json()) == (404, geen.json())


async def test_de_kaart_en_de_plaatsen_per_gebruiker(client, wereld):
    w = wereld
    verwacht = {"eva": 3, "bram": 1, "iris": 1}
    for naam, (rol, _weergave) in W.GEBRUIKERS.items():
        h = await kop(client, naam)
        kaart = await get(client, h, "/photos/map", bbox=VENSTER, z=8)
        items = await get(client, h, "/photos/map/items", bbox=VENSTER)
        plaatsen = await get(client, h, "/photos/places")
        if rol == "guest":
            for r in (kaart, items, plaatsen):
                assert (r.status_code, code_of(r)) == (403, "guest"), naam
            continue
        n = verwacht.get(naam, 0)
        assert sum(c["n"] for c in kaart.json()["cells"]) == n, naam
        assert len(items.json()["items"]) == n, naam
        if n:
            assert plaatsen.json()["places"] == [
                {"id": W.PLAATS_ID, "name": "Utrecht", "admin1": "Utrecht", "country": "NL", "n": n}
            ], naam
        else:
            assert plaatsen.json() == {"places": []}, naam
    # Eva's kaartitems zijn precies haar drie foto's
    eva = await kop(client, "eva")
    items = (await get(client, eva, "/photos/map/items", bbox=VENSTER)).json()["items"]
    assert {i["id"] for i in items} == {str(w.ids[k]) for k in ("foto", "foto_kopie", "foto_klein")}


async def test_wat_naar_de_prullenbak_gaat_is_van_de_kaart(client, wereld):
    w = wereld
    eva = await kop(client, "eva")
    r = await client.post(
        "/api/v1/batch/trash", json={"ids": [str(w.ids["foto_klein"])]}, headers=eva
    )
    assert r.status_code in (200, 204), r.text
    kaart = await get(client, eva, "/photos/map", bbox=VENSTER, z=8)
    assert sum(c["n"] for c in kaart.json()["cells"]) == 2
    r = await get(client, eva, f"/files/{w.ids['foto_klein']}/location")
    assert r.status_code == 404
    plaatsen = (await get(client, eva, "/photos/places")).json()["places"]
    assert [p["n"] for p in plaatsen] == [2]


async def test_bronvermelding(client, wereld, settings):
    h = await kop(client, "bram")
    r = await get(client, h, "/geo/attribution")
    assert r.status_code == 200
    body = r.json()
    assert body["places"]["source"] == "GeoNames cities1000"
    assert body["places"]["license"] == "CC BY 4.0"
    assert body["countries"] == {
        "source": "Natural Earth 1:10m admin-0", "license": "publiek domein", "version": "v5.1.2"
    }  # fmt: skip
    assert body["world"]["license"] == "publiek domein"
