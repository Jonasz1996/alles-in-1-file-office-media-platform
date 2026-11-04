"""De suggesties voor het zoekveld (bouwontwerp fase 6, Keuze 46; app/search/suggest.py).

Wat hier vastligt, met de wereld van §4.2:
- namen: alleen wat je mag zien (de bereiken van zoeken: geen prullenbak, geen album, geen
  bestand van een ander zonder deling), eerst wie met het eerste woord begint, hooguit 5, met
  kind; pas bij een woord van ≥ 3 tekens;
- de tweede controle haalt een naam weg die in hetzelfde verzoek onzichtbaar werd;
- tags: alleen de eigen, het voorvoegsel eerst; albums: eigen en gedeelde, niet als gast en
  niet van een ander zonder deling;
- personen en plaatsen via de haken (hooks.register_suggest en de suggest van een filter); een
  haak die faalt, geeft één logregel zonder de vraag;
- een te lange vraag geeft 400 search_query_too_long.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from app.search import filters, hooks, limits, suggest
from app.search.types import PersonRef, PlaceRef, Suggestions
from tests.search import wereld as W
from tests.search.test_search_access import _doe, kop

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _zonder_snelheidsgrens(monkeypatch):
    monkeypatch.setattr(limits, "RATE", {"search": 100_000, "suggest": 100_000})


@pytest.fixture(autouse=True)
def _ruime_grens(monkeypatch):
    """Het gaat om wat er komt, niet om de tijd (zie test_search_access.py, _ruime_grens)."""
    monkeypatch.setattr(suggest, "NAMES_MS", 10_000.0)


async def _suggest(client, headers, q: str) -> dict:
    r = await client.get("/api/v1/search/suggest", params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _namen(found: dict) -> list[str]:
    return [n["name"] for n in found["names"]]


async def test_namen_die_je_mag_zien_het_voorvoegsel_eerst(client, settings):
    w = await W.bouw_async(settings)
    eva, bram, dirk = [await kop(client, naam) for naam in ("eva", "bram", "dirk")]
    # De prullenbak (Oud verslag.txt) en het album niet
    found = await _suggest(client, eva, "verslag")
    assert found["names"] == [
        {"id": str(w.ids["doc"]), "name": "Verslag kwartaal.docx", "kind": "file"}
    ]
    found = await _suggest(client, eva, "utrecht")
    assert set(_namen(found)) == {
        "IMG_utrecht.jpg",
        "IMG_utrecht kopie.jpg",
        "IMG_utrecht klein.jpg",
    }
    assert str(w.ids["album"]) not in {n["id"] for n in found["names"]}
    # Een map, met kind
    assert (await _suggest(client, eva, "werk"))["names"][0] == {
        "id": str(w.ids["werk"]),
        "name": "Werk",
        "kind": "dir",
    }
    # Bram: zijn eigen kopie begint met "kopie" en komt eerst; Eva's kopie via de deling daarna
    assert _namen(await _suggest(client, bram, "kopie")) == [
        "Kopie Utrecht.jpg",
        "IMG_utrecht kopie.jpg",
    ]
    # Dirk ziet niets van Eva
    assert _namen(await _suggest(client, dirk, "utrecht")) == []
    assert _namen(await _suggest(client, dirk, "notities")) == ["Notities.txt"]
    # Hooguit 5
    assert len((await _suggest(client, eva, "jpg"))["names"]) == 5


async def test_kort_geen_namen_wel_tags(client, settings):
    """Bij één of twee tekens geen namen (geen trigram), wel tags."""
    await W.bouw_async(settings)
    eva = await kop(client, "eva")
    r = await client.post("/api/v1/tags", json={"name": "Ab-tag"}, headers=eva)
    assert r.status_code == 201, r.text
    found = await _suggest(client, eva, "ab")
    assert found["names"] == []
    assert [t["name"] for t in found["tags"]] == ["Ab-tag"]


async def test_de_tweede_controle(client, settings, monkeypatch, caplog):
    """De deling met Hans verdwijnt nadat de bereiken gemaakt zijn: de naam valt weg, met één
    logregel met alleen het node-id."""
    w = await W.bouw_async(settings)
    hans = await kop(client, "hans")
    assert _namen(await _suggest(client, hans, "verslag")) == ["Verslag kwartaal.docx"]
    echt = suggest.search_ranges

    async def en_dan_intrekken(session, user):
        roots = await echt(session, user)
        await _doe(settings, W.intrekken, w)
        return roots

    monkeypatch.setattr(suggest, "search_ranges", en_dan_intrekken)
    with caplog.at_level(logging.INFO, logger="platform.search"):
        assert (await _suggest(client, hans, "verslag"))["names"] == []
    regels = [r.getMessage() for r in caplog.records if "search_access_mismatch" in r.getMessage()]
    assert regels == [f"search_access_mismatch {w.ids['doc']}"]


async def test_tags_alleen_de_eigen(client, settings):
    await W.bouw_async(settings)
    eva, bram = await kop(client, "eva"), await kop(client, "bram")
    for naam in ("Zomervakantie", "Vakantie", "Werk", "Wintervakantie 2025"):
        r = await client.post("/api/v1/tags", json={"name": naam}, headers=eva)
        assert r.status_code == 201, r.text
    found = await _suggest(client, eva, "vakantie")
    assert [t["name"] for t in found["tags"]] == [
        "Vakantie",
        "Wintervakantie 2025",
        "Zomervakantie",
    ]
    assert set(found["tags"][0]) == {"id", "name", "color"}
    assert [t["name"] for t in (await _suggest(client, eva, "VAKANTIE 2025"))["tags"]] == [
        "Wintervakantie 2025"
    ]
    assert (await _suggest(client, bram, "vakantie"))["tags"] == []


async def test_albums_eigen_en_gedeeld(client, settings):
    w = await W.bouw_async(settings)
    album = [{"id": str(w.ids["album"]), "name": "Utrecht"}]
    for naam in ("eva", "bram"):
        assert (await _suggest(client, await kop(client, naam), "utrecht"))["albums"] == album
    for naam in ("dirk", "gijs", "cas", "hans"):
        assert (await _suggest(client, await kop(client, naam), "utrecht"))["albums"] == [], naam


async def test_personen_en_plaatsen_via_de_haken(client, settings, monkeypatch, caplog):
    await W.bouw_async(settings)
    eva = await kop(client, "eva")
    gezien: list[str] = []
    anna = PersonRef(id=uuid.uuid4(), name="Anna")
    utrecht = PlaceRef(id=W.PLAATS_ID, name="Utrecht", admin1="Utrecht", country="NL")

    async def personen(db, user, q):
        gezien.append(q)
        return Suggestions(people=[anna, anna], places=[])

    async def kapot(db, user, q):
        raise RuntimeError("stuk")

    async def plaatsen(db, user, q):
        return [utrecht, *[PlaceRef(id=i, name=f"Plaats {i}") for i in range(10)]]

    monkeypatch.setattr(hooks, "_suggest", [kapot, personen])
    zonder = filters.Filter("plaats_test", lambda sel, n, p, user: sel, plaatsen)
    monkeypatch.setitem(filters.FILTERS, "plaats_test", zonder)
    with caplog.at_level(logging.WARNING, logger="platform.search"):
        found = await _suggest(client, eva, "geheimwoord")
    assert gezien == ["geheimwoord"]
    assert found["people"] == [{"id": str(anna.id), "name": "Anna"}]
    assert found["places"][0] == utrecht.model_dump() and len(found["places"]) == 5
    regels = [r.getMessage() for r in caplog.records]
    assert any("search_suggest_hook_failed" in r and "kapot" in r for r in regels)
    assert not any("geheimwoord" in r for r in regels)


async def test_te_lang_en_leeg(client, settings):
    await W.bouw_async(settings)
    eva = await kop(client, "eva")
    r = await client.get(
        "/api/v1/search/suggest", params={"q": " ".join(["woord"] * 11)}, headers=eva
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "search_query_too_long"
    leeg = {"names": [], "people": [], "places": [], "tags": [], "albums": []}
    assert await _suggest(client, eva, "!!") == leeg
