"""Gezichten lezen voor de API (fase 6, X2b): de stand, voorstellen, groepen en de gezichten van
één foto. Alleen de eigenaar, alleen eigen, levende foto's (own_live). Nooit een vector of een
score naar buiten: sure zegt alleen of het voorstel ruim boven de drempel zit (Keuze 66).

Een cursor is de plek in de lijst (een getal als tekst); een verkeerde cursor begint vooraan.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import face_rows
from app.models import Face, FaceOwner, FacePerson, FaceScan, Node, Person
from app.people.faces import drempels
from app.people.faces_types import (
    FACES_PAGE,
    FaceGroup,
    FaceGroups,
    FaceOut,
    FacePage,
    FacesCount,
    FacesPhotos,
    FaceSuggest,
    PersonSuggestions,
    SuggestionsByPerson,
)
from app.search.live import own_live
from app.search.types import PersonRef

PEOPLE_PAGE = 60
GROUPS_PAGE = 30
SAMPLE = 6
GROUP_FACES = 12


def offset(cursor: str | None) -> int:
    try:
        found = int(cursor or 0)
    except ValueError:
        return 0
    return max(0, found)


def _next(start: int, got: int, page: int) -> str | None:
    return str(start + page) if got > page else None


async def _names(db: AsyncSession, owner: uuid.UUID, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Person.id, Person.name).where(Person.owner_id == owner, Person.id.in_(ids))
    )
    return dict((r.id, r.name) for r in rows)


async def faces_out(db: AsyncSession, owner: uuid.UUID, rows: list[Any]) -> list[FaceOut]:
    """rows: Face-achtige rijen (id, node_id, x, y, w, h, suggest_id, suggest_score, suggest_why)
    plus person (de ja of None)."""
    names = await _names(
        db,
        owner,
        {r.person for r in rows if r.person} | {r.suggest_id for r in rows if r.suggest_id},
    )
    out = []
    for r in rows:
        person = PersonRef(id=r.person, name=names[r.person]) if r.person in names else None
        suggest = None
        if person is None and r.suggest_id in names and r.suggest_why in ("sim", "tag"):
            sure = r.suggest_why == "sim" and (r.suggest_score or 0) >= drempels.SURE
            suggest = FaceSuggest(
                person_id=r.suggest_id, name=names[r.suggest_id], sure=sure, why=r.suggest_why
            )
        out.append(
            FaceOut(
                id=r.id, node_id=r.node_id, box=(r.x, r.y, r.w, r.h), person=person, suggest=suggest
            )
        )
    return out


def _face_cols(owner: uuid.UUID, live: Any = own_live):
    """De kolommen van een gezicht met zijn ja; live is de regel voor eigen, levende foto's
    (own_live), die elke aanroeper zelf noemt."""
    ja = (
        select(FacePerson.person_id)
        .where(FacePerson.face_id == Face.id, FacePerson.verdict == "ja")
        .scalar_subquery()
    )
    cols = (
        Face.id,
        Face.node_id,
        Face.x,
        Face.y,
        Face.w,
        Face.h,
        Face.suggest_id,
        Face.suggest_score,
        Face.suggest_why,
        ja.label("person"),
    )
    return (
        select(*cols)
        .join(Node, Node.id == Face.node_id)
        .where(Face.owner_id == owner, live(Node, owner), Face.hidden == 0)
    )


def _open():
    return face_rows.open_faces()


async def file_faces(db: AsyncSession, owner: uuid.UUID, node: uuid.UUID) -> list[FaceOut]:
    rows = list(
        await db.execute(
            _face_cols(owner, own_live)
            .where(Face.node_id == node)
            .order_by(Face.x, Face.id)
            .limit(200)
        )
    )
    return await faces_out(db, owner, rows)


async def counts(db: AsyncSession, owner: uuid.UUID) -> tuple[FacesPhotos, FacesCount, bool]:
    states = dict(
        (s, n)
        for s, n in await db.execute(
            select(FaceScan.state, func.count())
            .join(Node, Node.id == FaceScan.node_id)
            .where(FaceScan.owner_id == owner, own_live(Node, owner))
            .group_by(FaceScan.state)
        )
    )
    photos = FacesPhotos(
        total=sum(states.values()),
        done=states.get("done", 0),
        waiting=states.get("wait", 0) + states.get("busy", 0),
        failed=states.get("failed", 0),
        skipped=states.get("skip", 0),
    )
    base = (
        select(func.count())
        .select_from(Face)
        .join(Node, Node.id == Face.node_id)
        .where(Face.owner_id == owner, own_live(Node, owner), Face.hidden == 0)
    )
    found = await db.scalar(base)
    open_ = await db.scalar(base.where(_open()))
    groups = await db.scalar(
        select(func.count()).select_from(
            select(Face.cluster)
            .join(Node, Node.id == Face.node_id)
            .where(
                Face.owner_id == owner,
                own_live(Node, owner),
                Face.hidden == 0,
                Face.cluster.is_not(None),
                _open(),
            )
            .group_by(Face.cluster)
            .having(func.count() >= drempels.MIN_GROUP)
            .subquery()
        )
    )
    wiping = await db.scalar(select(FaceOwner.wiping).where(FaceOwner.owner_id == owner))
    faces = FacesCount(
        found=int(found or 0),
        named=int(found or 0) - int(open_ or 0),
        open=int(open_ or 0),
        groups=int(groups or 0),
    )
    return photos, faces, bool(wiping)


async def suggestions(
    db: AsyncSession, owner: uuid.UUID, cursor: str | None
) -> SuggestionsByPerson:
    start = offset(cursor)
    rows = list(
        await db.execute(
            select(Face.suggest_id, func.count())
            .join(Node, Node.id == Face.node_id)
            .join(Person, Person.id == Face.suggest_id)
            .where(
                Face.owner_id == owner,
                own_live(Node, owner),
                Face.hidden == 0,
                Face.suggest_id.is_not(None),
                Person.hidden == 0,
                _open(),
            )
            .group_by(Face.suggest_id)
            .order_by(func.count().desc(), Face.suggest_id)
            .offset(start)
            .limit(PEOPLE_PAGE + 1)
        )
    )
    names = await _names(db, owner, {r[0] for r in rows})
    people = []
    for person, count in rows[:PEOPLE_PAGE]:
        sample = list(
            await db.execute(
                _face_cols(owner, own_live)
                .where(Face.suggest_id == person, _open())
                .order_by(Face.suggest_score.desc(), Face.id)
                .limit(SAMPLE)
            )
        )
        people.append(
            PersonSuggestions(
                person=PersonRef(id=person, name=names.get(person, "")),
                count=count,
                sample=await faces_out(db, owner, sample),
            )
        )
    return SuggestionsByPerson(people=people, cursor=_next(start, len(rows), PEOPLE_PAGE))


async def person_suggestions(
    db: AsyncSession, owner: uuid.UUID, person: uuid.UUID, cursor: str | None, limit: int
) -> FacePage:
    start = offset(cursor)
    rows = list(
        await db.execute(
            _face_cols(owner, own_live)
            .where(Face.suggest_id == person, _open())
            .order_by(Face.suggest_score.desc(), Face.id)
            .offset(start)
            .limit(limit + 1)
        )
    )
    return FacePage(
        faces=await faces_out(db, owner, rows[:limit]), cursor=_next(start, len(rows), limit)
    )


async def run_of(db: AsyncSession, owner: uuid.UUID) -> int:
    return int(await db.scalar(select(FaceOwner.run).where(FaceOwner.owner_id == owner)) or 0)


async def groups(db: AsyncSession, owner: uuid.UUID, cursor: str | None) -> FaceGroups:
    start = offset(cursor)
    run = await run_of(db, owner)
    rows = list(
        await db.execute(
            select(Face.cluster, func.count())
            .join(Node, Node.id == Face.node_id)
            .where(
                Face.owner_id == owner,
                own_live(Node, owner),
                Face.hidden == 0,
                Face.cluster.is_not(None),
                _open(),
            )
            .group_by(Face.cluster)
            .having(func.count() >= drempels.MIN_GROUP)
            .order_by(func.count().desc(), Face.cluster)
            .offset(start)
            .limit(GROUPS_PAGE + 1)
        )
    )
    out = []
    for cluster, count in rows[:GROUPS_PAGE]:
        faces = list(
            await db.execute(
                _face_cols(owner, own_live)
                .where(Face.cluster == cluster, _open())
                .order_by(Face.created_at.desc(), Face.id)
                .limit(GROUP_FACES)
            )
        )
        out.append(
            FaceGroup(key=f"{run}:{cluster}", count=count, faces=await faces_out(db, owner, faces))
        )
    return FaceGroups(groups=out, cursor=_next(start, len(rows), GROUPS_PAGE))


async def group(
    db: AsyncSession, owner: uuid.UUID, run: int, cluster: int, cursor: str | None
) -> FacePage:
    start = offset(cursor)
    rows = list(
        await db.execute(
            _face_cols(owner, own_live)
            .where(Face.cluster == cluster, _open())
            .order_by(Face.created_at.desc(), Face.id)
            .offset(start)
            .limit(FACES_PAGE + 1)
        )
    )
    return FacePage(
        faces=await faces_out(db, owner, rows[:FACES_PAGE]),
        cursor=_next(start, len(rows), FACES_PAGE),
    )


async def crop_source(db: AsyncSession, owner: uuid.UUID, face_id: uuid.UUID) -> Any:
    """(blob sha, x, y, w, h) van een eigen, levend gezicht, of None."""
    return (
        await db.execute(
            select(Node.blob_id, Face.x, Face.y, Face.w, Face.h)
            .join(Node, Node.id == Face.node_id)
            .where(Face.id == face_id, Face.owner_id == owner, own_live(Node, owner))
        )
    ).first()
