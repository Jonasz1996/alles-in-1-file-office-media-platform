"""Wie de tekst van OCR ziet (bouwontwerp fase 6, X1; Keuze 16, 23, 25a).

- De OCR van een foto hoort bij de node van één eigenaar (photo_ocr). Een kopie van Bram krijgt
  Eva's tekst niet; Brams OCR van zijn kopie komt niet bij Eva; Bram ziet via een deling niets
  van Eva's foto-tekst: niet in /search, niet in de stand van de analyse, niet in de woorden.
- Een publieke link toont nooit de tekst van een foto (geen zoekroute, geen woorden).
- De OCR van een PDF staat in blob_text.ocr: wie het document mag lezen, ziet de tekst ook (en
  de woorden, met source ocr); na het intrekken van de deling niet meer.
"""

from __future__ import annotations

import uuid

import pytest

from app import analysis
from app.security import download_token
from tests.ocr import nep
from tests.search.fixtures import pdf_scan
from tests.test_files import mkdir, put, root
from tests.test_public import bearer, new_link, open_link
from tests.test_shares import share_with

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stappen")]

WORD = "bonwoord"


async def upload(client, headers, name: str, data: bytes, parent: str) -> dict:
    r = await put(client, headers, parent, name, data)
    assert r.status_code == 201, r.text
    return r.json()


async def words(client, settings, user_id: str, node_id: str, n: int = 1):
    token = download_token(settings, uuid.UUID(user_id), uuid.UUID(node_id))
    return await client.get(f"/api/v1/files/{node_id}/pages/{n}/words", params={"token": token})


async def analysis_of(client, headers, node_id: str) -> dict:
    r = await client.get(f"/api/v1/files/{node_id}/analysis", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def copy(client, headers, node_id: str, parent: str) -> dict:
    r = await client.post(
        f"/api/v1/files/{node_id}/copy", json={"parent_id": parent}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def analysis_backfill(settings) -> None:
    await analysis.backfill_job(settings, {"after": ""})


async def gedeeld(client, eva, bram_id: str) -> str:
    """Eva's map Gedeeld, met Bram (lezen)."""
    eva_root = (await root(client, eva))["folder"]["id"]
    folder = await mkdir(client, eva, eva_root, "Gedeeld")
    await share_with(client, eva, folder, user_id=bram_id, permission="read")
    return folder


async def test_een_kopie_van_bram_krijgt_eva_s_tekst_niet(client, mensen, aan, tmp_path):
    (eva, eva_id), (bram, bram_id) = mensen
    folder = await gedeeld(client, eva, bram_id)
    data = nep.photo("eva")
    foto = await upload(client, eva, "bon.jpg", data, folder)
    async with nep.worker(tmp_path) as w:
        w.page(data, 1, "Kassabon", WORD)
        await analysis_backfill(aan)
        assert (
            await client.post(f"/api/v1/files/{foto['id']}/ocr", headers=eva)
        ).status_code == 202
        assert await nep.pump(aan) == 1
    assert (await nep.photo_text(aan, foto["id"]))[0] == f"Kassabon {WORD}"
    # Eva vindt haar foto; Bram ziet hem via de deling, maar niet via de tekst
    assert (await nep.search(client, eva, WORD))[foto["id"]]["match"] == "ocr"
    assert await nep.search(client, bram, WORD) == {}
    assert (await nep.search(client, bram, "bon"))[foto["id"]]["match"] == "name"
    assert (await analysis_of(client, bram, foto["id"]))["ocr"] == "skip"
    assert (await analysis_of(client, eva, foto["id"]))["ocr"] == "done"
    r = await words(client, aan, bram_id, foto["id"])
    assert r.status_code == 404
    r = await words(client, aan, eva_id, foto["id"])
    assert r.status_code == 200 and r.json()["source"] == "ocr"
    assert [w["t"] for w in r.json()["words"]] == ["Kassabon", WORD]
    # Bram kopieert de foto naar zijn eigen map: de kopie heeft geen tekst
    bram_root = (await root(client, bram))["folder"]["id"]
    kopie = await copy(client, bram, foto["id"], bram_root)
    assert await nep.photo_text(aan, kopie["id"]) is None
    assert await nep.fts(aan, "pocr", WORD) == {foto["id"]}
    assert await nep.search(client, bram, WORD) == {}
    assert (await analysis_of(client, bram, kopie["id"]))["ocr"] == "skip"
    assert (await words(client, aan, bram_id, kopie["id"])).status_code == 404


async def test_brams_ocr_van_zijn_kopie_komt_niet_bij_eva(client, mensen, aan, tmp_path):
    """Bram vraagt OCR van zijn kopie (en heeft "Tekst in foto's herkennen" aan): de tekst komt
    alleen bij zijn node. Eva vroeg niets: haar node blijft zonder tekst."""
    (eva, eva_id), (bram, bram_id) = mensen
    folder = await gedeeld(client, eva, bram_id)
    data = nep.photo("gedeeld")
    foto = await upload(client, eva, "kaart.jpg", data, folder)
    r = await client.patch("/api/v1/me/photo-settings", json={"ocr_photos": True}, headers=bram)
    assert r.status_code == 200, r.text
    bram_root = (await root(client, bram))["folder"]["id"]
    async with nep.worker(tmp_path) as w:
        w.page(data, 1, WORD)
        await analysis_backfill(aan)
        # De kopie van een ander met het aan: de haak photo_copy zet hem in de rij voor Bram
        kopie = await copy(client, bram, foto["id"], bram_root)
        assert await nep.row(aan, nep.sha(data)) == ("wait", None, 0)
        assert await nep.pump(aan) == 1
        # Bram vraagt het ook nog zelf: hetzelfde, alleen bij zijn node
        assert (
            await client.post(f"/api/v1/files/{kopie['id']}/ocr", headers=bram)
        ).status_code == 202
        assert await nep.pump(aan) == 1
    assert (await nep.photo_text(aan, kopie["id"]))[0] == WORD
    assert await nep.photo_text(aan, foto["id"]) is None
    assert await nep.fts(aan, "pocr", WORD) == {kopie["id"]}
    assert set(await nep.search(client, bram, WORD)) == {kopie["id"]}
    assert await nep.search(client, eva, WORD) == {}
    assert (await analysis_of(client, eva, foto["id"]))["ocr"] == "skip"
    assert (await words(client, aan, eva_id, foto["id"])).status_code == 404


async def test_een_publieke_link_toont_nooit_foto_ocr(client, mensen, aan, tmp_path):
    (eva, _), _ = mensen
    eva_root = (await root(client, eva))["folder"]["id"]
    folder = await mkdir(client, eva, eva_root, "Openbaar")
    data = nep.photo("openbaar")
    foto = await upload(client, eva, "bon.jpg", data, folder)
    async with nep.worker(tmp_path) as w:
        w.page(data, 1, WORD)
        await analysis_backfill(aan)
        await client.post(f"/api/v1/files/{foto['id']}/ocr", headers=eva)
        assert await nep.pump(aan) == 1
    assert (await nep.photo_text(aan, foto["id"]))[0] == WORD
    link = await new_link(client, eva, folder, permission="read")
    key = (await open_link(client, link))["key"]
    seen = []
    r = await client.get("/api/v1/public/children", headers=bearer(key))
    assert r.status_code == 200
    seen.append(r.text)
    r = await client.post(
        "/api/v1/public/previews", json={"node_ids": [foto["id"]]}, headers=bearer(key)
    )
    seen.append(r.text)
    assert all(WORD not in text for text in seen)
    # Geen zoekroute en geen woorden met de sleutel van de link
    r = await client.get("/api/v1/search", params={"q": WORD}, headers=bearer(key))
    assert r.status_code == 401
    for url in (
        f"/api/v1/public/files/{foto['id']}/pages/1/words",
        f"/api/v1/public/words/{foto['id']}/1",
    ):
        assert (await client.get(url, headers=bearer(key))).status_code == 404
    r = await client.get(f"/api/v1/files/{foto['id']}/pages/1/words", params={"token": key})
    assert r.status_code == 401


async def test_de_ocr_van_een_pdf_volgt_de_leestoegang(client, mensen, aan, tmp_path):
    (eva, eva_id), (bram, bram_id) = mensen
    eva_root = (await root(client, eva))["folder"]["id"]
    folder = await mkdir(client, eva, eva_root, "Scans")
    share = await share_with(client, eva, folder, user_id=bram_id, permission="read")
    data = pdf_scan(WORD)
    pdf = await upload(client, eva, "scan.pdf", data, folder)
    async with nep.worker(tmp_path) as w:
        w.pdf(data, [1])
        w.page(data, 1, "Kenmerk", WORD)
        await analysis_backfill(aan)
        assert await nep.pump(aan) == 1
    assert await nep.pdf_ocr(aan, nep.sha(data)) == f"Kenmerk {WORD}"
    # Wie de PDF mag lezen, vindt hem op de tekst en krijgt de woorden
    for headers, user_id in ((eva, eva_id), (bram, bram_id)):
        hits = await nep.search(client, headers, WORD)
        assert hits[pdf["id"]]["match"] == "ocr", hits
        r = await words(client, aan, user_id, pdf["id"])
        assert r.status_code == 200 and r.json()["source"] == "ocr"
        assert (await analysis_of(client, headers, pdf["id"]))["ocr"] == "done"
    # De deling ingetrokken: niets meer
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=eva)
    assert r.status_code == 204, r.text
    assert await nep.search(client, bram, WORD) == {}
    assert (await words(client, aan, bram_id, pdf["id"])).status_code in (403, 404)
    r = await client.get(f"/api/v1/files/{pdf['id']}/analysis", headers=bram)
    assert r.status_code == 404
