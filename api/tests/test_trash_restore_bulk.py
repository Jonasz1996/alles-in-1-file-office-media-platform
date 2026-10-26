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

    # x alleen: zijn map ligt nog in de prullenbak, dus naar de hoofdmap, met een vrije naam
    r = await restore(client, jonas, [x])
    results = r.json()["results"]
    assert [x["ok"] for x in results] == [True]
    assert results[0]["node"]["parent_id"] == root_id
    assert results[0]["node"]["name"] == "x (1).txt"
    r = await restore(client, jonas, [folder])
    assert r.json()["results"][0]["node"]["parent_id"] == root_id


@pytest.mark.anyio
async def test_een_map_en_wat_erin_lag_samen_terug(client, admin, monkeypatch):
    """Ligt een map en iets daaronder allebei in de prullenbak (in één batch/trash, met dezelfde
    deleted_at), dan gaat de map eerst terug, in welke volgorde de ids ook komen: zo komt wat
    erin lag terug in de map en niet in de hoofdmap. Ook over de stukken van 500 heen."""
    jonas, _ = admin
    monkeypatch.setattr(trash, "BATCH", 2)
    root_id = (await root(client, jonas))["folder"]["id"]
    reis = await mkdir(client, jonas, root_id, "Reis")
    dag = await mkdir(client, jonas, reis, "Dag 1")
    foto = (await put(client, jonas, dag, "Reis foto.txt", b"f")).json()["id"]
    los = (await put(client, jonas, root_id, "los.txt", b"l")).json()["id"]
    r = await client.post(
        "/api/v1/batch/trash", json={"ids": [foto, dag, los, reis]}, headers=jonas
    )
    assert [x["ok"] for x in r.json()["results"]] == [True] * 4

    r = await restore(client, jonas, [foto, dag, los, reis])
    results = r.json()["results"]
    # In de volgorde van het verzoek, elk op zijn oude plek
    assert [x["id"] for x in results] == [foto, dag, los, reis]
    assert [(x["ok"], x["node"]["parent_id"]) for x in results] == [
        (True, dag),
        (True, reis),
        (True, root_id),
        (True, root_id),
    ]
    assert [x["node"]["name"] for x in results] == ["Reis foto.txt", "Dag 1", "los.txt", "Reis"]
    children = (await root(client, jonas))["children"]
    assert sorted(c["name"] for c in children) == ["Reis", "los.txt"]
    assert (await client.get("/api/v1/trash", headers=jonas)).json() == []


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


@pytest.mark.anyio
async def test_geen_query_per_node(client, admin, settings):
    """Terugzetten kijkt de nodes en de bezette namen per stuk in één keer na (niet per node):
    10.000 terug uit de prullenbak duurde anders ruim een minuut. Een naam die wel bezet is,
    wordt nog steeds genummerd, ook tussen twee nodes uit hetzelfde stuk."""
    from tests.test_search import count_queries

    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]

    async def statements(n):
        folder = await mkdir(client, jonas, root_id, f"Map {n}")
        ids = [(await put(client, jonas, folder, f"{i}.txt", b"x")).json()["id"] for i in range(n)]
        r = await client.post("/api/v1/batch/trash", json={"ids": ids}, headers=jonas)
        assert all(x["ok"] for x in r.json()["results"])
        with count_queries(settings) as seen:
            r = await restore(client, jonas, ids)
        assert [x["ok"] for x in r.json()["results"]] == [True] * n
        assert [x["node"]["parent_id"] for x in r.json()["results"]] == [folder] * n
        return len(seen)

    assert await statements(3) == await statements(40)

    # Bezet: door wat er nu staat, en door een node die in hetzelfde stuk eerder terugging
    a = (await put(client, jonas, root_id, "zelfde.txt", b"a")).json()["id"]
    await client.delete(f"/api/v1/files/{a}", headers=jonas)
    b = (await put(client, jonas, root_id, "Zelfde.txt", b"b")).json()["id"]
    await client.delete(f"/api/v1/files/{b}", headers=jonas)
    await put(client, jonas, root_id, "ZELFDE.txt", b"c")
    r = await restore(client, jonas, [a, b])
    assert [x["node"]["name"] for x in r.json()["results"]] == ["zelfde (1).txt", "Zelfde (2).txt"]
    c = (await put(client, jonas, root_id, "uniek.txt", b"d")).json()["id"]
    await client.delete(f"/api/v1/files/{c}", headers=jonas)
    d = (await put(client, jonas, root_id, "Uniek.txt", b"e")).json()["id"]
    await client.delete(f"/api/v1/files/{d}", headers=jonas)
    r = await restore(client, jonas, [c, d])
    assert [x["node"]["name"] for x in r.json()["results"]] == ["uniek.txt", "Uniek (1).txt"]
