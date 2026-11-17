"""De versie van gezichten (fase 6, X2 "Een nieuwe versie"; X2b).

- LOCK_SHA is de sha256 over de regels van YuNet en SFace in models.lock: verandert een van die
  regels zonder een hogere FACES_VERSION, dan faalt deze test; een regel van X3 telt niet mee;
- een nieuwe FACES_VERSION (bij de start): de open gezichten weg, alles opnieuw zoeken, ja komt
  terug via de kaders in photo_person, verborgen blijft verborgen, en afwijzingen zijn weg.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.analyse import lock
from app.models import Face, FacePerson, FaceScan, PhotoPerson
from app.people.faces import register, scan, version
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"


def test_lock_sha_volgt_models_lock(tmp_path):
    assert version.lock_sha() == version.LOCK_SHA
    names = {f.name for f in lock.load()}
    assert set(version.FACES_MODELS) <= names
    # Een andere regel van YuNet: een andere sha (dus FACES_VERSION omhoog)
    text = lock.LOCK_PATH.read_text(encoding="utf-8")
    yunet = next(f for f in lock.load() if f.name == version.FACES_MODELS[0])
    anders = tmp_path / "models.lock"
    anders.write_text(text.replace(yunet.sha256, "0" * 64), encoding="utf-8")
    assert version.lock_sha(anders) != version.LOCK_SHA
    # Een regel van een ander model (zoals X3 die erbij zet) telt niet mee; een test-sha, geen
    # echt model
    extra = f"{'1' * 64} 1 beeld/test.onnx -\n"
    anders.write_text(text + extra, encoding="utf-8")
    assert [f.name for f in lock.load(anders)][-1] == "beeld/test.onnx"
    assert version.lock_sha(anders) == version.LOCK_SHA


async def faces_of(settings, node) -> list[Face]:
    async with maker(settings)() as session:
        return list(
            await session.scalars(select(Face).where(Face.node_id == node).order_by(Face.x))
        )


async def verdicts_of(settings, face_id) -> list[tuple[uuid.UUID, str]]:
    async with maker(settings)() as session:
        return list(
            await session.execute(
                select(FacePerson.person_id, FacePerson.verdict).where(
                    FacePerson.face_id == face_id
                )
            )
        )


async def test_een_nieuwe_versie(client, settings, nep, monkeypatch):
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    eva = user.id
    een = await foto(settings, nep, eva, "een.jpg", [(A, 1), (B, 2)])
    twee = await foto(settings, nep, eva, "twee.jpg", [(A, 1), (B, 2)])
    drie = await foto(settings, nep, eva, "drie.jpg", [(A, 1)])
    await aan(settings, eva)
    await draai(settings)
    h = await login(client, "eva@example.nl")
    # Oma op een (ja, met een kader in photo_person), B op twee niet tonen, A op drie nee
    oma_face = (await faces_of(settings, een))[0]
    r = await client.post(
        f"{F}/confirm", json={"faces": [str(oma_face.id)], "name": "Oma"}, headers=h
    )
    assert r.status_code == 204, r.text
    oma = uuid.UUID((await client.get("/api/v1/people", headers=h)).json()["people"][0]["id"])
    verborgen = (await faces_of(settings, twee))[1]
    r = await client.post(f"{F}/hide", json={"faces": [str(verborgen.id)]}, headers=h)
    assert r.status_code == 204, r.text
    nee = (await faces_of(settings, drie))[0]
    r = await client.post(
        f"{F}/reject", json={"faces": [str(nee.id)], "person_id": str(oma)}, headers=h
    )
    assert r.status_code == 204, r.text
    await draai(settings)
    oud = {f.id for node in (een, twee, drie) for f in await faces_of(settings, node)}

    # FACES_VERSION 2: de start ziet een oude versie en zet faces.plan version klaar
    for module in (version, scan, register):
        monkeypatch.setattr(module, "FACES_VERSION", 2)
    seen = nep.seen
    await register.on_start(settings)
    await draai(settings)
    assert nep.seen - seen == 3  # elke foto opnieuw naar de worker

    async with maker(settings)() as session:
        states = {
            s.node_id: (s.state, s.version, s.faces)
            for s in await session.scalars(select(FaceScan))
        }
        links = list(
            await session.execute(
                select(PhotoPerson.node_id, PhotoPerson.person_id, PhotoPerson.source)
            )
        )
    assert states == {een: ("done", 2, 2), twee: ("done", 2, 1), drie: ("done", 2, 1)}
    assert links == [(een, oma, "auto")]
    # een: nieuwe gezichten, en Oma weer ja via het kader
    nieuw = await faces_of(settings, een)
    assert [f.version for f in nieuw] == [2, 2] and not {f.id for f in nieuw} & oud
    assert await verdicts_of(settings, nieuw[0].id) == [(oma, "ja")]
    assert await verdicts_of(settings, nieuw[1].id) == []
    # twee: het verborgen gezicht blijft (versie 1, zonder vector); op die plek geen nieuw
    rows = await faces_of(settings, twee)
    assert [(f.id == verborgen.id, f.hidden, f.version) for f in rows] == [
        (False, 0, 2),
        (True, 1, 1),
    ]
    assert rows[1].vec is None
    # drie: de afwijzing is weg (een nieuw gezicht zonder oordeel)
    rows = await faces_of(settings, drie)
    assert len(rows) == 1 and rows[0].id != nee.id and rows[0].version == 2
    assert await verdicts_of(settings, rows[0].id) == []
    # Een tweede start: niets meer van een oude versie, niets opnieuw
    seen = nep.seen
    await register.on_start(settings)
    await draai(settings)
    assert nep.seen == seen
