"""De tijdlijn bij 50.000 foto's: /photos/months en /photos?month= in minder dan 100 ms
(mediaan van 5 verzoeken, gemeten aan het hele verzoek via de ASGI-client)."""

import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from app.models import Blob, Node
from app.photodate import day_of
from app.text import fold
from tests.factories import create_folder, create_user, maker
from tests.test_photos import login

pytestmark = pytest.mark.anyio
PHOTOS = 50_000
BLOBS = 2000
LIMIT_MS = 100


async def seed(settings) -> None:
    async with maker(settings)() as session:
        owner = await create_user(session, "veel@example.nl")
        folders = [(await create_folder(session, owner, name=f"Jaar {i}")).id for i in range(12)]
        # Een paar mappen in de prullenbak (met foto's erin) en elders: telt niet
        trash = await create_folder(session, owner, name="Weg", deleted=True)
        folders_all = [*folders, trash.id]
        shas = [f"{i:064x}" for i in range(BLOBS)]
        await session.execute(
            insert(Blob),
            [
                {
                    "sha256": sha,
                    "size": 1000,
                    "ref_count": 0,
                    "mime": "image/webp",
                    "preview": "ready",
                    "preview_meta": {"width": 256, "height": 256, "sizes": [256]},
                    "meta_version": 1,
                    "render_version": 0,
                }
                for sha in shas
            ],
        )
        start = datetime(2015, 1, 1, tzinfo=UTC)
        rows = []
        for i in range(PHOTOS):
            taken = start + timedelta(hours=2 * i)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "parent_id": folders_all[i % len(folders_all)],
                    "owner_id": owner.id,
                    "name": f"foto-{i}.webp",
                    "search_name": fold(f"foto-{i}.webp"),
                    "kind": "file",
                    "mime": "image/webp",
                    "size": 1000,
                    "blob_id": shas[i % BLOBS],
                    "created_at": taken,
                    "modified_at": taken,
                    "taken_at": taken,
                    "taken_day": day_of(taken),
                }
            )
            if len(rows) == 5000:
                await session.execute(insert(Node), rows)
                rows = []
        if rows:
            await session.execute(insert(Node), rows)
        await session.commit()


async def timed(client, headers, path, **params) -> tuple[float, dict]:
    times, body = [], None
    for _ in range(5):
        t = time.perf_counter()
        r = await client.get(path, params=params, headers=headers)
        times.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200, r.text
        body = r.json()
    return statistics.median(times), body


async def test_tijdlijn_snel_bij_50k(client, admin, settings):
    await seed(settings)
    headers = await login(client, "veel@example.nl")
    await client.get("/api/v1/photos/months", headers=headers)  # opwarmen (verbinding, plan)

    ms, body = await timed(client, headers, "/api/v1/photos/months")
    months = body["months"]
    assert sum(m["count"] for m in months) == PHOTOS - PHOTOS // 13
    print(f"\n/photos/months: {ms:.1f} ms ({len(months)} maanden)")
    assert ms < LIMIT_MS, ms

    busiest = max(months, key=lambda m: m["count"])["month"]
    ms, body = await timed(client, headers, "/api/v1/photos", month=busiest)
    assert len(body["items"]) == min(500, max(m["count"] for m in months))
    print(f"/photos?month={busiest}: {ms:.1f} ms ({len(body['items'])} items)")
    assert ms < LIMIT_MS, ms

    # Een pagina diep in de tijdlijn (met cursor)
    first = (await client.get("/api/v1/photos", headers=headers)).json()
    assert len(first["items"]) == 500 and first["next"]
    ms, body = await timed(client, headers, "/api/v1/photos", after=first["next"])
    assert body["items"][0]["id"] != first["items"][-1]["id"]
    print(f"/photos?after=…: {ms:.1f} ms")
    assert ms < LIMIT_MS, ms
