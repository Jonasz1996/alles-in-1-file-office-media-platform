"""De taak user.delete: een geblokkeerde gebruiker met al zijn spullen verwijderen.

DELETE /users/{id} zet de taak klaar; hij draait op de achtergrond en mag halverwege stoppen
(een herstart, een timeout): de volgende poging gaat verder met wat er nog is. Volgorde:

1. Zijn delingen en links weg, en wie ze kreeg hoort share.deleted. Daarna kan niemand
   anders nog iets in zijn mappen zetten.
2. Lopende uploads weg: die naar zijn bestanden (onder zijn user_lock, net als het
   aanmaken ervan) en die hij zelf in de mappen van anderen deed (onder hun user_lock: het
   reserveert ruimte in hun quotum).
3. Al zijn nodes (de boom vanaf zijn hoofdmap, de prullenbak, albums) in batches van 500,
   kinderen vóór hun ouders, elke batch onder BlobStore.lock: de versies weg
   (drop_versions), ref_count omlaag (add_refs met -1) en de rijen weg. Nooit via de
   cascade van node.owner_id: dan blijven de verwijzingen tellen en wordt de inhoud nooit
   opgeruimd.
4. collect_garbage: blobs waar niemand meer naar verwijst, ook van schijf (en hun previews).
5. Pas dan de gebruiker zelf (tags, groepslidmaatschappen, logins gaan mee via de cascade).

Wie tijdens het verwijderen de blokkade wil opheffen, krijgt 409 user_being_deleted.
"""

import logging
import uuid
from collections import Counter
from itertools import batched
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import jobs
from app.config import Settings
from app.db import get_engine
from app.models import Node, Share, Upload, User

log = logging.getLogger("platform.userdelete")

TYPE = "user.delete"
BATCH = 500
# Hooguit zoveel rondes over wat er nog over is (normaal is het na één ronde leeg)
ROUNDS = 5


def key(user_id: uuid.UUID) -> str:
    return f"{TYPE}:{user_id}"


def user_of(job_key: str | None) -> uuid.UUID | None:
    if not job_key or not job_key.startswith(f"{TYPE}:"):
        return None
    try:
        return uuid.UUID(job_key.removeprefix(f"{TYPE}:"))
    except ValueError:
        return None


async def start(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """Zet de taak klaar in de transactie van de aanroeper (die commit). Staat hij al klaar
    of loopt hij, dan het id van die taak."""
    from app.models import Job

    job_id = await jobs.enqueue(session, TYPE, {"user_id": str(user_id)}, key=key(user_id))
    if job_id is not None:
        return job_id
    existing = await session.scalar(
        select(Job.id)
        .where(Job.key == key(user_id), Job.status.in_(jobs.ACTIVE))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    assert existing is not None  # enqueue zag hem net nog
    return existing


async def _drop_shares(session: AsyncSession, user_id: uuid.UUID) -> None:
    from app.routers.shares import announce, grantees

    shares = list((await session.scalars(select(Share).where(Share.owner_id == user_id))).all())
    told = [(await grantees(session, s), s.id, s.node_id) for s in shares]
    for chunk in batched([s.id for s in shares], BATCH):
        await session.execute(
            delete(Share).where(Share.id.in_(chunk)).execution_options(synchronize_session=False)
        )
    await session.commit()
    for receivers, share_id, node_id in told:
        announce(receivers - {user_id}, "share.deleted", share_id, node_id)


async def _drop_uploads(session: AsyncSession, settings: Settings, user_id: uuid.UUID) -> None:
    from app.routers.files import user_lock
    from app.routers.uploads import _drop

    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with user_lock(user_id):
        own = (await session.scalars(select(Upload.id).where(Upload.owner_id == user_id))).all()
        for upload_id in own:
            await _drop(session, settings, upload_id)
    # Wat hij in de mappen van anderen uploadde: per eigenaar onder diens slot
    rows = (
        await session.execute(
            select(Upload.id, Upload.owner_id).where(
                Upload.created_by == user_id, Upload.owner_id != user_id
            )
        )
    ).all()
    await session.commit()
    by_owner: dict[uuid.UUID, list[uuid.UUID]] = {}
    for upload_id, owner_id in rows:
        by_owner.setdefault(owner_id, []).append(upload_id)
    for owner_id, ids in by_owner.items():
        async with user_lock(owner_id):
            for upload_id in ids:
                await _drop(session, settings, upload_id)


async def _remaining(session: AsyncSession, user: User) -> list:
    """(id, blob_id) van al zijn nodes, de diepste eerst: zo komen kinderen vóór hun ouders.
    Wat niet onder zijn hoofdmap hangt (albums), telt als diepte 0."""
    from app.routers.files import tree

    rows: list = []
    if user.root_id is not None:
        found = tree(user.root_id)
        query = (
            select(Node.id, Node.blob_id, func.coalesce(found.c.depth, 0).label("depth"))
            .outerjoin(found, found.c.id == Node.id)
            .where(Node.owner_id == user.id)
            .order_by(func.coalesce(found.c.depth, 0).desc(), Node.id)
        )
    else:
        query = select(Node.id, Node.blob_id).where(Node.owner_id == user.id).order_by(Node.id)
    async for part in (await session.stream(query)).partitions(BATCH):
        rows.extend((row[0], row[1]) for row in part)
    return rows


async def _drop_nodes(session: AsyncSession, settings: Settings, user: User) -> int:
    from app.routers.files import add_refs, drop_versions
    from app.storage import BlobStore

    blobs = BlobStore(settings.blob_dir)
    removed = 0
    for _ in range(ROUNDS):
        rows = await _remaining(session, user)
        if not rows:
            break
        for chunk in batched(rows, BATCH):
            ids = [node_id for node_id, _ in chunk]
            await session.commit()  # niet met een databaseverbinding op het slot wachten
            async with blobs.lock:
                # Alleen wat er nu (onder het slot) nog is: een eerdere poging kan al verder
                # zijn gekomen
                present = (
                    await session.execute(
                        select(Node.id, Node.blob_id).where(
                            Node.id.in_(ids), Node.owner_id == user.id
                        )
                    )
                ).all()
                if not present:
                    continue
                present_ids = [row.id for row in present]
                refs = Counter(row.blob_id for row in present if row.blob_id)
                refs += await drop_versions(session, present_ids)
                await add_refs(session, refs, sign=-1)
                # Kinderen staan vóór hun ouders in de lijst; binnen een batch eerst de diepste
                order = {node_id: i for i, node_id in enumerate(ids)}
                for part in batched(sorted(present_ids, key=order.__getitem__), BATCH):
                    await session.execute(
                        delete(Node)
                        .where(Node.id.in_(part))
                        .execution_options(synchronize_session=False)
                    )
                await session.commit()
                removed += len(present_ids)
    return removed


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    from app.routers.files import collect_garbage
    from app.storage import BlobStore

    user_id = uuid.UUID(payload["user_id"])
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        user = await session.get(User, user_id)
        if user is None:
            return  # al weg (een eerdere poging kwam tot het eind)
        if user.disabled_at is None:
            # Intussen weer actief (kan alleen als de taak al klaarstond van vóór het
            # opheffen van de blokkade): niets verwijderen
            log.warning("Gebruiker %s is niet (meer) geblokkeerd; niet verwijderd", user_id)
            return
        await _drop_shares(session, user_id)
        await _drop_uploads(session, settings, user_id)
        removed = await _drop_nodes(session, settings, user)
        await collect_garbage(session, BlobStore(settings.blob_dir))
        left = await session.scalar(
            select(func.count()).select_from(Node).where(Node.owner_id == user_id)
        )
        if left:
            raise jobs.JobError(f"Er staan nog {left} bestanden of mappen; zo meteen opnieuw")
        await session.execute(
            delete(User).where(User.id == user_id).execution_options(synchronize_session=False)
        )
        await session.commit()
    log.info("Gebruiker %s verwijderd (%d bestanden en mappen)", user_id, removed)


jobs.register(TYPE, run, max_attempts=5)
