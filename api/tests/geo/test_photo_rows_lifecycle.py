"""De rijen van photo_geo door het leven van een bestand (bouwontwerp fase 6, §3.9, Keuze 25a),
met echte uploads en de echte jobs: prullenbak, terugzetten, definitief wissen, een kopie door
dezelfde eigenaar, een kopie door Bram, nieuwe inhoud, een versie terug en een andere eigenaar.
Locatie "uit" en weer "mij": tests/geo/test_location_off.py."""

import uuid

import pytest
from sqlalchemy import update

from app import jobs, photometa
from app.models import Node
from tests.factories import maker
from tests.geo.hulp import (
    AMSTERDAM,
    kaart_ids,
    map_maken,
    met_gps,
    plek,
    rijen,
    uid,
    verwerk,
    zonder_gps,
)
from tests.test_previews import upload
from tests.test_shares import person, share_with

pytestmark = pytest.mark.anyio


async def test_prullenbak_terug_en_definitief_wissen(client, admin, settings, plaatsen):
    h, tokens = admin
    eva = uid(tokens)
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    reis = await map_maken(client, h, "Reis")
    in_map = (await upload(client, h, "b.jpg", met_gps("green"), parent=reis))["id"]
    await verwerk(settings)
    assert await rijen(settings) == {
        foto: (eva, 0, AMSTERDAM, "exif"),
        in_map: (eva, 0, AMSTERDAM, "exif"),
    }
    assert await kaart_ids(client, h) == {foto, in_map}

    # Naar de prullenbak: de rij blijft (dead volgt in dezelfde transactie), maar is weg uit
    # de kaart en /location
    assert (await client.delete(f"/api/v1/files/{foto}", headers=h)).status_code == 204
    assert (await client.delete(f"/api/v1/files/{reis}", headers=h)).status_code == 204
    assert await rijen(settings) == {
        foto: (eva, 1, AMSTERDAM, "exif"),
        in_map: (eva, 1, AMSTERDAM, "exif"),
    }
    assert await kaart_ids(client, h) == set()
    assert await plek(client, h, foto) == 404
    r = await client.get("/api/v1/photos/places", headers=h)
    assert r.json() == {"places": []}

    # Terug: dezelfde rij, weer levend (ook wat in de map zat)
    for node in (foto, reis):
        r = await client.post(f"/api/v1/files/{node}/restore", headers=h)
        assert r.status_code == 200, r.text
    assert await rijen(settings) == {
        foto: (eva, 0, AMSTERDAM, "exif"),
        in_map: (eva, 0, AMSTERDAM, "exif"),
    }
    assert await kaart_ids(client, h) == {foto, in_map}
    assert await plek(client, h, foto) == 200

    # Definitief wissen: de rij gaat mee (cascade)
    assert (await client.delete(f"/api/v1/files/{reis}", headers=h)).status_code == 204
    assert (await client.delete(f"/api/v1/trash/{reis}", headers=h)).status_code == 204
    assert await rijen(settings) == {foto: (eva, 0, AMSTERDAM, "exif")}


async def test_kopie_door_dezelfde_eigenaar_wordt_opnieuw_gelezen(
    client, admin, settings, plaatsen
):
    h, tokens = admin
    eva = uid(tokens)
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    reis = await map_maken(client, h, "Reis")
    r = await client.post(f"/api/v1/files/{foto}/copy", json={"parent_id": reis}, headers=h)
    assert r.status_code == 201, r.text
    kopie = r.json()["id"]
    # Niets gaat mee in de kopie zelf; de haak zette de keten in fase nodes klaar
    assert kopie not in await rijen(settings)
    await jobs.run_pending(settings)
    assert (await rijen(settings))[kopie] == (eva, 0, AMSTERDAM, "exif")


async def test_kopie_door_bram_krijgt_alleen_zijn_eigen_plek(client, admin, settings, plaatsen):
    h, tokens = admin
    eva = uid(tokens)
    reis = await map_maken(client, h, "Reis")
    foto = (await upload(client, h, "a.jpg", met_gps(), parent=reis))["id"]
    await verwerk(settings)
    bram_h, bram = await person(client, h, "bram@example.com")
    await share_with(client, h, reis, user_id=bram, permission="read")
    # Bram ziet Eva's foto, maar niet haar plek (en niet op zijn kaart)
    assert await plek(client, bram_h, foto) == 404
    assert await kaart_ids(client, bram_h) == set()

    r = await client.get("/api/v1/files/root", headers=bram_h)
    bram_root = r.json()["folder"]["id"]
    r = await client.post(
        f"/api/v1/files/{foto}/copy", json={"parent_id": bram_root}, headers=bram_h
    )
    assert r.status_code == 201, r.text
    kopie = r.json()["id"]
    assert kopie not in await rijen(settings)  # niets gaat mee (Keuze 25a)
    await jobs.run_pending(settings)
    # Brams eigen kopie, met zijn eigen "mij": zijn rij, alleen voor hem
    assert await rijen(settings) == {
        foto: (eva, 0, AMSTERDAM, "exif"),
        kopie: (str(uuid.UUID(bram)), 0, AMSTERDAM, "exif"),
    }
    assert await plek(client, bram_h, kopie) == 200
    assert await plek(client, h, kopie) == 404
    assert await kaart_ids(client, bram_h) == {kopie}
    assert await kaart_ids(client, h) == {foto}


async def test_nieuwe_inhoud_en_een_versie_terug(client, admin, settings, plaatsen):
    h, tokens = admin
    eva = uid(tokens)
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    assert foto in await rijen(settings)

    # Nieuwe inhoud zonder GPS: de rij van de oude inhoud meteen weg (node_bu_text), ook
    # voordat er iets gelezen is; en er komt er geen terug
    r = await client.put(f"/api/v1/files/{foto}/content", content=zonder_gps(), headers=h)
    assert r.status_code == 200, r.text
    assert await rijen(settings) == {}
    assert await plek(client, h, foto) == 404
    await verwerk(settings)
    assert await rijen(settings) == {}

    # Nieuwe inhoud met een plek: die wordt gelezen
    r = await client.put(f"/api/v1/files/{foto}/content", content=met_gps("green"), headers=h)
    assert r.status_code == 200, r.text
    await verwerk(settings)
    assert await rijen(settings) == {foto: (eva, 0, AMSTERDAM, "exif")}

    # De versie zonder plek terug: weer weg (zoals bij vervangen)
    versies = (await client.get(f"/api/v1/files/{foto}/versions", headers=h)).json()
    zonder = versies[0]["id"]  # de nieuwste vorige inhoud: die zonder GPS
    r = await client.post(f"/api/v1/files/{foto}/versions/{zonder}/restore", headers=h)
    assert r.status_code == 200, r.text
    assert await rijen(settings) == {}

    # De eerste inhoud terug: die heeft zijn blob_meta al, dus de rij komt bij de volgende
    # ronde van de keten in fase nodes (die volgt op elke upload, en bij het starten)
    versies = (await client.get(f"/api/v1/files/{foto}/versions", headers=h)).json()
    eerste = versies[-1]["id"]
    r = await client.post(f"/api/v1/files/{foto}/versions/{eerste}/restore", headers=h)
    assert r.status_code == 200, r.text
    assert await rijen(settings) == {}
    assert await photometa.enqueue_backfill(settings, photometa.NODES) == 1
    await jobs.run_pending(settings)
    assert await rijen(settings) == {foto: (eva, 0, AMSTERDAM, "exif")}


async def test_een_andere_eigenaar_wist_de_rij(client, admin, settings, plaatsen):
    """Geen route verandert de eigenaar van een bestand; de trigger node_au_owner bewaakt wat
    het ook doet: de rij van de oude eigenaar weg, en de nieuwe krijgt pas een plek als
    photometa voor hem leest, met zijn eigen schakelaar."""
    h, tokens = admin
    foto = (await upload(client, h, "a.jpg", met_gps()))["id"]
    await verwerk(settings)
    bram_h, bram = await person(client, h, "bram@example.com")
    async with maker(settings)() as db:
        await db.execute(
            update(Node).where(Node.id == uuid.UUID(foto)).values(owner_id=uuid.UUID(bram))
        )
        await db.commit()
    assert await rijen(settings) == {}
    assert await plek(client, h, foto) == 404
    assert await photometa.enqueue_backfill(settings, photometa.NODES) == 1
    await jobs.run_pending(settings)
    assert await rijen(settings) == {foto: (str(uuid.UUID(bram)), 0, AMSTERDAM, "exif")}
    assert await plek(client, bram_h, foto) == 200
    assert await plek(client, h, foto) == 404
