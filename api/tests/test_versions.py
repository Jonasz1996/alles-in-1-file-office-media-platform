import asyncio
import hashlib
import uuid

import anyio
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import events
from app.db import get_engine
from app.events import Hub
from app.models import Blob
from app.routers import files
from app.routers.files import MAX_VERSIONS, collect_garbage
from app.storage import BlobStore
from tests.test_files import blob_files, check_refs, mkdir, new_user, put, root
from tests.test_uploads import create, hold, send

PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def replace(client, headers, parent, name, data):
    r = await put(client, headers, parent, name, data, conflict="replace")
    assert r.status_code == 201, r.text
    return r.json()


async def versions(client, headers, node_id):
    r = await client.get(f"/api/v1/files/{node_id}/versions", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def content(client, headers, node_id, **params) -> bytes:
    r = await client.get(f"/api/v1/files/{node_id}/content", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.content


async def version_link(client, headers, node_id, version_id):
    return await client.post(
        f"/api/v1/files/{node_id}/download-link", params={"version": version_id}, headers=headers
    )


@pytest.mark.anyio
async def test_vervangen_bewaart_de_vorige_inhoud(client, admin, settings):
    headers, tokens = admin
    root_id = (await root(client, headers))["folder"]["id"]
    v1 = (await put(client, headers, root_id, "a.txt", b"een")).json()
    assert await versions(client, headers, v1["id"]) == []

    v2 = await replace(client, headers, root_id, "a.txt", b"twee")
    assert v2["id"] == v1["id"] and v2["size"] == 4
    assert v2["modified_at"] > v1["modified_at"]
    [old] = await versions(client, headers, v1["id"])
    assert old["size"] == 3
    assert old["created_at"] == v1["modified_at"]
    assert old["replaced_by"] == {"id": tokens["user"]["id"], "display_name": "Jonas"}
    assert await content(client, headers, v1["id"]) == b"twee"
    assert await content(client, headers, v1["id"], version=old["id"]) == b"een"

    # Dezelfde inhoud nog eens: geen nieuwe versie
    await replace(client, headers, root_id, "a.txt", b"twee")
    assert len(await versions(client, headers, v1["id"])) == 1
    # Ook via tus (hervatbaar)
    location = (await create(client, headers, root_id, "a.txt", 4, conflict="replace")).headers[
        "location"
    ]
    r = await send(client, headers, location, 0, b"drie")
    assert r.headers["platform-node-id"] == v1["id"]
    listed = await versions(client, headers, v1["id"])
    assert [v["size"] for v in listed] == [4, 3]  # nieuwste eerst
    assert listed[0]["replaced_at"] >= listed[1]["replaced_at"]
    await check_refs(settings)


@pytest.mark.anyio
async def test_hooguit_twintig_versies(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "log.txt", b"versie 0")).json()
    for i in range(1, MAX_VERSIONS + 3):
        await replace(client, headers, root_id, "log.txt", f"versie {i}".encode())
    listed = await versions(client, headers, node["id"])
    assert len(listed) == MAX_VERSIONS
    # De oudste twee zijn weg, ook van schijf
    kept = [await content(client, headers, node["id"], version=v["id"]) for v in listed]
    assert kept[-1] == b"versie 2" and kept[0] == f"versie {MAX_VERSIONS + 1}".encode()
    assert sha(b"versie 0") not in blob_files(settings)
    assert sha(b"versie 1") not in blob_files(settings)
    assert len(await check_refs(settings)) == MAX_VERSIONS + 1


@pytest.mark.anyio
async def test_versie_terugzetten(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "logo", PNG)).json()
    assert node["mime"] == "image/png"
    await replace(client, headers, root_id, "logo", b"nu tekst")
    await replace(client, headers, root_id, "logo", b"nu andere tekst")
    v_tekst, v_png = await versions(client, headers, node["id"])

    r = await client.post(
        f"/api/v1/files/{node['id']}/versions/{v_png['id']}/restore", headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["mime"] == "image/png" and r.json()["size"] == len(PNG)
    assert await content(client, headers, node["id"]) == PNG
    # Wat erin stond is nu zelf een versie; de teruggezette versie is weg
    listed = await versions(client, headers, node["id"])
    assert [await content(client, headers, node["id"], version=v["id"]) for v in listed] == [
        b"nu andere tekst",
        b"nu tekst",
    ]
    assert listed[1]["id"] == v_tekst["id"]
    await check_refs(settings)

    # Andermans of onbekende versie
    other = (await put(client, headers, root_id, "ander", b"x")).json()["id"]
    r = await client.post(
        f"/api/v1/files/{other}/versions/{v_tekst['id']}/restore", headers=headers
    )
    assert r.status_code == 404
    r = await client.post(
        f"/api/v1/files/{node['id']}/versions/{uuid.uuid4()}/restore", headers=headers
    )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_versie_verwijderen(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "a.txt", b"oud")).json()
    await replace(client, headers, root_id, "a.txt", b"nieuw")
    [old] = await versions(client, headers, node["id"])
    url = f"/api/v1/files/{node['id']}/versions/{old['id']}"
    assert (await client.delete(url, headers=headers)).status_code == 204
    assert (await client.delete(url, headers=headers)).status_code == 404
    assert await versions(client, headers, node["id"]) == []
    assert sha(b"oud") not in blob_files(settings)
    await check_refs(settings)


@pytest.mark.anyio
async def test_versie_downloaden(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = (await put(client, headers, root_id, "a.html", b"<p>oud</p>")).json()
    await replace(client, headers, root_id, "a.html", b"<p>nieuw</p>")
    [old] = await versions(client, headers, a["id"])
    b = (await put(client, headers, root_id, "b.txt", b"b")).json()

    link = (await version_link(client, headers, a["id"], old["id"])).json()
    r = await client.get(link["url"])
    assert r.content == b"<p>oud</p>"
    assert r.headers["etag"] == f'"{sha(b"<p>oud</p>")}"'
    assert r.headers["content-security-policy"] == "sandbox"
    r = await client.get(link["url"] + "&inline=true")
    assert "inline" in r.headers["content-disposition"]
    # De versie zit in het ondertekende token; een losse parameter verandert niets
    current = (await client.post(f"/api/v1/files/{a['id']}/download-link", headers=headers)).json()
    r = await client.get(current["url"] + f"&version={old['id']}")
    assert r.content == b"<p>nieuw</p>"
    # Een versie van een ander bestand: geen link
    assert (await version_link(client, headers, b["id"], old["id"])).status_code == 404
    assert (await version_link(client, headers, a["id"], uuid.uuid4())).status_code == 404
    r = await client.get(f"/api/v1/files/{b['id']}/content", params={"version": old["id"]})
    assert r.status_code == 401
    r = await client.get(
        f"/api/v1/files/{b['id']}/content", params={"version": old["id"]}, headers=headers
    )
    assert r.status_code == 404

    # Weg na het aanmaken van de link: 404, niet de huidige inhoud
    await client.delete(f"/api/v1/files/{a['id']}/versions/{old['id']}", headers=headers)
    assert (await client.get(link["url"])).status_code == 404


@pytest.mark.anyio
async def test_versie_heeft_het_soort_van_haar_eigen_inhoud(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = (await put(client, headers, root_id, "a.html", b"<p>oud</p>")).json()
    now = await replace(client, headers, root_id, "a.html", PNG)
    assert now["mime"] == "image/png"
    [old] = await versions(client, headers, a["id"])
    # Een blob van voor de inhoudsherkenning weet zijn soort niet
    async with AsyncSession(get_engine(settings)) as session:
        await session.execute(
            update(Blob).where(Blob.sha256 == sha(b"<p>oud</p>")).values(mime=None)
        )
        await session.commit()

    link = (await version_link(client, headers, a["id"], old["id"])).json()
    r = await client.get(link["url"])
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == "sandbox"
    r = await client.get(
        f"/api/v1/files/{a['id']}/content", params={"version": old["id"]}, headers=headers
    )
    assert r.headers["content-type"].startswith("text/html")
    # Het huidige bestand blijft gewoon een PNG
    r = await client.get(f"/api/v1/files/{a['id']}/content", headers=headers)
    assert r.headers["content-type"] == "image/png"


@pytest.mark.anyio
async def test_hernoemen_verandert_de_datum_van_een_versie_niet(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    folder = await mkdir(client, headers, root_id, "Map")
    a = (await put(client, headers, root_id, "a.txt", b"een")).json()
    await asyncio.sleep(0.01)
    body = {"name": "b.txt", "parent_id": folder}
    await client.patch(f"/api/v1/files/{a['id']}", json=body, headers=headers)
    await replace(client, headers, folder, "b.txt", b"twee")
    [old] = await versions(client, headers, a["id"])
    # Wanneer die inhoud er kwam, niet wanneer het bestand hernoemd werd
    assert old["created_at"] == a["modified_at"]


@pytest.mark.anyio
async def test_alleen_eigen_bestanden_en_alleen_bestanden(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "a.txt", b"1")).json()
    await replace(client, headers, root_id, "a.txt", b"2")
    [old] = await versions(client, headers, node["id"])
    folder = await mkdir(client, headers, root_id, "Map")
    r = await client.get(f"/api/v1/files/{folder}/versions", headers=headers)
    assert r.status_code == 422

    z = await new_user(client, headers, "z@example.nl")
    for method, url in [
        ("GET", f"/api/v1/files/{node['id']}/versions"),
        ("POST", f"/api/v1/files/{node['id']}/versions/{old['id']}/restore"),
        ("DELETE", f"/api/v1/files/{node['id']}/versions/{old['id']}"),
        ("POST", f"/api/v1/files/{node['id']}/download-link?version={old['id']}"),
    ]:
        assert (await client.request(method, url, headers=z)).status_code == 404, url
    assert len(await versions(client, headers, node["id"])) == 1

    # In de prullenbak: eerst terugzetten
    await client.delete(f"/api/v1/files/{node['id']}", headers=headers)
    r = await client.post(
        f"/api/v1/files/{node['id']}/versions/{old['id']}/restore", headers=headers
    )
    assert r.status_code == 422


@pytest.mark.anyio
async def test_definitief_verwijderen_ruimt_versies_op(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    folder = await mkdir(client, headers, root_id, "Map")
    a = (await put(client, headers, folder, "a.txt", b"a1")).json()
    await replace(client, headers, folder, "a.txt", b"a2")
    await replace(client, headers, folder, "a.txt", b"a3")
    b = (await put(client, headers, root_id, "b.txt", b"b1")).json()
    await replace(client, headers, root_id, "b.txt", b"a1")  # zelfde inhoud als een versie van a
    assert len(await check_refs(settings)) == 4

    await client.delete(f"/api/v1/files/{folder}", headers=headers)
    assert (await client.delete(f"/api/v1/trash/{folder}", headers=headers)).status_code == 204
    # a1 blijft: b wijst er nog naar; b1 als versie van b
    assert set(await check_refs(settings)) == {sha(b"a1"), sha(b"b1")}
    await client.delete(f"/api/v1/files/{b['id']}", headers=headers)
    await client.delete(f"/api/v1/trash/{b['id']}", headers=headers)
    assert await check_refs(settings) == {}
    assert a["id"] != b["id"]


@pytest.mark.anyio
async def test_opruimen_laat_inhoud_van_versies_staan(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "a.txt", b"oud")).json()
    await replace(client, headers, root_id, "a.txt", b"nieuw")
    blobs = BlobStore(settings.blob_dir)
    async with async_sessionmaker(get_engine(settings), expire_on_commit=False)() as session:
        # ref_count klopt niet meer (0) terwijl de versie er nog naar wijst
        await session.execute(update(Blob).where(Blob.sha256 == sha(b"oud")).values(ref_count=0))
        await session.commit()
        await collect_garbage(session, blobs)
    assert blobs.path(sha(b"oud")).exists()
    [old] = await versions(client, headers, node["id"])
    assert await content(client, headers, node["id"], version=old["id"]) == b"oud"


@pytest.mark.anyio
async def test_quotum_telt_versies_mee(client, admin):
    headers, _ = admin
    k = await new_user(client, headers, "k@example.nl", quota_bytes=10)
    kroot = (await root(client, k))["folder"]["id"]
    node = (await put(client, k, kroot, "a", b"1234")).json()
    await replace(client, k, kroot, "a", b"5678")
    me = (await client.get("/api/v1/auth/me", headers=k)).json()
    assert me["used_bytes"] == 8  # de huidige inhoud plus de versie
    assert (await put(client, k, kroot, "a", b"abcd", conflict="replace")).status_code == 413
    [old] = await versions(client, k, node["id"])
    await client.delete(f"/api/v1/files/{node['id']}/versions/{old['id']}", headers=k)
    assert (await client.get("/api/v1/auth/me", headers=k)).json()["used_bytes"] == 4
    assert (await put(client, k, kroot, "a", b"abcd", conflict="replace")).status_code == 201


@pytest.mark.anyio
async def test_twee_keer_tegelijk_vervangen(client, admin, settings, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "a.txt", b"origineel")).json()
    entered, release = hold(monkeypatch, files, "keep_version")  # de eerste is halverwege

    with anyio.fail_after(5):
        first = asyncio.create_task(replace(client, headers, root_id, "a.txt", b"eerste"))
        await entered.wait()
        second = asyncio.create_task(replace(client, headers, root_id, "a.txt", b"tweede"))
        await asyncio.sleep(0.05)
        assert not second.done()  # wacht op het blob-slot
        release.set()
        await first, await second
    # Geen versie kwijt: de tweede ziet de inhoud van de eerste als de huidige
    listed = await versions(client, headers, node["id"])
    assert [await content(client, headers, node["id"], version=v["id"]) for v in listed] == [
        b"eerste",
        b"origineel",
    ]
    assert await content(client, headers, node["id"]) == b"tweede"
    await check_refs(settings)


@pytest.mark.anyio
async def test_terugzetten_tegelijk_met_vervangen(client, admin, settings, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "a.txt", b"een")).json()
    await replace(client, headers, root_id, "a.txt", b"twee")
    [een] = await versions(client, headers, node["id"])
    entered, release = hold(monkeypatch, files, "get_version")  # terugzetten heeft het slot

    with anyio.fail_after(5):
        restoring = asyncio.create_task(
            client.post(f"/api/v1/files/{node['id']}/versions/{een['id']}/restore", headers=headers)
        )
        await entered.wait()
        replacing = asyncio.create_task(replace(client, headers, root_id, "a.txt", b"drie"))
        await asyncio.sleep(0.05)
        release.set()
        r, _ = await restoring, await replacing
    assert r.status_code == 200
    assert await content(client, headers, node["id"]) == b"drie"
    listed = await versions(client, headers, node["id"])
    assert sorted(
        [await content(client, headers, node["id"], version=v["id"]) for v in listed]
    ) == [
        b"een",
        b"twee",
    ]
    await check_refs(settings)


@pytest.mark.anyio
async def test_mislukt_vervangen_verandert_niets(client, admin, settings, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "a.txt", b"blijft")).json()
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

    # De commit na het vastleggen van de versie mislukt (database bezet, server stopt)
    monkeypatch.setattr(BlobStore, "adopt", adopt)
    monkeypatch.setattr(AsyncSession, "commit", commit)
    with pytest.raises(RuntimeError):
        await put(client, headers, root_id, "a.txt", b"nieuw", conflict="replace")
    monkeypatch.undo()

    assert await content(client, headers, node["id"]) == b"blijft"
    assert await versions(client, headers, node["id"]) == []
    await check_refs(settings)
    # Opnieuw proberen lukt gewoon
    await replace(client, headers, root_id, "a.txt", b"nieuw")
    assert len(await versions(client, headers, node["id"])) == 1
    await check_refs(settings)


@pytest.mark.anyio
async def test_gebeurtenissen_bij_vervangen_en_terugzetten(client, admin, monkeypatch):
    headers, tokens = admin
    monkeypatch.setattr(events, "hub", Hub())
    root_id = (await root(client, headers))["folder"]["id"]
    with events.hub.subscribe(uuid.UUID(tokens["user"]["id"])) as sub:
        node = (await put(client, headers, root_id, "a.txt", b"1")).json()
        await replace(client, headers, root_id, "a.txt", b"2")
        [old] = await versions(client, headers, node["id"])
        await client.post(
            f"/api/v1/files/{node['id']}/versions/{old['id']}/restore", headers=headers
        )
        seen = []
        while not sub.queue.empty():
            seen.append(sub.queue.get_nowait())
    data = {"id": node["id"], "parent_id": root_id}
    assert seen == [
        {"type": "node.created", "data": data},
        {"type": "node.changed", "data": data},
        {"type": "node.changed", "data": data},
    ]
