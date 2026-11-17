"""GET /admin/jobs: per soort, wat loopt, de oudste wachtende, de mislukte in pagina's; en
DELETE /admin/jobs/{id} voor een mislukte taak."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Job
from tests.factories import code_of, maker
from tests.test_shares import person


def job(type_: str, status: str, **values) -> Job:
    return Job(
        id=values.pop("id", uuid.uuid4()),
        type=type_,
        key=values.pop("key", None),
        payload={},
        status=status,
        attempts=values.pop("attempts", 1),
        max_attempts=3,
        run_after=values.pop("run_after", datetime.now(UTC)),
        **values,
    )


@pytest.mark.anyio
async def test_overzicht_per_soort_en_lopend(client, admin, settings):
    headers, _ = admin
    now = datetime.now(UTC)
    async with maker(settings)() as session:
        session.add_all(
            [
                job("preview", "queued", run_after=now - timedelta(minutes=3)),
                job("preview", "queued", run_after=now + timedelta(days=1)),  # bewust later
                job("preview", "running", started_at=now - timedelta(seconds=40), key="p:1"),
                job("user.delete", "running", started_at=now - timedelta(seconds=90)),
                job("preview", "failed", finished_at=now, error="kapot"),
                job("preview", "done", finished_at=now),
            ]
        )
        await session.commit()
    r = await client.get("/api/v1/admin/jobs", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"] == {"queued": 2, "running": 2, "done": 1, "failed": 1}
    assert body["by_type"] == [
        {"type": "preview", "status": "done", "count": 1},
        {"type": "preview", "status": "failed", "count": 1},
        {"type": "preview", "status": "queued", "count": 2},
        {"type": "preview", "status": "running", "count": 1},
        {"type": "user.delete", "status": "running", "count": 1},
    ]
    assert [j["type"] for j in body["running"]] == ["user.delete", "preview"]  # langst eerst
    assert body["running"][1]["key"] == "p:1"
    oldest = datetime.fromisoformat(body["oldest_queued_at"])
    assert abs((oldest - (now - timedelta(minutes=3))).total_seconds()) < 2
    assert body["next_before"] is None


@pytest.mark.anyio
async def test_mislukte_in_paginas(client, admin, settings):
    headers, _ = admin
    now = datetime.now(UTC)
    async with maker(settings)() as session:
        session.add_all(
            job("preview", "failed", finished_at=now - timedelta(seconds=i), error=f"fout {i}")
            for i in range(150)
        )
        await session.commit()
    first = (await client.get("/api/v1/admin/jobs", headers=headers)).json()
    assert len(first["failed"]) == 100 and first["failed"][0]["error"] == "fout 0"
    assert first["next_before"]
    second = await client.get(
        "/api/v1/admin/jobs", params={"before": first["next_before"]}, headers=headers
    )
    body = second.json()
    assert [j["error"] for j in body["failed"]] == [f"fout {i}" for i in range(100, 150)]
    assert body["next_before"] is None
    r = await client.get("/api/v1/admin/jobs", params={"before": "onzin"}, headers=headers)
    assert r.status_code == 422 and code_of(r) == "invalid_input"


@pytest.mark.anyio
async def test_mislukte_taak_weggooien(client, admin, settings):
    headers, _ = admin
    failed = job("preview", "failed", finished_at=datetime.now(UTC), error="kapot")
    queued = job("preview", "queued")
    async with maker(settings)() as session:
        session.add_all([failed, queued])
        await session.commit()
    r = await client.delete(f"/api/v1/admin/jobs/{failed.id}", headers=headers)
    assert r.status_code == 204
    async with maker(settings)() as session:
        assert await session.get(Job, failed.id) is None
    r = await client.delete(f"/api/v1/admin/jobs/{failed.id}", headers=headers)
    assert r.status_code == 404 and code_of(r) == "job_not_found"
    r = await client.delete(f"/api/v1/admin/jobs/{queued.id}", headers=headers)
    assert r.status_code == 409 and code_of(r) == "not_failed"
    other, _ = await person(client, headers, "anna@example.nl")
    assert (
        await client.delete(f"/api/v1/admin/jobs/{queued.id}", headers=other)
    ).status_code == 403
