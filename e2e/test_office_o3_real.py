"""Office O3 met de echte DS (marker office_real, §4.5 punt 2 van docs/fase-4/bouwontwerp.md).

Voorbeeld zonder editor: proef.docx, .xlsx, .pptx, .doc en .rtf uit e2e/fixtures/office/
uploaden → binnen 60 s pdf_state "ready" en pagina's in de viewer, zonder verkeer naar /office/
uit de browser. DS haalt de bron bij de interne luisteraar van de app (host.docker.internal) met
het download-JWT; de app haalt de PDF via fileUrl op (urls.rewrite). Draaien: zie
test_office_o0_real.py en docs/fase-4/e2e.md.
"""

from __future__ import annotations

import time

import pypdfium2
import pytest
from playwright.sync_api import expect

from conftest import create_user
from office_harness import Office, fixture
from test_office_o3 import detail, mkdir, thumb, until, upload, wait_pages, watch_office

pytestmark = pytest.mark.office_real

FILES = ("proef.docx", "proef.xlsx", "proef.pptx", "proef.doc", "proef.rtf")
# Hooguit zo lang na de upload (§4.5 punt 2)
READY_SECONDS = 60


def test_voorbeeld_zonder_editor_echt(real_office: Office, real_browsers) -> None:
    office = real_office
    server = office.server
    owner = create_user(server, office.admin, name="Voorbeeld echt")
    folder = mkdir(server, owner, "Kantoor echt")
    start = time.monotonic()
    ids = {name: upload(server, owner, folder, name, fixture(name)) for name in FILES}

    ready: dict[str, float] = {}

    def all_ready() -> bool:
        for name, node in ids.items():
            if name in ready:
                continue
            d = detail(server, owner, node)
            state = d["meta"].get("pdf_state")
            # None: de preview van B21 loopt nog (daarna zet de haak de conversie klaar)
            assert state in (None, "pending", "ready"), (name, state)
            if state == "ready":
                ready[name] = round(time.monotonic() - start, 1)
        return len(ready) == len(ids)

    until("alle vijf omgezet", all_ready, READY_SECONDS)
    print(f"klaar na (s): {ready}")

    # De PDF zelf: via de download als PDF, met pagina's
    for name, node in ids.items():
        link = server.call(owner, "POST", f"/api/v1/office/files/{node}/pdf", expect=200).json()
        r = server.request("GET", link["url"], expect=200)
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF-"), name
        pages = len(pypdfium2.PdfDocument(r.content))
        assert pages >= 1, name
        assert until(f"thumbnail van {name}", lambda n=node: thumb(server, owner, n), 30)

    context = real_browsers.context_as(owner, service_workers="block")
    page = context.new_page()
    traffic = watch_office(page)
    for name, node in ids.items():
        page.goto(f"/map/{folder}?bestand={node}")
        viewer = page.get_by_role("dialog", name=name, exact=True)
        expect(viewer).to_be_visible()
        expect(viewer.locator(".pages-note")).to_contain_text("Voorbeeld")
        wait_pages(page)
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)
    assert traffic == []
