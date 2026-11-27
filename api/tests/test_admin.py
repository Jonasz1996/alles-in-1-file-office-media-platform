import pytest


@pytest.mark.anyio
async def test_status_standalone(client, admin):
    headers, _ = admin
    root_id = (await client.get("/api/v1/files/root", headers=headers)).json()["folder"]["id"]
    for name in ("a", "b"):
        await client.put(
            f"/api/v1/files/{root_id}/upload",
            params={"name": name},
            content=b"zelfde",
            headers=headers,
        )
    r = await client.get("/api/v1/admin/status", headers=headers)
    body = r.json()
    assert r.status_code == 200
    assert body["nodes"][0]["checks"] == {"database": True, "storage": True, "api": True}
    assert body["users"] == 1 and body["files"] == 2
    assert body["logical_bytes"] == 12 and body["stored_bytes"] == 6


@pytest.mark.anyio
async def test_status_alleen_voor_beheerder(client):
    assert (await client.get("/api/v1/admin/status")).status_code == 401
