"""De wachtrij van de analyse (bouwontwerp fase 6, Z2; Keuzes 19, 20, 49 en 50).

Versies (een hogere versie in de code zet de stap opnieuw klaar, ook bij het starten), again en
first, het herstel na een crash, de pogingen met wachttijd, geen nieuwe poging na too_slow, om
de beurt per eigenaar, en wijken voor omzettingen en een startende OnlyOffice. De stap hier is
een nep-stap op de rij index; de echte stap text staat in tests/extract/test_extract_subprocess.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, jobs
from app.analysis import Outcome, Step
from app.db import get_engine
from app.extract import step as extract_step
from app.models import Analysis, Blob, Job, User
from app.search import hooks

# zoek: ook in e2e/slim.sh op SQLite 3.46.1 (bouwontwerp §4.1)
pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

PDF = "application/pdf"
DONE = Outcome("done")


@pytest.fixture(autouse=True)
def _alleen_de_nepstap():
    analysis.reset()
    yield
    analysis.reset()
    hooks.reset()
    extract_step.register()  # zoals na de import van app.main


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def sha(n: int) -> str:
    return f"{n:064x}"


def aware(at: datetime) -> datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


class Nep:
    """Een stap die per batch werkt en bijhoudt wat hij kreeg."""

    def __init__(self, outcome: Outcome | None = DONE):
        self.outcome = outcome
        self.batches: list[list[str]] = []

    async def batch(self, settings, shas: list[str]) -> dict[str, Outcome]:
        self.batches.append(list(shas))
        return {} if self.outcome is None else {s: self.outcome for s in shas}

    async def run(self, settings, sha: str) -> Outcome:
        return (await self.batch(settings, [sha])).get(sha) or Outcome("failed")


def aanmelden(nep: Nep, size: int = 16, eligible=lambda sha, mime: mime == PDF) -> None:
    analysis.register_step(
        Step.TEXT, "index", eligible, nep.run, batch=nep.batch, size=lambda s: size
    )


async def users(settings, *names: str) -> list[uuid.UUID]:
    found = []
    async with maker(settings)() as db:
        for name in names:
            user = User(email=f"{name}@example.nl", display_name=name, password_hash="x")
            db.add(user)
            await db.flush()
            found.append(user.id)
        await db.commit()
    return found


async def queue(settings, shas, *, owner=None, mime=PDF, **kw) -> None:
    async with maker(settings)() as db:
        for s in shas:
            if await db.get(Blob, s) is None:
                db.add(Blob(sha256=s, size=1, mime=mime))
                await db.flush()
            await analysis.queue(db, s, [Step.TEXT], owner=owner, **kw)
        await db.commit()


async def rows(settings) -> dict[str, Analysis]:
    async with maker(settings)() as db:
        found = (await db.scalars(sa.select(Analysis).where(Analysis.step == "text"))).all()
    return {row.blob_sha: row for row in found}


async def zet(settings, shas, **values) -> None:
    async with maker(settings)() as db:
        await db.execute(
            sa.update(Analysis)
            .where(Analysis.blob_sha.in_(list(shas)), Analysis.step == "text")
            .values(**values)
        )
        await db.commit()


async def pomp(settings, lane: str = "index") -> int:
    """Eén ronde zoals de jobs-runner hem draait: de job die klaarstond, loopt nu."""
    async with maker(settings)() as db:
        await db.execute(
            sa.update(Job)
            .where(Job.type == analysis.pump_type(lane), Job.status == "queued")
            .values(status="running")
        )
        await db.commit()
    try:
        return await analysis.pump(settings, lane)
    finally:
        async with maker(settings)() as db:
            await db.execute(
                sa.update(Job)
                .where(Job.type == analysis.pump_type(lane), Job.status == "running")
                .values(status="done")
            )
            await db.commit()


async def pumps(settings) -> list[tuple[str, datetime]]:
    async with maker(settings)() as db:
        found = (
            await db.execute(
                sa.select(Job.type, Job.run_after).where(
                    Job.type == "analysis.index", Job.status == "queued"
                )
            )
        ).all()
    return [(t, aware(at)) for t, at in found]


# --- versies, again, first en het herstel ------------------------------------------------------


async def test_een_hogere_versie_zet_de_stap_opnieuw_klaar(settings, monkeypatch):
    nep = Nep()
    aanmelden(nep)
    await queue(settings, [sha(1), sha(2)])
    await zet(settings, [sha(1), sha(2)], state="done", tries=2, code="too_slow")
    await queue(settings, [sha(1)])
    assert (await rows(settings))[sha(1)].state == "done"  # zelfde versie: blijft klaar

    monkeypatch.setitem(analysis.VERSIONS, Step.TEXT, 2)
    await queue(settings, [sha(1)])
    row = (await rows(settings))[sha(1)]
    assert (row.state, row.version, row.tries, row.code) == ("wait", 2, 0, None)

    # Bij het starten: elke rij met een lagere versie terug op wait, en de pomp aan
    async with maker(settings)() as db:
        await db.execute(sa.delete(Job))
        await db.commit()
    await hooks.backfill(settings)  # register_step meldde analysis.recover aan
    row = (await rows(settings))[sha(2)]
    assert (row.state, row.version, row.tries, row.code) == ("wait", 2, 0, None)
    assert [t for t, _ in await pumps(settings)] == ["analysis.index"]


async def test_again_first_en_de_eigenaar(settings):
    eva, bram = await users(settings, "eva", "bram")
    aanmelden(Nep())
    await queue(settings, [sha(1)], owner=eva)
    await queue(settings, [sha(2)], owner=None)
    before = (await rows(settings))[sha(1)].queued_at

    # again op een open rij verandert niets; first zet hem vooraan
    await queue(settings, [sha(1)], owner=bram, again=True)
    assert (await rows(settings))[sha(1)].queued_at == before
    await queue(settings, [sha(1)], owner=bram, first=True)
    assert aware((await rows(settings))[sha(1)].queued_at) == analysis.FRONT

    # again op een klare rij: weer wait; de eerste eigenaar blijft, een lege krijgt er een
    await zet(settings, [sha(1), sha(2)], state="done")
    await queue(settings, [sha(1), sha(2)], owner=bram, again=True)
    found = await rows(settings)
    assert (found[sha(1)].state, found[sha(1)].owner_id) == ("wait", eva)
    assert (found[sha(2)].state, found[sha(2)].owner_id) == ("wait", bram)


async def test_past_niet_is_skip_zonder_pomp(settings):
    aanmelden(Nep())
    await queue(settings, [sha(1)], mime="image/png")
    assert (await rows(settings))[sha(1)].state == "skip"
    assert await pumps(settings) == []
    await queue(settings, [sha(9)], mime=None)  # nog geen soort: past ook niet
    assert (await rows(settings))[sha(9)].state == "skip"


async def test_herstel_zet_busy_terug_op_wait(settings):
    aanmelden(Nep())
    await queue(settings, [sha(1), sha(2)])
    await zet(settings, [sha(1)], state="busy")
    await zet(settings, [sha(2)], state="done")
    async with maker(settings)() as db:
        await db.execute(sa.delete(Job))
        await db.commit()
    await analysis.recover(settings)
    found = await rows(settings)
    assert (found[sha(1)].state, found[sha(1)].tries) == ("wait", 0)
    assert found[sha(2)].state == "done"
    assert len(await pumps(settings)) == 1


# --- pogingen ---------------------------------------------------------------------------------


async def test_mislukt_wacht_steeds_langer_en_stopt_na_drie_pogingen(settings):
    nep = Nep(Outcome("failed"))
    aanmelden(nep)
    await queue(settings, [sha(1)])
    for attempt, wait in ((1, 1), (2, 2)):
        start = datetime.now(UTC)
        assert await pomp(settings) == 1
        row = (await rows(settings))[sha(1)]
        assert (row.state, row.tries) == ("wait", attempt)
        due = aware(row.queued_at)
        assert start + timedelta(minutes=wait) <= due <= datetime.now(UTC) + timedelta(minutes=wait)
        # De volgende job van de pomp staat op die tijd, niet eerder
        assert [at for _, at in await pumps(settings)] == [due]
        assert await pomp(settings) == 0  # nog niet aan de beurt
        await zet(settings, [sha(1)], queued_at=start - timedelta(seconds=1))
    assert await pomp(settings) == 1
    row = (await rows(settings))[sha(1)]
    assert (row.state, row.tries, row.code) == ("failed", 3, None)
    assert len(nep.batches) == 3
    assert await pumps(settings) == []  # geen werk meer: geen job


async def test_nieuw_werk_wacht_niet_op_een_latere_poging(settings):
    aanmelden(Nep(Outcome("failed")))
    await queue(settings, [sha(1)])
    await pomp(settings)
    ((_, later),) = await pumps(settings)
    assert later > datetime.now(UTC) + timedelta(seconds=50)
    start = datetime.now(UTC)
    await queue(settings, [sha(2)])
    ((_, at),) = await pumps(settings)  # dezelfde job, naar voren
    assert start <= at <= datetime.now(UTC)


@pytest.mark.parametrize("code", ["too_slow", "zip_limit", "xml_refused"])
async def test_te_traag_of_geweigerd_zonder_nieuwe_poging(settings, code):
    aanmelden(Nep(Outcome("failed", code, retry=False)))
    await queue(settings, [sha(1)])
    await pomp(settings)
    row = (await rows(settings))[sha(1)]
    assert (row.state, row.tries, row.code) == ("failed", 1, code)


async def test_zonder_uitkomst_terug_op_wait_zonder_poging(settings):
    aanmelden(Nep(None))
    await queue(settings, [sha(1)])
    await pomp(settings)
    row = (await rows(settings))[sha(1)]
    assert (row.state, row.tries) == ("wait", 0)
    assert len(await pumps(settings)) == 1


async def test_een_kapotte_batch_telt_als_een_poging_voor_elk(settings):
    async def kapot(settings, shas):
        raise RuntimeError("stuk")

    analysis.register_step(Step.TEXT, "index", lambda s, m: True, Nep().run, batch=kapot)
    await queue(settings, [sha(1)])
    await pomp(settings)
    row = (await rows(settings))[sha(1)]
    assert (row.state, row.tries) == ("wait", 1)


async def test_defer_van_de_stap_zet_de_batch_terug(settings):
    async def druk(settings, shas):
        raise jobs.Defer(30)

    analysis.register_step(Step.TEXT, "index", lambda s, m: True, Nep().run, batch=druk)
    await queue(settings, [sha(1)])
    with pytest.raises(jobs.Defer):
        await pomp(settings)
    row = (await rows(settings))[sha(1)]
    assert (row.state, row.tries) == ("wait", 0)


async def test_een_nieuwe_versie_tijdens_de_batch_blijft_staan(settings, monkeypatch):
    async def intussen(settings_, shas):
        monkeypatch.setitem(analysis.VERSIONS, Step.TEXT, 2)
        await queue(settings_, shas)
        return {s: Outcome("done") for s in shas}

    analysis.register_step(Step.TEXT, "index", lambda s, m: True, Nep().run, batch=intussen)
    await queue(settings, [sha(1)])
    await pomp(settings)
    row = (await rows(settings))[sha(1)]
    assert (row.state, row.version) == ("wait", 2)


# --- om de beurt per eigenaar (Keuze 20) -------------------------------------------------------


async def test_bram_wacht_hooguit_een_batch_op_eva(settings):
    eva, bram = await users(settings, "eva", "bram")
    nep = Nep()
    aanmelden(nep, size=16)
    evas = [sha(100 + i) for i in range(40)]
    await queue(settings, evas, owner=eva)
    start = datetime.now(UTC) - timedelta(hours=1)
    for i, s in enumerate(evas):
        await zet(settings, [s], queued_at=start + timedelta(seconds=i))
    await pomp(settings)
    # Alleen Eva: haar nieuwste eerst
    assert nep.batches[0] == list(reversed(evas))[:16]

    await queue(settings, [sha(1)], owner=bram)
    await pomp(settings)
    # Bram was nog niet aan de beurt: hij eerst, dan Eva verder
    assert nep.batches[1] == [sha(1), *list(reversed(evas))[16:31]]


async def test_hooguit_vier_per_eigenaar_per_ronde(settings):
    eva, bram, ans = await users(settings, "eva", "bram", "ans")
    nep = Nep()
    aanmelden(nep, size=10)
    for n, owner in enumerate((eva, bram, ans)):
        await queue(settings, [sha(100 * (n + 1) + i) for i in range(10)], owner=owner)
    await pomp(settings)
    (batch,) = nep.batches
    found = await rows(settings)
    per = {o: sum(found[s].owner_id == o for s in batch) for o in (eva, bram, ans)}
    assert sorted(per.values()) == [2, 4, 4]


async def test_vooraan_gaat_voor_alles(settings):
    eva, bram = await users(settings, "eva", "bram")
    nep = Nep()
    aanmelden(nep, size=2)
    await queue(settings, [sha(100 + i) for i in range(5)], owner=eva)
    await queue(settings, [sha(1)], owner=bram, first=True)
    await pomp(settings)
    assert nep.batches[0][0] == sha(1)


# --- wijken (Keuze 50) -------------------------------------------------------------------------


async def test_wijkt_voor_omzettingen_en_een_startende_onlyoffice(settings, monkeypatch):
    from app.media import admin as media_admin
    from app.media import admission

    nep = Nep()
    aanmelden(nep)
    await queue(settings, [sha(1)])
    aan = settings.model_copy(update={"media_enabled": True})

    monkeypatch.setattr(media_admin, "transcodes_running", lambda: aan.index_defer_transcodes)
    with pytest.raises(jobs.Defer):
        await pomp(aan)
    monkeypatch.setattr(media_admin, "transcodes_running", lambda: aan.index_defer_transcodes - 1)
    monkeypatch.setattr(admission, "_office_starting", lambda: True)
    with pytest.raises(jobs.Defer):
        await pomp(aan)
    assert nep.batches == []
    assert (await rows(settings))[sha(1)].state == "wait"

    monkeypatch.setattr(admission, "_office_starting", lambda: False)
    assert await pomp(aan) == 1
    assert (await rows(settings))[sha(1)].state == "done"


async def test_wijkt_ook_tijdens_de_belastingtest_van_media(settings, monkeypatch):
    """De belastingtest van media (fase 5) telt niet als omzetting van een kijker
    (transcodes_running), maar zet zolang hij loopt media.analysis.busy_live op minstens 2. Ook de
    index en OCR wijken dan; anders meet zoek_samen (§4.5, stap 2 tegen stap 3) Defer niet.
    Met index_defer_transcodes hoog (Defer uit) leest de index gewoon door."""
    from app.media import admin as media_admin
    from app.media import analysis as media_analysis

    nep = Nep()
    aanmelden(nep)
    await queue(settings, [sha(1)])
    aan = settings.model_copy(update={"media_enabled": True})
    monkeypatch.setattr(media_admin, "transcodes_running", lambda: 0)
    monkeypatch.setattr(media_analysis, "busy_live", lambda: 2)

    with pytest.raises(jobs.Defer):
        await pomp(aan)
    assert nep.batches == []
    assert (await rows(settings))[sha(1)].state == "wait"

    uit = aan.model_copy(update={"index_defer_transcodes": 1000})
    assert await pomp(uit) == 1
    assert (await rows(settings))[sha(1)].state == "done"


def test_de_pomp_is_een_job_in_de_rij_index():
    aanmelden(Nep())
    assert jobs.lane_of(analysis.pump_type("index")) == "index"
    with pytest.raises(ValueError):
        analysis.register_step(Step.HASH, "nergens", lambda s, m: True, Nep().run)
