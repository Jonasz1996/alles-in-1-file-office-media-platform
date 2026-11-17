"""Nextcloud-clients en WebDAV (app/nc/, routers/dav.py en routers/nc_ocs.py)."""

import base64
import re

import pytest

from tests.test_files import root

pytestmark = pytest.mark.anyio


def basic(login: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def app_password(client, headers) -> dict[str, str]:
    r = await client.post("/api/v1/account/app-passwords", json={"name": "rclone"}, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    return basic(body["login"], body["password"])


async def test_status_en_capabilities(client):
    r = await client.get("/status.php")
    assert r.status_code == 200
    assert r.json()["installed"] is True and r.json()["version"].startswith("30.")
    r = await client.get("/ocs/v2.php/cloud/capabilities?format=json")
    assert r.json()["ocs"]["meta"]["statuscode"] == 200
    assert r.json()["ocs"]["data"]["capabilities"]["dav"]["chunking"] == "1.0"


async def test_inlogflow_v2(client, admin):
    r = await client.post("/index.php/login/v2", headers={"User-Agent": "Mozilla/5.0 mirall/3.14"})
    flow = r.json()
    poll = flow["poll"]["token"]
    login_path = flow["login"].split("http://test", 1)[1]
    assert (await client.post("/index.php/login/v2/poll", data={"token": poll})).status_code == 404
    page = await client.get(login_path)
    assert page.status_code == 200 and "Nextcloud desktop" in page.text
    bad = await client.post(login_path, data={"email": "jonas@example.nl", "password": "fout"})
    assert "klopt niet" in bad.text
    ok = await client.post(
        login_path, data={"email": "jonas@example.nl", "password": "lang-genoeg-123"}
    )
    assert "Gekoppeld" in ok.text
    r = await client.post("/index.php/login/v2/poll", data={"token": poll})
    assert r.status_code == 200
    creds = r.json()
    assert creds["loginName"] == "jonas@example.nl" and creds["server"] == "http://test"
    # Eén keer ophalen
    assert (await client.post("/index.php/login/v2/poll", data={"token": poll})).status_code == 404
    user = await client.get(
        "/ocs/v1.php/cloud/user?format=json", headers=basic(creds["loginName"], creds["appPassword"])
    )
    assert user.json()["ocs"]["data"]["id"] == "jonas@example.nl"
    # Uitloggen in de client trekt het wachtwoord in
    h = basic(creds["loginName"], creds["appPassword"])
    assert (await client.delete("/ocs/v2.php/core/apppassword", headers=h)).status_code == 200
    assert (await client.get("/ocs/v2.php/cloud/user", headers=h)).status_code == 401


async def test_geen_toegang_zonder_app_wachtwoord(client, admin):
    r = await client.request("PROPFIND", "/remote.php/dav/files/jonas@example.nl/")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    # Het gewone wachtwoord werkt niet (2FA zou anders omzeild worden)
    r = await client.request(
        "PROPFIND",
        "/remote.php/dav/files/jonas@example.nl/",
        headers=basic("jonas@example.nl", "lang-genoeg-123"),
    )
    assert r.status_code == 401


def hrefs(text: str) -> list[str]:
    return re.findall(r"<d:href>([^<]*)</d:href>", text)


def etag_of(text: str, href: str) -> str:
    for block in text.split("<d:response>"):
        if f"<d:href>{href}</d:href>" in block:
            return re.search(r"<d:getetag>([^<]*)</d:getetag>", block).group(1)
    raise AssertionError(href)


async def test_webdav_rondje(client, admin):
    headers, _ = admin
    h = await app_password(client, headers)
    base = "/remote.php/dav/files/jonas@example.nl"
    r = await client.request("PROPFIND", base + "/", headers={**h, "Depth": "0"})
    assert r.status_code == 207
    root_etag = etag_of(r.text, "/remote.php/dav/files/jonas%40example.nl/")

    assert (await client.request("MKCOL", base + "/Foto's", headers=h)).status_code == 201
    assert (await client.request("MKCOL", base + "/Foto's", headers=h)).status_code == 405
    assert (await client.request("MKCOL", base + "/x/y", headers=h)).status_code == 409

    r = await client.put(
        base + "/Foto's/a.txt", content=b"hallo", headers={**h, "X-OC-Mtime": "1700000000"}
    )
    assert r.status_code == 201, r.text
    assert r.headers["x-oc-mtime"] == "accepted" and r.headers["oc-fileid"]
    r = await client.put(base + "/Foto's/a.txt", content=b"hallo2", headers=h)
    assert r.status_code == 204

    r = await client.request("PROPFIND", base + "/", headers={**h, "Depth": "0"})
    assert etag_of(r.text, "/remote.php/dav/files/jonas%40example.nl/") != root_etag

    r = await client.request("PROPFIND", base + "/Foto's/", headers={**h, "Depth": "1"})
    assert r.status_code == 207
    assert "/remote.php/dav/files/jonas%40example.nl/Foto%27s/a.txt" in hrefs(r.text)
    assert "<d:getcontentlength>6</d:getcontentlength>" in r.text

    r = await client.get(base + "/Foto's/a.txt", headers=h)
    assert r.content == b"hallo2"

    dest = "http://test/remote.php/dav/files/jonas%40example.nl/b.txt"
    r = await client.request("COPY", base + "/Foto's/a.txt", headers={**h, "Destination": dest})
    assert r.status_code == 201
    r = await client.request(
        "MOVE", base + "/Foto's/a.txt", headers={**h, "Destination": dest, "Overwrite": "F"}
    )
    assert r.status_code == 412
    r = await client.request("MOVE", base + "/Foto's/a.txt", headers={**h, "Destination": dest})
    assert r.status_code == 204
    assert (await client.get(base + "/Foto's/a.txt", headers=h)).status_code == 404
    assert (await client.get(base + "/b.txt", headers=h)).content == b"hallo2"

    assert (await client.delete(base + "/b.txt", headers=h)).status_code == 204
    assert (await client.get(base + "/b.txt", headers=h)).status_code == 404
    # In de prullenbak, niet echt weg
    r = await client.get("/api/v1/trash", headers=headers)
    assert r.status_code == 200 and "b.txt" in r.text

    # Zelfde boom via de oude url
    r = await client.request("PROPFIND", "/remote.php/webdav/", headers={**h, "Depth": "1"})
    assert "/remote.php/webdav/Foto%27s/" in hrefs(r.text)

    # Iemand anders zijn map: nee
    r = await client.request("PROPFIND", "/remote.php/dav/files/ander@x.nl/", headers=h)
    assert r.status_code == 403
    # De bestanden zijn gewoon in de web-app te zien
    tree = await root(client, headers)
    assert tree["children"][0]["name"] == "Foto's"


async def test_upload_in_stukken(client, admin):
    headers, _ = admin
    h = await app_password(client, headers)
    up = "/remote.php/dav/uploads/jonas@example.nl/web-file-upload-123"
    assert (await client.request("MKCOL", up, headers=h)).status_code == 201
    parts = [b"a" * 1000, b"b" * 1000, b"c" * 5]
    for i, part in enumerate(parts, start=1):
        r = await client.put(f"{up}/{i:05d}", content=part, headers=h)
        assert r.status_code == 201
    r = await client.request("PROPFIND", up + "/", headers={**h, "Depth": "1"})
    assert len(hrefs(r.text)) == 4
    dest = "http://test/remote.php/dav/files/jonas%40example.nl/groot.bin"
    r = await client.request(
        "MOVE",
        up + "/.file",
        headers={**h, "Destination": dest, "OC-Total-Length": "2005", "X-OC-Mtime": "1600000000"},
    )
    assert r.status_code == 201, r.text
    got = await client.get("/remote.php/dav/files/jonas@example.nl/groot.bin", headers=h)
    assert got.content == b"".join(parts)
    r = await client.request("PROPFIND", up + "/", headers=h)
    assert r.status_code == 404  # opgeruimd


async def test_app_wachtwoorden_beheren(client, admin):
    headers, _ = admin
    h = await app_password(client, headers)
    rows = (await client.get("/api/v1/account/app-passwords", headers=headers)).json()
    assert [r["name"] for r in rows] == ["rclone"]
    r = await client.delete(f"/api/v1/account/app-passwords/{rows[0]['id']}", headers=headers)
    assert r.status_code == 204
    r = await client.request("PROPFIND", "/remote.php/webdav/", headers=h)
    assert r.status_code == 401
