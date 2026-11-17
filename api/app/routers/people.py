"""Personen op foto's (bouwontwerp fase 6, §3.4, Keuze 37 en 38; Z6). Alleen eigen personen:
een id van een ander geeft 404 not_found, en op de foto van een ander is de lijst leeg. Tellingen,
omslagen en de foto's van een persoon gaan met own_live (app/people/people.py).

Na elke wijziging people.changed {} naar de eigenaar. Het filter person, de suggesties, de groep
bij zoeken en de haak van een kopie staan in app/people/search.py; die meldt zich aan bij de
import hieronder.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import exists

from app import errors
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, PhotoPerson
from app.people import hooks
from app.people import people as P
from app.people import search as _search  # noqa: F401 - meldt filter, groep en kopie aan
from app.people.types import (
    AddPersonIn,
    FilePeople,
    MergeIn,
    People,
    Person,
    PersonIn,
    PersonPatch,
    PersonPhotos,
)
from app.routers.photos import next_cursor, page_where, photo_items, photo_query
from app.routers.stubs import QueryInt
from app.search.live import own_live
from app.security import CurrentUser
from app.text import fold

router = APIRouter(prefix="/api/v1", tags=["foto's"])

# ?hidden=1 komt als tekst binnen; Literal[0, 1] alleen neemt geen "1" (dan altijd 422)
Hidden = Annotated[Literal[0, 1], QueryInt]
# Zoveel foto's van een persoon per pagina
PAGE = 200


async def _out(session, user, person) -> Person:
    [found] = await P.out(session, user, [person])
    return found


@router.get("/people")
async def list_people(user: CurrentUser, session: Session, hidden: Hidden = 0) -> People:
    """Je personen met het aantal foto's en een omslag; hidden=1 geeft de verborgen."""
    return People(people=await P.list_people(session, user, hidden))


@router.post("/people", status_code=201)
async def create_person(body: PersonIn, user: CurrentUser, session: Session) -> Person:
    """Een nieuwe persoon; 409 people_name_taken als je er al een met die naam hebt."""
    async with P.lock(user.id):
        person = await P.create(session, user, body.name)
        await session.commit()
    P.changed(user.id)
    return await _out(session, user, person)


@router.patch("/people/{person_id}")
async def change_person(
    person_id: uuid.UUID, body: PersonPatch, user: CurrentUser, session: Session
) -> Person:
    """Hernoemen, verbergen of terughalen, en de omslag kiezen (null: de nieuwste foto)."""
    given = body.model_fields_set
    async with P.lock(user.id):
        person = await P.own_person(session, user, person_id)
        if body.name is not None:
            name = P.clean(body.name)
            await P.check_free(session, user, name, skip=person.id)
            person.name, person.search_name = name, fold(name)
        if body.hidden is not None:
            person.hidden = int(body.hidden)
        if "cover" in given:
            if body.cover is not None:
                await P.check_cover(session, user, person, body.cover)
            person.cover_node = body.cover
        person.updated_at = P.now()
        await session.commit()
    P.changed(user.id)
    return await _out(session, user, person)


@router.post("/people/{person_id}/merge")
async def merge_person(
    person_id: uuid.UUID, body: MergeIn, user: CurrentUser, session: Session
) -> Person:
    """Deze persoon gaat op in into: zijn foto's krijgen into, en hij is daarna weg."""
    async with P.lock(user.id):
        source = await P.own_person(session, user, person_id)
        into = await P.own_person(session, user, body.into)
        if source.id == into.id:
            raise errors.fail(
                400, "people_merge_self", "Een persoon kan niet met zichzelf samengaan"
            )
        # Gezichten (X2, Keuze 63): vóór het wissen van de bron
        await hooks.persons_merged(session, user.id, source.id, into.id)
        await P.merge(session, user, source, into)
        await session.commit()
    P.changed(user.id)
    return await _out(session, user, into)


@router.delete("/people/{person_id}", status_code=204)
async def delete_person(person_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """De persoon weg, en daarmee van al zijn foto's (de foto's zelf blijven)."""
    async with P.lock(user.id):
        person = await P.own_person(session, user, person_id)
        await session.delete(person)
        await session.flush()  # de trigger person_bd_face loopt nu (fase 6, §3.1a)
        await hooks.person_deleted(session, user.id, person_id)
        await session.commit()
    P.changed(user.id)


@router.get("/people/{person_id}/photos")
async def person_photos(
    person_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    background: BackgroundTasks,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> PersonPhotos:
    """De eigen, levende foto's met deze persoon, nieuwste eerst (zoals /photos); cursor is de
    volgende pagina, of null."""
    person = await P.own_person(session, user, person_id)
    on_it = exists().where(
        PhotoPerson.node_id == Node.id,
        PhotoPerson.person_id == person.id,
        PhotoPerson.owner_id == user.id,
    )
    where = [own_live(Node, user.id), Node.taken_day.is_not(None), on_it, *page_where(cursor)]
    rows = (await session.execute(photo_query(*where).limit(PAGE))).all()
    return PersonPhotos(
        items=await photo_items(session, user, rows, settings, background),
        cursor=next_cursor(rows, PAGE),
    )


@router.get("/files/{node_id}/people")
async def file_people(node_id: uuid.UUID, user: CurrentUser, session: Session) -> FilePeople:
    """Wie er op deze foto staat; voor een ander dan de eigenaar altijd leeg."""
    return await P.on_file(session, user, node_id)


@router.post("/files/{node_id}/people", status_code=201)
async def add_file_person(
    node_id: uuid.UUID, body: AddPersonIn, user: CurrentUser, session: Session
) -> None:
    """Een bestaande persoon (person_id) of een nieuwe (name) op je eigen foto, met een kader
    of zonder. Een foto van een ander, of in de prullenbak: 404."""
    P.owner_only(user)
    P.check_box(body.box)
    async with P.lock(user.id):
        await P.own_photo(session, user, node_id)
        if body.person_id is not None:
            person = await P.own_person(session, user, body.person_id)
        else:
            person = await P.create(session, user, body.name or "")
        await P.put_on(session, user, node_id, person, body.box)
        await session.commit()
    P.changed(user.id)


@router.delete("/files/{node_id}/people/{person_id}", status_code=204)
async def remove_file_person(
    node_id: uuid.UUID, person_id: uuid.UUID, user: CurrentUser, session: Session
) -> None:
    """De persoon van deze foto halen (de persoon zelf blijft)."""
    async with P.lock(user.id):
        person = await P.own_person(session, user, person_id)
        await P.take_off(session, user, node_id, person)
        await hooks.person_removed(session, user.id, node_id, person.id)
        await session.commit()
    P.changed(user.id)
