"""Delen met gebruikers en groepen: wie wat mag, en dat niemand iets ziet van wat erboven of
ernaast ligt."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_engine
from app.models import Share
from tests.factories import message_of
from tests.test_files import check_refs, mkdir, put, root
from tests.test_uploads import TUS, create, send


async def person(client, admin_headers, email, role="user", quota_bytes=None):
    """Een nieuwe gebruiker: (headers, id)."""
    body = {
        "email": email,
        "display_name": email.split("@")[0].capitalize(),
        "password": "lang-genoeg-123",
        "role": role,
    }
    if quota_bytes is not None:
        body["quota_bytes"] = quota_bytes
    r = await client.post("/api/v1/users", json=body, headers=admin_headers)
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": body["password"]})
    tokens = r.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, tokens["user"]["id"]


async def share_with(client, headers, node_id, expect=201, **body):
    r = await client.post(f"/api/v1/files/{node_id}/shares", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


async def group_of(client, admin_headers, name, *user_ids) -> str:
    r = await client.post("/api/v1/groups", json={"name": name}, headers=admin_headers)
    assert r.status_code == 201, r.text
    group = r.json()["id"]
    r = await client.put(
        f"/api/v1/groups/{group}/members", json={"user_ids": list(user_ids)}, headers=admin_headers
    )
    assert r.status_code == 200, r.text
    return group


async def get(client, headers, url, expect=200, **params):
    r = await client.get(url, params=params, headers=headers)
    assert r.status_code == expect, (url, r.text)
    return r.json()


def code(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


async def expire(settings, share_id):
    """De deling is verlopen (een minuut geleden)."""
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(
            update(Share)
            .where(Share.id == uuid.UUID(share_id))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()


@pytest.fixture
async def setup(client, admin):
    """Jonas (beheerder) met /Werk/Gedeeld/{Sub/diep.txt, a.txt} en /Werk/geheim.txt, en Anna."""
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    werk = await mkdir(client, jonas, root_id, "Werk")
    gedeeld = await mkdir(client, jonas, werk, "Gedeeld")
    sub = await mkdir(client, jonas, gedeeld, "Sub")
    diep = (await put(client, jonas, sub, "diep.txt", b"diep")).json()["id"]
    a = (await put(client, jonas, gedeeld, "a.txt", b"aaa")).json()["id"]
    geheim = (await put(client, jonas, werk, "geheim.txt", b"geheim")).json()["id"]
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    return {
        "jonas": jonas,
        "jonas_id": tokens["user"]["id"],
        "root": root_id,
        "werk": werk,
        "gedeeld": gedeeld,
        "sub": sub,
        "diep": diep,
        "a": a,
        "geheim": geheim,
        "anna": anna,
        "anna_id": anna_id,
    }


@pytest.mark.anyio
async def test_lezen_via_een_deling(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    out = await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="read")
    assert out["type"] == "user" and out["permission"] == "read"
    assert out["user"] == {"id": s["anna_id"], "display_name": "Anna", "email": "anna@example.nl"}
    assert out["group"] is None and out["link"] is None and out["expires_at"] is None

    # De gedeelde map zelf: geen parent_id (de map erboven is niet van haar)
    info = await get(client, anna, f"/api/v1/files/{s['gedeeld']}")
    assert info["parent_id"] is None
    assert info["permission"] == "read"
    assert info["owner"] == {"id": s["jonas_id"], "display_name": "Jonas"}
    assert info["shared"] is False
    listing = await get(client, anna, f"/api/v1/files/{s['gedeeld']}/children")
    assert [p["name"] for p in listing["path"]] == ["Gedeeld"]
    assert listing["path"][0]["parent_id"] is None and listing["folder"]["parent_id"] is None
    assert [c["name"] for c in listing["children"]] == ["Sub", "a.txt"]
    assert {c["permission"] for c in listing["children"]} == {"read"}
    assert {c["parent_id"] for c in listing["children"]} == {s["gedeeld"]}
    listing = await get(client, anna, f"/api/v1/files/{s['sub']}/children")
    assert [(p["name"], p["parent_id"]) for p in listing["path"]] == [
        ("Gedeeld", None),
        ("Sub", s["gedeeld"]),
    ]
    assert listing["children"][0]["owner"]["display_name"] == "Jonas"

    # Lezen en downloaden mag
    r = await client.get(f"/api/v1/files/{s['diep']}/content", headers=anna)
    assert r.content == b"diep"
    link = (await client.post(f"/api/v1/files/{s['a']}/download-link", headers=anna)).json()
    assert (await client.get(link["url"])).content == b"aaa"
    assert await get(client, anna, f"/api/v1/files/{s['a']}/versions") == []

    # Wat erboven of ernaast ligt, bestaat voor haar niet
    for node in (s["werk"], s["root"], s["geheim"]):
        await get(client, anna, f"/api/v1/files/{node}", expect=404)
        r = await client.get(f"/api/v1/files/{node}/content", headers=anna)
        assert r.status_code == 404
    await get(client, anna, f"/api/v1/files/{s['werk']}/children", expect=404)

    # De eigenaar ziet dat het gedeeld is; wie het gedeeld kreeg niet
    mine = await get(client, jonas, f"/api/v1/files/{s['werk']}/children")
    assert {c["name"]: c["shared"] for c in mine["children"]} == {
        "Gedeeld": True,
        "geheim.txt": False,
    }
    assert mine["folder"]["permission"] == "owner"


@pytest.mark.anyio
async def test_alleen_kijken_betekent_niets_veranderen(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="read")
    anna_root = (await root(client, anna))["folder"]["id"]
    eigen = (await put(client, anna, anna_root, "eigen.txt", b"eigen")).json()["id"]
    attempts = [
        ("POST", f"/api/v1/files/{s['gedeeld']}/folders", {"json": {"name": "N"}}),
        ("PUT", f"/api/v1/files/{s['gedeeld']}/upload?name=x", {"content": b"x"}),
        ("PATCH", f"/api/v1/files/{s['a']}", {"json": {"name": "b.txt"}}),
        ("PATCH", f"/api/v1/files/{s['diep']}", {"json": {"parent_id": s["gedeeld"]}}),
        ("DELETE", f"/api/v1/files/{s['a']}", {}),
        ("POST", f"/api/v1/files/{eigen}/copy", {"json": {"parent_id": s["gedeeld"]}}),
    ]
    for method, url, extra in attempts:
        r = await client.request(method, url, headers=anna, **extra)
        assert r.status_code == 403, (method, url, r.text)
        assert r.json()["detail"] == {"message": "Je mag hier alleen kijken", "code": "read_only"}
    r = await create(client, anna, s["gedeeld"], "t.txt", 1)
    assert r.status_code == 403 and code(r) == "read_only"
    # Alleen voor de eigenaar
    for method, url in [
        ("GET", f"/api/v1/files/{s['gedeeld']}/shares"),
        ("POST", f"/api/v1/files/{s['sub']}/shares"),
        ("POST", f"/api/v1/files/{s['sub']}/links"),
        ("POST", f"/api/v1/files/{s['a']}/restore"),
        ("DELETE", f"/api/v1/trash/{s['a']}"),
    ]:
        body = (
            {"json": {"user_id": s["jonas_id"], "permission": "read"}} if "POST" in method else {}
        )
        r = await client.request(method, url, headers=anna, **body)
        assert r.status_code in (403, 422), (method, url, r.text)
        if r.status_code == 403:
            assert code(r) == "owner_only"
    # Kopiëren naar je eigen map mag wel: de kopie is van jou
    r = await client.post(
        f"/api/v1/files/{s['gedeeld']}/copy", json={"parent_id": anna_root}, headers=anna
    )
    assert r.status_code == 201, r.text
    assert r.json()["permission"] == "owner" and r.json()["owner"]["id"] == s["anna_id"]
    kopie = await get(client, anna, f"/api/v1/files/{r.json()['id']}/children")
    assert sorted(c["name"] for c in kopie["children"]) == ["Sub", "a.txt"]
    assert (await get(client, anna, "/api/v1/auth/me"))["used_bytes"] == 5 + 3 + 4
    # Niets veranderd bij Jonas
    listing = await get(client, jonas, f"/api/v1/files/{s['gedeeld']}/children")
    assert [c["name"] for c in listing["children"]] == ["Sub", "a.txt"]


@pytest.mark.anyio
async def test_schrijven_in_een_gedeelde_map(client, setup, settings):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="write")
    before = (await get(client, jonas, "/api/v1/auth/me"))["used_bytes"]

    # Wat zij maakt, is van Jonas en kost zijn quotum
    r = await client.post(
        f"/api/v1/files/{s['gedeeld']}/folders", json={"name": "Van Anna"}, headers=anna
    )
    assert r.status_code == 201, r.text
    nieuw = r.json()
    assert nieuw["owner"]["id"] == s["jonas_id"] and nieuw["permission"] == "write"
    assert nieuw["parent_id"] == s["gedeeld"]
    r = await put(client, anna, nieuw["id"], "foto.jpg", b"\xff\xd8\xffjpeg")
    assert r.status_code == 201, r.text
    foto = r.json()
    assert foto["owner"]["id"] == s["jonas_id"] and foto["permission"] == "write"
    assert (await get(client, jonas, "/api/v1/auth/me"))["used_bytes"] == before + 7
    assert (await get(client, anna, "/api/v1/auth/me"))["used_bytes"] == 0
    # Hervatbaar uploaden ook
    location = (await create(client, anna, s["sub"], "tus.txt", 3)).headers["location"]
    r = await send(client, anna, location, 0, b"tus")
    assert r.status_code == 204, r.text
    tus_node = r.headers["platform-node-id"]
    assert (await get(client, jonas, f"/api/v1/files/{tus_node}"))["name"] == "tus.txt"
    assert (await get(client, jonas, "/api/v1/auth/me"))["used_bytes"] == before + 10

    # Vervangen: de vorige inhoud blijft als versie, vervangen door Anna
    r = await put(client, anna, s["gedeeld"], "a.txt", b"nieuw", conflict="replace")
    assert r.status_code == 201
    [old] = await get(client, jonas, f"/api/v1/files/{s['a']}/versions")
    assert old["replaced_by"] == {"id": s["anna_id"], "display_name": "Anna"}
    # Terugzetten mag ze (schrijven), weggooien van een versie niet (eigenaar)
    r = await client.post(f"/api/v1/files/{s['a']}/versions/{old['id']}/restore", headers=anna)
    assert r.status_code == 200
    [old] = await get(client, anna, f"/api/v1/files/{s['a']}/versions")
    r = await client.delete(f"/api/v1/files/{s['a']}/versions/{old['id']}", headers=anna)
    assert r.status_code == 403 and code(r) == "owner_only"

    # Hernoemen en verplaatsen binnen de gedeelde map
    r = await client.patch(
        f"/api/v1/files/{s['diep']}",
        json={"parent_id": nieuw["id"], "name": "verplaatst.txt"},
        headers=anna,
    )
    assert r.status_code == 200, r.text
    assert r.json()["parent_id"] == nieuw["id"]
    # Niet naar haar eigen map (andere eigenaar) en niet erbuiten
    anna_root = (await root(client, anna))["folder"]["id"]
    r = await client.patch(
        f"/api/v1/files/{s['diep']}", json={"parent_id": anna_root}, headers=anna
    )
    assert r.status_code == 422 and code(r) == "cross_owner"
    r = await client.patch(
        f"/api/v1/files/{s['diep']}", json={"parent_id": s["werk"]}, headers=anna
    )
    assert r.status_code == 404
    # Ook de eigenaar verplaatst niet naar een map van een ander
    jonas_file = (await put(client, jonas, s["werk"], "los.txt", b"l")).json()["id"]
    await share_with(client, anna, anna_root, expect=422, user_id=s["jonas_id"], permission="read")
    anna_map = await mkdir(client, anna, anna_root, "Voor Jonas")
    await share_with(client, anna, anna_map, user_id=s["jonas_id"], permission="write")
    r = await client.patch(
        f"/api/v1/files/{jonas_file}", json={"parent_id": anna_map}, headers=jonas
    )
    assert r.status_code == 422 and code(r) == "cross_owner"

    # De gedeelde map zelf: niet hernoemen, verplaatsen of weggooien
    for method, extra in [
        ("PATCH", {"json": {"name": "Anders"}}),
        ("PATCH", {"json": {"parent_id": nieuw["id"]}}),
        ("DELETE", {}),
    ]:
        r = await client.request(method, f"/api/v1/files/{s['gedeeld']}", headers=anna, **extra)
        assert r.status_code == 403 and code(r) == "share_root", r.text
        assert r.json()["detail"]["message"].startswith("Dit is de gedeelde map zelf;")

    # Weggooien: naar de prullenbak van Jonas; Anna ziet het niet meer
    assert (await client.delete(f"/api/v1/files/{s['sub']}", headers=anna)).status_code == 204
    await get(client, anna, f"/api/v1/files/{s['sub']}", expect=404)
    await get(client, anna, f"/api/v1/files/{tus_node}", expect=404)
    assert await get(client, anna, "/api/v1/trash") == []
    trash = await get(client, jonas, "/api/v1/trash")
    assert [t["name"] for t in trash] == ["Sub"]
    assert trash[0]["permission"] == "owner"
    r = await client.post(f"/api/v1/files/{s['sub']}/restore", headers=jonas)
    assert r.status_code == 200
    assert (await get(client, anna, f"/api/v1/files/{s['sub']}"))["parent_id"] == s["gedeeld"]

    # Kopiëren naar de gedeelde map: van Jonas, op zijn quotum
    eigen = (await put(client, anna, anna_root, "eigen.txt", b"123456")).json()["id"]
    before = (await get(client, jonas, "/api/v1/auth/me"))["used_bytes"]
    r = await client.post(
        f"/api/v1/files/{eigen}/copy", json={"parent_id": s["gedeeld"]}, headers=anna
    )
    assert r.status_code == 201, r.text
    assert r.json()["owner"]["id"] == s["jonas_id"] and r.json()["permission"] == "write"
    assert (await get(client, jonas, "/api/v1/auth/me"))["used_bytes"] == before + 6
    await check_refs(settings)


@pytest.mark.anyio
async def test_quotum_van_de_eigenaar(client, admin, settings):
    jonas, _ = admin
    klein, klein_id = await person(client, jonas, "klein@example.nl", quota_bytes=10)
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    kroot = (await root(client, klein))["folder"]["id"]
    folder = await mkdir(client, klein, kroot, "Samen")
    await share_with(client, klein, folder, user_id=anna_id, permission="write")
    assert (await put(client, anna, folder, "a", b"12345678")).status_code == 201
    # Anna zelf heeft geen quotum, maar het is de ruimte van Klein (en dat zegt de melding)
    full = "Er is niet genoeg ruimte meer bij Klein; vraag hem of haar om ruimte te maken"
    r = await put(client, anna, folder, "b", b"12345")
    assert r.status_code == 413 and message_of(r) == full
    r = await create(client, anna, folder, "c", 5)
    assert r.status_code == 413 and message_of(r) == full
    anna_root = (await root(client, anna))["folder"]["id"]
    eigen = (await put(client, anna, anna_root, "x", b"12345")).json()["id"]
    r = await client.post(f"/api/v1/files/{eigen}/copy", json={"parent_id": folder}, headers=anna)
    assert r.status_code == 413 and message_of(r) == full
    assert (await get(client, klein, "/api/v1/auth/me"))["used_bytes"] == 8
    # Een upload van Anna reserveert ruimte bij Klein
    assert (await create(client, anna, folder, "d", 2)).status_code == 201
    r = await put(client, klein, kroot, "e", b"12")
    assert r.status_code == 413 and message_of(r) == "Onvoldoende quotum"
    await check_refs(settings)


@pytest.mark.anyio
async def test_geen_toegang_zonder_geldige_deling(client, setup, settings):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    bert, bert_id = await person(client, jonas, "bert@example.nl")
    share = await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="write")
    urls = [
        f"/api/v1/files/{s['gedeeld']}",
        f"/api/v1/files/{s['gedeeld']}/children",
        f"/api/v1/files/{s['a']}",
        f"/api/v1/files/{s['a']}/content",
        f"/api/v1/files/{s['a']}/versions",
    ]
    writes = [
        ("POST", f"/api/v1/files/{s['gedeeld']}/folders", {"json": {"name": "N"}}),
        ("PUT", f"/api/v1/files/{s['gedeeld']}/upload?name=x", {"content": b"x"}),
        ("PATCH", f"/api/v1/files/{s['a']}", {"json": {"name": "b.txt"}}),
        ("DELETE", f"/api/v1/files/{s['a']}", {}),
        ("POST", f"/api/v1/files/{s['a']}/download-link", {}),
        ("PUT", f"/api/v1/files/{s['a']}/tags", {"json": {"tag_ids": []}}),
    ]

    async def no_access(headers):
        for url in urls:
            assert (await client.get(url, headers=headers)).status_code == 404, url
        for method, url, extra in writes:
            r = await client.request(method, url, headers=headers, **extra)
            assert r.status_code == 404, (method, url, r.text)
        assert (await create(client, headers, s["gedeeld"], "t", 1)).status_code == 404
        r = await client.post("/api/v1/previews", json={"node_ids": [s["a"]]}, headers=headers)
        assert r.json() == {}
        r = await client.get("/api/v1/search", params={"q": "a"}, headers=headers)
        assert r.json()["results"] == []
        assert (await client.get("/api/v1/shared-with-me", headers=headers)).json() == []

    # Een ander met wie niets gedeeld is
    await no_access(bert)
    # Anna kan erbij...
    assert (await client.get(urls[0], headers=anna)).status_code == 200
    link = (await client.post(f"/api/v1/files/{s['a']}/download-link", headers=anna)).json()

    # ...tot de deling verloopt
    await expire(settings, share["id"])
    await no_access(anna)
    assert (await client.get(link["url"])).status_code == 404
    r = await client.patch(
        f"/api/v1/shares/{share['id']}", json={"expires_at": None}, headers=jonas
    )
    assert r.status_code == 200 and r.json()["expires_at"] is None
    assert (await client.get(urls[0], headers=anna)).status_code == 200

    # ...of de gedeelde map (of een map erboven) in de prullenbak ligt
    await client.delete(f"/api/v1/files/{s['werk']}", headers=jonas)
    await no_access(anna)
    await client.post(f"/api/v1/files/{s['werk']}/restore", headers=jonas)
    await client.delete(f"/api/v1/files/{s['gedeeld']}", headers=jonas)
    await no_access(anna)
    await client.post(f"/api/v1/files/{s['gedeeld']}/restore", headers=jonas)
    assert (await client.get(urls[0], headers=anna)).status_code == 200

    # ...of de deling wordt ingetrokken: ook een downloadlink die al uit was werkt niet meer
    link = (await client.post(f"/api/v1/files/{s['a']}/download-link", headers=anna)).json()
    assert (await client.get(link["url"])).status_code == 200
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=anna)
    assert r.status_code == 404  # niet de hare
    assert (await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)).status_code == 204
    await no_access(anna)
    assert (await client.get(link["url"])).status_code == 404


@pytest.mark.anyio
async def test_hoogste_recht_telt(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    group = await group_of(client, jonas, "Team", s["anna_id"])
    await share_with(client, jonas, s["gedeeld"], group_id=group, permission="read")
    await share_with(client, jonas, s["sub"], user_id=s["anna_id"], permission="write")
    listing = await get(client, anna, f"/api/v1/files/{s['gedeeld']}/children")
    assert listing["folder"]["permission"] == "read"
    assert {c["name"]: c["permission"] for c in listing["children"]} == {
        "Sub": "write",
        "a.txt": "read",
    }
    listing = await get(client, anna, f"/api/v1/files/{s['sub']}/children")
    assert [(p["name"], p["permission"]) for p in listing["path"]] == [
        ("Gedeeld", "read"),
        ("Sub", "write"),
    ]
    assert listing["children"][0]["permission"] == "write"
    assert (await put(client, anna, s["sub"], "x.txt", b"x")).status_code == 201
    assert (await put(client, anna, s["gedeeld"], "x.txt", b"x")).status_code == 403
    # Sub draagt zelf een deling voor haar: niet weg te gooien of te hernoemen (en voor haar
    # is het niet "de gedeelde map", dat is Gedeeld)
    r = await client.delete(f"/api/v1/files/{s['sub']}", headers=anna)
    assert r.status_code == 403 and code(r) == "share_root"
    assert r.json()["detail"]["message"] == (
        "Dit is zelf met je gedeeld; alleen de eigenaar kan het hernoemen, verplaatsen of "
        "verwijderen"
    )
    # Wat in Sub staat wel
    assert (await client.delete(f"/api/v1/files/{s['diep']}", headers=anna)).status_code == 204

    # Eén regel per map in Gedeeld met mij: alleen de bovenste
    items = await get(client, anna, "/api/v1/shared-with-me")
    assert [(i["node"]["name"], i["permission"], i["via"]) for i in items] == [
        ("Gedeeld", "read", "group")
    ]
    assert items[0]["group"] == {"id": group, "name": "Team", "member_count": 1}

    # Uit de groep: meteen geen toegang meer tot Gedeeld, Sub blijft (eigen deling)
    r = await client.put(f"/api/v1/groups/{group}/members", json={"user_ids": []}, headers=jonas)
    assert r.status_code == 200
    await get(client, anna, f"/api/v1/files/{s['gedeeld']}", expect=404)
    sub = await get(client, anna, f"/api/v1/files/{s['sub']}")
    assert sub["parent_id"] is None and sub["permission"] == "write"
    listing = await get(client, anna, f"/api/v1/files/{s['sub']}/children")
    assert [p["name"] for p in listing["path"]] == ["Sub"]
    items = await get(client, anna, "/api/v1/shared-with-me")
    assert [(i["node"]["name"], i["via"]) for i in items] == [("Sub", "user")]


@pytest.mark.anyio
async def test_gedeelde_map_in_de_prullenbak_breekt_de_lijst_erboven_niet(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    bram, bram_id = await person(client, jonas, "bram@example.nl")
    group = await group_of(client, jonas, "Team", s["anna_id"])
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="read")
    await share_with(client, jonas, s["gedeeld"], user_id=bram_id, permission="write")
    # Sub draagt zelf een deling voor Anna, rechtstreeks en via een groep
    await share_with(client, jonas, s["sub"], user_id=s["anna_id"], permission="write")
    await share_with(client, jonas, s["sub"], group_id=group, permission="write")
    # Bram mag Sub weggooien: voor hem is het niet de gedeelde map
    assert (await client.delete(f"/api/v1/files/{s['sub']}", headers=bram)).status_code == 204
    listing = await get(client, anna, f"/api/v1/files/{s['gedeeld']}/children")
    assert [(c["name"], c["permission"]) for c in listing["children"]] == [("a.txt", "read")]
    await get(client, anna, f"/api/v1/files/{s['sub']}/children", expect=404)
    # Ook als de eigenaar het weggooit en terugzet
    assert (
        await client.post(f"/api/v1/files/{s['sub']}/restore", headers=jonas)
    ).status_code == 200
    listing = await get(client, anna, f"/api/v1/files/{s['gedeeld']}/children")
    assert {c["name"]: c["permission"] for c in listing["children"]} == {
        "Sub": "write",
        "a.txt": "read",
    }
    assert (await client.delete(f"/api/v1/files/{s['sub']}", headers=jonas)).status_code == 204
    await get(client, anna, f"/api/v1/files/{s['gedeeld']}/children")


@pytest.mark.anyio
async def test_gedeeld_met_mij(client, setup, settings):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    bert, bert_id = await person(client, jonas, "bert@example.nl")
    bert_root = (await root(client, bert))["folder"]["id"]
    van_bert = await mkdir(client, bert, bert_root, "Álbum")
    bestand = (await put(client, bert, bert_root, "zebra.pdf", b"%PDF-1.4")).json()["id"]
    verlopen = await mkdir(client, bert, bert_root, "Verlopen")
    weg = await mkdir(client, bert, bert_root, "Weg")
    await share_with(client, bert, van_bert, user_id=s["anna_id"], permission="read")
    await share_with(client, bert, bestand, user_id=s["anna_id"], permission="write")
    old = await share_with(client, bert, verlopen, user_id=s["anna_id"], permission="read")
    await expire(settings, old["id"])
    await share_with(client, bert, weg, user_id=s["anna_id"], permission="read")
    await client.delete(f"/api/v1/files/{weg}", headers=bert)
    # Via de gebruiker en via een groep: één regel, met het hoogste recht
    group = await group_of(client, jonas, "Allen", s["anna_id"], s["jonas_id"])
    await share_with(client, jonas, s["gedeeld"], group_id=group, permission="write")
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="read")
    await share_with(client, jonas, s["sub"], user_id=s["anna_id"], permission="read")

    items = await get(client, anna, "/api/v1/shared-with-me")
    assert [i["node"]["name"] for i in items] == ["Álbum", "Gedeeld", "zebra.pdf"]
    by_name = {i["node"]["name"]: i for i in items}
    assert by_name["Gedeeld"]["permission"] == "write"
    assert by_name["Gedeeld"]["via"] == "group"
    assert by_name["Gedeeld"]["owner"] == {"id": s["jonas_id"], "display_name": "Jonas"}
    assert by_name["Gedeeld"]["node"]["parent_id"] is None
    assert by_name["Gedeeld"]["node"]["permission"] == "write"
    assert by_name["zebra.pdf"]["node"]["kind"] == "file"
    assert by_name["zebra.pdf"]["owner"]["display_name"] == "Bert"
    assert by_name["Álbum"]["via"] == "user" and by_name["Álbum"]["group"] is None
    # De eigenaar zit zelf in de groep: zijn eigen map staat niet bij hem in de lijst
    assert await get(client, jonas, "/api/v1/shared-with-me") == []
    # Een gedeeld bestand is te openen
    assert (await client.get(f"/api/v1/files/{bestand}/content", headers=anna)).status_code == 200


@pytest.mark.anyio
async def test_fouten_bij_delen(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]

    async def attempt(node, expect, error_code=None, headers=jonas, **body):
        r = await client.post(f"/api/v1/files/{node}/shares", json=body, headers=headers)
        assert r.status_code == expect, r.text
        if error_code:
            assert code(r) == error_code, r.text
        return r

    await attempt(s["root"], 422, "root", user_id=s["anna_id"], permission="read")
    await attempt(s["werk"], 422, "self", user_id=s["jonas_id"], permission="read")
    await attempt(s["werk"], 404, user_id=str(uuid.uuid4()), permission="read")
    await attempt(s["werk"], 404, group_id=str(uuid.uuid4()), permission="read")
    await attempt(s["werk"], 422, permission="read")
    group = await group_of(client, jonas, "G")
    await attempt(s["werk"], 422, user_id=s["anna_id"], group_id=group, permission="read")
    await attempt(s["werk"], 422, user_id=s["anna_id"], permission="upload")
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await attempt(
        s["werk"], 422, "expires_past", user_id=s["anna_id"], permission="read", expires_at=past
    )
    # Van een ander: niet te vinden
    await attempt(s["werk"], 404, headers=anna, user_id=s["jonas_id"], permission="read")
    later = datetime.now(UTC) + timedelta(days=1)
    r = await attempt(
        s["werk"], 201, user_id=s["anna_id"], permission="read", expires_at=later.isoformat()
    )
    share = r.json()
    assert datetime.fromisoformat(share["expires_at"]) == later
    await attempt(s["werk"], 409, "already_shared", user_id=s["anna_id"], permission="write")
    await attempt(s["werk"], 201, group_id=group, permission="read")
    await attempt(s["werk"], 409, "already_shared", group_id=group, permission="write")
    # Een tijd met een andere tijdzone komt in UTC terug
    amsterdam = (datetime.now(UTC) + timedelta(days=2)).astimezone(timezone(timedelta(hours=2)))
    r = await client.patch(
        f"/api/v1/shares/{share['id']}", json={"expires_at": amsterdam.isoformat()}, headers=jonas
    )
    assert datetime.fromisoformat(r.json()["expires_at"]) == amsterdam
    # In de prullenbak
    await client.delete(f"/api/v1/files/{s['sub']}", headers=jonas)
    await attempt(s["sub"], 422, "trashed", user_id=s["anna_id"], permission="read")
    await attempt(s["diep"], 422, "trashed", user_id=s["anna_id"], permission="read")

    # Aanpassen: alleen wat bij het soort deling past, alleen door de eigenaar
    url = f"/api/v1/shares/{share['id']}"
    for body, expect in [
        ({"permission": "upload"}, 422),
        ({"password": "geheim123"}, 422),
        ({"label": "x"}, 422),
        ({"expires_at": past}, 422),
    ]:
        assert (await client.patch(url, json=body, headers=jonas)).status_code == expect, body
    assert (await client.patch(url, json={"permission": "write"}, headers=anna)).status_code == 404
    r = await client.patch(url, json={"permission": "write"}, headers=jonas)
    assert r.json()["permission"] == "write"
    assert r.json()["expires_at"] is not None  # weglaten laat hem staan
    shares = await get(client, jonas, f"/api/v1/files/{s['werk']}/shares")
    assert [(x["type"], x["permission"]) for x in shares] == [("user", "write"), ("group", "read")]
    assert (await client.delete(f"/api/v1/shares/{uuid.uuid4()}", headers=jonas)).status_code == 404


@pytest.mark.anyio
async def test_gasten(client, setup):
    s = setup
    jonas = s["jonas"]
    gast, gast_id = await person(client, jonas, "gast@example.nl", role="guest")
    gast_root = (await get(client, jonas, "/api/v1/users"))[-1]["root_id"]
    guest = {
        "message": "Gasten hebben geen eigen bestanden; je ziet alleen wat met je is gedeeld",
        "code": "guest",
    }
    share_body = {"user_id": s["anna_id"], "permission": "read"}
    for method, url, extra in [
        ("GET", "/api/v1/files/root", {}),
        ("GET", f"/api/v1/files/{gast_root}/children", {}),
        ("POST", f"/api/v1/files/{gast_root}/folders", {"json": {"name": "N"}}),
        ("PUT", f"/api/v1/files/{gast_root}/upload?name=x", {"content": b"x"}),
        ("GET", "/api/v1/search", {"params": {"in": gast_root}}),
        ("GET", "/api/v1/directory", {"params": {"q": "a"}}),
        ("POST", f"/api/v1/files/{s['werk']}/shares", {"json": share_body}),
        ("POST", f"/api/v1/files/{s['werk']}/links", {"json": {"permission": "read"}}),
    ]:
        r = await client.request(method, url, headers=gast, **extra)
        assert r.status_code == 403, (method, url, r.text)
        assert r.json()["detail"] == guest
    r = await create(client, gast, gast_root, "t", 1)
    assert r.status_code == 403 and code(r) == "guest"

    # Wat met een gast gedeeld is, werkt gewoon, ook schrijven
    await share_with(client, jonas, s["gedeeld"], user_id=gast_id, permission="write")
    assert (await put(client, gast, s["gedeeld"], "van gast.txt", b"g")).status_code == 201
    location = (await create(client, gast, s["gedeeld"], "tus.txt", 2)).headers["location"]
    assert (await send(client, gast, location, 0, b"ok")).status_code == 204
    hits = (await client.get("/api/v1/search", params={"q": "gast"}, headers=gast)).json()
    assert [h["node"]["name"] for h in hits["results"]] == ["van gast.txt"]
    assert [p["name"] for p in hits["results"][0]["path"]] == ["Gedeeld"]
    # Kopiëren naar de eigen map kan een gast niet
    r = await client.post(
        f"/api/v1/files/{s['a']}/copy", json={"parent_id": gast_root}, headers=gast
    )
    assert r.status_code == 403 and code(r) == "guest"
    r = await client.post(
        f"/api/v1/files/{s['a']}/copy", json={"parent_id": s["sub"]}, headers=gast
    )
    assert r.status_code == 201
    assert [i["node"]["name"] for i in await get(client, gast, "/api/v1/shared-with-me")] == [
        "Gedeeld"
    ]


@pytest.mark.anyio
async def test_tags_blijven_persoonlijk(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="read")
    tag = (await client.post("/api/v1/tags", json={"name": "Mooi"}, headers=anna)).json()
    r = await client.put(
        f"/api/v1/files/{s['a']}/tags", json={"tag_ids": [tag["id"]]}, headers=anna
    )
    assert r.status_code == 200 and [t["name"] for t in r.json()] == ["Mooi"]
    assert [t["name"] for t in (await get(client, anna, f"/api/v1/files/{s['a']}"))["tags"]] == [
        "Mooi"
    ]
    assert (await get(client, jonas, f"/api/v1/files/{s['a']}"))["tags"] == []
    hits = (await client.get("/api/v1/search", params={"tag": tag["id"]}, headers=anna)).json()
    assert [h["node"]["name"] for h in hits["results"]] == ["a.txt"]
    tags = await get(client, anna, "/api/v1/tags")
    assert tags[0]["count"] == 1
    # Tags van een ander kun je niet gebruiken
    jonas_tag = (await client.post("/api/v1/tags", json={"name": "J"}, headers=jonas)).json()
    r = await client.put(
        f"/api/v1/files/{s['a']}/tags", json={"tag_ids": [jonas_tag["id"]]}, headers=anna
    )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_zoeken_in_wat_gedeeld_is(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    anna_root = (await root(client, anna))["folder"]["id"]
    await put(client, anna, anna_root, "rapport van anna.txt", b"x")
    await put(client, jonas, s["sub"], "rapport diep.txt", b"x")
    await put(client, jonas, s["werk"], "rapport geheim.txt", b"x")
    rapporten = await mkdir(client, jonas, s["werk"], "Rapporten")
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="read")
    await share_with(client, jonas, rapporten, user_id=s["anna_id"], permission="write")

    result = await get(client, anna, "/api/v1/search", q="rapport")
    hits = {h["node"]["name"]: h for h in result["results"]}
    assert sorted(hits) == ["Rapporten", "rapport diep.txt", "rapport van anna.txt"]
    diep = hits["rapport diep.txt"]
    assert [p["name"] for p in diep["path"]] == ["Gedeeld", "Sub"]
    assert diep["node"]["permission"] == "read" and diep["node"]["owner"]["id"] == s["jonas_id"]
    assert diep["node"]["parent_id"] == s["sub"]
    # De gedeelde map zelf: zonder pad en zonder parent_id
    assert hits["Rapporten"]["path"] == [] and hits["Rapporten"]["node"]["parent_id"] is None
    assert hits["Rapporten"]["node"]["permission"] == "write"
    eigen = hits["rapport van anna.txt"]
    assert eigen["node"]["permission"] == "owner" and [p["name"] for p in eigen["path"]] == [""]
    # Binnen een gedeelde map zoeken; het pad begint nog steeds bij de gedeelde map
    result = await get(client, anna, "/api/v1/search", q="rapport", **{"in": s["sub"]})
    assert [(h["node"]["name"], [p["name"] for p in h["path"]]) for h in result["results"]] == [
        ("rapport diep.txt", ["Gedeeld", "Sub"])
    ]
    await get(client, anna, "/api/v1/search", expect=404, **{"in": s["werk"]})
    # Jonas vindt alles van zichzelf, met het gewone pad
    result = await get(client, jonas, "/api/v1/search", q="rapport")
    names = [h["node"]["name"] for h in result["results"]]
    assert names == ["Rapporten", "rapport diep.txt", "rapport geheim.txt"]


@pytest.mark.anyio
async def test_previews_van_wat_gedeeld_is(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    await share_with(client, jonas, s["sub"], user_id=s["anna_id"], permission="read")
    r = await client.post(
        "/api/v1/previews", json={"node_ids": [s["diep"], s["a"], s["geheim"]]}, headers=anna
    )
    assert list(r.json()) == [s["diep"]]


@pytest.mark.anyio
async def test_lijst_van_een_gedeelde_map_zonder_query_per_bestand(client, setup, settings):
    from tests.test_search import count_queries

    s = setup
    jonas, anna = s["jonas"], s["anna"]
    few, many = (
        await mkdir(client, jonas, s["gedeeld"], "few"),
        await mkdir(client, jonas, s["gedeeld"], "many"),
    )
    for folder, count in ((few, 2), (many, 25)):
        for i in range(count):
            await put(client, jonas, folder, f"bestand {i}.txt", f"{folder}{i}".encode())
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="read")

    async def statements(url):
        with count_queries(settings) as seen:
            r = await client.get(url, headers=anna)
        assert r.status_code == 200
        return len(seen)

    assert await statements(f"/api/v1/files/{few}/children") == await statements(
        f"/api/v1/files/{many}/children"
    )
    with count_queries(settings) as seen:
        r = await client.get("/api/v1/search", params={"q": "bestand", "limit": 200}, headers=anna)
    assert len(r.json()["results"]) == 27
    assert len(seen) < 15, seen


@pytest.mark.anyio
async def test_tus_upload_alleen_voor_wie_hem_begon(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="write")
    location = (await create(client, anna, s["gedeeld"], "x.bin", 4)).headers["location"]
    # De eigenaar van de map kan er niet bij: het is de upload van Anna
    assert (await client.head(location, headers={**jonas, **TUS})).status_code == 404
    assert (await send(client, jonas, location, 0, b"1234")).status_code == 404
    assert (await client.head(location, headers={**anna, **TUS})).status_code == 200
    # Alleen nog lezen: verder uploaden gaat niet
    [share] = await get(client, jonas, f"/api/v1/files/{s['gedeeld']}/shares")
    await client.patch(f"/api/v1/shares/{share['id']}", json={"permission": "read"}, headers=jonas)
    assert (await send(client, anna, location, 0, b"1234")).status_code == 404


@pytest.mark.anyio
async def test_nieuwe_inhoud_voor_een_gedeeld_bestand(client, setup, settings):
    """Kan bewerken op één bestand: de ontvanger ziet de map erboven niet, maar kan via het
    bestand zelf een nieuwe versie zetten. Het blijft van de eigenaar."""
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    bert, _ = await person(client, jonas, "bert@example.nl")
    url = f"/api/v1/files/{s['a']}/content"
    share = await share_with(client, jonas, s["a"], user_id=s["anna_id"], permission="read")
    r = await client.put(url, content=b"van anna", headers=anna)
    assert r.status_code == 403 and code(r) == "read_only"
    r = await client.patch(
        f"/api/v1/shares/{share['id']}", json={"permission": "write"}, headers=jonas
    )
    assert r.status_code == 200
    r = await client.put(url, content=b"van anna", headers=anna)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["id"], out["parent_id"], out["permission"]) == (s["a"], None, "write")
    assert out["size"] == 8 and out["owner"]["display_name"] == "Jonas"
    assert (await client.get(url, headers=jonas)).content == b"van anna"
    [old] = await get(client, jonas, f"/api/v1/files/{s['a']}/versions")
    assert old["size"] == 3 and old["replaced_by"]["display_name"] == "Anna"
    listing = await get(client, jonas, f"/api/v1/files/{s['gedeeld']}/children")
    assert [c["name"] for c in listing["children"]] == ["Sub", "a.txt"]
    # De eigenaar kan het ook; dezelfde inhoud geeft geen nieuwe versie
    r = await client.put(url, content=b"van anna", headers=jonas)
    assert r.status_code == 200 and r.json()["permission"] == "owner"
    assert len(await get(client, jonas, f"/api/v1/files/{s['a']}/versions")) == 1
    # Niet zonder deling, niet voor een map, niet in de prullenbak
    assert (await client.put(url, content=b"x", headers=bert)).status_code == 404
    r = await client.put(f"/api/v1/files/{s['sub']}/content", content=b"x", headers=jonas)
    assert r.status_code == 422
    assert (await client.delete(f"/api/v1/files/{s['a']}", headers=jonas)).status_code == 204
    assert (await client.put(url, content=b"x", headers=anna)).status_code == 404
    assert (await client.put(url, content=b"x", headers=jonas)).status_code == 422
    await check_refs(settings)


@pytest.mark.anyio
async def test_versies_van_wat_gedeeld_is(client, setup):
    s = setup
    jonas, anna = s["jonas"], s["anna"]
    bert, _ = await person(client, jonas, "bert@example.nl")
    await put(client, jonas, s["gedeeld"], "a.txt", b"nieuw", conflict="replace")
    [old] = await get(client, jonas, f"/api/v1/files/{s['a']}/versions")
    await share_with(client, jonas, s["gedeeld"], user_id=s["anna_id"], permission="read")
    base = f"/api/v1/files/{s['a']}"
    # Kijken en de oude inhoud ophalen mag
    assert [v["id"] for v in await get(client, anna, f"{base}/versions")] == [old["id"]]
    r = await client.get(f"{base}/content", params={"version": old["id"]}, headers=anna)
    assert r.content == b"aaa"
    r = await client.post(f"{base}/download-link", params={"version": old["id"]}, headers=anna)
    assert (await client.get(r.json()["url"])).content == b"aaa"
    # Terugzetten niet (alleen kijken), weggooien niet (eigenaar)
    r = await client.post(f"{base}/versions/{old['id']}/restore", headers=anna)
    assert r.status_code == 403 and code(r) == "read_only"
    r = await client.delete(f"{base}/versions/{old['id']}", headers=anna)
    assert r.status_code == 403 and code(r) == "owner_only"
    # Iemand zonder deling ziet er niets van
    await get(client, bert, f"{base}/versions", expect=404)
    for method, url, params in [
        ("GET", f"{base}/content", {"version": old["id"]}),
        ("POST", f"{base}/download-link", {"version": old["id"]}),
        ("POST", f"{base}/versions/{old['id']}/restore", {}),
        ("DELETE", f"{base}/versions/{old['id']}", {}),
    ]:
        r = await client.request(method, url, params=params, headers=bert)
        assert r.status_code == 404, (method, url, r.text)
