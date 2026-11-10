"""POST /files/{id}/ocr, "Tekst herkennen" (bouwontwerp fase 6, X1; §3.4, Keuze 23).

- een foto: alleen de eigenaar; een ander (ook met een deling, ook met schrijfrecht) krijgt 404,
  net als voor een bestand dat er niet is;
- een PDF: wie schrijfrecht heeft; wie alleen mag lezen, krijgt 404;
- iets anders: 422 ocr_unsupported;
- 409 ocr_running als hij al wacht of gelezen wordt; 409 ocr_has_text voor een PDF met al
  ≥ 200.000 tekens tekst; 503 ocr_off als OCR niet draait;
- 429 ocr_limit: per gebruiker hooguit 20 verzoeken per uur en 200 pagina's per dag; alleen een
  verzoek dat doorgaat, telt.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.config import get_settings
from app.main import app
from app.models import Blob, BlobText
from app.routers import ocr as ocr_router
from tests.ocr import nep
from tests.search.fixtures import pdf_scan
from tests.test_files import mkdir, put, root
from tests.test_shares import share_with

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stappen")]


async def upload(client, headers, name: str, data: bytes, parent: str | None = None) -> dict:
    parent = parent or (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, parent, name, data)
    assert r.status_code == 201, r.text
    return r.json()


async def ask(client, headers, node_id: str):
    return await client.post(f"/api/v1/files/{node_id}/ocr", headers=headers)


def code_of(r) -> str:
    return r.json()["detail"]["code"]


async def set_pages(settings, sha: str, pages: int) -> None:
    """Het aantal pagina's zoals de preview het vond (preview_meta)."""
    async with nep.maker(settings)() as db:
        await db.execute(
            sa.update(Blob).where(Blob.sha256 == sha).values(preview_meta={"pages": pages})
        )
        await db.commit()


async def test_een_foto_alleen_de_eigenaar_een_pdf_met_schrijfrecht(client, mensen, aan):
    (eva, _), (bram, bram_id) = mensen
    eva_root = (await root(client, eva))["folder"]["id"]
    leest = await mkdir(client, eva, eva_root, "Lezen")
    schrijft = await mkdir(client, eva, eva_root, "Schrijven")
    await share_with(client, eva, leest, user_id=bram_id, permission="read")
    await share_with(client, eva, schrijft, user_id=bram_id, permission="write")
    foto_lezen = await upload(client, eva, "a.jpg", nep.photo("a"), leest)
    foto_schrijven = await upload(client, eva, "b.jpg", nep.photo("b"), schrijft)
    pdf_lezen = await upload(client, eva, "a.pdf", pdf_scan("alezen"), leest)
    pdf_schrijven = await upload(client, eva, "b.pdf", pdf_scan("bschrijven"), schrijft)
    niet_er = await ask(client, bram, str(uuid.uuid4()))
    assert niet_er.status_code == 404
    # Een foto van Eva: voor Bram 404, net als iets dat er niet is (ook met schrijfrecht)
    for node in (foto_lezen, foto_schrijven):
        r = await ask(client, bram, node["id"])
        assert r.status_code == 404 and r.json() == niet_er.json()
    # Een PDF: alleen lezen is 404, schrijven mag
    r = await ask(client, bram, pdf_lezen["id"])
    assert r.status_code == 404 and r.json() == niet_er.json()
    assert (await ask(client, bram, pdf_schrijven["id"])).status_code == 202
    # De eigenaar mag beide
    assert (await ask(client, eva, foto_lezen["id"])).status_code == 202
    assert (await ask(client, eva, pdf_lezen["id"])).status_code == 202
    # Zonder inlog: 401
    assert (await client.post(f"/api/v1/files/{pdf_lezen['id']}/ocr")).status_code == 401


async def test_alleen_een_foto_of_pdf(client, mensen, aan):
    (eva, _), _ = mensen
    eva_root = (await root(client, eva))["folder"]["id"]
    folder = await mkdir(client, eva, eva_root, "Map")
    text = await upload(client, eva, "notitie.txt", b"gewone tekst")
    for node_id in (folder, text["id"]):
        r = await ask(client, eva, node_id)
        assert r.status_code == 422 and code_of(r) == "ocr_unsupported"


async def test_409_als_hij_al_wacht_en_weer_als_hij_klaar_is(client, mensen, aan):
    (eva, _), _ = mensen
    foto = await upload(client, eva, "bon.jpg", nep.photo("dubbel"))
    pdf = await upload(client, eva, "scan.pdf", pdf_scan("dubbel"))
    for node in (foto, pdf):
        assert (await ask(client, eva, node["id"])).status_code == 202
        r = await ask(client, eva, node["id"])
        assert r.status_code == 409 and code_of(r) == "ocr_running"
    # Klaar (of mislukt): een nieuw verzoek mag weer
    await nep.set_row(aan, nep.sha(pdf_scan("dubbel")), "done")
    await nep.set_row(aan, nep.sha(nep.photo("dubbel")), "failed")
    assert (await ask(client, eva, pdf["id"])).status_code == 202
    assert (await ask(client, eva, foto["id"])).status_code == 202


async def test_ocr_has_text(client, mensen, aan):
    """Een PDF met al ≥ 200.000 tekens tekst krijgt geen OCR (C11): 409 ocr_has_text, ook als de
    stap dat al vond (de code in analysis)."""
    (eva, _), _ = mensen
    dik = pdf_scan("dik")
    node = await upload(client, eva, "dik.pdf", dik)
    async with nep.maker(aan)() as db:
        db.add(BlobText(blob_sha=nep.sha(dik), body="w " * 100_000, ocr="", meta=""))
        await db.commit()
    r = await ask(client, eva, node["id"])
    assert r.status_code == 409 and code_of(r) == "ocr_has_text"
    ander = pdf_scan("gevonden")
    node = await upload(client, eva, "gevonden.pdf", ander)
    assert (await ask(client, eva, node["id"])).status_code == 202
    await nep.set_row(aan, nep.sha(ander), "skip", "ocr_has_text")
    r = await ask(client, eva, node["id"])
    assert r.status_code == 409 and code_of(r) == "ocr_has_text"


async def test_503_als_ocr_niet_draait(client, mensen, settings, aan):
    (eva, _), _ = mensen
    node = await upload(client, eva, "scan.pdf", pdf_scan("uit"))
    for update in ({"media_enabled": False}, {"ocr_enabled": False}):
        off = aan.model_copy(update=update)
        app.dependency_overrides[get_settings] = lambda off=off: off
        r = await ask(client, eva, node["id"])
        assert r.status_code == 503 and code_of(r) == "ocr_off"
    app.dependency_overrides[get_settings] = lambda: aan
    assert (await ask(client, eva, node["id"])).status_code == 202


async def test_429_na_20_verzoeken_per_uur(client, mensen, aan, monkeypatch):
    (eva, _), (bram, _) = mensen
    clock = {"now": 1000.0}
    monkeypatch.setattr(ocr_router.time, "monotonic", lambda: clock["now"])
    data = nep.photo("vaak")
    node = await upload(client, eva, "vaak.jpg", data)
    for _ in range(20):
        assert (await ask(client, eva, node["id"])).status_code == 202
        await nep.set_row(aan, nep.sha(data), "done")
    r = await ask(client, eva, node["id"])
    assert r.status_code == 429 and code_of(r) == "ocr_limit"
    # Een verzoek dat niet doorgaat, telt niet; en de grens is per gebruiker
    van_bram = await upload(client, bram, "bram.jpg", nep.photo("bram"))
    assert (await ask(client, bram, van_bram["id"])).status_code == 202
    # Een uur later mag het weer
    clock["now"] += 3600
    assert (await ask(client, eva, node["id"])).status_code == 202


async def test_429_na_200_pagina_s_per_dag(client, mensen, aan, monkeypatch):
    """Een PDF telt als zijn aantal pagina's (uit de preview), een foto als één. Wat er niet
    meer bij past, is 429; een dag later mag het weer."""
    (eva, _), _ = mensen
    clock = {"now": 5000.0}
    monkeypatch.setattr(ocr_router.time, "monotonic", lambda: clock["now"])
    groot, klein = pdf_scan("groot"), pdf_scan("klein")
    a = await upload(client, eva, "groot.pdf", groot)
    b = await upload(client, eva, "klein.pdf", klein)
    foto = await upload(client, eva, "foto.jpg", nep.photo("dag"))
    await set_pages(aan, nep.sha(groot), 150)
    await set_pages(aan, nep.sha(klein), 50)
    assert (await ask(client, eva, a["id"])).status_code == 202  # 150
    assert (await ask(client, eva, b["id"])).status_code == 202  # 200
    r = await ask(client, eva, foto["id"])  # 201: past niet meer
    assert r.status_code == 429 and code_of(r) == "ocr_limit"
    clock["now"] += 86_400
    assert (await ask(client, eva, foto["id"])).status_code == 202
    # Een document groter dan ocr_max_pages telt als ocr_max_pages: één kan altijd
    reus = pdf_scan("reus")
    c = await upload(client, eva, "reus.pdf", reus)
    await set_pages(aan, nep.sha(reus), 5000)
    clock["now"] += 86_400
    assert (await ask(client, eva, c["id"])).status_code == 202
    await nep.set_row(aan, nep.sha(nep.photo("dag")), "done")
    r = await ask(client, eva, foto["id"])
    assert r.status_code == 429 and code_of(r) == "ocr_limit"
