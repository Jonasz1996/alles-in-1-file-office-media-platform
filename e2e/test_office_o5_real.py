"""O5 (beheer en bewaking) met de echte DS, via de echte web-app (marker office_real).

Het tabblad Office (Alt+7) toont wat health.py bij de echte DS 10.0.0.1 las: de vastgepinde
versie (command version: 10.0.0.169), de licentie als "geen limiet" (2147483647) en dat DS
tokens eist; geen melding van office op het overzicht. Terwijl Anna en Bram één docx in de
echte editor bewerken, staan er 2 verbindingen, 2 mensen en 1 document; na het sluiten weer 0.

Lokaal zo (docs/fase-4/e2e.md), met een eigen container:

    OFFICE_DS_NAME=o5-ds OFFICE_DS_PORT=18091 OFFICE_DS_DIR=<map> bash e2e/office_ds.sh start
    ... bash e2e/office_ds.sh wait
    cd web && npm run build
    cd api && E2E_REAL_DS=http://127.0.0.1:18091 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright pytest ../e2e -m office_real
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import expect

from office_harness import Office
from test_office_o2_real import duo, info_users, open_via_button, uid, wait_until
from test_office_o5 import ADMIN, count, office_tab

pytestmark = pytest.mark.office_real

PINNED = "10.0.0.169"
UNLIMITED = 2_147_483_647
OFFICE_NOTICES = {
    "office_down",
    "office_misconfigured",
    "office_version",
    "office_limit",
    "office_unsaved",
    "office_convert_failed",
}


def test_beheer_met_de_echte_ds(real_office: Office, real_browsers) -> None:
    office = real_office
    name = f"beheer-{uuid.uuid4().hex[:6]}.docx"
    d = duo(office, name)

    # Wat health.py bij de echte DS las
    admin = office.server.call(office.admin, "GET", ADMIN, expect=200).json()
    assert admin["configured"] is True and admin["state"] == "ok", admin
    assert admin["version"] == PINNED, admin
    assert admin["license_connections"] == UNLIMITED, admin
    assert admin["tokens_enforced"] is True, admin
    status = office.server.call(office.admin, "GET", "/api/v1/admin/status", expect=200).json()
    found = {n["code"] for n in status["notices"]} & OFFICE_NOTICES
    assert not found, status["notices"]

    tab = office_tab(office, real_browsers)
    facts = tab.get_by_role("list", name="De document server", exact=True)
    expect(facts).to_contain_text(PINNED)
    expect(facts).not_to_contain_text("vastgepinde")
    expect(facts).to_contain_text("geen limiet")
    expect(facts).to_contain_text("geëist")

    # Twee gebruikers in de echte editor op één docx
    page_a = real_browsers.context_as(d.anna, service_workers="block").new_page()
    page_b = real_browsers.context_as(d.bram, service_workers="block").new_page()
    key, _ = open_via_button(page_a, d, d.node, name)
    key_b, _ = open_via_button(page_b, d, d.node, name)
    assert key_b == key
    users = wait_until(lambda: len(info_users(office, key)) == 2 and info_users(office, key), 30)
    assert sorted(users) == sorted([uid(office, d.anna), uid(office, d.bram)]), users

    expect(count(tab, "connections")).to_have_text("2", timeout=30_000)
    expect(count(tab, "people")).to_have_text("2")
    expect(count(tab, "documents")).to_have_text("1")
    row = tab.get_by_role("row").filter(has_text=name)
    expect(row).to_contain_text(f"{d.anna.display_name}, {d.bram.display_name}")
    admin = office.server.call(office.admin, "GET", ADMIN, expect=200).json()
    assert (admin["connections"], admin["people"], admin["documents"]) == (2, 2, 1), admin

    # Allebei sluiten: de sessie dicht, weer 0 verbindingen
    for page in (page_a, page_b):
        page.get_by_role("button", name="Sluiten", exact=True).click()
        expect(page).to_have_url(re.compile(rf"/map/{d.folder}\?bestand={d.node}$"))

    def closed() -> bool:
        r = office.server.call(d.anna, "GET", f"/api/v1/office/files/{d.node}", expect=200)
        return r.json()["open"] is None

    wait_until(closed, 60, "de sessie dicht")
    expect(count(tab, "connections")).to_have_text("0", timeout=30_000)
    peak = office.server.call(office.admin, "GET", ADMIN, expect=200).json()["peak"]
    assert peak is not None and peak["connections"] >= 2, peak
