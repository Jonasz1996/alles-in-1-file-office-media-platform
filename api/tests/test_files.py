import asyncio
from collections import Counter

import anyio
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import storage
from app.db import get_engine
from app.models import Blob, Node, Version
from app.routers.files import collect_garbage
from app.storage import BlobStore


async def root(client, headers):
    return (await client.get("/api/v1/files/root", headers=headers)).json()


async def put(client, headers, parent, name, data: bytes, **params):
    return await client.put(
        f"/api/v1/files/{parent}/upload",
        params={"name": name, **params},
        content=data,
        headers=headers,
    )


async def mkdir(client, headers, parent, name) -> str:
    r = await client.post(f"/api/v1/files/{parent}/folders", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def new_user(client, admin_headers, email, quota_bytes=None) -> dict:
    """Maakt een gewone gebruiker aan en logt in; geeft de headers."""
    body = {"email": email, "display_name": email.split("@")[0], "password": "lang-genoeg-123"}
    if quota_bytes is not None:
        body["quota_bytes"] = quota_bytes
    assert (await client.post("/api/v1/users", json=body, headers=admin_headers)).status_code == 201
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": body["password"]})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def blob_files(settings) -> set[str]:
    return {p.name for p in settings.blob_dir.rglob("*") if p.is_file() and ".tmp" not in p.parts}


async def check_refs(settings) -> dict[str, int]:
    """De boekhouding klopt: ref_count van elke blob is het aantal bestanden plus versies dat
    ernaar wijst, er is geen blob zonder verwijzing, en op schijf staat precies wat in de
    database staat. Geeft {sha: ref_count}."""
    async with async_sessionmaker(get_engine(settings))() as session:
        rows = dict((await session.execute(select(Blob.sha256, Blob.ref_count))).all())
        nodes = Counter(
            (await session.scalars(select(Node.blob_id).where(Node.blob_id.is_not(None)))).all()
        )
        versions = Counter((await session.scalars(select(Version.blob_id))).all())
    assert rows == dict(nodes + versions)
    assert blob_files(settings) == set(rows)
    return rows


@pytest.mark.anyio
async def test_mappen_en_upload(client, admin):
    headers, _ = admin
    r = await root(client, headers)
    root_id = r["folder"]["id"]
    assert r["children"] == []

    r = await client.post(
        f"/api/v1/files/{root_id}/folders", json={"name": "Foto's"}, headers=headers
    )
    assert r.status_code == 201
    fotos = r.json()["id"]
    dup = await client.post(
        f"/api/v1/files/{root_id}/folders", json={"name": "foto's"}, headers=headers
    )
    assert dup.status_code == 409

    r = await put(client, headers, fotos, "zee.jpg", b"\xff\xd8\xffjpeg")
    assert r.status_code == 201
    assert r.json()["mime"] == "image/jpeg"
    assert r.json()["size"] == 7

    listing = (await client.get(f"/api/v1/files/{fotos}/children", headers=headers)).json()
    assert [c["name"] for c in listing["children"]] == ["zee.jpg"]
    assert [p["name"] for p in listing["path"]] == ["", "Foto's"]


@pytest.mark.anyio
async def test_ongeldige_namen(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    for name in ["..", "a/b", "a\\b", " "]:
        r = await client.post(
            f"/api/v1/files/{root_id}/folders", json={"name": name}, headers=headers
        )
        assert r.status_code == 422, name


@pytest.mark.anyio
async def test_dedupe_en_naamconflict(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = (await put(client, headers, root_id, "a.txt", b"zelfde")).json()
    b = (await put(client, headers, root_id, "a.txt", b"zelfde")).json()
    assert b["name"] == "a (1).txt"
    assert a["etag"] == b["etag"]
    blobs = [p for p in settings.blob_dir.rglob("*") if p.is_file() and ".tmp" not in p.parts]
    assert len(blobs) == 1
    assert (await put(client, headers, root_id, "a.txt", b"x", conflict="fail")).status_code == 409
    r = await put(client, headers, root_id, "a.txt", b"nieuw", conflict="replace")
    assert r.json()["id"] == a["id"] and r.json()["size"] == 5


@pytest.mark.anyio
async def test_download_range_en_etag(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "tekst.txt", b"0123456789")).json()

    r = await client.get(f"/api/v1/files/{node['id']}/content", headers=headers)
    assert r.content == b"0123456789"
    assert r.headers["etag"] == node["etag"]

    r = await client.get(
        f"/api/v1/files/{node['id']}/content", headers={**headers, "Range": "bytes=2-4"}
    )
    assert r.status_code == 206 and r.content == b"234"

    r = await client.get(
        f"/api/v1/files/{node['id']}/content", headers={**headers, "If-None-Match": node["etag"]}
    )
    assert r.status_code == 304

    link = (await client.post(f"/api/v1/files/{node['id']}/download-link", headers=headers)).json()
    r = await client.get(link["url"])
    assert r.content == b"0123456789"
    assert "attachment" in r.headers["content-disposition"]
    assert (await client.get(f"/api/v1/download/{node['id']}?token=onzin")).status_code == 401


@pytest.mark.anyio
async def test_hernoemen_en_verplaatsen(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = (
        await client.post(f"/api/v1/files/{root_id}/folders", json={"name": "A"}, headers=headers)
    ).json()
    b = (
        await client.post(f"/api/v1/files/{a['id']}/folders", json={"name": "B"}, headers=headers)
    ).json()
    # A in zijn eigen submap B zetten mag niet
    r = await client.patch(f"/api/v1/files/{a['id']}", json={"parent_id": b["id"]}, headers=headers)
    assert r.status_code == 422
    r = await client.patch(
        f"/api/v1/files/{b['id']}", json={"parent_id": root_id, "name": "B2"}, headers=headers
    )
    assert r.json()["parent_id"] == root_id and r.json()["name"] == "B2"
    # Hernoemen en verplaatsen gaat niet over de inhoud: gewijzigd blijft staan
    f = (await put(client, headers, root_id, "f.txt", b"f")).json()
    r = await client.patch(
        f"/api/v1/files/{f['id']}", json={"parent_id": a["id"], "name": "g.txt"}, headers=headers
    )
    assert r.json()["name"] == "g.txt" and r.json()["modified_at"] == f["modified_at"]


@pytest.mark.anyio
async def test_prullenbak_herstellen_en_leegmaken(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    d = (
        await client.post(f"/api/v1/files/{root_id}/folders", json={"name": "D"}, headers=headers)
    ).json()
    f = (await put(client, headers, d["id"], "f.bin", b"inhoud")).json()

    assert (await client.delete(f"/api/v1/files/{d['id']}", headers=headers)).status_code == 204
    assert (await root(client, headers))["children"] == []
    trash = (await client.get("/api/v1/trash", headers=headers)).json()
    assert [t["name"] for t in trash] == ["D"]

    r = await client.post(f"/api/v1/files/{d['id']}/restore", headers=headers)
    assert r.json()["deleted_at"] is None
    assert [c["name"] for c in (await root(client, headers))["children"]] == ["D"]

    await client.delete(f"/api/v1/files/{d['id']}", headers=headers)
    assert (await client.delete(f"/api/v1/trash/{d['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/v1/files/{f['id']}", headers=headers)).status_code == 404
    blobs = [p for p in settings.blob_dir.rglob("*") if p.is_file() and ".tmp" not in p.parts]
    assert blobs == []


@pytest.mark.anyio
async def test_quotum(client, admin):
    headers, _ = admin
    new = {
        "email": "k@example.nl",
        "display_name": "K",
        "password": "lang-genoeg-1",
        "quota_bytes": 10,
    }
    await client.post("/api/v1/users", json=new, headers=headers)
    r = await client.post(
        "/api/v1/auth/login", json={"email": new["email"], "password": new["password"]}
    )
    kh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    kroot = (await root(client, kh))["folder"]["id"]
    assert (await put(client, kh, kroot, "a", b"12345678")).status_code == 201
    assert (await put(client, kh, kroot, "b", b"12345")).status_code == 413


@pytest.mark.anyio
async def test_twee_uploads_tegelijk_niet_over_het_quotum(client, admin, monkeypatch, settings):
    from app.routers import files
    from tests.test_copy import meet_up

    headers, _ = admin
    q = await new_user(client, headers, "q@example.nl", quota_bytes=20)
    qroot = (await root(client, q))["folder"]["id"]
    await put(client, q, qroot, "acht", b"12345678")
    meet_up(monkeypatch, files)
    # Vooraf past elk (8 + 8); als de inhoud binnen is, wordt het nog eens gecontroleerd
    r1, r2 = await asyncio.gather(
        put(client, q, qroot, "een", b"abcdefgh"), put(client, q, qroot, "twee", b"ABCDEFGH")
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 413]
    assert (await client.get("/api/v1/auth/me", headers=q)).json()["used_bytes"] == 16
    await check_refs(settings)


@pytest.mark.anyio
async def test_niets_erbij_onder_een_map_in_de_prullenbak(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, root_id, "Weg")
    sub = await mkdir(client, headers, weg, "Sub")
    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    assert (await put(client, headers, sub, "x.txt", b"x")).status_code == 404
    r = await client.post(f"/api/v1/files/{sub}/folders", json={"name": "N"}, headers=headers)
    assert r.status_code == 404
    assert (await client.get(f"/api/v1/files/{sub}/children", headers=headers)).json()[
        "children"
    ] == []


@pytest.mark.anyio
async def test_upload_terwijl_de_map_definitief_weggaat(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, root_id, "Weg")
    sub = await mkdir(client, headers, weg, "Sub")
    await put(client, headers, sub, "x.txt", b"oud")
    halfway = asyncio.Event()
    go = asyncio.Event()

    async def body():
        yield b"nieuwe "
        halfway.set()
        await go.wait()
        yield b"inhoud"

    with anyio.fail_after(5):
        uploading = asyncio.create_task(
            put(client, headers, sub, "x.txt", body(), conflict="replace")
        )
        await halfway.wait()
        await client.delete(f"/api/v1/files/{weg}", headers=headers)
        assert (await client.delete(f"/api/v1/trash/{weg}", headers=headers)).status_code == 204
        go.set()
        r = await uploading
    assert r.status_code == 404
    assert (await root(client, headers))["children"] == []
    await check_refs(settings)


@pytest.mark.anyio
async def test_andermans_bestanden_onzichtbaar(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "geheim.txt", b"x")).json()
    new = {"email": "z@example.nl", "display_name": "Z", "password": "lang-genoeg-1"}
    await client.post("/api/v1/users", json=new, headers=headers)
    r = await client.post(
        "/api/v1/auth/login", json={"email": new["email"], "password": new["password"]}
    )
    zh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get(f"/api/v1/files/{node['id']}", headers=zh)).status_code == 404
    assert (await client.get(f"/api/v1/files/{node['id']}/content", headers=zh)).status_code == 404
    assert (await client.get(f"/api/v1/files/{root_id}/children", headers=zh)).status_code == 404


@pytest.mark.anyio
async def test_html_draait_geen_scripts(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    html = (await put(client, headers, root_id, "pagina.html", b"<script>1</script>")).json()
    foto = (await put(client, headers, root_id, "foto.png", b"png")).json()
    r = await client.get(f"/api/v1/files/{html['id']}/content", headers=headers)
    assert r.headers["content-security-policy"] == "sandbox"
    r = await client.get(f"/api/v1/files/{foto['id']}/content", headers=headers)
    assert "content-security-policy" not in r.headers


@pytest.mark.anyio
async def test_opruimen_laat_inhoud_staan_waar_een_bestand_naar_wijst(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "a.txt", b"inhoud")).json()
    sha = node["etag"].strip('"')
    blobs = BlobStore(settings.blob_dir)
    # ref_count klopt niet meer (0) terwijl a.txt er nog naar wijst; daarnaast een echte wees
    orphan = "0" * 64
    blobs.path(orphan).parent.mkdir(parents=True)
    blobs.path(orphan).write_bytes(b"wees")
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        await session.execute(update(Blob).where(Blob.sha256 == sha).values(ref_count=0))
        session.add(Blob(sha256=orphan, size=4, ref_count=0))
        await session.commit()
        await collect_garbage(session, blobs)
        remaining = (await session.scalars(select(Blob.sha256))).all()
    assert remaining == [sha]
    assert blobs.path(sha).exists()
    assert not blobs.path(orphan).exists()
    r = await client.get(f"/api/v1/files/{node['id']}/content", headers=headers)
    assert r.content == b"inhoud"


@pytest.mark.anyio
async def test_stilgevallen_upload_geeft_verbinding_vrij(client, admin, settings, monkeypatch):
    monkeypatch.setattr(storage, "CHUNK_TIMEOUT", 0.3)
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    in_use = []

    async def body():
        yield b"x" * 100
        in_use.append(get_engine(settings).pool.checkedout())  # terwijl de server wacht
        await asyncio.Event().wait()  # valt stil zonder de verbinding te sluiten

    with anyio.fail_after(5):
        r = await put(client, headers, root_id, "stil", body())
    assert r.status_code == 408
    assert in_use == [0]
    assert list((settings.blob_dir / ".tmp").iterdir()) == []
    assert (await root(client, headers))["children"] == []


async def purge_while(client, headers, folder, monkeypatch, other):
    """Definitief verwijderen van `folder`, met `other()` ertussen terwijl dat bezig is (het
    heeft de inhoud van de map net gelezen, maar nog niets weggehaald). Geeft (antwoord
    purge, antwoord other)."""
    from app.routers import files
    from tests.test_uploads import hold

    entered, release = hold(monkeypatch, files, "drop_versions")
    with anyio.fail_after(5):
        purging = asyncio.create_task(client.delete(f"/api/v1/trash/{folder}", headers=headers))
        await entered.wait()
        racing = asyncio.create_task(other())
        await asyncio.sleep(0.05)
        release.set()
        return await purging, await racing


@pytest.mark.anyio
async def test_verplaatsen_tijdens_definitief_verwijderen(client, admin, settings, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, root_id, "Weg")
    sub = await mkdir(client, headers, weg, "Sub")
    x = (await put(client, headers, weg, "x.txt", b"x")).json()["id"]
    y = (await put(client, headers, root_id, "y.txt", b"y")).json()["id"]
    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    # Iets naar een map in de prullenbak verplaatsen kan niet
    r = await client.patch(f"/api/v1/files/{y}", json={"parent_id": sub}, headers=headers)
    assert r.status_code == 404

    # x eruit halen terwijl de map definitief weggaat: of het lukt en x blijft, of x is weg
    p, r = await purge_while(
        client,
        headers,
        weg,
        monkeypatch,
        lambda: client.patch(f"/api/v1/files/{x}", json={"parent_id": root_id}, headers=headers),
    )
    assert p.status_code == 204
    still_there = (await client.get(f"/api/v1/files/{x}", headers=headers)).status_code == 200
    assert (r.status_code, still_there) in [(200, True), (404, False)]
    await check_refs(settings)


@pytest.mark.anyio
async def test_verplaatsen_naar_map_die_definitief_weggaat(client, admin, settings, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, root_id, "Weg")
    sub = await mkdir(client, headers, weg, "Sub")
    await put(client, headers, sub, "z.txt", b"z")
    y = (await put(client, headers, root_id, "y.txt", b"y")).json()["id"]
    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    # Terwijl Weg definitief weggaat, iets naar Sub verplaatsen: dat moet daarna 404 geven,
    # niet stilletjes mee weg (en dan zijn verwijzing laten tellen)
    p, r = await purge_while(
        client,
        headers,
        weg,
        monkeypatch,
        lambda: client.patch(f"/api/v1/files/{y}", json={"parent_id": sub}, headers=headers),
    )
    assert (p.status_code, r.status_code) == (204, 404)
    assert (await client.get(f"/api/v1/files/{y}", headers=headers)).json()["parent_id"] == root_id
    await check_refs(settings)


@pytest.mark.anyio
async def test_terugzetten_tijdens_definitief_verwijderen(client, admin, settings, monkeypatch):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    weg = await mkdir(client, headers, root_id, "Weg")
    c = (await put(client, headers, weg, "c.txt", b"c")).json()["id"]
    await client.delete(f"/api/v1/files/{c}", headers=headers)
    await client.delete(f"/api/v1/files/{weg}", headers=headers)
    p, r = await purge_while(
        client,
        headers,
        weg,
        monkeypatch,
        lambda: client.post(f"/api/v1/files/{c}/restore", headers=headers),
    )
    assert p.status_code == 204
    still_there = (await client.get(f"/api/v1/files/{c}", headers=headers)).status_code == 200
    assert (r.status_code, still_there) in [(200, True), (404, False)]
    await check_refs(settings)
