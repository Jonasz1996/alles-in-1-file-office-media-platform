"""De drie haken van personen voor gezichten (bouwontwerp fase 6, Keuze 63 en "De haken"; A0):
person_removed, persons_merged en person_deleted in routers/people.py.

- zonder aanmelding doen ze niets: de acties zijn die van 0.8.0;
- elke haak precies één keer, met de eigenaar en de ids, in de transactie van de actie;
- persons_merged vóór het wissen van de bron (de bron en zijn face_person zijn er nog);
- person_deleted na de flush: de trigger person_bd_face heeft zijn bevestigde gezicht dan al
  "niet tonen" gemaakt, zonder vector, en de persoon is weg;
- een fout in een haak gaat door, en de actie gebeurt dan niet.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import Face, FacePerson, Person
from app.people import hooks
from tests.factories import maker
from tests.people.test_people import BASE, huis, nieuw, op, op_foto, personen  # noqa: F401

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]


@pytest.fixture(autouse=True)
def _schoon():
    hooks.reset()
    yield
    hooks.reset()


async def _gezicht(settings, owner_id, node_id, person_id) -> uuid.UUID:
    """Een gezicht met een vector, bevestigd voor person_id (zoals X2 het schrijft)."""
    face_id = uuid.uuid4()
    async with maker(settings)() as session:
        session.add(
            Face(id=face_id, node_id=node_id, owner_id=owner_id, x=0.1, y=0.1, w=0.2, h=0.2,
                 score=0.9, px=80, vec=b"\x01" * 16, version=1, created_at=BASE)
        )  # fmt: skip
        await session.flush()
        session.add(
            FacePerson(face_id=face_id, person_id=uuid.UUID(person_id), verdict="ja",
                       owner_id=owner_id, created_at=BASE)
        )  # fmt: skip
        await session.commit()
    return face_id


async def test_zonder_aanmelding_niets(client, huis):  # noqa: F811
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna")
    await op(client, eva, huis["een"], name="Anneke")
    p = await personen(client, eva)
    anna, anneke = p["Anna"]["id"], p["Anneke"]["id"]
    r = await client.delete(f"/api/v1/files/{huis['een']}/people/{anna}", headers=eva)
    assert r.status_code == 204, r.text
    r = await client.post(f"/api/v1/people/{anneke}/merge", json={"into": anna}, headers=eva)
    assert r.status_code == 200, r.text
    r = await client.delete(f"/api/v1/people/{anna}", headers=eva)
    assert r.status_code == 204, r.text
    assert await personen(client, eva) == {}


async def test_elke_haak_precies_een_keer(client, huis, settings):  # noqa: F811
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna")
    await op(client, eva, huis["twee"], name="Anneke")
    p = await personen(client, eva)
    anna, anneke = p["Anna"]["id"], p["Anneke"]["id"]
    face_anneke = await _gezicht(settings, huis["eva"], huis["twee"], anneke)
    face_anna = await _gezicht(settings, huis["eva"], huis["een"], anna)
    calls: list[tuple] = []

    async def removed(session, owner_id, node_id, person_id):
        # Nog in de transactie: de persoon is al van de foto af, en nog niet gecommit
        calls.append(("removed", owner_id, node_id, person_id, session.in_transaction()))

    async def merged(session, owner_id, source_id, into_id):
        # Vóór het wissen van de bron: hij en zijn ja zijn er nog
        source = await session.get(Person, source_id)
        ja = await session.scalar(
            select(FacePerson.face_id).where(FacePerson.person_id == source_id)
        )
        calls.append(("merged", owner_id, source_id, into_id, source is not None, ja))

    async def deleted(session, owner_id, person_id):
        # Na de flush: de trigger heeft het gezicht "niet tonen" gemaakt, zonder vector
        face = (
            await session.execute(select(Face.hidden, Face.vec).where(Face.id == face_anna))
        ).one()
        gone = await session.scalar(select(Person.id).where(Person.id == person_id)) is None
        calls.append(("deleted", owner_id, person_id, tuple(face), gone))

    hooks.register_person_removed(removed)
    hooks.register_persons_merged(merged)
    hooks.register_person_deleted(deleted)
    hooks.register_person_removed(removed)  # twee keer aanmelden telt één keer

    r = await client.delete(f"/api/v1/files/{huis['een']}/people/{anna}", headers=eva)
    assert r.status_code == 204, r.text
    r = await client.post(f"/api/v1/people/{anneke}/merge", json={"into": anna}, headers=eva)
    assert r.status_code == 200, r.text
    r = await client.delete(f"/api/v1/people/{anna}", headers=eva)
    assert r.status_code == 204, r.text

    a, b = uuid.UUID(anna), uuid.UUID(anneke)
    assert calls == [
        ("removed", huis["eva"], huis["een"], a, True),
        ("merged", huis["eva"], b, a, True, face_anneke),
        ("deleted", huis["eva"], a, (1, None), True),
    ]


@pytest.mark.parametrize("which", ["removed", "merged", "deleted"])
async def test_een_fout_in_de_haak_houdt_de_actie_tegen(client, huis, which):  # noqa: F811
    eva = huis["h_eva"]
    await op(client, eva, huis["een"], name="Anna")
    await op(client, eva, huis["een"], name="Anneke")
    p = await personen(client, eva)
    anna, anneke = p["Anna"]["id"], p["Anneke"]["id"]

    async def fout(*_args):
        raise RuntimeError("haak faalt")

    {
        "removed": hooks.register_person_removed,
        "merged": hooks.register_persons_merged,
        "deleted": hooks.register_person_deleted,
    }[which](fout)
    with pytest.raises(RuntimeError, match="haak faalt"):
        if which == "removed":
            await client.delete(f"/api/v1/files/{huis['een']}/people/{anna}", headers=eva)
        elif which == "merged":
            await client.post(f"/api/v1/people/{anneke}/merge", json={"into": anna}, headers=eva)
        else:
            await client.delete(f"/api/v1/people/{anna}", headers=eva)
    # Niets veranderd: beiden bestaan en staan op de foto
    assert set(await personen(client, eva)) == {"Anna", "Anneke"}
    assert {x["person"]["name"] for x in await op_foto(client, eva, huis["een"])} == {
        "Anna",
        "Anneke",
    }
