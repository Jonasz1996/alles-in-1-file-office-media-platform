"""Zoeken op naam, soort en tag, en tags zelf (per gebruiker: een tag op een bestand ziet
alleen wie hem maakte).

Zoeken gaat met een recursieve CTE vanaf de gekozen map omlaag, en stopt bij wat in de
prullenbak ligt: zo komt niets uit (of onder) de prullenbak in de resultaten. Werkt op
SQLite en PostgreSQL.
"""

import asyncio
import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, delete, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import Session
from app.mime import ARCHIVE_TYPES, DOCUMENT_TYPES
from app.models import Node, NodeTag, Tag, User
from app.routers.files import (
    NodeOut,
    TagRef,
    get_dir,
    get_node,
    is_trashed,
    node_out,
    notify,
    tag_order,
    tags_by_node,
    tree,
)
from app.security import CurrentUser
from app.text import fold

router = APIRouter(prefix="/api/v1", tags=["zoeken"])

Category = Literal["folder", "image", "video", "audio", "pdf", "document", "archive", "other"]
# Stuurtekens (ook C1) en tekens die de schrijfrichting omdraaien (een naam die er anders
# uitziet dan hij is); de zero-width joiner (U+200D) in samengestelde emoji mag wel
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
COLOR = re.compile(r"^(#[0-9a-f]{6})?$")
MAX_WORDS = 10
MAX_TAG_NAME = 50
# Zoveel tags heeft een gebruiker hooguit (bij elke nieuwe naam worden ze allemaal vergeleken)
MAX_TAGS = 500

# Per gebruiker één wijziging aan tags tegelijk: of "Foto" en "foto" allebei vrij zijn, en
# welke tags een bestand krijgt, moet vastliggen tot de wijziging erin staat
_tag_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _tag_lock(user_id: uuid.UUID) -> asyncio.Lock:
    return _tag_locks.setdefault(user_id, asyncio.Lock())


class PathPart(BaseModel):
    id: uuid.UUID
    name: str


class Hit(BaseModel):
    node: NodeOut
    # Van de hoofdmap tot en met de map waar het in staat
    path: list[PathPart]


class SearchResult(BaseModel):
    results: list[Hit]
    more: bool


def of_category(category: str):
    """SQL-voorwaarde die hetzelfde zegt als mime.category()."""
    if category == "folder":
        return Node.kind == "dir"
    mime = Node.mime
    groups = {
        "archive": mime.in_(ARCHIVE_TYPES),
        "pdf": mime == "application/pdf",
        "image": mime.like("image/%"),
        "video": mime.like("video/%"),
        "audio": mime.like("audio/%"),
        "document": or_(mime.like("text/%"), mime.in_(DOCUMENT_TYPES)),
    }
    if category == "other":
        return and_(Node.kind == "file", or_(mime.is_(None), not_(or_(*groups.values()))))
    return and_(Node.kind == "file", groups[category])


async def own_tag(session: AsyncSession, user: User, tag_id: uuid.UUID) -> Tag:
    tag = await session.get(Tag, tag_id, populate_existing=True)
    if tag is None or tag.owner_id != user.id:
        raise HTTPException(404, "Tag niet gevonden")
    return tag


async def paths(session: AsyncSession, nodes: list[Node]) -> dict[uuid.UUID, list[PathPart]]:
    """Het pad naar elk van deze nodes, met één recursieve query omhoog."""
    parents = {n.parent_id for n in nodes if n.parent_id}
    if not parents:
        return {}
    up = (
        select(Node.id, Node.parent_id, Node.name)
        .where(Node.id.in_(parents))
        .cte("up", recursive=True)
    )
    above = aliased(Node)
    up = up.union(select(above.id, above.parent_id, above.name).where(above.id == up.c.parent_id))
    known = {row.id: row for row in await session.execute(select(up))}
    result = {}
    for node in nodes:
        chain, current = [], node.parent_id
        while current in known and len(chain) <= len(known):
            row = known[current]
            chain.append(PathPart(id=row.id, name=row.name))
            current = row.parent_id
        result[node.id] = list(reversed(chain))
    return result


@router.get("/search")
async def search(
    user: CurrentUser,
    session: Session,
    q: Annotated[str, Query(max_length=200)] = "",
    type_: Annotated[Category | None, Query(alias="type")] = None,
    tag: uuid.UUID | None = None,
    in_: Annotated[uuid.UUID | None, Query(alias="in")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SearchResult:
    """Zoekt in map `in` (standaard de hoofdmap) en alles eronder. Elk woord van q moet in
    de naam staan (hoofdletters en accenten tellen niet). Mappen eerst, dan op naam."""
    start = await get_dir(session, user, in_ or user.root_id)
    if await is_trashed(session, start):
        raise HTTPException(404, "Map niet gevonden")
    found = tree(start.id, live=True)
    query = select(Node).join(found, Node.id == found.c.id).where(Node.id != start.id)
    for word in fold(q).split()[:MAX_WORDS]:
        query = query.where(Node.search_name.contains(word, autoescape=True))
    if type_ is not None:
        query = query.where(of_category(type_))
    if tag is not None:
        await own_tag(session, user, tag)
        query = query.where(
            select(NodeTag.node_id)
            .where(NodeTag.node_id == Node.id, NodeTag.tag_id == tag)
            .exists()
        )
    rows = list(
        (
            await session.scalars(
                query.order_by(Node.kind, Node.search_name, Node.id).limit(limit + 1)
            )
        ).all()
    )
    more, rows = len(rows) > limit, rows[:limit]
    tags = await tags_by_node(session, user, [n.id for n in rows])
    where = await paths(session, rows)
    return SearchResult(
        results=[Hit(node=node_out(n, tags), path=where.get(n.id, [])) for n in rows],
        more=more,
    )


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
        super().__init__(409, {"message": "Je hebt al een tag met deze naam", "code": "name_taken"})


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
    """Per tag het aantal mappen en bestanden in de boom van de gebruiker, zonder wat in de
    prullenbak ligt; zo klopt het met wat zoeken op die tag vindt."""
    found = tree(user.root_id, live=True)
    query = (
        select(NodeTag.tag_id, func.count())
        .join(found, found.c.id == NodeTag.node_id)
        .join(Tag, Tag.id == NodeTag.tag_id)
        .where(Tag.owner_id == user.id)
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
            raise HTTPException(422, f"Je kunt hooguit {MAX_TAGS} tags hebben")
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


@router.put("/files/{node_id}/tags", tags=["tags"])
async def set_tags(
    node_id: uuid.UUID, body: TagSet, user: CurrentUser, session: Session
) -> list[TagRef]:
    """Vervangt de tags (van deze gebruiker) op een map of bestand door deze set."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _tag_lock(user.id):
        node = await get_node(session, user, node_id)
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
            raise HTTPException(404, "Tag niet gevonden")
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
            raise HTTPException(404, "Niet gevonden") from None
    notify(node, "node.changed")
    return sorted((TagRef.model_validate(t) for t in tags), key=tag_order)
