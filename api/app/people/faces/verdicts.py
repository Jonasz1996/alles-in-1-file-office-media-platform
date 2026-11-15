"""Bevestigen, afwijzen, niet tonen, ongedaan maken, en de haken van Z6 (fase 6, Keuze 63; X2b).

Z6 blijft de bron van personen. Alles hier draait onder P.lock van de eigenaar, in de transactie
van de aanroeper (nog niet gecommit). Na elke keuze: face_owner.verdict_at = nu, en faces.group
met suggest na 30 s (VERDICT_WAIT). Na niet tonen en ongedaan maken: faces.wipe met scope
checkpoint (K1-15). Alleen eigen, levende foto's (own_live); een gezicht dat er niet (meer) is,
geeft 409 faces_gone.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors, face_rows
from app.models import Face, FacePerson, Node, User
from app.people import people as P
from app.people.faces import group, links, state, wipe
from app.search.live import own_live

RESET_WINDOW = timedelta(minutes=10)


def gone() -> Exception:
    return errors.fail(409, "faces_gone", "Dit gezicht of deze groep bestaat niet meer")


async def _faces(
    db: AsyncSession,
    owner: uuid.UUID,
    ids: list[uuid.UUID],
    live: Any = own_live,
    *,
    hidden: bool = False,
) -> dict[uuid.UUID, uuid.UUID]:
    """face id → node, alleen eigen, levende gezichten; ontbreekt er een, dan 409 faces_gone."""
    q = (
        select(Face.id, Face.node_id)
        .join(Node, Node.id == Face.node_id)
        .where(Face.id.in_(ids), face_rows.no_index(Face.owner_id) == owner, live(Node, owner))
    )
    if not hidden:
        q = q.where(Face.hidden == 0)
    found = dict((r.id, r.node_id) for r in await db.execute(q))
    if len(found) != len(set(ids)):
        raise gone()
    return found


async def _ja_of(db: AsyncSession, ids: list[uuid.UUID]) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(face, person) met ja voor deze gezichten."""
    rows = await db.execute(
        select(FacePerson.face_id, FacePerson.person_id)
        .join(Face, Face.id == FacePerson.face_id)
        .join(Node, Node.id == Face.node_id)
        .where(
            FacePerson.face_id.in_(ids), FacePerson.verdict == "ja", own_live(Node, Face.owner_id)
        )
    )
    return [(r.face_id, r.person_id) for r in rows]


async def _after(db: AsyncSession, owner: uuid.UUID) -> None:
    row = await state.owner_row(db, owner)
    row.verdict_at = state.now()
    await group.queue_group(db, owner, "suggest", wait=True)


async def confirm(
    db: AsyncSession,
    user: User,
    ids: list[uuid.UUID],
    person_id: uuid.UUID | None,
    name: str | None,
) -> uuid.UUID:
    """Ja: face_person ja; een ja voor een ander wordt nee ("nee, dit is Bram" in één stap); een
    nieuwe naam maakt de persoon via Z6. photo_person via sync_links. Geeft de persoon."""
    owner = user.id
    nodes = await _faces(db, owner, ids, own_live)
    if person_id is not None:
        person = await P.own_person(db, user, person_id)
    else:
        person = await P.create(db, user, name or "")
    others = [(f, p) for f, p in await _ja_of(db, ids) if p != person.id]
    at = state.now()
    for face_id, other in others:
        await db.execute(
            update(FacePerson)
            .where(FacePerson.face_id == face_id, FacePerson.person_id == other)
            .values(verdict="nee", created_at=at)
        )
    await db.execute(
        delete(FacePerson).where(FacePerson.face_id.in_(ids), FacePerson.person_id == person.id)
    )
    await db.execute(
        insert(FacePerson),
        [
            {
                "face_id": f,
                "person_id": person.id,
                "owner_id": owner,
                "verdict": "ja",
                "created_at": at,
            }
            for f in dict.fromkeys(ids)
        ],
    )
    await db.execute(
        update(Face)
        .where(Face.id.in_(ids))
        .values(suggest_id=None, suggest_score=None, suggest_why=None, cluster=None)
    )
    for node in sorted(set(nodes.values())):
        await links.sync_links(db, owner, node, person.id)
    for face_id, other in others:
        await links.sync_links(db, owner, nodes[face_id], other)
    await _after(db, owner)
    return person.id


async def reject(db: AsyncSession, user: User, ids: list[uuid.UUID], person_id: uuid.UUID) -> None:
    """Nee: face_person nee voor dat paar (was het ja, dan sync_links); het voorstel weg."""
    owner = user.id
    nodes = await _faces(db, owner, ids, own_live)
    person = await P.own_person(db, user, person_id)
    was_ja = {f for f, p in await _ja_of(db, ids) if p == person.id}
    await db.execute(
        delete(FacePerson).where(FacePerson.face_id.in_(ids), FacePerson.person_id == person.id)
    )
    at = state.now()
    await db.execute(
        insert(FacePerson),
        [
            {
                "face_id": f,
                "person_id": person.id,
                "owner_id": owner,
                "verdict": "nee",
                "created_at": at,
            }
            for f in dict.fromkeys(ids)
        ],
    )
    await db.execute(
        update(Face)
        .where(Face.id.in_(ids), Face.suggest_id == person.id)
        .values(suggest_id=None, suggest_score=None, suggest_why=None)
    )
    for node in sorted({nodes[f] for f in was_ja}):
        await links.sync_links(db, owner, node, person.id)
    await _after(db, owner)


async def hide(db: AsyncSession, user: User, ids: list[uuid.UUID]) -> None:
    """Niet tonen: vec, voorstel en groep NULL, hidden 1, de oordelen weg (sync_links); daarna
    faces.wipe met scope checkpoint."""
    owner = user.id
    nodes = await _faces(db, owner, ids, own_live)
    ja = await _ja_of(db, ids)
    await face_rows.hide_rows(db, list(dict.fromkeys(ids)))
    for face_id, person in ja:
        await links.sync_links(db, owner, nodes[face_id], person)
    await _after(db, owner)
    await wipe.queue_wipe(db, owner, "checkpoint")


async def reset(db: AsyncSession, user: User, ids: list[uuid.UUID]) -> None:
    """Ongedaan maken, hooguit RESET_WINDOW na de laatste keuze: de oordelen over deze gezichten
    weg (sync_links), verborgen weer zichtbaar maar zonder vector (die komt pas terug na
    "Opnieuw zoeken"). Daarna faces.wipe met scope checkpoint."""
    owner = user.id
    row = await state.owner_row(db, owner)
    last = row.verdict_at
    if last is None or state.now() - group._aware(last) > RESET_WINDOW:
        raise errors.fail(409, "faces_gone", "Dit kan niet meer ongedaan gemaakt worden")
    nodes = await _faces(db, owner, ids, own_live, hidden=True)
    ja = await _ja_of(db, ids)
    await db.execute(delete(FacePerson).where(FacePerson.face_id.in_(ids)))
    await db.execute(update(Face).where(Face.id.in_(ids), Face.hidden == 1).values(hidden=0))
    for face_id, person in ja:
        await links.sync_links(db, owner, nodes[face_id], person)
    await _after(db, owner)
    await wipe.queue_wipe(db, owner, "checkpoint")


# --- de haken van Z6 (app/people/hooks.py) ------------------------------------------------------


async def person_removed(
    db: AsyncSession, owner: uuid.UUID, node: uuid.UUID, person: uuid.UUID
) -> None:
    """Een persoon van een foto gehaald: zijn ja op die node wordt nee."""
    ids = list(
        await db.scalars(
            select(Face.id)
            .join(FacePerson, FacePerson.face_id == Face.id)
            .join(Node, Node.id == Face.node_id)
            .where(
                Face.node_id == node,
                FacePerson.person_id == person,
                FacePerson.verdict == "ja",
                own_live(Node, owner),
            )
        )
    )
    if not ids:
        return
    await db.execute(
        update(FacePerson)
        .where(FacePerson.face_id.in_(ids), FacePerson.person_id == person)
        .values(verdict="nee", created_at=state.now())
    )
    await _after(db, owner)


async def persons_merged(
    db: AsyncSession, owner: uuid.UUID, source: uuid.UUID, into: uuid.UUID
) -> None:
    """Samenvoegen, vóór P.merge: ja en nee van de bron naar het doel (face_rows.move_verdicts),
    suggest_id ook; photo_person verhuist P.merge zelf. Daarna suggest na 30 s."""
    if not await face_rows.has_rows(db, owner):
        return
    await face_rows.move_verdicts(db, owner, source, into)
    await _after(db, owner)


async def person_deleted(db: AsyncSession, owner: uuid.UUID, person: uuid.UUID) -> None:
    """Een persoon gewist: de trigger person_bd_face maakte zijn gezichten al "niet tonen",
    zonder vector. Dan faces.wipe met scope checkpoint."""
    if not await face_rows.has_rows(db, owner):
        return
    await wipe.queue_wipe(db, owner, "checkpoint")
