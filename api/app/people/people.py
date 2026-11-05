"""Personen op foto's met de hand (bouwontwerp fase 6, Keuze 37 en 38; Z6).

Een persoon is van één eigenaar (person.owner_id). photo_person zet hem op een node van die
eigenaar, met een kader of zonder (x, y, w, h als deel van de foto, 0..1). Alleen de eigenaar
ziet zijn personen: een id van een ander is 404 not_found, en op de foto van een ander is de
lijst leeg (Keuze 38).

Tellingen, omslagen en foto's van een persoon gaan met own_live: een foto in de prullenbak (ook
via een map erboven) telt niet mee, maar zijn rij in photo_person blijft, zodat hij na
terugzetten de persoon weer heeft (§3.9).

Na een wijziging people.changed {} naar de eigenaar (§3.4).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, column, delete, exists, func, insert, literal, select, update, values
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.types import Uuid

from app import errors, events
from app.models import Node, Person, PhotoPerson, User
from app.people.types import Box, FilePeople, FilePerson
from app.people.types import Person as PersonOut
from app.routers.files import GUEST
from app.search.live import own_live
from app.search.types import EVENT_PEOPLE_CHANGED, PeopleChangedEvent, PersonRef, event_data
from app.text import CONTROL, fold

# Zoveel personen op één foto, en zoveel kopieën per keer naar de haak van een kopie
MAX_ON_PHOTO = 100
COPY_CHUNK = 500

# Per gebruiker één wijziging tegelijk: of een naam vrij is, moet vastliggen tot hij erin staat
_locks: dict[uuid.UUID, asyncio.Lock] = {}


def lock(user_id: uuid.UUID) -> asyncio.Lock:
    return _locks.setdefault(user_id, asyncio.Lock())


def now() -> datetime:
    return datetime.now(UTC)


def not_found() -> Exception:
    return errors.fail(404, "not_found", "Niet gevonden")


def owner_only(user: User) -> None:
    """Een gast heeft geen eigen foto's, en dus geen eigen personen."""
    if user.role == "guest":
        raise errors.fail(403, **GUEST)


def changed(user_id: uuid.UUID) -> None:
    """people.changed naar de eigenaar, na de commit."""
    data = event_data(EVENT_PEOPLE_CHANGED, PeopleChangedEvent())
    events.publish([user_id], EVENT_PEOPLE_CHANGED, data)


def clean(name: str) -> str:
    """De naam zonder stuurtekens en spaties eromheen; leeg is 422."""
    name = " ".join(CONTROL.sub("", name).split())
    if not name:
        raise errors.fail(422, "invalid_input", "Ongeldige invoer", field="body.name")
    return name


async def own_person(db: AsyncSession, user: User, person_id: uuid.UUID) -> Person:
    """De persoon als hij van user is; anders 404 (ook voor een persoon van een ander)."""
    person = await db.get(Person, person_id)
    if person is None or person.owner_id != user.id:
        raise not_found()
    return person


async def check_free(
    db: AsyncSession, user: User, name: str, skip: uuid.UUID | None = None
) -> None:
    """409 people_name_taken als user al een persoon met deze naam heeft (hoofdletters en
    accenten tellen niet)."""
    query = select(Person.id).where(Person.owner_id == user.id, Person.search_name == fold(name))
    if skip is not None:
        query = query.where(Person.id != skip)
    if await db.scalar(query.limit(1)) is not None:
        raise errors.fail(409, "people_name_taken", "Je hebt al een persoon met deze naam")


async def counts(
    db: AsyncSession, user: User, person_ids: Sequence[uuid.UUID] | None = None
) -> dict[uuid.UUID, int]:
    """Per persoon het aantal eigen, levende foto's (own_live). Zonder person_ids: al zijn
    personen."""
    query = (
        select(PhotoPerson.person_id, func.count())
        .join(Node, Node.id == PhotoPerson.node_id)
        .where(PhotoPerson.owner_id == user.id, own_live(Node, user.id))
        .group_by(PhotoPerson.person_id)
    )
    if person_ids is not None:
        query = query.where(PhotoPerson.person_id.in_(list(person_ids)))
    return {pid: n for pid, n in await db.execute(query)}


async def covers(
    db: AsyncSession, user: User, people: Sequence[Person]
) -> dict[uuid.UUID, uuid.UUID]:
    """De omslag per persoon: de gekozen foto als die nog een eigen, levende foto met deze
    persoon is, anders de nieuwste."""
    if not people:
        return {}
    ids = [p.id for p in people]
    newest = (
        func.row_number()
        .over(
            partition_by=PhotoPerson.person_id,
            order_by=(Node.taken_day.desc(), Node.taken_at.desc(), Node.id.desc()),
        )
        .label("rank")
    )
    ranked = (
        select(PhotoPerson.person_id, PhotoPerson.node_id, newest)
        .join(Node, Node.id == PhotoPerson.node_id)
        .where(
            PhotoPerson.owner_id == user.id,
            PhotoPerson.person_id.in_(ids),
            own_live(Node, user.id),
            Node.taken_day.is_not(None),
        )
        .subquery()
    )
    chosen = {p.id: p.cover_node for p in people if p.cover_node is not None}
    rows = await db.execute(
        select(ranked.c.person_id, ranked.c.node_id, ranked.c.rank).where(
            (ranked.c.rank == 1) | ranked.c.node_id.in_(list(chosen.values()))
        )
    )
    found: dict[uuid.UUID, uuid.UUID] = {}
    for person_id, node_id, rank in rows:
        if chosen.get(person_id) == node_id:
            # De gekozen omslag wint van de nieuwste, in welke volgorde de rijen ook komen
            found[person_id] = node_id
        elif rank == 1:
            found.setdefault(person_id, node_id)
    return found


async def out(db: AsyncSession, user: User, people: Sequence[Person]) -> list[PersonOut]:
    """Person van de API, met het aantal en de omslag (own_live)."""
    n = await counts(db, user, [p.id for p in people]) if people else {}
    cover = await covers(db, user, people)
    return [
        PersonOut(
            id=p.id, name=p.name, count=n.get(p.id, 0), cover=cover.get(p.id), hidden=bool(p.hidden)
        )
        for p in people
    ]


async def list_people(db: AsyncSession, user: User, hidden: int) -> list[PersonOut]:
    """Je personen (hidden 0: de zichtbare, 1: de verborgen); de meeste foto's eerst, dan op
    naam."""
    if user.role == "guest":
        return []
    people = list(
        (
            await db.scalars(
                select(Person)
                .where(Person.owner_id == user.id, Person.hidden == hidden)
                .order_by(Person.search_name, Person.id)
            )
        ).all()
    )
    n = await counts(db, user)
    cover = await covers(db, user, people)
    people.sort(key=lambda p: -n.get(p.id, 0))
    return [
        PersonOut(
            id=p.id, name=p.name, count=n.get(p.id, 0), cover=cover.get(p.id), hidden=bool(p.hidden)
        )
        for p in people
    ]


async def create(db: AsyncSession, user: User, name: str) -> Person:
    """Een nieuwe persoon van user (nog niet gecommit). 409 people_name_taken."""
    owner_only(user)
    name = clean(name)
    await check_free(db, user, name)
    at = now()
    person = Person(
        id=uuid.uuid4(),
        owner_id=user.id,
        name=name,
        search_name=fold(name),
        created_at=at,
        updated_at=at,
    )
    db.add(person)
    await db.flush()
    return person


async def check_cover(db: AsyncSession, user: User, person: Person, node_id: uuid.UUID) -> None:
    """Een omslag is een eigen, levende foto waar deze persoon op staat; anders 422."""
    found = await db.scalar(
        select(PhotoPerson.node_id)
        .join(Node, Node.id == PhotoPerson.node_id)
        .where(
            PhotoPerson.person_id == person.id,
            PhotoPerson.node_id == node_id,
            own_live(Node, user.id),
        )
    )
    if found is None:
        raise errors.fail(422, "invalid_input", "Ongeldige invoer", field="body.cover")


async def merge(db: AsyncSession, user: User, source: Person, into: Person) -> None:
    """source gaat op in into (nog niet gecommit): zijn foto's krijgen into, ook die in de
    prullenbak (dan hebben ze hem na terugzetten). Staat into al op een foto, dan blijft die
    rij; heeft die geen kader en de rij van source wel, dan krijgt hij dat kader. Had into geen
    omslag, dan neemt hij die van source over als het nog een eigen, levende foto is. Daarna is
    source weg."""
    other = aliased(PhotoPerson)
    both = exists().where(
        other.person_id == into.id,
        other.node_id == PhotoPerson.node_id,
        other.owner_id == user.id,
    )
    # Een kader van source waar into er geen heeft
    src = aliased(PhotoPerson)
    box = select(src.x, src.y, src.w, src.h).where(
        src.person_id == source.id, src.node_id == PhotoPerson.node_id, src.x.is_not(None)
    )
    await db.execute(
        update(PhotoPerson)
        .where(
            PhotoPerson.person_id == into.id,
            PhotoPerson.owner_id == user.id,
            PhotoPerson.x.is_(None),
            box.exists(),
        )
        .values(
            x=box.with_only_columns(src.x).scalar_subquery(),
            y=box.with_only_columns(src.y).scalar_subquery(),
            w=box.with_only_columns(src.w).scalar_subquery(),
            h=box.with_only_columns(src.h).scalar_subquery(),
        )
    )
    await db.execute(
        update(PhotoPerson)
        .where(PhotoPerson.person_id == source.id, PhotoPerson.owner_id == user.id, ~both)
        .values(person_id=into.id)
        .execution_options(synchronize_session=False)
    )
    if into.cover_node is None and source.cover_node is not None:
        alive = await db.scalar(
            select(Node.id).where(Node.id == source.cover_node, own_live(Node, user.id))
        )
        if alive is not None:
            into.cover_node = alive
    into.updated_at = now()
    await db.execute(delete(Person).where(Person.id == source.id, Person.owner_id == user.id))


async def own_photo(db: AsyncSession, user: User, node_id: uuid.UUID) -> uuid.UUID:
    """De node als hij een eigen, levende foto of video is (met een opnamedag); anders 404."""
    found = await db.scalar(
        select(Node.id).where(
            Node.id == node_id, own_live(Node, user.id), Node.taken_day.is_not(None)
        )
    )
    if found is None:
        raise not_found()
    return found


async def on_file(db: AsyncSession, user: User, node_id: uuid.UUID) -> FilePeople:
    """De personen op deze foto, alleen voor de eigenaar en alleen als hij leeft; voor een ander
    altijd leeg (zonder te zeggen of de node bestaat)."""
    rows = await db.execute(
        select(Person.id, Person.name, PhotoPerson.x, PhotoPerson.y, PhotoPerson.w, PhotoPerson.h,
               PhotoPerson.source)
        .join(PhotoPerson, PhotoPerson.person_id == Person.id)
        .join(Node, Node.id == PhotoPerson.node_id)
        .where(
            PhotoPerson.node_id == node_id,
            PhotoPerson.owner_id == user.id,
            own_live(Node, user.id),
        )
        .order_by(Person.search_name, Person.id)
    )  # fmt: skip
    people = []
    for pid, name, x, y, w, h, source in rows:
        box: Box | None = None if x is None or y is None or w is None or h is None else (x, y, w, h)
        people.append(FilePerson(person=PersonRef(id=pid, name=name), box=box, source=source))
    return FilePeople(people=people)


def check_box(box: Box | None) -> None:
    """Een kader ligt op de foto: x en y vanaf 0, breedte en hoogte groter dan 0, en samen niet
    voorbij de rand (met een kleine marge voor afronden)."""
    if box is None:
        return
    x, y, w, h = box
    ok = all(v == v for v in box) and 0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1
    if not ok or x + w > 1.0001 or y + h > 1.0001:
        raise errors.fail(422, "invalid_input", "Ongeldige invoer", field="body.box")


async def put_on(
    db: AsyncSession, user: User, node_id: uuid.UUID, person: Person, box: Box | None
) -> None:
    """Zet person op de foto (nog niet gecommit). Staat hij er al op, dan krijgt hij dit kader
    (een nieuw kader, of geen als er geen gegeven is en hij er een had: dan blijft dat)."""
    await own_photo(db, user, node_id)
    row = await db.get(PhotoPerson, (person.id, node_id))
    if row is not None:
        if box is not None:
            row.x, row.y, row.w, row.h = box
            row.source = "hand"
        return
    many = await db.scalar(
        select(func.count())
        .select_from(PhotoPerson)
        .join(Node, Node.id == PhotoPerson.node_id)
        .where(PhotoPerson.node_id == node_id, own_live(Node, user.id))
    )
    if (many or 0) >= MAX_ON_PHOTO:
        raise errors.fail(
            422, "invalid_input", "Ongeldige invoer", field="body.person_id", max=MAX_ON_PHOTO
        )
    x, y, w, h = box if box is not None else (None, None, None, None)
    db.add(
        PhotoPerson(
            person_id=person.id,
            node_id=node_id,
            owner_id=user.id,
            x=x,
            y=y,
            w=w,
            h=h,
            source="hand",
            created_at=now(),
        )
    )
    await db.flush()


async def take_off(db: AsyncSession, user: User, node_id: uuid.UUID, person: Person) -> None:
    """Haal person van de foto (nog niet gecommit). Alleen op een eigen, levende foto; staat hij
    er niet op, dan is dat 404."""
    await own_photo(db, user, node_id)
    gone = await db.execute(
        delete(PhotoPerson).where(
            PhotoPerson.person_id == person.id,
            PhotoPerson.node_id == node_id,
            PhotoPerson.owner_id == user.id,
            PhotoPerson.node_id.in_(select(Node.id).where(own_live(Node, user.id))),
        )
    )
    if not gone.rowcount:
        raise not_found()
    if person.cover_node == node_id:
        person.cover_node = None


async def copy_people(db: AsyncSession, pairs: Sequence[tuple[uuid.UUID, uuid.UUID]]) -> None:
    """De haak van een kopie (search.hooks.photo_copy, Keuze 25a): de personen van de bron gaan
    mee naar de kopie, met hun kader, als de kopie een eigen, levend bestand van dezelfde
    eigenaar is. Kopieert Bram een foto van Eva, dan is de kopie van Bram en gaat er niets mee.
    In de transactie van de kopie, vóór de commit."""
    at = now()
    for start in range(0, len(pairs), COPY_CHUNK):
        chunk = pairs[start : start + COPY_CHUNK]
        pair = (
            values(column("src", Uuid()), column("dst", Uuid()), name="pair")
            .data(list(chunk))
            .cte("pair")
        )
        dst = aliased(Node, name="dst")
        rows = (
            select(
                PhotoPerson.person_id,
                pair.c.dst,
                PhotoPerson.owner_id,
                PhotoPerson.x,
                PhotoPerson.y,
                PhotoPerson.w,
                PhotoPerson.h,
                PhotoPerson.source,
                literal(at),
            )
            .select_from(pair)
            .join(PhotoPerson, PhotoPerson.node_id == pair.c.src)
            .join(dst, dst.id == pair.c.dst)
            .where(own_live(dst, PhotoPerson.owner_id))
        )
        await db.execute(
            insert(PhotoPerson).from_select(
                ["person_id", "node_id", "owner_id", "x", "y", "w", "h", "source", "created_at"],
                rows,
            )
        )


def search_prefix(q: str) -> str | None:
    """De gevouwen vraag om personen op te zoeken (de naam begint ermee); None als er niets is."""
    folded = " ".join(fold(CONTROL.sub("", q)).split())
    return folded or None


def starts_with(prefix: str):
    """search_name begint met prefix, als bereik op de index (geen LIKE: dat negeert hoofdletters
    al en gebruikt de index niet)."""
    return and_(Person.search_name >= prefix, Person.search_name < prefix + "\U0010ffff")
