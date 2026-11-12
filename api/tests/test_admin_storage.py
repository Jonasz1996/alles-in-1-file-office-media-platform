"""GET /admin/storage: de onderdelen van de datadisk (gecachet, niet per verzoek gemeten),
totalen en het gebruik per gebruiker; en de bewaartermijn van de prullenbak op /beheer."""

import pytest

from app import jobs, usage
from tests.factories import code_of
from tests.test_files import put, root
from tests.test_shares import person
from tests.test_uploads import create


@pytest.fixture(autouse=True)
def fresh_cache(settings):
    usage.forget(settings)
    yield
    usage.forget(settings)


@pytest.mark.anyio
async def test_onderdelen_en_totalen(client, admin, settings):
    jonas, _ = admin
    jonas_root = (await root(client, jonas))["folder"]["id"]
    await put(client, jonas, jonas_root, "a.txt", b"12345")
    await put(client, jonas, jonas_root, "b.txt", b"12345")  # zelfde inhoud: één blob
    await put(client, jonas, jonas_root, "c.txt", b"abc")
    await put(client, jonas, jonas_root, "c.txt", b"abcd", conflict="replace")  # versie van 3
    gone = (await put(client, jonas, jonas_root, "weg.txt", b"xy")).json()["id"]
    await client.delete(f"/api/v1/files/{gone}", headers=jonas)
    anna_h, _ = await person(client, jonas, "anna@example.nl", quota_bytes=1000)
    location = await create(client, anna_h, (await root(client, anna_h))["folder"]["id"], "u", 50)
    assert location.status_code == 201
    (settings.data_dir / "previews").mkdir(exist_ok=True)
    (settings.data_dir / "previews" / "p.webp").write_bytes(b"p" * 7)

    r = await client.get("/api/v1/admin/storage", headers=jonas)
    assert r.status_code == 200, r.text
    body = r.json()
    [node] = body["nodes"]
    assert node["name"] == settings.node_name
    assert node["parts"]["blobs"] == 5 + 3 + 4 + 2
    assert node["parts"]["previews"] == 7
    assert node["parts"]["database"] > 0
    assert node["disk"]["total_bytes"] > 0
    totals = body["totals"]
    assert totals["logical_bytes"] == 5 + 5 + 4 + 2
    assert totals["stored_bytes"] == 14
    assert totals["versions_bytes"] == 3
    assert totals["trash_bytes"] == 2
    assert totals["previews_bytes"] == 7
    assert totals["uploads_reserved_bytes"] == 50
    assert totals["replicas"] == 1
    first = body["users"][0]
    assert first["display_name"] == "Jonas"
    assert (first["files_bytes"], first["trash_bytes"], first["versions_bytes"]) == (14, 2, 3)
    assert first["file_count"] == 3
    assert [u["display_name"] for u in body["users"]] == ["Jonas", "Anna"]


@pytest.mark.anyio
async def test_meting_gecachet_en_ververst_door_opruimwerk(client, admin, settings):
    jonas, _ = admin
    before = usage.scans
    for _ in range(3):
        assert (await client.get("/api/v1/admin/storage", headers=jonas)).status_code == 200
    assert usage.scans == before + 1  # alleen de eerste keer, niet per verzoek
    (settings.data_dir / "previews").mkdir(exist_ok=True)
    (settings.data_dir / "previews" / "nieuw").write_bytes(b"n" * 11)
    body = (await client.get("/api/v1/admin/storage", headers=jonas)).json()
    assert body["nodes"][0]["parts"]["previews"] == 0  # nog de oude meting
    assert "usage.refresh" in await jobs.run_housekeeping(settings)
    body = (await client.get("/api/v1/admin/storage", headers=jonas)).json()
    assert body["nodes"][0]["parts"]["previews"] == 11


@pytest.mark.anyio
async def test_bewaartermijn_instellen(client, admin):
    jonas, _ = admin
    assert (await client.get("/api/v1/admin/trash", headers=jonas)).json() == {"retention_days": 0}
    r = await client.put("/api/v1/admin/trash", json={"retention_days": 30}, headers=jonas)
    assert r.status_code == 200 and r.json() == {"retention_days": 30}
    assert (await client.get("/api/v1/admin/trash", headers=jonas)).json()["retention_days"] == 30
    r = await client.put("/api/v1/admin/trash", json={"retention_days": 4000}, headers=jonas)
    assert r.status_code == 422 and code_of(r) == "invalid_input"
    anna_h, _ = await person(client, jonas, "anna@example.nl")
    assert (await client.get("/api/v1/admin/storage", headers=anna_h)).status_code == 403
    assert (await client.get("/api/v1/admin/trash", headers=anna_h)).status_code == 403
