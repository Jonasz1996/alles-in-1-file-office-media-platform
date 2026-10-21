"""jobs.chain en run_after: een taak die zijn opvolger klaarzet (een keten in stukken), en
taken die pas later mogen lopen."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import jobs
from app.models import Job
from tests.factories import maker


@pytest.fixture
def name():
    """Een eigen soort taak per test, en daarna weer weg."""
    kind = f"keten-{uuid.uuid4().hex[:8]}"
    yield kind
    jobs._kinds.pop(kind, None)


async def rows(settings, kind) -> list[Job]:
    async with maker(settings)() as session:
        return list(
            await session.scalars(select(Job).where(Job.type == kind).order_by(Job.created_at))
        )


@pytest.mark.anyio
async def test_keten_van_drie_loopt_drie_keer(settings, name):
    seen: list[int] = []
    blocked: list[uuid.UUID | None] = []

    async def run(s, payload):
        seen.append(payload["n"])
        async with maker(s)() as session:
            # Terwijl de keten loopt: een tweede keten via enqueue met dezelfde key mag niet
            blocked.append(await jobs.enqueue(session, name, {"n": 99}, key="keten"))
            if payload["n"] < 3:
                follow = await jobs.chain(session, name, {"n": payload["n"] + 1}, "keten")
                assert follow is not None
                # Een tweede opvolger naast de klaarstaande komt er niet bij
                assert await jobs.chain(session, name, {"n": 0}, "keten") is None
            await session.commit()

    jobs.register(name, run)
    async with maker(settings)() as session:
        first = await jobs.enqueue(session, name, {"n": 1}, key="keten")
        assert isinstance(first, uuid.UUID)
        await session.commit()

    # Een opvolger staat klaar vanaf het moment van klaarzetten: even verder kijken
    assert await jobs.run_pending(settings, at=jobs.now() + timedelta(minutes=1)) == 3
    assert seen == [1, 2, 3]
    assert blocked == [None, None, None]
    assert [j.status for j in await rows(settings, name)] == ["done"] * 3


@pytest.mark.anyio
async def test_run_after_wordt_gerespecteerd(settings, name):
    seen: list[str] = []

    async def run(_s, payload):
        seen.append(payload["w"])

    jobs.register(name, run)
    now = jobs.now()
    async with maker(settings)() as session:
        await jobs.enqueue(session, name, {"w": "later"}, run_after=now + timedelta(minutes=5))
        await jobs.enqueue(session, name, {"w": "nu"})
        added = await jobs.enqueue_many(
            session, name, [({"w": "veel"}, "veel")], run_after=now + timedelta(minutes=10)
        )
        assert added == 1
        await session.commit()

    assert await jobs.run_pending(settings, at=now + timedelta(seconds=1)) == 1
    assert seen == ["nu"]
    assert await jobs.run_pending(settings, at=now + timedelta(minutes=6)) == 1
    assert seen == ["nu", "later"]
    assert await jobs.run_pending(settings, at=now + timedelta(minutes=11)) == 1
    assert seen == ["nu", "later", "veel"]


@pytest.mark.anyio
async def test_chain_met_run_after(settings, name):
    async def run(_s, _payload):
        pass

    jobs.register(name, run)
    later = jobs.now() + timedelta(hours=1)
    async with maker(settings)() as session:
        job_id = await jobs.chain(session, name, {"after": "x"}, "keten", run_after=later)
        await session.commit()
        job = await session.get(Job, job_id)
    assert job.status == "queued"
    assert job.run_after.replace(tzinfo=None) == later.replace(tzinfo=None)
