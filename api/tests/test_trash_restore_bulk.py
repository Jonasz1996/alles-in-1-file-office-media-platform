"""POST /api/v1/trash/restore: veel nodes tegelijk terug uit de prullenbak, met per id een
ItemResult, en de events gebundeld via notify_many."""

import uuid

import pytest

from app import events
from app.events import Hub
from app.routers import trash
from tests.factories import code_of
from tests.test_files import mkdir, put, root
from tests.test_files_events import drain
from tests.test_shares import person, share_with


@pytest.fixture
def hub(monkeypatch):
    fresh = Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


async def restore(client, headers, ids):
    return await client.post(
        "/api/v1/trash/restore", json={"ids": [str(i) for i in ids]}, headers=headers
    )


@pytest.mark.anyio
async def test_deels_gelukt_met_een_code_per_id(client, admin, hub):
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    a = (await put(client, jonas, root_id, "a.txt", b"a")).json()["id"]
    b = (await put(client, jonas, root_id, "b.txt", b"b")).json()["id"]
    live = (await put(client, jonas, root_id, "c.txt", b"c")).json()["id"]
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    hers = (await put(client, anna, (await root(client, anna))["folder"]["id"], "h", b"h")).json()
    for node_id in (a, b):
        assert (await client.delete(f"/api/v1/files/{node_id}", headers=jonas)).status_code == 204
    await client.delete(f"/api/v1/files/{hers['id']}", headers=anna)
    missing = uuid.uuid4()

    with hub.subscribe(uuid.UUID(tokens["user"]["id"])) as j:
        r = await restore(client, jonas, [a, live, missing, hers["id"], b, a])
        assert r.status_code == 200, r.text
        results = r.json()["results"]
        # Eén resultaat per id, in de volgorde van het verzoek (dubbele één keer)
        assert [x["id"] for x in results] == [a, live, str(missing), hers["id"], b]
        assert [(x["ok"], x["code"]) for x in results] == [
            (True, None),
            (False, "not_trashed"),
            (False, "not_found"),
            (False, "not_found"),  # van een ander: niet te zien
            (True, None),
        ]
        assert results[0]["node"]["id"] == a and results[0]["node"]["parent_id"] == root_id
        assert results[1]["message"] == "Staat niet in de prullenbak"
        # Gebundeld: één event voor beide teruggezette bestanden
        assert drain(j) == [("node.changed", {"ids": [a, b], "parent_id": root_id})]

    listing = (await client.get("/api/v1/trash", headers=jonas)).json()
    assert listing == []
    assert (await client.get("/api/v1/trash", headers=anna)).json()[0]["id"] == hers["id"]


@pytest.mark.anyio
async def test_naam_bezet_en_map_weg(client, admin):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Map")
    x = (await put(client, jonas, folder, "x.txt", b"x")).json()["id"]
    await client.delete(f"/api/v1/files/{x}", headers=jonas)
    await client.delete(f"/api/v1/files/{folder}", headers=jonas)
    await put(client, jonas, root_id, "x.txt", b"ander")

    # x eerst: zijn map ligt nog in de prullenbak, dus naar de hoofdmap, met een vrije naam
    r = await restore(client, jonas, [x, folder])
    results = r.json()["results"]
    assert [x["ok"] for x in results] == [True, True]
    assert results[0]["node"]["parent_id"] == root_id
    assert results[0]["node"]["name"] == "x (1).txt"
    assert results[1]["node"]["parent_id"] == root_id


@pytest.mark.anyio
async def test_in_stukken_van_500(client, admin, monkeypatch):
    jonas, _ = admin
    monkeypatch.setattr(trash, "BATCH", 2)
    root_id = (await root(client, jonas))["folder"]["id"]
    ids = [
        (await put(client, jonas, root_id, f"{i}.txt", b"%d" % i)).json()["id"] for i in range(5)
    ]
    for node_id in ids:
        await client.delete(f"/api/v1/files/{node_id}", headers=jonas)
    r = await restore(client, jonas, ids)
    assert [x["ok"] for x in r.json()["results"]] == [True] * 5
    assert (await client.get("/api/v1/trash", headers=jonas)).json() == []


@pytest.mark.anyio
async def test_gedeelde_map_eronder_komt_terug_bij_wie_hem_ziet(client, admin, hub):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    werk = await mkdir(client, jonas, root_id, "Werk")
    gedeeld = await mkdir(client, jonas, werk, "Gedeeld")
    _, anna_id = await person(client, jonas, "anna@example.nl")
    await share_with(client, jonas, gedeeld, user_id=anna_id, permission="read")
    await client.delete(f"/api/v1/files/{werk}", headers=jonas)
    with hub.subscribe(uuid.UUID(anna_id)) as a:
        r = await restore(client, jonas, [werk])
        assert r.json()["results"][0]["ok"] is True
        assert drain(a) == [("node.changed", {"id": gedeeld, "parent_id": None})]


@pytest.mark.anyio
async def test_hooguit_10000_ids(client, admin):
    jonas, _ = admin
    r = await restore(client, jonas, [uuid.uuid4() for _ in range(10_001)])
    assert (r.status_code, code_of(r)) == (422, "invalid_input")
    r = await client.post("/api/v1/trash/restore", json={"ids": []}, headers=jonas)
    assert r.json() == {"results": []}
