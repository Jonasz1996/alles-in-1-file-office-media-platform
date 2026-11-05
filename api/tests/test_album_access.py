"""Wie wat mag met een gedeeld album en de foto's erin (app/access.py, routers/albums.py,
routers/shares.py). Elk pad naar een album-item: een deling met een persoon of groep, ingetrokken
of verlopen, een groepslid dat eruit gaat, een foto in de prullenbak of definitief weg, een gast,
schrijven dat geweigerd wordt, en de bulkcontrole die de zip van P2 gebruikt."""

import uuid

import pytest
from sqlalchemy import insert

from app import access
from app.models import AlbumItem
from tests.factories import code_of, maker
from tests.test_files import mkdir, put, root
from tests.test_render_meta import camera_jpeg
from tests.test_shares import expire, group_of, person

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(client, admin):
    """Jonas (eigenaar) met /Reis/{a.jpg, b.jpg}, /Reis/Diep/c.jpg en /los.jpg; album
    "Vakantie" met a, b en c (niet los). Bas (gebruiker), Gijs (gast) en Mia (zit in groep
    Familie)."""
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    reis = await mkdir(client, jonas, root_id, "Reis")
    diep = await mkdir(client, jonas, reis, "Diep")
    ids = {}
    for name, parent, color in (("a", reis, "red"), ("b", reis, "blue"), ("c", diep, "green"),
                                ("los", root_id, "black")):  # fmt: skip
        r = await put(client, jonas, parent, f"{name}.jpg", camera_jpeg(color=color))
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    r = await client.post("/api/v1/albums", json={"name": "Vakantie"}, headers=jonas)
    album = r.json()["id"]
    r = await client.post(
        f"/api/v1/albums/{album}/items",
        json={"node_ids": [ids["a"], ids["b"], ids["c"]]},
        headers=jonas,
    )
    assert r.json()["added"] == 3, r.text
    bas, bas_id = await person(client, jonas, "bas@example.nl")
    gijs, gijs_id = await person(client, jonas, "gijs@example.nl", role="guest")
    mia, mia_id = await person(client, jonas, "mia@example.nl")
    familie = await group_of(client, jonas, "Familie", mia_id)
    return {
        "jonas": jonas,
        "jonas_id": tokens["user"]["id"],
        "album": album,
        "reis": reis,
        "diep": diep,
        **ids,
        "bas": bas,
        "bas_id": bas_id,
        "gijs": gijs,
        "gijs_id": gijs_id,
        "mia": mia,
        "mia_id": mia_id,
        "familie": familie,
    }


async def share(client, w, expect=201, **body):
    r = await client.post(f"/api/v1/files/{w['album']}/shares", json=body, headers=w["jonas"])
    assert r.status_code == expect, r.text
    return r.json()


async def can_see(client, headers, w, node_id) -> bool:
    """Ziet deze gebruiker dit bestand: info, content en een downloadlink die werkt."""
    r = await client.get(f"/api/v1/files/{node_id}", headers=headers)
    if r.status_code == 404:
        for path in (f"/api/v1/files/{node_id}/content", f"/api/v1/files/{node_id}/versions"):
            assert (await client.get(path, headers=headers)).status_code == 404, path
        r = await client.post(f"/api/v1/files/{node_id}/download-link", headers=headers)
        assert r.status_code == 404
        return False
    assert r.status_code == 200, r.text
    node = r.json()
    assert node["permission"] == "read" and node["parent_id"] is None  # niets erboven
    r = await client.get(f"/api/v1/files/{node_id}/content", headers=headers)
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")
    link = (await client.post(f"/api/v1/files/{node_id}/download-link", headers=headers)).json()
    assert (await client.get(link["url"])).status_code == 200
    return True


async def album_visible(client, headers, w) -> bool:
    r = await client.get(f"/api/v1/albums/{w['album']}", headers=headers)
    items = await client.get(f"/api/v1/albums/{w['album']}/items", headers=headers)
    ids = await client.get(f"/api/v1/albums/{w['album']}/ids", headers=headers)
    statuses = {r.status_code, items.status_code, ids.status_code}
    assert len(statuses) == 1, statuses
    return r.status_code == 200


async def test_zonder_deling_niets(client, world):
    w = world
    assert not await album_visible(client, w["bas"], w)
    assert not await can_see(client, w["bas"], w, w["a"])


async def test_gedeeld_album_alleen_lezen(client, world):
    w = world
    await share(client, w, user_id=w["bas_id"], permission="read")
    assert await album_visible(client, w["bas"], w)
    album = (await client.get(f"/api/v1/albums/{w['album']}", headers=w["bas"])).json()
    assert album["permission"] == "read" and album["count"] == 3 and album["shared"] is False
    items = (await client.get(f"/api/v1/albums/{w['album']}/items", headers=w["bas"])).json()
    assert {i["id"] for i in items["items"]} == {w["a"], w["b"], w["c"]}
    assert all(i["shared"] is False for i in items["items"])
    for name in ("a", "b", "c"):
        assert await can_see(client, w["bas"], w, w[name])
    # Wat niet in het album zit, en de mappen erboven, ziet hij niet
    assert not await can_see(client, w["bas"], w, w["los"])
    for folder in (w["reis"], w["diep"]):
        r = await client.get(f"/api/v1/files/{folder}/children", headers=w["bas"])
        assert r.status_code == 404
    # Previews van album-items wel, van de rest niet
    r = await client.post(
        "/api/v1/previews", json={"node_ids": [w["a"], w["los"]]}, headers=w["bas"]
    )
    assert set(r.json()) == {w["a"]}
    # Gedeeld met mij: het album, als album
    shared = (await client.get("/api/v1/shared-with-me", headers=w["bas"])).json()
    assert [(s["node"]["id"], s["node"]["kind"]) for s in shared] == [(w["album"], "album")]


async def test_schrijven_blijft_geweigerd(client, world):
    w = world
    await share(client, w, user_id=w["bas_id"], permission="read")
    bas, album = w["bas"], w["album"]
    # Het album zelf
    for method, path, body in (
        ("PATCH", f"/api/v1/albums/{album}", {"name": "Van mij"}),
        ("POST", f"/api/v1/albums/{album}/items", {"node_ids": [w["los"]]}),
        ("POST", f"/api/v1/albums/{album}/items/remove", {"node_ids": [w["a"]]}),
        ("DELETE", f"/api/v1/albums/{album}", None),
        ("POST", f"/api/v1/files/{album}/shares", {"user_id": w["mia_id"], "permission": "read"}),
        ("POST", f"/api/v1/files/{album}/links", {"permission": "read"}),
    ):
        r = await client.request(method, path, json=body, headers=bas)
        assert r.status_code == 403 and code_of(r) == "owner_only", (method, path, r.text)
    # Een foto in het album
    a = w["a"]
    r = await client.patch(f"/api/v1/files/{a}", json={"name": "x.jpg"}, headers=bas)
    assert r.status_code == 403, r.text
    r = await client.delete(f"/api/v1/files/{a}", headers=bas)
    assert r.status_code == 403, r.text
    r = await client.put(f"/api/v1/files/{a}/content", content=b"anders", headers=bas)
    assert r.status_code == 403, r.text
    r = await client.post(f"/api/v1/files/{a}/shares", json={"user_id": w["mia_id"],
                          "permission": "read"}, headers=bas)  # fmt: skip
    assert r.status_code == 403, r.text
    # Zijn eigen foto in andermans album zetten: ook niet
    root_id = (await root(client, bas))["folder"]["id"]
    own = (await put(client, bas, root_id, "van-bas.jpg", camera_jpeg(color="white"))).json()
    r = await client.post(
        f"/api/v1/albums/{album}/items", json={"node_ids": [own["id"]]}, headers=bas
    )
    assert r.status_code == 403
    # En de eigenaar kan andermans foto niet toevoegen
    r = await client.post(
        f"/api/v1/albums/{album}/items", json={"node_ids": [own["id"]]}, headers=w["jonas"]
    )
    assert r.json()["skipped"][0]["code"] == "not_own"


async def test_schrijfrecht_en_uploadlink_op_een_album_kunnen_niet(client, world):
    w = world
    r = await client.post(
        f"/api/v1/files/{w['album']}/shares",
        json={"user_id": w["bas_id"], "permission": "write"},
        headers=w["jonas"],
    )
    assert r.status_code == 422 and code_of(r) == "album_read_only"
    made = await share(client, w, user_id=w["bas_id"], permission="read")
    r = await client.patch(
        f"/api/v1/shares/{made['id']}", json={"permission": "write"}, headers=w["jonas"]
    )
    assert r.status_code == 422 and code_of(r) == "album_read_only"
    r = await client.post(
        f"/api/v1/files/{w['album']}/links", json={"permission": "upload"}, headers=w["jonas"]
    )
    assert r.status_code == 422 and code_of(r) == "upload_folder_only"
    # Lezen blijft
    assert await can_see(client, w["bas"], w, w["a"])


async def test_ingetrokken_deling(client, world):
    w = world
    made = await share(client, w, user_id=w["bas_id"], permission="read")
    link = (await client.post(f"/api/v1/files/{w['a']}/download-link", headers=w["bas"])).json()
    r = await client.delete(f"/api/v1/shares/{made['id']}", headers=w["jonas"])
    assert r.status_code == 204
    assert not await album_visible(client, w["bas"], w)
    assert not await can_see(client, w["bas"], w, w["a"])
    # Een downloadlink die al uit was, werkt ook niet meer
    assert (await client.get(link["url"])).status_code == 404


async def test_verlopen_deling(client, world, settings):
    w = world
    made = await share(client, w, user_id=w["bas_id"], permission="read")
    assert await can_see(client, w["bas"], w, w["b"])
    await expire(settings, made["id"])
    assert not await album_visible(client, w["bas"], w)
    assert not await can_see(client, w["bas"], w, w["b"])


async def test_groepslid_eruit(client, world):
    w = world
    await share(client, w, group_id=w["familie"], permission="read")
    assert await album_visible(client, w["mia"], w)
    assert await can_see(client, w["mia"], w, w["c"])
    r = await client.put(
        f"/api/v1/groups/{w['familie']}/members", json={"user_ids": []}, headers=w["jonas"]
    )
    assert r.status_code == 200, r.text
    assert not await album_visible(client, w["mia"], w)
    assert not await can_see(client, w["mia"], w, w["c"])


async def test_foto_of_map_in_de_prullenbak_en_definitief_weg(client, world):
    w = world
    await share(client, w, user_id=w["bas_id"], permission="read")
    bas, jonas = w["bas"], w["jonas"]
    # De foto zelf in de prullenbak
    assert (await client.delete(f"/api/v1/files/{w['a']}", headers=jonas)).status_code == 204
    assert not await can_see(client, bas, w, w["a"])
    items = (await client.get(f"/api/v1/albums/{w['album']}/items", headers=bas)).json()["items"]
    assert w["a"] not in {i["id"] for i in items}
    # Een map erboven in de prullenbak
    assert (await client.delete(f"/api/v1/files/{w['diep']}", headers=jonas)).status_code == 204
    assert not await can_see(client, bas, w, w["c"])
    ids = (await client.get(f"/api/v1/albums/{w['album']}/ids", headers=bas)).json()["ids"]
    assert ids == [w["b"]]
    # Terugzetten: weer zichtbaar
    r = await client.post("/api/v1/trash/restore", json={"ids": [w["a"], w["diep"]]}, headers=jonas)
    assert r.status_code == 200, r.text
    assert await can_see(client, bas, w, w["a"]) and await can_see(client, bas, w, w["c"])
    # Definitief weg
    await client.delete(f"/api/v1/files/{w['b']}", headers=jonas)
    assert (await client.delete(f"/api/v1/trash/{w['b']}", headers=jonas)).status_code == 204
    assert not await can_see(client, bas, w, w["b"])
    assert (await client.get(f"/api/v1/albums/{w['album']}", headers=bas)).json()["count"] == 2


async def test_gast_kijkt_mee(client, world):
    w = world
    await share(client, w, user_id=w["gijs_id"], permission="read")
    assert await album_visible(client, w["gijs"], w)
    assert await can_see(client, w["gijs"], w, w["a"])
    albums = (await client.get("/api/v1/albums", headers=w["gijs"])).json()["albums"]
    assert [a["id"] for a in albums] == [w["album"]]
    # Een gast heeft zelf geen tijdlijn en geen albums
    r = await client.get("/api/v1/photos", headers=w["gijs"])
    assert r.status_code == 403 and code_of(r) == "guest"
    r = await client.post("/api/v1/albums", json={"name": "x"}, headers=w["gijs"])
    assert r.status_code == 403 and code_of(r) == "guest"


async def test_item_van_een_ander_telt_niet(client, world, settings):
    """Zou er toch een bestand van een ander in album_item staan (rechtstreeks in de database),
    dan geeft het album daar geen toegang toe."""
    w = world
    await share(client, w, user_id=w["mia_id"], permission="read")
    root_id = (await root(client, w["bas"]))["folder"]["id"]
    van_bas = (await put(client, w["bas"], root_id, "privé.jpg", camera_jpeg(color="white"))).json()
    async with maker(settings)() as session:
        await session.execute(
            insert(AlbumItem).values(
                album_id=uuid.UUID(w["album"]), node_id=uuid.UUID(van_bas["id"])
            )
        )
        await session.commit()
    assert not await can_see(client, w["mia"], w, van_bas["id"])
    ids = (await client.get(f"/api/v1/albums/{w['album']}/ids", headers=w["mia"])).json()["ids"]
    assert van_bas["id"] not in ids


async def test_toegang_in_bulk_voor_de_zip(client, world, settings):
    """De zip van P2 controleert met access.resolve (zoals get_access(read)): alle ids uit
    /albums/{id}/ids krijgen toegang, en na intrekken geen enkele meer."""
    w = world
    made = await share(client, w, user_id=w["bas_id"], permission="read")
    ids = (await client.get(f"/api/v1/albums/{w['album']}/ids", headers=w["bas"])).json()["ids"]
    wanted = [uuid.UUID(i) for i in ids] + [uuid.UUID(w["los"])]
    async with maker(settings)() as session:
        grants = await access.resolve(session, uuid.UUID(w["bas_id"]), wanted)
    assert set(grants) == {uuid.UUID(i) for i in ids}
    assert all(g.permission == "read" and g.direct and g.root_id == n for n, g in grants.items())
    await client.delete(f"/api/v1/shares/{made['id']}", headers=w["jonas"])
    async with maker(settings)() as session:
        assert await access.resolve(session, uuid.UUID(w["bas_id"]), wanted) == {}
