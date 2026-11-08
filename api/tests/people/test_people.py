"""Personen op foto's (bouwontwerp fase 6, §3.4, Keuze 25a, 37 en 38; Z6): de routes van
routers/people.py, het filter person in beide motoren, de groep en de suggesties, en de haak bij
een kopie.

- alleen de eigenaar: een persoon van een ander is 404, op de foto van een ander is de lijst leeg;
- samenvoegen en wissen;
- een kopie door dezelfde eigenaar heeft de persoon ook, een kopie door Bram niet;
- tellingen en omslagen alleen met eigen, levende foto's (own_live).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import Person, PhotoPerson, Share
from app.people import people as P
from app.people import search as people_search
from app.search import limits
from tests.factories import code_of, create_file, create_folder, create_user, maker, params_of
from tests.test_photos import login, photo

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

BASE = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _aangemeld(monkeypatch):
    """Een andere test kan filters.reset() of hooks.reset() gedaan hebben; de snelheid per
    gebruiker staat in test_search_limits.py."""
    people_search.register()
    monkeypatch.setattr(limits, "RATE", {"search": 100_000, "suggest": 100_000})


@pytest.fixture
async def huis(client, settings):
    """Eva met drie foto's (een in de prullenbak), een tekstbestand en de map Gedeeld (gedeeld
    met Bram om te lezen) met een foto; Bram met een eigen foto en een eigen map."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        gedeeld = await create_folder(session, eva, name="Gedeeld")
        ids = {
            "eva": eva.id,
            "bram": bram.id,
            "eva_root": eva.root_id,
            "bram_root": bram.root_id,
            "gedeeld": gedeeld.id,
            "een": (await photo(session, eva, "strand-1.jpg", BASE)).id,
            "twee": (await photo(session, eva, "strand-2.jpg", BASE + timedelta(days=1))).id,
            "weg": (
                await photo(session, eva, "strand-weg.jpg", BASE + timedelta(days=2), deleted=True)
            ).id,
            "tekst": (await create_file(session, eva, name="notitie.txt")).id,
            "samen": (
                await photo(session, eva, "samen.jpg", BASE + timedelta(days=3), gedeeld.id)
            ).id,
            "bram_foto": (await photo(session, bram, "bram.jpg", BASE)).id,
            "bram_map": (await create_folder(session, bram, name="Kopieën")).id,
        }
        session.add(Share(node_id=gedeeld.id, owner_id=eva.id, grantee_id=bram.id,
                          permission="read"))  # fmt: skip
        await session.commit()
    ids["h_eva"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    return ids


async def nieuw(client, headers, name: str) -> str:
    r = await client.post("/api/v1/people", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def op(client, headers, node_id, expect=201, **body) -> None:
    r = await client.post(f"/api/v1/files/{node_id}/people", json=body, headers=headers)
    assert r.status_code == expect, r.text


async def op_foto(client, headers, node_id) -> list[dict]:
    r = await client.get(f"/api/v1/files/{node_id}/people", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["people"]


async def personen(client, headers, hidden=None) -> dict[str, dict]:
    params = {} if hidden is None else {"hidden": hidden}
    r = await client.get("/api/v1/people", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return {p["name"]: p for p in r.json()["people"]}


def rij(person_id, node_id, owner_id) -> PhotoPerson:
    """Een persoon op een foto, rechtstreeks (zoals vóór de prullenbak, of in groten getale)."""
    return PhotoPerson(person_id=uuid.UUID(str(person_id)), node_id=node_id, owner_id=owner_id,
                       source="hand", created_at=BASE)  # fmt: skip


async def rijen(settings, **where) -> int:
    async with maker(settings)() as session:
        q = select(func.count()).select_from(PhotoPerson)
        for key, value in where.items():
            q = q.where(getattr(PhotoPerson, key) == value)
        return await session.scalar(q)


async def test_maken_hernoemen_en_verbergen(client, huis):
    eva = huis["h_eva"]
    r = await client.post("/api/v1/people", json={"name": "  Anna \t de  Vries "}, headers=eva)
    assert r.status_code == 201, r.text
    anna = r.json()
    assert anna == {"id": anna["id"], "name": "Anna de Vries", "count": 0, "cover": None,
                    "hidden": False}  # fmt: skip
    # Dezelfde naam (gevouwen) is bezet; een lege naam kan niet
    r = await client.post("/api/v1/people", json={"name": "anna DE vries"}, headers=eva)
    assert (r.status_code, code_of(r)) == (409, "people_name_taken")
    r = await client.post("/api/v1/people", json={"name": " \u0007 "}, headers=eva)
    assert (r.status_code, code_of(r), params_of(r)["field"]) == (422, "invalid_input", "body.name")
    # Hernoemen naar zijn eigen naam mag; naar die van een ander niet
    jan = await nieuw(client, eva, "Jan")
    r = await client.patch(f"/api/v1/people/{anna['id']}", json={"name": "Anna"}, headers=eva)
    assert r.status_code == 200 and r.json()["name"] == "Anna", r.text
    r = await client.patch(f"/api/v1/people/{jan}", json={"name": "ANNA"}, headers=eva)
    assert (r.status_code, code_of(r)) == (409, "people_name_taken")
    r = await client.patch(f"/api/v1/people/{jan}", json={"name": "jan"}, headers=eva)
    assert r.status_code == 200 and r.json()["name"] == "jan", r.text
    # Verbergen en terughalen; hidden komt als tekst binnen
    r = await client.patch(f"/api/v1/people/{jan}", json={"hidden": True}, headers=eva)
    assert r.status_code == 200 and r.json()["hidden"] is True
    assert set(await personen(client, eva)) == {"Anna"}
    assert set(await personen(client, eva, hidden="1")) == {"jan"}
    assert set(await personen(client, eva, hidden="0")) == {"Anna"}
    r = await client.get("/api/v1/people", params={"hidden": "2"}, headers=eva)
    assert r.status_code == 422
    r = await client.patch(f"/api/v1/people/{jan}", json={"hidden": False}, headers=eva)
    assert set(await personen(client, eva)) == {"Anna", "jan"}
    # Een veld dat niet bestaat
    r = await client.patch(f"/api/v1/people/{jan}", json={"naam": "x"}, headers=eva)
    assert r.status_code == 422


async def test_op_een_foto_tellen_en_omslag(client, huis, settings):
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna", box=[0.1, 0.2, 0.3, 0.4])
    anna = (await personen(client, eva))["Anna"]
    await op(client, eva, huis["twee"], person_id=anna["id"])
    await op(client, eva, huis["samen"], person_id=anna["id"])
    gezien = await op_foto(client, eva, huis["een"])
    assert gezien == [{"person": {"id": anna["id"], "name": "Anna"},
                       "box": [0.1, 0.2, 0.3, 0.4], "source": "hand"}]  # fmt: skip
    assert (await op_foto(client, eva, huis["twee"]))[0]["box"] is None
    # Drie foto's; de omslag is de nieuwste
    anna = (await personen(client, eva))["Anna"]
    assert (anna["count"], anna["cover"]) == (3, str(huis["samen"]))
    # Nog eens op dezelfde foto: geen tweede rij, wel het nieuwe kader
    await op(client, eva, huis["twee"], person_id=anna["id"], box=[0, 0, 1, 1])
    assert (await op_foto(client, eva, huis["twee"]))[0]["box"] == [0, 0, 1, 1]
    assert await rijen(settings, node_id=huis["twee"]) == 1
    # Zelf een omslag kiezen; null is weer de nieuwste
    r = await client.patch(
        f"/api/v1/people/{anna['id']}", json={"cover": str(huis["een"])}, headers=eva
    )
    assert r.json()["cover"] == str(huis["een"])
    r = await client.patch(f"/api/v1/people/{anna['id']}", json={"name": "Anna"}, headers=eva)
    assert r.json()["cover"] == str(huis["een"]), "een ander veld laat de omslag staan"
    r = await client.patch(f"/api/v1/people/{anna['id']}", json={"cover": None}, headers=eva)
    assert r.json()["cover"] == str(huis["samen"])
    # Een omslag waar Anna niet op staat, of van een ander: 422
    for node in (huis["tekst"], huis["bram_foto"], huis["weg"], uuid.uuid4()):
        r = await client.patch(
            f"/api/v1/people/{anna['id']}", json={"cover": str(node)}, headers=eva
        )
        assert (r.status_code, params_of(r).get("field")) == (422, "body.cover"), node
    # De prullenbak telt niet mee; terugzetten wel weer
    await client.patch(
        f"/api/v1/people/{anna['id']}", json={"cover": str(huis["samen"])}, headers=eva
    )
    r = await client.delete(f"/api/v1/files/{huis['samen']}", headers=eva)
    assert r.status_code == 204, r.text
    anna = (await personen(client, eva))["Anna"]
    assert (anna["count"], anna["cover"]) == (2, str(huis["twee"]))
    assert await op_foto(client, eva, huis["samen"]) == []
    r = await client.post(f"/api/v1/files/{huis['samen']}/restore", headers=eva)
    assert r.status_code == 200, r.text
    anna = (await personen(client, eva))["Anna"]
    assert (anna["count"], anna["cover"]) == (3, str(huis["samen"]))
    # Eraf halen: de persoon blijft, de omslag die erop wees ook niet
    r = await client.delete(f"/api/v1/files/{huis['samen']}/people/{anna['id']}", headers=eva)
    assert r.status_code == 204, r.text
    anna = (await personen(client, eva))["Anna"]
    assert (anna["count"], anna["cover"]) == (2, str(huis["twee"]))
    r = await client.delete(f"/api/v1/files/{huis['samen']}/people/{anna['id']}", headers=eva)
    assert (r.status_code, code_of(r)) == (404, "not_found")


async def test_de_volgorde_meeste_foto_s_eerst(client, huis):
    eva = huis["h_eva"]
    for naam in ("Bert", "Anna", "Cor"):
        await nieuw(client, eva, naam)
    await op(client, eva, huis["een"], person_id=(await personen(client, eva))["Cor"]["id"])
    r = await client.get("/api/v1/people", headers=eva)
    assert [p["name"] for p in r.json()["people"]] == ["Cor", "Anna", "Bert"]


async def test_kaders_en_grenzen(client, huis, settings):
    eva = huis["h_eva"]
    for box in ([0.5, 0.5, 0.6, 0.1], [0, 0, 0, 0.1], [-0.1, 0, 0.1, 0.1], [0, 0, 0.1, 1.2]):
        r = await client.post(
            f"/api/v1/files/{huis['een']}/people", json={"name": "Anna", "box": box}, headers=eva
        )
        assert (r.status_code, params_of(r).get("field")) == (422, "body.box"), box
    r = await client.post(
        f"/api/v1/files/{huis['een']}/people",
        content='{"name": "Anna", "box": [NaN, 0, 0.1, 0.1]}',
        headers={**eva, "content-type": "application/json"},
    )
    assert r.status_code == 422, r.text
    # Precies één van person_id en name
    r = await client.post(f"/api/v1/files/{huis['een']}/people", json={}, headers=eva)
    assert r.status_code == 422
    r = await client.post(
        f"/api/v1/files/{huis['een']}/people",
        json={"name": "Anna", "person_id": str(uuid.uuid4())},
        headers=eva,
    )
    assert r.status_code == 422
    assert await personen(client, eva) == {}, "een geweigerd verzoek maakt geen persoon"
    # Geen foto (een tekstbestand), de prullenbak, of iets wat niet bestaat: 404
    for node in (huis["tekst"], huis["weg"], uuid.uuid4()):
        r = await client.post(f"/api/v1/files/{node}/people", json={"name": "X"}, headers=eva)
        assert (r.status_code, code_of(r)) == (404, "not_found"), node
    # Hooguit 100 personen op één foto
    async with maker(settings)() as session:
        for i in range(P.MAX_ON_PHOTO):
            person = Person(owner_id=huis["eva"], name=f"P{i}", search_name=f"p{i}",
                            created_at=BASE, updated_at=BASE)  # fmt: skip
            session.add(person)
            await session.flush()
            session.add(rij(person.id, huis["een"], huis["eva"]))
        await session.commit()
    r = await client.post(f"/api/v1/files/{huis['een']}/people", json={"name": "Te"}, headers=eva)
    assert (r.status_code, params_of(r).get("max")) == (422, P.MAX_ON_PHOTO), r.text
    assert "Te" not in await personen(client, eva)


async def test_alleen_de_eigenaar(client, huis, settings):
    """Bram kan Eva's foto in Gedeeld lezen, maar ziet er niemand op en kan er niemand op
    zetten; Eva's personen bestaan voor hem niet (404), ook niet in zijn eigen lijst."""
    eva, bram = huis["h_eva"], huis["h_bram"]
    await op(client, eva, huis["samen"], name="Anna", box=[0.1, 0.1, 0.2, 0.2])
    anna = (await personen(client, eva))["Anna"]["id"]
    r = await client.get(f"/api/v1/files/{huis['samen']}", headers=bram)
    assert r.status_code == 200, "Bram mag de foto zelf zien"
    assert await op_foto(client, bram, huis["samen"]) == []
    assert await personen(client, bram) == {}
    assert await personen(client, bram, hidden="1") == {}
    gemist = [
        client.get(f"/api/v1/people/{anna}/photos", headers=bram),
        client.patch(f"/api/v1/people/{anna}", json={"name": "B"}, headers=bram),
        client.post(f"/api/v1/people/{anna}/merge", json={"into": anna}, headers=bram),
        client.delete(f"/api/v1/people/{anna}", headers=bram),
        client.post(f"/api/v1/files/{huis['samen']}/people", json={"name": "X"}, headers=bram),
        client.post(
            f"/api/v1/files/{huis['bram_foto']}/people", json={"person_id": anna}, headers=bram
        ),
        client.delete(f"/api/v1/files/{huis['samen']}/people/{anna}", headers=bram),
    ]
    for verzoek in gemist:
        r = await verzoek
        assert (r.status_code, code_of(r)) == (404, "not_found"), r.request.url
    # Bram zet zijn eigen persoon op zijn eigen foto, en voegt Eva's Anna niet samen in de zijne
    await op(client, bram, huis["bram_foto"], name="Anna")
    zijn = (await personen(client, bram))["Anna"]["id"]
    r = await client.post(f"/api/v1/people/{zijn}/merge", json={"into": anna}, headers=bram)
    assert (r.status_code, code_of(r)) == (404, "not_found")
    # Eva ziet Brams persoon niet, en niets op Brams foto
    assert set(await personen(client, eva)) == {"Anna"}
    assert await op_foto(client, eva, huis["bram_foto"]) == []
    r = await client.patch(f"/api/v1/people/{zijn}", json={"hidden": True}, headers=eva)
    assert (r.status_code, code_of(r)) == (404, "not_found")
    # Niets van Eva veranderd
    assert (await personen(client, eva))["Anna"]["count"] == 1
    assert await rijen(settings, owner_id=huis["eva"]) == 1


async def test_een_gast_mag_niets_zetten(client, admin, huis, settings):
    async with maker(settings)() as session:
        await create_user(session, "gast@example.nl", role="guest")
        await session.commit()
    gast = await login(client, "gast@example.nl")
    assert await personen(client, gast) == {}
    r = await client.post(f"/api/v1/files/{huis['een']}/people", json={"name": "X"}, headers=gast)
    assert r.status_code == 403, r.text


async def test_samenvoegen(client, huis, settings):
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna")
    await op(client, eva, huis["een"], name="Anneke", box=[0.1, 0.1, 0.2, 0.2])
    p = await personen(client, eva)
    anna, anneke = p["Anna"]["id"], p["Anneke"]["id"]
    await op(client, eva, huis["twee"], person_id=anneke)
    await op(client, eva, huis["samen"], person_id=anneke)
    await op(client, eva, huis["samen"], person_id=anna, box=[0.5, 0.5, 0.1, 0.1])
    # Ook op een foto in de prullenbak: die gaat mee, zodat terugzetten hem terugbrengt
    async with maker(settings)() as session:
        session.add(rij(uuid.UUID(anneke), huis["weg"], huis["eva"]))
        await session.commit()
    r = await client.post(f"/api/v1/people/{anna}/merge", json={"into": anna}, headers=eva)
    assert (r.status_code, code_of(r)) == (400, "people_merge_self")
    r = await client.post(f"/api/v1/people/{anneke}/merge", json={"into": anna}, headers=eva)
    assert r.status_code == 200, r.text
    assert (r.json()["id"], r.json()["name"], r.json()["count"]) == (anna, "Anna", 3)
    assert set(await personen(client, eva)) == {"Anna"}
    r = await client.get(f"/api/v1/people/{anneke}/photos", headers=eva)
    assert (r.status_code, code_of(r)) == (404, "not_found")
    # Op een foto met beiden: één rij; Anna had geen kader en krijgt dat van Anneke, en waar
    # Anna al een kader had, blijft het hare
    assert [p["box"] for p in await op_foto(client, eva, huis["een"])] == [[0.1, 0.1, 0.2, 0.2]]
    assert [p["box"] for p in await op_foto(client, eva, huis["samen"])] == [[0.5, 0.5, 0.1, 0.1]]
    assert await rijen(settings, person_id=uuid.UUID(anneke)) == 0
    assert await rijen(settings, person_id=uuid.UUID(anna)) == 4
    r = await client.post(f"/api/v1/files/{huis['weg']}/restore", headers=eva)
    assert r.status_code == 200, r.text
    assert (await personen(client, eva))["Anna"]["count"] == 4


async def test_wissen(client, huis, settings):
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna")
    await op(client, eva, huis["een"], name="Bert")
    anna = (await personen(client, eva))["Anna"]["id"]
    r = await client.delete(f"/api/v1/people/{anna}", headers=eva)
    assert r.status_code == 204, r.text
    assert set(await personen(client, eva)) == {"Bert"}
    assert [p["person"]["name"] for p in await op_foto(client, eva, huis["een"])] == ["Bert"]
    assert await rijen(settings, person_id=uuid.UUID(anna)) == 0
    # De foto zelf blijft
    assert (await client.get(f"/api/v1/files/{huis['een']}", headers=eva)).status_code == 200
    r = await client.delete(f"/api/v1/people/{anna}", headers=eva)
    assert (r.status_code, code_of(r)) == (404, "not_found")


async def test_de_foto_s_van_een_persoon(client, huis, monkeypatch):
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna")
    anna = (await personen(client, eva))["Anna"]["id"]
    for node in ("twee", "samen"):
        await op(client, eva, huis[node], person_id=anna)
    r = await client.get(f"/api/v1/people/{anna}/photos", headers=eva)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["id"] for i in body["items"]] == [str(huis[k]) for k in ("samen", "twee", "een")]
    assert body["cursor"] is None
    # In pagina's: de cursor is de volgende
    from app.routers import people as router

    monkeypatch.setattr(router, "PAGE", 2)
    r = await client.get(f"/api/v1/people/{anna}/photos", headers=eva)
    first = r.json()
    assert [i["id"] for i in first["items"]] == [str(huis["samen"]), str(huis["twee"])]
    assert first["cursor"]
    r = await client.get(
        f"/api/v1/people/{anna}/photos", params={"cursor": first["cursor"]}, headers=eva
    )
    assert [i["id"] for i in r.json()["items"]] == [str(huis["een"])]
    assert r.json()["cursor"] is None
    r = await client.get(f"/api/v1/people/{anna}/photos", params={"cursor": "!!"}, headers=eva)
    assert r.status_code == 422


async def test_een_kopie_van_dezelfde_eigenaar_heeft_de_persoon_ook(client, huis, settings):
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna", box=[0.1, 0.1, 0.2, 0.2])
    await op(client, eva, huis["samen"], name="Bert")
    anna = (await personen(client, eva))["Anna"]["id"]
    # Een los bestand
    r = await client.post(
        f"/api/v1/files/{huis['een']}/copy", json={"parent_id": str(huis["gedeeld"])}, headers=eva
    )
    assert r.status_code == 201, r.text
    kopie = r.json()["id"]
    assert await op_foto(client, eva, kopie) == [
        {"person": {"id": anna, "name": "Anna"}, "box": [0.1, 0.1, 0.2, 0.2], "source": "hand"}
    ]
    assert (await personen(client, eva))["Anna"]["count"] == 2
    # Een map met alles erin
    r = await client.post(
        f"/api/v1/files/{huis['gedeeld']}/copy",
        json={"parent_id": str(huis["eva_root"]), "name": "Gedeeld 2"},
        headers=eva,
    )
    assert r.status_code == 201, r.text
    p = await personen(client, eva)
    assert (p["Anna"]["count"], p["Bert"]["count"]) == (3, 2)
    # Bram ziet ook op Eva's kopie in Gedeeld niemand
    assert await op_foto(client, huis["h_bram"], kopie) == []


async def test_een_kopie_van_bram_heeft_de_persoon_niet(client, huis, settings):
    eva, bram = huis["h_eva"], huis["h_bram"]
    await op(client, eva, huis["samen"], name="Anna")
    r = await client.post(
        f"/api/v1/files/{huis['samen']}/copy",
        json={"parent_id": str(huis["bram_map"])},
        headers=bram,
    )
    assert r.status_code == 201, r.text
    kopie = uuid.UUID(r.json()["id"])
    assert await op_foto(client, bram, kopie) == []
    assert await op_foto(client, eva, kopie) == []
    assert await rijen(settings, node_id=kopie) == 0
    # Met de batchroute ook niet
    r = await client.post(
        "/api/v1/batch/copy",
        json={"ids": [str(huis["samen"])], "parent_id": str(huis["bram_root"])},
        headers=bram,
    )
    assert r.status_code in (200, 201), r.text
    assert await rijen(settings, owner_id=huis["bram"]) == 0
    assert (await personen(client, eva))["Anna"]["count"] == 1


async def test_een_kopie_met_de_batchroute(client, huis, settings):
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna")
    r = await client.post(
        "/api/v1/batch/copy",
        json={"ids": [str(huis["een"]), str(huis["twee"])], "parent_id": str(huis["gedeeld"])},
        headers=eva,
    )
    assert r.status_code in (200, 201), r.text
    assert (await personen(client, eva))["Anna"]["count"] == 2
    assert await rijen(settings, owner_id=huis["eva"]) == 2


async def test_elke_wijziging_meldt_people_changed(client, huis, monkeypatch):
    gemeld: list[tuple] = []
    monkeypatch.setattr(P.events, "publish", lambda ids, *a, **k: gemeld.append(tuple(ids)))
    eva = huis["h_eva"]
    anna = await nieuw(client, eva, "Anna")
    await op(client, eva, huis["een"], person_id=anna)
    await client.patch(f"/api/v1/people/{anna}", json={"hidden": True}, headers=eva)
    await client.delete(f"/api/v1/files/{huis['een']}/people/{anna}", headers=eva)
    bert = await nieuw(client, eva, "Bert")
    await client.post(f"/api/v1/people/{bert}/merge", json={"into": anna}, headers=eva)
    await client.delete(f"/api/v1/people/{anna}", headers=eva)
    assert gemeld == [(huis["eva"],)] * 7


# --- Zoeken: het filter person (beide motoren), de groep en de suggesties ---


@pytest.fixture(params=["fts", "like"])
def motor(request, settings, monkeypatch) -> str:
    if request.param == "like":
        monkeypatch.setattr(settings, "search_index", "uit")
    return request.param


async def zoek(client, headers, motor, **params) -> dict:
    r = await client.get("/api/v1/search", params=params, headers=headers)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["engine"] == motor
    return page


def namen(page: dict) -> set[str]:
    return {h["node"]["name"] for h in page["results"]}


async def test_het_filter_person_in_beide_motoren(client, huis, settings, motor):
    eva, bram = huis["h_eva"], huis["h_bram"]
    await op(client, eva, huis["een"], name="Anna")
    await op(client, eva, huis["samen"], name="Bert")
    p = await personen(client, eva)
    anna, bert = p["Anna"]["id"], p["Bert"]["id"]
    for node in ("twee", "samen"):
        await op(client, eva, huis[node], person_id=anna)
    # Anna ook op de foto in de prullenbak (zoals vóór het weggooien): die vindt het filter niet
    async with maker(settings)() as session:
        session.add(rij(uuid.UUID(anna), huis["weg"], huis["eva"]))
        await session.commit()
    # Zonder q, met q, en twee personen (EN)
    assert namen(await zoek(client, eva, motor, person=anna)) == {
        "strand-1.jpg", "strand-2.jpg", "samen.jpg"}  # fmt: skip
    assert namen(await zoek(client, eva, motor, q="strand", person=anna)) == {
        "strand-1.jpg", "strand-2.jpg"}  # fmt: skip
    assert namen(await zoek(client, eva, motor, person=[anna, bert])) == {"samen.jpg"}
    assert namen(await zoek(client, eva, motor, person=[anna, anna])) == {
        "strand-1.jpg", "strand-2.jpg", "samen.jpg"}  # fmt: skip
    # Samen met een ander filter
    assert namen(await zoek(client, eva, motor, person=anna, **{"in": str(huis["gedeeld"])})) == {
        "samen.jpg"}  # fmt: skip
    # De persoon van een ander, of een die niet bestaat: niets
    assert namen(await zoek(client, bram, motor, person=anna)) == set()
    assert namen(await zoek(client, bram, motor, q="samen", person=anna)) == set()
    assert namen(await zoek(client, eva, motor, person=str(uuid.uuid4()))) == set()
    # Bram vindt de gedeelde foto wel zonder filter
    assert namen(await zoek(client, bram, motor, q="samen")) == {"samen.jpg"}


async def test_de_groep_personen_bij_zoeken(client, huis, motor, settings):
    eva, bram = huis["h_eva"], huis["h_bram"]
    await op(client, eva, huis["een"], name="Anna")
    await op(client, eva, huis["twee"], name="Annemiek")
    await op(client, eva, huis["samen"], name="Bert")
    p = await personen(client, eva)
    await op(client, eva, huis["twee"], person_id=p["Anna"]["id"])
    # Anna ook op de foto in de prullenbak (zoals vóór het weggooien): die telt niet, net als in
    # GET /people (review golf 2, contract C2)
    async with maker(settings)() as session:
        session.add(rij(uuid.UUID(p["Anna"]["id"]), huis["weg"], huis["eva"]))
        await session.commit()
    assert (await personen(client, eva))["Anna"]["count"] == 2
    verborgen = await nieuw(client, eva, "Anouk")
    await client.patch(f"/api/v1/people/{verborgen}", json={"hidden": True}, headers=eva)
    page = await zoek(client, eva, motor, q="ann")
    assert page["groups"] == {
        "people": [
            {"id": p["Anna"]["id"], "name": "Anna", "n": 2},
            {"id": p["Annemiek"]["id"], "name": "Annemiek", "n": 1},
        ],
        "places": [],
    }
    # Gevouwen: hoofdletters en accenten tellen niet
    page = await zoek(client, eva, motor, q="ÄNNEM")
    assert [g["name"] for g in page["groups"]["people"]] == ["Annemiek"]
    # Alleen op de eerste pagina en met een vraag; voor Bram geen groep
    assert (await zoek(client, eva, motor, q="ann", offset=30))["groups"] is None
    assert (await zoek(client, eva, motor, type="image"))["groups"] is None
    assert (await zoek(client, eva, motor, q="xyz"))["groups"] is None
    assert (await zoek(client, bram, motor, q="ann"))["groups"] is None
    # Hooguit 5
    for i in range(6):
        await nieuw(client, eva, f"Annet {i}")
    assert len((await zoek(client, eva, motor, q="ann"))["groups"]["people"]) == 5


async def test_de_suggesties_voor_personen(client, huis):
    eva, bram = huis["h_eva"], huis["h_bram"]
    await op(client, eva, huis["een"], name="Anna")
    await nieuw(client, eva, "Bert")
    verborgen = await nieuw(client, eva, "Annabel")
    await client.patch(f"/api/v1/people/{verborgen}", json={"hidden": True}, headers=eva)
    anna = (await personen(client, eva))["Anna"]["id"]

    async def suggesties(headers, q):
        r = await client.get("/api/v1/search/suggest", params={"q": q}, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()["people"]

    assert await suggesties(eva, "an") == [{"id": anna, "name": "Anna"}]
    assert await suggesties(eva, "AN") == [{"id": anna, "name": "Anna"}]
    assert await suggesties(eva, "nn") == []
    assert await suggesties(bram, "an") == []
