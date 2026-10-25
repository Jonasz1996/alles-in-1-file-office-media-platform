"""GET /admin/status: het blok system en de meldingen met een code (notices)."""

from datetime import UTC, datetime, timedelta

import pytest

from app import __version__, jobs
from app.cluster import DbMember, Disk, NodeStatus, disk_notice, summarize
from app.main import app
from app.routers import admin as admin_router
from tests.factories import maker


async def status(client, headers) -> dict:
    r = await client.get("/api/v1/admin/status", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.anyio
async def test_systeemblok(client, admin, settings, monkeypatch):
    headers, _ = admin
    started = datetime.now(UTC) - timedelta(minutes=5)
    monkeypatch.setattr(app.state, "started_at", started, raising=False)
    monkeypatch.setattr(jobs, "runner_alive", lambda: True)
    body = await status(client, headers)
    system = body["system"]
    assert system["version"] == __version__
    assert system["mode"] == "standalone" and system["db_kind"] == "sqlite"
    assert 290 <= system["uptime_s"] <= 400
    assert system["db_bytes"] > 0
    assert system["jobs"] == {
        "queued": 0,
        "running": 0,
        "failed": 0,
        "oldest_queued_at": None,
        "runner_alive": True,
    }
    assert system["https"] == {"state": "uit", "expires_at": None, "days_left": None}
    assert body["status"] == "ok" or body["notices"][0]["code"] == "disk_full"
    assert body["messages"] == [n["text"] for n in body["notices"]]


@pytest.mark.anyio
async def test_taken_die_niet_draaien_of_wachten(client, admin, settings, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(app.state, "started_at", datetime.now(UTC), raising=False)
    monkeypatch.setattr(jobs, "runner_alive", lambda: False)
    async with maker(settings)() as session:
        await jobs.enqueue(
            session, "test-wacht", {}, run_after=datetime.now(UTC) - timedelta(minutes=30)
        )
        await session.commit()
    body = await status(client, headers)
    codes = {n["code"]: n for n in body["notices"]}
    assert "jobs_stopped" in codes
    assert codes["jobs_waiting"]["params"]["minutes"] >= 29
    assert body["status"] == "degraded"
    assert body["system"]["jobs"]["queued"] == 1
    assert body["system"]["jobs"]["oldest_queued_at"] is not None


@pytest.mark.anyio
async def test_https_fout_en_bijna_verlopen(client, admin, certs, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(jobs, "runner_alive", lambda: True)
    certs.config.domain = "app.voorbeeld.nl"
    certs.state = "fout"
    certs.error = "De DNS klopt niet"
    certs.error_code, certs.error_params = "acme_dns", {"detail": "NXDOMAIN"}
    certs.not_after = datetime.now(UTC) + timedelta(days=3, hours=1)
    body = await status(client, headers)
    codes = {n["code"]: n for n in body["notices"]}
    assert codes["https_error"]["params"] == {"error": "acme_dns", "detail": "NXDOMAIN"}
    assert codes["https_expiring"]["params"] == {"days": 3}
    assert body["status"] == "degraded"
    assert body["system"]["https"]["state"] == "fout"
    assert body["system"]["https"]["days_left"] == 3

    certs.listener_error = "Poort 443 is bezet"
    certs.listener_error_code = "listener_bind"
    certs.listener_error_params = {"port": 443, "reason": "bezet"}
    https = (await client.get("/api/v1/admin/https", headers=headers)).json()
    assert https["error_code"] == "acme_dns"
    assert https["listener_error_code"] == "listener_bind"
    assert https["listener_error_params"] == {"port": 443, "reason": "bezet"}
    body = await status(client, headers)
    listener = next(n for n in body["notices"] if n["code"] == "https_listener")
    assert listener["params"] == {"port": 443, "reason": "bezet", "error": "listener_bind"}


@pytest.mark.anyio
async def test_standalone_meldingen_met_code(client, admin, settings, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(jobs, "runner_alive", lambda: True)

    class Full:
        total, used, free = 100, 95, 5

    monkeypatch.setattr(admin_router.shutil, "disk_usage", lambda _path: Full)
    body = await status(client, headers)
    full = next(n for n in body["notices"] if n["code"] == "disk_full")
    assert full["params"] == {"node": settings.node_name, "percent": 80}
    assert full["text"] == f"{settings.node_name}: datadisk meer dan 80 % vol."


def test_cluster_meldingen_met_code():
    nodes = [
        NodeStatus(
            name="node1",
            ip="1",
            checks={"etcd": True, "postgres": True, "api": False},
            seaweed_leader=True,
            disk=Disk(total_bytes=10, used_bytes=9, free_bytes=1),
        ),
        NodeStatus(name="node2", ip="2", checks={"etcd": True, "postgres": True, "api": True}),
    ]
    members = [DbMember(name="node1", role="leader", state="running")]
    state, notices = summarize(nodes, members)
    assert state == "degraded"
    assert [n.code for n in notices] == ["db_no_sync", "node_down", "disk_full"]
    assert notices[1].params == {"node": "node1", "parts": "api"}
    assert disk_notice("x", Disk(total_bytes=10, used_bytes=5, free_bytes=5)) is None
