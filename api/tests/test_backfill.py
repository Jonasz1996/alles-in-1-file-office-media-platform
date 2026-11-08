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
import sys
import time

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import analysis, jobs
from app.db import get_engine
from app.extract import step as extract_step
from app.models import Analysis, Blob, BlobText
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


async def counts(settings, shas: set[str] | None = None) -> tuple[int, int]:
    """(open, totaal) in analysis; met shas alleen de rijen van die inhouden.

    In één SELECT, dus van één moment. pysqlite begint geen transactie voor een SELECT: twee
    losse SELECTs zien elk een eigen stand, ook in één sessie. Committe de rij van een upload
    (analysis.on_blob_added, na de commit van die upload) tussen de twee in, dan telde hij wel
    in het totaal maar niet bij open, en stopte de test de pomp terwijl die rij op wait stond
    (CI run 87; Z6 vond hetzelfde in e2e/slim.sh, docs/fase-6/z6.md)."""
    only = [] if shas is None else [Analysis.blob_sha.in_(shas)]
    async with maker(settings)() as db:
        total, open_ = (
            await db.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.count().filter(Analysis.state.in_(analysis.OPEN)),
                )
                .select_from(Analysis)
                .where(*only)
            )
        ).one()
    return open_, total


async def run_backfill_with_uploads(
    settings, client, bram: dict, bram_root: str, before: int
) -> list[tuple[float, float]]:
    """De backfill en de pomp in de rij index; intussen uploadt Bram, tot de backfill klaar is
    (en minstens 10 keer), en daarna nog één keer: die upload begint pas als alles van de
    backfill gelezen is. Daarna wacht de test tot ook Brams uploads gelezen zijn: de rij van een
    upload komt kort na de commit (analysis.on_blob_added). Geeft de duur van elke upload als
    (begin, eind).

    "De backfill klaar" telt alleen de rijen van de before inhouden van vóór de backfill. Brams
    eigen uploads komen er om de ±150 ms bij; wachten tot er op het moment van kijken geen enkele
    open rij was, ook niet van Bram, hing van toeval af (de nacontrole van golf 1: de backfill
    was na 2 s klaar, en pas na 64 s, of nooit binnen de 240 s, zag de test een keer geen open
    rij van Bram).

    De laatste lus kijkt meteen na de laatste upload, terwijl de rij van die upload nog
    onderweg is; daarom komen open en totaal uit één SELECT (counts, CI run 87)."""
    waits: list[tuple[float, float]] = []
    async with maker(settings)() as db:
        backfill = set((await db.scalars(sa.select(Blob.sha256))).all())
    assert len(backfill) == before
    await analysis.start_backfill(settings)
    runner = jobs.Runner(settings, "index")
    await runner.start()
    try:
        deadline = time.monotonic() + 240
        i = 0
        klaar = False
        while time.monotonic() < deadline:
            started = time.monotonic()
            await upload(client, bram, bram_root, f"tussendoor-{i}.txt", f"klein {i}".encode())
            waits.append((started, time.monotonic()))
            i += 1
            if klaar:
                break
            open_, total = await counts(settings, backfill)
            klaar = total == before and not open_ and i >= 10
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
    # Een rij voor elke inhoud (de 61 van de backfill en elke upload van Bram), en elke rij done
    assert len(states) == 61 + len(waits), len(states)
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


async def test_de_rij_van_een_upload_commit_midden_in_een_telling(
    settings, client, admin, monkeypatch
):
    """CI run 87 (op 7ffe77a): de rij van Brams laatste upload komt uit een taak na de commit
    (analysis.on_blob_added) en committe midden in de eerste telling van de laatste lus. Las
    counts() open en totaal met twee SELECTs, dan zag de test open 0 (van vóór die commit) en
    het volle totaal (van erna), stopte de pomp, en bleef die rij op wait. Hier commit de rij van
    elke upload met opzet net na het eerste statement van een telling, als de pomp al het andere
    af heeft: zo valt hij altijd tussen de SELECTs, als die er twee zijn."""
    async with maker(settings)() as db:
        await create_user(db, "eva@example.nl", password="lang-genoeg-123")
        await create_user(db, "bram@example.nl", password="lang-genoeg-123")
        await db.commit()
    eva = await login(client, "eva@example.nl")
    bram = await login(client, "bram@example.nl")
    eva_root = (await test_files.root(client, eva))["folder"]["id"]
    bram_root = (await test_files.root(client, bram))["folder"]["id"]
    for i in range(5):
        await upload(client, eva, eva_root, f"doc-{i}.txt", f"document {i}".encode())

    plain = maker
    queue_new = analysis._queue_new
    ready: list[tuple[asyncio.Event, asyncio.Event]] = []  # (los, gecommit) per rij
    gated: list[str] = []

    async def open_rows() -> int:
        async with plain(settings)() as db:
            return await db.scalar(
                sa.select(sa.func.count())
                .select_from(Analysis)
                .where(Analysis.state.in_(analysis.OPEN))
            )

    async def held(settings, sha: str, owner) -> None:
        # Pas als de pomp al het andere af heeft: dan is open 0 zolang deze rij er niet is
        for _ in range(2000):
            if not await open_rows():
                break
            await asyncio.sleep(0.01)
        go, committed = asyncio.Event(), asyncio.Event()
        ready.append((go, committed))
        await go.wait()
        try:
            await queue_new(settings, sha, owner)
            gated.append(sha)
        finally:
            committed.set()

    class Counting(AsyncSession):
        """Na het eerste statement van elke sessie van de test (dus van elke telling): de rijen
        die klaarstaan los, en wachten tot ze gecommit zijn."""

        first = True

        async def _after_first(self) -> None:
            if not self.first:
                return
            self.first = False
            batch = list(ready)
            ready.clear()
            for go, _ in batch:
                go.set()
            for _, committed in batch:
                await asyncio.wait_for(committed.wait(), 10)

        async def execute(self, *args, **kwargs):
            result = await super().execute(*args, **kwargs)
            await self._after_first()
            return result

        async def scalar(self, *args, **kwargs):
            result = await super().scalar(*args, **kwargs)
            await self._after_first()
            return result

    monkeypatch.setattr(analysis, "_queue_new", held)
    monkeypatch.setattr(
        sys.modules[__name__],
        "maker",
        lambda settings: async_sessionmaker(
            get_engine(settings), class_=Counting, expire_on_commit=False
        ),
    )
    spans = await run_backfill_with_uploads(settings, client, bram, bram_root, 5)

    async with plain(settings)() as db:
        states = (await db.scalars(sa.select(Analysis.state))).all()
    # Elke upload ging door het hek: de rij van de laatste kwam pas na het eerste statement van
    # een telling, toen al het andere af was
    assert len(gated) == len(spans) >= 11
    assert len(states) == 5 + len(spans)
    assert set(states) == {"done"}, states


async def test_veel_kopieen_houden_de_schrijfactie_klein(settings, client, admin):
    """H1: de triggers schrijven de tekst voor elke node met die sha. Een tekst van 1 MB met 250
    kopieën was 251 MB in fts_node: Bram wachtte seconden, of kreeg "database is locked".
    write_text geeft elke node nu een deel van TEXT_BUDGET; Brams uploads blijven onder 1 s."""
    async with maker(settings)() as db:
        await create_user(db, "eva@example.nl", password="lang-genoeg-123")
        await create_user(db, "bram@example.nl", password="lang-genoeg-123")
        await db.commit()
    eva = await login(client, "eva@example.nl")
    bram = await login(client, "bram@example.nl")
    eva_root = (await test_files.root(client, eva))["folder"]["id"]
    bram_root = (await test_files.root(client, bram))["folder"]["id"]
    rng = random.Random("veel kopieën")
    groot = bench.tekst(rng, MB).encode()
    sha = hashlib.sha256(groot).hexdigest()
    bron = await upload(client, eva, eva_root, "groot.txt", groot)
    for i in range(250):
        r = await client.post(
            f"/api/v1/files/{bron['id']}/copy",
            json={"parent_id": eva_root, "name": f"kopie-{i}.txt"},
            headers=eva,
        )
        assert r.status_code == 201, r.text
    text = groot.decode()

    async def write() -> tuple[float, float]:
        started = time.monotonic()
        async with maker(settings)() as db:
            await analysis.write_text(db, sha, body=text)
        return started, time.monotonic()

    spans: list[tuple[float, float]] = []
    writer: asyncio.Task | None = None
    for i in range(400):
        started = time.monotonic()
        await upload(client, bram, bram_root, f"tussendoor-{i}.txt", f"klein {i}".encode())
        spans.append((started, time.monotonic()))
        if i == 3:
            writer = asyncio.create_task(write())
        if writer is not None and writer.done() and i >= 10:
            break
        await asyncio.sleep(0.05)
    assert writer is not None and writer.done()
    big_start, big_end = writer.result()
    waits = [end - start for start, end in spans]

    share = analysis.TEXT_BUDGET // 251
    async with maker(settings)() as db:
        length, truncated = (
            await db.execute(
                sa.select(sa.func.length(BlobText.body), BlobText.truncated).where(
                    BlobText.blob_sha == sha
                )
            )
        ).one()
        word = text[:share].split()[5].strip(".").lower()
        hits = await db.scalar(
            sa.text("SELECT count(*) FROM fts_node WHERE fts_node MATCH :q"),
            {"q": f'body:"{word}"'},
        )
    assert length == share and truncated == 1
    # Het begin van de tekst staat bij alle 251 nodes in de index
    assert hits >= 251
    assert spans[0][0] < big_start and big_end < spans[-1][1]
    print(
        f"\n=== kort === uploads {len(waits)}, max {max(waits) * 1000:.0f} ms; "
        f"de tekst bij 251 nodes {(big_end - big_start) * 1000:.0f} ms"
    )
    assert max(waits) < MAX_WAIT_S, f"een upload wachtte {max(waits):.2f} s"


async def test_het_deel_per_node_ook_bij_alleen_ocr(settings, client, admin, monkeypatch):
    """De regels van write_text met een klein budget: body eerst, dan ocr met wat over is, nooit
    minder dan TEXT_MIN_SHARE. Komt alleen ocr mee, dan gaat ook een te lange opgeslagen body
    terug. fts_node blijft gelijk met wat er staat (integrity-check)."""
    monkeypatch.setattr(analysis, "TEXT_BUDGET", 10_000)
    monkeypatch.setattr(analysis, "TEXT_MIN_SHARE", 100)
    async with maker(settings)() as db:
        await create_user(db, "eva@example.nl", password="lang-genoeg-123")
        await db.commit()
    eva = await login(client, "eva@example.nl")
    root = (await test_files.root(client, eva))["folder"]["id"]
    bron = await upload(client, eva, root, "bron.txt", b"de bron")
    sha = hashlib.sha256(b"de bron").hexdigest()

    async def copies(n: int, start: int) -> None:
        for i in range(start, start + n):
            r = await client.post(
                f"/api/v1/files/{bron['id']}/copy",
                json={"parent_id": root, "name": f"kopie-{i}.txt"},
                headers=eva,
            )
            assert r.status_code == 201, r.text

    async def stored() -> tuple[str, str, int]:
        async with maker(settings)() as db:
            row = (
                await db.execute(
                    sa.select(BlobText.body, BlobText.ocr, BlobText.truncated).where(
                        BlobText.blob_sha == sha
                    )
                )
            ).one()
            await db.execute(sa.text("INSERT INTO fts_node(fts_node) VALUES('integrity-check')"))
        return row[0], row[1], row[2]

    async def write(**kwargs) -> None:
        async with maker(settings)() as db:
            await analysis.write_text(db, sha, **kwargs)

    woorden = " ".join(f"woord{i:05d}" for i in range(1000))  # 10 tekens per woord
    await copies(4, 0)  # 5 nodes: 2000 tekens per node
    await write(body=woorden[:5000], ocr="scan " * 100)
    body, ocr, truncated = await stored()
    assert (len(body), truncated) == (2000, 1)
    assert len(ocr) == 100  # niets over: TEXT_MIN_SHARE
    await write(body=woorden[:1500])
    body, ocr, truncated = await stored()
    assert (len(body), truncated) == (1500, 0)
    assert len(ocr) == 100
    await copies(15, 4)  # 20 nodes: 500 per node
    await write(ocr="scan " * 600)
    body, ocr, truncated = await stored()
    assert (body, truncated) == (woorden[:500], 1)
    assert len(ocr) == 100
    await write(meta="titel")
    assert await stored() == (woorden[:500], ocr, 1)
    # Een lange ocr van toen er minder kopieën waren: komt daarna alleen meta mee, dan gaat ook
    # die ocr terug tot wat er nu naast de body over is (de nacontrole van golf 1)
    await write(body=woorden[:100], ocr="scan " * 80)  # 20 nodes: 500, de ocr krijgt 400
    body, ocr, truncated = await stored()
    assert (body, len(ocr), truncated) == (woorden[:100], 400, 0)
    await copies(20, 19)  # 40 nodes: 250 per node, naast de body van 100 nog 150
    await write(meta="titel")
    assert await stored() == (woorden[:100], ("scan " * 80)[:150], 0)
