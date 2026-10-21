"""Zip van mappen en bestanden: POST /archive/link en de streamende GET /archive?token=."""

import asyncio
import io
import uuid
import zipfile

import anyio
import pytest
from sqlalchemy import event, insert

from app.db import get_engine
from app.models import Node
from app.routers import archive
from app.storage import BlobStore
from app.text import fold
from tests.factories import code_of, create_album, create_file, maker, user_by_email
from tests.test_disabled import block
from tests.test_files import mkdir, put, root
from tests.test_shares import person, share_with


async def link(client, headers, *ids, name="", expect=200) -> str:
    r = await client.post(
        "/api/v1/archive/link", json={"ids": list(ids), "name": name}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json()["url"] if expect == 200 else ""


async def download(client, url) -> zipfile.ZipFile:
    r = await client.get(url)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["cache-control"] == "no-store"
    return zipfile.ZipFile(io.BytesIO(r.content))


@pytest.mark.anyio
async def test_zip_met_mappen_en_bestanden(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = await mkdir(client, headers, root_id, "Foto's")
    b = await mkdir(client, headers, a, "Leeg")
    await put(client, headers, a, "één.txt", b"1")
    weg = (await put(client, headers, a, "weg.txt", b"weg")).json()["id"]
    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    los = (await put(client, headers, root_id, "los.txt", b"los")).json()["id"]
    andere = await mkdir(client, headers, b, "los.txt")  # zelfde naam als een top: genummerd

    url = await link(client, headers, a, los, b, andere, name="Mijn zip.zip")
    assert url.startswith("/api/v1/archive?token=")
    r = await client.get(url)
    assert r.headers["content-disposition"] == (
        "attachment; filename=\"Mijn zip.zip\"; filename*=UTF-8''Mijn%20zip.zip"
    )
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.testzip() is None
    assert zf.namelist() == [
        "Foto's/",
        "los.txt",
        "Foto's/Leeg/",
        "Foto's/één.txt",
        "Foto's/Leeg/los.txt/",
    ]
    assert zf.read("Foto's/één.txt") == b"1" and zf.read("los.txt") == b"los"
    # De link werkt (binnen de tijd) vaker, zonder inloggen; een verzonnen token niet
    assert (await download(client, url)).namelist()[0] == "Foto's/"
    assert (await client.get("/api/v1/archive?token=verzonnen")).status_code == 404

    # Zonder naam: download.zip; twee tops met dezelfde naam: de tweede genummerd
    x = await mkdir(client, headers, root_id, "X")
    other = await mkdir(client, headers, x, "Foto's")
    r = await client.get(await link(client, headers, a, other))
    assert 'filename="download.zip"' in r.headers["content-disposition"]
    assert "Foto's (1)/" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()


@pytest.mark.anyio
async def test_fouten_bij_de_link(client, admin, settings, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = await mkdir(client, headers, root_id, "A")
    await put(client, headers, a, "1.txt", b"1")
    await put(client, headers, a, "2.txt", b"2")
    weg = (await put(client, headers, root_id, "weg.txt", b"x")).json()["id"]
    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    await link(client, headers, weg, expect=404)
    await link(client, headers, str(uuid.uuid4()), expect=404)
    await link(client, headers, expect=422)
    async with maker(settings)() as session:
        album = await create_album(session, await user_by_email(session, "jonas@example.nl"))
        await session.commit()
    r = await client.post("/api/v1/archive/link", json={"ids": [str(album.id)]}, headers=headers)
    assert r.status_code == 422 and code_of(r) == "album"

    # Te veel: 422 too_many vóór de eerste byte
    url = await link(client, headers, a)
    monkeypatch.setattr(archive, "MAX_ENTRIES", 2)
    r = await client.get(url)
    assert r.status_code == 422 and code_of(r) == "too_many"


@pytest.mark.anyio
async def test_ingetrokken_of_geblokkeerd_geeft_404(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    gedeeld = await mkdir(client, jonas, root_id, "Gedeeld")
    await put(client, jonas, gedeeld, "x.txt", b"x")
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    share = await share_with(client, jonas, gedeeld, user_id=anna_id, permission="read")

    url = await link(client, anna, gedeeld)
    assert (await download(client, url)).namelist() == ["Gedeeld/", "Gedeeld/x.txt"]
    await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)
    assert (await client.get(url)).status_code == 404

    await share_with(client, jonas, gedeeld, user_id=anna_id, permission="read")
    url = await link(client, anna, gedeeld)
    await block(settings)
    assert (await client.get(url)).status_code == 404


@pytest.mark.anyio
async def test_schrijver_stopt_als_de_verbinding_wegvalt(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    blobs = BlobStore(settings.blob_dir)
    async with maker(settings)() as session:
        jonas = await user_by_email(session, "jonas@example.nl")
        for i in range(4):
            data = bytes([i]) * (archive.CHUNK * archive.BUFFERED)
            await create_file(
                session, jonas, uuid.UUID(root_id), f"{i}.bin", data=data, blobs=blobs
            )
        await session.commit()
    listing = (await client.get(f"/api/v1/files/{root_id}/children", headers=headers)).json()
    url = await link(client, headers, *[c["id"] for c in listing["children"]])
    # httpx (ASGITransport) leest alles in: hier de body van de StreamingResponse zelf, één
    # stuk, en dan weg (zoals Starlette doet als de verbinding wegvalt)
    token = url.split("token=")[1]
    async with maker(settings)() as session:
        response = await archive.archive(token, session, settings)
    body = response.body_iterator
    assert (await anext(body))[:2] == b"PK"
    await body.aclose()
    assert archive._tasks, "de schrijver loopt nog (het stuk was er voordat hij klaar was)"
    with anyio.fail_after(5):
        await asyncio.gather(*archive._tasks)
    assert archive.writers == 0


@pytest.mark.anyio
async def test_tienduizend_ids_in_drie_queries(client, admin, settings):
    headers, _ = admin
    async with maker(settings)() as session:
        jonas = await user_by_email(session, "jonas@example.nl")
        folder = Node(parent_id=jonas.root_id, owner_id=jonas.id, name="Veel", kind="dir")
        session.add(folder)
        await session.flush()
        rows = [
            {
                "id": uuid.uuid4(),
                "parent_id": folder.id,
                "owner_id": jonas.id,
                "name": f"{i}.txt",
                "search_name": fold(f"{i}.txt"),
                "kind": "file",
                "size": 0,
            }
            for i in range(archive.MAX_IDS)
        ]
        await session.execute(insert(Node), rows)
        await session.commit()
    statements = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    engine = get_engine(settings).sync_engine
    async with maker(settings)() as session:
        jonas = await user_by_email(session, "jonas@example.nl")
        event.listen(engine, "before_cursor_execute", count)
        try:
            tops = await archive.checked(session, jonas, [r["id"] for r in rows])
        finally:
            event.remove(engine, "before_cursor_execute", count)
    assert len(tops) == archive.MAX_IDS
    assert len(statements) <= 3, statements
    url = await link(client, headers, *[str(r["id"]) for r in rows[:10]])
    assert len((await download(client, url)).namelist()) == 0  # geen inhoud op schijf: overgeslagen
