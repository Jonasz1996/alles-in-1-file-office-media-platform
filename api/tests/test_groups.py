"""Groepen (beheer) en het adresboek om mee te delen."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_engine
from app.models import Group, User
from app.routers import shares
from tests.test_files import mkdir, put, root
from tests.test_shares import code, group_of, person, share_with


@pytest.fixture(autouse=True)
def fresh_limit():
    shares.directory_limiter.reset()
    yield
    shares.directory_limiter.reset()


@pytest.mark.anyio
async def test_groepen_beheren(client, admin):
    jonas, _ = admin
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    _, bert_id = await person(client, jonas, "bert@example.nl")

    r = await client.post("/api/v1/groups", json={"name": "  Ouders  "}, headers=jonas)
    assert r.status_code == 201, r.text
    group = r.json()
    assert group["name"] == "Ouders" and group["member_count"] == 0 and group["members"] == []
    # Hoofdletters en accenten tellen niet mee
    for name in ("ouders", "OUDERS", "Oüders"):
        r = await client.post("/api/v1/groups", json={"name": name}, headers=jonas)
        assert r.status_code == 409 and r.json()["detail"] == {
            "message": "Er bestaat al een groep met deze naam",
            "code": "name_taken",
        }
    for name in ("", "   ", "x" * 101):
        r = await client.post("/api/v1/groups", json={"name": name}, headers=jonas)
        assert r.status_code == 422, name
    assert (
        await client.post("/api/v1/groups", json={"name": "x" * 100}, headers=jonas)
    ).status_code == 201
    kids = (await client.post("/api/v1/groups", json={"name": "Kinderen"}, headers=jonas)).json()

    # Leden vervangen
    r = await client.put(
        f"/api/v1/groups/{group['id']}/members",
        json={"user_ids": [bert_id, anna_id, anna_id]},
        headers=jonas,
    )
    assert r.status_code == 200, r.text
    assert r.json()["member_count"] == 2
    assert [m["display_name"] for m in r.json()["members"]] == ["Anna", "Bert"]
    assert r.json()["members"][0] == {
        "id": anna_id,
        "display_name": "Anna",
        "email": "anna@example.nl",
    }
    r = await client.put(
        f"/api/v1/groups/{group['id']}/members", json={"user_ids": [bert_id]}, headers=jonas
    )
    assert [m["id"] for m in r.json()["members"]] == [bert_id]
    r = await client.put(
        f"/api/v1/groups/{group['id']}/members",
        json={"user_ids": [anna_id, str(uuid.uuid4())]},
        headers=jonas,
    )
    assert r.status_code == 422 and code(r) == "unknown_user"
    # Niets veranderd
    listed = (await client.get("/api/v1/groups", headers=jonas)).json()
    assert [g["name"] for g in listed] == ["Kinderen", "Ouders", "x" * 100]
    assert {g["name"]: g["member_count"] for g in listed}["Ouders"] == 1

    # Hernoemen: naar een bezette naam niet, naar dezelfde naam in andere letters wel
    r = await client.patch(
        f"/api/v1/groups/{group['id']}", json={"name": "kinderen"}, headers=jonas
    )
    assert r.status_code == 409 and code(r) == "name_taken"
    r = await client.patch(f"/api/v1/groups/{group['id']}", json={"name": "OUDERS"}, headers=jonas)
    assert r.status_code == 200 and r.json()["name"] == "OUDERS"
    assert r.json()["member_count"] == 1
    r = await client.patch(f"/api/v1/groups/{group['id']}", json={"name": " "}, headers=jonas)
    assert r.status_code == 422

    # Weg
    assert (await client.delete(f"/api/v1/groups/{kids['id']}", headers=jonas)).status_code == 204
    assert (await client.delete(f"/api/v1/groups/{kids['id']}", headers=jonas)).status_code == 404
    missing = str(uuid.uuid4())
    assert (
        await client.patch(f"/api/v1/groups/{missing}", json={"name": "y"}, headers=jonas)
    ).status_code == 404
    assert (
        await client.put(f"/api/v1/groups/{missing}/members", json={"user_ids": []}, headers=jonas)
    ).status_code == 404

    # Alleen voor beheerders
    for r in [
        await client.get("/api/v1/groups", headers=anna),
        await client.post("/api/v1/groups", json={"name": "Van Anna"}, headers=anna),
        await client.patch(f"/api/v1/groups/{group['id']}", json={"name": "y"}, headers=anna),
        await client.delete(f"/api/v1/groups/{group['id']}", headers=anna),
        await client.put(
            f"/api/v1/groups/{group['id']}/members", json={"user_ids": [anna_id]}, headers=anna
        ),
    ]:
        assert r.status_code == 403
    assert (await client.get("/api/v1/groups")).status_code == 401


@pytest.mark.anyio
async def test_lidmaatschap_telt_meteen(client, admin):
    jonas, _ = admin
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    bert, bert_id = await person(client, jonas, "bert@example.nl")
    root_id = (await root(client, jonas))["folder"]["id"]
    team = await mkdir(client, jonas, root_id, "Team")
    f = (await put(client, jonas, team, "plan.txt", b"plan")).json()["id"]
    group = await group_of(client, jonas, "Team", anna_id)
    await share_with(client, jonas, team, group_id=group, permission="write")
    # Anna heeft ook een eigen deling om te lezen: die blijft als de groep weggaat
    await share_with(client, jonas, f, user_id=anna_id, permission="read")

    assert (await client.get(f"/api/v1/files/{team}", headers=anna)).json()["permission"] == "write"
    assert (await client.get(f"/api/v1/files/{team}", headers=bert)).status_code == 404
    # Bert erbij, Anna eruit: bij het volgende verzoek al
    await client.put(f"/api/v1/groups/{group}/members", json={"user_ids": [bert_id]}, headers=jonas)
    assert (await client.get(f"/api/v1/files/{team}", headers=bert)).json()["permission"] == "write"
    assert (await client.get(f"/api/v1/files/{team}", headers=anna)).status_code == 404
    assert (await client.get(f"/api/v1/files/{f}", headers=anna)).json()["permission"] == "read"
    r = await client.get("/api/v1/shared-with-me", headers=bert)
    [item] = r.json()
    assert item["via"] == "group" and item["group"]["name"] == "Team"
    assert item["node"]["id"] == team
    # De groep weg: de deling gaat mee
    assert (await client.delete(f"/api/v1/groups/{group}", headers=jonas)).status_code == 204
    assert (await client.get(f"/api/v1/files/{team}", headers=bert)).status_code == 404
    assert (await client.get("/api/v1/shared-with-me", headers=bert)).json() == []
    left = (await client.get(f"/api/v1/files/{team}/shares", headers=jonas)).json()
    assert left == []
    left = (await client.get(f"/api/v1/files/{f}/shares", headers=jonas)).json()
    assert [s["type"] for s in left] == ["user"]
    assert (await client.get(f"/api/v1/files/{f}", headers=anna)).status_code == 200
    # Een groep die weg is, kun je niet meer mee delen
    r = await client.post(
        f"/api/v1/files/{team}/shares",
        json={"group_id": group, "permission": "read"},
        headers=jonas,
    )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_adresboek(client, admin):
    jonas, tokens = admin
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    await person(client, jonas, "bert@voorbeeld.be")
    gast, _ = await person(client, jonas, "gast@example.nl", role="guest")
    r = await client.post(
        "/api/v1/users",
        json={
            "email": "zoe@example.nl",
            "display_name": "Zoë Ångström",
            "password": "lang-genoeg-123",
        },
        headers=jonas,
    )
    zoe_id = r.json()["id"]
    team = await group_of(client, jonas, "Éénhoorns", anna_id, zoe_id)
    await group_of(client, jonas, "Leeg")

    async def search(headers, q, expect=200):
        r = await client.get("/api/v1/directory", params={"q": q}, headers=headers)
        assert r.status_code == expect, r.text
        return r.json()

    found = await search(anna, "zoe")
    assert found == {
        "users": [{"id": zoe_id, "display_name": "Zoë Ångström", "email": "zoe@example.nl"}],
        "groups": [],
    }
    assert [u["display_name"] for u in (await search(anna, "ANGSTR"))["users"]] == ["Zoë Ångström"]
    # Op e-mailadres, nooit jezelf
    assert [u["display_name"] for u in (await search(anna, "example.nl"))["users"]] == [
        "Gast",
        "Jonas",
        "Zoë Ångström",
    ]
    assert [u["display_name"] for u in (await search(anna, "voorbeeld"))["users"]] == ["Bert"]
    assert (await search(anna, "anna"))["users"] == []
    # Groepen, met het aantal leden
    assert (await search(anna, "eenhoorn"))["groups"] == [
        {"id": team, "name": "Éénhoorns", "member_count": 2}
    ]
    assert (await search(jonas, "leeg"))["groups"][0]["member_count"] == 0
    assert await search(anna, "   ") == {"users": [], "groups": []}
    await search(anna, "", expect=422)
    await search(anna, "x" * 101, expect=422)
    # Gasten niet
    r = await client.get("/api/v1/directory", params={"q": "a"}, headers=gast)
    assert r.status_code == 403 and code(r) == "guest"
    assert (await client.get("/api/v1/directory", params={"q": "a"})).status_code == 401


@pytest.mark.anyio
async def test_adresboek_hooguit_tien(client, admin, settings):
    jonas, _ = admin
    # Rechtstreeks in de database: 12 gebruikers en 12 groepen (zonder de inloglimiet)
    async with async_sessionmaker(get_engine(settings))() as session:
        session.add_all(
            User(email=f"lid{i:02}@example.nl", display_name=f"Lid {i:02}", password_hash="x")
            for i in range(12)
        )
        session.add_all(Group(name=f"Lidgroep {i:02}") for i in range(12))
        await session.commit()
    found = (await client.get("/api/v1/directory", params={"q": "lid"}, headers=jonas)).json()
    assert [u["display_name"] for u in found["users"]] == [f"Lid {i:02}" for i in range(10)]
    assert [g["name"] for g in found["groups"]] == [f"Lidgroep {i:02}" for i in range(10)]


@pytest.mark.anyio
async def test_adresboek_niet_leeg_te_halen(client, admin):
    jonas, _ = admin
    anna, _ = await person(client, jonas, "anna@example.nl")
    for _ in range(60):
        r = await client.get("/api/v1/directory", params={"q": "jo"}, headers=anna)
        assert r.status_code == 200
    r = await client.get("/api/v1/directory", params={"q": "jo"}, headers=anna)
    assert r.status_code == 429 and code(r) == "rate_limited"
    assert int(r.headers["retry-after"]) > 0
    # Per gebruiker
    r = await client.get("/api/v1/directory", params={"q": "an"}, headers=jonas)
    assert r.status_code == 200
