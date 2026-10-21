import uuid
from datetime import datetime

import pytest

from app import events
from app.events import Hub
from tests.test_files import mkdir, new_user, put, root
from tests.test_uploads import create, send


def drain(sub) -> list[tuple[str, dict]]:
    seen = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        seen.append((event["type"], event["data"]))
    return seen


@pytest.mark.anyio
async def test_elke_wijziging_aan_de_boom_wordt_gemeld(client, admin, monkeypatch):
    headers, tokens = admin
    monkeypatch.setattr(events, "hub", Hub())
    root_id = (await root(client, headers))["folder"]["id"]
    other = await new_user(client, headers, "z@example.nl")
    other_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=other)).json()["id"])

    with (
        events.hub.subscribe(uuid.UUID(tokens["user"]["id"])) as sub,
        events.hub.subscribe(other_id) as others,
    ):
        a = await mkdir(client, headers, root_id, "A")
        b = await mkdir(client, headers, root_id, "B")
        assert drain(sub) == [
            ("node.created", {"id": a, "parent_id": root_id}),
            ("node.created", {"id": b, "parent_id": root_id}),
        ]
        f = (await put(client, headers, a, "f.txt", b"1")).json()["id"]
        await put(client, headers, a, "f.txt", b"2", conflict="replace")
        location = (await create(client, headers, b, "t.txt", 2)).headers["location"]
        t = (await send(client, headers, location, 0, b"ok")).headers["platform-node-id"]
        assert drain(sub) == [
            ("node.created", {"id": f, "parent_id": a}),
            ("node.changed", {"id": f, "parent_id": a}),
            ("node.created", {"id": t, "parent_id": b}),
        ]

        await client.patch(f"/api/v1/files/{f}", json={"name": "g.txt"}, headers=headers)
        await client.patch(f"/api/v1/files/{f}", json={"parent_id": b}, headers=headers)
        assert drain(sub) == [
            ("node.changed", {"id": f, "parent_id": a}),
            ("node.changed", {"id": f, "parent_id": b, "old_parent_id": a}),
        ]

        await client.delete(f"/api/v1/files/{b}", headers=headers)
        await client.post(f"/api/v1/files/{b}/restore", headers=headers)
        await client.delete(f"/api/v1/files/{b}", headers=headers)
        await client.delete(f"/api/v1/trash/{b}", headers=headers)
        assert drain(sub) == [
            ("node.deleted", {"id": b, "parent_id": root_id}),
            ("node.changed", {"id": b, "parent_id": root_id}),
            ("node.deleted", {"id": b, "parent_id": root_id}),
            ("node.deleted", {"id": b, "parent_id": root_id}),
        ]

        # Wat mislukt, wordt niet gemeld
        assert (
            await client.post(
                f"/api/v1/files/{root_id}/folders", json={"name": "a"}, headers=headers
            )
        ).status_code == 409
        assert (await put(client, headers, a, "x", b"x", conflict="fail")).status_code == 201
        assert (await put(client, headers, a, "x", b"y", conflict="fail")).status_code == 409
        assert [e[0] for e in drain(sub)] == ["node.created"]
        # En alleen aan de eigenaar
        assert drain(others) == []


@pytest.mark.anyio
async def test_tijden_met_tijdzone(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    created = (await put(client, headers, root_id, "a.txt", b"a")).json()
    listed = (await root(client, headers))["children"][0]
    for value in (created["created_at"], listed["created_at"], listed["modified_at"]):
        assert datetime.fromisoformat(value).utcoffset() is not None, value
    assert datetime.fromisoformat(created["modified_at"]) == datetime.fromisoformat(
        listed["modified_at"]
    )


@pytest.mark.anyio
async def test_tag_hernoemen_of_weggooien_wordt_gemeld(client, admin, monkeypatch):
    """Lijsten tonen tags als chips: na hernoemen of weggooien laden ze opnieuw."""
    headers, tokens = admin
    monkeypatch.setattr(events, "hub", Hub())
    other = await new_user(client, headers, "t@example.nl")
    other_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=other)).json()["id"])
    tag = (await client.post("/api/v1/tags", json={"name": "Werk"}, headers=headers)).json()["id"]
    with (
        events.hub.subscribe(uuid.UUID(tokens["user"]["id"])) as sub,
        events.hub.subscribe(other_id) as others,
    ):
        await client.patch(f"/api/v1/tags/{tag}", json={"name": "Thuis"}, headers=headers)
        await client.delete(f"/api/v1/tags/{tag}", headers=headers)
        assert drain(sub) == [("tags.changed", {"id": tag}), ("tags.changed", {"id": tag})]
        assert drain(others) == []  # tags zijn persoonlijk
