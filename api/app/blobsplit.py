"""Replicatie per bibliotheek (Jonas, 24-11-2026: "Films x2, Series x3"), naast de standaard van
het cluster. Volgt op fase 11 (23-11: films en series op 2 kopieën, de rest op 3).

In het cluster staat de data op SeaweedFS. Per map kan SeaweedFS een andere replicatie kiezen
(`fs.configure -locationPrefix=/blobs-r2/ -replication=001`). Onze blobs staan op hun hash
(blobs/ab/cd/<sha256>), niet op hun plek in de boom; daarom een map per aantal kopieën dat
afwijkt van de standaard (blobs-r1/, blobs-r2/, blobs-r3/ naast blobs/), en deze verhuizer.
docs/runbooks/replicatie.md zegt hoe je het cluster instelt.

Het doel van een inhoud is het hoogste aantal over al haar verwijzingen (bestanden en versies,
ook in de prullenbak): een verwijzing in een bibliotheek telt met de kopieën van die
bibliotheek (MediaLibrary.copies; NULL is de standaard; in elkaar: de hoogste), een verwijzing
buiten elke bibliotheek met de standaard (PLATFORM_BLOB_DEFAULT_COPIES). Elke soort
bibliotheek telt: de keuze is per bibliotheek. Dezelfde film in Films (x2) en in je eigen map
(standaard 3) blijft dus op 3; in Films (x2) en Series (x3) op 3. Is het doel de standaard,
dan staat de inhoud in blobs/, anders in blobs-r<doel>/.

Elk uur (en bij de start, en kort na een wijziging op /beheer) een ronde, alleen met
PLATFORM_BLOB_SPLIT=true:

1. Het doel van alle inhoud waar een bibliotheek met een afwijkend aantal naar wijst: een paar
   queries over de boom (één per aantal).
2. Wat niet op zijn plek staat: kopiëren naar de juiste map (een nieuwe write, zodat SeaweedFS
   de regel van die map toepast; hernoemen houdt de oude replicatie), daarna onder het slot van
   de BlobStore het doel opnieuw nakijken, de kopie neerzetten en de oude opzij (<map>/.weg/).
   Ook wat nog in blobs-media/ van fase 11 staat, verhuist zo naar zijn plek.
3. Wat in .weg/ ligt en ouder is dan een dag, gaat echt weg. Zo kan wie het bestand nog open
   had (een stream, ffmpeg) rustig uitlezen.

Nieuwe inhoud komt altijd eerst in blobs/ en verhuist bij de volgende ronde. Zonder
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

from app import access
from app.config import Settings
from app.db import get_engine
from app.models import MediaLibrary, Node, Version
from app.storage import COPIES, BlobStore

log = logging.getLogger(__name__)

ROUND_S = 3600
SOON_S = 60  # zo snel na een wijziging op /beheer de volgende ronde
GRACE_S = 24 * 3600  # zo lang blijft een verhuisde kopie in .weg/
TRASH = ".weg"

# Wekt de lus na een wijziging van de kopieën van een bibliotheek (kick)
_wake: asyncio.Event | None = None


def kick() -> None:
    """Na een wijziging van de kopieën: de volgende ronde binnen SOON_S, niet pas over een uur.
    Zonder lus (PLATFORM_BLOB_SPLIT uit) doet dit niets."""
    if _wake is not None:
        _wake.set()


def _refs(name: str = "refs"):
    """(blob, node) van elk bestand en elke versie."""
    nodes = select(Node.blob_id.label("b"), Node.id.label("n")).where(Node.blob_id.is_not(None))
    versions = select(Version.blob_id.label("b"), Version.node_id.label("n"))
    return union_all(nodes, versions).subquery(name)


def _below(roots: list[uuid.UUID], name: str):
    """Recursieve CTE (id) van deze mappen en alles eronder, zoals files.tree maar met een eigen
    naam: één query gebruikt er twee."""
    top = select(Node.id, literal(0, Integer).label("depth")).where(Node.id.in_(roots))
    found = top.cte(name, recursive=True)
    child = aliased(Node)
    return found.union_all(select(child.id, found.c.depth + 1).where(child.parent_id == found.c.id))


def effective(copies: int | None, default: int) -> int:
    return copies if copies in COPIES else default


async def _groups(session: AsyncSession, default: int) -> dict[int, list[uuid.UUID]]:
    """De mappen van de bibliotheken per aantal kopieën (NULL telt als de standaard)."""
    groups: dict[int, list[uuid.UUID]] = {}
    for root, copies in await session.execute(select(MediaLibrary.root_id, MediaLibrary.copies)):
        groups.setdefault(effective(copies, default), []).append(root)
    return groups


async def targets(session: AsyncSession, default: int) -> dict[str, int]:
    """Het doel van elke inhoud waarvan het doel niet de standaard is (alle andere: de
    standaard). Eén query voor de verwijzingen en één per aantal kopieën."""
    groups = await _groups(session, default)
    special = [root for copies, roots in groups.items() if copies != default for root in roots]
    if not special:
        await session.commit()
        return {}
    # De inhoud met een verwijzing in een bibliotheek met een afwijkend aantal; alleen daarvan
    # kan het doel iets anders zijn dan de standaard
    inside = _below(special, "special")
    near = _refs("near")
    candidates = select(near.c.b).join(inside, inside.c.id == near.c.n)
    refs = _refs()
    rows = await session.execute(select(refs.c.b, refs.c.n).where(refs.c.b.in_(candidates)))
    level: dict[tuple[str, uuid.UUID], int] = {(b, n): 0 for b, n in rows}
    for copies, roots in groups.items():
        found = _below(roots, f"r{copies}")
        rows = await session.execute(
            select(refs.c.b, refs.c.n)
            .join(found, found.c.id == refs.c.n)
            .where(refs.c.b.in_(candidates))
        )
        for b, n in rows:
            level[(b, n)] = max(level.get((b, n), 0), copies)
    await session.commit()  # de leestransactie dicht (PostgreSQL)
    out: dict[str, int] = {}
    for (sha, _node), copies in level.items():
        # Buiten elke bibliotheek (0): de standaard
        out[sha] = max(out.get(sha, 0), copies or default)
    return {sha: copies for sha, copies in out.items() if copies != default}


async def target_of(session: AsyncSession, sha: str, default: int) -> int:
    """Hetzelfde doel voor één inhoud, vlak voor het verhuizen (onder het slot): van elk
    bestand omhoog in de boom, de bibliotheken op de weg."""
    refs = _refs()
    starts = set((await session.scalars(select(refs.c.n).where(refs.c.b == sha))).all())
    if not starts:
        await session.commit()
        return default
    up = access.ancestry(Node.id.in_(starts))
    rows = await session.execute(
        select(up.c.origin, MediaLibrary.copies).join(MediaLibrary, MediaLibrary.root_id == up.c.id)
    )
    level: dict[uuid.UUID, int] = {}
    for origin, copies in rows:
        level[origin] = max(level.get(origin, 0), effective(copies, default))
    await session.commit()
    return max(level.get(start, default) for start in starts)


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


def _aside(path: Path) -> None:
    """Een oude kopie opzij in <map>/.weg/ (hernoemen: wie hem open heeft, leest verder).
    path is <map>/ab/cd/<sha256>."""
    target = path.parents[2] / TRASH / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)
    os.utime(target)


def folder_of(blobs: BlobStore, copies: int, default: int) -> Path:
    """De map voor dit doel: blobs/ voor de standaard, anders blobs-r<doel>/."""
    return blobs.root if copies == default else blobs.copies_dir(copies)


async def _move(
    session: AsyncSession, blobs: BlobStore, sha: str, folder: Path, default: int
) -> bool:
    """Zet de inhoud in deze map en de andere kopieën opzij. True als er iets verhuisde."""
    dest = blobs.at(folder, sha)
    srcs = [p for p in blobs.everywhere(sha) if p != dest and p.is_file()]
    if not srcs:
        return False
    # Staat ze er al (een vorige ronde stopte halverwege), dan alleen de rest opzij
    tmp = None
    if not dest.is_file():
        tmp = await anyio.to_thread.run_sync(_copy, srcs[0], folder / ".tmp")
    try:
        async with blobs.lock:
            # Intussen weggegooid, al verhuisd, of hoort ze nu ergens anders?
            if folder_of(blobs, await target_of(session, sha, default), default) != folder:
                return False
            if tmp is not None:
                if not srcs[0].is_file() or dest.exists():
                    return False
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, dest)
                blobs.saw(folder)
            elif not dest.is_file():
                return False
            for src in srcs:
                if src.is_file():
                    _aside(src)
            return True
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _listed(folder: Path) -> set[str]:
    """De inhoud in een map naast blobs/ (alleen ab/cd/<sha256>, geen werkmappen)."""
    found: set[str] = set()
    if not folder.is_dir():
        return found
    for first in folder.iterdir():
        if len(first.name) != 2 or not first.is_dir():
            continue
        for second in first.iterdir():
            if not second.is_dir():
                continue
            found.update(p.name for p in second.iterdir() if len(p.name) == 64)
    return found


def _in_main(blobs: BlobStore, shas: list[str]) -> list[str]:
    return [sha for sha in shas if blobs.main_path(sha).exists()]


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


def _key(blobs: BlobStore, folder: Path) -> str:
    return "naar_blobs" if folder == blobs.root else f"naar_{folder.name.rsplit('-', 1)[-1]}"


async def run(session: AsyncSession, blobs: BlobStore, default: int = 3) -> dict[str, int]:
    """Eén ronde; geeft per map hoeveel er naartoe verhuisde, en hoeveel oude kopieën weg zijn.
    default: het aantal kopieën van blobs/ (PLATFORM_BLOB_DEFAULT_COPIES)."""
    want = await targets(session, default)
    others = blobs.others()
    have = {folder: await anyio.to_thread.run_sync(_listed, folder) for folder in others}
    plan: dict[str, Path] = {}
    placed: list[str] = []
    for sha, copies in want.items():
        folder = folder_of(blobs, copies, default)
        if sha not in have.get(folder, ()):
            plan[sha] = folder
        else:
            placed.append(sha)
    # Al op zijn plek, maar ook nog in blobs/ (een ronde stopte tussen neerzetten en opzij
    # leggen): die kopie moet nog opzij. blobs/ zelf lijsten is te veel; één blik per inhoud
    for sha in await anyio.to_thread.run_sync(_in_main, blobs, placed):
        plan[sha] = folder_of(blobs, want[sha], default)
    for folder, shas in have.items():
        for sha in shas:
            if folder_of(blobs, want.get(sha, default), default) != folder:
                plan[sha] = folder_of(blobs, want.get(sha, default), default)
    out = {"naar_blobs": 0, **{f"naar_r{n}": 0 for n in COPIES}, "weg": 0}
    for sha, folder in sorted(plan.items()):
        try:
            if await _move(session, blobs, sha, folder, default):
                out[_key(blobs, folder)] += 1
        except OSError as exc:
            log.warning("blobsplit: %s niet naar %s: %s", sha, folder.name, exc)
    now = time.time()
    for root in (blobs.root, *others):
        out["weg"] += await anyio.to_thread.run_sync(_purge, root, now)
    return out


async def loop(settings: Settings) -> None:
    global _wake
    _wake = asyncio.Event()
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    blobs = BlobStore(settings.blob_dir)
    default = settings.blob_default_copies
    log.info("blobsplit: aan, standaard %d kopieën", default)
    try:
        while True:
            _wake.clear()
            try:
                async with maker() as session:
                    done = await run(session, blobs, default)
                if any(done.values()):
                    log.info("blobsplit: %s", done)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - de volgende ronde opnieuw
                log.exception("blobsplit: ronde mislukt")
            try:
                await asyncio.wait_for(_wake.wait(), ROUND_S)
                await asyncio.sleep(SOON_S)  # wie meer bibliotheken wijzigt, één ronde
            except TimeoutError:
                pass
    finally:
        _wake = None
