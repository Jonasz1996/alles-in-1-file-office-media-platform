"""Personen in de wereld van §4.2 (tests/search/wereld.py; Keuze 25a en 37; Z6): Eva heeft
"Anna" op IMG_utrecht.jpg en de kopie ervan in Werk/Foto's, Iris heeft "Joop". Iedereen die de
foto mag zien (Bram, Cas, de gast Gijs) ziet er niemand op; niemand anders dan Eva kent Anna, ook
de beheerder niet; Brams eigen kopie heeft niemand; een publieke link heeft geen route voor
personen. In beide motoren.
"""

from __future__ import annotations

import pytest

from app.main import app
from app.people import search as people_search
from app.search import limits
from tests.search import wereld as W

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

ANDEREN = [naam for naam in W.GEBRUIKERS if naam != "eva"]


@pytest.fixture(autouse=True)
def _aangemeld(monkeypatch):
    people_search.register()
    monkeypatch.setattr(limits, "RATE", {"search": 100_000, "suggest": 100_000})


@pytest.fixture(params=["fts", "like"])
def motor(request, settings, monkeypatch) -> str:
    if request.param == "like":
        monkeypatch.setattr(settings, "search_index", "uit")
    return request.param


async def kop(client, naam: str) -> dict[str, str]:
    login = {"email": W.email(naam), "password": W.WACHTWOORD}
    r = await client.post("/api/v1/auth/login", json=login)
    assert r.status_code == 200, (naam, r.text)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def get(client, headers, path, **params):
    return await client.get(f"/api/v1{path}", params=params, headers=headers)


async def test_alleen_eva_ziet_anna(client, settings, motor):
    w = await W.bouw_async(settings)
    anna, joop = str(w.personen["anna"]), str(w.personen["joop"])
    foto, kopie, bram_kopie = w.ids["foto"], w.ids["foto_kopie"], w.ids["bram_kopie"]
    eva = await kop(client, "eva")
    # Eva: Anna op beide foto's, als persoon, in het filter, de groep en de suggesties
    for node in (foto, kopie):
        r = await get(client, eva, f"/files/{node}/people")
        assert [p["person"]["id"] for p in r.json()["people"]] == [anna]
    r = await get(client, eva, "/people")
    assert [(p["id"], p["count"]) for p in r.json()["people"]] == [(anna, 2)]
    r = await get(client, eva, "/search", person=anna)
    assert {h["node"]["id"] for h in r.json()["results"]} == {str(foto), str(kopie)}
    r = await get(client, eva, "/search", q=W.PERSOON)
    assert r.json()["engine"] == motor
    assert r.json()["groups"]["people"] == [{"id": anna, "name": W.PERSOON, "n": 2}]
    for naam in ANDEREN:
        h = await kop(client, naam)
        # Op Eva's foto (die Bram, Cas en Gijs mogen zien), op Brams kopie, op die van Iris
        for node in (foto, kopie, bram_kopie, w.ids["iris_foto"]):
            r = await get(client, h, f"/files/{node}/people")
            assert r.status_code == 200, (naam, r.text)
            eigen = naam == "iris" and node == w.ids["iris_foto"]
            assert (r.json()["people"] != []) == eigen, (naam, node)
        r = await get(client, h, "/people")
        assert [p["id"] for p in r.json()["people"]] == ([joop] if naam == "iris" else [])
        r = await get(client, h, f"/people/{anna}/photos")
        assert r.status_code == 404, naam
        r = await client.patch(f"/api/v1/people/{anna}", json={"hidden": True}, headers=h)
        assert r.status_code == 404, naam
        # Het filter met Anna vindt niets, ook niet de foto die ze mogen zien
        r = await get(client, h, "/search", person=anna)
        assert r.status_code == 200 and r.json()["results"] == [], (naam, r.text)
        r = await get(client, h, "/search", q="utrecht", person=anna)
        assert r.json()["results"] == [], naam
        # Geen groep en geen suggestie met Anna
        r = await get(client, h, "/search", q=W.PERSOON)
        assert r.json()["groups"] is None, naam
        if motor == "fts":
            r = await get(client, h, "/search/suggest", q="an")
            assert r.status_code == 200 and r.json()["people"] == [], naam
    # Iris ziet haar eigen Joop; Eva niet
    r = await get(client, eva, f"/files/{w.ids['iris_foto']}/people")
    assert r.json()["people"] == []
    r = await get(client, eva, "/search", person=joop)
    assert r.json()["results"] == []


async def test_geen_route_voor_personen_onder_een_publieke_link():
    paden = app.openapi()["paths"]
    publiek = [p for p in paden if p.startswith("/api/v1/public") or p.startswith("/s/")]
    assert publiek, "de publieke routes staan in de OpenAPI"
    assert [p for p in publiek if "people" in p or "person" in p] == []
