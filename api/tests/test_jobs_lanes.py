"""De rijen van de achtergrondtaken (fase 5, Keuze 14 en §3.5): een Runner per rij, het opruimwerk
één keer, claim per rij, Defer en de tijdgrens per soort. De soorten van fase 3 en 4 blijven in
de rij default, met settings.job_timeout."""

import asyncio
import inspect
import time
from datetime import UTC, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.main  # noqa: F401 - registreert alle soorten van de app
import app.media.analysis  # noqa: F401 - media.analyse
from app import jobs
from app.db import get_engine
from app.models import Job


@pytest.fixture
def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


@pytest.fixture
def kind(monkeypatch):
    """Een testsoort in een rij; weg na de test."""

    def add(name, run, *, lane="default", timeout=None, max_attempts=jobs.MAX_ATTEMPTS):
        monkeypatch.setitem(jobs._kinds, name, jobs.Kind(run, None, max_attempts, lane, timeout))
        return name

    return add


async def put(maker, type, payload=None, n: int = 1) -> list:
    async with maker() as session:
        ids = [await jobs.enqueue(session, type, dict(payload or {}, n=i)) for i in range(n)]
        await session.commit()
    return ids


async def get(maker, job_id) -> Job:
    async with maker() as session:
        return await session.get(Job, job_id)


async def until(predicate, seconds: float = 5.0) -> float:
    """Wacht (begrensd) tot predicate() waar is (gewoon of async); geeft de verstreken tijd."""
    start = time.monotonic()

    async def check() -> bool:
        found = predicate()
        return bool(await found) if inspect.isawaitable(found) else bool(found)

    while not await check():
        assert time.monotonic() - start < seconds, "niet op tijd"
        await asyncio.sleep(0.01)
    return time.monotonic() - start


@pytest.fixture
async def runners(settings):
    """Start een Runner per rij (met media aan: anders alleen default); stopt ze na de test."""
    started: list[jobs.Runner] = []

    async def start(s=None):
        s = (s or settings).model_copy(update={"media_enabled": True})
        started.extend(await jobs.start_runners(s))
        return started

    yield start
    await jobs.stop_runners(started)


@pytest.mark.anyio
async def test_een_lange_mediataak_houdt_een_preview_niet_op(settings, maker, kind, runners):
    release = asyncio.Event()
    running = []
    done = []

    async def long(_s, payload):
        running.append(payload["n"])
        await release.wait()

    async def short(_s, payload):
        done.append(payload["n"])

    lang = kind("test.media-lang", long, lane="media")
    kort = kind("test.preview-kort", short)
    s = settings.model_copy(update={"job_workers": 1, "media_job_workers": 1})
    try:
        await runners(s)
        await put(maker, lang, n=3)
        await until(lambda: running)
        await put(maker, kort)
        took = await until(lambda: done, 2)
        assert took < 1.0
        # De media-rij heeft één worker: de andere twee lange wachten nog
        assert running == [0]
    finally:
        release.set()


@pytest.mark.anyio
async def test_honderd_analyses_houden_een_scan_niet_op(settings, maker, kind, runners):
    analysed = []
    scanned = []

    async def analyse(_s, payload):
        await asyncio.sleep(0.05)
        analysed.append(payload["n"])

    async def scan(_s, payload):
        scanned.append(time.monotonic())

    analyse_type = kind("test.analyse", analyse, lane="media")
    scan_type = kind("test.scan", scan, lane="media_scan")
    await runners()
    await put(maker, analyse_type, n=100)
    queued_at = time.monotonic()
    await put(maker, scan_type)
    await until(lambda: scanned, 2)
    # Binnen één tik: de scan wacht niet op de rij media (100 × 0,05 s = 5 s)
    assert scanned[0] - queued_at < 1.0
    assert len(analysed) < 100


@pytest.mark.anyio
async def test_opruimwerk_een_keer_per_periode_met_alle_runners(settings, runners, monkeypatch):
    calls = []

    async def count(_s):
        calls.append(time.monotonic())

    monkeypatch.setattr(jobs, "HOUSEKEEPING_TICK", 0.02)
    monkeypatch.setattr(
        jobs, "_housekeeping", {"test.tel": jobs.Housekeeping("test.tel", count, 3600)}
    )
    started = await runners()
    assert [r.lane for r in started] == list(jobs.LANES)
    assert [r.housekeeping for r in started] == [True, False, False, False, False]
    await asyncio.sleep(0.3)
    assert len(calls) == 1  # niet vijf keer (één per Runner)


@pytest.mark.anyio
async def test_zonder_media_alleen_de_rij_default(settings, maker, kind):
    """Zonder PLATFORM_MEDIA_ENABLED draait alleen default, zoals in 0.6.0; een taak van media
    blijft dan staan (default pakt hem niet)."""
    ran = []

    async def note(_s, payload):
        ran.append(payload["n"])

    name = kind("test.media-uit", note, lane="media")
    assert not settings.media_enabled
    assert jobs.active_lanes(settings) == ("default",)
    started = await jobs.start_runners(settings)
    try:
        assert [r.lane for r in started] == ["default"]
        [job_id] = await put(maker, name)
        jobs.kick()
        await asyncio.sleep(0.3)
        assert ran == []
        assert (await get(maker, job_id)).status == "queued"
    finally:
        await jobs.stop_runners(started)


@pytest.mark.anyio
async def test_workers_per_rij(settings):
    s = settings.model_copy(update={"job_workers": 3, "media_job_workers": 2})
    assert [jobs.lane_workers(s, lane) for lane in jobs.LANES] == [3, 1, 2, 1, 1]


@pytest.mark.anyio
async def test_claim_alleen_uit_de_eigen_rij(settings, maker, kind):
    async def nothing(_s, _p):
        return None

    media = kind("test.media", nothing, lane="media")
    plain = kind("test.gewoon", nothing)
    [media_id] = await put(maker, media)
    [plain_id] = await put(maker, plain)
    [unknown_id] = await put(maker, "test.bestaat-niet")

    assert await jobs.claim(settings, lane="media_net") is None
    assert await jobs.claim(settings, lane="import") is None
    got = await jobs.claim(settings, lane="media")
    assert got is not None and got.id == media_id
    assert await jobs.claim(settings, lane="media") is None
    # default pakt alles wat niet in een andere rij staat, ook een onbekend soort
    first = await jobs.claim(settings, lane="default")
    second = await jobs.claim(settings, lane="default")
    assert {first.id, second.id} == {plain_id, unknown_id}
    assert await jobs.claim(settings, lane="default") is None
    unknown = first if first.id == unknown_id else second
    await jobs.execute(settings, unknown)
    failed = await get(maker, unknown_id)
    assert failed.status == "failed" and "Onbekend soort taak" in failed.error


@pytest.mark.anyio
async def test_defer_telt_niet(settings, maker, kind):
    calls = []

    async def later(_s, payload):
        calls.append(payload["n"])
        if len(calls) <= 5:
            raise jobs.Defer(60)

    name = kind("test.later", later, max_attempts=1)
    [job_id] = await put(maker, name)
    before = jobs.now()
    assert await jobs.run_pending(settings) == 1
    job = await get(maker, job_id)
    assert job.status == "queued" and job.attempts == 0 and job.error is None
    run_after = job.run_after.replace(tzinfo=job.run_after.tzinfo or UTC)
    assert before + timedelta(seconds=59) <= run_after <= jobs.now() + timedelta(seconds=61)
    # Nu staat hij nog niet klaar
    assert await jobs.run_pending(settings) == 0
    # Vier keer later nog eens uitgesteld: met max_attempts 1 faalt hij toch niet
    at = jobs.now()
    for _ in range(4):
        at += timedelta(seconds=61)
        assert await jobs.run_one(settings, at=at)
        job = await get(maker, job_id)
        assert job.status == "queued" and job.attempts == 0
    assert await jobs.run_one(settings, at=at + timedelta(seconds=61))
    job = await get(maker, job_id)
    assert job.status == "done" and job.attempts == 1
    assert len(calls) == 6


@pytest.mark.anyio
async def test_defer_in_de_runner(settings, maker, kind, runners):
    calls = []

    async def once(_s, payload):
        calls.append(payload["n"])
        if len(calls) == 1:
            raise jobs.Defer(0.05)

    name = kind("test.even-wachten", once, lane="media_net")
    [job_id] = await put(maker, name)
    await runners()
    # Het seintje na de commit wekt de Runner; de tweede poging komt na POLL (5 s) of het
    # volgende seintje
    await until(lambda: calls)
    await asyncio.sleep(0.1)
    jobs.kick()

    async def finished():
        return (await get(maker, job_id)).status == "done"

    await until(finished)
    assert (await get(maker, job_id)).attempts == 1


@pytest.mark.anyio
async def test_tijdgrens_per_soort(settings, maker, kind):
    async def slow(_s, _p):
        await asyncio.sleep(0.5)

    kort = kind("test.kort", slow, timeout=0.05, max_attempts=1)
    ruim = kind("test.ruim", slow, timeout=5)
    [kort_id] = await put(maker, kort)
    [ruim_id] = await put(maker, ruim)
    s = settings.model_copy(update={"job_timeout": 0.05})
    assert await jobs.run_pending(s) == 2
    failed = await get(maker, kort_id)
    assert failed.status == "failed" and failed.error == "Duurde langer dan 0.05 seconden"
    # timeout van de soort gaat voor settings.job_timeout
    assert (await get(maker, ruim_id)).status == "done"


@pytest.mark.anyio
async def test_zonder_tijdgrens_van_de_soort_geldt_job_timeout(settings, maker, kind):
    async def slow(_s, _p):
        await asyncio.sleep(0.5)

    name = kind("test.standaard", slow, max_attempts=1)
    [job_id] = await put(maker, name)
    s = settings.model_copy(update={"job_timeout": 0.05})
    assert await jobs.run_pending(s) == 1
    job = await get(maker, job_id)
    assert job.status == "failed" and job.error == "Duurde langer dan 0.05 seconden"


def test_de_bestaande_soorten_veranderen_niet():
    fase3_en_4 = {
        "preview",
        "preview.outdated",
        "photometa",
        "user.delete",
        "office.pdf",
        "office.pdf.backfill",
    }
    known = {t for t in jobs._kinds if not t.startswith(("media.", "test."))}
    assert fase3_en_4 <= known, sorted(fase3_en_4 - known)
    for t in known:
        assert jobs._kinds[t].lane == "default", t
        assert jobs._kinds[t].timeout is None, t
    assert jobs.lane_of("bestaat.niet") == "default"
    # De analyse van media staat in zijn eigen rij, met een eigen grens
    assert jobs._kinds["media.analyse"].lane == "media"
    assert jobs._kinds["media.analyse"].timeout == 3600


@pytest.mark.anyio
async def test_onbekende_rij_geweigerd(settings):
    async def nothing(_s, _p):
        return None

    with pytest.raises(ValueError):
        jobs.register("test.ergens", nothing, lane="ergens")
    with pytest.raises(ValueError):
        jobs.register("test.nul", nothing, timeout=0)
    with pytest.raises(ValueError):
        jobs.Runner(settings, lane="ergens")
    assert "test.ergens" not in jobs._kinds and "test.nul" not in jobs._kinds
