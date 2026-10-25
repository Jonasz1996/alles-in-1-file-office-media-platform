"""Schijfgebruik per node voor /beheer: hoeveel ruimte de blobs, previews, lopende uploads en
de database innemen, en wat er verder op de datadisk staat.

De previews en uploads tellen we door de mappen te lopen (scandir). Dat kan bij veel bestanden
seconden duren, dus nooit per verzoek en nooit op de event loop: het draait in een thread, het
resultaat staat in een cache, en het opruimwerk van de achtergrondtaken (jobs.housekeeping)
ververst het om de ~10 minuten. Alleen als er nog niets in de cache staat (net gestart), meet
het eerste verzoek één keer zelf.

In standalone-modus is er één node: deze. In het cluster (later) komt hier per node een meting
bij; tot dan staat ook daar alleen de node waar deze API draait.
"""

import asyncio
import logging
import os
import shutil
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
import anyio.to_thread
from pydantic import BaseModel
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app import jobs
from app.cluster import Disk
from app.config import Settings
from app.db import get_engine
from app.models import Blob, Node, Version

log = logging.getLogger("platform.usage")

# Zo vaak (seconden) ververst het opruimwerk de meting
REFRESH = 600.0


class StorageParts(BaseModel):
    blobs: int
    previews: int
    uploads: int
    database: int
    # Wat er verder op de datadisk staat (het systeem, andere programma's)
    other: int


class NodeStorage(BaseModel):
    name: str
    disk: Disk | None
    parts: StorageParts
    measured_at: datetime


class UserUsage(BaseModel):
    """Wat een gebruiker opslaat. Samen (files + trash + versions) is het wat tegen het quotum
    telt (used_bytes in UserOut)."""

    # Bestanden buiten de prullenbak
    files_bytes: int = 0
    # Bestanden in de prullenbak (zelf weggegooid of onder een weggegooide map)
    trash_bytes: int = 0
    # Eerdere versies van zijn bestanden (ook die in de prullenbak)
    versions_bytes: int = 0
    # Aantal bestanden buiten de prullenbak
    file_count: int = 0

    @property
    def used_bytes(self) -> int:
        return self.files_bytes + self.trash_bytes + self.versions_bytes


def trashed_tree():
    """Recursieve CTE met de id van alles wat in een prullenbak ligt: de weggegooide nodes en
    alles eronder (UNION, zodat een weggegooid bestand in een weggegooide map één keer telt)."""
    top = select(Node.id).where(Node.deleted_at.is_not(None))
    found = top.cte("trashed", recursive=True)
    child = aliased(Node)
    return found.union(select(child.id).where(child.parent_id == found.c.id))


async def user_usage(
    session: AsyncSession, user_ids: Collection[uuid.UUID] | None = None
) -> dict[uuid.UUID, UserUsage]:
    """Het gebruik per gebruiker in twee query's, hoeveel gebruikers het ook zijn (geen query
    per gebruiker). user_ids: alleen deze; None: iedereen. Wie niets heeft, ontbreekt."""
    trashed = trashed_tree()
    is_file = Node.kind == "file"
    in_trash = trashed.c.id.is_not(None)
    query = (
        select(
            Node.owner_id,
            func.coalesce(func.sum(case((is_file & ~in_trash, Node.size), else_=0)), 0),
            func.coalesce(func.sum(case((is_file & in_trash, Node.size), else_=0)), 0),
            func.count(case((is_file & ~in_trash, 1))),
        )
        .outerjoin(trashed, trashed.c.id == Node.id)
        .group_by(Node.owner_id)
    )
    versions = (
        select(Node.owner_id, func.coalesce(func.sum(Version.size), 0))
        .join(Node, Node.id == Version.node_id)
        .group_by(Node.owner_id)
    )
    if user_ids is not None:
        ids = list(user_ids)
        query = query.where(Node.owner_id.in_(ids))
        versions = versions.where(Node.owner_id.in_(ids))
    result: dict[uuid.UUID, UserUsage] = {}
    for owner_id, files, trash, count in await session.execute(query):
        result[owner_id] = UserUsage(
            files_bytes=int(files), trash_bytes=int(trash), file_count=int(count)
        )
    for owner_id, size in await session.execute(versions):
        result.setdefault(owner_id, UserUsage()).versions_bytes = int(size)
    return result


def dir_bytes(path: Path) -> int:
    """De grootte van alle bestanden onder path (zonder symlinks te volgen); 0 als het er niet
    is. Draai dit in een thread."""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue  # intussen weg
        except OSError:
            continue
    return total


def sqlite_path(settings: Settings) -> Path | None:
    url = settings.db_url
    if not url.startswith("sqlite"):
        return None
    _, _, rest = url.partition(":///")
    return Path(rest) if rest and rest != ":memory:" else None


def sqlite_bytes(path: Path) -> int:
    """Het databasebestand plus zijn WAL- en shm-bestand."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += Path(f"{path}{suffix}").stat().st_size
        except OSError:
            continue
    return total


async def database_bytes(session: AsyncSession, settings: Settings) -> int:
    """Hoe groot de database is: het bestand (SQLite) of pg_database_size (PostgreSQL)."""
    path = sqlite_path(settings)
    if path is not None:
        return await anyio.to_thread.run_sync(sqlite_bytes, path)
    try:
        size = await session.scalar(text("SELECT pg_database_size(current_database())"))
    except Exception:  # noqa: BLE001 - geen rechten of een andere database: dan onbekend
        await session.rollback()
        return 0
    return int(size or 0)


@dataclass
class _Scan:
    previews: int
    uploads: int
    disk: Disk | None


def _scan(settings: Settings) -> _Scan:
    previews = dir_bytes(settings.data_dir / "previews")
    uploads = dir_bytes(settings.upload_dir)
    try:
        found = shutil.disk_usage(settings.data_dir)
        disk = Disk(total_bytes=found.total, used_bytes=found.used, free_bytes=found.free)
    except OSError:
        disk = None
    return _Scan(previews, uploads, disk)


async def measure(settings: Settings) -> NodeStorage:
    """Meet nu (de mappen in een thread). Gebruik local_storage() voor de cache."""
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        blobs = int(await session.scalar(select(func.coalesce(func.sum(Blob.size), 0))) or 0)
        database = await database_bytes(session, settings)
        await session.commit()
    scan = await anyio.to_thread.run_sync(_scan, settings)
    known = blobs + scan.previews + scan.uploads + database
    other = max(0, scan.disk.used_bytes - known) if scan.disk else 0
    return NodeStorage(
        name=settings.node_name,
        disk=scan.disk,
        parts=StorageParts(
            blobs=blobs,
            previews=scan.previews,
            uploads=scan.uploads,
            database=database,
            other=other,
        ),
        measured_at=datetime.now(UTC),
    )


# Per datamap de laatste meting (tests draaien elk met een eigen map)
_cache: dict[Path, NodeStorage] = {}
_locks: dict[Path, asyncio.Lock] = {}
# Hoe vaak er echt gemeten is (voor tests: niet per verzoek)
scans = 0


async def refresh(settings: Settings) -> NodeStorage:
    """Meet opnieuw en zet het in de cache (het opruimwerk roept dit aan)."""
    global scans
    lock = _locks.setdefault(settings.data_dir, asyncio.Lock())
    async with lock:
        scans += 1
        found = await measure(settings)
        _cache[settings.data_dir] = found
        return found


async def local_storage(settings: Settings) -> NodeStorage:
    """De laatste meting van deze node; alleen als er nog geen is, wordt er nu gemeten."""
    found = _cache.get(settings.data_dir)
    if found is not None:
        return found
    lock = _locks.setdefault(settings.data_dir, asyncio.Lock())
    async with lock:
        found = _cache.get(settings.data_dir)
        if found is not None:  # een ander verzoek mat intussen
            return found
    return await refresh(settings)


def forget(settings: Settings) -> None:
    """Voor tests: de cache van deze datamap weg."""
    _cache.pop(settings.data_dir, None)


async def _housekeeping(settings: Settings) -> None:
    await refresh(settings)


jobs.housekeeping("usage.refresh", _housekeeping, REFRESH)
