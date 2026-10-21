"""Het detail voor de viewer (GET /files/{id}/preview): licht in lijsten, volledig in de
viewer, voor een eerdere versie, kantoordocumenten via /document, de publieke varianten, en
nergens een locatie."""

import io
import json
import uuid

import pytest
from PIL import Image
from sqlalchemy import update

from app import jobs, previews
from app.models import Blob
from tests.factories import code_of, maker
from tests.test_files import mkdir, put, root
from tests.test_office import docx_bytes, xlsx_bytes
from tests.test_previews import picture, zip_bytes
from tests.test_shares import person

# 52°22'7.5" N, 4°53'1.25" E: wat er ook van in een antwoord zou staan
GPS_TRACES = ("gps", "52.3", "22.0, 7.5", "4.88", "53.0, 1.25", "latitude", "longitude")


def jpeg_with_gps() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "Camerafabriek"
    exif[0x8825] = {1: "N", 2: (52.0, 22.0, 7.5), 3: "E", 4: (4.0, 53.0, 1.25)}
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "green").save(buf, "JPEG", exif=exif)
    return buf.getvalue()


async def upload(client, headers, name, data, parent=None, **params):
    parent = parent or (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, parent, name, data, **params)
    assert r.status_code == 201, r.text
    return r.json()


async def listing(client, headers, *ids):
    r = await client.post("/api/v1/previews", json={"node_ids": list(ids)}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def detail(client, headers, node_id, expect=200, **params):
    r = await client.get(f"/api/v1/files/{node_id}/preview", params=params, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def no_location(payload) -> None:
    text = json.dumps(payload).lower()
    for trace in GPS_TRACES:
        assert trace not in text, (trace, text)


@pytest.mark.anyio
async def test_licht_in_de_lijst_en_alles_in_de_viewer(client, admin, settings):
    jonas, _ = admin
    txt = await upload(client, jonas, "notities.txt", b"hallo\nwereld\n")
    archive = await upload(client, jonas, "map.zip", zip_bytes(2))
    scan = await upload(client, jonas, "scan.tiff", picture("TIFF", (3000, 2000)))
    photo = await upload(client, jonas, "foto.png", picture("PNG", (300, 200)))
    assert await jobs.run_pending(settings) == 4
    light = await listing(client, jonas, txt["id"], archive["id"], scan["id"], photo["id"])
    for item in light.values():
        assert set(item) == {"state", "thumb", "large", "meta"}
        assert set(item["meta"]) <= set(previews.LIGHT)
        for heavy in ("text", "entries", "exif", "encoding", "video", "audio"):
            assert heavy not in item["meta"]
    assert light[scan["id"]]["meta"]["width"] == 3000

    full = await detail(client, jonas, txt["id"])
    assert full["meta"]["text"] == "hallo\nwereld\n" and full["meta"]["encoding"] == "utf-8"
    assert full["display"] is None and full["taken_at"] is None
    assert len((await detail(client, jonas, archive["id"]))["meta"]["entries"]) == 2
    # TIFF: 2048 voor de viewer (de browser kan het origineel niet tonen); PNG niet
    tiff = await detail(client, jonas, scan["id"])
    assert "/2048.webp" in tiff["display"] and tiff["meta"]["sizes"] == [256, 1024, 2048]
    r = await client.get(tiff["display"])
    assert r.status_code == 200
    assert Image.open(io.BytesIO(r.content)).size == (2048, 1365)
    assert (await detail(client, jonas, photo["id"]))["display"] is None
    assert (await detail(client, jonas, photo["id"]))["meta"]["animated"] is False


@pytest.mark.anyio
async def test_detail_van_een_eerdere_versie(client, admin, settings):
    jonas, _ = admin
    first = await upload(client, jonas, "a.txt", b"eerste versie")
    await upload(client, jonas, "a.txt", b"tweede", conflict="replace")
    await jobs.run_pending(settings)
    versions = (await client.get(f"/api/v1/files/{first['id']}/versions", headers=jonas)).json()
    assert (await detail(client, jonas, first["id"]))["meta"]["text"] == "tweede"
    old = await detail(client, jonas, first["id"], version=versions[0]["id"])
    assert old["meta"]["text"] == "eerste versie"
    r = await client.get(
        f"/api/v1/files/{first['id']}/preview",
        params={"version": str(uuid.uuid4())},
        headers=jonas,
    )
    assert r.status_code == 404 and code_of(r) == "version_not_found"


@pytest.mark.anyio
async def test_alleen_wat_je_mag_zien(client, admin, settings):
    jonas, _ = admin
    node = await upload(client, jonas, "geheim.txt", b"geheim")
    anna, _ = await person(client, jonas, "anna@example.nl")
    r = await client.get(f"/api/v1/files/{node['id']}/preview", headers=anna)
    assert r.status_code == 404
    r = await client.get(f"/api/v1/files/{node['id']}/preview")
    assert r.status_code == 401
    folder = (await root(client, jonas))["folder"]["id"]
    r = await client.get(f"/api/v1/files/{folder}/preview", headers=jonas)
    assert r.status_code == 422 and code_of(r) == "not_a_file"


@pytest.mark.anyio
async def test_nergens_een_locatie(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Vakantie")
    photo = await upload(client, jonas, "zee.jpg", jpeg_with_gps(), parent=folder)
    await jobs.run_pending(settings)
    # Ook als een (oudere of toekomstige) versie het ooit in de metadata zette
    async with maker(settings)() as session:
        blob = await session.get(Blob, photo["etag"].strip('"'))
        meta = dict(blob.preview_meta or {})
        meta.update(
            gps={"lat": 52.369, "lon": 4.884},
            latitude=52.369,
            longitude=4.884,
            location="Amsterdam",
            exif={"make": "Camerafabriek", "GPSLatitude": [52.0, 22.0, 7.5], "gps_lat": 52.369},
        )
        await session.execute(
            update(Blob).where(Blob.sha256 == blob.sha256).values(preview_meta=meta)
        )
        await session.commit()

    full = await detail(client, jonas, photo["id"])
    assert full["meta"]["exif"] == {"make": "Camerafabriek"} and "location" not in full["meta"]
    no_location(full)
    no_location(await listing(client, jonas, photo["id"]))
    r = await client.get(f"/api/v1/files/{folder}/children", headers=jonas)
    no_location(r.json())
    r = await client.get(f"/api/v1/files/{photo['id']}", headers=jonas)
    no_location(r.json())

    r = await client.post(f"/api/v1/files/{folder}/links", json={"permission": "read"},
                          headers=jonas)  # fmt: skip
    token = r.json()["link"]["url"].removeprefix("/s/")
    opened = (await client.post("/api/v1/public/open", json={"token": token})).json()
    key = {"Authorization": f"Bearer {opened['key']}"}
    r = await client.get(f"/api/v1/public/preview/{photo['id']}", headers=key)
    assert r.status_code == 200
    no_location(r.json())
    r = await client.post("/api/v1/public/previews", json={"node_ids": [photo["id"]]}, headers=key)
    assert r.status_code == 200
    no_location(r.json())


@pytest.mark.anyio
async def test_kantoordocument_via_document(client, admin, settings):
    jonas, _ = admin
    brief = await upload(client, jonas, "brief.docx", docx_bytes())
    sheet = await upload(client, jonas, "cijfers.xlsx", xlsx_bytes())
    # Nog geen preview: wel het soort, nog geen inhoud
    r = await client.get(f"/api/v1/files/{brief['id']}/document", headers=jonas)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "office" and r.json()["office"] is None
    await jobs.run_pending(settings)
    r = await client.get(f"/api/v1/files/{brief['id']}/document", headers=jonas)
    doc = r.json()
    assert doc["kind"] == "office" and doc["page_url"] is None and doc["pages"] is None
    assert doc["office"]["type"] == "document" and doc["office"]["blocks"]
    r = await client.get(f"/api/v1/files/{sheet['id']}/document", headers=jonas)
    assert r.json()["office"]["type"] == "sheet"
    meta = (await detail(client, jonas, brief["id"]))["meta"]
    assert meta["office"] == "document"
    # Het lichte deel heeft de inhoud niet
    assert "office" not in (await listing(client, jonas, brief["id"]))[brief["id"]]["meta"]
