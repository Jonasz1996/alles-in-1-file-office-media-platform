"""Bulkacties op veel bestanden en mappen tegelijk: naar de prullenbak, verplaatsen en
kopiëren. Eén verzoek, per id een errors.ItemResult (gelukt met de node, of de code waarom
niet: dezelfde codes als de losse endpoints), één transactie per BATCH ids en de events
gebundeld via files.notify_many (hooguit 500 ids per event).

De toegang gaat in bulk (files.get_tree_accesses: twee queries per stuk, hoeveel ids het ook
zijn); een album staat buiten de boom en geeft album. Wat in een eerder stuk lukte, blijft
gelukt, ook als een id verderop faalt.
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import batched
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, errors
from app.db import Session
from app.models import Node, User

# De hulpfuncties via de module: tests vervangen ze daar (monkeypatch op routers.files)
from app.routers import files
from app.routers.files import Access, NodeOut, Store
from app.security import CurrentUser
from app.text import fold

router = APIRouter(prefix="/api/v1", tags=["bestanden"])

MAX_IDS = 10_000
# Zoveel ids per transactie (en per keer onder de sloten): andere schrijvers wachten niet te lang
BATCH = 500


class Ids(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_IDS)


class MoveMany(Ids):
    parent_id: uuid.UUID
    # fail: een bezette naam geeft name_taken; rename: de node krijgt "naam (1)"
    conflict: Literal["fail", "rename"] = "fail"


class CopyMany(Ids):
    parent_id: uuid.UUID


class Results(BaseModel):
    results: list[errors.ItemResult]


def _ordered(ids: Sequence[uuid.UUID], results: dict[uuid.UUID, errors.ItemResult]) -> Results:
    """Eén resultaat per verschillend id, in de volgorde waarin het voor het eerst voorkomt."""
    return Results(results=[results[i] for i in dict.fromkeys(ids)])


async def seen_as(session: AsyncSession, user: User, nodes: Sequence[Node]) -> list[NodeOut]:
    """NodeOut van deze nodes zoals user ze nu ziet (na een wijziging), met één access.resolve
    voor wat van anderen is; wat hij niet meer ziet, als read zonder parent_id (zoals
    files.view)."""
    others = [n.id for n in nodes if n.owner_id != user.id]
    grants = await access.resolve(session, user.id, others) if others else {}
    permissions: dict[uuid.UUID, str] = {}
    roots: set[uuid.UUID] = set()
    for node in nodes:
        if node.owner_id == user.id:
            permissions[node.id] = "owner"
            continue
        grant = grants.get(node.id)
        permissions[node.id] = grant.permission if grant else "read"
        if grant is None or grant.root_id == node.id:
            roots.add(node.id)
    return await files.describe(session, user, nodes, permissions, roots=roots)


def _change_refused(found: Access) -> HTTPException | None:
    """Wat bij weggooien en verplaatsen van één node niet mag, zoals bij de losse endpoints."""
    if found.node.parent_id is None:
        return errors.fail(422, "root", "De hoofdmap kan niet worden gewijzigd")
    if found.grant is not None and found.grant.direct:
        return files.share_root(found)
    return None


@router.post("/batch/trash")
async def trash_many(body: Ids, user: CurrentUser, session: Session) -> Results:
    """Naar de prullenbak (van de eigenaar), zoals DELETE /files/{id}, voor veel ids tegelijk.
    Wat al in de prullenbak ligt (of in een map daar), geeft trashed."""
    results: dict[uuid.UUID, errors.ItemResult] = {}
    for chunk in batched(dict.fromkeys(body.ids), BATCH):
        found = await files.get_tree_accesses(session, user, chunk, "write")
        own = [f.node.id for f in found.values() if isinstance(f, Access) and f.owner]
        gone = await files.trashed_among(session, own) if own else set()
        now = datetime.now(UTC)
        done: list[Node] = []
        for node_id in chunk:
            f = found[node_id]
            if isinstance(f, HTTPException):
                results[node_id] = errors.item_of(node_id, f)
                continue
            refused = _change_refused(f)
            if refused is not None:
                results[node_id] = errors.item_of(node_id, refused)
                continue
            if node_id in gone:
                results[node_id] = errors.item_fail(
                    node_id, "trashed", "Dit ligt al in de prullenbak"
                )
                continue
            f.node.deleted_at = now
            done.append(f.node)
        await session.commit()
        # Wie alleen een gedeelde map daaronder heeft, raakt die kwijt (zoals bij één node)
        below = {n.id: await files.shared_below(session, n) for n in done if n.kind == "dir"}
        await files.notify_many(session, done, "node.deleted", below=below)
        for node in done:
            results[node.id] = errors.ItemResult(id=node.id, ok=True)
    return _ordered(body.ids, results)


@router.post("/batch/move")
async def move_many(body: MoveMany, user: CurrentUser, session: Session, blobs: Store) -> Results:
    """Verplaatsen naar map parent_id, zoals PATCH /files/{id} met parent_id, voor veel ids.
    Alleen binnen dezelfde eigenaar (anders cross_owner); onder het blob-slot, net als
    change(). Een doelmap die er niet is of waar je niet mag schrijven, is een fout voor het
    hele verzoek."""
    results: dict[uuid.UUID, errors.ItemResult] = {}
    first = True
    for chunk in batched(dict.fromkeys(body.ids), BATCH):
        await session.commit()  # niet met een databaseverbinding op het slot wachten
        moved: list[Node] = []
        old_parents: dict[uuid.UUID, uuid.UUID | None] = {}
        async with blobs.lock:
            try:
                target = (await files.get_dir(session, user, body.parent_id, "write")).node
                chain = await files.ancestors(session, target)
                if any(a.deleted_at is not None for a in chain):
                    raise errors.fail(404, "folder_not_found", "Map niet gevonden")
            except HTTPException as e:
                if first:
                    raise
                # Intussen weg: de rest mislukt per id (wat al verplaatst is, blijft dat)
                for node_id in chunk:
                    results[node_id] = errors.item_of(node_id, e)
                continue
            first = False
            above = {a.id for a in chain}
            found = await files.get_tree_accesses(session, user, chunk, "write")
            own = [f.node.id for f in found.values() if isinstance(f, Access) and f.owner]
            gone = await files.trashed_among(session, own) if own else set()
            for node_id in chunk:
                f = found[node_id]
                if isinstance(f, HTTPException):
                    results[node_id] = errors.item_of(node_id, f)
                    continue
                node = f.node
                refused = _change_refused(f)
                if refused is None and node.owner_id != target.owner_id:
                    refused = errors.fail(422, **files.CROSS_OWNER)
                if refused is None and node.id in above:
                    refused = errors.fail(
                        422, "into_itself", "Een map kan niet in zichzelf worden geplaatst"
                    )
                if refused is None and node_id in gone:
                    refused = errors.fail(422, "trashed", "Zet het eerst terug uit de prullenbak")
                if refused is not None:
                    results[node_id] = errors.item_of(node_id, refused)
                    continue
                if node.parent_id == target.id:
                    moved.append(node)  # staat er al: niets te doen, wel gelukt
                    continue
                name = node.name
                other = await files.sibling(session, target.id, name)
                if other is not None and other.id != node.id:
                    if body.conflict == "fail":
                        results[node_id] = errors.item_fail(
                            node_id, "name_taken", "Er bestaat al iets met deze naam"
                        )
                        continue
                    name = await files.free_name(session, target.id, name)
                old_parents[node.id] = node.parent_id
                node.name, node.parent_id = name, target.id
                await session.flush()  # de volgende naamcontrole ziet deze al
                moved.append(node)
            await session.commit()
        changed = [n for n in moved if n.id in old_parents]
        await files.notify_many(session, changed, "node.changed", old_parents=old_parents)
        for out in await seen_as(session, user, moved):
            results[out.id] = errors.ItemResult(id=out.id, ok=True, node=out)
    return _ordered(body.ids, results)


COPY_COLUMNS = (
    *(Node.id, Node.parent_id, Node.name, Node.kind, Node.mime, Node.size),
    *(Node.blob_id, Node.modified_at, Node.taken_at, Node.taken_day),
)


@router.post("/batch/copy")
async def copy_many(body: CopyMany, user: CurrentUser, session: Session, blobs: Store) -> Results:
    """Kopiëren naar map parent_id, zoals POST /files/{id}/copy, voor veel ids. Samen hooguit
    files.MAX_COPY mappen en bestanden (wat er niet meer bij past: too_many). Het quotum van de
    eigenaar van de doelmap wordt één keer gecontroleerd, voor alles samen (413 voor het hele
    verzoek), onder zijn gebruikersslot en het blob-slot. ItemResult.node is de kopie."""
    target_owner = await session.scalar(select(Node.owner_id).where(Node.id == body.parent_id))
    await session.commit()  # niet met een databaseverbinding op een slot wachten
    results: dict[uuid.UUID, errors.ItemResult] = {}
    async with files.user_lock(target_owner or user.id), blobs.lock:
        destination = await files.get_dir(session, user, body.parent_id, "write")
        target = destination.node
        chain = await files.ancestors(session, target)
        if any(a.deleted_at is not None for a in chain):
            raise errors.fail(404, "folder_not_found", "Map niet gevonden")
        above = {a.id for a in chain}
        owner = user if destination.owner else await session.get(User, target.owner_id)
        plan: list[tuple[Node, list]] = []
        total = 0
        for chunk in batched(dict.fromkeys(body.ids), BATCH):
            found = await files.get_tree_accesses(session, user, chunk, "read")
            own = [f.node.id for f in found.values() if isinstance(f, Access) and f.owner]
            gone = await files.trashed_among(session, own) if own else set()
            for node_id in chunk:
                f = found[node_id]
                if isinstance(f, HTTPException):
                    results[node_id] = errors.item_of(node_id, f)
                    continue
                if node_id in gone:
                    results[node_id] = errors.item_fail(
                        node_id, "trashed", "Wat in de prullenbak ligt, kan niet worden gekopieerd"
                    )
                    continue
                if node_id in above:
                    results[node_id] = errors.item_fail(
                        node_id, "into_itself", "Een map kan niet in zichzelf worden gekopieerd"
                    )
                    continue
                left = files.MAX_COPY - total
                rows = await files.subtree_rows(
                    session, node_id, *COPY_COLUMNS, live=True, limit=left + 1
                )
                if len(rows) > left:
                    most = f"{files.MAX_COPY:,}".replace(",", ".")
                    results[node_id] = errors.item_fail(
                        node_id,
                        "too_many",
                        f"Te veel om in één keer te kopiëren (meer dan {most} mappen en bestanden)",
                        max=files.MAX_COPY,
                    )
                    continue
                total += len(rows)
                plan.append((f.node, rows))
        room = await files.room_left(session, owner)
        size = sum(r.size for _, rows in plan for r in rows if r.kind == "file")
        if room is not None and size > room:
            raise files.no_room(owner, user.id)
        for part in batched(plan, BATCH):
            tops: dict[uuid.UUID, uuid.UUID] = {}
            refs: Counter[str] = Counter()
            for node, rows in part:
                tops[node.id] = await _copy_tree(session, node, rows, target)
                refs.update(r.blob_id for r in rows if r.blob_id)
            await files.add_refs(session, refs)
            await session.commit()
            copies = (
                await session.scalars(select(Node).where(Node.id.in_(list(tops.values()))))
            ).all()
            by_id = {c.id: c for c in copies}
            ordered = [by_id[tops[node.id]] for node, _ in part]
            await files.notify_many(session, ordered, "node.created")
            outs = await files.describe(session, user, ordered, destination.permission)
            for (node, _), out in zip(part, outs, strict=True):
                results[node.id] = errors.ItemResult(id=node.id, ok=True, node=out)
    return _ordered(body.ids, results)


async def _copy_tree(session: AsyncSession, node: Node, rows: list, target: Node) -> uuid.UUID:
    """De kopie van node (rows: zijn subtree, ouders voor kinderen) in map target, met een vrije
    naam; geeft het id van de kopie. Rechtstreeks in stukken (zoals files.copy): één
    ORM-object per kopie houdt de event loop te lang bezig."""
    name = await files.free_name(session, target.id, node.name)
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

    for chunk in batched(rows, 500):
        await session.execute(insert(Node), [copied(n) for n in chunk])
        await asyncio.sleep(0)
    return new_ids[node.id]
