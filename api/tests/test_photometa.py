"""De backfill van opnamedatums en EXIF (app/photometa.py): een keten van taken in stukken
van 200 blobs, die maar één keer tegelijk loopt en niet eindeloos herhaalt."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from app import jobs, photometa, previews, render_meta
from app.db import get_engine
from app.models import Blob, Job, Node, User
from app.storage import BlobStore
from tests.factories import create_file, create_user, maker
from tests.test_render_meta import camera_jpeg, no_gps

pytestmark = pytest.mark.anyio
FALLBACK = datetime(2025, 3, 1, 9, 0, tzinfo=UTC)
LATER = datetime.now(UTC) + timedelta(days=2)  # voorbij elke backoff


async def old_photo(session, owner, data: bytes, blobs: BlobStore | None, name="oud.jpg") -> Node:
    """Een foto van vóór fase 3: preview klaar, meta_version 0, de terugvaldatum."""
    node = await create_file(
        session, owner, name=name, data=data, mime="image/jpeg", blobs=blobs, taken_at=FALLBACK
    )
    blob = await session.get(Blob, node.blob_id)
    blob.preview, blob.preview_meta, blob.meta_version = "ready", {"width": 64, "height": 48}, 0
    return node


async def job_rows(settings) -> list[Job]:
    async with maker(settings)() as session:
        return list((await session.scalars(select(Job).where(Job.type == photometa.TYPE))).all())


async def test_oude_foto_krijgt_exif_en_datum(client, admin, settings):
    headers, tokens = admin
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        owner = await session.get(User, uuid.UUID(tokens["user"]["id"]))
        node = await old_photo(session, owner, camera_jpeg(), blobs)
        await session.commit()
        node_id, sha = node.id, node.blob_id

    assert await photometa.enqueue_backfill(settings) == 1
    assert await jobs.run_pending(settings) == 1
    async with maker(settings)() as session:
        blob = await session.get(Blob, sha)
        node = await session.get(Node, node_id)
    assert blob.meta_version == render_meta.VERSION
    assert blob.preview_meta["width"] == 64  # wat er al stond, blijft
    assert blob.preview_meta["exif"]["model"] == "Camera X1"
    assert node.taken_day == 20240701
    assert node.taken_at.replace(tzinfo=None) == datetime(2024, 7, 1, 12, 30, 5)

    # De viewer ziet de EXIF (GET /files/{id}/preview, P4), zonder GPS; de lijst (POST
    # /previews) blijft licht en heeft geen exif
    r = await client.get(f"/api/v1/files/{node_id}/preview", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["exif"]["make"] == "Fabrikant"
    no_gps(r.json())
    r = await client.post("/api/v1/previews", json={"node_ids": [str(node_id)]}, headers=headers)
    assert "exif" not in r.json()[str(node_id)]["meta"]
    no_gps(r.json())
    # En de tijdlijn de nieuwe datum
    r = await client.get("/api/v1/photos", params={"month": "2024-07"}, headers=headers)
    assert [i["id"] for i in r.json()["items"]] == [str(node_id)]
    no_gps(r.json())

    # Niets meer te doen: geen nieuwe keten
    assert await photometa.enqueue_backfill(settings) == 0


async def test_keten_over_450_blobs_in_drie_runs(settings):
    async with maker(settings)() as session:
        owner = await create_user(session, "f@example.nl")
        for i in range(450):
            await old_photo(session, owner, f"foto {i}".encode(), None, f"{i}.jpg")
        # Geen foto of video: doet niet mee
        await create_file(session, owner, name="a.txt", data=b"tekst")
        await session.commit()
    assert await photometa.enqueue_backfill(settings) == 1
    assert await jobs.run_pending(settings) == 3
    async with maker(settings)() as session:
        left = await session.scalar(
            select(func.count()).select_from(Blob).where(Blob.meta_version < render_meta.VERSION)
        )
        text = await session.scalar(select(Blob).where(Blob.mime == "text/plain"))
    assert left == 1 and text.meta_version == 0
    runs = await job_rows(settings)
    assert len(runs) == 3 and all(j.status == "done" for j in runs)
    assert sorted(j.payload["after"] for j in runs)[0] == ""


async def test_twee_keer_starten_geeft_geen_tweede_keten(settings):
    async with maker(settings)() as session:
        owner = await create_user(session, "f@example.nl")
        await old_photo(session, owner, b"x", None)
        await session.commit()
    assert await photometa.enqueue_backfill(settings) == 1
    assert await photometa.enqueue_backfill(settings) == 0
    assert len(await job_rows(settings)) == 1
    # Ook niet terwijl hij loopt
    async with maker(settings)() as session:
        job = await session.scalar(select(Job).where(Job.type == photometa.TYPE))
        job.status = "running"
        await session.commit()
    assert await photometa.enqueue_backfill(settings) == 0


async def test_on_dead_stopt_de_lus(settings, monkeypatch):
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        owner = await create_user(session, "f@example.nl")
        node = await old_photo(session, owner, camera_jpeg(), blobs)
        await session.commit()
        sha = node.blob_id

    async def crash(*_args, **_kw):
        raise jobs.JobError("Het voorbeeldproces stopte onverwacht (signaal 9)")

    monkeypatch.setattr(previews, "render", crash)
    assert await photometa.enqueue_backfill(settings) == 1
    ran = 0
    for day in range(5):
        ran += await jobs.run_pending(settings, at=LATER + timedelta(days=day))
    assert ran == 3  # max_attempts, daarna niet meer
    [job] = await job_rows(settings)
    assert job.status == "failed"
    async with maker(settings)() as session:
        blob = await session.get(Blob, sha)
    assert blob.meta_version == render_meta.VERSION  # gemarkeerd: geen nieuwe keten
    assert await photometa.enqueue_backfill(settings) == 0


async def test_onleesbaar_bestand_niet_opnieuw(settings):
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        owner = await create_user(session, "f@example.nl")
        kapot = await old_photo(session, owner, b"geen jpeg", blobs, "kapot.jpg")
        weg = await old_photo(session, owner, b"weg", None, "weg.jpg")  # niet op schijf
        await session.commit()
        ids = kapot.id, weg.id
    assert await photometa.enqueue_backfill(settings) == 1
    assert await jobs.run_pending(settings) == 1
    async with maker(settings)() as session:
        for node_id in ids:
            node = await session.get(Node, node_id)
            blob = await session.get(Blob, node.blob_id)
            assert blob.meta_version == render_meta.VERSION
            assert node.taken_at.replace(tzinfo=None) == FALLBACK.replace(tzinfo=None)
            assert "exif" not in (blob.preview_meta or {})


async def test_een_stuk_schrijft_kort(settings):
    """Eerst lezen en uitrekenen, dan schrijven in een vast aantal statements, hoe groot het
    stuk ook is. SQLite heeft één schrijver: zolang de backfill schrijft, wacht elk ander
    schrijvend verzoek op het slot, ook /auth/refresh bij het openen van de app. Per blob lezen
    en schrijven hield dat slot per stuk van 200 een halve seconde vast (na een update was de
    eerste weergave van /map/<10k> daardoor 1,0-1,3 s in plaats van 0,65 s)."""
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        owner = await create_user(session, "f@example.nl")
        nodes = []
        for d in range(1, 25):
            jpeg = camera_jpeg(f"2024:07:{d:02d} 12:30:05")
            nodes.append(await old_photo(session, owner, jpeg, blobs, f"{d}.jpg"))
        await session.commit()
        ids = [node.id for node in nodes]

    seen: list[str] = []
    engine = get_engine(settings).sync_engine

    def on_execute(_conn, _cursor, statement, _params, _context, executemany):
        seen.append(("many " if executemany else "") + statement.split()[0].upper())

    def on_commit(_conn):
        seen.append("COMMIT")

    event.listen(engine, "before_cursor_execute", on_execute)
    event.listen(engine, "commit", on_commit)
    try:
        await photometa.run(settings, {"after": ""})
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
        event.remove(engine, "commit", on_commit)

    first = next(i for i, s in enumerate(seen) if s.split()[-1] in ("UPDATE", "INSERT", "DELETE"))
    burst = seen[first : seen.index("COMMIT", first)]
    # De blobs en de bestanden, elk één executemany; geen leesronde per blob meer
    assert burst == ["many UPDATE", "many UPDATE"], seen
    async with maker(settings)() as session:
        for d, node_id in enumerate(ids, start=1):
            node = await session.get(Node, node_id)
            blob = await session.get(Blob, node.blob_id)
            assert node.taken_day == 20240700 + d
            assert blob.meta_version == render_meta.VERSION
            assert blob.preview_meta["exif"]["model"] == "Camera X1"
