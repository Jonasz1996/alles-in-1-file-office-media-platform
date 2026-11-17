"""De filters place, country en camera, de suggesties "plaatsen" en de groep plaatsen, met echte
foto's door de keten van photometa (bouwontwerp fase 6, Keuzes 26, 27, 28 en 43; Z3).

Elk filter en elk paar tegen de referentie staat in tests/search/test_search_filters.py; hier de
randen met echte uploads, in beide motoren:

- een plek is alleen van de eigenaar: Bram ziet Eva's gedeelde foto, maar met place of country
  vindt hij hem nooit (Keuze 25a en 27), en camera ook niet (own_live, Keuze 43);
- camera past op de hele camera of op de eerste woorden ervan, gevouwen; niet midden in;
- locatie "uit": place en country geven meteen niets en de plaatsen verdwijnen uit de suggesties
  en de groep, ook vóór de job geo.off de rijen wist; camera blijft werken; "mij" weer: terug
  (suggesties geeft alleen FtsEngine: LikeFallback heeft er geen, dat is van Z1);
- zonder places.sqlite geen namen (geen suggesties), maar het filter op een id werkt;
- een gast heeft geen eigen foto's: geen plaatsen.
"""

from __future__ import annotations

import pytest

from app.geo import places
from app.search import filters_photo, limits
from tests.geo.hulp import AMSTERDAM, map_maken, met_gps, uid, verwerk, zonder_gps
from tests.test_previews import upload
from tests.test_shares import person, share_with

pytestmark = pytest.mark.anyio

CAMERA = "fabrikant camera x1"  # camera_jpeg(): "Fabrikant" en "  Camera X1 "


@pytest.fixture(params=["fts", "like"])
def motor(request, settings, monkeypatch) -> str:
    if request.param == "like":
        monkeypatch.setattr(settings, "search_index", "uit")
    return request.param


@pytest.fixture(autouse=True)
def _filters(settings, monkeypatch):
    """Een test eerder in de run kan filters.reset() gedaan hebben (test_contracts.py). De haak
    van de suggesties krijgt geen settings mee en leest places.sqlite via get_settings(), zoals
    de app (die heeft er één); hier die van de test. Geen grens van de snelheid per gebruiker:
    het gaat om wat er gevonden wordt."""
    filters_photo.register()
    monkeypatch.setattr(filters_photo, "get_settings", lambda: settings)
    monkeypatch.setattr(limits, "RATE", {"search": 100_000, "suggest": 100_000})


async def zoek(client, headers, motor: str, **params) -> set[str]:
    r = await client.get("/api/v1/search", params={**params, "limit": 50}, headers=headers)
    assert r.status_code == 200, (params, r.text)
    page = r.json()
    assert page["engine"] == motor and not page["more"], params
    return {h["node"]["name"] for h in page["results"]}


async def tijdlijn(client, headers, **params) -> set[str]:
    r = await client.get("/api/v1/photos", params=params, headers=headers)
    assert r.status_code == 200, (params, r.text)
    return {item["name"] for item in r.json()["items"]}


async def suggesties(client, headers, q: str) -> list[dict]:
    r = await client.get("/api/v1/search/suggest", params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["places"]


async def groep(client, headers, q: str) -> list[dict] | None:
    r = await client.get("/api/v1/search", params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    groups = r.json()["groups"]
    return None if groups is None else groups["places"]


async def _wereld(client, h, settings):
    """Eva: a.jpg (plek), b.jpg (zonder plek) en in de map Gedeeld (met Bram) d.jpg (plek). Bram:
    c.jpg (plek). Alle vier dezelfde camera."""
    await upload(client, h, "a.jpg", met_gps("red"))
    await upload(client, h, "b.jpg", zonder_gps("blue"))
    gedeeld = await map_maken(client, h, "Gedeeld")
    await upload(client, h, "d.jpg", met_gps("white"), parent=gedeeld)
    bram_h, bram = await person(client, h, "bram@example.com")
    await share_with(client, h, gedeeld, user_id=bram, permission="read")
    await upload(client, bram_h, "c.jpg", met_gps("green"))
    await verwerk(settings)
    return bram_h


async def test_een_plek_en_een_camera_zijn_alleen_van_de_eigenaar(
    client, admin, settings, plaatsen, motor
):
    h, _ = admin
    bram_h = await _wereld(client, h, settings)
    for params in ({"place": AMSTERDAM}, {"country": "NL"}, {"country": "nl"},
                   {"country": " NL "}):  # fmt: skip
        assert await zoek(client, h, motor, **params) == {"a.jpg", "d.jpg"}, params
        assert await zoek(client, h, motor, q="jpg", **params) == {"a.jpg", "d.jpg"}, params
        assert await tijdlijn(client, h, **params) == {"a.jpg", "d.jpg"}, params
        # Bram ziet d.jpg (gedeeld), maar de plek is van Eva
        assert await zoek(client, bram_h, motor, **params) == {"c.jpg"}, params
        assert await tijdlijn(client, bram_h, **params) == {"c.jpg"}, params
    for camera in (CAMERA, "Fabrikant Camera X1", "FABRIKANT", "  fabrikant\tcamera  "):
        assert await zoek(client, h, motor, camera=camera) == {"a.jpg", "b.jpg", "d.jpg"}, camera
        assert await zoek(client, bram_h, motor, camera=camera) == {"c.jpg"}, camera
    # Niet midden in de camera, niet een deel van een woord, en geen andere plaats of land
    for params in ({"camera": "camera x1"}, {"camera": "fabri"}, {"camera": CAMERA + " pro"},
                   {"place": AMSTERDAM + 1}, {"country": "BE"}, {"country": "NLD"}):  # fmt: skip
        assert await zoek(client, h, motor, **params) == set(), params
        assert await tijdlijn(client, h, **params) == set(), params
    # Samen: EN
    assert await zoek(client, h, motor, place=AMSTERDAM, camera="fabrikant") == {"a.jpg", "d.jpg"}
    assert await zoek(client, h, motor, place=AMSTERDAM, country="BE") == set()


async def test_de_plaatsen_in_de_suggesties_en_de_groep(client, admin, settings, plaatsen):
    h, _ = admin
    bram_h = await _wereld(client, h, settings)
    amsterdam = {"id": AMSTERDAM, "name": "Amsterdam", "admin1": "North Holland", "country": "NL"}
    for q in ("amst", "Amsterdam", "AMSTÉ", "  amsterdam  "):
        assert await suggesties(client, h, q) == [amsterdam], q
    # Utrecht staat in places.sqlite, maar Eva heeft daar geen foto; de provincie telt niet
    for q in ("utrecht", "north", "dam", "x"):
        assert await suggesties(client, h, q) == [], q
    assert await groep(client, h, "amsterdam") == [{"id": str(AMSTERDAM), "name": "Amsterdam",
                                                    "n": 2}]  # fmt: skip
    # Bram telt alleen zijn eigen foto
    assert await suggesties(client, bram_h, "amst") == [amsterdam]
    assert await groep(client, bram_h, "amsterdam") == [{"id": str(AMSTERDAM), "name": "Amsterdam",
                                                         "n": 1}]  # fmt: skip
    # Niet op een volgende pagina
    r = await client.get("/api/v1/search", params={"q": "amsterdam", "offset": 30}, headers=h)
    assert r.status_code == 200 and r.json()["groups"] is None, r.text
    # Een gast heeft geen eigen foto's
    gast_h, _ = await person(client, h, "gast@example.com", role="guest")
    assert await suggesties(client, gast_h, "amst") == []
    # Zonder places.sqlite geen namen, dus geen plaatsen; het filter op een id werkt wel
    places.forget()
    settings.places = "uit"
    try:
        assert await suggesties(client, h, "amst") == []
        assert await groep(client, h, "amsterdam") is None
        assert await zoek(client, h, "fts", place=AMSTERDAM) == {"a.jpg", "d.jpg"}
    finally:
        settings.places = "aan"
        places.forget()


async def test_locatie_uit_en_weer_mij(client, admin, settings, plaatsen, motor):
    h, tokens = admin
    await _wereld(client, h, settings)

    async def locatie(waarde: str) -> None:
        r = await client.patch("/api/v1/me/photo-settings", json={"location": waarde}, headers=h)
        assert r.status_code == 200, r.text

    await locatie("uit")
    # Meteen: de rijen staan er nog (de job geo.off draait hier niet), de filters kijken naar
    # photo_pref
    for params in ({"place": AMSTERDAM}, {"country": "NL"}):
        assert await zoek(client, h, motor, **params) == set(), params
        assert await tijdlijn(client, h, **params) == set(), params
    assert await suggesties(client, h, "amst") == []
    assert await groep(client, h, "amsterdam") is None
    # De camera is geen plek
    assert await zoek(client, h, motor, camera="fabrikant") == {"a.jpg", "b.jpg", "d.jpg"}

    await locatie("mij")
    assert await zoek(client, h, motor, place=AMSTERDAM) == {"a.jpg", "d.jpg"}
    assert await groep(client, h, "amsterdam") == [{"id": str(AMSTERDAM), "name": "Amsterdam",
                                                    "n": 2}]  # fmt: skip
    if motor == "fts":  # LikeFallback heeft geen suggesties (like.suggest, van Z1)
        assert [p["id"] for p in await suggesties(client, h, "amst")] == [AMSTERDAM]
    assert uid(tokens)


async def test_de_camera_en_het_land_zoals_blob_meta():
    assert filters_photo.camera_key("  Fabrikant\x00 \tCamera X1 ") == CAMERA
    assert filters_photo.camera_key("Ñikon  D750") == "nikon d750"
    assert filters_photo.camera_key(" \t ") == ""
    assert filters_photo.camera_key("x" * 500) == "x" * filters_photo.CAMERA_CHARS
    assert filters_photo.country_code(" nl ") == "NL"
    for fout in ("", "N", "NLD", "N1", "1N", "ñl"):
        assert filters_photo.country_code(fout) is None, fout
