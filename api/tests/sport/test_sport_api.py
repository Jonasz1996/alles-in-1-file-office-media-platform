"""Sport: de routes (importeren, lijst, detail, wijzigen, statistieken, tegels uit)."""

from __future__ import annotations

import gzip
import io
import zipfile

import pytest

from tests.sport.test_sport_core import gpx, line
from tests.test_files import put, root
from tests.test_shares import person

pytestmark = pytest.mark.anyio


def _ele(i):
    return 0.0 if 100 <= i < 160 else 200.0 + 0.05 * i


async def _import(client, headers, name, data):
    r = await client.post(
        "/api/v1/sport/import", content=data, headers={**headers, "X-File-Name": name}
    )
    assert r.status_code == 200, r.text
    return r.json()["results"]


async def test_importeren_lijst_detail_wijzigen(client, admin):
    jonas, _ = admin
    data = gpx(line(600, ele=_ele), name="Rondje park")
    [res] = await _import(client, jonas, "rondje.gpx", data)
    assert res["status"] == "new", res
    ident = res["id"]
    # Hetzelfde bestand nog eens: geen tweede activiteit
    [again] = await _import(client, jonas, "kopie.gpx", data)
    assert again == {"name": "kopie.gpx", "status": "duplicate", "id": ident, "reason": None}
    [bad] = await _import(client, jonas, "x.gpx", b"<gpx/>")
    assert bad["status"] == "error"

    r = await client.get("/api/v1/sport/activities", headers=jonas)
    assert r.status_code == 200
    [item] = r.json()["items"]
    assert item["name"] == "Rondje park" and item["sport"] == "run"
    assert item["preview"]

    r = await client.get(f"/api/v1/sport/activities/{ident}", headers=jonas)
    assert r.status_code == 200, r.text
    act = r.json()
    assert act["fixed"] == 60 and act["gain"] < 40
    assert len(act["series"]["lat"]) == 600
    assert act["series"]["ele_raw"][120] == 0.0 and act["series"]["ele"][120] > 190

    r = await client.patch(
        f"/api/v1/sport/activities/{ident}",
        json={"ele_filter": False, "sport": "walk", "name": "Wandeling"},
        headers=jonas,
    )
    assert r.status_code == 200, r.text
    assert r.json()["gain"] > 190 and r.json()["sport"] == "walk"

    r = await client.get(f"/api/v1/sport/activities/{ident}/file", headers=jonas)
    assert r.status_code == 200 and r.content == data

    r = await client.get("/api/v1/sport/stats", headers=jonas)
    assert r.status_code == 200
    assert r.json()["all_time"]["count"] == 1

    r = await client.get("/api/v1/sport/records?sport=walk", headers=jonas)
    assert r.status_code == 200 and r.json()["rows"][0]["best"]

    # Een ander ziet hem niet
    anna, _ = await person(client, jonas, "anna@example.nl")
    r = await client.get(f"/api/v1/sport/activities/{ident}", headers=anna)
    assert r.status_code == 404

    r = await client.delete(f"/api/v1/sport/activities/{ident}", headers=jonas)
    assert r.status_code == 204
    r = await client.get("/api/v1/sport/activities", headers=jonas)
    assert r.json()["items"] == []


async def test_zip_en_gz_en_uit_bestanden(client, admin):
    jonas, _ = admin
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities/1.gpx", gpx(line(50), name="Een"))
        zf.writestr("activities/2.gpx.gz", gzip.compress(gpx(line(60), name="Twee")))
        zf.writestr("activities/3.fit", b"binair")
        zf.writestr("activities/4.gpx", b"kapot")
    results = await _import(client, jonas, "export.zip", buf.getvalue())
    assert [r["status"] for r in results] == ["new", "new", "error"]

    folder = (await root(client, jonas))["folder"]["id"]
    r = await put(client, jonas, folder, "drie.gpx", gpx(line(70), name="Drie"))
    assert r.status_code in (200, 201), r.text
    node = r.json()["id"]
    r = await client.post("/api/v1/sport/import-files", json={"ids": [node]}, headers=jonas)
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "new"
    r = await client.get("/api/v1/sport/activities?limit=2", headers=jonas)
    page = r.json()
    assert len(page["items"]) == 2 and page["cursor"]
    r = await client.get(f"/api/v1/sport/activities?cursor={page['cursor']}", headers=jonas)
    assert len(r.json()["items"]) == 1


async def test_tegels(client, admin):
    jonas, _ = admin
    r = await client.get("/api/v1/sport/config", headers=jonas)
    assert r.json() == {"tiles": False, "attribution": None, "can_edit": True, "tiles_url": None}
    r = await client.get("/api/v1/sport/tiles/1/0/0", headers=jonas)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "sport_tiles_off"
    r = await client.put(
        "/api/v1/sport/config", json={"tiles_url": "http://x/{z}/{x}/{y}"}, headers=jonas
    )
    assert r.status_code == 422
    r = await client.put(
        "/api/v1/sport/config",
        json={"tiles_url": "https://tile.example.org/{z}/{x}/{y}.png", "attribution": "© OSM"},
        headers=jonas,
    )
    assert r.status_code == 200 and r.json()["tiles"] is True
    anna, _ = await person(client, jonas, "anna@example.nl")
    r = await client.get("/api/v1/sport/config", headers=anna)
    assert r.json() == {"tiles": True, "attribution": "© OSM", "can_edit": False, "tiles_url": None}
    r = await client.put("/api/v1/sport/config", json={"tiles_url": None}, headers=anna)
    assert r.status_code == 403
