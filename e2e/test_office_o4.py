"""O4 (nieuw document) met de nep-DS, via de echte web-app (marker office; §2 O4 en het deel van
O4 van §4.4 punt 9 in docs/fase-4/bouwontwerp.md).

Nieuw → Document met alleen het toetsenbord (O, de pijltjes, Enter): de app maakt het lege
bestand (POST /api/v1/office/new), opent /bewerken/<id> en de nep-editor is klaar. Sluiten
zonder te typen geeft status 4 en geen versie. Ook via de knop Nieuw (Tab, ↑) in het Engels,
en via het submenu Nieuw in het contextmenu van de map, met een naam die al bestaat.

Sinds O1 geldt de echte poort: het cookie office uit POST …/session en het recht van nu (het
harnas zet PLATFORM_OFFICE_DEV_UNGATED niet meer).
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from conftest import Account, create_user
from office_harness import Office
from test_office_o2 import callback, ready, start, tab_to, versions

pytestmark = pytest.mark.office

NEW = "/api/v1/office/new"
# Elke rij van de lijst (ROW van O2 is alleen de rij met tabindex 0)
ROWS = "#inhoud [role=grid] [role=row]"


MENU = {
    "nl": ["Map", "Document", "Werkblad", "Presentatie"],
    "en": ["Folder", "Document", "Spreadsheet", "Presentation"],
}


def menu_text(lang: str) -> list[str | re.Pattern[str]]:
    """De tekst van de items van het menu Nieuw; bij Map staat de sneltoets N van fase 3."""
    folder, *rest = MENU[lang]
    return [re.compile(rf"^{folder}N$"), *rest]


def is_new(r: Any) -> bool:
    return r.request.method == "POST" and r.url.split("?", 1)[0].endswith(NEW)


def is_any_session(r: Any) -> bool:
    path = r.url.split("?", 1)[0]
    return (
        r.request.method == "POST" and "/api/v1/office/files/" in path and path.endswith("/session")
    )


def mkdir(office: Office, owner: Account, name: str) -> str:
    return office.server.call(
        owner, "POST", f"/api/v1/files/{owner.root_id}/folders", json={"name": name}, expect=201
    ).json()["id"]


def choose(page: Page, key: str) -> tuple[dict, str]:
    """Druk op key in een open menu (de keuze) en wacht op het nieuwe bestand en de sessie van
    de editor; geeft het bestand en de key van de sessie."""
    with page.expect_response(is_any_session) as session:
        with page.expect_response(is_new) as created:
            page.keyboard.press(key)
    assert created.value.status == 201, created.value.text()
    node = created.value.json()
    assert session.value.status == 200, session.value.text()
    assert session.value.url.split("?", 1)[0].endswith(f"/files/{node['id']}/session")
    return node, session.value.json()["key"]


def close_with_keys(page: Page, folder: str, close: str) -> None:
    """Alt+Shift+Q zet de focus op Sluiten (O2), Enter sluit: terug naar de map."""
    page.keyboard.press("Alt+Shift+Q")
    expect(page.get_by_role("button", name=close, exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"/map/{folder}$"))


def opened(page: Page, node: dict, lang: str) -> None:
    """De editorpagina van het nieuwe bestand is open en klaar; de iframe heeft de focus."""
    expect(page).to_have_url(re.compile(rf"/bewerken/{node['id']}$"))
    ready(page, lang)
    expect(page.get_by_role("heading", name=node["name"], exact=True)).to_be_visible()
    frame = page.locator("iframe[name=frameEditor]")
    expect(frame).to_have_attribute("title", f"Editor: {node['name']}")
    expect(frame).to_be_focused()


def test_nieuw_document_met_alleen_het_toetsenbord(office: Office, office_browsers) -> None:
    """In het Nederlands: O opent het menu Nieuw met de focus op Map; ↓ naar Document en Enter
    maakt "Nieuw document.docx" en opent de editor. Alt+Shift+Q en Enter sluiten: terug naar de
    map, waar het bestand staat; de nep-DS stuurt status 4 en er is geen versie. In het Engels:
    Tab naar de knop New, ↑ opent het menu onderaan (Presentation), nog eens ↑ naar Spreadsheet
    en Enter maakt "New spreadsheet.xlsx"."""
    anna = create_user(office.server, office.admin, name="Anna nieuw")
    folder = mkdir(office, anna, "Werk")
    page = office_browsers.context_as(anna, service_workers="block").new_page()

    # --- Nederlands: O, ↓, Enter ---
    office.server.call(anna, "PATCH", "/api/v1/auth/me", json={"locale": "nl"}, expect=200)
    page.goto(f"/map/{folder}")
    button = page.get_by_role("button", name="Nieuw", exact=True)
    expect(button).to_have_attribute("aria-keyshortcuts", "O")
    expect(button).to_have_attribute("aria-haspopup", "menu")
    expect(page.get_by_role("button", name="Nieuwe map", exact=True)).to_have_count(0)
    start(page)
    page.keyboard.press("o")
    menu = page.get_by_role("menu", name="Nieuw", exact=True)
    expect(menu).to_be_visible()
    expect(button).to_have_attribute("aria-expanded", "true")
    items = menu.get_by_role("menuitem")
    expect(items).to_have_text(menu_text("nl"))
    expect(menu.get_by_role("menuitem", name="Map", exact=True)).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(menu.get_by_role("menuitem", name="Document", exact=True)).to_be_focused()
    node, key = choose(page, "Enter")
    assert node["name"] == "Nieuw document.docx"
    assert node["parent_id"] == folder and node["permission"] == "owner"
    opened(page, node, "nl")
    close_with_keys(page, folder, "Sluiten")
    expect(page.locator(ROWS).filter(has_text="Nieuw document.docx")).to_be_visible()
    callback(office, key, 4)
    assert versions(office, anna, node["id"]) == []

    # --- Engels: Tab naar de knop, ↑, ↑, Enter ---
    office.server.call(anna, "PATCH", "/api/v1/auth/me", json={"locale": "en"}, expect=200)
    page.goto(f"/map/{folder}")
    button = page.get_by_role("button", name="New", exact=True)
    start(page)
    tab_to(page, button)
    page.keyboard.press("ArrowUp")
    menu = page.get_by_role("menu", name="New", exact=True)
    expect(menu.get_by_role("menuitem")).to_have_text(menu_text("en"))
    expect(menu.get_by_role("menuitem", name="Presentation", exact=True)).to_be_focused()
    page.keyboard.press("ArrowUp")
    expect(menu.get_by_role("menuitem", name="Spreadsheet", exact=True)).to_be_focused()
    node, key = choose(page, "Enter")
    assert node["name"] == "New spreadsheet.xlsx"
    opened(page, node, "en")
    close_with_keys(page, folder, "Close")
    expect(page.locator(ROWS).filter(has_text="New spreadsheet.xlsx")).to_be_visible()
    callback(office, key, 4)
    assert versions(office, anna, node["id"]) == []

    # Escape sluit het menu zonder iets te maken; de focus gaat terug naar de knop
    start(page)
    tab_to(page, button)
    page.keyboard.press("Enter")
    expect(menu).to_be_visible()
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect(button).to_be_focused()
    names = sorted(
        n["name"]
        for n in office.server.call(
            anna, "GET", f"/api/v1/files/{folder}/children", expect=200
        ).json()["children"]
    )
    assert names == ["New spreadsheet.xlsx", "Nieuw document.docx"]
    office.server.call(anna, "PATCH", "/api/v1/auth/me", json={"locale": None}, expect=200)


def test_nieuw_via_het_contextmenu_van_de_map(office: Office, office_browsers) -> None:
    """Meer acties in de kop van de lijst → Nieuw (→ opent het submenu) → End → Presentatie →
    Enter. De naam bestond al: "Nieuwe presentatie (1).pptx". Met alleen leesrecht is er geen
    menu Nieuw en doet O niets."""
    server = office.server
    anna = create_user(server, office.admin, name="Anna menu")
    bram = create_user(server, office.admin, name="Bram menu")
    folder = mkdir(office, anna, "Dia's")
    first = server.call(
        anna, "POST", NEW, json={"parent_id": folder, "kind": "pptx"}, expect=201
    ).json()
    assert first["name"] == "Nieuwe presentatie.pptx"

    page = office_browsers.context_as(anna, service_workers="block").new_page()
    page.goto(f"/map/{folder}")
    expect(page.locator(ROWS).filter(has_text="Nieuwe presentatie.pptx")).to_be_visible()
    more = page.get_by_role("button", name="Meer acties", exact=True)
    start(page)
    tab_to(page, more)
    page.keyboard.press("Enter")
    menu = page.get_by_role("menu", name="Meer acties", exact=True)
    new = menu.get_by_role("menuitem", name="Nieuw", exact=True)
    expect(new).to_be_focused()
    expect(new).to_have_attribute("aria-haspopup", "menu")
    page.keyboard.press("ArrowRight")
    sub = page.get_by_role("menu", name="Nieuw", exact=True)
    expect(sub.get_by_role("menuitem")).to_have_text(menu_text("nl"))
    expect(sub.get_by_role("menuitem", name="Map", exact=True)).to_be_focused()
    page.keyboard.press("End")
    expect(sub.get_by_role("menuitem", name="Presentatie", exact=True)).to_be_focused()
    node, key = choose(page, "Enter")
    assert node["name"] == "Nieuwe presentatie (1).pptx"
    assert node["mime"].endswith(".presentationml.presentation")
    opened(page, node, "nl")
    close_with_keys(page, folder, "Sluiten")
    callback(office, key, 4)
    assert versions(office, anna, node["id"]) == []

    # Bram met alleen leesrecht: de werkbalk van fase 3 zonder Nieuwe map, en O doet niets
    server.call(
        anna,
        "POST",
        f"/api/v1/files/{folder}/shares",
        json={"user_id": bram.id, "permission": "read"},
        expect=201,
    )
    reader = office_browsers.context_as(bram, service_workers="block").new_page()
    reader.goto(f"/map/{folder}")
    expect(reader.locator(ROWS).filter(has_text="Nieuwe presentatie.pptx")).to_be_visible()
    expect(reader.get_by_role("button", name="Nieuw", exact=True)).to_have_count(0)
    start(reader)
    reader.keyboard.press("o")
    reader.wait_for_timeout(300)
    expect(reader.get_by_role("menu")).to_have_count(0)
    assert len(server.call(anna, "GET", f"/api/v1/files/{folder}/children").json()["children"]) == 2
