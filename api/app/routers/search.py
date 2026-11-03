"""Zoeken, en tags zelf (per gebruiker: een tag op een bestand ziet alleen wie hem maakte).

Zoeken gaat via de zoekmachine van app/search/engine.py (fase 6, Keuze 10): select_engine geeft
tot Z1 altijd LikeFallback, de zoekfunctie van 0.7.0 (app/search/like.py). De vraag blijft
die van 0.7.0 (q, één type, één tag, in, limit tot 200); het antwoord is SearchPage, met
results en more zoals in 0.7.0 en de velden van fase 6 erbij. Z1 maakt de vraag van §3.4.
"""

import asyncio
import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors, events
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, NodeTag, Tag, User
from app.routers.files import TagRef, get_tree_access, tag_order, tree
from app.search.engine import read_conn_info, select_engine
from app.search.like import own_tag, search_roots
from app.search.types import SearchPage, SearchParams, Suggestions
from app.security import CurrentUser
from app.text import CONTROL, fold

router = APIRouter(prefix="/api/v1", tags=["zoeken"])

# De soorten van 0.7.0 (mime.category, met archive); Z1 maakt er de soorten van node.ftype van
Category = Literal["folder", "image", "video", "audio", "pdf", "document", "archive", "other"]
COLOR = re.compile(r"^(#[0-9a-f]{6})?$")
MAX_TAG_NAME = 50
# Zoveel tags heeft een gebruiker hooguit (bij elke nieuwe naam worden ze allemaal vergeleken)
MAX_TAGS = 500

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
    q: Annotated[str, Query(max_length=200)] = "",
    type_: Annotated[Category | None, Query(alias="type")] = None,
    tag: uuid.UUID | None = None,
    in_: Annotated[uuid.UUID | None, Query(alias="in")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SearchPage:
    """Zoekt in map `in` en alles eronder, of zonder `in` in je eigen bestanden en alles wat
    met je gedeeld is. Elk woord van q moet in de naam staan (hoofdletters en accenten tellen
    niet). Mappen eerst, dan op naam. tag: alleen je eigen tags."""
    # Zonder controle gebouwd: de vraag van 0.7.0 past niet in de grenzen van SearchParams
    # (archive als soort, limit tot 200). LikeFallback leest alleen q, types, tags, in_ en limit
    params = SearchParams.model_construct(
        q=q,
        types=[type_] if type_ is not None else [],
        tags=[tag] if tag is not None else [],
        in_=in_,
        limit=limit,
    )
    engine = select_engine(settings, await read_conn_info(await session.connection()))
    return await engine.search(session, user, params)


@router.get("/search/suggest")
async def suggest(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
) -> Suggestions:
    """Namen, personen, plaatsen, tags en albums voor het zoekveld, elk hooguit 5. Tot Z1 geeft
    LikeFallback geen suggesties."""
    engine = select_engine(settings, await read_conn_info(await session.connection()))
    return await engine.suggest(session, user, q)


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
