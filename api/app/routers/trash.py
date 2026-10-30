"""De prullenbak: bekijken, terugzetten (ook in bulk) en definitief verwijderen.

list_trash, restore en purge zijn ongewijzigd verhuisd uit routers/files.py (zelfde paden).
POST /trash/restore is het contract van P0 voor terugzetten in bulk (ook voor "ongedaan
maken" in de web-app). Daarbij (P6):

- GET /trash/overview: alles in de prullenbak met waar het stond ("Was in"), de grootte
  (van een map: alles eronder) en of terugzetten naar de oude plek kan;
- POST /trash/purge (veel tegelijk) en DELETE /trash (legen): per 500 één transactie onder
  BlobStore.lock, daarna collect_garbage;
- de bewaartermijn (Setting trash_retention_days, ingesteld op /beheer): het opruimwerk
  haalt elk uur weg wat langer geleden is weggegooid, op dezelfde manier.
"""

import logging
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import batched

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app import access, errors, jobs
from app.config import Settings
from app.db import Session, get_engine
from app.models import Node, Setting, User

# De hulpfuncties via de module: tests vervangen ze daar (monkeypatch op routers.files)
from app.routers import files
from app.routers.files import NodeOut, Store
from app.security import CurrentUser
from app.storage import BlobStore
from app.text import fold
from app.usage import user_usage

router = APIRouter(prefix="/api/v1", tags=["bestanden"])

MAX_RESTORE = 10_000
BATCH = 500
RETENTION_KEY = "trash_retention_days"
MAX_RETENTION_DAYS = 3650
# Zo vaak (seconden) kijkt het opruimwerk of er iets over de bewaartermijn is
RETENTION_EVERY = 3600.0

log = logging.getLogger("platform.trash")


class RestoreMany(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_RESTORE)


class Results(BaseModel):
    results: list[errors.ItemResult]


async def restore_node(
    session: AsyncSession,
    user: User,
    node_id: uuid.UUID,
    node: Node | None = None,
    taken: set[tuple[uuid.UUID, str]] | None = None,
) -> Node:
    """Eén node uit de prullenbak terug, in de transactie van de aanroeper en alleen onder
    BlobStore.lock. Staat de map erboven niet meer (of zelf in de prullenbak), dan komt hij in
    de hoofdmap; de naam wordt zo nodig genummerd. In bulk: node al opgehaald (van user), en
    taken van taken_names (wordt bijgewerkt)."""
    if node is None:
        node = await files.get_node(session, user, node_id, "owner")
    if node.deleted_at is None:
        raise errors.fail(422, "not_trashed", "Staat niet in de prullenbak")
    parent = await session.get(Node, node.parent_id)
    parent_id = node.parent_id
    if parent is None or await files.is_trashed(session, parent):
        parent_id = user.root_id
    # Pas wijzigen als alles gelukt is: in bulk wordt de rest van de batch wel gecommit
    if taken is None or (parent_id, node.name.lower()) in taken:
        name = await files.free_name(session, parent_id, node.name)
    else:
        name = node.name  # vrij: dat zei taken_names al (zonder query per node)
    if taken is not None:
        taken.add((parent_id, name.lower()))
    node.parent_id, node.name, node.deleted_at = parent_id, name, None
    return node


async def taken_names(
    session: AsyncSession, user: User, nodes: list[Node]
) -> set[tuple[uuid.UUID, str]]:
    """(map, naam in kleine letters) van wat er nu levend staat in de mappen waar deze nodes
    terug kunnen komen (hun oude map of de hoofdmap), met een naam als die van een van hen
    (de kandidaten van files.sibling). Eén query voor het hele stuk, in plaats van een
    naamcontrole per node."""
    if not nodes:
        return set()
    parents = {n.parent_id for n in nodes} | {user.root_id}
    parents.discard(None)
    rows = await session.execute(
        select(Node.parent_id, Node.name).where(
            Node.parent_id.in_(parents),
            Node.search_name.in_({fold(n.name) for n in nodes}),
            Node.deleted_at.is_(None),
        )
    )
    return {(parent_id, name.lower()) for parent_id, name in rows}


async def depths(session: AsyncSession, user: User, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Hoe diep elk van deze ids (van user) onder de hoofdmap ligt. Eén query per 500 ids."""
    found: dict[uuid.UUID, int] = {}
    for chunk in batched(ids, BATCH):
        up = access.ancestry(Node.id.in_(chunk) & (Node.owner_id == user.id))
        rows = await session.execute(
            select(up.c.origin, func.max(up.c.depth)).group_by(up.c.origin)
        )
        found.update({origin: depth for origin, depth in rows})
    return found


@router.get("/trash")
async def list_trash(user: CurrentUser, session: Session) -> list[NodeOut]:
    rows = (
        await session.scalars(
            select(Node)
            .where(Node.owner_id == user.id, Node.deleted_at.is_not(None))
            .order_by(Node.deleted_at.desc())
        )
    ).all()
    scope = select(Node.id).where(Node.owner_id == user.id, Node.deleted_at.is_not(None))
    return await files.describe(session, user, rows, "owner", scope=scope)


@router.post("/files/{node_id}/restore")
async def restore(node_id: uuid.UUID, user: CurrentUser, session: Session, blobs: Store) -> NodeOut:
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with blobs.lock:  # niet tegelijk met definitief verwijderen, zie change()
        node = await restore_node(session, user, node_id)
        await session.commit()
    await files.notify(session, node, "node.changed", below=await files.shared_below(session, node))
    return await files.one_out(session, user, node)


@router.delete("/trash/{node_id}", status_code=204)
async def purge(node_id: uuid.UUID, user: CurrentUser, session: Session, blobs: Store) -> None:
    """Definitief verwijderen, inclusief alles eronder en alle versies (en de delingen en
    links erop)."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with blobs.lock:
        # Pas onder het slot lezen: intussen kan het al weg of teruggezet zijn
        node = await files.get_node(session, user, node_id, "owner")
        if node.deleted_at is None:
            raise errors.fail(422, "not_trashed", "Verplaats het eerst naar de prullenbak")
        rows = await files.descendants(session, node)
        ids = [n.id for n in rows]
        # Wie het via een deling zag, nu het nog kan: daarna zijn node en delingen weg
        seen = await access.watchers(session, [node.id])
        below = await files.shared_below(session, node)
        refs = Counter(n.blob_id for n in rows if n.blob_id)
        refs += await files.drop_versions(session, ids)
        await files.add_refs(session, refs, sign=-1)
        for chunk in batched(reversed(ids), 500):  # kinderen voor hun ouders
            await session.execute(
                delete(Node).where(Node.id.in_(chunk)).execution_options(synchronize_session=False)
            )
        await session.commit()
    await files.notify(session, node, "node.deleted", seen=seen, below=below)
    await files.collect_garbage(session, blobs)


@router.post("/trash/restore")
async def restore_many(
    body: RestoreMany, user: CurrentUser, session: Session, blobs: Store
) -> Results:
    """Veel nodes tegelijk terugzetten: per id een ItemResult (ok met de node, of de code
    waarom niet). Per 500 één transactie onder BlobStore.lock; wat lukte blijft gelukt, ook
    als een id verderop faalt. Ondiep voor diep: een map gaat terug vóór wat eronder ligt (en
    ook in de prullenbak ligt), anders komt dat in de hoofdmap terecht."""
    results: dict[uuid.UUID, errors.ItemResult] = {}
    ids = list(dict.fromkeys(body.ids))
    depth = await depths(session, user, ids)
    ids.sort(key=lambda node_id: depth.get(node_id, 0))
    for chunk in batched(ids, BATCH):
        await session.commit()  # niet met een databaseverbinding op het slot wachten
        restored: list[Node] = []
        async with blobs.lock:
            # Pas onder het slot lezen, en per stuk in één keer (niet per node): de eigen nodes
            # (de rest via get_node, voor de juiste fout), de mappen erboven (vastgehouden: de
            # identity map houdt ze anders niet, en dan haalt restore_node ze per node opnieuw)
            # en de bezette namen. Vers (populate_existing): de sessie houdt nodes van het vorige
            # stuk nog vast, en die kunnen intussen weer weggegooid zijn
            own: Sequence[Node] = []
            if user.role != "guest":
                own = (
                    await session.scalars(
                        select(Node)
                        .where(Node.id.in_(chunk), Node.owner_id == user.id)
                        .execution_options(populate_existing=True)
                    )
                ).all()
            mine = {n.id: n for n in own}
            trashed = [n for n in own if n.deleted_at]
            up = access.ancestry(Node.id.in_([n.id for n in trashed]))
            above = (
                await session.scalars(
                    select(Node)
                    .where(Node.id.in_(select(up.c.id).where(up.c.depth > 0)))
                    .execution_options(populate_existing=True)
                )
            ).all()
            taken = await taken_names(session, user, trashed)
            for node_id in chunk:
                try:
                    node = await restore_node(session, user, node_id, mine.get(node_id), taken)
                except HTTPException as e:
                    results[node_id] = errors.item_of(node_id, e)
                    continue
                restored.append(node)
            await session.commit()
            del above
        below = {node.id: await files.shared_below(session, node) for node in restored}
        await files.notify_many(session, restored, "node.changed", below=below)
        for out in await files.describe(session, user, restored, "owner"):
            results[out.id] = errors.ItemResult(id=out.id, ok=True, node=out)
    return Results(results=[results[node_id] for node_id in dict.fromkeys(body.ids)])


# --- Overzicht ---


class PathPart(BaseModel):
    id: uuid.UUID
    name: str


class TrashItem(BaseModel):
    node: NodeOut
    # De mappen van de hoofdmap (niet meegeteld) tot de map waar het in stond; [] = in de
    # hoofdmap; null als het nergens in stond (een album)
    location: list[PathPart] | None
    # Een map: alles eronder samen
    size: int
    # original: terug op de oude plek; root: die map is weg of ligt zelf in de prullenbak
    restore_to: str


class TrashOverview(BaseModel):
    items: list[TrashItem]
    count: int
    # Alles in de prullenbak samen (wat in een weggegooide map ligt, telt één keer)
    total_bytes: int


async def folder_sizes(session: AsyncSession, user: User) -> dict[uuid.UUID, int]:
    """Per weggegooide map van user wat de bestanden eronder samen groot zijn (één query)."""
    top = select(Node.id.label("origin"), Node.id.label("id")).where(
        Node.owner_id == user.id, Node.deleted_at.is_not(None), Node.kind == "dir"
    )
    down = top.cte("down", recursive=True)
    child = aliased(Node)
    down = down.union_all(select(down.c.origin, child.id).where(child.parent_id == down.c.id))
    rows = await session.execute(
        select(down.c.origin, func.coalesce(func.sum(Node.size), 0))
        .join(Node, Node.id == down.c.id)
        .where(Node.kind == "file")
        .group_by(down.c.origin)
    )
    return {origin: int(size) for origin, size in rows}


async def locations(
    session: AsyncSession, user: User
) -> dict[uuid.UUID, tuple[list[PathPart] | None, str]]:
    """Per weggegooide node van user: de mappen erboven (zonder de hoofdmap) en waar hij bij
    terugzetten heen gaat (één query)."""
    up = access.ancestry((Node.owner_id == user.id) & Node.deleted_at.is_not(None))
    rows = await session.execute(
        select(up.c.origin, up.c.id, up.c.parent_id, up.c.name, up.c.deleted_at, up.c.depth)
        .where(up.c.depth > 0)
        .order_by(up.c.origin, up.c.depth.desc())
    )
    chains: dict[uuid.UUID, list] = defaultdict(list)
    for row in rows:
        chains[row.origin].append(row)
    result: dict[uuid.UUID, tuple[list[PathPart] | None, str]] = {}
    for origin, chain in chains.items():
        trashed = any(row.deleted_at is not None for row in chain)
        path = [PathPart(id=row.id, name=row.name) for row in chain if row.parent_id is not None]
        result[origin] = (path, "root" if trashed else "original")
    return result


@router.get("/trash/overview")
async def trash_overview(user: CurrentUser, session: Session) -> TrashOverview:
    """Alles in je prullenbak, het laatst weggegooide eerst, met "Was in" en de grootte. Een
    vast aantal query's."""
    rows = (
        await session.scalars(
            select(Node)
            .where(Node.owner_id == user.id, Node.deleted_at.is_not(None))
            .order_by(Node.deleted_at.desc(), Node.id)
        )
    ).all()
    scope = select(Node.id).where(Node.owner_id == user.id, Node.deleted_at.is_not(None))
    described = await files.describe(session, user, rows, "owner", scope=scope)
    sizes = await folder_sizes(session, user) if any(n.kind == "dir" for n in rows) else {}
    places = await locations(session, user) if rows else {}
    items = []
    for node, out in zip(rows, described, strict=True):
        if node.parent_id is None:
            location, restore_to = None, "original"  # een album
        else:
            # Geen keten: de map erboven bestaat niet meer
            location, restore_to = places.get(node.id, (None, "root"))
        size = sizes.get(node.id, 0) if node.kind == "dir" else node.size
        items.append(TrashItem(node=out, location=location, size=size, restore_to=restore_to))
    total = (await user_usage(session, [user.id])).get(user.id)
    return TrashOverview(
        items=items, count=len(items), total_bytes=total.trash_bytes if total else 0
    )


# --- Definitief verwijderen, in bulk ---


class PurgeMany(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_RESTORE)


class Emptied(BaseModel):
    purged: int


async def covered(session: AsyncSession, user: User, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Welke van deze ids onder een andere van deze ids liggen die in de prullenbak ligt: die
    gaan met hun map mee weg. Eén query per 500 ids."""
    wanted = set(ids)
    found: set[uuid.UUID] = set()
    for chunk in batched(ids, BATCH):
        up = access.ancestry(Node.id.in_(chunk) & (Node.owner_id == user.id))
        rows = await session.execute(
            select(up.c.origin, up.c.id).where(up.c.depth > 0, up.c.deleted_at.is_not(None))
        )
        found.update(origin for origin, ancestor in rows if ancestor in wanted)
    return found


async def _subtree(session: AsyncSession, ids: list[uuid.UUID]) -> list:
    """(id, blob_id) van deze nodes en alles eronder, kinderen vóór hun ouders, elk één keer
    (ook als de ene node onder de andere ligt). In stukken binnengehaald."""
    found = files.tree(ids)
    query = select(Node.id, Node.blob_id, found.c.depth).join(found, Node.id == found.c.id)
    deepest: dict[uuid.UUID, tuple[int, str | None]] = {}
    async for part in (await session.stream(query)).partitions(BATCH):
        for node_id, blob_id, depth in part:
            if node_id not in deepest or deepest[node_id][0] < depth:
                deepest[node_id] = (depth, blob_id)
    order = sorted(deepest.items(), key=lambda item: -item[1][0])
    return [(node_id, blob_id) for node_id, (_, blob_id) in order]


async def purge_locked(session: AsyncSession, nodes: list[Node]) -> tuple[dict, dict]:
    """Onder BlobStore.lock, in de transactie van de aanroeper (die commit): deze nodes (in de
    prullenbak) en alles eronder weg, met hun versies, en ref_count omlaag. Geeft (seen,
    below) voor notify_many na de commit."""
    ids = [n.id for n in nodes]
    # Wie het via een deling zag, nu het nog kan: daarna zijn node en delingen weg
    seen = await access.watchers(session, ids)
    below = {n.id: await files.shared_below(session, n) for n in nodes if n.kind == "dir"}
    rows = await _subtree(session, ids)
    all_ids = [node_id for node_id, _ in rows]
    refs = Counter(blob_id for _, blob_id in rows if blob_id)
    refs += await files.drop_versions(session, all_ids)
    await files.add_refs(session, refs, sign=-1)
    for chunk in batched(all_ids, BATCH):  # kinderen voor hun ouders
        await session.execute(
            delete(Node).where(Node.id.in_(chunk)).execution_options(synchronize_session=False)
        )
    return seen, below


async def purge_ids(
    session: AsyncSession, blobs: BlobStore, user: User, ids: list[uuid.UUID]
) -> dict[uuid.UUID, errors.ItemResult]:
    """Definitief verwijderen voor een lijst ids van user: per 500 één transactie onder
    BlobStore.lock, dan de gebeurtenissen en aan het eind collect_garbage. Wat onder een
    andere weggegooide id uit de lijst ligt, gaat met die mee (ok)."""
    requested = list(dict.fromkeys(ids))
    results: dict[uuid.UUID, errors.ItemResult] = {}
    inside = await covered(session, user, requested)
    todo = [node_id for node_id in requested if node_id not in inside]
    for chunk in batched(todo, BATCH):
        await session.commit()  # niet met een databaseverbinding op het slot wachten
        purged: list[Node] = []
        seen: dict = {}
        below: dict = {}
        async with blobs.lock:
            # Pas onder het slot lezen: intussen kan het al weg of teruggezet zijn
            for node_id in chunk:
                try:
                    node = await files.get_node(session, user, node_id, "owner")
                except HTTPException as e:
                    results[node_id] = errors.item_of(node_id, e)
                    continue
                if node.deleted_at is None:
                    results[node_id] = errors.item_fail(
                        node_id, "not_trashed", "Verplaats het eerst naar de prullenbak"
                    )
                    continue
                purged.append(node)
            if purged:
                seen, below = await purge_locked(session, purged)
            await session.commit()
        await files.notify_many(session, purged, "node.deleted", seen=seen, below=below)
        for node in purged:
            results[node.id] = errors.ItemResult(id=node.id, ok=True)
    for node_id in inside:
        results[node_id] = errors.ItemResult(id=node_id, ok=True)
    if any(r.ok for r in results.values()):
        await files.collect_garbage(session, blobs)
    return results


@router.post("/trash/purge")
async def purge_many(body: PurgeMany, user: CurrentUser, session: Session, blobs: Store) -> Results:
    """Veel nodes uit de prullenbak tegelijk definitief verwijderen: per id een ItemResult."""
    results = await purge_ids(session, blobs, user, body.ids)
    return Results(results=[results[node_id] for node_id in dict.fromkeys(body.ids)])


@router.delete("/trash")
async def empty_trash(user: CurrentUser, session: Session, blobs: Store) -> Emptied:
    """De hele prullenbak legen. purged: hoeveel er in de prullenbak stond en weg is."""
    ids = list(
        (
            await session.scalars(
                select(Node.id).where(Node.owner_id == user.id, Node.deleted_at.is_not(None))
            )
        ).all()
    )
    if not ids:
        return Emptied(purged=0)
    results = await purge_ids(session, blobs, user, ids)
    return Emptied(purged=sum(1 for r in results.values() if r.ok))


# --- Bewaartermijn ---


async def retention_days(session: AsyncSession) -> int:
    """Na zoveel dagen gaat wat in een prullenbak ligt vanzelf weg; 0 = nooit (standaard)."""
    row = await session.get(Setting, RETENTION_KEY)
    if row is None or not isinstance(row.value, dict):
        return 0
    days = row.value.get("days", 0)
    return days if isinstance(days, int) and 0 <= days <= MAX_RETENTION_DAYS else 0


async def save_retention(session: AsyncSession, days: int) -> None:
    """In de transactie van de aanroeper (die commit)."""
    row = await session.get(Setting, RETENTION_KEY)
    if row is None:
        session.add(Setting(key=RETENTION_KEY, value={"days": days}))
    else:
        row.value = {"days": days}


async def purge_expired(settings: Settings, now: datetime | None = None) -> int:
    """Wat langer dan de bewaartermijn in een prullenbak ligt, definitief weg (per eigenaar,
    zoals bij legen). Geeft hoeveel er weg is."""
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    blobs = BlobStore(settings.blob_dir)
    purged = 0
    async with maker() as session:
        days = await retention_days(session)
        if not days:
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
        rows = (
            await session.execute(
                select(Node.owner_id, Node.id).where(
                    Node.deleted_at.is_not(None), Node.deleted_at < cutoff
                )
            )
        ).all()
        by_owner: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for owner_id, node_id in rows:
            by_owner[owner_id].append(node_id)
        for owner_id, ids in by_owner.items():
            owner = await session.get(User, owner_id)
            if owner is None:
                continue
            results = await purge_ids(session, blobs, owner, ids)
            purged += sum(1 for r in results.values() if r.ok)
        await session.commit()
    if purged:
        log.info("Prullenbak: %d onderdelen ouder dan %d dagen definitief verwijderd", purged, days)
    return purged


async def _retention(settings: Settings) -> None:
    await purge_expired(settings)


jobs.housekeeping("trash.retention", _retention, RETENTION_EVERY)
