import asyncio
import base64
import binascii
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path
from typing import Annotated, Literal

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import (
    Integer,
    Select,
    Uuid,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import access, errors, events, photodate, previews
from app.access import Grant, Step
from app.config import Settings, get_settings
from app.db import Session
from app.mime import read_head, sniff
from app.models import Blob, GroupMember, Node, NodeTag, Share, Tag, Upload, User, Version
from app.routers.auth import used_bytes
from app.security import CurrentUser, active_user, download_token, read_download_token
from app.storage import BlobStore, TooLarge
from app.text import CONTROL, fold

router = APIRouter(prefix="/api/v1", tags=["bestanden"])
FORBIDDEN = re.compile(r"[/\\]")
MAX_NAME = 255
# Zoveel eerdere versies van een bestand blijven bewaard; de oudste gaan eerst
MAX_VERSIONS = 20
# Zoveel mappen en bestanden kopieert één verzoek hooguit: de kopie gebeurt in één transactie
# (onder het blob-slot), en die mag andere schrijvers niet te lang laten wachten
MAX_COPY = 10_000

GUEST = {
    "message": "Gasten hebben geen eigen bestanden; je ziet alleen wat met je is gedeeld",
    "code": "guest",
}
READ_ONLY = {"message": "Je mag hier alleen kijken", "code": "read_only"}
OWNER_ONLY = {"message": "Dat kan alleen de eigenaar", "code": "owner_only"}
SHARE_ROOT = {
    "message": "Dit is de gedeelde map zelf; alleen de eigenaar kan die hernoemen, verplaatsen "
    "of verwijderen",
    "code": "share_root",
}
# Hetzelfde voor een gedeeld bestand, of een map in een gedeelde map die ook zelf met je
# gedeeld is (dan is het voor jou niet "de gedeelde map")
SHARED_ITSELF = {
    "message": "Dit is zelf met je gedeeld; alleen de eigenaar kan het hernoemen, "
    "verplaatsen of verwijderen",
    "code": "share_root",
}
CROSS_OWNER = {
    "message": "Verplaatsen kan alleen binnen dezelfde eigenaar; kopieer het in plaats daarvan",
    "code": "cross_owner",
}
MOVED = {
    "message": "Het bestand is intussen hernoemd, verplaatst of verwijderd; probeer het opnieuw",
    "code": "moved",
}

# Per eigenaar: quotum controleren en ruimte innemen in één keer (hervatbare uploads
# reserveren, kopiëren maakt de kopie), zodat twee verzoeken samen niet over het quotum gaan.
# Ook als anderen in een gedeelde map van die eigenaar uploaden: het is zijn quotum.
user_locks: dict[uuid.UUID, asyncio.Lock] = {}


def user_lock(user_id: uuid.UUID) -> asyncio.Lock:
    return user_locks.setdefault(user_id, asyncio.Lock())


def _utc(value: datetime) -> datetime:
    """SQLite geeft tijden zonder tijdzone terug; ze staan altijd in UTC opgeslagen."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


Utc = Annotated[datetime, AfterValidator(_utc)]


def to_utc(value: datetime) -> datetime:
    """Een tijd van een client naar UTC (zonder tijdzone: dan is het al UTC). SQLite bewaart
    geen tijdzone, dus alles moet in UTC staan om het te kunnen vergelijken."""
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


class TagRef(BaseModel):
    id: uuid.UUID
    name: str
    color: str

    model_config = {"from_attributes": True}


class OwnerRef(BaseModel):
    id: uuid.UUID
    display_name: str


class NodeOut(BaseModel):
    id: uuid.UUID
    # Voor wie het via een deling ziet: null bij de gedeelde map zelf
    parent_id: uuid.UUID | None
    name: str
    kind: str
    mime: str | None
    size: int
    etag: str
    created_at: Utc
    modified_at: Utc
    deleted_at: Utc | None
    # Alleen de tags van wie het vraagt
    tags: list[TagRef] = []
    # Wat wie het vraagt ermee mag: owner, write of read
    permission: str = "owner"
    owner: OwnerRef | None = None
    # Er staat een deling of link op; alleen de eigenaar ziet dat (voor de rest altijd false)
    shared: bool = False

    model_config = {"from_attributes": True}


class Listing(BaseModel):
    folder: NodeOut
    # Van de hoofdmap (of de gedeelde map) tot en met folder
    path: list[NodeOut]
    children: list[NodeOut]
    # De cursor voor de volgende pagina (?after=), of null: dit was de laatste (of alles)
    next: str | None = None
    # Hoeveel kinderen er in totaal zijn (met ?kind= alleen van dat soort)
    total: int = 0


class Lookup(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=500)


class Found(BaseModel):
    # Wat de vrager nu kan zien; wat weg is, in de prullenbak ligt of onzichtbaar werd, ontbreekt
    nodes: list[NodeOut]


class NewFolder(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class Change(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None


class CopyTo(BaseModel):
    parent_id: uuid.UUID
    name: str | None = Field(default=None, min_length=1, max_length=255)


def store(settings: Annotated[Settings, Depends(get_settings)]) -> BlobStore:
    return BlobStore(settings.blob_dir)


Store = Annotated[BlobStore, Depends(store)]


class NameTaken(HTTPException):
    """409 met code name_taken: opnieuw proberen heeft geen zin."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        e = errors.fail(409, "name_taken", "Er bestaat al iets met deze naam", headers=headers)
        super().__init__(e.status_code, e.detail, e.headers)


class Gone(HTTPException):
    """De map waar iets in zou komen, is er niet meer voor wie het doet: weg, in de
    prullenbak, of de deling of link is ingetrokken."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        e = errors.fail(404, "folder_not_found", "Map niet gevonden", headers=headers)
        super().__init__(e.status_code, e.detail, e.headers)


def clean_name(name: str) -> str:
    name = name.strip()
    if (
        not name
        or name in (".", "..")
        or FORBIDDEN.search(name)
        or CONTROL.search(name)
        or len(name) > MAX_NAME
    ):
        raise errors.fail(422, "invalid_name", "Ongeldige naam")
    return name


Need = Literal["read", "write", "owner"]


def share_root(found: "Access") -> HTTPException:
    """403 share_root: de vrager ziet dit via een deling op de node zelf."""
    grant = found.grant
    if grant is not None and grant.root_id == found.node.id and found.node.kind == "dir":
        return errors.fail(403, **SHARE_ROOT)
    return errors.fail(403, **SHARED_ITSELF)


@dataclass
class Access:
    """Een node en wat de vrager ermee mag. grant is gezet als het via een deling gaat."""

    node: Node
    permission: str
    grant: Grant | None = None

    @property
    def owner(self) -> bool:
        return self.permission == "owner"

    @property
    def roots(self) -> set[uuid.UUID]:
        """De gedeelde map van de vrager (daarvan ziet hij geen parent_id)."""
        return {self.grant.root_id} if self.grant else set()

    def child(self, node: Node) -> "Access":
        """Wat er net in deze map bij kwam: zelfde recht (er staat nog geen deling op)."""
        if self.grant is None:
            return Access(node, self.permission)
        chain = [*self.grant.chain, Step(node.id, node.name, self.permission)]
        return Access(node, self.permission, Grant(self.permission, self.grant.root_id, chain))


async def get_access(
    session: AsyncSession, user: User, node_id: uuid.UUID, need: Need = "owner"
) -> Access:
    """De enige toegangscontrole voor een map of bestand. Niet zichtbaar: 404 (dezelfde als
    voor iets dat niet bestaat, zodat niemand ids kan aftasten). Wel zichtbaar maar te weinig
    recht: 403 read_only of owner_only. De eigenaar ziet ook wat in zijn prullenbak ligt;
    wie het via een deling ziet niet."""
    node = await session.get(Node, node_id)
    if node is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    if node.owner_id == user.id:
        if user.role == "guest":
            raise errors.fail(403, **GUEST)
        return Access(node, "owner")
    grant = (await access.resolve(session, user.id, [node.id])).get(node.id)
    if grant is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    if need == "owner":
        raise errors.fail(403, **OWNER_ONLY)
    if need == "write" and grant.permission != "write":
        raise errors.fail(403, **READ_ONLY)
    return Access(node, grant.permission, grant)


async def get_node(
    session: AsyncSession, user: User, node_id: uuid.UUID, need: Need = "owner"
) -> Node:
    return (await get_access(session, user, node_id, need)).node


ALBUM = {
    "message": "Een album is geen map of bestand in je bestanden; dat kan hier niet",
    "code": "album",
}


async def get_tree_access(
    session: AsyncSession, user: User, node_id: uuid.UUID, need: Need = "owner"
) -> Access:
    """get_access voor de endpoints van de boom (hernoemen, verplaatsen, kopiëren, prullenbak,
    tags, versies, inhoud): een album staat buiten de boom en geeft 422 album. get_access zelf
    laat albums door (delen gaat er wel mee)."""
    found = await get_access(session, user, node_id, need)
    if found.node.kind == "album":
        raise errors.fail(422, **ALBUM)
    return found


async def get_tree_accesses(
    session: AsyncSession, user: User, node_ids: Sequence[uuid.UUID], need: Need = "owner"
) -> dict[uuid.UUID, Access | HTTPException]:
    """get_tree_access voor veel ids tegelijk (bulkacties): per id de Access of de fout die
    get_tree_access zou geven. Twee queries, hoeveel ids het ook zijn: de nodes zelf en één
    access.resolve voor wat van anderen is."""
    ids = list(dict.fromkeys(node_ids))
    nodes = {}
    for chunk in batched(ids, 1000):
        rows = await session.scalars(select(Node).where(Node.id.in_(chunk)))
        nodes.update({n.id: n for n in rows})
    others = [n.id for n in nodes.values() if n.owner_id != user.id]
    grants = await access.resolve(session, user.id, others) if others else {}
    result: dict[uuid.UUID, Access | HTTPException] = {}
    for node_id in ids:
        node = nodes.get(node_id)
        if node is None:
            result[node_id] = errors.fail(404, "not_found", "Niet gevonden")
            continue
        if node.owner_id == user.id:
            found: Access | HTTPException = (
                errors.fail(403, **GUEST) if user.role == "guest" else Access(node, "owner")
            )
        else:
            grant = grants.get(node_id)
            if grant is None:
                found = errors.fail(404, "not_found", "Niet gevonden")
            elif need == "owner":
                found = errors.fail(403, **OWNER_ONLY)
            elif need == "write" and grant.permission != "write":
                found = errors.fail(403, **READ_ONLY)
            else:
                found = Access(node, grant.permission, grant)
        if isinstance(found, Access) and node.kind == "album":
            found = errors.fail(422, **ALBUM)
        result[node_id] = found
    return result


async def trashed_among(session: AsyncSession, node_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    """Welke van deze nodes zelf in de prullenbak liggen, of onder een map in de prullenbak (of
    niet meer bestaan niet: die ontbreken gewoon). Eén recursieve query omhoog per 1000 ids."""
    gone: set[uuid.UUID] = set()
    for chunk in batched(list(node_ids), 1000):
        up = access.ancestry(Node.id.in_(chunk))
        rows = await session.scalars(
            select(up.c.origin).where(up.c.deleted_at.is_not(None)).distinct()
        )
        gone.update(rows.all())
    return gone


async def get_dir(
    session: AsyncSession, user: User, node_id: uuid.UUID, need: Need = "read"
) -> Access:
    found = await get_access(session, user, node_id, need)
    if found.node.kind != "dir" or found.node.deleted_at is not None:
        raise errors.fail(404, "folder_not_found", "Map niet gevonden")
    return found


async def ancestors(session: AsyncSession, node: Node) -> list[Node]:
    """Van de root tot en met node."""
    chain = [node]
    while chain[-1].parent_id is not None:
        parent = await session.get(Node, chain[-1].parent_id)
        if parent is None:
            break
        chain.append(parent)
    return list(reversed(chain))


async def is_trashed(session: AsyncSession, node: Node) -> bool:
    return any(n.deleted_at is not None for n in await ancestors(session, node))


async def get_live_dir(
    session: AsyncSession, user: User, node_id: uuid.UUID, need: Need = "write"
) -> Access:
    """Een map waar iets in mag komen: ook niet ergens onder een map in de prullenbak."""
    found = await get_dir(session, user, node_id, need)
    # Via een deling is dat al gecontroleerd (de hele keten omhoog)
    if found.owner and await is_trashed(session, found.node):
        raise errors.fail(404, "folder_not_found", "Map niet gevonden")
    return found


def writable(user_id: uuid.UUID, folder_id: uuid.UUID) -> Callable[[AsyncSession], Awaitable]:
    """Controle vlak voor het neerzetten (onder de sloten): mag deze gebruiker nog steeds in
    deze map (of dit bestand) van een ander schrijven? De deling kan intussen zijn ingetrokken
    of de map in de prullenbak zijn beland."""

    async def check(session: AsyncSession) -> None:
        grant = (await access.resolve(session, user_id, [folder_id])).get(folder_id)
        if grant is None or grant.permission != "write":
            raise Gone

    return check


async def sibling(session: AsyncSession, parent_id: uuid.UUID, name: str) -> Node | None:
    # Vers uit de database: wie op een slot wachtte, mag niet op een oude kopie beslissen
    return await session.scalar(
        select(Node)
        .where(
            Node.parent_id == parent_id,
            func.lower(Node.name) == name.lower(),
            Node.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )


def numbered(name: str, i: int) -> str:
    """x.txt -> x (i).txt; een lange naam wordt ingekort tot het binnen MAX_NAME past."""
    stem, dot, ext = name.rpartition(".")
    if not stem:
        stem, dot, ext = name, "", ""
    tail = f" ({i}){dot}{ext}"
    if len(tail) > MAX_NAME // 2:  # een heel lange "extensie" is gewoon deel van de naam
        stem, tail = name, f" ({i})"
    return stem[: MAX_NAME - len(tail)].rstrip() + tail


async def free_name(session: AsyncSession, parent_id: uuid.UUID, name: str) -> str:
    if await sibling(session, parent_id, name) is None:
        return name
    for i in range(1, 10_000):
        candidate = numbered(name, i)
        if await sibling(session, parent_id, candidate) is None:
            return candidate
    raise errors.fail(409, "no_free_name", "Geen vrije naam gevonden")


def tree(start: uuid.UUID | Collection[uuid.UUID], live: bool = False):
    """Recursieve CTE met (id, depth) van start (één id of meer) en alles eronder. live: niet
    verder dan wat in de prullenbak ligt (dat, en alles daaronder, komt er niet in)."""
    ids = [start] if isinstance(start, uuid.UUID) else list(start)
    top = select(Node.id, literal(0, Integer).label("depth")).where(Node.id.in_(ids))
    found = top.cte("tree", recursive=True)
    child = aliased(Node)
    step = select(child.id, found.c.depth + 1).where(child.parent_id == found.c.id)
    if live:
        step = step.where(child.deleted_at.is_(None))
    return found.union_all(step)


async def subtree_rows(
    session: AsyncSession, node_id: uuid.UUID, *columns, live: bool = False, limit: int = 0
) -> list:
    """Deze kolommen van node_id en alles eronder in één query, ouders voor hun kinderen.
    Rijen, geen ORM-objecten, en in stukken binnengehaald: duizenden rijen in één keer
    omzetten houdt de event loop (en daarmee elk ander verzoek) te lang bezig. limit: hooguit
    zoveel rijen."""
    found = tree(node_id, live)
    query = select(*columns).join(found, Node.id == found.c.id).order_by(found.c.depth)
    if limit:
        query = query.limit(limit)
    rows = []
    async for part in (await session.stream(query)).partitions(500):
        rows.extend(part)
    return rows


async def descendants(session: AsyncSession, node: Node) -> list:
    """(id, blob_id) van node en alles eronder, ouders voor hun kinderen."""
    return await subtree_rows(session, node.id, Node.id, Node.blob_id)


async def tags_by_node(
    session: AsyncSession, user: User, node_ids: Iterable[uuid.UUID] | Select
) -> dict[uuid.UUID, list[TagRef]]:
    """De tags van deze gebruiker op deze nodes, in één query. node_ids mag ook een
    subquery zijn (bijv. alle kinderen van een map)."""
    if not isinstance(node_ids, Select):
        node_ids = list(node_ids)
        if not node_ids:
            return {}
    rows = await session.execute(
        select(NodeTag.node_id, Tag)
        .join(Tag, Tag.id == NodeTag.tag_id)
        .where(Tag.owner_id == user.id, NodeTag.node_id.in_(node_ids))
    )
    result: dict[uuid.UUID, list[TagRef]] = {}
    for node_id, tag in rows:
        result.setdefault(node_id, []).append(TagRef.model_validate(tag))
    for tags in result.values():
        tags.sort(key=tag_order)
    return result


def tag_order(tag: TagRef | Tag) -> tuple[str, str]:
    """Op naam zonder hoofdletters en accenten (in Python: lower() in SQLite kent alleen ASCII)."""
    return fold(tag.name), str(tag.id)


async def owner_refs(
    session: AsyncSession, user: User, owner_ids: set[uuid.UUID]
) -> dict[uuid.UUID, OwnerRef]:
    """Naam van elke eigenaar; alleen een query als er iets van een ander bij zit."""
    refs = {user.id: OwnerRef(id=user.id, display_name=user.display_name)}
    others = owner_ids - {user.id}
    if others:
        rows = await session.execute(select(User.id, User.display_name).where(User.id.in_(others)))
        refs.update({uid: OwnerRef(id=uid, display_name=name) for uid, name in rows})
    return refs


async def shared_ids(
    session: AsyncSession, node_ids: Iterable[uuid.UUID] | Select
) -> set[uuid.UUID]:
    """Welke van deze nodes een deling of link hebben, in één query."""
    if not isinstance(node_ids, Select):
        node_ids = list(node_ids)
        if not node_ids:
            return set()
    rows = await session.scalars(select(Share.node_id).where(Share.node_id.in_(node_ids)))
    return set(rows.all())


async def describe(
    session: AsyncSession,
    user: User,
    nodes: Sequence[Node],
    permission: str | dict[uuid.UUID, str],
    *,
    tags: dict[uuid.UUID, list[TagRef]] | None = None,
    roots: Collection[uuid.UUID] = (),
    scope: Select | None = None,
) -> list[NodeOut]:
    """NodeOut van deze nodes zoals `user` ze ziet. Een vast aantal queries, hoeveel nodes het
    ook zijn: de tags (tenzij meegegeven), namen van eigenaren (alleen als er iets van een
    ander bij zit) en welke gedeeld zijn (alleen voor wat van user zelf is). roots: de
    gedeelde mappen van user, die geen parent_id krijgen. scope: een subquery met dezelfde
    ids, voor een lange lijst."""
    if not nodes:
        return []
    ids = scope if scope is not None else [n.id for n in nodes]
    if tags is None:
        tags = await tags_by_node(session, user, ids)
    owners = await owner_refs(session, user, {n.owner_id for n in nodes})
    mine = any(n.owner_id == user.id for n in nodes)
    shared = await shared_ids(session, ids) if mine else set()
    result = []
    construct = NodeOut.model_construct
    for node in nodes:
        owner_id = node.owner_id
        deleted_at = node.deleted_at
        # Zonder validatie (model_construct): de waarden komen uit de database en hebben al
        # het goede type; per node valideren kost bij 10.000 kinderen honderden ms. Dezelfde
        # uitvoer als NodeOut.model_validate(node) (zie test_listing_paging.py).
        result.append(
            construct(
                id=node.id,
                parent_id=None if node.id in roots else node.parent_id,
                name=node.name,
                kind=node.kind,
                mime=node.mime,
                size=node.size,
                etag=f'"{node.blob_id or node.id.hex}"',
                created_at=_utc(node.created_at),
                modified_at=_utc(node.modified_at),
                deleted_at=None if deleted_at is None else _utc(deleted_at),
                tags=tags.get(node.id, []),
                permission=permission if isinstance(permission, str) else permission[node.id],
                owner=owners.get(owner_id) or OwnerRef(id=owner_id, display_name=""),
                shared=owner_id == user.id and node.id in shared,
            )
        )
    return result


async def view(session: AsyncSession, user: User, node: Node) -> Access:
    """Wat user nu met node mag, zonder fout als het niets is (dan als read, zonder
    parent_id): voor het antwoord na een wijziging."""
    if node.owner_id == user.id:
        return Access(node, "owner")
    grant = (await access.resolve(session, user.id, [node.id])).get(node.id)
    if grant is None:
        return Access(node, "read", Grant("read", node.id, [Step(node.id, node.name, "read")]))
    return Access(node, grant.permission, grant)


async def one_out(
    session: AsyncSession, user: User, node: Node, found: Access | None = None
) -> NodeOut:
    if found is None or found.node is not node:
        found = await view(session, user, node)
    [out] = await describe(session, user, [node], found.permission, roots=found.roots)
    return out


async def shared_below(session: AsyncSession, node: Node) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Per gebruiker de mappen en bestanden onder deze map (niet de map zelf, en niets onder
    iets in de prullenbak) met een deling voor hem of een groep van hem: zijn gedeelde mappen
    daar. Eén query; voor een bestand geen."""
    if node.kind != "dir":
        return {}
    found = tree(node.id, live=True)
    rows = await session.execute(
        select(Share.node_id, Share.grantee_id, GroupMember.user_id)
        .select_from(Share)
        .join(found, Share.node_id == found.c.id)
        .outerjoin(GroupMember, GroupMember.group_id == Share.group_id)
        .where(found.c.depth > 0, access.live_share())
    )
    result: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for node_id, grantee_id, member_id in rows:
        user_id = grantee_id or member_id
        if user_id is not None and user_id != node.owner_id:
            result[user_id].add(node_id)
    return result


async def notify(
    session: AsyncSession,
    node: Node,
    type: str,
    old_parent_id: uuid.UUID | None = None,
    seen: dict[uuid.UUID, dict[uuid.UUID, int]] | None = None,
    below: dict[uuid.UUID, set[uuid.UUID]] | None = None,
) -> None:
    """Na de commit: open web-apps en apps laten weten dat de boom veranderde. Naar de
    eigenaar en naar iedereen die de node via een deling kan zien (bij verplaatsen ook wie de
    oude plek kon zien), ook als hij in de prullenbak ligt. Wie de map erboven niet ziet,
    krijgt parent_id null. seen: van access.watchers, vooraf uitgerekend als de node na de
    commit weg is. below: van shared_below, bij weggooien, terugzetten en definitief
    verwijderen van een map: wie alleen een gedeelde map daaronder heeft, raakt die kwijt of
    krijgt hem terug, en hoort dat over die gedeelde map zelf (zonder iets erboven)."""
    moved = old_parent_id is not None and old_parent_id != node.parent_id
    if seen is None:
        seen = await access.watchers(session, [node.id, old_parent_id if moved else None])
    parent_id = str(node.parent_id) if node.parent_id else None
    data = {"id": str(node.id), "parent_id": parent_id}
    if moved:
        data["old_parent_id"] = str(old_parent_id)
    events.publish([node.owner_id], type, data)
    here = seen.get(node.id, {})
    there = seen.get(old_parent_id, {}) if moved else {}
    for user_id in (here.keys() | there.keys()) - {node.owner_id}:
        # depth >= 1: gedeeld vanaf een map boven de node, dus die map ziet hij
        part = {"id": str(node.id), "parent_id": parent_id if here.get(user_id, 0) >= 1 else None}
        if user_id in there:
            part["old_parent_id"] = str(old_parent_id)
        events.publish([user_id], type, part)
    told = here.keys() | there.keys() | {node.owner_id}
    for user_id, roots in (below or {}).items():
        if user_id not in told:
            for root_id in roots:
                events.publish([user_id], type, {"id": str(root_id), "parent_id": None})


async def notify_many(
    session: AsyncSession,
    nodes: Sequence[Node],
    type: str,
    *,
    old_parents: Mapping[uuid.UUID, uuid.UUID | None] | None = None,
    seen: dict[uuid.UUID, dict[uuid.UUID, int]] | None = None,
    below: Mapping[uuid.UUID, dict[uuid.UUID, set[uuid.UUID]]] | None = None,
) -> None:
    """notify() voor veel nodes tegelijk (bulkacties), na de commit. Dezelfde ontvangers en
    dezelfde zichtbaarheid van parent_id als notify(), maar één event per (ontvanger, ouder,
    oude ouder) met {"ids": [≤500], "parent_id", "old_parent_id"?}; zit er maar één node in
    zo'n groep, dan het oude formaat {"id", "parent_id", ...}. old_parents: per node de ouder
    van voor het verplaatsen. seen: van access.watchers over de nodes en de oude ouders,
    vooraf uitgerekend als de nodes na de commit weg zijn. below: per node het resultaat van
    shared_below (bij weggooien, terugzetten en definitief verwijderen van mappen)."""
    if not nodes:
        return
    old_parents = old_parents or {}
    moved = {
        n.id: old
        for n in nodes
        if (old := old_parents.get(n.id)) is not None and old != n.parent_id
    }
    if seen is None:
        seen = await access.watchers(session, [*(n.id for n in nodes), *moved.values()])
    groups: dict[tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None], list[uuid.UUID]]
    groups = defaultdict(list)
    told: dict[uuid.UUID, set[uuid.UUID]] = {}
    for node in nodes:
        old_parent_id = moved.get(node.id)
        here = seen.get(node.id, {})
        there = seen.get(old_parent_id, {}) if old_parent_id is not None else {}
        groups[node.owner_id, node.parent_id, old_parent_id].append(node.id)
        for user_id in (here.keys() | there.keys()) - {node.owner_id}:
            # depth >= 1: gedeeld vanaf een map boven de node, dus die map ziet hij
            parent_id = node.parent_id if here.get(user_id, 0) >= 1 else None
            old_id = old_parent_id if user_id in there else None
            groups[user_id, parent_id, old_id].append(node.id)
        told[node.id] = here.keys() | there.keys() | {node.owner_id}
    for node_id, users in (below or {}).items():
        for user_id, roots in users.items():
            if user_id not in told.get(node_id, set()):
                groups[user_id, None, None].extend(roots)
    for (user_id, parent_id, old_parent_id), ids in groups.items():
        for chunk in batched(dict.fromkeys(ids), 500):
            data: dict = (
                {"id": str(chunk[0])} if len(chunk) == 1 else {"ids": [str(i) for i in chunk]}
            )
            data["parent_id"] = str(parent_id) if parent_id else None
            if old_parent_id is not None:
                data["old_parent_id"] = str(old_parent_id)
            events.publish([user_id], type, data)


MAX_PAGE = 5000
SEP = "\x1f"


def cursor_of(node: Node) -> str:
    """De cursor na deze rij: base64url van "<kind>\\x1f<search_name>\\x1f<id>" (de volgorde
    van de lijst), zonder opvulling."""
    raw = f"{node.kind}{SEP}{node.search_name}{SEP}{node.id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def read_cursor(cursor: str) -> tuple[str, str, uuid.UUID]:
    """(kind, search_name, id) uit een cursor; 422 invalid_cursor als hij niet klopt."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        kind, rest = raw.split(SEP, 1)
        search_name, node_id = rest.rsplit(SEP, 1)
        return kind, search_name, uuid.UUID(node_id)
    except (ValueError, binascii.Error):
        raise errors.fail(422, "invalid_cursor", "Ongeldige cursor voor de lijst") from None


Limit = Annotated[int | None, Query(ge=1, le=MAX_PAGE)]
KindFilter = Annotated[Literal["dir", "file"] | None, Query()]


@router.get("/files/root")
async def root(
    user: CurrentUser,
    session: Session,
    limit: Limit = None,
    after: str | None = None,
    kind: KindFilter = None,
) -> Listing:
    return await listing(user.root_id, user, session, limit, after, kind)


@router.get("/files/{node_id}/children")
async def listing(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    limit: Limit = None,
    after: str | None = None,
    kind: KindFilter = None,
) -> Listing:
    """De inhoud van een map: mappen eerst, dan op naam zonder hoofdletters en accenten (de
    volgorde van de server). Met limit in pagina's: next is de cursor voor ?after= van de
    volgende pagina (null bij de laatste), total het aantal kinderen. Zonder limit alles in
    één keer (next null). kind: alleen mappen (dir) of alleen bestanden (file)."""
    found = await get_dir(session, user, node_id, "read")
    folder = found.node
    live = [Node.parent_id == folder.id, Node.deleted_at.is_(None)]
    if kind is not None:
        live.append(Node.kind == kind)
    query = select(Node).where(*live)
    if after is not None:
        k, name, last = read_cursor(after)
        query = query.where(
            tuple_(Node.kind, Node.search_name, Node.id)
            > tuple_(literal(k), literal(name), literal(last, Uuid))
        )
    # Op de naam zonder hoofdletters en accenten: lower() in SQLite kent alleen ASCII
    query = query.order_by(Node.kind, Node.search_name, Node.id)
    if limit is not None:
        query = query.limit(limit + 1)
    children = list((await session.scalars(query)).all())
    more = limit is not None and len(children) > limit
    if more:
        children = children[:limit]
    if limit is None and after is None:
        total = len(children)
    else:
        total = await session.scalar(select(func.count()).select_from(Node).where(*live)) or 0
    if limit is None:
        # Alles (of alles na de cursor): de tags in één query over de hele map
        scope: Select | list[uuid.UUID] = select(Node.id).where(
            or_(Node.parent_id == folder.id, Node.id == folder.id)
        )
    else:
        scope = [folder.id, *(c.id for c in children)]
    tags = await tags_by_node(session, user, scope)
    permissions: str | dict[uuid.UUID, str] = found.permission
    if found.owner:
        path = await describe(session, user, await ancestors(session, folder), "owner", tags={})
    else:
        # Vanaf de gedeelde map: wat erboven ligt, ziet deze gebruiker niet
        steps = found.grant.chain
        loaded = (
            await session.scalars(select(Node).where(Node.id.in_([s.id for s in steps])))
        ).all()
        by_id = {n.id: n for n in loaded}
        path = await describe(
            session,
            user,
            [by_id[s.id] for s in steps if s.id in by_id],
            {s.id: s.permission for s in steps},
            tags={},
            roots=found.roots,
        )
        # Kinderen krijgen het recht van de map, of meer als ze zelf met hem gedeeld zijn (alleen
        # wat niet in de prullenbak ligt: dat staat ook niet in de lijst)
        live_ids = select(Node.id).where(Node.parent_id == folder.id, Node.deleted_at.is_(None))
        own = await session.execute(
            select(Share.node_id, Share.permission).where(
                Share.node_id.in_(live_ids), access.live_share(), access.for_user(user.id)
            )
        )
        permissions = {c.id: found.permission for c in children}
        permissions[folder.id] = found.permission
        for child_id, permission in own:
            if child_id in permissions:  # alleen wat in de lijst staat
                permissions[child_id] = access.better(permissions[child_id], permission)
    described = await describe(
        session,
        user,
        [folder, *children],
        permissions,
        tags=tags,
        roots=found.roots,
        scope=scope if isinstance(scope, Select) else None,
    )
    return Listing(
        folder=described[0],
        path=path,
        children=described[1:],
        next=cursor_of(children[-1]) if more else None,
        total=total,
    )


@router.post("/files/lookup")
async def lookup(body: Lookup, user: CurrentUser, session: Session) -> Found:
    """Deze mappen en bestanden (hooguit 500) zoals de vrager ze nu ziet, met dezelfde toegang
    als GET /files/{id}, maar zonder wat in de prullenbak ligt of onder een map in de prullenbak
    (voor de lijst van een open map: zo komen live wijzigingen binnen zonder alles opnieuw te
    laden). Wat er niet (meer) is of onzichtbaar werd, ontbreekt."""
    ids = list(dict.fromkeys(body.ids))
    if not ids:
        return Found(nodes=[])
    rows = (
        await session.scalars(select(Node).where(Node.id.in_(ids), Node.deleted_at.is_(None)))
    ).all()
    mine = [n for n in rows if n.owner_id == user.id and user.role != "guest"]
    # Eén query omhoog voor de eigen nodes: ligt er een map erboven in de prullenbak?
    gone = await trashed_among(session, [n.id for n in mine]) if mine else set()
    others = [n for n in rows if n.owner_id != user.id]
    # Via een deling: access.resolve laat al weg wat in (of onder) de prullenbak ligt
    grants = await access.resolve(session, user.id, [n.id for n in others]) if others else {}
    permissions: dict[uuid.UUID, str] = {n.id: "owner" for n in mine if n.id not in gone}
    roots = set()
    for node_id, grant in grants.items():
        permissions[node_id] = grant.permission
        if grant.root_id == node_id:
            roots.add(node_id)
    order = {node_id: i for i, node_id in enumerate(ids)}
    visible = sorted((n for n in rows if n.id in permissions), key=lambda n: order[n.id])
    return Found(nodes=await describe(session, user, visible, permissions, roots=roots))


@router.get("/files/{node_id}")
async def info(node_id: uuid.UUID, user: CurrentUser, session: Session) -> NodeOut:
    found = await get_access(session, user, node_id, "read")
    return await one_out(session, user, found.node, found)


@router.post("/files/{node_id}/folders", status_code=201)
async def new_folder(
    node_id: uuid.UUID, body: NewFolder, user: CurrentUser, session: Session
) -> NodeOut:
    found = await get_live_dir(session, user, node_id, "write")
    parent = found.node
    name = clean_name(body.name)
    if await sibling(session, parent.id, name):
        raise errors.fail(409, "name_taken", "Er bestaat al iets met deze naam")
    # In een gedeelde map is de nieuwe map van de eigenaar van die map
    folder = Node(parent_id=parent.id, owner_id=parent.owner_id, name=name, kind="dir")
    session.add(folder)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()  # de map is intussen definitief verwijderd
        raise errors.fail(404, "folder_not_found", "Map niet gevonden") from None
    await notify(session, folder, "node.created")
    return await one_out(session, user, folder, found.child(folder))


async def room_left(
    session: AsyncSession, user: User, exclude_upload: uuid.UUID | None = None
) -> int | None:
    """Hoeveel bytes de gebruiker nog mag opslaan, of None bij onbeperkt. Lopende
    hervatbare uploads tellen al mee, zodat twee uploads samen niet over het quotum gaan.
    Ook die van anderen in zijn gedeelde mappen: die worden van hem."""
    if not user.quota_bytes:
        return None
    query = select(func.coalesce(func.sum(Upload.size), 0)).where(
        Upload.owner_id == user.id, Upload.node_id.is_(None)
    )
    if exclude_upload:
        query = query.where(Upload.id != exclude_upload)
    reserved = await session.scalar(query)
    return max(0, user.quota_bytes - await used_bytes(session, user.id) - int(reserved or 0))


def no_room(owner: User, actor_id: uuid.UUID | None) -> HTTPException:
    """413: het quotum is op. Doet een ander het (in een gedeelde map of via een uploadlink),
    dan staat erbij wiens ruimte op is."""
    if actor_id == owner.id:
        return errors.fail(413, "no_room", "Onvoldoende quotum")
    return errors.fail(
        413,
        "no_room_owner",
        f"Er is niet genoeg ruimte meer bij {owner.display_name}; vraag hem of haar om ruimte "
        "te maken",
        owner=owner.display_name,
    )


async def add_ref(session: AsyncSession, sha: str, size: int, mime: str | None = None) -> None:
    """ref_count + 1 in de database zelf, of een nieuwe blob-rij (en dan een taak voor de
    preview, die begint zodra de aanroeper commit). Alleen onder BlobStore.lock."""
    result = await session.execute(
        update(Blob)
        .where(Blob.sha256 == sha)
        .values(ref_count=Blob.ref_count + 1, mime=func.coalesce(Blob.mime, mime))
    )
    if result.rowcount == 0:
        session.add(Blob(sha256=sha, size=size, ref_count=1, mime=mime, preview="pending"))
        await previews.queue(session, sha)


async def drop_ref(session: AsyncSession, sha: str) -> None:
    """ref_count - 1 in de database zelf. Alleen onder BlobStore.lock."""
    await session.execute(
        update(Blob).where(Blob.sha256 == sha).values(ref_count=Blob.ref_count - 1)
    )


async def add_refs(session: AsyncSession, counts: Counter[str], sign: int = 1) -> None:
    """ref_count + n (of - n met sign=-1) voor bestaande blobs: één update per aantal en per
    500 blobs, niet per blob. Alleen onder BlobStore.lock."""
    by_count: dict[int, list[str]] = {}
    for sha, n in counts.items():
        by_count.setdefault(n, []).append(sha)
    for n, shas in by_count.items():
        for chunk in batched(shas, 500):
            await session.execute(
                update(Blob)
                .where(Blob.sha256.in_(chunk))
                .values(ref_count=Blob.ref_count + sign * n)
            )


async def keep_version(
    session: AsyncSession, node: Node, actor: User | None, now: datetime
) -> None:
    """De huidige inhoud van node wordt een versie: de verwijzing (ref_count) gaat mee naar
    de versie. Daarna mogen er hooguit MAX_VERSIONS zijn. actor: wie hem vervangt. Alleen
    onder BlobStore.lock."""
    session.add(
        Version(
            node_id=node.id,
            blob_id=node.blob_id,
            size=node.size,
            created_at=node.modified_at,
            replaced_at=now,
            replaced_by=actor.id if actor else None,
        )
    )
    await session.flush()
    too_old = (
        await session.scalars(
            select(Version)
            .where(Version.node_id == node.id)
            .order_by(Version.replaced_at.desc(), Version.id)
            .offset(MAX_VERSIONS)
        )
    ).all()
    for version in too_old:
        await drop_ref(session, version.blob_id)
        await session.delete(version)


async def place_file(
    session: AsyncSession,
    blobs: BlobStore,
    owner: User,
    parent: Node,
    name: str,
    conflict: str,
    sha: str,
    size: int,
    source: Path,
    upload: Upload | None = None,
    *,
    actor: User | None = None,
    guard: Callable[[AsyncSession], Awaitable] | None = None,
    only: uuid.UUID | None = None,
    client_mtime: datetime | None = None,
    tz: str | None = None,
) -> Node:
    """Zet de inhoud in bestand `source` (SHA-256 `sha`) als bestand `name` in map `parent`.
    conflict: rename = nieuwe naam als het al bestaat, replace = vervangen (de oude inhoud
    blijft als versie bewaard), fail = NameTaken.
    owner: van wie het bestand wordt en wiens quotum het kost (de eigenaar van parent); actor:
    wie het doet (anders dan owner in een gedeelde map, None via een uploadlink). guard: nog
    een controle onder de sloten, vlak voor het neerzetten (mag actor er nog in?). only: alleen
    dit bestand vervangen (met replace); heet het intussen anders of staat het ergens anders,
    dan 409 en verandert er niets.
    Een afbeelding of video krijgt een opnamedatum (photodate.stamp): die uit de inhoud als
    die al bekend is, anders client_mtime (de lastModified bij de client), anders nu; tz is
    settings.timezone (standaard die van get_settings()).
    Gelukt: `source` is weg (verplaatst naar de blob-opslag, of weggegooid omdat die inhoud
    er al was). Mislukt: `source` staat er nog en de database is niet veranderd. Komt het van
    een hervatbare upload, dan wordt die in dezelfde transactie afgerond (het quotum is dan al
    gereserveerd); anders wordt het quotum hier nog eens gecontroleerd, onder hetzelfde
    gebruikersslot als kopiëren en reserveren. Sluit eerst de transactie van de aanroeper af
    (die mag niets openstaan hebben): wie op het slot wacht, houdt geen databaseverbinding
    vast, anders kan wie het slot heeft er geen meer krijgen."""
    # Het soort volgt uit de inhoud; de naam helpt alleen waar de inhoud niets zegt
    mime = sniff(await anyio.to_thread.run_sync(read_head, source), name)
    tz = tz or get_settings().timezone
    moved, created = False, False
    await session.commit()
    async with user_lock(owner.id) if upload is None else nullcontext(), blobs.lock:
        try:
            if await session.scalar(select(Node.id).where(Node.id == parent.id)) is None:
                # Intussen definitief verwijderd
                raise errors.fail(404, "folder_not_found", "Map niet gevonden")
            if guard is not None:
                await guard(session)
            existing = await sibling(session, parent.id, name)
            if existing and conflict == "fail":
                raise NameTaken
            if only is not None and (existing is None or existing.id != only):
                raise errors.fail(409, **MOVED)
            replacing = existing and conflict == "replace" and existing.kind == "file"
            if upload is None and not (replacing and existing.blob_id == sha):
                # Pas nu weet je wat anderen intussen innamen (de vorige inhoud blijft als
                # versie staan, dus ook vervangen kost ruimte)
                room = await room_left(session, owner)
                if room is not None and size > room:
                    raise no_room(owner, actor.id if actor else None)
            now = datetime.now(UTC)
            if replacing:
                if existing.blob_id != sha:
                    await add_ref(session, sha, size, mime)
                    if existing.blob_id:
                        await keep_version(session, existing, actor, now)
                # Dezelfde inhoud als nu: geen nieuwe versie
                existing.blob_id, existing.size, existing.mime = sha, size, mime
                existing.modified_at = now
                node = existing
            else:
                await add_ref(session, sha, size, mime)
                if existing:
                    name = await free_name(session, parent.id, name)
                node = Node(
                    id=uuid.uuid4(),
                    parent_id=parent.id,
                    owner_id=owner.id,
                    name=name,
                    kind="file",
                    mime=mime,
                    size=size,
                    blob_id=sha,
                )
                session.add(node)
                created = True
            blob_taken_at = await session.scalar(select(Blob.taken_at).where(Blob.sha256 == sha))
            photodate.stamp(node, blob_taken_at=blob_taken_at, fallback=client_mtime or now, tz=tz)
            if upload is not None:
                upload.node_id = node.id
            await session.flush()
            node_id = node.id  # na een rollback is het object verlopen
            # Pas nu de schijf; wat hierna nog misgaat, draait give_back() terug
            moved = await blobs.adopt(source, sha)
            await session.commit()
        except BaseException:
            with anyio.CancelScope(shield=True):
                await session.rollback()
                # Terug waar het stond, tenzij de commit toch doorkwam (dan wijst dit bestand
                # naar die inhoud). Niet op de blob-rij afgaan: die kan er al zijn geweest
                # terwijl de inhoud op schijf ontbrak.
                if moved and not await session.scalar(
                    select(Node.id).where(Node.id == node_id, Node.blob_id == sha)
                ):
                    await blobs.give_back(source, sha)
            raise
    await notify(session, node, "node.created" if created else "node.changed")
    if not moved:
        await anyio.Path(source).unlink(missing_ok=True)  # die inhoud stond er al
    await collect_garbage(session, blobs)
    return node


@router.put("/files/{node_id}/upload", status_code=201)
async def upload(
    node_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
    name: Annotated[str, Query(min_length=1, max_length=255)],
    conflict: Literal["rename", "replace", "fail"] = "rename",
    mtime: int | None = None,
    content_length: Annotated[int | None, Header()] = None,
) -> NodeOut:
    """Uploadt de request-body in één keer als bestand `name` in map `node_id`.
    Voor grote bestanden is /api/v1/tus (hervatbaar) beter. mtime: de lastModified van het
    bestand bij de client in milliseconden, voor de opnamedatum van een foto of video.
    Dezelfde inhoud wordt maar één keer opgeslagen (SHA-256). Met conflict=replace blijft
    de vorige inhoud bewaard als versie. In een gedeelde map (met schrijfrecht) wordt het
    bestand van de eigenaar van die map, en kost het zijn quotum."""
    found = await get_live_dir(session, user, node_id, "write")
    parent = found.node
    owner = user if found.owner else await session.get(User, parent.owner_id)
    name = clean_name(name)
    tmp, sha, size = await receive_body(
        session, settings, blobs, request, owner, user, content_length
    )
    guard = None if found.owner else writable(user.id, parent.id)
    try:
        node = await place_file(
            session,
            blobs,
            owner,
            parent,
            name,
            conflict,
            sha,
            size,
            tmp,
            actor=user,
            guard=guard,
            client_mtime=photodate.from_ms(mtime),
            tz=settings.timezone,
        )
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)  # alleen nog over als het misging
    return await one_out(session, user, node)


async def receive_body(
    session: AsyncSession,
    settings: Settings,
    blobs: BlobStore,
    request: Request,
    owner: User,
    actor: User,
    content_length: int | None,
) -> tuple[Path, str, int]:
    """De request-body in een tijdelijk bestand: (pad, SHA-256, grootte). Niet meer dan de
    upload-limiet en wat er nog in het quotum van owner past."""
    limit = settings.max_upload_bytes
    room = await room_left(session, owner)
    if room is not None:
        if content_length is not None and content_length > room:
            raise no_room(owner, actor.id)
        limit = min(limit, room) if limit else room
        if limit == 0 and content_length != 0:  # 0 betekent voor receive() onbeperkt
            raise no_room(owner, actor.id)
    if content_length is not None and limit and content_length > limit:
        raise errors.fail(413, "too_large", "Bestand is te groot")
    await session.commit()  # geen databaseverbinding vasthouden terwijl de body binnenkomt
    try:
        return await blobs.receive(request.stream(), limit)
    except TooLarge as e:
        raise errors.fail(413, "too_large_or_quota", "Bestand is te groot of quotum is op") from e
    except TimeoutError as e:
        raise errors.fail(
            408, "body_timeout", "Er kwam te lang niets binnen; probeer het opnieuw"
        ) from e


@router.put("/files/{node_id}/content")
async def replace_content(
    node_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
    content_length: Annotated[int | None, Header()] = None,
) -> NodeOut:
    """Nieuwe inhoud voor dit bestand, in één keer (de vorige blijft bewaard als versie). Met
    schrijfrecht op het bestand zelf: zo kan ook wie alleen dit ene bestand met zich gedeeld
    kreeg (de map erboven ziet hij niet) er een nieuwe versie van zetten. Het blijft van de
    eigenaar en kost diens quotum."""
    found = await get_tree_access(session, user, node_id, "write")
    node = found.node
    if node.kind != "file":
        raise errors.fail(422, "not_a_file", "Alleen van een bestand kun je de inhoud vervangen")
    if found.owner and await is_trashed(session, node):
        raise errors.fail(422, "trashed", "Zet het bestand eerst terug uit de prullenbak")
    parent = await session.get(Node, node.parent_id)
    owner = user if found.owner else await session.get(User, node.owner_id)
    tmp, sha, size = await receive_body(
        session, settings, blobs, request, owner, user, content_length
    )
    guard = None if found.owner else writable(user.id, node.id)
    try:
        node = await place_file(
            session,
            blobs,
            owner,
            parent,
            node.name,
            "replace",
            sha,
            size,
            tmp,
            actor=user,
            guard=guard,
            only=node.id,
            tz=settings.timezone,
        )
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)  # alleen nog over als het misging
    return await one_out(session, user, node)


@router.patch("/files/{node_id}")
async def change(
    node_id: uuid.UUID, body: Change, user: CurrentUser, session: Session, blobs: Store
) -> NodeOut:
    """Hernoemen en/of verplaatsen. modified_at blijft staan: dat gaat over de inhoud (zoals
    bij een bestandssysteem), en een versie onthoudt het als het moment van die inhoud.
    Verplaatsen kan alleen binnen de bestanden van één eigenaar."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    # Onder het blob-slot, net als definitief verwijderen: anders kan dat iets weggooien dat
    # net uit een weggegooide map werd gehaald, of iets missen dat er net in kwam
    async with blobs.lock:
        found = await get_tree_access(session, user, node_id, "write")
        node = found.node
        if node.parent_id is None:
            raise errors.fail(422, "root", "De hoofdmap kan niet worden gewijzigd")
        if found.grant is not None and found.grant.direct:
            raise share_root(found)
        name = clean_name(body.name) if body.name else node.name
        old_parent_id = node.parent_id
        parent_id = body.parent_id or node.parent_id
        if body.parent_id:
            target = (await get_dir(session, user, body.parent_id, "write")).node
            if target.owner_id != node.owner_id:
                raise errors.fail(422, **CROSS_OWNER)
            chain = await ancestors(session, target)
            if any(a.id == node.id for a in chain):
                raise errors.fail(
                    422, "into_itself", "Een map kan niet in zichzelf worden geplaatst"
                )
            if any(a.deleted_at is not None for a in chain):
                raise errors.fail(404, "folder_not_found", "Map niet gevonden")
        other = await sibling(session, parent_id, name)
        if other and other.id != node.id:
            raise errors.fail(409, "name_taken", "Er bestaat al iets met deze naam")
        node.name, node.parent_id = name, parent_id
        await session.commit()
    await notify(session, node, "node.changed", old_parent_id)
    return await one_out(session, user, node)


@router.post("/files/{node_id}/copy", status_code=201)
async def copy(
    node_id: uuid.UUID, body: CopyTo, user: CurrentUser, session: Session, blobs: Store
) -> NodeOut:
    """Kopieert een bestand, of een map met alles erin (hooguit MAX_COPY mappen en
    bestanden), naar map parent_id. Bestaat de naam daar al, dan krijgt de kopie een vrije
    naam ("x (1)"). De inhoud zelf wordt niet nog eens opgeslagen: de kopie wijst naar
    dezelfde blobs. Versies en tags gaan niet mee. De kopie is van de eigenaar van de map
    waar hij in komt, en kost diens quotum."""
    # Van wie de doelmap is, verandert nooit; het slot van die eigenaar is nodig
    target_owner = await session.scalar(select(Node.owner_id).where(Node.id == body.parent_id))
    await session.commit()  # niet met een databaseverbinding op een slot wachten
    # Quotum controleren en de kopie maken in één keer, en onder het blob-slot: ref_count
    # ophogen gebeurt nooit tegelijk met opruimen. Pas onder de sloten lezen: intussen kan
    # er van alles veranderd zijn.
    async with user_lock(target_owner or user.id), blobs.lock:
        source = await get_tree_access(session, user, node_id, "read")
        node = source.node
        if source.owner and await is_trashed(session, node):
            raise errors.fail(
                422, "trashed", "Wat in de prullenbak ligt, kan niet worden gekopieerd"
            )
        destination = await get_dir(session, user, body.parent_id, "write")
        target = destination.node
        chain = await ancestors(session, target)
        if any(a.id == node.id for a in chain):
            raise errors.fail(422, "into_itself", "Een map kan niet in zichzelf worden gekopieerd")
        if any(a.deleted_at is not None for a in chain):
            raise errors.fail(404, "folder_not_found", "Map niet gevonden")
        owner = user if destination.owner else await session.get(User, target.owner_id)
        name = clean_name(body.name) if body.name else node.name
        originals = await subtree_rows(
            session,
            node.id,
            *(Node.id, Node.parent_id, Node.name, Node.kind, Node.mime, Node.size),
            *(Node.blob_id, Node.modified_at, Node.taken_at, Node.taken_day),
            live=True,
            limit=MAX_COPY + 1,
        )
        if len(originals) > MAX_COPY:
            most = f"{MAX_COPY:,}".replace(",", ".")
            raise errors.fail(
                422,
                "too_many",
                f"Te veel om in één keer te kopiëren (meer dan {most} mappen en bestanden)",
                max=MAX_COPY,
            )
        room = await room_left(session, owner)
        if room is not None and sum(n.size for n in originals if n.kind == "file") > room:
            raise no_room(owner, user.id)
        name = await free_name(session, target.id, name)
        now = datetime.now(UTC)
        new_ids: dict[uuid.UUID, uuid.UUID] = {}

        def copied(n) -> dict:
            top = n.id == node.id
            new_name = name if top else n.name
            new_ids[n.id] = uuid.uuid4()
            return {
                "id": new_ids[n.id],
                "parent_id": target.id if top else new_ids[n.parent_id],
                "owner_id": target.owner_id,
                "name": new_name,
                "search_name": fold(new_name),  # het attribuut-event loopt hier niet
                "kind": n.kind,
                "mime": n.mime,
                "size": n.size,
                "blob_id": n.blob_id,
                "created_at": now,
                "modified_at": n.modified_at,
                "taken_at": n.taken_at,
                "taken_day": n.taken_day,
            }

        # Rechtstreeks in stukken, ouders voor kinderen, en tussendoor de anderen even aan
        # de beurt: één ORM-object per kopie houdt de event loop seconden bezig
        for chunk in batched(originals, 500):
            await session.execute(insert(Node), [copied(n) for n in chunk])
            await asyncio.sleep(0)
        await add_refs(session, Counter(n.blob_id for n in originals if n.blob_id))
        await session.commit()
    top = await session.get(Node, new_ids[node.id])
    await notify(session, top, "node.created")
    return await one_out(session, user, top, destination.child(top))


@router.delete("/files/{node_id}", status_code=204)
async def trash(node_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """Naar de prullenbak (van de eigenaar, ook als iemand met schrijfrecht het weggooit); de
    inhoud blijft bewaard tot de prullenbak wordt geleegd."""
    found = await get_tree_access(session, user, node_id, "write")
    node = found.node
    if node.parent_id is None:
        raise errors.fail(422, "root", "De hoofdmap kan niet worden verwijderd")
    if found.grant is not None and found.grant.direct:
        raise share_root(found)
    node.deleted_at = datetime.now(UTC)
    await session.commit()
    await notify(session, node, "node.deleted", below=await shared_below(session, node))


async def drop_versions(session: AsyncSession, node_ids: list[uuid.UUID]) -> Counter[str]:
    """Haalt alle versies van deze nodes weg en geeft per blob hoeveel verwijzingen dat
    scheelt. Zelf, niet via de cascade van de database: anders blijven de verwijzingen van de
    versies tellen en wordt hun inhoud nooit opgeruimd. Alleen onder BlobStore.lock."""
    refs: Counter[str] = Counter()
    for chunk in batched(node_ids, 500):
        refs.update(
            (await session.scalars(select(Version.blob_id).where(Version.node_id.in_(chunk)))).all()
        )
        await session.execute(
            delete(Version)
            .where(Version.node_id.in_(chunk))
            .execution_options(synchronize_session=False)
        )
    return refs


async def collect_garbage(session: AsyncSession, blobs: BlobStore) -> None:
    """Verwijdert blobs waar niemand meer naar verwijst. De database beslist: alleen een rij
    met ref_count 0 waar ook echt geen bestand of versie naar wijst, gaat weg, en pas als dat
    vastligt ook de inhoud op schijf."""
    async with blobs.lock:
        orphans = (await session.scalars(select(Blob.sha256).where(Blob.ref_count <= 0))).all()
        gone = []
        for sha in orphans:
            result = await session.execute(
                delete(Blob)
                .where(
                    Blob.sha256 == sha,
                    Blob.ref_count <= 0,
                    ~select(Node.id).where(Node.blob_id == sha).exists(),
                    ~select(Version.id).where(Version.blob_id == sha).exists(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount:
                gone.append(sha)
        await session.commit()
        for sha in gone:
            await blobs.delete(sha)
            await previews.discard(blobs, sha)


class UserRef(BaseModel):
    id: uuid.UUID
    display_name: str


class VersionOut(BaseModel):
    id: uuid.UUID
    size: int
    # Wanneer deze inhoud gemaakt werd, en wanneer en door wie hij vervangen werd
    created_at: Utc
    replaced_at: Utc
    replaced_by: UserRef | None


async def get_file(
    session: AsyncSession, user: User, node_id: uuid.UUID, need: Need = "owner"
) -> Node:
    node = (await get_tree_access(session, user, node_id, need)).node
    if node.kind != "file":
        raise errors.fail(422, "not_a_file", "Alleen bestanden hebben versies")
    return node


async def get_version(session: AsyncSession, node: Node, version_id: uuid.UUID) -> Version:
    version = await session.get(Version, version_id, populate_existing=True)
    if version is None or version.node_id != node.id:
        raise errors.fail(404, "version_not_found", "Versie niet gevonden")
    return version


@router.get("/files/{node_id}/versions")
async def versions(node_id: uuid.UUID, user: CurrentUser, session: Session) -> list[VersionOut]:
    """Eerdere inhoud van een bestand, nieuwste eerst."""
    node = await get_file(session, user, node_id, "read")
    rows = await session.execute(
        select(Version, User.id, User.display_name)
        .outerjoin(User, User.id == Version.replaced_by)
        .where(Version.node_id == node.id)
        .order_by(Version.replaced_at.desc(), Version.id)
    )
    return [
        VersionOut(
            id=v.id,
            size=v.size,
            created_at=v.created_at,
            replaced_at=v.replaced_at,
            replaced_by=UserRef(id=uid, display_name=name) if uid else None,
        )
        for v, uid, name in rows
    ]


@router.post("/files/{node_id}/versions/{version_id}/restore")
async def restore_version(
    node_id: uuid.UUID,
    version_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
) -> NodeOut:
    """Zet een eerdere inhoud terug; wat er nu in staat, wordt zelf een versie. De
    opnamedatum volgt de teruggezette inhoud (anders het moment van die versie)."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with blobs.lock:
        node = await get_file(session, user, node_id, "write")
        if await is_trashed(session, node):
            raise errors.fail(422, "trashed", "Zet het bestand eerst terug uit de prullenbak")
        version = await get_version(session, node, version_id)
        changed = version.blob_id != node.blob_id
        if changed:
            # De verwijzingen ruilen alleen van plaats: ref_count blijft gelijk
            head = await anyio.to_thread.run_sync(read_head, blobs.path(version.blob_id))
            now = datetime.now(UTC)
            session.add(
                Version(
                    node_id=node.id,
                    blob_id=node.blob_id,
                    size=node.size,
                    created_at=node.modified_at,
                    replaced_at=now,
                    replaced_by=user.id,
                )
            )
            node.blob_id, node.size = version.blob_id, version.size
            node.mime = sniff(head, node.name)
            node.modified_at = now
            taken = await session.scalar(select(Blob.taken_at).where(Blob.sha256 == node.blob_id))
            photodate.stamp(
                node, blob_taken_at=taken, fallback=version.created_at, tz=settings.timezone
            )
            await session.delete(version)
        await session.commit()
    if changed:
        await notify(session, node, "node.changed")
    return await one_out(session, user, node)


@router.delete("/files/{node_id}/versions/{version_id}", status_code=204)
async def delete_version(
    node_id: uuid.UUID,
    version_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    blobs: Store,
) -> None:
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with blobs.lock:
        node = await get_file(session, user, node_id, "owner")
        version = await get_version(session, node, version_id)
        await drop_ref(session, version.blob_id)
        await session.delete(version)
        await session.commit()
    await collect_garbage(session, blobs)


class DownloadLink(BaseModel):
    url: str
    expires_in: int


@router.post("/files/{node_id}/download-link")
async def download_link(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    version: uuid.UUID | None = None,
) -> DownloadLink:
    """Kortlevende link voor de browser (een <a>- of <video>-tag kan geen header meesturen).
    Met version: de link naar die eerdere inhoud; de versie zit in het ondertekende token.
    De toegang wordt bij het downloaden opnieuw gecontroleerd."""
    node = (await get_tree_access(session, user, node_id, "read")).node
    if node.kind != "file":
        raise errors.fail(422, "not_a_file", "Alleen bestanden kunnen worden gedownload")
    if version is not None:
        await get_version(session, node, version)
    token = download_token(settings, user.id, node.id, version)
    return DownloadLink(
        url=f"/api/v1/download/{node.id}?token={token}",
        expires_in=settings.download_token_minutes * 60,
    )


ACTIVE_TYPES = ("html", "xml", "svg", "javascript", "ecmascript")


async def send_file(
    node: Node,
    settings: Settings,
    if_none_match: str | None,
    inline: bool,
    version: Version | None = None,
) -> Response:
    if node.kind != "file" or not node.blob_id:
        raise errors.fail(422, "not_a_file", "Geen bestand")
    sha = version.blob_id if version is not None else node.blob_id
    etag = f'"{sha}"'
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    path = BlobStore(settings.blob_dir).path(sha)
    headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    if version is not None:
        # Het soort van die inhoud zelf, met de naam van nu (zoals bij terugzetten)
        mime = sniff(await anyio.to_thread.run_sync(read_head, path), node.name)
    else:
        mime = node.mime or "application/octet-stream"
    if any(t in mime for t in ACTIVE_TYPES):
        # Geüploade HTML/SVG mag geen scripts draaien onder het adres van het platform
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(
        path,
        media_type=mime,
        filename=node.name,
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )


@router.get("/download/{node_id}")
async def download(
    node_id: uuid.UUID,
    token: str,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    inline: bool = False,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    user_id, token_node, version_id = read_download_token(settings, token)
    if token_node != node_id:
        raise errors.fail(403, "wrong_token", "Token hoort bij een ander bestand")
    # Weg of geblokkeerd: 404, ook met een link die al uit was
    user = await active_user(session, user_id)
    # Opnieuw: een deling die intussen is ingetrokken, stopt ook een link die al uit was
    node = (await get_tree_access(session, user, node_id, "read")).node
    version = await get_version(session, node, version_id) if version_id else None
    return await send_file(node, settings, if_none_match, inline, version)


@router.get("/files/{node_id}/content")
async def content(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    inline: bool = False,
    version: uuid.UUID | None = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Download met een gewone Authorization-header (voor apps); ondersteunt Range en ETag.
    Met version: die eerdere inhoud."""
    node = (await get_tree_access(session, user, node_id, "read")).node
    found = await get_version(session, node, version) if version else None
    return await send_file(node, settings, if_none_match, inline, found)
