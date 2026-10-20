import pytest


async def root(client, headers):
    return (await client.get("/api/v1/files/root", headers=headers)).json()


async def put(client, headers, parent, name, data: bytes, **params):
    return await client.put(
        f"/api/v1/files/{parent}/upload",
        params={"name": name, **params},
        content=data,
        headers=headers,
    )


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

    r = await put(client, headers, fotos, "zee.jpg", b"jpegdata")
    assert r.status_code == 201
    assert r.json()["mime"] == "image/jpeg"
    assert r.json()["size"] == 8

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
