"""Previews opnieuw maken na een hogere previews.RENDER_VERSION: de keten preview.outdated,
wat opnieuw gemaakt wordt (kantoordocumenten, HEIC/HEIF/TIFF) en wat alleen het nieuwe
versienummer krijgt, en dat nieuwe uploads voorgaan."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app import jobs, previews
from app.models import Blob, Job
from app.storage import BlobStore
from tests.factories import create_blob, create_file, create_user, maker
from tests.test_office import docx_bytes
from tests.test_previews import picture

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def old_blob(settings, data: bytes, mime: str, name: str):
    """Een bestand met een preview van vóór deze versie (render_version 0)."""
    async with maker(settings)() as session:
        owner = await create_user(session, f"{name}@example.nl")
        node = await create_file(
            session, owner, name=name, data=data, mime=mime, blobs=BlobStore(settings.blob_dir)
        )
        blob = await session.get(Blob, node.blob_id)
        blob.preview = "ready"
        blob.preview_meta = {"sizes": []}
        blob.render_version = 0
        await session.commit()
        return node.blob_id


async def blob_of(settings, sha):
    async with maker(settings)() as session:
        return await session.get(Blob, sha)


@pytest.mark.anyio
async def test_kantoordocument_opnieuw_en_de_rest_alleen_het_nummer(settings):
    docx = await old_blob(settings, docx_bytes(), DOCX, "brief.docx")
    png = await old_blob(settings, picture("PNG", (50, 50)), "image/png", "a.png")
    assert await previews.enqueue_outdated(settings) == 1
    assert await previews.enqueue_outdated(settings) == 0  # er staat er al een klaar
    assert await jobs.run_pending(settings) == 1  # alleen de keten: het opnieuw maken wacht
    assert (await blob_of(settings, png)).render_version == previews.RENDER_VERSION
    old = await blob_of(settings, docx)
    assert old.render_version == 0 and old.preview == "ready"  # de oude blijft zolang staan
    later = jobs.now() + timedelta(seconds=previews.RERENDER_DELAY + 1)
    assert await jobs.run_pending(settings, at=later) == 1
    new = await blob_of(settings, docx)
    assert new.render_version == previews.RENDER_VERSION and new.preview == "ready"
    assert new.preview_meta["office"] == "document"
    assert previews.doc_path(BlobStore(settings.blob_dir), docx).is_file()
    # Niets meer te doen: bij het volgende starten geen keten
    assert await previews.enqueue_outdated(settings) == 0


@pytest.mark.anyio
async def test_nieuwe_uploads_gaan_voor(settings):
    tiff = await old_blob(settings, picture("TIFF", (64, 64)), "image/tiff", "scan.tiff")
    assert await previews.enqueue_outdated(settings) == 1
    assert await jobs.run_pending(settings) == 1
    async with maker(settings)() as session:
        owner = await create_user(session, "nieuw@example.nl")
        fresh = await create_file(
            session,
            owner,
            name="nieuw.png",
            data=picture("PNG", (70, 70)),
            mime="image/png",
            blobs=BlobStore(settings.blob_dir),
        )
        await previews.queue(session, fresh.blob_id)
        await session.commit()
    later = jobs.now() + timedelta(seconds=previews.RERENDER_DELAY + 1)
    assert await jobs.run_pending(settings, at=later) == 2
    async with maker(settings)() as session:
        done = (
            await session.scalars(
                select(Job.payload).where(Job.type == previews.TYPE).order_by(Job.started_at)
            )
        ).all()
    assert [p["sha"] for p in done] == [fresh.blob_id, tiff]


@pytest.mark.anyio
async def test_de_keten_loopt_in_stukken_door_alles(settings):
    async with maker(settings)() as session:
        for i in range(450):
            blob = await create_blob(session, f"tekst {i}".encode(), mime="text/plain")
            blob.preview = "ready"
        await session.commit()
    assert await previews.enqueue_outdated(settings) == 1
    assert await jobs.run_pending(settings) == 3  # 200 + 200 + 50
    async with maker(settings)() as session:
        left = await session.scalar(
            select(func.count()).where(Blob.render_version < previews.RENDER_VERSION)
        )
        runs = await session.scalar(
            select(func.count()).where(Job.type == previews.OUTDATED, Job.status == "done")
        )
    assert left == 0 and runs == 3


@pytest.mark.anyio
async def test_maken_slaat_alleen_de_huidige_versie_over(settings, monkeypatch):
    sha = await old_blob(settings, picture("PNG", (40, 40)), "image/png", "a.png")
    calls = []
    real = previews.render

    async def counted(*args, **kw):
        calls.append(1)
        return await real(*args, **kw)

    monkeypatch.setattr(previews, "render", counted)
    await previews.make(settings, {"sha": sha})  # versie 0: opnieuw
    assert len(calls) == 1
    blob = await blob_of(settings, sha)
    assert blob.render_version == previews.RENDER_VERSION and blob.preview_meta["width"] == 40
    await previews.make(settings, {"sha": sha})  # nu de huidige: niets te doen
    assert len(calls) == 1


@pytest.mark.anyio
async def test_een_keten_die_al_loopt_krijgt_er_geen_naast(settings):
    await old_blob(settings, b"tekst", "text/plain", "a.txt")
    assert await previews.enqueue_outdated(settings) == 1
    async with maker(settings)() as session:
        await session.execute(
            update(Job).where(Job.type == previews.OUTDATED).values(status="running")
        )
        await session.commit()
    assert await previews.enqueue_outdated(settings) == 0
