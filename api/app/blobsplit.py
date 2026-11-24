"""Films en series op 2 kopieën, al de rest op 3 (fase 11; Jonas, 23-11-2026).

In het cluster staat de data op SeaweedFS. Per map kan SeaweedFS een andere replicatie kiezen
(`fs.configure -locationPrefix=…/blobs-media/ -replication=001`). Onze blobs staan op hun hash
(blobs/ab/cd/<sha256>), niet op hun plek in de boom; daarom een tweede map blobs-media/ naast
blobs/, en deze verhuizer.

De regel gaat per plek, niet per soort bestand: in blobs-media/ komt alleen inhoud waar ENKEL
bestanden in een bibliotheek voor films of series naar wijzen (wat vroeger in Plex stond). Een
video in iemands eigen mappen (wat vroeger op Nextcloud stond) blijft in blobs/, ook als
dezelfde film ook in de bibliotheek staat. Versies tellen mee.

Elk uur (en bij de start) een ronde, alleen met PLATFORM_BLOB_SPLIT=true:

1. Welke inhoud is "alleen media"? Eén query over de boom.
2. Wat alleen media is en nog in blobs/ staat: kopiëren naar blobs-media/ (een nieuwe write,
   zodat SeaweedFS de regel van die map toepast; hernoemen houdt de oude replicatie), daarna
   onder het slot van de BlobStore de oude kopie opzij (blobs/.weg/).
3. Wat in blobs-media/ staat maar niet meer alleen media is (bv. een film naar je eigen map
   gekopieerd): terug naar blobs/, op dezelfde manier.
4. Wat in .weg/ ligt en ouder is dan een dag, gaat echt weg. Zo kan wie het bestand nog open
   had (een stream, ffmpeg) rustig uitlezen.

Nieuwe inhoud komt altijd eerst in blobs/ (3 kopieën) en verhuist binnen het uur. Zonder
PLATFORM_BLOB_SPLIT doet dit niets, en blijft alles in blobs/ (de single heeft er niets aan).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

import anyio
from sqlalchemy import Integer, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.config import Settings
from app.db import get_engine
from app.models import MediaLibrary, Node, Version
from app.routers.files import tree
from app.storage import BlobStore

log = logging.getLogger(__name__)

KINDS = ("movies", "shows")  # films en series; muziek en eigen video's niet
ROUND_S = 3600
GRACE_S = 24 * 3600  # zo lang blijft een verhuisde kopie in .weg/
TRASH = ".weg"


async def _roots(session: AsyncSession) -> list[uuid.UUID]:
    return list(
        (
            await session.scalars(select(MediaLibrary.root_id).where(MediaLibrary.kind.in_(KINDS)))
        ).all()
    )


def _refs():
    """(blob, node) van elk bestand en elke versie."""
    nodes = select(Node.blob_id.label("b"), Node.id.label("n")).where(Node.blob_id.is_not(None))
    versions = select(Version.blob_id.label("b"), Version.node_id.label("n"))
    return union_all(nodes, versions).subquery("refs")


async def media_only(session: AsyncSession) -> set[str]:
    """De inhoud waar alleen bestanden (en versies) in een film- of seriebibliotheek naar
    wijzen."""
    roots = await _roots(session)
    if not roots:
        return set()
    inside = tree(roots)
    refs = _refs()
    # Elke verwijzing naar inhoud die ook in een bibliotheek staat, met binnen of buiten
    candidates = (
        select(Node.blob_id).join(inside, inside.c.id == Node.id).where(Node.blob_id.is_not(None))
    )
    rows = await session.execute(
        select(refs.c.b, inside.c.id.is_not(None))
        .outerjoin(inside, inside.c.id == refs.c.n)
        .where(refs.c.b.in_(candidates))
    )
    good: set[str] = set()
    bad: set[str] = set()
    for sha, within in rows:
        (good if within else bad).add(sha)
    await session.commit()  # de leestransactie dicht (PostgreSQL)
    return good - bad


async def still_media_only(session: AsyncSession, sha: str) -> bool:
    """Dezelfde vraag voor één blob, vlak voor het verhuizen (onder het slot)."""
    roots = set(await _roots(session))
    refs = _refs()
    starts = (await session.scalars(select(refs.c.n).where(refs.c.b == sha))).all()
    if not roots or not starts:
        return False
    # Omhoog in de boom vanaf elk bestand; elk moet een bibliotheekmap boven zich hebben
    top = select(
        Node.id.label("origin"),
        Node.id.label("id"),
        Node.parent_id.label("parent_id"),
        literal(0, Integer).label("depth"),
    ).where(Node.id.in_(set(starts)))
    up = top.cte("up_split", recursive=True)
    parent = aliased(Node)
    up = up.union_all(
        select(up.c.origin, parent.id, parent.parent_id, up.c.depth + 1).where(
            parent.id == up.c.parent_id
        )
    )
    under = set((await session.scalars(select(up.c.origin).where(up.c.id.in_(roots)))).all())
    await session.commit()
    return set(starts) <= under


def _copy(src: Path, folder: Path) -> Path:
    """Kopie in een werkmap; geeft het tijdelijke bestand. Controle op de grootte."""
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"split-{uuid.uuid4().hex}.part"
    try:
        shutil.copyfile(src, tmp)
        if tmp.stat().st_size != src.stat().st_size:
            raise OSError(f"kopie van {src.name} is onvolledig")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def _aside(path: Path, root: Path) -> None:
    """Een oude kopie opzij in <root>/.weg/ (hernoemen: wie hem open heeft, leest verder)."""
    target = root / TRASH / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)
    os.utime(target)


async def _move(session: AsyncSession, blobs: BlobStore, sha: str, to_media: bool) -> bool:
    src = blobs.main_path(sha) if to_media else blobs.media_path(sha)
    dest = blobs.media_path(sha) if to_media else blobs.main_path(sha)
    src_root = blobs.root if to_media else blobs.media
    work = (blobs.media if to_media else blobs.root) / ".tmp"
    if not src.is_file():
        return False
    tmp = await anyio.to_thread.run_sync(_copy, src, work)
    try:
        async with blobs.lock:
            # Intussen weggegooid, al verhuisd, of niet meer (of nog niet) alleen media?
            if not src.is_file() or dest.exists():
                return False
            if await still_media_only(session, sha) != to_media:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dest)
            _aside(src, src_root)
            return True
    finally:
        tmp.unlink(missing_ok=True)


def _listed(media: Path) -> set[str]:
    """De inhoud in blobs-media/ (alleen ab/cd/<sha256>, geen werkmappen)."""
    found: set[str] = set()
    if not media.is_dir():
        return found
    for first in media.iterdir():
        if len(first.name) != 2 or not first.is_dir():
            continue
        for second in first.iterdir():
            if not second.is_dir():
                continue
            found.update(p.name for p in second.iterdir() if len(p.name) == 64)
    return found


def _purge(root: Path, now: float) -> int:
    folder = root / TRASH
    gone = 0
    if folder.is_dir():
        for p in folder.iterdir():
            try:
                if now - p.stat().st_mtime > GRACE_S:
                    p.unlink()
                    gone += 1
            except FileNotFoundError:
                pass
    return gone


async def run(session: AsyncSession, blobs: BlobStore) -> dict[str, int]:
    """Eén ronde; geeft wat er verhuisde."""
    blobs.media.mkdir(parents=True, exist_ok=True)
    BlobStore._media_seen[blobs.media] = (True, time.monotonic())  # path() kijkt er nu ook
    want = await media_only(session)
    have = await anyio.to_thread.run_sync(_listed, blobs.media)
    out = {"naar_media": 0, "terug": 0, "weg": 0}
    for sha in sorted(want - have):
        try:
            if await _move(session, blobs, sha, to_media=True):
                out["naar_media"] += 1
        except OSError as exc:
            log.warning("blobsplit: %s niet naar blobs-media: %s", sha, exc)
    for sha in sorted(have - want):
        try:
            if await _move(session, blobs, sha, to_media=False):
                out["terug"] += 1
        except OSError as exc:
            log.warning("blobsplit: %s niet terug naar blobs: %s", sha, exc)
    now = time.time()
    for root in (blobs.root, blobs.media):
        out["weg"] += await anyio.to_thread.run_sync(_purge, root, now)
    return out


async def loop(settings: Settings) -> None:
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    blobs = BlobStore(settings.blob_dir)
    while True:
        try:
            async with maker() as session:
                done = await run(session, blobs)
            if any(done.values()):
                log.info("blobsplit: %s", done)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - de volgende ronde opnieuw
            log.exception("blobsplit: ronde mislukt")
        await asyncio.sleep(ROUND_S)
