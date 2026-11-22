"""Verzoeken van gezinsleden (fase 11, stap 6)."""

from __future__ import annotations

import pytest

from tests.arr.test_wanted import fake_tmdb, make_library  # noqa: F401
from tests.test_shares import person

pytestmark = pytest.mark.anyio
BASE = "/api/v1/arr/requests"


async def test_request_approve_decline(client, admin, settings, fake_tmdb):  # noqa: F811
    headers, _ = admin
    films = await make_library(client, headers, settings, "movies", "Films")
    gezin, _ = await person(client, headers, "gezin@example.nl")

    r = await client.get(f"{BASE}/lookup", params={"kind": "movie", "q": "matrix"}, headers=gezin)
    assert r.status_code == 200 and r.json()[0]["status"] == "none"
    r = await client.post(
        BASE, json={"kind": "movie", "tmdb_id": 603, "note": "graag"}, headers=gezin
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["state"] == "pending" and req["title"]
    r = await client.post(BASE, json={"kind": "movie", "tmdb_id": 603}, headers=gezin)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "arr_request_exists"

    # Een gezinslid kan niet goedkeuren en ziet niet alles
    r = await client.post(f"{BASE}/{req['id']}/approve", json={}, headers=gezin)
    assert r.status_code == 403
    assert (await client.get(BASE, params={"scope": "all"}, headers=gezin)).status_code == 403

    r = await client.get(BASE, params={"scope": "all"}, headers=headers)
    assert [x["id"] for x in r.json()] == [req["id"]]
    r = await client.post(f"{BASE}/{req['id']}/approve", json={}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "approved"
    wanted = (await client.get("/api/v1/arr/wanted", headers=headers)).json()
    assert [(w["tmdb_id"], w["library_id"]) for w in wanted] == [(603, films)]

    r = await client.get(f"{BASE}/lookup", params={"kind": "movie", "q": "matrix"}, headers=gezin)
    assert r.json()[0]["status"] == "following"


async def test_auto_approve_and_withdraw(client, admin, settings, fake_tmdb):  # noqa: F811
    headers, _ = admin
    await make_library(client, headers, settings, "movies", "Films")
    gezin, _ = await person(client, headers, "gezin@example.nl")
    me = (await client.get("/api/v1/auth/me", headers=gezin)).json()

    r = await client.post(BASE, json={"kind": "movie", "tmdb_id": 603}, headers=gezin)
    req = r.json()
    r = await client.post(f"{BASE}/{req['id']}/decline", json={"reason": "te eng"}, headers=headers)
    assert r.json()["state"] == "declined" and r.json()["reason"] == "te eng"
    r = await client.delete(f"{BASE}/{req['id']}", headers=gezin)
    assert r.status_code == 404  # al beslist: alleen een beheerder haalt hem weg
    assert (await client.delete(f"{BASE}/{req['id']}", headers=headers)).status_code == 204

    r = await client.put("/api/v1/admin/arr/requests", json={"auto": [me["id"]]}, headers=headers)
    assert r.status_code == 200, r.text
    r = await client.post(BASE, json={"kind": "movie", "tmdb_id": 603}, headers=gezin)
    assert r.status_code == 201 and r.json()["state"] == "approved"
