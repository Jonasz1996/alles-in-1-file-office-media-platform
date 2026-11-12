"""De stap probe van media.analyse (Keuze 14, 15): alleen een fout van het bestand zelf wordt een
blijvende probe met state failed. Een fout van de worker of de omgeving (ffmpeg_missing,
not_built, timeout, internal) slaat niets op: de job probeert het later opnieuw."""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import jobs
from app.db import get_engine
from app.media import analysis, client
from app.media.client import WorkerFailed
from app.media.types import Probe
from app.media.wire import Code
from app.models import Blob, Job, MediaProbe
from app.storage import BlobStore

pytestmark = pytest.mark.anyio

READY = {
    "state": "ready",
    "container": "matroska",
    "duration_ms": 6000,
    "streams": [{"index": 0, "type": "video", "codec": "h264", "width": 320, "height": 240}],
}


@pytest.fixture
def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


@pytest.fixture
async def sha(settings, maker):
    """Een blob op schijf en in de tabel blob (de vreemde sleutel van media_probe)."""
    data = b"geen echte video"
    digest = hashlib.sha256(data).hexdigest()
    path = BlobStore(settings.blob_dir).path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    async with maker() as session:
        session.add(Blob(sha256=digest, size=len(data), ref_count=1))
        await session.commit()
    return digest


@pytest.fixture
def worker(monkeypatch):
    """client.probe uit een lijst: een Probe-dict, of een code voor WorkerFailed."""
    answers: list = []
    calls: list[str] = []

    async def probe(source, *, limit_s=client.PROBE_TIMEOUT):
        calls.append(source.name)
        answer = answers.pop(0)
        if isinstance(answer, str):
            raise WorkerFailed(answer, "nep")
        return Probe.model_validate(answer)

    monkeypatch.setattr(client, "probe", probe)
    # Alleen de stap probe: de andere stappen (keyframes van M4, …) vragen de worker zelf, en
    # zonder worker wordt dat Defer; deze tests gaan over de probe (M0-hotfix van M4)
    only = {name: step for name, step in analysis._steps.items() if name == "probe"}
    monkeypatch.setattr(analysis, "_steps", only)
    return answers, calls


async def stored(maker, sha) -> MediaProbe | None:
    async with maker() as session:
        return await session.get(MediaProbe, sha)


@pytest.mark.parametrize(
    "code",
    [Code.FFMPEG_MISSING, Code.NOT_BUILT, Code.TIMEOUT, Code.INTERNAL, Code.BAD_ARGS],
)
async def test_een_fout_van_de_worker_slaat_niets_op_en_de_volgende_run_probet(
    settings, maker, sha, worker, code
):
    answers, calls = worker
    answers += [code.value, READY]
    with pytest.raises(WorkerFailed):
        await analysis.run(settings, {"sha": sha, "library_id": None})
    assert await stored(maker, sha) is None
    await analysis.run(settings, {"sha": sha, "library_id": None})
    row = await stored(maker, sha)
    assert row is not None and row.state == "ready" and row.error is None
    assert len(calls) == 2


@pytest.mark.parametrize("code", sorted(analysis.FILE_ERRORS))
async def test_een_fout_van_het_bestand_blijft_staan(settings, maker, sha, worker, code):
    answers, calls = worker
    answers += [code, READY]
    await analysis.run(settings, {"sha": sha, "library_id": None})
    row = await stored(maker, sha)
    assert row is not None and row.state == "failed" and row.error == code
    await analysis.run(settings, {"sha": sha, "library_id": None})
    assert len(calls) == 1  # niet opnieuw: het bestand verandert niet
    assert (await stored(maker, sha)).state == "failed"


async def test_een_oude_failed_met_een_fout_van_de_worker_wordt_opnieuw_geprobet(
    settings, maker, sha, worker
):
    """Een rij van vóór FILE_ERRORS (failed met ffmpeg_missing) is geen eindoordeel."""
    answers, calls = worker
    await analysis.save_probe(
        maker, sha, Probe(state="failed", error=Code.FFMPEG_MISSING.value, blob_sha=sha)
    )
    answers.append(READY)
    await analysis.run(settings, {"sha": sha, "library_id": None})
    assert (await stored(maker, sha)).state == "ready"
    assert len(calls) == 1


async def test_de_job_probeert_het_later_opnieuw_met_backoff(settings, maker, sha, worker):
    """Via de rij media: ffmpeg_missing is een mislukte poging (queued, met backoff), en de
    volgende poging maakt de probe ready."""
    answers, calls = worker
    answers += [Code.FFMPEG_MISSING.value, READY]
    async with maker() as session:
        await analysis.enqueue(session, sha, library_id=None)
        await session.commit()
    media = settings.model_copy(update={"media_enabled": True})
    assert await jobs.run_pending(media, lane=analysis.LANE) == 1
    async with maker() as session:
        job = (await session.execute(Job.__table__.select())).one()
    assert job.status == "queued" and job.attempts == 1 and "ffmpeg_missing" in job.error
    assert await stored(maker, sha) is None
    later = jobs.now() + timedelta(hours=1)
    assert await jobs.run_pending(media, at=later, lane=analysis.LANE) == 1
    assert (await stored(maker, sha)).state == "ready"
    assert len(calls) == 2
