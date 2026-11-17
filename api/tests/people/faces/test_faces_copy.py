"""Een kopie van een foto (fase 6, X2 "Levensloop"; X2b).

- dezelfde eigenaar, de bron klaar op deze versie met dezelfde inhoud: de gezichten (opnieuw
  versleuteld met de id van de kopie), de keuzes en face_scan gaan mee, zonder scan;
- dezelfde eigenaar, de bron nog niet klaar: de kopie wacht (wait) en wordt zelf gescand;
- een kopie door Bram krijgt niets van Eva mee; met gezichten aan krijgt Bram een eigen scan, met
  zijn eigen gezichten en zonder de keuzes van Eva.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.analyse import wire
from app.models import Blob, Face, FacePerson, FaceScan, Job, Node, Share
from app.people.faces import crypto, scan
from tests.factories import create_folder, create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"


@pytest.fixture
async def eva(client, settings, nep):
    """Eva (aan): een.jpg gescand met Oma bevestigd op het eerste gezicht; wacht.jpg zonder 1024
    (wacht op de preview); een map Gedeeld met samen.jpg, leesbaar voor Bram."""
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        gedeeld = await create_folder(session, user, name="Gedeeld")
        session.add(
            Share(node_id=gedeeld.id, owner_id=user.id, grantee_id=bram.id, permission="read")
        )
        await session.commit()
    ids = {"id": user.id, "root": user.root_id, "bram": bram.id, "bram_root": bram.root_id}
    ids["een"] = await foto(settings, nep, user.id, "een.jpg", [(A, 1), (B, 2)])
    ids["wacht"] = await foto(
        settings, nep, user.id, "wacht.jpg", [(A, 1)], large=False, sizes=(256,)
    )
    async with maker(settings)() as session:
        node = await session.get(Node, ids["wacht"])
        blob = await session.get(Blob, node.blob_id)
        blob.preview_meta = {"sizes": [256], "partial": True}  # de 1024 komt nog
        await session.commit()
    ids["samen"] = await foto(settings, nep, user.id, "samen.jpg", [(A, 1)], parent=gedeeld.id)
    await aan(settings, user.id)
    await draai(settings)
    ids["h"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    first = (await rows(settings, ids["een"]))[0]
    r = await client.post(
        f"{F}/confirm", json={"faces": [str(first.id)], "name": "Oma"}, headers=ids["h"]
    )
    assert r.status_code == 204, r.text
    return ids


async def rows(settings, node) -> list[Face]:
    async with maker(settings)() as session:
        return list(
            await session.scalars(select(Face).where(Face.node_id == node).order_by(Face.x))
        )


async def copy(client, node, parent, headers) -> uuid.UUID:
    r = await client.post(
        f"/api/v1/files/{node}/copy", json={"parent_id": str(parent)}, headers=headers
    )
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


async def scan_jobs(settings) -> int:
    async with maker(settings)() as session:
        return await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.type == scan.SCAN, Job.status == "queued")
        )


async def test_een_kopie_van_eva_neemt_alles_mee(client, settings, eva):
    bron = await rows(settings, eva["een"])
    nieuw = await copy(client, eva["een"], eva["root"], eva["h"])
    kopie = await rows(settings, nieuw)
    assert [(f.x, f.y, f.w, f.h) for f in kopie] == [(f.x, f.y, f.w, f.h) for f in bron]
    assert not {f.id for f in kopie} & {f.id for f in bron}
    key = crypto.load_key(settings, eva["id"])
    for old, new in zip(bron, kopie, strict=True):
        # Opnieuw verzegeld met de id van de kopie: een andere vec, dezelfde vector
        assert new.vec != old.vec
        assert crypto.open_vec(key, new.id, new.vec) == crypto.open_vec(key, old.id, old.vec)
        assert crypto.open_vec(key, old.id, new.vec) is None
        assert (new.owner_id, new.hidden, new.cluster) == (eva["id"], 0, None)
    async with maker(settings)() as session:
        row = await session.get(FaceScan, nieuw)
        verdicts = list(
            await session.execute(
                select(FacePerson.face_id, FacePerson.verdict).where(
                    FacePerson.face_id.in_([f.id for f in kopie])
                )
            )
        )
    assert (row.state, row.version, row.faces) == ("done", 1, 2)
    assert verdicts == [(kopie[0].id, "ja")]
    # Geen scan nodig; de route laat Oma op de kopie zien
    assert await scan_jobs(settings) == 0
    r = await client.get(f"/api/v1/files/{nieuw}/faces", headers=eva["h"])
    people = sorted((f["person"] or {}).get("name", "") for f in r.json())
    assert people == ["", "Oma"]


async def test_een_kopie_van_een_foto_die_wacht(client, settings, nep, eva):
    async with maker(settings)() as session:
        assert (await session.get(FaceScan, eva["wacht"])).state == "wait"
    nieuw = await copy(client, eva["wacht"], eva["root"], eva["h"])
    async with maker(settings)() as session:
        row = await session.get(FaceScan, nieuw)
    assert (row.state, row.version) == ("wait", 1)
    assert await rows(settings, nieuw) == []


async def test_bram_krijgt_niets_van_eva(client, settings, nep, eva):
    # Bram heeft gezichten uit: niets
    uit = await copy(client, eva["samen"], eva["bram_root"], eva["h_bram"])
    await draai(settings)
    async with maker(settings)() as session:
        assert await session.get(FaceScan, uit) is None
    assert await rows(settings, uit) == []
    # Met gezichten aan: een eigen scan, met zijn eigen sleutel en zonder de keuzes van Eva
    await aan(settings, eva["bram"])
    await draai(settings)
    aan_kopie = await copy(client, eva["samen"], eva["bram_root"], eva["h_bram"])
    async with maker(settings)() as session:
        assert (await session.get(FaceScan, aan_kopie)).state == "wait"
    await draai(settings)
    kopie = await rows(settings, aan_kopie)
    bron = await rows(settings, eva["samen"])
    assert len(kopie) == len(bron) == 1
    assert kopie[0].owner_id == eva["bram"] and kopie[0].id != bron[0].id
    bram_key = crypto.load_key(settings, eva["bram"])
    eva_key = crypto.load_key(settings, eva["id"])
    assert bram_key != eva_key
    assert len(crypto.open_vec(bram_key, kopie[0].id, kopie[0].vec)) == wire.FACE_VEC_BYTES
    assert crypto.open_vec(eva_key, kopie[0].id, kopie[0].vec) is None
    async with maker(settings)() as session:
        n = await session.scalar(
            select(func.count()).select_from(FacePerson).where(FacePerson.face_id == kopie[0].id)
        )
    assert n == 0
    r = await client.get(f"/api/v1/files/{aan_kopie}/faces", headers=eva["h_bram"])
    assert [f["person"] for f in r.json()] == [None]
    # Eva ziet Bram's kopie niet
    r = await client.get(f"/api/v1/files/{aan_kopie}/faces", headers=eva["h"])
    assert r.status_code == 404
