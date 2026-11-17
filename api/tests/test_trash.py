"""De prullenbak van P6: overzicht ("Was in", mapgroottes, totaal), definitief verwijderen in
bulk, legen en de bewaartermijn via het opruimwerk."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app import events, jobs
from app.events import Hub
from app.models import Node
from app.routers import trash
from tests.factories import code_of, maker
from tests.test_files import blob_files, check_refs, mkdir, put, root
from tests.test_files_events import drain
from tests.test_shares import person, share_with


@pytest.fixture
def hub(monkeypatch):
    fresh = Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


async def trash_it(client, headers, node_id):
    assert (await client.delete(f"/api/v1/files/{node_id}", headers=headers)).status_code == 204


async def overview(client, headers) -> dict:
    r = await client.get("/api/v1/trash/overview", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.anyio
async def test_overzicht(client, admin):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    work = await mkdir(client, jonas, root_id, "Werk")
    sub = await mkdir(client, jonas, work, "Sub")
    deep = await mkdir(client, jonas, sub, "Diep")
    await put(client, jonas, deep, "a.txt", b"12345")
    loose = (await put(client, jonas, sub, "los.txt", b"abc")).json()["id"]
    top = (await put(client, jonas, root_id, "top.txt", b"xy")).json()["id"]
    await trash_it(client, jonas, loose)  # eerst het bestand, dan de map erboven
    await trash_it(client, jonas, deep)
    await trash_it(client, jonas, top)
    body = await overview(client, jonas)
    by_name = {i["node"]["name"]: i for i in body["items"]}
    assert [i["node"]["name"] for i in body["items"]] == ["top.txt", "Diep", "los.txt"]
    assert body["count"] == 3
    assert body["total_bytes"] == 5 + 3 + 2
    assert by_name["Diep"]["size"] == 5
    assert by_name["Diep"]["location"] == [{"id": work, "name": "Werk"}, {"id": sub, "name": "Sub"}]
    assert by_name["Diep"]["restore_to"] == "original"
    assert by_name["top.txt"]["location"] == [] and by_name["top.txt"]["size"] == 2
    # Nu de map erboven ook weg: los.txt kan niet meer terug naar zijn plek
    await trash_it(client, jonas, sub)
    body = await overview(client, jonas)
    by_name = {i["node"]["name"]: i for i in body["items"]}
    assert by_name["los.txt"]["restore_to"] == "root"
    assert by_name["Sub"]["size"] == 5 + 3  # alles eronder, ook wat zelf weggegooid was
    assert body["total_bytes"] == 5 + 3 + 2  # telt één keer


@pytest.mark.anyio
async def test_bulk_verwijderen(client, admin, settings, hub):
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Map")
    inside = (await put(client, jonas, folder, "in.txt", b"binnen")).json()["id"]
    await put(client, jonas, folder, "in.txt", b"binnen 2", conflict="replace")  # met versie
    a = (await put(client, jonas, root_id, "a.txt", b"a")).json()["id"]
    live = (await put(client, jonas, root_id, "blijft.txt", b"blijft")).json()["id"]
    await trash_it(client, jonas, inside)
    await trash_it(client, jonas, folder)
    await trash_it(client, jonas, a)
    anna_h, _ = await person(client, jonas, "anna@example.nl")
    hers = await put(client, anna_h, (await root(client, anna_h))["folder"]["id"], "h", b"h")
    missing = str(uuid.uuid4())
    me = uuid.UUID(tokens["user"]["id"])
    with hub.subscribe(me) as sub:
        r = await client.post(
            "/api/v1/trash/purge",
            json={"ids": [folder, inside, a, live, hers.json()["id"], missing]},
            headers=jonas,
        )
        assert r.status_code == 200, r.text
        events_seen = drain(sub)
    results = {x["id"]: x for x in r.json()["results"]}
    assert [x["id"] for x in r.json()["results"]] == [
        folder,
        inside,
        a,
        live,
        hers.json()["id"],
        missing,
    ]
    assert results[folder]["ok"] and results[inside]["ok"] and results[a]["ok"]
    assert results[live]["code"] == "not_trashed"
    assert results[missing]["code"] == "not_found"
    assert results[hers.json()["id"]]["code"] == "not_found"
    deleted = [data for kind, data in events_seen if kind == "node.deleted"]
    assert {i for d in deleted for i in d.get("ids", [d.get("id")])} == {folder, a}
    assert (await overview(client, jonas))["count"] == 0
    await check_refs(settings)  # ref_count klopt en de inhoud is van schijf
    assert len(blob_files(settings)) == 2  # blijft.txt en die van Anna


@pytest.mark.anyio
async def test_legen(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    for name in ("a", "b", "c"):
        node = (await put(client, jonas, root_id, name, name.encode() * 3)).json()["id"]
        await trash_it(client, jonas, node)
    folder = await mkdir(client, jonas, root_id, "Map")
    await put(client, jonas, folder, "d", b"ddd")
    await trash_it(client, jonas, folder)
    r = await client.delete("/api/v1/trash", headers=jonas)
    assert r.status_code == 200 and r.json() == {"purged": 4}
    assert (await overview(client, jonas))["count"] == 0
    assert blob_files(settings) == set()
    assert (await client.delete("/api/v1/trash", headers=jonas)).json() == {"purged": 0}


@pytest.mark.anyio
async def test_gedeeld_en_weg_ontvanger_hoort_het(client, admin, hub):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Samen")
    anna_h, anna = await person(client, jonas, "anna@example.nl")
    await share_with(client, jonas, folder, user_id=anna, permission="read")
    await trash_it(client, jonas, folder)
    with hub.subscribe(uuid.UUID(anna)) as sub:
        r = await client.post("/api/v1/trash/purge", json={"ids": [folder]}, headers=jonas)
        assert r.json()["results"][0]["ok"]
        assert ("node.deleted", {"id": folder, "parent_id": None}) in drain(sub)


@pytest.mark.anyio
async def test_bewaartermijn_via_opruimwerk(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    old = (await put(client, jonas, root_id, "oud.txt", b"oud")).json()["id"]
    new = (await put(client, jonas, root_id, "nieuw.txt", b"nieuw")).json()["id"]
    await trash_it(client, jonas, old)
    await trash_it(client, jonas, new)
    async with maker(settings)() as session:
        await session.execute(
            update(Node)
            .where(Node.id == uuid.UUID(old))
            .values(deleted_at=datetime.now(UTC) - timedelta(days=40))
        )
        await session.commit()
    # Zonder termijn (standaard 0) blijft alles
    assert await trash.purge_expired(settings) == 0
    r = await client.put("/api/v1/admin/trash", json={"retention_days": 30}, headers=jonas)
    assert r.status_code == 200
    assert "trash.retention" in await jobs.run_housekeeping(settings)
    names = [i["node"]["name"] for i in (await overview(client, jonas))["items"]]
    assert names == ["nieuw.txt"]
    await check_refs(settings)


@pytest.mark.anyio
async def test_te_veel_ids_en_gasten(client, admin):
    jonas, _ = admin
    ids = [str(uuid.uuid4()) for _ in range(trash.MAX_RESTORE + 1)]
    r = await client.post("/api/v1/trash/purge", json={"ids": ids}, headers=jonas)
    assert r.status_code == 422 and code_of(r) == "invalid_input"
    guest, _ = await person(client, jonas, "gast@example.nl", role="guest")
    assert (await overview(client, guest))["count"] == 0
