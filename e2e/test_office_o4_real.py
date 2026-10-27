"""O4 (nieuw document) met de echte DS, via de echte web-app (marker office_real; §4.5 punt 3
van docs/fase-4/bouwontwerp.md).

Voor docx, xlsx en pptx: Nieuw in het menu (met het toetsenbord) maakt het lege bestand en
opent het in de echte editor zonder onError; sluiten zonder wijziging geeft status 4 (de sessie
dicht met no_changes) en geen versie, en de inhoud is nog precies het lege document; omzetten
naar PDF lukt (pdf_state ready en een PDF met een pagina). Bij de docx ook A17: "Downloaden als
docx" in de editor geeft geen melding over het nieuwe formaat (compatibilityMode 15).

Draaien: zie test_office_o2_real.py en docs/fase-4/e2e.md. Zonder E2E_REAL_DS wordt hij
overgeslagen. Sinds O1 geldt de echte poort (het cookie office en het recht van nu), ook voor
"Downloaden als" (een editoractie); het harnas zet PLATFORM_OFFICE_DEV_UNGATED niet meer.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import time
import zipfile
from io import BytesIO
from typing import Any

import pypdfium2
import pytest
from playwright.sync_api import Page, expect

from conftest import Account, Server, artifacts_dir, create_user
from office_harness import Office, frame_editor
from test_office_o2 import start
from test_office_o2_real import ready, versions, wait_until
from test_office_o3 import detail, mkdir
from test_office_o4 import ROWS, choose

pytestmark = pytest.mark.office_real

# kind: (aantal keer ↓ vanaf Map in het menu Nieuw, de naam, het hoofdonderdeel)
KINDS = {
    "docx": (1, "Nieuw document.docx", "word/document.xml"),
    "xlsx": (2, "Nieuw werkblad.xlsx", "xl/workbook.xml"),
    "pptx": (3, "Nieuwe presentatie.pptx", "ppt/presentation.xml"),
}
# Hooguit zo lang na het maken tot de PDF er is (zoals §4.5 punt 2: 60 s, met marge voor de
# conversies die tegelijk lopen)
PDF_SECONDS = 90


def content(server: Server, account: Account, node: str) -> bytes:
    return server.call(account, "GET", f"/api/v1/files/{node}/content", expect=200).content


def session_row(server: Server, key: str) -> tuple[str, str | None]:
    """(state, closed_reason) van de sessie, uit de database van de server (alleen lezen)."""
    db = server.data_dir / "platform.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) as conn:
        row = conn.execute(
            "SELECT state, closed_reason FROM office_session WHERE key = ?", (key,)
        ).fetchone()
    assert row is not None, f"geen sessie met key {key}"
    return row[0], row[1]


def no_error(page: Page) -> None:
    """De editor meldde geen fout (onError geeft op de pagina "De editor meldt een fout")."""
    expect(page.get_by_text("De editor meldt een fout", exact=False)).to_have_count(0)


def download_as_docx(page: Page) -> tuple[bytes, str]:
    """Bestand → Downloaden als → DOCX in de echte editor (de knoppen uit de spike). Kwam de
    melding over het nieuwe formaat (een knop OK, A17), dan faalt de test met die reden."""
    frame = frame_editor(page)
    frame.locator("#file").click()
    frame.locator("#fm-btn-download").click()
    warning = frame.get_by_role("button", name="OK", exact=True)
    with page.expect_download(timeout=60_000) as download:
        frame.locator("#panel-saveas button.btn-doc-format[format='65']").click()
        end = time.monotonic() + 45
        while time.monotonic() < end:
            if warning.count() and warning.first.is_visible():
                raise AssertionError("A17: de editor toont de melding over het nieuwe formaat")
            if download.is_done():
                break
            page.wait_for_timeout(250)
    got = download.value
    path = got.path()
    assert path is not None, got.failure()
    data = path.read_bytes()
    # Terug naar het document (het menu Bestand dicht)
    back = frame.locator("#fm-btn-return")
    if back.count() and back.first.is_visible():
        back.first.click()
    else:
        page.keyboard.press("Escape")
    return data, got.suggested_filename


@pytest.mark.parametrize("kind", list(KINDS))
def test_nieuw_openen_sluiten_en_omzetten_echt(
    real_office: Office, real_browsers, tmp_path_factory, kind: str
) -> None:
    office = real_office
    server = office.server
    downs, name, main = KINDS[kind]
    anna = create_user(server, office.admin, name=f"Anna nieuw {kind}")
    server.call(anna, "PATCH", "/api/v1/auth/me", json={"locale": "nl"}, expect=200)
    folder = mkdir(server, anna, "Nieuw echt")
    page = real_browsers.context_as(anna, service_workers="block").new_page()

    # Nieuw met het toetsenbord: O, ↓ tot de soort, Enter
    page.goto(f"/map/{folder}")
    expect(page.get_by_role("button", name="Nieuw", exact=True)).to_be_visible()
    start(page)
    page.keyboard.press("o")
    expect(page.get_by_role("menu", name="Nieuw", exact=True)).to_be_visible()
    for _ in range(downs):
        page.keyboard.press("ArrowDown")
    node, key = choose(page, "Enter")
    assert node["name"] == name, node
    created = time.monotonic()
    blank = content(server, anna, node["id"])
    with zipfile.ZipFile(BytesIO(blank)) as archive:
        assert archive.testzip() is None and main in archive.namelist()

    # De echte editor opent zonder onError (A22: hooguit één keer opnieuw laden)
    expect(page).to_have_url(re.compile(rf"/bewerken/{node['id']}$"))
    reloads = ready(page)
    no_error(page)
    expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
    expect(page.locator("iframe[name=frameEditor]")).to_have_attribute("title", f"Editor: {name}")
    page.screenshot(path=str(artifacts_dir(tmp_path_factory) / f"o4-echt-{kind}.png"))

    warned = "-"
    if kind == "docx":
        data, filename = download_as_docx(page)
        assert data.startswith(b"PK\x03\x04"), data[:8]
        assert filename.endswith(".docx"), filename
        warned = "geen melding (A17)"
        no_error(page)

    # Sluiten zonder wijziging: terug naar de map, status 4, geen versie, dezelfde inhoud
    page.get_by_role("button", name="Sluiten", exact=True).click()
    expect(page).to_have_url(re.compile(rf"/map/{folder}$"))
    expect(page.locator(ROWS).filter(has_text=name)).to_be_visible()

    def closed() -> bool:
        r = server.call(anna, "GET", f"/api/v1/office/files/{node['id']}", expect=200)
        return r.json()["open"] is None

    wait_until(closed, 60, "status 4 en de sessie dicht")
    assert session_row(server, key) == ("closed", "no_changes")
    assert versions(office, anna, node["id"]) == []
    assert content(server, anna, node["id"]) == blank

    # Omzetten naar PDF: de haak van O3 zet het klaar; de download als PDF geeft pagina's
    def pdf_ready() -> bool:
        state = detail(server, anna, node["id"])["meta"].get("pdf_state")
        assert state in (None, "pending", "ready"), state
        return state == "ready"

    wait_until(pdf_ready, PDF_SECONDS, f"pdf_state ready voor {name}")
    seconds = round(time.monotonic() - created, 1)
    link = server.call(anna, "POST", f"/api/v1/office/files/{node['id']}/pdf", expect=200).json()
    r = server.request("GET", link["url"], expect=200)
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    pages = len(pypdfium2.PdfDocument(r.content))
    assert pages >= 1
    print(
        f"{name}: opnieuw geladen (A22) {reloads}, downloaden als docx {warned}, "
        f"PDF met {pages} pagina('s) na {seconds} s",
        file=sys.stderr,
    )
    server.call(anna, "PATCH", "/api/v1/auth/me", json={"locale": None}, expect=200)


def test_een_echte_editor_opent_ook_een_nieuw_engels_werkblad(
    real_office: Office, real_browsers
) -> None:
    """De Engelse lege bestanden (Sheet1, en-GB) openen net zo: hier het werkblad via de API
    gemaakt en via /bewerken/<id> geopend, en zonder wijziging gesloten."""
    office = real_office
    server = office.server
    bram = create_user(server, office.admin, name="Bram new")
    server.call(bram, "PATCH", "/api/v1/auth/me", json={"locale": "en"}, expect=200)
    folder = mkdir(server, bram, "New real")
    node = server.call(
        bram, "POST", "/api/v1/office/new", json={"parent_id": folder, "kind": "xlsx"}, expect=201
    ).json()
    assert node["name"] == "New spreadsheet.xlsx"
    page = real_browsers.context_as(bram, service_workers="block").new_page()
    page.goto(f"/map/{folder}")
    page.goto(f"/bewerken/{node['id']}")
    status = page.locator(".editor-status")
    expect(status).to_have_text(re.compile(r"^Editing"), timeout=120_000)
    expect(page.get_by_text("The editor reports an error", exact=False)).to_have_count(0)
    page.get_by_role("button", name="Close", exact=True).click()
    expect(page).to_have_url(re.compile(rf"/map/{folder}$"))

    def closed() -> Any:
        r = server.call(bram, "GET", f"/api/v1/office/files/{node['id']}", expect=200)
        return r.json()["open"] is None

    wait_until(closed, 60, "de sessie dicht")
    assert versions(office, bram, node["id"]) == []
    server.call(bram, "PATCH", "/api/v1/auth/me", json={"locale": None}, expect=200)
