"""Wissen is crypto-shred, op SQLite en op PostgreSQL (0.8.2, P4; Keuze 64).

Op PostgreSQL bestaat er geen secure_delete: een oude rij kan nog even in een dode tuple, de WAL,
een replica of een reservekopie staan (test_faces_wipe_bytes.py kijkt naar de bytes en geldt
alleen op SQLite). Wat hier vastligt, geldt op beide: na "uit" is eerst de sleutel weg (het
bestand), dan staan er geen rijen meer van de eigenaar (face, face_scan, face_owner), en een oude
versleutelde vector gaat met geen enkele sleutel die er nog is open. De vectoren van een ander
(Bram) blijven open met zijn eigen sleutel.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models import Face, FaceOwner, FaceScan
from app.people.faces import crypto
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)


async def _user(settings, email: str) -> uuid.UUID:
    async with maker(settings)() as session:
        user = await create_user(session, email)
        await session.commit()
    return user.id


async def _sealed(settings, owner: uuid.UUID) -> list[tuple[uuid.UUID, bytes]]:
    async with maker(settings)() as db:
        rows = await db.execute(
            select(Face.id, Face.vec).where(Face.owner_id == owner, Face.vec.is_not(None))
        )
        return [(i, v) for i, v in rows.all()]


async def _rows(settings, owner: uuid.UUID) -> dict[str, int]:
    async with maker(settings)() as db:
        return {
            model.__tablename__: int(
                await db.scalar(
                    select(func.count()).select_from(model).where(model.owner_id == owner)
                )
            )
            for model in (Face, FaceScan, FaceOwner)
        }


async def test_uit_haalt_eerst_de_sleutel_weg_en_dan_de_rijen(client, settings, nep):
    eva = await _user(settings, "eva@example.nl")
    bram = await _user(settings, "bram@example.nl")
    for owner, naam in ((eva, "e"), (bram, "b")):
        for i in range(2):
            await foto(settings, nep, owner, f"{naam}{i}.jpg", [(A, 1), (B, 2)])
    await aan(settings, eva, bram)
    await draai(settings)
    oud = await _sealed(settings, eva)
    van_bram = await _sealed(settings, bram)
    assert len(oud) == 4 and len(van_bram) == 4
    eva_key = crypto.load_key(settings, eva)
    assert eva_key is not None
    assert all(crypto.open_vec(eva_key, i, v) is not None for i, v in oud)
    pad = crypto.key_path(settings, eva)

    h = await login(client, "eva@example.nl")
    r = await client.patch("/api/v1/me/photo-settings", json={"faces": "uit"}, headers=h)
    assert r.status_code == 200, r.text
    # De sleutel is weg vóór de wisronde loopt: vanaf nu is elke kopie onleesbaar
    assert not pad.exists()
    await draai(settings)
    assert not pad.exists()
    assert await _rows(settings, eva) == {"face": 0, "face_scan": 0, "face_owner": 0}

    # Geen sleutel die er nog is, opent een oude vector van Eva (ook niet die van Bram)
    sleutels = sorted(crypto.keys_dir(settings).glob("*.key"))
    assert sleutels, "de sleutel van Bram hoort er nog te zijn"
    for path in sleutels:
        key = path.read_bytes()
        assert all(crypto.open_vec(key, i, v) is None for i, v in oud), path.name
    # Bram is niet geraakt: zijn rijen en sleutel zijn er, en zijn vectoren gaan open
    assert await _sealed(settings, bram) == van_bram
    bram_key = crypto.load_key(settings, bram)
    assert all(crypto.open_vec(bram_key, i, v) is not None for i, v in van_bram)
