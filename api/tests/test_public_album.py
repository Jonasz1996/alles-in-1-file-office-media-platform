"""Een album achter een publieke leeslink (routers/public.py): de foto's erin bekijken en
downloaden zonder in te loggen, niets daarbuiten, en nergens GPS."""

import pytest

from app import jobs
from tests import test_album_access
from tests.factories import code_of
from tests.test_public import bearer, fresh_limits, new_link, open_link  # noqa: F401
from tests.test_render_meta import no_gps

pytestmark = pytest.mark.anyio
# Dezelfde opzet als de toegangstests: Jonas met album "Vakantie" (a, b, c) en /los.jpg
world = test_album_access.world


async def opened(client, w, **body):
    link = await new_link(client, w["jonas"], w["album"], permission="read", **body)
    return link, await open_link(client, link)


async def test_album_via_link(client, world, settings):
    w = world
    await jobs.run_pending(settings)  # previews en EXIF
    link, got = await opened(client, w)
    assert got["share"]["kind"] == "album" and got["share"]["name"] == "Vakantie"
    key = bearer(got["key"])

    r = await client.get("/api/v1/public/album/items", headers=key)
    assert r.status_code == 200, r.text
    page = r.json()
    assert {i["id"] for i in page["items"]} == {w["a"], w["b"], w["c"]}
    assert all("shared" not in i and "owner" not in i for i in page["items"])
    assert all(i["thumb"] and i["thumb"].startswith("/api/v1/previews/") for i in page["items"])
    assert page["next"] is None
    no_gps(page)
    # In pagina's
    first = (
        await client.get("/api/v1/public/album/items", params={"limit": 2}, headers=key)
    ).json()
    rest = (
        await client.get(
            "/api/v1/public/album/items", params={"limit": 2, "after": first["next"]}, headers=key
        )
    ).json()
    assert [i["id"] for i in first["items"] + rest["items"]] == [i["id"] for i in page["items"]]

    # Downloaden en previews: alleen wat in het album zit
    r = await client.get(f"/api/v1/public/download/{w['a']}", headers=key)
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")
    r = await client.get(f"/api/v1/public/download/{w['los']}", headers=key)
    assert r.status_code == 404
    r = await client.post(
        "/api/v1/public/previews", json={"node_ids": [w["a"], w["los"], w["album"]]}, headers=key
    )
    assert set(r.json()) == {w["a"]}
    assert "exif" not in r.json()[w["a"]]["meta"]  # de lijst blijft licht (P4)
    no_gps(r.json())
    # De EXIF komt in de viewer (GET /public/preview/{id}, P4), ook in een album, zonder GPS
    r = await client.get(f"/api/v1/public/preview/{w['a']}", headers=key)
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["exif"]["model"] == "Camera X1"
    no_gps(r.json())
    r = await client.get(f"/api/v1/public/preview/{w['los']}", headers=key)
    assert r.status_code == 404
    # Geen map: children weigert
    r = await client.get("/api/v1/public/children", headers=key)
    assert r.status_code == 422 and code_of(r) == "album"


async def test_prullenbak_en_intrekken(client, world):
    w = world
    link, got = await opened(client, w)
    key = bearer(got["key"])
    await client.delete(f"/api/v1/files/{w['diep']}", headers=w["jonas"])
    items = (await client.get("/api/v1/public/album/items", headers=key)).json()["items"]
    assert {i["id"] for i in items} == {w["a"], w["b"]}
    r = await client.get(f"/api/v1/public/download/{w['c']}", headers=key)
    assert r.status_code == 404

    r = await client.delete(f"/api/v1/shares/{link['id']}", headers=w["jonas"])
    assert r.status_code == 204
    for path in ("/api/v1/public/album/items", f"/api/v1/public/download/{w['a']}"):
        r = await client.get(path, headers=key)
        assert r.status_code == 404 and code_of(r) == "link_not_found", path


async def test_album_weg_dan_link_weg(client, world):
    w = world
    _link, got = await opened(client, w)
    key = bearer(got["key"])
    assert (
        await client.delete(f"/api/v1/albums/{w['album']}", headers=w["jonas"])
    ).status_code == 204
    r = await client.get("/api/v1/public/album/items", headers=key)
    assert r.status_code == 404


async def test_link_met_wachtwoord_en_gewone_map(client, world):
    w = world
    link = await new_link(client, w["jonas"], w["album"], permission="read", password="geheim123")
    await open_link(client, link, expect=401)
    got = await open_link(client, link, password="geheim123")
    r = await client.get("/api/v1/public/album/items", headers=bearer(got["key"]))
    assert r.status_code == 200 and len(r.json()["items"]) == 3
    # Een link op een map heeft geen album-items
    folder = await new_link(client, w["jonas"], w["reis"], permission="read")
    got = await open_link(client, folder)
    r = await client.get("/api/v1/public/album/items", headers=bearer(got["key"]))
    assert r.status_code == 422 and code_of(r) == "not_an_album"
