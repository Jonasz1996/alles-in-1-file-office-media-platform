"""GET /photos/share-check?node= (bouwontwerp fase 6, Keuze 30; Z3): vóór het delen, hoeveel foto's
en video's in een map, een album of een los bestand een plek hebben, en van hoeveel dat nog niet
bekend is.

- De telling kijkt naar blob_meta.gps: wat er in het bestand zelf staat. Dat gaat mee als je het
  bestand deelt, ook als de eigenaar locatie op "uit" heeft (dan is photo_geo leeg, de EXIF niet).
- Een map telt alles eronder (ook in submappen) en niets in de prullenbak. Wat een ander met
  schrijfrecht in je map zet, is van jou (de eigenaar van de map) en telt dus mee.
- Een album telt alleen wat er gedeeld wordt (album_item.shared = 1); een publieke link verandert
  daar niets aan.
- unknown: nog niet gelezen (photometa komt nog), of niet te lezen: een video zonder media.
- Alleen de eigenaar: van een ander 404 (ook als hij het mag zien), een gast 403.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import update

from app.geo import wire
from app.models import AlbumItem
from tests.factories import maker
from tests.geo.hulp import map_maken, met_gps, verwerk, zonder_gps
from tests.test_albums import add, new_album
from tests.test_previews import upload
from tests.test_public import new_link
from tests.test_shares import person, share_with
from tests.test_trash import trash_it

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

# Een MP4-kop (ftyp): de upload herkent hem als video/mp4; zonder media wordt hij niet gelezen
VIDEO = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _aangemeld():
    wire.register()


async def check(client, headers, node_id: str, expect: int = 200) -> dict:
    r = await client.get("/api/v1/photos/share-check", params={"node": node_id}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def telling(with_location: int, unknown: int) -> dict:
    return {"with_location": with_location, "unknown": unknown}


async def test_map_los_bestand_en_video(client, admin, settings):
    eva, _ = admin
    reis = await map_maken(client, eva, "Reis")
    dag2 = await map_maken(client, eva, "Dag 2", parent=reis)
    a = await upload(client, eva, "a.jpg", met_gps("red"), parent=reis)
    b = await upload(client, eva, "b.jpg", zonder_gps("blue"), parent=reis)
    await upload(client, eva, "c.jpg", met_gps("green"), parent=dag2)
    film = await upload(client, eva, "film.mp4", VIDEO, parent=reis)
    assert film["mime"] == "video/mp4", film
    await upload(client, eva, "notitie.txt", b"geen foto", parent=reis)
    weg = await upload(client, eva, "weg.jpg", met_gps("white"), parent=reis)
    elders = await upload(client, eva, "elders.jpg", met_gps("yellow"))
    # Nog niets gelezen: alles is onbekend (de tekst telt niet)
    assert await check(client, eva, reis) == telling(0, 5)
    await verwerk(settings)
    await trash_it(client, eva, weg["id"])
    # De map: a en c (ook in de submap) met een plek; de video zonder media onbekend
    assert await check(client, eva, reis) == telling(2, 1)
    assert await check(client, eva, dag2) == telling(1, 0)
    # Los bestand
    assert await check(client, eva, a["id"]) == telling(1, 0)
    assert await check(client, eva, b["id"]) == telling(0, 0)
    assert await check(client, eva, film["id"]) == telling(0, 1)
    assert await check(client, eva, elders["id"]) == telling(1, 0)
    # Een nieuwe foto is onbekend tot photometa hem gelezen heeft
    await upload(client, eva, "nieuw.jpg", met_gps("black"), parent=reis)
    assert await check(client, eva, reis) == telling(2, 2)
    await verwerk(settings)
    assert await check(client, eva, reis) == telling(3, 1)
    # In de prullenbak: 404
    await check(client, eva, weg["id"], 404)


async def test_eigenaar_met_locatie_uit(client, admin, settings):
    """Met locatie "uit" heeft de eigenaar geen plekken in de app (photo_geo leeg), maar de GPS
    staat nog in het bestand: wie het krijgt, kan hem lezen. De waarschuwing telt dus wel."""
    eva, _ = admin
    reis = await map_maken(client, eva, "Reis")
    a = await upload(client, eva, "a.jpg", met_gps("red"), parent=reis)
    await upload(client, eva, "b.jpg", zonder_gps("blue"), parent=reis)
    r = await client.patch("/api/v1/me/photo-settings", json={"location": "uit"}, headers=eva)
    assert r.status_code == 200, r.text
    await verwerk(settings)
    # Geen plek in de app
    r = await client.get(f"/api/v1/files/{a['id']}/location", headers=eva)
    assert r.status_code == 404, r.text
    assert await check(client, eva, reis) == telling(1, 0)
    assert await check(client, eva, a["id"]) == telling(1, 0)


async def test_album_met_publieke_link(client, admin, settings):
    eva, _ = admin
    a = await upload(client, eva, "a.jpg", met_gps("red"))
    b = await upload(client, eva, "b.jpg", met_gps("green"))
    c = await upload(client, eva, "c.jpg", zonder_gps("blue"))
    film = await upload(client, eva, "film.mp4", VIDEO)
    await verwerk(settings)
    album = (await new_album(client, eva, "Zomer"))["id"]
    await add(client, eva, album, [a["id"], b["id"], c["id"], film["id"]])
    assert await check(client, eva, album) == telling(2, 1)
    await new_link(client, eva, album, permission="read")
    assert await check(client, eva, album) == telling(2, 1)
    # Een item dat niet gedeeld wordt (een regel-item zonder share_new, Keuze 42): telt niet mee
    async with maker(settings)() as db:
        await db.execute(
            update(AlbumItem)
            .where(AlbumItem.album_id == uuid.UUID(album), AlbumItem.node_id == uuid.UUID(b["id"]))
            .values(shared=0)
        )  # fmt: skip
        await db.commit()
    assert await check(client, eva, album) == telling(1, 1)
    # Een foto in de prullenbak telt niet mee
    await trash_it(client, eva, a["id"])
    assert await check(client, eva, album) == telling(0, 1)


async def test_alleen_de_eigenaar(client, admin, settings):
    eva, _ = admin
    reis = await map_maken(client, eva, "Reis")
    a = await upload(client, eva, "a.jpg", met_gps("red"), parent=reis)
    bram, bram_id = await person(client, eva, "bram@example.com")
    await share_with(client, eva, reis, user_id=bram_id, permission="write")
    # Wat Bram in Eva's map zet, is van Eva (de eigenaar van de map): het telt mee
    van_bram = await upload(client, bram, "bram.jpg", met_gps("green"), parent=reis)
    assert van_bram["permission"] == "write" and van_bram["owner"]["display_name"] == "Jonas"
    await verwerk(settings)
    assert await check(client, eva, reis) == telling(2, 0)
    # Bram ziet de map, maar is niet de eigenaar: 404, net als iets wat niet bestaat
    for node in (reis, a["id"], "00000000-0000-0000-0000-000000000000"):
        await check(client, bram, node, 404)
    gast, _ = await person(client, eva, "gast@example.com", role="guest")
    r = await client.get("/api/v1/photos/share-check", params={"node": reis}, headers=gast)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "guest", r.text
    r = await client.get("/api/v1/photos/share-check", params={"node": "geen-id"}, headers=eva)
    assert r.status_code == 422, r.text
