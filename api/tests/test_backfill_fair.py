"""De backfill om de beurt per eigenaar (bouwontwerp fase 6, Z2; Keuze 20).

Eva heeft 100 trage PDF's in de rij (de backfill zette ze klaar); Bram uploadt één nieuw
bestand terwijl er een batch van Eva loopt. Zijn upload wacht hooguit die ene batch: de
volgende batch neemt hem mee, want wie het langst niet aan de beurt was, gaat voor.

De stap is hier de echte stap text met een batch die alleen telt en wacht (een trage PDF kost
de echte lezer tot 30 s; hier 0,1 s per batch): het gaat om de volgorde van de pomp, niet om
het lezen. De backfill zelf (analysis.backfill_job) is echt: de eigenaar van de nieuwste
levende node, en binnen een eigenaar de nieuwste eerst.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, jobs
from app.analysis import Outcome, Step
from app.db import get_engine
from app.extract import step as extract_step
from app.models import Analysis, Blob, Job, Node, User, now
from app.search import hooks
from tests import factories

pytestmark = pytest.mark.anyio

BATCH = 16
SLOW_S = 0.1


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


class Trage:
    """De batch van de stap: onthoudt elke batch, wacht SLOW_S, en roept tijdens de eerste
    batch during() aan (de upload van Bram terwijl Eva's batch loopt)."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.during = None

    async def __call__(self, settings, shas: list[str]) -> dict[str, Outcome]:
        self.batches.append(list(shas))
        if len(self.batches) == 1 and self.during is not None:
            await self.during()
        await asyncio.sleep(SLOW_S)
        return {sha: Outcome("done") for sha in shas}


@pytest.fixture
def trage():
    analysis.reset()
    hooks.reset()
    fake = Trage()

    async def one(settings, sha):
        return (await fake(settings, [sha]))[sha]

    analysis.register_step(
        Step.TEXT, "index", extract_step.eligible, one, batch=fake, size=lambda s: BATCH
    )
    yield fake
    analysis.reset()
    hooks.reset()
    extract_step.register()


async def users(settings, *names: str) -> list[User]:
    async with maker(settings)() as db:
        found = [await factories.create_user(db, f"{n.lower()}@example.nl") for n in names]
        await db.commit()
    return found


async def documents(settings, owner: User, count: int, label: str, *, start=None) -> list[str]:
    """count PDF's van owner: blob en node, zonder rij in analysis (zoals vóór Z2)."""
    shas = []
    at = start or now() - timedelta(days=30)
    async with maker(settings)() as db:
        for i in range(count):
            sha = hashlib.sha256(f"{label}-{i}".encode()).hexdigest()
            db.add(Blob(sha256=sha, size=1000, mime="application/pdf", ref_count=1))
            db.add(
                Node(
                    owner_id=owner.id,
                    parent_id=owner.root_id,
                    name=f"{label}-{i:03d}.pdf",
                    kind="file",
                    mime="application/pdf",
                    size=1000,
                    blob_id=sha,
                    created_at=at + timedelta(minutes=i),
                )
            )
            shas.append(sha)
        await db.commit()
    return shas


async def backfill(settings) -> None:
    """De hele keten van de backfill: elk stuk, tot er geen volgende meer klaarstaat."""
    payload: dict = {"after": ""}
    while True:
        await analysis.backfill_job(settings, payload)
        async with maker(settings)() as db:
            job = (
                await db.execute(
                    sa.select(Job.id, Job.payload).where(
                        Job.type == analysis.BACKFILL, Job.status == "queued"
                    )
                )
            ).first()
            if job is None:
                return
            await db.execute(sa.delete(Job).where(Job.id == job.id))
            await db.commit()
        payload = job.payload


async def test_de_backfill_kiest_de_eigenaar_van_de_nieuwste_levende_node(settings, trage):
    eva, bram = await users(settings, "Eva", "Bram")
    [gedeeld] = await documents(settings, eva, 1, "gedeeld")
    [weg] = await documents(settings, bram, 1, "weg")
    async with maker(settings)() as db:
        root = bram.root_id
        # Bram heeft een nieuwere kopie van Eva's document; Eva een nieuwere van "weg", maar
        # die van Eva ligt in de prullenbak (dead via de trigger van 0010)
        db.add(Node(owner_id=bram.id, parent_id=root, name="kopie.pdf", kind="file",
                    blob_id=gedeeld, created_at=now()))  # fmt: skip
        db.add(Node(owner_id=eva.id, parent_id=eva.root_id, name="weg.pdf", kind="file",
                    blob_id=weg, created_at=now(), deleted_at=now()))  # fmt: skip
        # Een blob zonder node (wordt opgeruimd): geen rij
        db.add(Blob(sha256="f" * 64, size=1, mime="text/plain", ref_count=0))
        await db.commit()
    await backfill(settings)
    async with maker(settings)() as db:
        rows = {r.blob_sha: r for r in (await db.scalars(sa.select(Analysis))).all()}
    assert rows[gedeeld].owner_id == bram.id
    assert rows[weg].owner_id == bram.id
    assert "f" * 64 not in rows
    assert {r.state for r in rows.values()} == {"wait"}
    # Nog een keer: niets nieuws (elke blob heeft zijn rij)
    await backfill(settings)
    async with maker(settings)() as db:
        assert await db.scalar(sa.select(sa.func.count()).select_from(Analysis)) == 2


async def test_de_backfill_in_stukken_van_500(settings, trage, monkeypatch):
    monkeypatch.setattr(analysis, "BACKFILL_CHUNK", 7)
    [eva] = await users(settings, "Eva")
    shas = await documents(settings, eva, 30, "stuk")
    stukken = 0
    payload: dict = {"after": ""}
    while True:
        await analysis.backfill_job(settings, payload)
        stukken += 1
        async with maker(settings)() as db:
            job = (
                await db.execute(
                    sa.select(Job.id, Job.payload, Job.key).where(
                        Job.type == analysis.BACKFILL, Job.status == "queued"
                    )
                )
            ).first()
            count = await db.scalar(sa.select(sa.func.count()).select_from(Analysis))
            if job is None:
                break
            assert job.key == analysis.BACKFILL
            assert count == min(30, 7 * stukken)  # elk stuk zijn eigen transactie
            await db.execute(sa.delete(Job).where(Job.id == job.id))
            await db.commit()
        payload = job.payload
    assert stukken == 5  # 7, 7, 7, 7, 2 en dan niets meer
    assert count == len(shas) == 30
    # De pomp staat klaar in de rij index
    async with maker(settings)() as db:
        assert await db.scalar(
            sa.select(sa.func.count()).select_from(Job).where(Job.type == "analysis.index")
        )


async def test_start_backfill_begint_hooguit_een_keten(settings, trage):
    await analysis.start_backfill(settings)
    await analysis.start_backfill(settings)  # een herstart terwijl er een klaarstaat
    async with maker(settings)() as db:
        found = (
            await db.scalars(sa.select(Job.payload).where(Job.type == analysis.BACKFILL))
        ).all()
    assert found == [{"after": ""}]


async def test_bram_wacht_hooguit_een_batch_op_de_100_pdfs_van_eva(settings, trage):
    eva, bram = await users(settings, "Eva", "Bram")
    eva_docs = await documents(settings, eva, 100, "eva")
    await backfill(settings)
    nieuw: list[str] = []

    async def upload() -> None:
        [sha] = await documents(settings, bram, 1, "bram", start=now())
        async with maker(settings)() as db:
            await analysis.queue(db, sha, [Step.TEXT], owner=bram.id)
            await db.commit()
        nieuw.append(sha)

    trage.during = upload
    pumped = 0
    while await analysis.pump(settings, "index"):
        pumped += 1
    assert pumped == 7  # 101 in batches van 16
    first = trage.batches[0]
    assert set(first) <= set(eva_docs)
    # Binnen Eva de nieuwste eerst (queued_at uit created_at van de node)
    assert first == sorted(first, key=lambda sha: eva_docs.index(sha), reverse=True)
    [sha] = nieuw
    assert sha in trage.batches[1], "Bram wacht langer dan één batch"
    # Wie het langst niet aan de beurt was, gaat voor: Bram vooraan in die batch
    assert trage.batches[1][0] == sha


async def test_twee_eigenaren_om_de_beurt(settings, trage):
    """Eva 100 en Bram 10 tegelijk in de rij: per ronde hooguit 4 per eigenaar, dus Bram is
    na twee batches klaar, niet na zeven."""
    eva, bram = await users(settings, "Eva", "Bram")
    await documents(settings, eva, 100, "eva")
    bram_docs = await documents(settings, bram, 10, "bram")
    await backfill(settings)
    while await analysis.pump(settings, "index"):
        pass
    seen = [sha for batch in trage.batches for sha in batch]
    last = max(seen.index(sha) for sha in bram_docs)
    assert last < 2 * BATCH
    assert sum(sha in bram_docs for sha in trage.batches[0]) == BATCH // 2


async def test_met_de_rij_index_en_echte_jobs(settings, trage):
    """De keten zoals hij draait: de backfill en de pomp als jobs in de rij index (één
    worker). Bram's upload tijdens de eerste batch is klaar binnen twee batches."""
    eva, bram = await users(settings, "Eva", "Bram")
    await documents(settings, eva, 100, "eva")
    done_at: dict[str, float] = {}

    async def upload() -> None:
        [sha] = await documents(settings, bram, 1, "bram", start=now())
        async with maker(settings)() as db:
            await analysis.queue(db, sha, [Step.TEXT], owner=bram.id)
            await db.commit()
        done_at["upload"] = time.monotonic()
        done_at["sha"] = sha  # type: ignore[assignment]

    trage.during = upload
    await analysis.start_backfill(settings)
    runner = jobs.Runner(settings, "index")
    await runner.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            async with maker(settings)() as db:
                states = (await db.scalars(sa.select(Analysis.state))).all()
            if len(states) == 101 and set(states) == {"done"}:
                break
            await asyncio.sleep(0.05)
    finally:
        await runner.stop()
    assert len(states) == 101 and set(states) == {"done"}
    sha = done_at["sha"]
    index = next(i for i, batch in enumerate(trage.batches) if sha in batch)
    assert index == 1, f"Bram's upload kwam in batch {index + 1}"


async def test_indexing_voor_het_antwoord_van_search(settings, trage, monkeypatch):
    """indexing (voor /search, §3.4): {"done": deel} zolang er minstens 50 open staan; skip telt
    niet mee; hooguit eens per 5 s geteld."""
    [eva] = await users(settings, "Eva")
    docs = await documents(settings, eva, 100, "eva")
    async with maker(settings)() as db:
        assert await analysis.indexing(db) is None  # nog niets in de rij
    await backfill(settings)
    async with maker(settings)() as db:
        assert await analysis.indexing(db) is None  # de vorige telling geldt nog 5 s
    monkeypatch.setattr(analysis, "INDEXING_TTL", 0.0)
    async with maker(settings)() as db:
        assert (await analysis.indexing(db)).done == 0.0
        # 40 klaar, 10 overgeslagen (tellen niet mee): 40 van de 90
        await db.execute(
            sa.update(Analysis).where(Analysis.blob_sha.in_(docs[:40])).values(state="done")
        )
        await db.execute(
            sa.update(Analysis).where(Analysis.blob_sha.in_(docs[40:50])).values(state="skip")
        )
        await db.commit()
        assert (await analysis.indexing(db)).done == round(40 / 90, 4)
        # Minder dan 50 open: geen melding meer
        await db.execute(
            sa.update(Analysis).where(Analysis.blob_sha.in_(docs[50:51])).values(state="done")
        )
        await db.commit()
        assert await analysis.indexing(db) is None
