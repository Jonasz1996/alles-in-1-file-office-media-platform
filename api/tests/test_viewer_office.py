"""De viewer met de omgezette PDF van office (O3, Keuze 25): /document geeft kind "pdf" met
source "office" zodra de PDF er is, de pagina's komen uit die PDF, en pdf_state vertelt de
web-app wat er te doen is. Een publieke link ziet de pagina's maar start geen conversie; een
ingetrokken deling stopt de pagina's meteen. Zonder office precies fase 3."""

import uuid

import pytest
from sqlalchemy import select

from app import jobs
from app.main import app
from app.models import Version
from app.office.state import office_state
from tests.factories import code_of, maker
from tests.test_files import mkdir, root
from tests.test_office import docx_bytes
from tests.test_office_convert import (  # noqa: F401
    OLE,
    ask,
    blob_of,
    docx_with_pages,
    lying_docx,
    office,
    office_jobs,
)
from tests.test_shares import person, share_with
from tests.test_viewer import detail, open_link, upload


async def document(client, headers, node_id, expect=200, **params) -> dict:
    r = await client.get(f"/api/v1/files/{node_id}/document", params=params, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def page_of(doc: dict, n: int = 1, width: int = 640) -> str:
    return doc["page_url"].replace("{n}", str(n)).replace("{w}", str(width))


@pytest.mark.anyio
async def test_document_met_en_zonder_pdf(client, admin, office):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"hold": True})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(3))
    # Nog geen preview en de conversie loopt: de eenvoudige weergave, met pending
    doc = await document(client, jonas, node["id"])
    assert (doc["kind"], doc["source"], doc["pdf_state"]) == ("office", None, None)
    await jobs.run_one(s)  # de preview van B21
    doc = await document(client, jonas, node["id"])
    assert (doc["kind"], doc["pdf_state"]) == ("office", "pending")
    assert doc["office"]["type"] == "document" and doc["page_url"] is None
    assert (await detail(client, jonas, node["id"]))["meta"]["pdf_state"] == "pending"
    office.conversions.configure({"hold": False})
    await jobs.run_pending(s)
    # De PDF is er: pagina's, uit de omgezette PDF
    doc = await document(client, jonas, node["id"])
    assert (doc["kind"], doc["source"], doc["pdf_state"], doc["pages"]) == (
        "pdf",
        "office",
        "ready",
        3,
    )
    assert doc["office"] is None and len(doc["page_sizes"]) == 3
    r = await client.get(page_of(doc, 3))
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert r.headers["etag"].endswith('-p1"')
    r = await client.get(page_of(doc, 4))
    assert r.status_code == 404 and code_of(r) == "page_not_found"
    assert (await detail(client, jonas, node["id"]))["meta"]["pdf_state"] == "ready"
    # Ook als DS weg is: de PDF staat bij de app
    office_state(app).health = "down"
    assert (await document(client, jonas, node["id"]))["source"] == "office"


@pytest.mark.anyio
async def test_een_doc_zonder_eigen_preview(client, admin, office):  # noqa: F811
    """Een doc, xls, ppt of rtf heeft geen preview van B21: kind office met pdf_state, en na
    de conversie pagina's."""
    jonas, _ = admin
    s = office.settings
    office_state(app).health = "down"
    node = await upload(client, jonas, "oud.doc", OLE)
    await jobs.run_pending(s)
    # DS weg: geen staat (de inhaalronde neemt hem later), dus zoals fase 3
    r = await client.get(f"/api/v1/files/{node['id']}/document", headers=jonas)
    assert r.status_code == 422
    office_state(app).health = "ok"
    r = await ask(client, jonas, node["id"])
    doc = await document(client, jonas, node["id"])
    assert (doc["kind"], doc["pdf_state"], doc["office"]) == ("office", "pending", None)
    await jobs.run_pending(s)
    doc = await document(client, jonas, node["id"])
    assert (doc["kind"], doc["source"], doc["pages"]) == ("pdf", "office", 1)
    assert (await client.get(page_of(doc))).status_code == 200


@pytest.mark.anyio
async def test_ask_en_unsafe_in_het_antwoord(client, admin, office, monkeypatch):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    monkeypatch.setattr(s, "office_auto_convert_bytes", 100)
    big = await upload(client, jonas, "groot.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    doc = await document(client, jonas, big["id"])
    assert (doc["kind"], doc["pdf_state"]) == ("office", "ask")
    assert (await detail(client, jonas, big["id"]))["meta"]["pdf_state"] == "ask"
    monkeypatch.setattr(s, "office_auto_convert_bytes", 20 * 1024**2)
    lying = await upload(client, jonas, "liegt.docx", lying_docx())
    await jobs.run_pending(s)
    doc = await document(client, jonas, lying["id"])
    assert (doc["kind"], doc["pdf_state"]) == ("office", "unsafe")


@pytest.mark.anyio
async def test_een_eerdere_versie_zegt_ask(client, admin, office):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    office_state(app).health = "down"  # de eerste inhoud wordt nooit omgezet
    parent = (await root(client, jonas))["folder"]["id"]
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2, "Oud"), parent)
    await jobs.run_pending(s)
    old_sha = (await blob_of(s, node["id"])).sha256
    office_state(app).health = "ok"
    await upload(client, jonas, "brief.docx", docx_with_pages(3, "Nieuw"), parent,
                 conflict="replace")  # fmt: skip
    await jobs.run_pending(s)
    async with maker(s)() as session:
        version = await session.scalar(select(Version).where(Version.blob_id == old_sha))
    doc = await document(client, jonas, node["id"], version=str(version.id))
    assert (doc["kind"], doc["pdf_state"]) == ("office", "ask")
    full = await detail(client, jonas, node["id"], version=str(version.id))
    assert full["meta"]["pdf_state"] == "ask"
    # Het bestand zelf heeft zijn PDF
    assert (await document(client, jonas, node["id"]))["source"] == "office"
    await ask(client, jonas, node["id"], version=str(version.id))
    await jobs.run_pending(s)
    doc = await document(client, jonas, node["id"], version=str(version.id))
    assert (doc["kind"], doc["source"], doc["pages"]) == ("pdf", "office", 2)
    r = await client.get(page_of(doc, 2))
    assert r.status_code == 200


@pytest.mark.anyio
async def test_een_publieke_link_ziet_de_paginas_maar_start_niets(client, admin, office):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Gedeeld")
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2), parent=folder)
    other = await upload(client, jonas, "ander.docx", docx_with_pages(3), parent=folder)
    office_state(app).health = "down"
    await jobs.run_pending(s)
    office_state(app).health = "ok"
    await ask(client, jonas, node["id"])
    await jobs.run_pending(s)
    key = await open_link(client, jonas, folder)
    r = await client.get(f"/api/v1/public/document/{node['id']}", headers=key)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert (doc["kind"], doc["source"], doc["pages"]) == ("pdf", "office", 2)
    r = await client.get(page_of(doc, 2))
    assert r.status_code == 200 and r.headers["etag"].endswith('-p1"')
    # De andere heeft nog geen PDF; met de sleutel van de link gaat er geen conversie
    before = len(await office_jobs(s))
    for path in ("convert", "pdf"):
        r = await client.post(f"/api/v1/office/files/{other['id']}/{path}", headers=key)
        assert r.status_code == 401
    r = await client.post(
        f"/api/v1/office/files/{other['id']}/convert", params={"key": key["Authorization"][7:]}
    )
    assert r.status_code == 401
    assert len(await office_jobs(s)) == before
    assert (await blob_of(s, other["id"])).pdf_state is None


@pytest.mark.anyio
async def test_een_ingetrokken_deling_stopt_de_paginas(client, admin, office):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Gedeeld")
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2), parent=folder)
    await jobs.run_pending(s)
    share = await share_with(client, jonas, folder, user_id=anna_id, permission="read")
    doc = await document(client, anna, node["id"])
    assert doc["source"] == "office"
    assert (await client.get(page_of(doc))).status_code == 200
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)
    assert r.status_code == 204
    r = await client.get(page_of(doc, 2))
    assert r.status_code == 404
    await document(client, anna, node["id"], expect=404)
    r = await client.post(f"/api/v1/office/files/{node['id']}/convert", headers=anna)
    assert r.status_code == 404


@pytest.mark.anyio
async def test_zonder_office_precies_fase_3(client, admin, settings):
    jonas, _ = admin
    brief = await upload(client, jonas, "brief.docx", docx_bytes())
    old = await upload(client, jonas, "oud.doc", OLE)
    await jobs.run_pending(settings)
    doc = await document(client, jonas, brief["id"])
    assert (doc["kind"], doc["source"], doc["pdf_state"]) == ("office", None, None)
    assert doc["office"]["type"] == "document"
    assert "pdf_state" not in (await detail(client, jonas, brief["id"]))["meta"]
    r = await client.get(f"/api/v1/files/{old['id']}/document", headers=jonas)
    assert r.status_code == 422
    assert "pdf_state" not in (await detail(client, jonas, old["id"]))["meta"]
    assert await office_jobs(settings) == []
    assert (await blob_of(settings, brief["id"])).pdf_state is None


@pytest.mark.anyio
async def test_een_onbekend_bestand(client, admin, office):  # noqa: F811
    jonas, _ = admin
    r = await client.post(f"/api/v1/office/files/{uuid.uuid4()}/convert", headers=jonas)
    assert r.status_code == 404
    folder = await mkdir(client, jonas, (await root(client, jonas))["folder"]["id"], "Map")
    r = await client.post(f"/api/v1/office/files/{folder}/convert", headers=jonas)
    assert r.status_code == 422 and code_of(r) == "not_a_file"
    note = await upload(client, jonas, "notitie.txt", b"hallo")
    r = await client.post(f"/api/v1/office/files/{note['id']}/convert", headers=jonas)
    assert r.status_code == 422 and code_of(r) == "office_format"
