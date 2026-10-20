import asyncio
import base64
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import storage
from app.db import get_engine
from app.models import Blob, Upload
from app.routers import files, uploads
from app.storage import BlobStore

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

    # Naam bezet en conflict=fail: meteen nee, niet pas als alle bytes binnen zijn
    r = await create(client, headers, parent, "a.txt", 3, conflict="fail")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "name_taken"


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
    # Bestanden zonder upload-rij (bijv. weg met hun map): na een uur weg, nieuwe blijven staan
    stray = settings.upload_dir / "verdwaald"
    stray.write_bytes(b"x")
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(stray, (old, old))
    fresh = settings.upload_dir / "net-aangemaakt"
    fresh.write_bytes(b"x")
    assert await uploads.remove_expired(settings) == 2
    assert list(settings.upload_dir.iterdir()) == [fresh]
    assert (await client.head(location, headers={**headers, **TUS})).status_code == 404


def blob_files(settings):
    return [p for p in settings.blob_dir.rglob("*") if p.is_file() and ".tmp" not in p.parts]


def upload_file(settings, location):
    return uploads.upload_path(settings, uuid.UUID(location.rsplit("/", 1)[1]))


async def put(client, headers, parent, name, data):
    return await client.put(
        f"/api/v1/files/{parent}/upload", params={"name": name}, content=data, headers=headers
    )


async def new_folder(client, headers, parent, name):
    r = await client.post(f"/api/v1/files/{parent}/folders", json={"name": name}, headers=headers)
    return r.json()["id"]


async def purge(client, headers, node_id):
    """Naar de prullenbak en daar definitief weg."""
    await client.delete(f"/api/v1/files/{node_id}", headers=headers)
    return await client.delete(f"/api/v1/trash/{node_id}", headers=headers)


def hold(monkeypatch, module, name):
    """Laat de eerstvolgende aanroep van module.name wachten tot `release` gezet is, zodat
    een test er iets tussen kan doen. Geeft (entered, release)."""
    entered, release = asyncio.Event(), asyncio.Event()
    real = getattr(module, name)

    async def held(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(module, name, held)
    return entered, release


@pytest.mark.anyio
async def test_naam_bezet_aan_het_eind(client, admin, settings):
    headers, _ = admin
    parent = await root_id(client, headers)
    r = await create(client, headers, parent, "b.txt", 3, conflict="fail")
    location = r.headers["location"]
    # Terwijl de upload loopt, komt er toch een b.txt
    assert (await put(client, headers, parent, "b.txt", b"eerst")).status_code == 201
    r = await send(client, headers, location, 0, b"nee")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "name_taken"
    # De upload is weg: opnieuw proberen heeft geen zin
    assert (await client.head(location, headers={**headers, **TUS})).status_code == 404
    assert not upload_file(settings, location).exists()
    assert len(blob_files(settings)) == 1


@pytest.mark.anyio
async def test_map_weg_dan_nooit_vervangen_in_hoofdmap(client, admin):
    headers, _ = admin
    parent = await root_id(client, headers)
    original = (await put(client, headers, parent, "rapport.pdf", b"ROOT-ORIGINAL")).json()["id"]
    folder = await new_folder(client, headers, parent, "m")
    r = await create(client, headers, folder, "rapport.pdf", 3, conflict="replace")
    replace = r.headers["location"]
    r = await create(client, headers, folder, "rapport.pdf", 4, conflict="fail")
    fail = r.headers["location"]
    await client.delete(f"/api/v1/files/{folder}", headers=headers)

    # Vervangen en weigeren golden voor map m; in de hoofdmap komt er een nieuwe naam
    r = await send(client, headers, replace, 0, b"new")
    assert r.status_code == 204
    assert r.headers["platform-node-id"] != original
    assert (await send(client, headers, fail, 0, b"ook!")).status_code == 204
    assert await content(client, headers, original) == b"ROOT-ORIGINAL"
    children = (await client.get("/api/v1/files/root", headers=headers)).json()["children"]
    names = sorted(c["name"] for c in children)
    assert names == ["rapport (1).pdf", "rapport (2).pdf", "rapport.pdf"]


@pytest.mark.anyio
async def test_stilgevallen_patch_geeft_slot_en_verbinding_vrij(
    client, admin, settings, monkeypatch
):
    monkeypatch.setattr(storage, "CHUNK_TIMEOUT", 1.0)
    headers, _ = admin
    parent = await root_id(client, headers)
    data = os.urandom(1000)
    location = (await create(client, headers, parent, "stil.bin", len(data))).headers["location"]
    stalled = asyncio.Event()

    async def body():
        yield data[:100]
        stalled.set()
        await asyncio.Event().wait()  # valt stil zonder de verbinding te sluiten

    with anyio.fail_after(5):
        stalling = asyncio.create_task(
            client.patch(
                location, content=body(), headers={**headers, **PATCH, "Upload-Offset": "0"}
            )
        )
        await stalled.wait()
        # Terwijl hij wacht: geen databaseverbinding bezet, en een tweede PATCH hoort dat er
        # al een verzoek loopt
        assert get_engine(settings).pool.checkedout() == 0
        r = await send(client, headers, location, 100, data[100:])
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "upload_busy"
        assert (await client.head(location, headers={**headers, **TUS})).status_code == 200

        # Na de time-out telt het als een verbroken verbinding: wat binnen is blijft staan
        r = await stalling
        assert r.status_code == 204
        assert r.headers["upload-offset"] == "100"
        r = await client.head(location, headers={**headers, **TUS})
        assert r.headers["upload-offset"] == "100"
        r = await send(client, headers, location, 100, data[100:])
    assert r.status_code == 204
    assert await content(client, headers, r.headers["platform-node-id"]) == data


@pytest.mark.anyio
async def test_head_rondt_volledige_upload_af(client, admin, settings):
    headers, _ = admin
    parent = await root_id(client, headers)
    location = (await create(client, headers, parent, "klaar.txt", 5)).headers["location"]
    # Alle bytes staan er, maar de server stopte voordat het bestand gemaakt was
    upload_file(settings, location).write_bytes(b"klaar")
    r = await client.head(location, headers={**headers, **TUS})
    assert r.status_code == 200
    assert r.headers["upload-offset"] == "5"
    node_id = r.headers["platform-node-id"]
    assert await content(client, headers, node_id) == b"klaar"
    # Nog eens vragen geeft hetzelfde bestand, geen kopie
    r = await client.head(location, headers={**headers, **TUS})
    assert r.headers["platform-node-id"] == node_id
    children = (await client.get("/api/v1/files/root", headers=headers)).json()["children"]
    assert [c["name"] for c in children] == ["klaar.txt"]


@pytest.mark.parametrize("state", ["nieuw", "dedupe", "inhoud-kwijt"])
@pytest.mark.anyio
async def test_fout_na_adopt_laat_niets_achter(client, admin, settings, monkeypatch, state):
    headers, _ = admin
    parent = await root_id(client, headers)
    if state != "nieuw":  # dezelfde inhoud staat er al
        await put(client, headers, parent, "al", b"qwerty")
    if state == "inhoud-kwijt":  # wel een blob-rij, maar het bestand ontbreekt (oude fout)
        for path in blob_files(settings):
            path.unlink()
    location = (await create(client, headers, parent, "z.bin", 6)).headers["location"]

    # De commit na het verplaatsen naar de blob-opslag mislukt (bijv. database bezet)
    real_adopt, real_commit = BlobStore.adopt, AsyncSession.commit
    armed = False

    async def adopt(self, path, sha):
        nonlocal armed
        armed = True
        return await real_adopt(self, path, sha)

    async def commit(self):
        nonlocal armed
        if armed:
            armed = False
            raise RuntimeError("database is locked")
        return await real_commit(self)

    monkeypatch.setattr(BlobStore, "adopt", adopt)
    monkeypatch.setattr(AsyncSession, "commit", commit)
    with pytest.raises(RuntimeError):
        await send(client, headers, location, 0, b"qwerty")
    monkeypatch.undo()

    # Geen blob zonder rij, en de upload staat er nog helemaal
    assert len(blob_files(settings)) == (1 if state == "dedupe" else 0)
    assert upload_file(settings, location).read_bytes() == b"qwerty"
    r = await client.head(location, headers={**headers, **TUS})
    assert r.status_code == 200
    assert await content(client, headers, r.headers["platform-node-id"]) == b"qwerty"
    assert len(blob_files(settings)) == 1


@pytest.mark.anyio
async def test_afbreken_wacht_op_afronden(client, admin, settings, monkeypatch):
    headers, _ = admin
    parent = await root_id(client, headers)
    location = (await create(client, headers, parent, "z.bin", 6)).headers["location"]
    entered, release = hold(monkeypatch, uploads, "room_left")  # midden in finish()

    with anyio.fail_after(5):
        finishing = asyncio.create_task(send(client, headers, location, 0, b"qwerty"))
        await entered.wait()
        cancel = asyncio.create_task(client.delete(location, headers={**headers, **TUS}))
        await asyncio.sleep(0.05)
        assert not cancel.done()  # wacht tot het afronden klaar is
        release.set()
        r, d = await finishing, await cancel
    assert r.status_code == 204 and d.status_code == 204
    # Afgerond is afgerond: het bestand blijft, met inhoud
    assert await content(client, headers, r.headers["platform-node-id"]) == b"qwerty"
    assert len(blob_files(settings)) == 1


@pytest.mark.anyio
async def test_head_tijdens_afronden_wacht_op_uitkomst(client, admin, monkeypatch):
    headers, _ = admin
    parent = await root_id(client, headers)
    location = (await create(client, headers, parent, "z.bin", 6)).headers["location"]
    real = BlobStore.adopt
    moved, release = asyncio.Event(), asyncio.Event()

    async def adopt(self, path, sha):
        result = await real(self, path, sha)
        moved.set()  # verplaatst naar de blob-opslag, nog niet vastgelegd
        await release.wait()
        return result

    monkeypatch.setattr(BlobStore, "adopt", adopt)
    with anyio.fail_after(5):
        finishing = asyncio.create_task(send(client, headers, location, 0, b"qwerty"))
        await moved.wait()
        status = asyncio.create_task(client.head(location, headers={**headers, **TUS}))
        await asyncio.sleep(0.05)
        release.set()
        r, h = await finishing, await status
    # Niet ten onrechte 404 (de client zou dan zeggen dat de upload verlopen is)
    assert h.status_code == 200
    assert h.headers["platform-node-id"] == r.headers["platform-node-id"]


@pytest.mark.anyio
async def test_opruimen_wacht_op_slot(client, admin, settings):
    headers, _ = admin
    parent = await root_id(client, headers)
    location = (await create(client, headers, parent, "x.bin", 4)).headers["location"]
    maker = async_sessionmaker(get_engine(settings))
    async with maker() as session:
        await session.execute(
            update(Upload).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
    lock = uploads._locks.setdefault(uuid.UUID(location.rsplit("/", 1)[1]), asyncio.Lock())
    with anyio.fail_after(5):
        async with lock:  # er loopt een verzoek voor deze upload
            cleanup = asyncio.create_task(uploads.remove_expired(settings))
            await asyncio.sleep(0.05)
            assert not cleanup.done()
        assert await cleanup == 1
    assert (await client.head(location, headers={**headers, **TUS})).status_code == 404


@pytest.mark.anyio
async def test_opruimen_wacht_niet_eindeloos(client, admin, settings, monkeypatch):
    monkeypatch.setattr(uploads, "CLEANUP_WAIT", 0.1)
    headers, _ = admin
    parent = await root_id(client, headers)
    busy = (await create(client, headers, parent, "lang.bin", 4)).headers["location"]
    other = (await create(client, headers, parent, "ander.bin", 4)).headers["location"]
    maker = async_sessionmaker(get_engine(settings))
    async with maker() as session:
        await session.execute(
            update(Upload).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
    lock = uploads._lock(uuid.UUID(busy.rsplit("/", 1)[1]))
    with anyio.fail_after(3):
        async with lock:  # een PATCH die uren duurt (groot bestand in één keer)
            # De andere upload wacht daar niet op; deze komt de volgende ronde aan de beurt
            assert await uploads.remove_expired(settings) == 1
            assert (await client.head(other, headers={**headers, **TUS})).status_code == 404
            assert (await client.head(busy, headers={**headers, **TUS})).status_code == 200
        assert await uploads.remove_expired(settings) == 1
    assert (await client.head(busy, headers={**headers, **TUS})).status_code == 404


@pytest.mark.anyio
async def test_quotum_gelijktijdig_aanmaken(client, admin, monkeypatch):
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
    real = uploads.room_left

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.01)  # zodat beide verzoeken tegelijk controleren
        return await real(*args, **kwargs)

    monkeypatch.setattr(uploads, "room_left", slow)
    r1, r2 = await asyncio.gather(
        create(client, q, parent, "a", 60), create(client, q, parent, "b", 60)
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 413]


@pytest.mark.anyio
async def test_map_definitief_weg_tijdens_patch(client, admin, settings):
    headers, _ = admin
    parent = await root_id(client, headers)
    folder = await new_folder(client, headers, parent, "m")
    location = (await create(client, headers, folder, "x.bin", 4)).headers["location"]
    sent, go = asyncio.Event(), asyncio.Event()

    async def body():
        yield b"ab"
        sent.set()
        await go.wait()

    with anyio.fail_after(5):
        patching = asyncio.create_task(
            client.patch(
                location, content=body(), headers={**headers, **PATCH, "Upload-Offset": "0"}
            )
        )
        await sent.wait()
        assert (await purge(client, headers, folder)).status_code == 204
        go.set()
        r = await patching
    assert r.status_code == 404
    assert not upload_file(settings, location).exists()
    assert (await client.head(location, headers={**headers, **TUS})).status_code == 404


@pytest.mark.anyio
async def test_map_definitief_weg_tijdens_afronden(client, admin, settings, monkeypatch):
    headers, _ = admin
    parent = await root_id(client, headers)
    folder = await new_folder(client, headers, parent, "m")
    location = (await create(client, headers, folder, "x.bin", 4)).headers["location"]
    entered, release = hold(monkeypatch, uploads, "room_left")  # midden in finish()

    with anyio.fail_after(5):
        finishing = asyncio.create_task(send(client, headers, location, 0, b"abcd"))
        await entered.wait()
        assert (await purge(client, headers, folder)).status_code == 204
        release.set()
        r = await finishing
    assert r.status_code == 404
    assert not upload_file(settings, location).exists()
    assert (await client.get("/api/v1/files/root", headers=headers)).json()["children"] == []
    assert blob_files(settings) == []


@pytest.mark.parametrize("via", ["tus", "put"])
@pytest.mark.anyio
async def test_ontdubbelen_tegelijk_met_opruimen(client, admin, settings, monkeypatch, via):
    """Dezelfde inhoud komt binnen terwijl de enige andere kopie definitief wordt verwijderd."""
    headers, _ = admin
    parent = await root_id(client, headers)
    old = (await put(client, headers, parent, "old.txt", b"SAME")).json()["id"]
    await client.delete(f"/api/v1/files/{old}", headers=headers)
    if via == "tus":
        location = (await create(client, headers, parent, "new.txt", 4)).headers["location"]
        upload = send(client, headers, location, 0, b"SAME")
    else:
        upload = put(client, headers, parent, "new.txt", b"SAME")
    entered, release = hold(monkeypatch, files, "sibling")  # na het ontdubbelen, voor de commit

    with anyio.fail_after(5):
        uploading = asyncio.create_task(upload)
        await entered.wait()
        purging = asyncio.create_task(client.delete(f"/api/v1/trash/{old}", headers=headers))
        await asyncio.sleep(0.05)
        release.set()
        r, p = await uploading, await purging
    assert r.status_code in (201, 204) and p.status_code == 204
    node_id = r.json()["id"] if via == "put" else r.headers["platform-node-id"]
    assert await content(client, headers, node_id) == b"SAME"
    assert len(blob_files(settings)) == 1


@pytest.mark.parametrize("via", ["tus", "put"])
@pytest.mark.anyio
async def test_ontdubbelen_tijdens_opruimen_van_inhoud(client, admin, settings, monkeypatch, via):
    """Het opruimen heeft de blob-rij al weggehaald maar het bestand nog niet, en dan komt
    dezelfde inhoud binnen: die mag zijn kopie niet weggooien omdat het oude bestand er nog
    staat."""
    headers, _ = admin
    parent = await root_id(client, headers)
    old = (await put(client, headers, parent, "old.txt", b"SAME")).json()["id"]
    await client.delete(f"/api/v1/files/{old}", headers=headers)
    if via == "tus":
        location = (await create(client, headers, parent, "new.txt", 4)).headers["location"]
        upload = send(client, headers, location, 0, b"SAME")
    else:
        upload = put(client, headers, parent, "new.txt", b"SAME")
    entered, release = hold(monkeypatch, BlobStore, "delete")  # rij weg, bestand nog niet

    with anyio.fail_after(5):
        purging = asyncio.create_task(client.delete(f"/api/v1/trash/{old}", headers=headers))
        await entered.wait()
        uploading = asyncio.create_task(upload)
        await asyncio.sleep(0.05)
        release.set()
        p, r = await purging, await uploading
    assert p.status_code == 204 and r.status_code in (201, 204)
    node_id = r.json()["id"] if via == "put" else r.headers["platform-node-id"]
    assert await content(client, headers, node_id) == b"SAME"
    assert len(blob_files(settings)) == 1


@pytest.mark.anyio
async def test_wachten_op_slot_zonder_databaseverbinding(client, admin, settings):
    """Wie op het blob-slot (of het quotumslot) wacht, houdt geen databaseverbinding vast;
    anders kan wie het slot heeft er geen meer krijgen als er genoeg wachten."""
    headers, _ = admin
    parent = await root_id(client, headers)
    user_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"])
    old = (await put(client, headers, parent, "old.txt", b"oud")).json()["id"]
    await client.delete(f"/api/v1/files/{old}", headers=headers)
    location = (await create(client, headers, parent, "tus.txt", 3)).headers["location"]
    blob_lock = BlobStore(settings.blob_dir).lock
    user_lock = uploads._user_locks.setdefault(user_id, asyncio.Lock())

    with anyio.fail_after(5):
        async with blob_lock, user_lock:
            waiting = [
                asyncio.create_task(send(client, headers, location, 0, b"tus")),
                asyncio.create_task(client.delete(f"/api/v1/trash/{old}", headers=headers)),
                asyncio.create_task(put(client, headers, parent, "put.txt", b"put")),
                asyncio.create_task(create(client, headers, parent, "nieuw.txt", 1)),
            ]
            await asyncio.sleep(0.1)
            assert not any(t.done() for t in waiting)
            assert get_engine(settings).pool.checkedout() == 0
        r = await asyncio.gather(*waiting)
    assert [x.status_code for x in r] == [204, 204, 201, 201]


@pytest.mark.anyio
async def test_tegelijk_twee_keer_definitief_verwijderen(client, admin, settings, monkeypatch):
    headers, _ = admin
    parent = await root_id(client, headers)
    await put(client, headers, parent, "blijft.txt", b"SAME")
    old = (await put(client, headers, parent, "weg.txt", b"SAME")).json()["id"]
    await client.delete(f"/api/v1/files/{old}", headers=headers)
    entered, release = hold(monkeypatch, files, "descendants")  # de eerste is bezig

    with anyio.fail_after(5):
        first = asyncio.create_task(client.delete(f"/api/v1/trash/{old}", headers=headers))
        await entered.wait()
        second = asyncio.create_task(client.delete(f"/api/v1/trash/{old}", headers=headers))
        await asyncio.sleep(0.05)
        release.set()
        a, b = await first, await second
    # De tweede leest pas onder het slot, en ziet dat het al weg is
    assert (a.status_code, b.status_code) == (204, 404)
    async with async_sessionmaker(get_engine(settings))() as session:
        assert (await session.scalars(select(Blob.ref_count))).all() == [1]
