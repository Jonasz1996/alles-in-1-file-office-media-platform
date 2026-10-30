"""Downloaden als PDF (O3, Keuze 26): POST /api/v1/office/files/{id}/pdf geeft een link met
een download-token; GET /api/v1/office/pdf/{id}?token= de PDF als bijlage, met het token, de
gebruiker en de toegang bij elk verzoek opnieuw (net als /download). Is de PDF er nog niet,
dan eerst een conversie (hooguit PDF_WAIT wachten, anders 409 pdf_pending)."""

import asyncio
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from app import jobs
from app.config import Settings
from app.main import app
from app.office import convert
from app.office.state import OfficeConfig, office_policy, office_state
from app.security import download_token
from tests.factories import code_of
from tests.test_office_convert import (  # noqa: F401
    blob_of,
    docx_with_pages,
    lying_docx,
    office,
    office_jobs,
)
from tests.test_shares import person, share_with
from tests.test_viewer import upload


async def pdf_link(client, headers, node_id, expect=200, **params):
    r = await client.post(f"/api/v1/office/files/{node_id}/pdf", headers=headers, params=params)
    assert r.status_code == expect, r.text
    return r


@pytest.mark.anyio
async def test_downloaden_als_pdf(client, admin, office):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    node = await upload(client, jonas, "Rapport 2024.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    link = (await pdf_link(client, jonas, node["id"])).json()
    assert link["expires_in"] == 600
    assert link["url"].startswith(f"/api/v1/office/pdf/{node['id']}?token=")
    r = await client.get(link["url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("attachment;")
    # Zoals /download: de naam (met een spatie) als filename* (RFC 5987)
    assert "filename*=utf-8''Rapport%202024.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-")
    assert "private" in r.headers["cache-control"]
    # Ook als DS weg is: de PDF staat bij de app
    office_state(app).health = "down"
    link = (await pdf_link(client, jonas, node["id"])).json()
    assert (await client.get(link["url"])).status_code == 200


@pytest.mark.anyio
async def test_met_office_uit_op_beheer_een_pdf_die_er_is(client, admin, office):  # noqa: F811
    """Uit op /beheer stopt nieuwe PDF's, niet het downloaden van een PDF die er al is (net als
    met DS weg). Een bestand zonder PDF krijgt dan geen conversie: 503 office_off."""
    jonas, _ = admin
    s = office.settings
    node = await upload(client, jonas, "Rapport.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    office_policy(app).config = OfficeConfig(enabled=False)
    link = (await pdf_link(client, jonas, node["id"])).json()
    r = await client.get(link["url"])
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    r = await client.post(f"/api/v1/office/files/{node['id']}/convert", headers=jonas)
    assert r.status_code == 202 and r.json() == {"pdf_state": "ready"}
    other = await upload(client, jonas, "Nieuw.docx", docx_with_pages(3))
    await jobs.run_pending(s)
    r = await pdf_link(client, jonas, other["id"], expect=503)
    assert code_of(r) == "office_off"
    r = await client.post(f"/api/v1/office/files/{other['id']}/convert", headers=jonas)
    assert r.status_code == 503 and code_of(r) == "office_off"


@pytest.mark.anyio
async def test_eerst_omzetten_dan_409_en_daarna_200(client, admin, office, monkeypatch):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    monkeypatch.setattr(convert, "PDF_WAIT", 0.2)
    office_state(app).health = "down"
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    assert (await blob_of(s, node["id"])).pdf_state is None
    office_state(app).health = "ok"
    # Nog geen PDF: een conversie op verzoek (vooraan in de rij), en na het wachten 409
    r = await pdf_link(client, jonas, node["id"], expect=409)
    assert code_of(r) == "pdf_pending"
    assert (await blob_of(s, node["id"])).pdf_state == "pending"
    [job] = await office_jobs(s, "queued")
    assert job.attempts == 0
    await jobs.run_pending(s)
    r = await pdf_link(client, jonas, node["id"])
    assert (await client.get(r.json()["url"])).status_code == 200


@pytest.mark.anyio
async def test_wachten_op_een_conversie_die_net_klaar_komt(client, admin, office):  # noqa: F811
    """POST /pdf wacht (hooguit PDF_WAIT) terwijl de worker omzet."""
    jonas, _ = admin
    s = office.settings
    office_state(app).health = "down"
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    office_state(app).health = "ok"
    worker = office.background(_work_soon(s))
    r = await pdf_link(client, jonas, node["id"])
    await worker
    assert (await client.get(r.json()["url"])).status_code == 200


async def _work_soon(s: Settings) -> None:
    for _ in range(500):
        if await jobs.run_pending(s):
            return
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_fouten_bij_een_pdf(client, admin, office):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    lying = await upload(client, jonas, "liegt.docx", lying_docx())
    office.conversions.configure({"fail": [-3]})
    broken = await upload(client, jonas, "kapot.docx", docx_with_pages(2))
    note = await upload(client, jonas, "notitie.txt", b"hallo")
    await jobs.run_pending(s)
    r = await pdf_link(client, jonas, lying["id"], expect=422)
    assert code_of(r) == "office_unsafe"
    r = await pdf_link(client, jonas, broken["id"], expect=502)
    assert code_of(r) == "office_convert_failed" and r.json()["detail"]["params"] == {"code": -3}
    r = await pdf_link(client, jonas, note["id"], expect=422)
    assert code_of(r) == "office_format"
    # Zonder PDF geen download, ook niet met een geldig token
    token = download_token(s, (await _user_id(client, jonas)), broken["id"])
    r = await client.get(f"/api/v1/office/pdf/{broken['id']}", params={"token": token})
    assert r.status_code == 404


async def _user_id(client, headers):
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"])


@pytest.mark.anyio
async def test_het_token_bij_elk_verzoek(client, admin, office):  # noqa: F811
    jonas, _ = admin
    s = office.settings
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    bob, _ = await person(client, jonas, "bob@example.nl")
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    other = await upload(client, jonas, "ander.docx", docx_with_pages(3))
    await jobs.run_pending(s)
    # Anna leest mee (een deling): zij mag de PDF; Bob niet
    share = await share_with(client, jonas, node["id"], user_id=anna_id, permission="read")
    url = (await pdf_link(client, anna, node["id"])).json()["url"]
    assert (await client.get(url)).status_code == 200
    await pdf_link(client, bob, node["id"], expect=404)
    # Het token hoort bij één bestand
    token = parse_qs(urlsplit(url).query)["token"][0]
    r = await client.get(f"/api/v1/office/pdf/{other['id']}", params={"token": token})
    assert r.status_code == 403 and code_of(r) == "wrong_token"
    # Ingetrokken: meteen weg, ook met het token dat ze al had
    assert (await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)).status_code == 204
    assert (await client.get(url)).status_code == 404
    # Verlopen
    old = Settings(**{**s.model_dump(), "download_token_minutes": -1})
    me = await _user_id(client, jonas)
    expired = download_token(old, me, node["id"])
    r = await client.get(f"/api/v1/office/pdf/{node['id']}", params={"token": expired})
    assert r.status_code == 401
    r = await client.get(f"/api/v1/office/pdf/{node['id']}", params={"token": "rommel"})
    assert r.status_code == 401
    r = await client.get(f"/api/v1/office/pdf/{node['id']}")
    assert r.status_code == 401
    # Een gewoon toegangstoken is geen download-token
    access = jonas["Authorization"].removeprefix("Bearer ")
    r = await client.get(f"/api/v1/office/pdf/{node['id']}", params={"token": access})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_zonder_office(client, admin, settings):
    jonas, _ = admin
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    r = await client.post(f"/api/v1/office/files/{node['id']}/pdf", headers=jonas)
    assert r.status_code == 503 and code_of(r) == "office_off"
    r = await client.get(f"/api/v1/office/pdf/{node['id']}", params={"token": "x"})
    assert r.status_code == 503 and code_of(r) == "office_off"


def test_de_naam_van_de_pdf():
    from app.routers.office_pdf import pdf_name

    assert pdf_name("Rapport 2024.docx") == "Rapport 2024.pdf"
    assert pdf_name("archief.tar.xlsx") == "archief.tar.pdf"
    assert pdf_name("zonder") == "zonder.pdf"
    assert pdf_name(".verborgen") == ".verborgen.pdf"
    assert pdf_name("") == "document.pdf"
