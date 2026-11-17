"""De rijen van §3.9 voor ocr_page en photo_ocr (bouwontwerp fase 6, X1): elke gebeurtenis, wat
er met de tekst van OCR gebeurt, en het bewijs.

| gebeurtenis | ocr_page / blob_text.ocr | photo_ocr | test |
|---|---|---|---|
| OCR in een foto | — | bij die node van die eigenaar | test_photo_ocr_per_node |
| OCR in een PDF | ocr gevuld | — | test_ocr_searchable |
| andere eigenaar | — | node_au_owner wist hem | test_owner_change_wipes |
| nieuwe inhoud | de oude sha houdt de zijne | weg | test_replace_no_old_text |
| prullenbak en terug | blijft | blijft (own_live laat hem weg) | test_trash_and_restore_keep_ocr |
| blob opgeruimd | cascade | (per node al weg) | test_gc_cascade |
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app import analysis
from app.models import BlobText, Node, OcrPage, PhotoOcr
from tests.ocr import nep
from tests.search.fixtures import pdf_scan
from tests.test_files import put, root

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stappen")]

WORD = "levenswoord"


async def upload(client, headers, name: str, data: bytes) -> dict:
    parent = (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, parent, name, data)
    assert r.status_code == 201, r.text
    return r.json()


async def photo_with_text(client, headers, settings, w, name: str, data: bytes) -> dict:
    """Een foto met OCR-tekst bij deze node (via "Tekst herkennen" en de pomp)."""
    node = await upload(client, headers, name, data)
    w.page(data, 1, WORD)
    await analysis.backfill_job(settings, {"after": ""})
    r = await client.post(f"/api/v1/files/{node['id']}/ocr", headers=headers)
    assert r.status_code == 202, r.text
    assert await nep.pump(settings) == 1
    assert (await nep.photo_text(settings, node["id"]))[0] == WORD
    return node


async def test_photo_ocr_per_node(client, mensen, aan, tmp_path):
    """Twee nodes van Eva met dezelfde inhoud (twee uploads): de tekst komt alleen bij de node
    waar ze om vroeg, en alleen die node vindt ze terug."""
    (eva, _), _ = mensen
    data = nep.photo("twee")
    andere = await upload(client, eva, "eerst.jpg", data)
    async with nep.worker(tmp_path) as w:
        node = await photo_with_text(client, eva, aan, w, "tweede.jpg", data)
    assert await nep.photo_text(aan, andere["id"]) is None
    assert await nep.fts(aan, "pocr", WORD) == {node["id"]}
    assert set(await nep.search(client, eva, WORD)) == {node["id"]}


async def test_ocr_searchable(client, mensen, aan, tmp_path):
    """De OCR van een PDF hoort bij de inhoud: elke node met die sha vindt hem (de triggers
    werken FTS bij), ook een kopie die er later bij komt."""
    (eva, _), _ = mensen
    data = pdf_scan(WORD)
    eerst = await upload(client, eva, "scan.pdf", data)
    async with nep.worker(tmp_path) as w:
        w.pdf(data, [1])
        w.page(data, 1, WORD)
        await analysis.backfill_job(aan, {"after": ""})
        assert await nep.pump(aan) == 1
    tweede = await upload(client, eva, "nog een keer.pdf", data)
    assert await nep.fts(aan, "ocr", WORD) == {eerst["id"], tweede["id"]}
    hits = await nep.search(client, eva, WORD)
    assert {hits[n]["match"] for n in (eerst["id"], tweede["id"])} == {"ocr"}


async def test_owner_change_wipes(client, mensen, aan, tmp_path):
    """Een andere eigenaar (owner_id verandert): node_au_owner wist photo_ocr van die node; de
    oude en de nieuwe eigenaar vinden de tekst niet meer."""
    (eva, _), (bram, bram_id) = mensen
    data = nep.photo("eigenaar")
    async with nep.worker(tmp_path) as w:
        node = await photo_with_text(client, eva, aan, w, "bon.jpg", data)
    async with nep.maker(aan)() as db:
        await db.execute(
            sa.update(Node)
            .where(Node.id == uuid.UUID(node["id"]))
            .values(owner_id=uuid.UUID(bram_id))
        )
        await db.commit()
    assert await nep.photo_text(aan, node["id"]) is None
    assert await nep.fts(aan, "pocr", WORD) == set()
    assert await nep.search(client, eva, WORD) == {}
    assert await nep.search(client, bram, WORD) == {}


async def test_replace_no_old_text(client, mensen, aan, tmp_path):
    """Nieuwe inhoud voor de foto (een nieuwe versie): photo_ocr van die node weg; de oude
    tekst nooit meer, ook niet als de nieuwe inhoud nog niet gelezen is."""
    (eva, _), _ = mensen
    data = nep.photo("oud")
    async with nep.worker(tmp_path) as w:
        node = await photo_with_text(client, eva, aan, w, "bon.jpg", data)
    r = await client.put(
        f"/api/v1/files/{node['id']}/content", content=nep.photo("nieuw"), headers=eva
    )
    assert r.status_code == 200, r.text
    assert await nep.photo_text(aan, node["id"]) is None
    assert await nep.fts(aan, "pocr", WORD) == set()
    assert await nep.search(client, eva, WORD) == {}
    r = await client.get(f"/api/v1/files/{node['id']}/analysis", headers=eva)
    assert r.json()["ocr"] == "skip"


async def test_trash_and_restore_keep_ocr(client, mensen, aan, tmp_path):
    """Naar de prullenbak: weg uit zoeken, de rij in photo_ocr blijft; terug: weer te vinden."""
    (eva, _), _ = mensen
    data = nep.photo("prullenbak")
    async with nep.worker(tmp_path) as w:
        node = await photo_with_text(client, eva, aan, w, "bon.jpg", data)
    assert (await client.delete(f"/api/v1/files/{node['id']}", headers=eva)).status_code == 204
    assert await nep.search(client, eva, WORD) == {}
    assert (await nep.photo_text(aan, node["id"]))[0] == WORD
    r = await client.post(f"/api/v1/files/{node['id']}/restore", headers=eva)
    assert r.status_code == 200, r.text
    assert set(await nep.search(client, eva, WORD)) == {node["id"]}


async def test_gc_cascade(client, mensen, aan, tmp_path):
    """Definitief gewist en de blob opgeruimd: ocr_page en blob_text gaan mee (cascade), en
    photo_ocr van de node ook."""
    (eva, _), _ = mensen
    scan = pdf_scan("opgeruimd")
    foto = nep.photo("opgeruimd")
    pdf = await upload(client, eva, "weg.pdf", scan)
    async with nep.worker(tmp_path) as w:
        w.pdf(scan, [1])
        w.page(scan, 1, WORD)
        await analysis.backfill_job(aan, {"after": ""})
        assert await nep.pump(aan) == 1
        node = await photo_with_text(client, eva, aan, w, "weg.jpg", foto)
    assert sorted(await nep.pages(aan, nep.sha(scan))) == [1]
    for item in (pdf, node):
        assert (await client.delete(f"/api/v1/files/{item['id']}", headers=eva)).status_code == 204
        r = await client.delete(f"/api/v1/trash/{item['id']}", headers=eva)
        assert r.status_code == 204, r.text
    async with nep.maker(aan)() as db:
        for table in (OcrPage, BlobText, PhotoOcr):
            assert await db.scalar(sa.select(sa.func.count()).select_from(table)) == 0, table
    assert await nep.fts(aan, "ocr", WORD) == set()
