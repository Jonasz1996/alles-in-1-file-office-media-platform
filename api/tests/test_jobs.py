"""De wachtrij voor achtergrondwerk: precies één keer oppakken, opnieuw proberen met
wachttijd, definitief mislukt na max_attempts, en wat er gebeurt bij een crash of stoppen."""

import asyncio
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import jobs
from app.db import get_engine
from app.models import Job


@pytest.fixture
def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


@pytest.fixture
def kind(monkeypatch):
    """Registreert een testsoort; geeft een functie die (naam, run, on_dead) vastlegt."""

    def add(name, run, on_dead=None, max_attempts=jobs.MAX_ATTEMPTS):
        monkeypatch.setitem(jobs._kinds, name, jobs.Kind(run, on_dead, max_attempts))
        return name

    return add


async def get(maker, job_id) -> Job:
    async with maker() as session:
        return await session.get(Job, job_id)


async def put(maker, type, payload=None, key=None):
    async with maker() as session:
        job_id = await jobs.enqueue(session, type, payload or {}, key=key)
        await session.commit()
        return None if job_id is None else await session.get(Job, job_id)


def aware(value):
    return value.replace(tzinfo=value.tzinfo or UTC)


@pytest.mark.anyio
async def test_dubbele_key_wordt_niet_nog_eens_klaargezet(settings, maker, kind):
    seen = []

    async def run(_settings, payload):
        seen.append(payload["n"])

    name = kind("test-dubbel", run)
    first = await put(maker, name, {"n": 1}, key="k")
    assert first is not None
    assert await put(maker, name, {"n": 2}, key="k") is None
    assert await put(maker, name, {"n": 3}) is not None  # zonder key nooit ontdubbeld
    assert await jobs.run_pending(settings) == 2
    assert sorted(seen) == [1, 3]
    # Klaar: dan mag dezelfde key weer
    assert await put(maker, name, {"n": 4}, key="k") is not None
    assert await jobs.run_pending(settings) == 1
    done = await get(maker, first.id)
    assert done.status == "done" and done.attempts == 1 and done.error is None


@pytest.mark.anyio
async def test_gelijktijdige_workers_pakken_elke_taak_precies_een_keer(settings, maker, kind):
    seen: list[int] = []

    async def run(_settings, payload):
        await asyncio.sleep(0.005)  # andere workers krijgen intussen de beurt
        seen.append(payload["n"])

    name = kind("test-race", run)
    async with maker() as session:
        for n in range(40):
            await jobs.enqueue(session, name, {"n": n})
        await session.commit()
    collisions = 0

    def count(_conn, cursor, statement, *_args):
        nonlocal collisions
        if statement.startswith("UPDATE job SET status") and cursor.rowcount == 0:
            collisions += 1  # een ander had deze net

    engine = get_engine(settings).sync_engine
    event.listen(engine, "after_cursor_execute", count)
    try:
        counts = await asyncio.gather(*(jobs.run_pending(settings) for _ in range(6)))
    finally:
        event.remove(engine, "after_cursor_execute", count)
    assert collisions > 0  # ze zaten elkaar echt in de weg
    assert sum(counts) == 40
    assert sorted(seen) == list(range(40))
    async with maker() as session:
        assert (await jobs.counts(session))["done"] == 40


@pytest.mark.anyio
async def test_opnieuw_met_wachttijd_daarna_failed(settings, maker, kind):
    dead = []

    async def run(_settings, _payload):
        raise RuntimeError("kapot")

    async def on_dead(_settings, payload, error):
        dead.append((payload, error))

    name = kind("test-fout", run, on_dead)
    job = await put(maker, name, {"x": 1})
    start = datetime.now(UTC)
    assert await jobs.run_pending(settings) == 1  # daarna wacht hij 30 s
    row = await get(maker, job.id)
    assert row.status == "queued" and row.attempts == 1
    assert "RuntimeError: kapot" in row.error
    wait = aware(row.run_after) - start
    assert timedelta(seconds=29) < wait < timedelta(seconds=35)

    assert await jobs.run_pending(settings, start + timedelta(seconds=31)) == 1
    row = await get(maker, job.id)
    assert row.attempts == 2
    assert timedelta(seconds=59) < aware(row.run_after) - start < timedelta(seconds=70)
    assert dead == []

    assert await jobs.run_pending(settings, start + timedelta(hours=1)) == 1
    row = await get(maker, job.id)
    assert row.status == "failed" and row.attempts == 3 and row.finished_at is not None
    assert dead == [({"x": 1}, "RuntimeError: kapot")]
    assert await jobs.run_pending(settings, start + timedelta(days=1)) == 0


@pytest.mark.anyio
async def test_eerst_mislukt_dan_gelukt(settings, maker, kind):
    calls = []

    async def run(_settings, _payload):
        calls.append(1)
        if len(calls) == 1:
            raise jobs.JobError("Even niet")

    name = kind("test-herstel", run)
    job = await put(maker, name)
    await jobs.run_pending(settings)
    assert (await get(maker, job.id)).error == "Even niet"  # JobError: alleen de melding
    await jobs.run_pending(settings, datetime.now(UTC) + timedelta(minutes=5))
    row = await get(maker, job.id)
    assert row.status == "done" and row.error is None and row.attempts == 2


@pytest.mark.anyio
async def test_te_lang_telt_als_mislukt(settings, maker, kind):
    settings.job_timeout = 0.2

    async def run(_settings, _payload):
        await asyncio.sleep(30)

    name = kind("test-traag", run, max_attempts=1)
    job = await put(maker, name)
    async with asyncio.timeout(5):
        await jobs.run_pending(settings)
    row = await get(maker, job.id)
    assert row.status == "failed"
    assert row.error == "Duurde langer dan 0.2 seconden"


@pytest.mark.anyio
async def test_onbekend_soort_meteen_failed(settings, maker):
    job = await put(maker, "bestaat-niet")
    await jobs.run_pending(settings)
    row = await get(maker, job.id)
    assert row.status == "failed" and "Onbekend soort taak" in row.error


@pytest.mark.anyio
async def test_na_een_crash_terug_in_de_rij(settings, maker, kind):
    dead = []

    async def on_dead(_settings, payload, error):
        dead.append(payload)

    async def run(_settings, _payload):
        pass

    name = kind("test-crash", run, on_dead)
    again, given_up = await put(maker, name, {"n": 1}), await put(maker, name, {"n": 2})
    async with maker() as session:
        # Alsof het proces stopte terwijl ze liepen; de tweede was al voor de 3e keer bezig
        await session.execute(
            update(Job).where(Job.id == again.id).values(status="running", attempts=1)
        )
        await session.execute(
            update(Job).where(Job.id == given_up.id).values(status="running", attempts=3)
        )
        await session.commit()
    assert await jobs.recover(settings) == 2
    assert (await get(maker, again.id)).status == "queued"
    row = await get(maker, given_up.id)
    assert row.status == "failed" and "stopte" in row.error
    assert dead == [{"n": 2}]
    assert await jobs.run_pending(settings) == 1
    assert (await get(maker, again.id)).status == "done"


@pytest.mark.anyio
async def test_oude_afgeronde_taken_weg(settings, maker, kind):
    async def run(_settings, _payload):
        pass

    name = kind("test-oud", run)
    old, recent = await put(maker, name), await put(maker, name)
    failed = await put(maker, "bestaat-niet")
    await jobs.run_pending(settings)
    async with maker() as session:
        long_ago = datetime.now(UTC) - timedelta(days=8)
        for job_id in (old.id, failed.id):
            await session.execute(update(Job).where(Job.id == job_id).values(finished_at=long_ago))
        await session.commit()
    assert await jobs.remove_old(settings) == 1
    async with maker() as session:
        left = set((await session.scalars(select(Job.id))).all())
    assert left == {recent.id, failed.id}  # mislukte blijven staan voor /beheer


@pytest.mark.anyio
async def test_workers_worden_gewekt_na_de_commit(settings, maker, kind, monkeypatch):
    """Zonder seintje zou het tot POLL duren; met de commit gaat het meteen."""
    monkeypatch.setattr(jobs, "POLL", 30.0)
    done = asyncio.Event()

    async def run(_settings, _payload):
        done.set()

    name = kind("test-wekken", run)
    runner = jobs.Runner(settings)
    await runner.start()
    try:
        await asyncio.sleep(0.05)  # de workers wachten nu
        async with maker() as session:
            await jobs.enqueue(session, name, {})
            await asyncio.sleep(0.2)
            assert not done.is_set()  # nog niet gecommit: niets te zien
            await session.commit()
        await asyncio.wait_for(done.wait(), 2)

        # Teruggedraaid: geen taak, en geen seintje dat blijft hangen
        done.clear()
        async with maker() as session:
            await jobs.enqueue(session, name, {})
            await session.rollback()
        await asyncio.sleep(0.2)
        assert not done.is_set()
    finally:
        await runner.stop()


@pytest.mark.anyio
async def test_stoppen_zet_lopende_taak_terug(settings, maker, kind):
    started = asyncio.Event()

    async def run(_settings, _payload):
        started.set()
        await asyncio.sleep(60)

    name = kind("test-stop", run)
    job = await put(maker, name)
    runner = jobs.Runner(settings)
    await runner.start()
    await asyncio.wait_for(started.wait(), 5)
    assert (await get(maker, job.id)).status == "running"
    await asyncio.wait_for(runner.stop(), 5)
    row = await get(maker, job.id)
    assert row.status == "queued" and row.attempts == 0  # telt niet als poging


def locked() -> OperationalError:
    """Wat _set geeft als een ander de database langer dan busy_timeout op slot houdt."""
    return OperationalError("UPDATE job", None, sqlite3.OperationalError("database is locked"))


async def requeue_locked(*_args, **_kwargs):
    raise locked()


@pytest.mark.anyio
async def test_stoppen_als_terugzetten_mislukt(settings, maker, kind, monkeypatch):
    """Lukt het terugzetten bij stoppen niet, dan stopt de Runner toch. De taak blijft op
    running, en recover() zet hem bij de volgende start terug, zoals na een crash. Eerst verving
    de fout de CancelledError, de worker draaide door, en stop wachtte voor altijd (het harnas
    van de e2e doodde de app na 30 s; fase-5/m0.md, punt 12)."""
    started = asyncio.Event()
    runs = []

    async def run(_settings, _payload):
        runs.append(1)
        started.set()
        if len(runs) == 1:
            await asyncio.sleep(60)

    name = kind("test-stop-slot", run)
    job = await put(maker, name)
    runner = jobs.Runner(settings)
    await runner.start()
    await asyncio.wait_for(started.wait(), 5)
    requeue = jobs._requeue
    monkeypatch.setattr(jobs, "_requeue", requeue_locked)
    await asyncio.wait_for(runner.stop(), 5)
    row = await get(maker, job.id)
    assert (row.status, row.attempts, row.error) == ("running", 1, None)
    monkeypatch.setattr(jobs, "_requeue", requeue)  # de volgende start: de database weer vrij
    assert await jobs.recover(settings) == 1
    assert (await get(maker, job.id)).status == "queued"
    assert await jobs.run_pending(settings) == 1
    row = await get(maker, job.id)
    assert (row.status, row.attempts) == ("done", 2)  # de gestopte poging telt, zoals na een crash


@pytest.mark.anyio
async def test_het_stopsein_blijft_als_terugzetten_mislukt(settings, maker, kind, monkeypatch):
    """execute eindigt bij stoppen altijd met CancelledError, ook als het terugzetten een fout
    gaf: anders zag de worker een gewone fout en ging hij door."""
    started = asyncio.Event()

    async def run(_settings, _payload):
        started.set()
        await asyncio.sleep(60)

    name = kind("test-stop-sein", run)
    job = await put(maker, name)
    claimed = await jobs.claim(settings)
    assert claimed is not None and claimed.id == job.id
    monkeypatch.setattr(jobs, "_requeue", requeue_locked)
    task = asyncio.create_task(jobs.execute(settings, claimed))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    row = await get(maker, job.id)
    assert (row.status, row.attempts, aware(row.started_at)) == (
        "running",
        1,
        claimed.started_at,
    )


@pytest.mark.anyio
async def test_stoppen_als_een_taak_het_sein_slikt(settings, maker, kind):
    """Een taak die de CancelledError slikt en gewoon eindigt: de worker pakt daarna niets meer
    op en stopt. asyncio geeft een geslikte cancel() niet opnieuw; zonder die controle wachtte
    stop voor altijd."""
    started = asyncio.Event()

    async def run(_settings, _payload):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    name = kind("test-stop-geslikt", run)
    job = await put(maker, name)
    runner = jobs.Runner(settings)
    await runner.start()
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wait_for(runner.stop(), 5)
    assert (await get(maker, job.id)).status == "done"  # de taak zelf zei klaar


@pytest.mark.anyio
async def test_stoppen_als_het_opruimen_van_de_taak_mislukt(settings, maker, kind):
    """Het opruimen in de taak zelf geeft bij stoppen een fout (zoals analysis._back_to_wait
    bij database is locked) die de CancelledError vervangt: toch stoppen, en terug in de rij
    zonder dat de poging telt. Geen mislukte poging met wachttijd."""
    started = asyncio.Event()

    async def run(_settings, _payload):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise locked() from None

    name = kind("test-stop-opruimen", run)
    job = await put(maker, name)
    runner = jobs.Runner(settings)
    await runner.start()
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wait_for(runner.stop(), 5)
    row = await get(maker, job.id)
    assert (row.status, row.attempts, row.error, row.started_at) == ("queued", 0, None, None)


@pytest.mark.anyio
async def test_stoppen_als_opruimwerk_het_sein_niet_doorgeeft(settings, monkeypatch):
    """Het opruimwerk van de Runner: een fout bij stoppen vervangt het stopsein. Eerst ving
    run_housekeeping die als gewone fout, en sliep de lus HOUSEKEEPING_TICK zonder cancel."""
    started = asyncio.Event()

    async def run(_settings):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise locked() from None

    monkeypatch.setattr(jobs, "_housekeeping", {"test": jobs.Housekeeping("test", run, every=3600)})
    runner = jobs.Runner(settings, housekeeping=True)
    await runner.start()
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wait_for(runner.stop(), 5)


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["klaar", "fout", "laatste_fout", "defer", "stoppen"])
async def test_de_uitkomst_landt_alleen_op_de_eigen_claim(settings, maker, kind, outcome):
    """Een taak die zichzelf terug in de rij zet (zoals office.convert bij wachten zonder
    poging), kan opnieuw zijn opgepakt voordat execute zijn uitkomst schrijft: een verzoek haalde
    hem naar voren, en een tweede worker pakte hem. Wat execute dan voor de eerste claim schrijft
    (done, failed, terug in de rij na een fout, Defer of stoppen), raakt die tweede claim niet,
    en on_dead komt niet. Eerst zette de done van de eerste claim de tweede op done; die stelde
    zelf ook uit, vond zijn rij niet meer, en de inhoud bleef pending zonder taak (o3.md
    afwijking 22)."""
    second: list[jobs.Claimed] = []
    dead = []

    async def run(_settings, _payload):
        if second:
            return  # de tweede claim: gewoon klaar
        async with maker() as session:  # terug in de rij, zonder poging (convert._postpone)
            await session.execute(
                update(Job)
                .where(Job.key == "eigen", Job.status == "running")
                .values(status="queued", started_at=None, attempts=Job.attempts - 1)
            )
            await session.commit()
        again = await jobs.claim(settings)  # een tweede worker, vóór de uitkomst van de eerste
        assert again is not None
        second.append(again)
        if outcome in ("fout", "laatste_fout"):
            raise RuntimeError("kapot")
        if outcome == "defer":
            raise jobs.Defer(60)
        if outcome == "stoppen":
            raise asyncio.CancelledError

    async def on_dead(_settings, payload, _error):
        dead.append(payload)

    tries = 1 if outcome == "laatste_fout" else jobs.MAX_ATTEMPTS
    name = kind("test-eigen-claim", run, on_dead, max_attempts=tries)
    job = await put(maker, name, key="eigen")
    first = await jobs.claim(settings)
    assert first is not None and first.id == job.id
    if outcome == "stoppen":
        with pytest.raises(asyncio.CancelledError):
            await jobs.execute(settings, first)
    else:
        await jobs.execute(settings, first)
    [again] = second
    assert again.id == job.id and again.started_at != first.started_at
    row = await get(maker, job.id)
    assert (row.status, row.attempts, row.error) == ("running", 1, None)
    assert aware(row.started_at) == again.started_at
    assert dead == []
    await jobs.execute(settings, again)
    row = await get(maker, job.id)
    assert (row.status, row.attempts, row.error) == ("done", 1, None)


@pytest.mark.anyio
async def test_kick_voor_de_commit_kan_geen_kwaad(settings, maker, kind, monkeypatch):
    """Te vroeg gewekt: de worker ziet niets, en vindt de taak bij het volgende rondje."""
    monkeypatch.setattr(jobs, "POLL", 0.2)
    done = asyncio.Event()

    async def run(_settings, _payload):
        done.set()

    name = kind("test-vroeg", run)
    runner = jobs.Runner(settings)
    await runner.start()
    try:
        async with maker() as session:
            await jobs.enqueue(session, name, {})
            jobs.kick()
            await asyncio.sleep(0.05)
            session.info.pop(jobs._KICK)  # alsof alleen de vroege kick er was
            await session.commit()
        await asyncio.wait_for(done.wait(), 3)
    finally:
        await runner.stop()


async def login_as(client, admin_headers, email):
    new = {"email": email, "display_name": "Ander", "password": "lang-genoeg-1"}
    await client.post("/api/v1/users", json=new, headers=admin_headers)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": new["password"]})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.anyio
async def test_beheer_ziet_mislukte_taken_en_start_ze_opnieuw(client, admin, settings, maker):
    headers, _ = admin
    a, b = await put(maker, "bestaat-niet", key="a"), await put(maker, "bestaat-niet", key="b")
    queued = await put(maker, "ook-niet-maar-wacht")
    async with maker() as session:
        await session.execute(
            update(Job)
            .where(Job.id == queued.id)
            .values(run_after=datetime(2100, 1, 1, tzinfo=UTC))
        )
        await session.commit()
    await jobs.run_pending(settings)

    r = await client.get("/api/v1/admin/jobs", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == {"queued": 1, "running": 0, "done": 0, "failed": 2}
    assert {j["id"] for j in body["failed"]} == {str(a.id), str(b.id)}
    assert all(j["error"] and j["attempts"] == 1 and j["finished_at"] for j in body["failed"])

    r = await client.post(f"/api/v1/admin/jobs/{a.id}/retry", headers=headers)
    assert r.status_code == 204
    row = await get(maker, a.id)
    assert row.status == "queued" and row.attempts == 0 and row.error is None
    r = await client.post(f"/api/v1/admin/jobs/{a.id}/retry", headers=headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_failed"
    r = await client.post(f"/api/v1/admin/jobs/{uuid.uuid4()}/retry", headers=headers)
    assert r.status_code == 404

    r = await client.post("/api/v1/admin/jobs/retry-failed", headers=headers)
    assert r.json() == {"count": 1}
    assert (await get(maker, b.id)).status == "queued"

    other = await login_as(client, headers, "gewoon@example.nl")
    assert (await client.get("/api/v1/admin/jobs", headers=other)).status_code == 403
    r = await client.post("/api/v1/admin/jobs/retry-failed", headers=other)
    assert r.status_code == 403
