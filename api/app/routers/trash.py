"""De prullenbak: bekijken, terugzetten (ook in bulk) en definitief verwijderen.

list_trash, restore en purge zijn ongewijzigd verhuisd uit routers/files.py (zelfde paden);
P6 breidt dit uit met een overzicht, legen en bewaartermijn. POST /trash/restore is het
contract van P0 voor terugzetten in bulk (ook voor "ongedaan maken" in de web-app).
"""

import uuid
from collections import Counter
from itertools import batched

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, errors
from app.db import Session
from app.models import Node, User

# De hulpfuncties via de module: tests vervangen ze daar (monkeypatch op routers.files)
from app.routers import files
from app.routers.files import NodeOut, Store
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["bestanden"])

MAX_RESTORE = 10_000
BATCH = 500


class RestoreMany(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_RESTORE)


class Results(BaseModel):
    results: list[errors.ItemResult]


async def restore_node(session: AsyncSession, user: User, node_id: uuid.UUID) -> Node:
    """Eén node uit de prullenbak terug, in de transactie van de aanroeper en alleen onder
    BlobStore.lock. Staat de map erboven niet meer (of zelf in de prullenbak), dan komt hij in
    de hoofdmap; de naam wordt zo nodig genummerd."""
    node = await files.get_node(session, user, node_id, "owner")
    if node.deleted_at is None:
        raise errors.fail(422, "not_trashed", "Staat niet in de prullenbak")
    parent = await session.get(Node, node.parent_id)
    parent_id = node.parent_id
    if parent is None or await files.is_trashed(session, parent):
        parent_id = user.root_id
    # Pas wijzigen als alles gelukt is: in bulk wordt de rest van de batch wel gecommit
    name = await files.free_name(session, parent_id, node.name)
    node.parent_id, node.name, node.deleted_at = parent_id, name, None
    return node


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
    als een id verderop faalt."""
    results: dict[uuid.UUID, errors.ItemResult] = {}
    for chunk in batched(dict.fromkeys(body.ids), BATCH):
        await session.commit()  # niet met een databaseverbinding op het slot wachten
        restored: list[Node] = []
        async with blobs.lock:
            for node_id in chunk:
                try:
                    node = await restore_node(session, user, node_id)
                except HTTPException as e:
                    results[node_id] = errors.item_of(node_id, e)
                    continue
                restored.append(node)
            await session.commit()
        below = {node.id: await files.shared_below(session, node) for node in restored}
        await files.notify_many(session, restored, "node.changed", below=below)
        for out in await files.describe(session, user, restored, "owner"):
            results[out.id] = errors.ItemResult(id=out.id, ok=True, node=out)
    return Results(results=[results[node_id] for node_id in dict.fromkeys(body.ids)])
