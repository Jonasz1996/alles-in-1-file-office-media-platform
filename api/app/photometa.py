"""De metadata van bestaande foto's en video's (alsnog) lezen: een achtergrondtaak die in
stukken van 200 blobs door alles met een oude Blob.meta_version loopt.

Eén keten (job-type "photometa", key "photometa:backfill"): elke run pakt de volgende 200
blobs (op sha, na payload["after"]) met meta_version < render_meta.VERSION en een afbeelding
of video als soort, leest ze in één renderproces (previews.render met mode "meta": alleen de
headers), zet per blob de opnamedatum en EXIF (photodate.set_blob_meta en retime_nodes: eerst
alles uitrekenen, dan in een paar statements schrijven) en zet dan zijn opvolger klaar met
jobs.chain. Minder dan 200 gevonden: dit was de laatste.

Nieuwe uploads hebben dit niet nodig: de preview-taak leest dezelfde metadata (render.py roept
render_meta aan) en zet meta_version al. Het schrijven gebeurt onder BlobStore.lock, net als
het neerzetten van een preview (previews._store): zo overschrijft de een nooit het
preview_meta dat de ander net schreef.

Mislukt een run definitief (het proces crasht steeds op hetzelfde bestand), dan zet on_dead
meta_version voor dat stuk toch op VERSION en gaat de keten verder: geen eindeloze lus.
"""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import jobs, photodate, previews, render_meta
from app.config import Settings
from app.db import get_engine
from app.models import Blob
from app.storage import BlobStore

TYPE = "photometa"
KEY = "photometa:backfill"
BATCH = 200


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def _todo():
    """Blobs die (opnieuw) gelezen moeten worden."""
    return (
        Blob.meta_version < render_meta.VERSION,
        or_(Blob.mime.like("image/%"), Blob.mime.like("video/%")),
    )


async def _batch(session: AsyncSession, after: str) -> list[tuple[str, str | None]]:
    rows = await session.execute(
        select(Blob.sha256, Blob.mime)
        .where(Blob.sha256 > after, *_todo())
        .order_by(Blob.sha256)
        .limit(BATCH)
    )
    return [(sha, mime) for sha, mime in rows]


async def _read(settings: Settings, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Metadata van deze bestanden uit één renderproces, met een eigen werkmap."""
    if not items:
        return {}
    blobs = BlobStore(settings.blob_dir)
    tmp = previews.root(blobs) / ".tmp"
    await anyio.Path(tmp).mkdir(parents=True, exist_ok=True)
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=tmp)))
    try:
        request = {
            "mode": "meta",
            "items": items,
            "max_pixels": settings.preview_max_pixels,
            "memory_mb": settings.preview_memory_mb,
        }
        result = await previews.render(settings, request, work)
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
    found = result.get("results")
    if not isinstance(found, dict):
        raise jobs.JobError("Het metadataproces gaf een onverwacht antwoord")
    return found


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    """Eén run van de keten: hooguit BATCH blobs na payload["after"]."""
    after = str(payload.get("after") or "")
    blobs = BlobStore(settings.blob_dir)
    maker = _maker(settings)
    async with maker() as session:
        batch = await _batch(session, after)
    if not batch:
        return
    present = {
        sha: mime
        for sha, mime in batch
        if await anyio.Path(blobs.path(sha)).is_file()  # weg op schijf: niets te lezen
    }
    items = [
        {"sha": sha, "source": str(blobs.path(sha)), "mime": mime} for sha, mime in present.items()
    ]
    results = await _read(settings, items)
    async with blobs.lock:
        async with maker() as session:
            # Eerst alles lezen en uitrekenen, dan schrijven in een paar statements. SQLite
            # heeft één schrijver: zolang deze transactie schrijft, wacht elk ander schrijvend
            # verzoek (inloggen, /auth/refresh bij het openen van de app) op het slot. Per blob
            # lezen en schrijven hield dat slot een halve seconde per stuk vast.
            found = {
                blob.sha256: blob
                for blob in await session.scalars(
                    select(Blob)
                    .where(Blob.sha256.in_([sha for sha, _mime in batch]))
                    .execution_options(populate_existing=True)
                )
            }
            taken: list[tuple[str, datetime]] = []
            for sha, _mime in batch:
                blob = found.get(sha)
                if blob is None or blob.meta_version >= render_meta.VERSION:
                    continue  # opgeruimd, of de preview-taak was intussen al zo ver
                meta = results.get(sha)
                if sha not in present or not isinstance(meta, dict) or "error" in meta:
                    # Niet te lezen: niet opnieuw proberen, en de datum die er is laten staan
                    blob.meta_version = render_meta.VERSION
                    continue
                meta = dict(meta)
                capture = photodate.split_capture(meta)
                exif = meta.get("exif") if isinstance(meta.get("exif"), dict) else None
                at = photodate.set_blob_meta(blob, capture, exif, settings.timezone)
                if at is not None:
                    taken.append((sha, at))
            await session.flush()
            await photodate.retime_nodes(session, taken)
            if len(batch) == BATCH:
                await jobs.chain(session, TYPE, {"after": batch[-1][0]}, KEY)
            await session.commit()


async def mark_dead(settings: Settings, payload: dict[str, Any], _error: str) -> None:
    """Na de laatste mislukte poging: dat stuk als gelezen markeren en verder met het
    volgende, zodat één kapot bestand de keten niet eindeloos laat herhalen."""
    after = str(payload.get("after") or "")
    async with _maker(settings)() as session:
        batch = await _batch(session, after)
        if not batch:
            return
        await session.execute(
            update(Blob)
            .where(Blob.sha256.in_([sha for sha, _ in batch]))
            .values(meta_version=render_meta.VERSION)
            .execution_options(synchronize_session=False)
        )
        if len(batch) == BATCH:
            await jobs.chain(session, TYPE, {"after": batch[-1][0]}, KEY)
        await session.commit()


async def enqueue_backfill(settings: Settings) -> int:
    """Bij het starten: de backfill klaarzetten als er iets te doen is. Geeft het aantal nieuwe
    taken (0 of 1); loopt of wacht er al een keten, dan geen tweede."""
    async with _maker(settings)() as session:
        todo = await session.scalar(select(Blob.sha256).where(*_todo()).limit(1))
        if todo is None:
            return 0
        added = await jobs.enqueue(session, TYPE, {"after": ""}, key=KEY)
        await session.commit()
    return 1 if added else 0


jobs.register(TYPE, run, on_dead=mark_dead)
