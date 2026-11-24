"""AI-upscaling: de rij, het venster en de API van de upscaler (fase 11, stap 5c)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.arr import upscale
from app.models import ArrUpscale, Node
from tests.arr.test_subs import fake_os, maker, movie  # noqa: F401

pytestmark = pytest.mark.anyio
BASE = "/api/v1/arr/upscale"


def at(minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, 23, 14, minute, second)


def test_window() -> None:
    assert upscale.seconds_left(at(19, 59), 20, 40) == 0
    assert upscale.seconds_left(at(20), 20, 40) == 20 * 60
    assert upscale.seconds_left(at(39, 30), 20, 40) == 30
    assert upscale.seconds_left(at(40), 20, 40) == 0
    # Over het hele uur heen
    assert upscale.seconds_left(at(55), 50, 10) == 15 * 60
    assert upscale.seconds_left(at(5), 50, 10) == 5 * 60
    assert upscale.seconds_left(at(30), 50, 10) == 0


@pytest.fixture
def open_window(monkeypatch):
    class Fixed(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 11, 23, 14, 25, tzinfo=tz) if tz else at(25)

    from app.routers import arr_upscale

    monkeypatch.setattr(arr_upscale.dt, "datetime", Fixed)


async def test_queue_and_worker(client, admin, settings, fake_os, open_window):  # noqa: F811
    headers, _ = admin
    node_id = await movie(client, headers, settings)

    r = await client.post(BASE, json={"node_id": str(node_id)}, headers=headers)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["state"] == "queued" and job["name"] == "Inception (2010).mkv"
    r = await client.post(BASE, json={"node_id": str(node_id)}, headers=headers)
    assert r.status_code == 409

    # Zonder token niets; uit: geen werk
    assert (await client.post("/api/v1/upscaler/claim")).status_code == 401
    token = (await client.post("/api/v1/admin/arr/upscale/token", headers=headers)).json()["token"]
    w = {"Authorization": f"Bearer {token}"}
    assert (await client.post("/api/v1/upscaler/claim", headers=w)).status_code == 204
    body = {"enabled": True, "start": 20, "end": 40, "vram_mb": 4096}
    r = await client.put("/api/v1/admin/arr/upscale", json=body, headers=headers)
    assert r.status_code == 200 and r.json()["window_open"] is True

    r = await client.post("/api/v1/upscaler/claim", headers=w)
    assert r.status_code == 200, r.text
    claim = r.json()
    assert claim["id"] == job["id"] and claim["seconds_left"] == 15 * 60
    src = await client.get(f"/api/v1/upscaler/jobs/{job['id']}/source", headers=w)
    assert src.status_code == 200 and len(src.content) == 200_000

    r = await client.post(
        f"/api/v1/upscaler/jobs/{job['id']}/progress",
        json={"segments_done": 3, "segments_total": 240},
        headers=w,
    )
    assert r.json() == {"seconds_left": 15 * 60}
    # Opnieuw claimen hervat dezelfde job
    assert (await client.post("/api/v1/upscaler/claim", headers=w)).json()["segments_done"] == 3

    r = await client.put(
        f"/api/v1/upscaler/jobs/{job['id']}/result", content=b"mkv" * 1000, headers=w
    )
    assert r.status_code == 204, r.text
    [row] = (await client.get(BASE, headers=headers)).json()
    assert row["state"] == "done"
    async with maker(settings)() as session:
        video = await session.get(Node, node_id)
        result = await session.get(Node, uuid.UUID(row["result_node_id"]))
        assert result.name == "Inception (2010) - AI 4K.mkv"
        assert result.parent_id == video.parent_id and result.owner_id == video.owner_id

    # Een nieuw token: het oude werkt niet meer
    await client.post("/api/v1/admin/arr/upscale/token", headers=headers)
    assert (await client.post("/api/v1/upscaler/claim", headers=w)).status_code == 401


async def test_cancel_stops_worker(client, admin, settings, fake_os, open_window):  # noqa: F811
    headers, _ = admin
    node_id = await movie(client, headers, settings)
    job = (await client.post(BASE, json={"node_id": str(node_id)}, headers=headers)).json()
    token = (await client.post("/api/v1/admin/arr/upscale/token", headers=headers)).json()["token"]
    w = {"Authorization": f"Bearer {token}"}
    body = {"enabled": True, "start": 20, "end": 40, "vram_mb": 4096}
    await client.put("/api/v1/admin/arr/upscale", json=body, headers=headers)
    assert (await client.post("/api/v1/upscaler/claim", headers=w)).status_code == 200
    assert (await client.delete(f"{BASE}/{job['id']}", headers=headers)).status_code == 204
    r = await client.post(
        f"/api/v1/upscaler/jobs/{job['id']}/progress", json={"segments_done": 1}, headers=w
    )
    assert r.status_code == 409
    async with maker(settings)() as session:
        assert (await session.scalar(select(ArrUpscale.state))) == "cancelled"
