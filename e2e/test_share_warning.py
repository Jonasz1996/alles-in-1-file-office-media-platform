"""De waarschuwing over de locatie in het deeldialoog (fase 6, Z5; Keuze 30) in de browser, alleen
met het toetsenbord: S opent het dialoog, Alt+P het zoekveld, Alt+L de publieke link, Escape
sluit (docs/fase-6/toetsenbord-z5.md).

- Een map met twee foto's met een plek, één zonder en een document: "2 foto's hierin hebben een
  locatie in het bestand. Wie ze downloadt, krijgt die mee." Het dialoog wacht er niet op: zolang
  de controle (GET /photos/share-check) nog loopt, werkt het zoekveld al; komt het antwoord, dan
  staat de zin bovenaan, leest de live-regio hem voor en blijft de focus in het zoekveld.
- Bij een publieke link (Alt+L, Link maken) is het hetzelfde dialoog: de zin staat er ook dan.
- Een los bestand met een plek: "Deze foto heeft een locatie in het bestand. …"; een document:
  geen zin en geen controle.
- Een album met één foto met een plek: "1 foto hierin heeft een locatie in het bestand. …"

De foto's komen via de API binnen (PUT upload); photometa (Z3) leest hun plek. Draait na de
samenvoeging met Z3: zonder diens /photos/share-check is er geen zin.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Locator, Page, expect
from tests.search import fixtures as F

from conftest import Account, Server
from seed_browser import mkdir, upload

pytestmark = pytest.mark.zoek

API = "/api/v1"
WACHT_S = 60.0
ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"
FOCUSED = '[role=gridcell][tabindex="0"]'
CHECK = "**/api/v1/photos/share-check?*"
MAP_ZIN = "2 foto's hierin hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee."
FOTO_ZIN = "Deze foto heeft een locatie in het bestand. Wie hem downloadt, krijgt die mee."
ALBUM_ZIN = "1 foto hierin heeft een locatie in het bestand. Wie hem downloadt, krijgt die mee."
ACTIEF = """() => {
  const a = document.activeElement;
  if (!a) return "-";
  const naam = (a.getAttribute("aria-label") || a.textContent || "").trim();
  return `${a.tagName} ${naam.slice(0, 40)}`;
}"""


def wacht_op_plek(server: Server, account: Account, *ids: str) -> None:
    """Tot photometa de plek van elk bestand gelezen heeft (GET /files/{id}/location: 200)."""
    eind = time.monotonic() + WACHT_S
    open_ = set(ids)
    while open_:
        for i in list(open_):
            if server.call(account, "GET", f"{API}/files/{i}/location").status_code == 200:
                open_.discard(i)
        if open_ and time.monotonic() > eind:
            raise AssertionError(f"geen plek binnen {WACHT_S:.0f} s voor {sorted(open_)}")
        time.sleep(0.2)


def tab_to(page: Page, target: Locator, limit: int = 40) -> int:
    """Tab tot target de focus heeft; geeft het aantal keer."""
    target.wait_for(state="attached")
    gezien = []
    for i in range(limit):
        page.keyboard.press("Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return i + 1
        gezien.append(page.evaluate(ACTIEF))
    raise AssertionError(f"Tab kwam niet bij {target}: {gezien}")


def row(page: Page, name: str) -> Locator:
    return page.locator(ROWS).filter(has=page.get_by_text(name, exact=True))


def enter_list(page: Page) -> None:
    page.locator(f'{ROWS}[tabindex="0"]').focus()


def expect_focus(page: Page, name: str) -> None:
    """De focus staat (zo meteen) op de rij met deze naam."""
    page.wait_for_function(
        """(name) => {
          const row = document.activeElement && document.activeElement.closest("[role=row]");
          const el = row && row.querySelector(".name");
          return !!el && el.textContent === name;
        }""",
        arg=name,
        timeout=5000,
    )


def node_van(url: str) -> str:
    return parse_qs(urlsplit(url).query)["node"][0]


def waarschuwing(dialog: Locator) -> Locator:
    return dialog.get_by_role("note")


def voorgelezen(dialog: Locator, zin: str) -> None:
    """De live-regio (in het bovenste dialoog) leest de zin voor."""
    expect(dialog.locator('[aria-live="polite"]')).to_have_text(zin)


def test_een_map_het_dialoog_wacht_niet_en_ook_bij_een_publieke_link(
    server: Server, user_factory, context_as
) -> None:
    sam = user_factory(name="Sam Deel")
    reis = mkdir(server, sam, sam.root_id, "Reis")
    met = [
        upload(server, sam, reis, "reis-1.jpg", F.foto_gps(52.3731, 4.8922, zaad="reis1")),
        upload(server, sam, reis, "reis-2.jpg", F.foto_gps(48.8566, 2.3522, zaad="reis2")),
    ]
    upload(server, sam, reis, "reis-zonder.jpg", F.foto_zonder_gps("reis3"))
    upload(server, sam, reis, "notities.txt", b"mee: paspoort\n")
    wacht_op_plek(server, sam, *met)

    page = context_as(sam).new_page()
    # De controle houden we vast: het dialoog moet al werken voor het antwoord er is
    vast: list[Any] = []
    page.route(CHECK, lambda route: vast.append(route))
    page.goto("/")
    page.locator(ROWS).first.wait_for()
    enter_list(page)
    page.keyboard.press("Home")
    expect_focus(page, "Reis")
    page.keyboard.press("s")
    dialog = page.get_by_role("dialog", name="Reis delen", exact=True)
    expect(dialog).to_be_visible()
    eind = time.monotonic() + 15
    while not vast and time.monotonic() < eind:
        page.wait_for_timeout(50)
    assert [node_van(r.request.url) for r in vast] == [reis]

    # Zonder antwoord: geen zin, en het zoekveld werkt al
    page.keyboard.press("Alt+P")
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    expect(box).to_be_focused()
    box.press_sequentially("Bra", delay=20)
    expect(waarschuwing(dialog)).to_have_count(0)

    # Het antwoord: de zin staat er, wordt voorgelezen, en de focus blijft waar hij was
    for r in vast:
        r.continue_()
    page.unroute(CHECK)
    expect(waarschuwing(dialog)).to_have_text(MAP_ZIN)
    voorgelezen(dialog, MAP_ZIN)
    expect(box).to_be_focused()
    expect(box).to_have_value("Bra")

    # Een publieke link: hetzelfde dialoog, de zin staat er nog
    page.keyboard.press("Alt+L")
    tab_to(page, dialog.get_by_role("button", name="Link maken", exact=True), limit=20)
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/files/{reis}/links")
    ) as gemaakt:
        page.keyboard.press("Enter")
    assert gemaakt.value.status == 201
    expect(dialog.get_by_role("button", name="Link kopiëren", exact=False).first).to_be_visible()
    expect(waarschuwing(dialog)).to_have_text(MAP_ZIN)

    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()


def test_een_los_bestand_wel_en_een_document_niet(server: Server, user_factory, context_as):
    lot = user_factory(name="Lot Los")
    los = upload(server, lot, lot.root_id, "los.jpg", F.foto_gps(51.9225, 4.4792, zaad="los"))
    brief = upload(server, lot, lot.root_id, "brief.txt", b"Beste Lot,\n")
    wacht_op_plek(server, lot, los)

    page = context_as(lot).new_page()
    controles: list[str] = []
    page.on(
        "request",
        lambda r: controles.append(node_van(r.url)) if "/photos/share-check?" in r.url else None,
    )
    page.goto("/")
    page.locator(ROWS).first.wait_for()
    enter_list(page)

    # Een foto met een plek
    page.keyboard.press("End")
    expect_focus(page, "los.jpg")
    with page.expect_response(lambda r: "/photos/share-check?" in r.url) as check:
        page.keyboard.press("s")
    assert check.value.json() == {"with_location": 1, "unknown": 0}
    dialog = page.get_by_role("dialog", name="los.jpg delen", exact=True)
    expect(waarschuwing(dialog)).to_have_text(FOTO_ZIN)
    voorgelezen(dialog, FOTO_ZIN)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect_focus(page, "los.jpg")

    # Een document: geen controle, geen zin (het stuk van de waarschuwing is al geladen)
    page.keyboard.press("ArrowUp")
    expect_focus(page, "brief.txt")
    page.keyboard.press("s")
    dialog = page.get_by_role("dialog", name="brief.txt delen", exact=True)
    expect(dialog.get_by_text("Nog met niemand gedeeld.")).to_be_visible()
    expect(waarschuwing(dialog)).to_have_count(0)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    assert controles == [los], controles
    assert brief not in controles


def test_een_album_met_een_foto_met_een_plek(server: Server, user_factory, context_as):
    ad = user_factory(name="Ad Album")
    met = upload(server, ad, ad.root_id, "zee.jpg", F.foto_gps(52.1, 4.27, zaad="zee"))
    zonder = upload(server, ad, ad.root_id, "duin.jpg", F.foto_zonder_gps("duin"))
    wacht_op_plek(server, ad, met)
    album = server.call(ad, "POST", f"{API}/albums", json={"name": "Kust"}, expect=201).json()["id"]
    server.call(
        ad, "POST", f"{API}/albums/{album}/items", json={"node_ids": [met, zonder]}, expect=200
    )

    page = context_as(ad).new_page()
    page.goto(f"/album/{album}")
    cells = page.get_by_role("grid", name="Foto's in Kust", exact=True).get_by_role("gridcell")
    expect(cells).to_have_count(2)
    tab_to(page, page.locator(FOCUSED), limit=60)
    with page.expect_response(lambda r: "/photos/share-check?" in r.url) as check:
        page.keyboard.press("s")
    assert node_van(check.value.url) == album
    assert check.value.json() == {"with_location": 1, "unknown": 0}
    dialog = page.get_by_role("dialog", name="Kust delen", exact=True)
    expect(waarschuwing(dialog)).to_have_text(ALBUM_ZIN)
    voorgelezen(dialog, ALBUM_ZIN)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
