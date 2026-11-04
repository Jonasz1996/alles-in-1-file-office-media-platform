"""De backfill houdt de schrijver vrij (bouwontwerp fase 6, Z2; Keuze 20, C11).

SQLite in WAL-stand heeft één schrijver. De backfill schrijft daarom kort: per stuk van 500
blobs één transactie voor de rijen in analysis, en elke tekst in een eigen transactie
(analysis.write_text). Een gewone upload wacht zo nooit langer dan 1 s, ook niet als er net
een tekst van 1 MB met 10 kopieën in de index gaat (de triggers schrijven de tekst voor elke
node met die sha: gemeten door de critici 648 ms per schrijfactie, C11).

De keten is echt: de job analysis.backfill en de pomp in de rij index (jobs.Runner), de stap
text met de terugval zonder media (het renderproces van de app leest), de triggers van 0010.
Ondertussen uploadt Bram om de 100 ms een klein bestand via de API; elke upload duurt korter
dan 1 s.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import statistics
import time

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, jobs
from app.db import get_engine
from app.extract import step as extract_step
from app.models import Analysis, BlobText
from app.search import bench, hooks
from tests import test_files
from tests.factories import create_user

# zoek: ook in e2e/slim.sh op SQLite 3.46.1 (bouwontwerp §4.1)
pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

MAX_WAIT_S = 1.0
MB = 1024 * 1024


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


@pytest.fixture(autouse=True)
def _de_stap_text():
    analysis.reset()
    hooks.reset()
    extract_step.register()
    yield
    analysis.reset()
    hooks.reset()
    extract_step.register()


async def login(client, email: str) -> dict:
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "lang-genoeg-123"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def upload(client, headers, parent: str, name: str, data: bytes) -> dict:
    r = await test_files.put(client, headers, parent, name, data)
    assert r.status_code == 201, r.text
    return r.json()


async def counts(settings) -> tuple[int, int]:
    """(open, totaal) in analysis."""
    async with maker(settings)() as db:
        open_ = await db.scalar(
            sa.select(sa.func.count())
            .select_from(Analysis)
            .where(Analysis.state.in_(analysis.OPEN))
        )
        total = await db.scalar(sa.select(sa.func.count()).select_from(Analysis))
    return open_, total


async def run_backfill_with_uploads(
    settings, client, bram: dict, bram_root: str, before: int
) -> list[tuple[float, float]]:
    """De backfill en de pomp in de rij index; intussen uploadt Bram, tot de backfill klaar is
    (en minstens 10 keer). Daarna wacht de test tot ook Brams laatste upload gelezen is: de
    rij van een upload komt kort na de commit (analysis.on_blob_added). Geeft de duur van elke
    upload als (begin, eind)."""
    waits: list[tuple[float, float]] = []
    await analysis.start_backfill(settings)
    runner = jobs.Runner(settings, "index")
    await runner.start()
    try:
        deadline = time.monotonic() + 240
        i = 0
        while time.monotonic() < deadline:
            started = time.monotonic()
            await upload(client, bram, bram_root, f"tussendoor-{i}.txt", f"klein {i}".encode())
            waits.append((started, time.monotonic()))
            i += 1
            open_, total = await counts(settings)
            if total >= before and not open_ and i >= 10:
                break
            await asyncio.sleep(0.1)
        while time.monotonic() < deadline:
            open_, total = await counts(settings)
            if total == before + i and not open_:
                break
            await asyncio.sleep(0.05)
    finally:
        await runner.stop()
    return waits


async def test_een_upload_wacht_nooit_langer_dan_1_s_tijdens_de_backfill(
    settings, client, admin, monkeypatch
):
    """Het profiel "kopieën" (C11): één tekst van 1 MB met 10 kopieën bij Eva, en 60 gewone
    documenten; de backfill leest ze terwijl Bram uploadt."""
    headers, _ = admin
    async with maker(settings)() as db:
        await create_user(db, "eva@example.nl", password="lang-genoeg-123")
        await create_user(db, "bram@example.nl", password="lang-genoeg-123")
        await db.commit()
    eva = await login(client, "eva@example.nl")
    bram = await login(client, "bram@example.nl")
    eva_root = (await test_files.root(client, eva))["folder"]["id"]
    bram_root = (await test_files.root(client, bram))["folder"]["id"]
    rng = random.Random("kopieen")
    groot = bench.tekst(rng, MB).encode()
    sha = hashlib.sha256(groot).hexdigest()
    bron = await upload(client, eva, eva_root, "groot.txt", groot)
    for _ in range(10):
        r = await client.post(
            f"/api/v1/files/{bron['id']}/copy", json={"parent_id": eva_root}, headers=eva
        )
        assert r.status_code == 201, r.text
    for i in range(60):
        await upload(client, eva, eva_root, f"doc-{i}.txt", bench.tekst(rng, 2000).encode())
    # Zonder rijen (geen Runner): niets staat nog in de analyse; de backfill vindt alles
    async with maker(settings)() as db:
        assert await db.scalar(sa.select(sa.func.count()).select_from(Analysis)) == 0

    # Wanneer de tekst van 1 MB in blob_text (en via de triggers bij 11 nodes in fts_node) gaat
    writes: dict[str, tuple[float, float]] = {}
    write_text = analysis.write_text

    async def timed(settings, blob_sha, *args, **kwargs):
        started = time.monotonic()
        try:
            return await write_text(settings, blob_sha, *args, **kwargs)
        finally:
            writes[blob_sha] = (started, time.monotonic())

    monkeypatch.setattr(analysis, "write_text", timed)
    # Verschillende inhouden: de tekst van 1 MB (met zijn kopieën één blob) en de 60 documenten
    spans = await run_backfill_with_uploads(settings, client, bram, bram_root, 61)
    waits = [end - start for start, end in spans]

    async with maker(settings)() as db:
        states = (await db.scalars(sa.select(Analysis.state))).all()
        body = await db.scalar(
            sa.select(sa.func.length(BlobText.body)).where(BlobText.blob_sha == sha)
        )
        # De tekst van 1 MB staat bij alle 11 nodes in de index (de triggers van 0010)
        word = groot.split()[5].decode().strip(".").lower()
        hits = await db.scalar(
            sa.text("SELECT count(*) FROM fts_node WHERE fts_node MATCH :q"),
            {"q": f'body:"{word}"'},
        )
    assert set(states) == {"done"}, states
    assert body >= MB - 10
    assert hits >= 11
    assert len(waits) >= 10
    # De schrijfactie van 1 MB viel midden in de uploads van Bram
    big_start, big_end = writes[sha]
    assert spans[0][0] < big_start and big_end < spans[-1][1]
    print(
        f"\n=== kort === uploads {len(waits)}, max {max(waits) * 1000:.0f} ms, "
        f"mediaan {statistics.median(waits) * 1000:.0f} ms; "
        f"de tekst van 1 MB bij 11 nodes {(big_end - big_start) * 1000:.0f} ms"
    )
    assert max(waits) < MAX_WAIT_S, f"een upload wachtte {max(waits):.2f} s"
