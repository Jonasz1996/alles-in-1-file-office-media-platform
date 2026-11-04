"""Zoeken, en tags zelf (per gebruiker: een tag op een bestand ziet alleen wie hem maakte).

Zoeken gaat via de zoekmachine van app/search/engine.py (fase 6, Keuze 10): FtsEngine over de
index, of LikeFallback (de zoekfunctie van 0.7.0, app/search/like.py) als de index uit staat,
de database geen FTS5 heeft of er een herbouw loopt. De vraag is die van §3.4 (q, type meer
keer, from, to, person, place of country, tag meer keer, library, camera, in, offset, limit,
after); het antwoord is SearchPage, met results en more zoals in 0.7.0.

De overgang (review Z0, haalbaar F4; bouwontwerp §3.4): een vraag die niet in SearchParams past
(type=archive, limit boven 50: wat de web van nu stuurt) gaat altijd naar LikeFallback, ook nu
Z1 FtsEngine aanzet. De rest gaat via de motor, met een gecontroleerde SearchParams. Z4 zet de
web op de vraag van §3.4 en haalt deze tak weg; tot dan blijft limit zonder waarde 50, zoals in
0.7.0 (§3.4 zegt 30).

Grenzen (Keuze 13): FtsEngine draait binnen limits.admit (2 tegelijk voor de app met een rij van
20, 1 per gebruiker, de snelheid per gebruiker). LikeFallback niet: dat is het zoeken van 0.7.0,
zonder tijdsgrens die een nieuwere vraag kan stoppen.

Zoeken op beeld (X3, Keuze 80; Z0b): mode=image of like geeft na de inlog 501 not_built("X3")
tot X3 er is. Zonder mode (of met mode=text) is het een vraag zoals ervoor, met hetzelfde
antwoord; daarin staan dan mode "text" en fallback null (de standaard van SearchPage).
"""

import asyncio
import re
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis, errors, events
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, NodeTag, Tag, User
from app.routers.files import TagRef, get_tree_access, tag_order, tree
from app.routers.stubs import not_built
from app.search import engine as engines
from app.search.engine import LIKE, engine_for
from app.search.like import own_tag, search_roots
from app.search.limits import admit
from app.search.query import QueryTooLong
from app.search.types import SearchMode, SearchPage, SearchParams, Suggestions
from app.security import CurrentUser
from app.text import CONTROL, fold

router = APIRouter(prefix="/api/v1", tags=["zoeken"])

# De soorten van 0.7.0 (mime.category, met archive) en die van node.ftype (§3.4)
Category = Literal[
    "folder", "image", "video", "audio", "pdf", "document", "spreadsheet", "presentation",
    "archive", "other",
]  # fmt: skip
COLOR = re.compile(r"^(#[0-9a-f]{6})?$")
MAX_TAG_NAME = 50
# Zoveel tags heeft een gebruiker hooguit (bij elke nieuwe naam worden ze allemaal vergeleken)
MAX_TAGS = 500
MAX_OFFSET = 1000

# Per gebruiker één wijziging aan tags tegelijk: of "Foto" en "foto" allebei vrij zijn, en
# welke tags een bestand krijgt, moet vastliggen tot de wijziging erin staat
_tag_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _tag_lock(user_id: uuid.UUID) -> asyncio.Lock:
    return _tag_locks.setdefault(user_id, asyncio.Lock())


@router.get("/search")
async def search(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
    q: Annotated[str, Query(max_length=200)] = "",
    type_: Annotated[list[Category], Query(alias="type")] = [],  # noqa: B006
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    person: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    place: int | None = None,
    country: Annotated[str | None, Query(max_length=8)] = None,
    tag: Annotated[list[uuid.UUID], Query()] = [],  # noqa: B006
    library: uuid.UUID | None = None,
    camera: Annotated[str | None, Query(max_length=200)] = None,
    in_: Annotated[uuid.UUID | None, Query(alias="in")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    after: int | None = None,
    mode: SearchMode = "text",
    like: uuid.UUID | None = None,
) -> SearchPage:
    """Zoekt in je eigen bestanden en alles wat met je gedeeld is (met `in`: alleen in die map
    en alles eronder), op hele woorden in naam, titel, tekst, OCR en meta, het laatste woord
    ook als begin van een woord, en daarna op stukken van namen. Zonder q en zonder filter:
    400 search_bad_filter. mode=image en like (zoeken op beeld, ook "Meer zoals deze") zijn
    van X3."""
    if mode == "image" or like is not None:
        raise not_built("X3")
    if offset > MAX_OFFSET:
        raise errors.fail(
            400, "search_bad_offset", "Verder dan dit kun je niet bladeren", max=MAX_OFFSET
        )
    fields = {
        "q": q,
        "types": type_,
        "date_from": date_from,
        "date_to": date_to,
        "persons": person,
        "place": place,
        "country": country,
        "tags": tag,
        "library": library,
        "camera": camera,
        "in_": in_,
        "offset": offset,
        "limit": limit,
        "after": after,
    }
    try:
        params = SearchParams(**fields)
    except ValidationError:
        # De vraag van 0.7.0 (archive als soort, limit tot 200) past niet in SearchParams:
        # zonder controle gebouwd, en altijd naar LikeFallback (die leest alleen q, types,
        # tags, in_, offset en limit), tot Z4 de web op de vraag van §3.4 zet
        return await LIKE.search(session, user, SearchParams.model_construct(**fields))
    if not q.strip() and not params.has_filter():
        raise errors.fail(400, "search_bad_filter", "Geef een zoekvraag of een filter")
    engine = await engine_for(settings, await session.connection())
    if engine is LIKE:
        page = await engine.search(session, user, params)
        page.indexing = await analysis.indexing(session)
        return page
    # Wie in de rij van admit wacht, houdt geen verbinding uit de pool vast (dat zijn er 15;
    # de rij is 20 lang): de motor neemt na het wachten een nieuwe
    await session.commit()
    token = engines.SETTINGS.set(settings)
    # De tijd per stap in Server-Timing, alleen voor tests en metingen (Keuze 13)
    steps: dict[str, float] | None = {} if settings.search_timing else None
    timing = engines.TIMING.set(steps)
    try:
        async with admit(user, "search", settings):
            page = await engine.search(session, user, params)
    except QueryTooLong:
        raise errors.fail(
            400, "search_query_too_long", "Je zoekvraag is te lang", max_words=10
        ) from None
    finally:
        engines.TIMING.reset(timing)
        engines.SETTINGS.reset(token)
        if steps:
            response.headers["Server-Timing"] = engines.server_timing(steps)
    # Z2 (Keuze 20): hoe ver de backfill van de tekst is, zolang er genoeg open staat; buiten
    # admit, want de telling heeft geen plek in de rij nodig (hooguit eens per 5 s)
    page.indexing = await analysis.indexing(session)
    return page


@router.get("/search/suggest")
async def suggest(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
) -> Suggestions:
    """Namen, personen, plaatsen, tags en albums voor het zoekveld, elk hooguit 5
    (app/search/suggest.py)."""
    engine = await engine_for(settings, await session.connection())
    if engine is LIKE:
        return await engine.suggest(session, user, q)
    await session.commit()  # geen verbinding vasthouden in de rij (zie search)
    try:
        async with admit(user, "suggest", settings):
            return await engine.suggest(session, user, q)
    except QueryTooLong:
        raise errors.fail(
            400, "search_query_too_long", "Je zoekvraag is te lang", max_words=10
        ) from None


class TagOut(TagRef):
    # Op hoeveel mappen en bestanden (niet in de prullenbak) de tag staat
    count: int = 0


def _clean_tag_name(value: str) -> str:
    value = value.strip()
    if not value or CONTROL.search(value):
        raise ValueError("Ongeldige naam voor een tag")
    if len(value) > MAX_TAG_NAME:  # pas na strip: spaties eromheen tellen niet
        raise ValueError(f"Een tag mag hooguit {MAX_TAG_NAME} tekens hebben")
    return value


def _clean_color(value: str) -> str:
    value = value.strip().lower()
    if not COLOR.match(value):
        raise ValueError("Kleur moet leeg zijn of zoals #1d5fd1")
    return value


class TagChange(BaseModel):
    # De echte grens (MAX_TAG_NAME) geldt na het weghalen van spaties eromheen
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # Leeg = geen kleur
    color: str | None = Field(default=None, max_length=16)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_tag_name(value)

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str | None) -> str | None:
        return None if value is None else _clean_color(value)


class NewTag(TagChange):
    name: str = Field(min_length=1, max_length=200)
    color: str = Field(default="", max_length=16)


class TagSet(BaseModel):
    tag_ids: list[uuid.UUID] = Field(max_length=100)


class TagNameTaken(HTTPException):
    def __init__(self) -> None:
        e = errors.fail(409, "name_taken", "Je hebt al een tag met deze naam")
        super().__init__(e.status_code, e.detail, e.headers)


async def check_free(session: AsyncSession, user: User, name: str, skip: uuid.UUID | None = None):
    """Hoofdletters en accenten tellen niet mee. In Python vergeleken: lower() in SQLite kent
    alleen ASCII. Een gebruiker heeft hooguit MAX_TAGS tags."""
    names = await session.execute(select(Tag.id, Tag.name).where(Tag.owner_id == user.id))
    if any(fold(other) == fold(name) and tag_id != skip for tag_id, other in names):
        raise TagNameTaken


async def tag_out(session: AsyncSession, user: User, tag: Tag) -> TagOut:
    counts = await tag_counts(session, user, [tag.id])
    return TagOut(id=tag.id, name=tag.name, color=tag.color, count=counts.get(tag.id, 0))


async def tag_counts(
    session: AsyncSession, user: User, tag_ids: list[uuid.UUID] | None = None
) -> dict[uuid.UUID, int]:
    """Per tag het aantal mappen en bestanden in de boom van de gebruiker en wat met hem
    gedeeld is, zonder wat in de prullenbak ligt; zo klopt het met wat zoeken op die tag
    vindt."""
    found = tree(await search_roots(session, user), live=True)
    query = (
        select(NodeTag.tag_id, func.count())
        .join(found, found.c.id == NodeTag.node_id)
        .join(Tag, Tag.id == NodeTag.tag_id)
        .join(Node, Node.id == NodeTag.node_id)
        .where(Tag.owner_id == user.id, Node.kind != "album")
        .group_by(NodeTag.tag_id)
    )
    if tag_ids is not None:
        query = query.where(NodeTag.tag_id.in_(tag_ids))
    return {tag_id: count for tag_id, count in await session.execute(query)}


@router.get("/tags", tags=["tags"])
async def list_tags(user: CurrentUser, session: Session) -> list[TagOut]:
    tags = sorted(
        (await session.scalars(select(Tag).where(Tag.owner_id == user.id))).all(), key=tag_order
    )
    counts = await tag_counts(session, user)
    return [TagOut(id=t.id, name=t.name, color=t.color, count=counts.get(t.id, 0)) for t in tags]


@router.post("/tags", tags=["tags"], status_code=201)
async def create_tag(body: NewTag, user: CurrentUser, session: Session) -> TagOut:
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _tag_lock(user.id):
        count = await session.scalar(
            select(func.count()).select_from(Tag).where(Tag.owner_id == user.id)
        )
        if (count or 0) >= MAX_TAGS:
            raise errors.fail(
                422, "too_many_tags", f"Je kunt hooguit {MAX_TAGS} tags hebben", max=MAX_TAGS
            )
        await check_free(session, user, body.name)
        tag = Tag(owner_id=user.id, name=body.name, color=body.color)
        session.add(tag)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise TagNameTaken from None
    return TagOut(id=tag.id, name=tag.name, color=tag.color, count=0)


@router.patch("/tags/{tag_id}", tags=["tags"])
async def change_tag(
    tag_id: uuid.UUID, body: TagChange, user: CurrentUser, session: Session
) -> TagOut:
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _tag_lock(user.id):
        tag = await own_tag(session, user, tag_id)
        if body.name is not None:
            await check_free(session, user, body.name, skip=tag.id)
            tag.name = body.name
        if body.color is not None:
            tag.color = body.color
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise TagNameTaken from None
    # Open lijsten tonen de tag als chip: die laden opnieuw
    events.publish([user.id], "tags.changed", {"id": str(tag.id)})
    return await tag_out(session, user, tag)


@router.delete("/tags/{tag_id}", tags=["tags"], status_code=204)
async def delete_tag(tag_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """Weg van alle bestanden en mappen; die blijven zelf gewoon staan."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _tag_lock(user.id):
        tag = await own_tag(session, user, tag_id)
        await session.execute(delete(NodeTag).where(NodeTag.tag_id == tag.id))
        await session.delete(tag)
        await session.commit()
    events.publish([user.id], "tags.changed", {"id": str(tag_id)})


@router.put("/files/{node_id}/tags", tags=["tags"])
async def set_tags(
    node_id: uuid.UUID, body: TagSet, user: CurrentUser, session: Session
) -> list[TagRef]:
    """Vervangt de tags (van deze gebruiker) op een map of bestand door deze set. Kan ook op
    wat met je gedeeld is (alleen kijken is genoeg): tags zijn persoonlijk."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _tag_lock(user.id):
        found = await get_tree_access(session, user, node_id, "read")
        node = found.node
        wanted = set(body.tag_ids)
        tags = (
            (
                await session.scalars(
                    select(Tag).where(Tag.owner_id == user.id, Tag.id.in_(wanted))
                )
            ).all()
            if wanted
            else []
        )
        if len(tags) != len(wanted):
            raise errors.fail(404, "tag_not_found", "Tag niet gevonden")
        mine = select(Tag.id).where(Tag.owner_id == user.id)
        await session.execute(
            delete(NodeTag).where(NodeTag.node_id == node.id, NodeTag.tag_id.in_(mine))
        )
        session.add_all(NodeTag(node_id=node.id, tag_id=t.id) for t in tags)
        try:
            await session.commit()
        except IntegrityError:
            # Het bestand is intussen definitief verwijderd
            await session.rollback()
            raise errors.fail(404, "not_found", "Niet gevonden") from None
    # Alleen naar wie het deed: niemand anders ziet deze tags
    hidden = found.grant is not None and found.grant.root_id == node.id
    parent_id = str(node.parent_id) if node.parent_id and not hidden else None
    events.publish([user.id], "node.changed", {"id": str(node.id), "parent_id": parent_id})
    return sorted((TagRef.model_validate(t) for t in tags), key=tag_order)
