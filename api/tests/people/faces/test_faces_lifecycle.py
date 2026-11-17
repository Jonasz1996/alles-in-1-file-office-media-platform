"""De levensloop van gezichten (fase 6, X2 "Levensloop"; X2b).

- prullenbak: de rijen blijven, own_live laat ze weg uit de stand, voorstellen en groepen; terug
  uit de prullenbak: weer mee; definitief wissen: de cascade, ook face_scan;
- een andere eigenaar (node_au_owner_face): face en face_scan weg; de nieuwe eigenaar na zijn
  eigen scan;
- nieuwe inhoud (node_au_blob_face): de gezichten weg, het kader in photo_person leeg en de
  persoon blijft; na de preview een nieuwe scan (met een tag voor de persoon zonder kader);
- gezichten uit: eerst de sleutel, dan alle rijen van die eigenaar weg; personen en photo_person
  blijven, een ander houdt alles; twee keer uit is hetzelfde; een wisronde die niet liep, gaat
  bij de start (resume) alsnog.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, update

from app.models import Face, FaceOwner, FacePerson, FaceScan, Job, Node, Person, PhotoPerson
from app.people.faces import crypto, group, register, wipe
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"


@pytest.fixture
async def eva(client, settings, nep):
    """Eva (aan): persoon 1 op vier foto's, persoon 2 erbij op een.jpg; Bram (aan) met een eigen
    foto. Gescand en gegroepeerd (full)."""
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        await session.commit()
    ids = {"id": user.id, "bram": bram.id, "bram_root": bram.root_id}
    ids["een"] = await foto(settings, nep, user.id, "een.jpg", [(A, 1), (B, 2)])
    for name in ("twee", "drie", "vier"):
        ids[name] = await foto(settings, nep, user.id, f"{name}.jpg", [(A, 1)])
    ids["bram_foto"] = await foto(settings, nep, bram.id, "bram.jpg", [(A, 5)])
    await aan(settings, user.id, bram.id)
    await draai(settings)
    await group.run_group(settings, user.id, "full")
    ids["h"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    return ids


async def stand(client, h) -> tuple[int, int, list[int]]:
    """(foto's, gezichten, de grootte van elke groep)."""
    status = (await client.get(f"{F}/status", headers=h)).json()
    groups = (await client.get(f"{F}/groups", headers=h)).json()["groups"]
    return status["photos"]["total"], status["faces"]["found"], [g["count"] for g in groups]


async def count(settings, table, *where) -> int:
    async with maker(settings)() as session:
        return await session.scalar(select(func.count()).select_from(table).where(*where))


async def test_prullenbak_terug_en_definitief(client, settings, eva):
    h = eva["h"]
    assert await stand(client, h) == (4, 5, [4])
    r = await client.delete(f"/api/v1/files/{eva['twee']}", headers=h)
    assert r.status_code == 204, r.text
    # De rijen blijven; own_live laat ze weg
    assert await count(settings, Face, Face.node_id == eva["twee"]) == 1
    assert await stand(client, h) == (3, 4, [3])
    group_key = (await client.get(f"{F}/groups", headers=h)).json()["groups"][0]["key"]
    faces = (await client.get(f"{F}/groups/{group_key}", headers=h)).json()["faces"]
    assert str(eva["twee"]) not in {f["node_id"] for f in faces}
    # Terug: weer mee
    r = await client.post("/api/v1/trash/restore", json={"ids": [str(eva["twee"])]}, headers=h)
    assert r.status_code == 200, r.text
    assert await stand(client, h) == (4, 5, [4])
    # Definitief wissen: de cascade
    r = await client.delete(f"/api/v1/files/{eva['drie']}", headers=h)
    assert r.status_code == 204
    r = await client.delete(f"/api/v1/trash/{eva['drie']}", headers=h)
    assert r.status_code == 204, r.text
    assert await count(settings, Face, Face.node_id == eva["drie"]) == 0
    assert await count(settings, FaceScan, FaceScan.node_id == eva["drie"]) == 0
    assert (await stand(client, h))[:2] == (3, 4)


async def test_een_andere_eigenaar(client, settings, nep, eva):
    async with maker(settings)() as session:
        await session.execute(
            update(Node)
            .where(Node.id == eva["vier"])
            .values(owner_id=eva["bram"], parent_id=eva["bram_root"])
        )
        await session.commit()
    assert await count(settings, Face, Face.node_id == eva["vier"]) == 0
    assert await count(settings, FaceScan, FaceScan.node_id == eva["vier"]) == 0
    assert (await stand(client, eva["h"]))[:2] == (3, 4)
    # Bram (aan) na zijn eigen scan, met zijn eigen sleutel
    await aan(settings, eva["bram"])
    await draai(settings)
    async with maker(settings)() as session:
        rows = list(await session.scalars(select(Face).where(Face.node_id == eva["vier"])))
    assert [f.owner_id for f in rows] == [eva["bram"]]
    key = crypto.load_key(settings, eva["bram"])
    assert crypto.open_vec(key, rows[0].id, rows[0].vec) is not None


async def test_nieuwe_inhoud(client, settings, nep, eva):
    h = eva["h"]
    face = (await client.get(f"/api/v1/files/{eva['twee']}/faces", headers=h)).json()[0]
    r = await client.post(f"{F}/confirm", json={"faces": [face["id"]], "name": "Oma"}, headers=h)
    assert r.status_code == 204
    oma = uuid.UUID((await client.get("/api/v1/people", headers=h)).json()["people"][0]["id"])
    async with maker(settings)() as session:
        row = await session.get(PhotoPerson, (oma, eva["twee"]))
        assert (row.source, row.x is not None) == ("auto", True)
    # Andere inhoud (een nieuwe foto met persoon 7), zoals een upload over twee.jpg
    bron = await foto(settings, nep, eva["id"], "nieuw.jpg", [(B, 7)])
    async with maker(settings)() as session:
        sha = (await session.get(Node, bron)).blob_id
        await session.execute(update(Node).where(Node.id == eva["twee"]).values(blob_id=sha))
        await session.commit()
    assert await count(settings, Face, Face.node_id == eva["twee"]) == 0
    assert await count(settings, FaceScan, FaceScan.node_id == eva["twee"]) == 0
    async with maker(settings)() as session:
        row = await session.get(PhotoPerson, (oma, eva["twee"]), populate_existing=True)
        assert (row.source, row.x, row.y, row.w, row.h) == ("auto", None, None, None, None)
        assert await session.get(Person, oma) is not None
    # Na de preview: een nieuwe scan; Oma zonder kader en één gezicht: een tag, geen ja
    await register.after_large(maker(settings), sha)
    await draai(settings)
    r = await client.get(f"/api/v1/files/{eva['twee']}/faces", headers=h)
    found = r.json()
    assert len(found) == 1 and found[0]["person"] is None
    assert found[0]["suggest"] == {
        "person_id": str(oma),
        "name": "Oma",
        "sure": False,
        "why": "tag",
    }


async def test_uit_wist_alle_rijen_van_eva(client, settings, eva):
    h = eva["h"]
    face = (await client.get(f"/api/v1/files/{eva['een']}/faces", headers=h)).json()[0]
    r = await client.post(f"{F}/confirm", json={"faces": [face["id"]], "name": "Oma"}, headers=h)
    assert r.status_code == 204
    bram_rows = await count(settings, Face, Face.owner_id == eva["bram"])
    assert bram_rows == 1
    r = await client.patch("/api/v1/me/photo-settings", json={"faces": "uit"}, headers=h)
    assert r.status_code == 200, r.text
    # Meteen: de sleutel weg en "wissen"
    assert crypto.load_key(settings, eva["id"]) is None
    assert (await client.get(f"{F}/status", headers=h)).json()["wiping"] is True
    await draai(settings)
    for table in (Face, FaceScan, FaceOwner, FacePerson):
        assert await count(settings, table, table.owner_id == eva["id"]) == 0, table
    # Personen en photo_person blijven; Bram houdt alles
    assert await count(settings, Person, Person.owner_id == eva["id"]) == 1
    assert await count(settings, PhotoPerson, PhotoPerson.owner_id == eva["id"]) == 1
    assert await count(settings, Face, Face.owner_id == eva["bram"]) == bram_rows
    assert crypto.load_key(settings, eva["bram"]) is not None
    status = (await client.get(f"{F}/status", headers=h)).json()
    assert (status["setting"], status["wiping"], status["faces"]["found"]) == ("uit", False, 0)
    # Twee keer uit: hetzelfde, en geen nieuwe wisronde
    r = await client.patch("/api/v1/me/photo-settings", json={"faces": "uit"}, headers=h)
    assert r.status_code == 200
    assert await count(settings, Job, Job.type == wipe.WIPE, Job.status == "queued") == 0


async def test_een_wisronde_die_niet_liep_gaat_bij_de_start_verder(client, settings, eva):
    h = eva["h"]
    r = await client.patch("/api/v1/me/photo-settings", json={"faces": "uit"}, headers=h)
    assert r.status_code == 200
    # Een crash vóór de wisronde liep: de job is weg, de rijen staan er nog (wiping = 1)
    async with maker(settings)() as session:
        await session.execute(Job.__table__.delete().where(Job.type == wipe.WIPE))
        await session.commit()
        assert (await session.get(FaceOwner, eva["id"])).wiping == 1
    assert await count(settings, Face, Face.owner_id == eva["id"]) == 5
    await register.on_start(settings)
    await draai(settings)
    assert await count(settings, Face, Face.owner_id == eva["id"]) == 0
    assert await count(settings, FaceOwner, FaceOwner.owner_id == eva["id"]) == 0
    assert await count(settings, Face, Face.owner_id == eva["bram"]) == 1
    # Nog een start: niets meer te doen
    assert await wipe.resume(settings) == 0
