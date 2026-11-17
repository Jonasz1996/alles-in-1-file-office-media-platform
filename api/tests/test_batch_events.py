"""Events van bulkacties: gebundeld (hooguit 500 ids per event), ook voor wie meekijkt via
een deling, na de commit."""

import uuid

import pytest
from sqlalchemy import insert

from app import events
from app.events import Hub
from app.models import Node
from app.text import fold
from tests.factories import maker, user_by_email
from tests.test_files import mkdir, put, root
from tests.test_files_events import drain
from tests.test_shares import person, share_with


@pytest.fixture
def hub(monkeypatch):
    fresh = Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


def ids_of(data) -> list[str]:
    return data["ids"] if "ids" in data else [data["id"]]


@pytest.mark.anyio
async def test_gebundeld_en_voor_meekijkers(client, admin, hub):
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    gedeeld = await mkdir(client, jonas, root_id, "Gedeeld")
    elders = await mkdir(client, jonas, root_id, "Elders")
    ids = [
        (await put(client, jonas, gedeeld, f"{i}.txt", b"%d" % i)).json()["id"] for i in range(3)
    ]
    _, anna = await person(client, jonas, "anna@example.nl")
    await share_with(client, jonas, gedeeld, user_id=anna, permission="read")
    mine = hub.subscribe(uuid.UUID(tokens["user"]["id"]))
    hers = hub.subscribe(uuid.UUID(anna))
    drain(mine), drain(hers)

    r = await client.post(
        "/api/v1/batch/move", json={"ids": ids, "parent_id": elders}, headers=jonas
    )
    assert r.status_code == 200, r.text
    assert drain(mine) == [
        ("node.changed", {"ids": ids, "parent_id": elders, "old_parent_id": gedeeld})
    ]
    # Anna ziet Elders niet: geen parent_id, wel waar het vandaan kwam
    assert drain(hers) == [
        ("node.changed", {"ids": ids, "parent_id": None, "old_parent_id": gedeeld})
    ]

    r = await client.post(
        "/api/v1/batch/copy", json={"ids": ids, "parent_id": gedeeld}, headers=jonas
    )
    copies = [x["node"]["id"] for x in r.json()["results"]]
    assert drain(mine) == [("node.created", {"ids": copies, "parent_id": gedeeld})]
    assert drain(hers) == [("node.created", {"ids": copies, "parent_id": gedeeld})]

    r = await client.post("/api/v1/batch/trash", json={"ids": [gedeeld]}, headers=jonas)
    assert drain(mine) == [("node.deleted", {"id": gedeeld, "parent_id": root_id})]
    assert drain(hers) == [("node.deleted", {"id": gedeeld, "parent_id": None})]


@pytest.mark.anyio
async def test_hooguit_500_ids_per_event(client, admin, hub, settings):
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    bron = await mkdir(client, jonas, root_id, "Bron")
    async with maker(settings)() as session:
        owner = await user_by_email(session, "jonas@example.nl")
        rows = [
            {
                "id": uuid.uuid4(),
                "parent_id": uuid.UUID(bron),
                "owner_id": owner.id,
                "name": f"map {i:04}",
                "search_name": fold(f"map {i:04}"),
                "kind": "dir",
            }
            for i in range(1100)
        ]
        await session.execute(insert(Node), rows)
        await session.commit()
    ids = [str(r["id"]) for r in rows]
    mine = hub.subscribe(uuid.UUID(tokens["user"]["id"]))
    drain(mine)
    r = await client.post("/api/v1/batch/trash", json={"ids": ids}, headers=jonas)
    assert r.status_code == 200 and all(x["ok"] for x in r.json()["results"])
    seen = drain(mine)
    assert {t for t, _ in seen} == {"node.deleted"}
    assert all(len(ids_of(d)) <= 500 for _, d in seen)
    assert sorted(i for _, d in seen for i in ids_of(d)) == sorted(ids)
    assert len(seen) == 3  # 500 + 500 + 100 (per stuk van BATCH)
