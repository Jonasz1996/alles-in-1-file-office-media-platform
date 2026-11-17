"""PDF-pagina's in de viewer (routers/viewer.py): /document, /pages/{n}.webp met het token,
dat een ingetrokken deling en een geblokkeerde gebruiker meteen stoppen, de grenzen (breedte,
paginanummer), hooguit page_render_workers renderprocessen tegelijk, de cache en het
verlopen van het token."""

import asyncio
import io
import os
import uuid
from datetime import UTC, datetime

import pytest
from PIL import Image
from sqlalchemy import update

from app import previews
from app.models import User
from app.routers import viewer
from app.storage import BlobStore
from tests.factories import code_of, maker
from tests.test_files import mkdir, put, root
from tests.test_shares import person, share_with


def outlined_pdf(pages: int = 9) -> bytes:
    """Een PDF met de hand: een inhoudsopgave (twee niveaus), paginalabels (i, ii, 1, 2, ...)
    en een liggende laatste pagina."""
    first_page = 7
    page_ids = list(range(first_page, first_page + pages))
    third, fourth = page_ids[min(2, pages - 1)], page_ids[min(3, pages - 1)]
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 3 0 R /PageMode /UseOutlines "
        "/PageLabels << /Nums [0 << /S /r >> 2 << /S /D >>] >> >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {pages} >>",
        3: "<< /Type /Outlines /First 4 0 R /Last 5 0 R /Count 3 >>",
        4: f"<< /Title (Inleiding) /Parent 3 0 R /Next 5 0 R /Dest [{page_ids[0]} 0 R /Fit] >>",
        5: "<< /Title (Hoofdstuk 1) /Parent 3 0 R /Prev 4 0 R /First 6 0 R /Last 6 0 R "
        f"/Count 1 /Dest [{third} 0 R /Fit] >>",
        6: f"<< /Title (Paragraaf 1.1) /Parent 5 0 R /Dest [{fourth} 0 R /Fit] >>",
    }
    for i, p in enumerate(page_ids):
        box = "0 0 842 595" if i == pages - 1 else "0 0 595 842"
        objects[p] = f"<< /Type /Page /Parent 2 0 R /MediaBox [{box}] >>"
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = out.tell()
        out.write(f"{number} 0 obj\n{objects[number]}\nendobj\n".encode())
    xref = out.tell()
    size = max(objects) + 1
    out.write(f"xref\n0 {size}\n0000000000 65535 f \n".encode())
    for number in range(1, size):
        out.write(f"{offsets[number]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


async def upload_pdf(client, headers, name="boek.pdf", data=None) -> str:
    root_id = (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, root_id, name, data or outlined_pdf())
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def document(client, headers, node_id, expect=200, **params):
    r = await client.get(f"/api/v1/files/{node_id}/document", params=params, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def page_url(doc, n: int, w: int = 640) -> str:
    return doc["page_url"].replace("{n}", str(n)).replace("{w}", str(w))


def cached(settings, sha: str, w: int) -> set[int]:
    folder = viewer.page_path(BlobStore(settings.blob_dir), sha, w, 1).parent
    return {int(p.stem) for p in folder.glob("*.webp")} if folder.is_dir() else set()


def sha_of(settings, node) -> str:
    return node["etag"].strip('"')


@pytest.mark.anyio
async def test_document_en_pagina(client, admin, settings):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas)
    doc = await document(client, jonas, node_id)
    assert doc["kind"] == "pdf" and doc["pages"] == 9 and doc["office"] is None
    assert doc["page_sizes"][0] == [595.0, 842.0] and doc["page_sizes"][-1] == [842.0, 595.0]
    assert doc["toc"] == [
        {"title": "Inleiding", "page": 1, "level": 0},
        {"title": "Hoofdstuk 1", "page": 3, "level": 0},
        {"title": "Paragraaf 1.1", "page": 4, "level": 1},
    ]
    assert doc["labels"][:4] == ["i", "ii", "1", "2"]
    assert doc["widths"] == [640, 1280, 1920, 2560]
    assert doc["token_expires_in"] == settings.download_token_minutes * 60
    assert doc["page_url"].startswith(f"/api/v1/files/{node_id}/pages/{{n}}.webp?w={{w}}&token=")

    r = await client.get(page_url(doc, 1))  # zonder inloggen: het token is genoeg
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"] == "private, max-age=600"
    img = Image.open(io.BytesIO(r.content))
    assert img.size[0] == 640 and 905 <= img.size[1] <= 906
    # Liggend: ook 640 breed
    r = await client.get(page_url(doc, 9))
    assert Image.open(io.BytesIO(r.content)).size[0] == 640
    # n, n+1 en n+2 in één keer
    meta = (await client.get(f"/api/v1/files/{node_id}", headers=jonas)).json()
    assert cached(settings, sha_of(settings, meta), 640) == {1, 2, 3, 9}

    # ETag: niets nieuws te sturen
    etag = r.headers["etag"]
    assert etag == f'"{sha_of(settings, meta)}-9-640"'
    again = await client.get(page_url(doc, 9), headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    # De tweede keer uit de cache, ook als het renderproces niet meer zou werken
    r = await client.get(page_url(doc, 2))
    assert r.status_code == 200


@pytest.mark.anyio
async def test_grenzen(client, admin, settings):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas)
    doc = await document(client, jonas, node_id)
    r = await client.get(page_url(doc, 1, w=1000))
    assert r.status_code == 422 and code_of(r) == "invalid_width"
    for n in (0, 10, 999):
        r = await client.get(page_url(doc, n))
        assert r.status_code == 404 and code_of(r) == "page_not_found", n
    # Een token van een ander bestand
    other = await upload_pdf(client, jonas, "ander.pdf", outlined_pdf(2))
    wrong = page_url(doc, 1).replace(f"/files/{node_id}/", f"/files/{other}/")
    r = await client.get(wrong)
    assert r.status_code == 403 and code_of(r) == "wrong_token"
    r = await client.get(f"/api/v1/files/{node_id}/pages/1.webp", params={"token": "rommel"})
    assert r.status_code == 401
    # Geen PDF: geen pagina's
    root_id = (await root(client, jonas))["folder"]["id"]
    txt = (await put(client, jonas, root_id, "a.txt", b"tekst")).json()["id"]
    r = await client.get(f"/api/v1/files/{txt}/document", headers=jonas)
    assert r.status_code == 422 and code_of(r) == "not_a_document"
    folder = (await root(client, jonas))["folder"]["id"]
    r = await client.get(f"/api/v1/files/{folder}/document", headers=jonas)
    assert r.status_code == 422 and code_of(r) == "not_a_file"
    # Een kapotte PDF
    broken = await upload_pdf(client, jonas, "kapot.pdf", b"%PDF-1.4\nrommel")
    r = await client.get(f"/api/v1/files/{broken}/document", headers=jonas)
    assert r.status_code == 422 and code_of(r) == "not_a_document"


@pytest.mark.anyio
async def test_ingetrokken_deling_en_geblokkeerde_gebruiker(client, admin, settings):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas)
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    share = await share_with(client, jonas, node_id, user_id=anna_id, permission="read")
    doc = await document(client, anna, node_id)
    assert (await client.get(page_url(doc, 1))).status_code == 200
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)
    assert r.status_code == 204
    # Hetzelfde adres, ook voor een pagina die al in de cache staat
    r = await client.get(page_url(doc, 1))
    assert r.status_code == 404
    assert (await client.get(page_url(doc, 5))).status_code == 404
    await document(client, anna, node_id, expect=404)

    # Geblokkeerd: het token stopt meteen
    await share_with(client, jonas, node_id, user_id=anna_id, permission="read")
    doc = await document(client, anna, node_id)
    assert (await client.get(page_url(doc, 1))).status_code == 200
    async with maker(settings)() as session:
        await session.execute(
            update(User).where(User.id == uuid.UUID(anna_id)).values(disabled_at=datetime.now(UTC))
        )
        await session.commit()
    r = await client.get(page_url(doc, 1))
    assert r.status_code == 404 and code_of(r) == "not_found"


@pytest.mark.anyio
async def test_token_verloopt_en_een_nieuw_document_helpt(client, admin, settings):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas)
    settings.download_token_minutes = 0
    doc = await document(client, jonas, node_id)
    assert doc["token_expires_in"] == 0
    r = await client.get(page_url(doc, 1))
    assert r.status_code == 401 and code_of(r) == "invalid_token"
    settings.download_token_minutes = 10
    doc = await document(client, jonas, node_id)
    assert doc["token_expires_in"] == 600
    assert (await client.get(page_url(doc, 1))).status_code == 200


@pytest.mark.anyio
async def test_eerdere_versie(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    first = (await put(client, jonas, root_id, "a.pdf", outlined_pdf(3))).json()["id"]
    r = await put(client, jonas, root_id, "a.pdf", outlined_pdf(5), conflict="replace")
    assert r.status_code == 201
    versions = (await client.get(f"/api/v1/files/{first}/versions", headers=jonas)).json()
    old = versions[0]["id"]
    assert (await document(client, jonas, first))["pages"] == 5
    doc = await document(client, jonas, first, version=old)
    assert doc["pages"] == 3
    assert (await client.get(page_url(doc, 3))).status_code == 200
    r = await client.get(page_url(doc, 4))
    assert r.status_code == 404 and code_of(r) == "page_not_found"
    await document(client, jonas, first, expect=404, version=str(uuid.uuid4()))


@pytest.mark.anyio
async def test_hooguit_twee_renderprocessen_tegelijk(client, admin, settings, monkeypatch):
    jonas, _ = admin
    settings.page_render_workers = 2
    node_id = await upload_pdf(client, jonas)
    doc = await document(client, jonas, node_id)  # docinfo staat nu in de cache
    running, most, release = 0, 0, asyncio.Event()
    started = asyncio.Event()
    real = previews.render

    async def slow(settings_, request, cwd):
        nonlocal running, most
        if request.get("mode") != "page":
            return await real(settings_, request, cwd)
        running += 1
        most = max(most, running)
        if running == 2:
            started.set()
        try:
            await release.wait()
            return await real(settings_, request, cwd)
        finally:
            running -= 1

    monkeypatch.setattr(previews, "render", slow)
    tasks = [asyncio.create_task(client.get(page_url(doc, n))) for n in (1, 4, 7)]
    await asyncio.wait_for(started.wait(), 10)
    for _ in range(20):
        await asyncio.sleep(0)  # het derde verzoek krijgt alle kans om te beginnen
    assert running == 2 and not any(t.done() for t in tasks)
    release.set()
    results = await asyncio.gather(*tasks)
    assert [r.status_code for r in results] == [200, 200, 200]
    assert most == 2


@pytest.mark.anyio
async def test_cache_ruimt_de_oudste_op(client, admin, settings):
    jonas, _ = admin
    settings.page_cache_mb = 0
    node_id = await upload_pdf(client, jonas)
    meta = (await client.get(f"/api/v1/files/{node_id}", headers=jonas)).json()
    sha = sha_of(settings, meta)
    doc = await document(client, jonas, node_id)
    assert (await client.get(page_url(doc, 1))).status_code == 200
    # Alleen wat net gevraagd is, blijft staan
    assert cached(settings, sha, 640) == {1}
    assert (await client.get(page_url(doc, 5))).status_code == 200
    assert cached(settings, sha, 640) == {5}

    # De langst niet gebruikte gaan eerst
    blobs = BlobStore(settings.blob_dir)
    paths = [viewer.page_path(blobs, sha, 1280, n) for n in (1, 2, 3)]
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(paths):
        path.write_bytes(b"x" * 600 * 1024)
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    os.utime(paths[0], (2_000_000, 2_000_000))  # pagina 1 is net nog bekeken
    settings.page_cache_mb = 1
    removed = await viewer.evict(settings)
    assert {p.stem for p in paths if p.exists()} == {"1"}
    assert removed == 2 and cached(settings, sha, 640) == {5}  # pagina 5 is de nieuwste


@pytest.mark.anyio
async def test_opgeruimde_inhoud_laat_geen_pagina_s_achter(settings, tmp_path):
    """Is de blob weg terwijl er gerenderd werd, dan komt de pagina nergens te staan."""
    source = tmp_path / "1.webp"
    source.write_bytes(b"webp")
    sha = "0" * 64
    target = viewer.page_path(BlobStore(settings.blob_dir), sha, 640, 1)
    assert await viewer._place(settings, sha, [(source, target)]) is False
    assert not target.exists() and source.exists()


@pytest.mark.anyio
async def test_publieke_link(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Boeken")
    inside = (await put(client, jonas, folder, "boek.pdf", outlined_pdf(4))).json()["id"]
    outside = await upload_pdf(client, jonas, "buiten.pdf", outlined_pdf(2))
    r = await client.post(f"/api/v1/files/{folder}/links", json={"permission": "read"},
                          headers=jonas)  # fmt: skip
    assert r.status_code == 201, r.text
    token = r.json()["link"]["url"].removeprefix("/s/")
    opened = (await client.post("/api/v1/public/open", json={"token": token})).json()
    key = {"Authorization": f"Bearer {opened['key']}"}

    r = await client.get(f"/api/v1/public/document/{inside}", headers=key)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["pages"] == 4 and doc["toc"][0]["title"] == "Inleiding"
    assert doc["page_url"].startswith(f"/api/v1/public/pages/{inside}/{{n}}.webp?w={{w}}&key=")
    assert 12 * 3600 - 5 <= doc["token_expires_in"] <= 12 * 3600
    r = await client.get(page_url(doc, 2))
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    r = await client.get(f"/api/v1/public/preview/{inside}", headers=key)
    assert r.status_code == 200 and "display" in r.json()
    # Wat buiten de gedeelde map ligt
    for path in (f"document/{outside}", f"preview/{outside}", f"pages/{outside}/1.webp"):
        r = await client.get(f"/api/v1/public/{path}", headers=key)
        assert r.status_code == 404, path
    # Zonder sleutel
    r = await client.get(page_url(doc, 2).split("&key=")[0])
    assert r.status_code == 401
