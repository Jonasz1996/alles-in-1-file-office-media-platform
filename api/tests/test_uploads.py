import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_engine
from app.models import Upload
from app.routers import uploads

TUS = {"Tus-Resumable": "1.0.0"}
PATCH = {**TUS, "Content-Type": "application/offset+octet-stream"}


def meta(**values: str) -> str:
    return ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in values.items())


async def root_id(client, headers):
    return (await client.get("/api/v1/files/root", headers=headers)).json()["folder"]["id"]


async def create(client, headers, parent, name, size, **extra):
    return await client.post(
        "/api/v1/tus",
        headers={
            **headers,
            **TUS,
            "Upload-Length": str(size),
            "Upload-Metadata": meta(filename=name, parent=parent, **extra),
        },
    )


async def send(client, headers, location, offset, data):
    return await client.patch(
        location, content=data, headers={**headers, **PATCH, "Upload-Offset": str(offset)}
    )


async def content(client, headers, node_id):
    return (await client.get(f"/api/v1/files/{node_id}/content", headers=headers)).content


@pytest.mark.anyio
async def test_options(client):
    r = await client.options("/api/v1/tus")
    assert r.status_code == 204
    assert r.headers["tus-version"] == "1.0.0"
    assert "creation" in r.headers["tus-extension"]


@pytest.mark.anyio
async def test_upload_in_stukken_en_hervatten(client, admin):
    headers, _ = admin
    parent = await root_id(client, headers)
    data = bytes(range(256)) * 4096  # 1 MiB
    r = await create(client, headers, parent, "groot.bin", len(data))
    assert r.status_code == 201, r.text
    location = r.headers["location"]
    assert "upload-expires" in r.headers

    r = await client.head(location, headers={**headers, **TUS})
    assert r.headers["upload-offset"] == "0"
    assert r.headers["upload-length"] == str(len(data))

    r = await send(client, headers, location, 0, data[:300_000])
    assert r.status_code == 204
    assert r.headers["upload-offset"] == "300000"
    assert "platform-node-id" not in r.headers

    # Verkeerde offset: 409 met de echte offset
    r = await send(client, headers, location, 0, data[:10])
    assert r.status_code == 409
    assert r.headers["upload-offset"] == "300000"

    # Alsof de server opnieuw is gestart: de hash tot nu toe is weg
    uploads._hashers.clear()
    r = await send(client, headers, location, 300_000, data[300_000:])
    assert r.status_code == 204
    node_id = r.headers["platform-node-id"]
    assert await content(client, headers, node_id) == data
    info = (await client.get(f"/api/v1/files/{node_id}", headers=headers)).json()
    assert info["etag"] == f'"{hashlib.sha256(data).hexdigest()}"'
    assert info["name"] == "groot.bin"

    # Antwoord gemist? HEAD en dezelfde PATCH nog eens geven hetzelfde bestand, geen kopie
    r = await client.head(location, headers={**headers, **TUS})
    assert r.headers["upload-offset"] == str(len(data))
    assert r.headers["platform-node-id"] == node_id
    r = await send(client, headers, location, len(data), b"")
    assert r.status_code == 204 and r.headers["platform-node-id"] == node_id
    children = (await client.get("/api/v1/files/root", headers=headers)).json()["children"]
    assert [c["name"] for c in children] == ["groot.bin"]


@pytest.mark.anyio
async def test_dedupe_en_naamconflict(client, admin, settings):
    headers, _ = admin
    parent = await root_id(client, headers)
    for _ in range(2):
        r = await create(client, headers, parent, "a.txt", 5)
        r = await send(client, headers, r.headers["location"], 0, b"hallo")
        assert r.status_code == 204
    names = [
        c["name"]
        for c in (await client.get("/api/v1/files/root", headers=headers)).json()["children"]
    ]
    assert names == ["a (1).txt", "a.txt"]
    blobs = [p for p in settings.blob_dir.rglob("*") if p.is_file() and ".tmp" not in p.parts]
    assert len(blobs) == 1

    r = await create(client, headers, parent, "a.txt", 3, conflict="fail")
    r = await send(client, headers, r.headers["location"], 0, b"nee")
    assert r.status_code == 409


@pytest.mark.anyio
async def test_leeg_bestand(client, admin):
    headers, _ = admin
    parent = await root_id(client, headers)
    r = await create(client, headers, parent, "leeg.txt", 0)
    assert r.status_code == 201
    assert await content(client, headers, r.headers["platform-node-id"]) == b""


@pytest.mark.anyio
async def test_te_veel_bytes_en_verkeerd_type(client, admin):
    headers, _ = admin
    parent = await root_id(client, headers)
    location = (await create(client, headers, parent, "x.bin", 4)).headers["location"]
    r = await send(client, headers, location, 0, b"123456")
    assert r.status_code == 413
    r = await client.patch(
        location,
        content=b"1",
        headers={**headers, **TUS, "Upload-Offset": "0", "Content-Type": "text/plain"},
    )
    assert r.status_code == 415


@pytest.mark.anyio
async def test_versie_en_metadata(client, admin):
    headers, _ = admin
    parent = await root_id(client, headers)
    r = await client.post(
        "/api/v1/tus",
        headers={
            **headers,
            "Upload-Length": "1",
            "Upload-Metadata": meta(filename="a", parent=parent),
        },
    )
    assert r.status_code == 412
    r = await client.post(
        "/api/v1/tus",
        headers={**headers, **TUS, "Upload-Length": "1", "Upload-Metadata": "filename !!!"},
    )
    assert r.status_code == 400
    r = await create(client, headers, parent, "../a", 1)
    assert r.status_code == 422
    r = await create(client, headers, "geen-uuid", "a", 1)
    assert r.status_code == 400
    r = await create(client, headers, parent, "a", 1, conflict="weg")
    assert r.status_code == 400


@pytest.mark.anyio
async def test_quotum_telt_lopende_uploads_mee(client, admin):
    headers, _ = admin
    await client.post(
        "/api/v1/users",
        json={
            "email": "q@example.nl",
            "display_name": "Q",
            "password": "lang-genoeg-456",
            "quota_bytes": 100,
        },
        headers=headers,
    )
    r = await client.post(
        "/api/v1/auth/login", json={"email": "q@example.nl", "password": "lang-genoeg-456"}
    )
    q = {"Authorization": f"Bearer {r.json()['access_token']}"}
    parent = await root_id(client, q)
    assert (await create(client, q, parent, "a", 101)).status_code == 413
    assert (await create(client, q, parent, "a", 60)).status_code == 201
    # 60 is gereserveerd: nog 40 over, ook voor een gewone upload
    assert (await create(client, q, parent, "b", 50)).status_code == 413
    r = await client.put(
        f"/api/v1/files/{parent}/upload", params={"name": "c"}, content=b"x" * 50, headers=q
    )
    assert r.status_code == 413
    # Afgerond telt als gebruikt, niet nog eens als gereserveerd
    location = (await create(client, q, parent, "d", 30)).headers["location"]
    assert (await send(client, q, location, 0, b"x" * 30)).status_code == 204
    assert (await create(client, q, parent, "e", 10)).status_code == 201
    assert (await create(client, q, parent, "f", 1)).status_code == 413


@pytest.mark.anyio
async def test_andermans_upload_en_afbreken(client, admin):
    headers, _ = admin
    parent = await root_id(client, headers)
    location = (await create(client, headers, parent, "x.bin", 4)).headers["location"]
    await client.post(
        "/api/v1/users",
        json={"email": "b@example.nl", "display_name": "B", "password": "lang-genoeg-456"},
        headers=headers,
    )
    r = await client.post(
        "/api/v1/auth/login", json={"email": "b@example.nl", "password": "lang-genoeg-456"}
    )
    b = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.head(location, headers={**b, **TUS})).status_code == 404
    assert (await send(client, b, location, 0, b"1234")).status_code == 404
    assert (await create(client, b, parent, "x", 1)).status_code == 404  # map van een ander
    assert (await client.delete(location, headers={**headers, **TUS})).status_code == 204
    assert (await client.head(location, headers={**headers, **TUS})).status_code == 404


@pytest.mark.anyio
async def test_map_weggegooid_dan_in_hoofdmap(client, admin):
    headers, _ = admin
    parent = await root_id(client, headers)
    folder = (
        await client.post(f"/api/v1/files/{parent}/folders", json={"name": "m"}, headers=headers)
    ).json()["id"]
    location = (await create(client, headers, folder, "x.txt", 2)).headers["location"]
    await client.delete(f"/api/v1/files/{folder}", headers=headers)
    r = await send(client, headers, location, 0, b"ok")
    info = (
        await client.get(f"/api/v1/files/{r.headers['platform-node-id']}", headers=headers)
    ).json()
    assert info["parent_id"] == parent


@pytest.mark.anyio
async def test_verlopen_uploads_opruimen(client, admin, settings):
    headers, _ = admin
    parent = await root_id(client, headers)
    location = (await create(client, headers, parent, "x.bin", 4)).headers["location"]
    await send(client, headers, location, 0, b"12")
    maker = async_sessionmaker(get_engine(settings))
    async with maker() as session:
        await session.execute(
            update(Upload).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
    stray = settings.upload_dir / "verdwaald"
    stray.write_bytes(b"x")
    old = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(stray, (old, old))
    assert await uploads.remove_expired(settings) == 2
    assert list(settings.upload_dir.iterdir()) == []
    assert (await client.head(location, headers={**headers, **TUS})).status_code == 404
