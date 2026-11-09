"""De kaart (fase 6, Z5; Keuze 31–33) in de browser, alleen met het toetsenbord: Tab, Enter, de
pijlen, + en -, Home en Escape; nergens een klik (docs/fase-6/toetsenbord-z5.md).

- De kaart haalt niets van een andere host: de wereld komt als statisch bestand van de app zelf
  (/kaart/world-110m.<hash>.json), de cellen en de foto's van /api/v1. De test telt elk verzoek
  van de context (ook van de service worker) en eist 0 naar een andere host.
- Drie foto's met een plek (twee in Amsterdam, één in Parijs) en één zonder: samen 3 foto's op
  de kaart. + en - zoomen (een nieuw verzoek om cellen met een andere z), de pijlen schuiven,
  Home toont weer alles. Tab gaat van de kaart naar de stippen; Enter op een stip met meer dan
  één foto zoomt in (de focus gaat terug naar de kaart), Enter op een stip met één foto opent de
  viewer; Escape sluit hem.
- Het venster en de zoom staan nooit in de adresbalk (bbox en z niet, ook niet als query).

De foto's komen via de API binnen (PUT upload); photometa (Z3) leest hun plek. Draait na de
samenvoeging met Z3: zonder diens /photos/map is de kaart een fout.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Locator, Page, expect
from tests.search import fixtures as F

from conftest import Account, Server

pytestmark = pytest.mark.zoek

API = "/api/v1"
WACHT_S = 60.0
AMSTERDAM = [(52.3731, 4.8922), (52.3600, 4.8852)]
PARIJS = (48.8566, 2.3522)
ACTIEF = """() => {
  const a = document.activeElement;
  if (!a) return "-";
  const naam = (a.getAttribute("aria-label") || a.textContent || "").trim();
  return `${a.tagName} ${naam.slice(0, 40)}`;
}"""


def upload(server: Server, account: Account, naam: str, data: bytes) -> dict[str, Any]:
    return server.call(
        account,
        "PUT",
        f"{API}/files/{account.root_id}/upload",
        params={"name": naam},
        content=data,
        expect=201,
    ).json()


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


def tab_to(page: Page, target: Locator, limit: int = 60, back: bool = False) -> int:
    """Tab (of Shift+Tab) tot target de focus heeft; geeft het aantal keer."""
    target.wait_for(state="attached")
    gezien = []
    for i in range(limit):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return i + 1
        gezien.append(page.evaluate(ACTIEF))
    raise AssertionError(f"Tab kwam niet bij {target}: {gezien}")


def klaar(kaart: Locator) -> None:
    """Tot de kaart de cellen van het laatste verzoek toont (dan is aria-busy weg)."""
    expect(kaart).not_to_have_attribute("aria-busy", "true")


def is_cellen(url: str) -> bool:
    return "/api/v1/photos/map?" in url


def z_van(url: str) -> int:
    return int(parse_qs(urlsplit(url).query)["z"][0])


def aantal(label: str | None) -> int:
    m = re.search(r"(\d+) foto", label or "")
    assert m, label
    return int(m.group(1))


def test_kaart_met_alleen_het_toetsenbord_en_niets_van_buiten(
    server: Server, user_factory, context_as
) -> None:
    kim = user_factory(name="Kim Kaart")
    ams = [
        upload(server, kim, f"amsterdam-{i}.jpg", F.foto_gps(lat, lon, zaad=f"ams{i}"))["id"]
        for i, (lat, lon) in enumerate(AMSTERDAM, start=1)
    ]
    parijs = upload(server, kim, "parijs.jpg", F.foto_gps(*PARIJS, zaad="parijs"))["id"]
    zonder = upload(server, kim, "zonder-plek.jpg", F.foto_zonder_gps("zonder"))["id"]
    wacht_op_plek(server, kim, *ams, parijs)
    assert server.call(kim, "GET", f"{API}/files/{zonder}/location").status_code == 404

    context = context_as(kim)
    eigen = urlsplit(server.url).netloc
    hosts: list[str] = []
    cellen: list[str] = []

    def verzoek(r: Any) -> None:
        hosts.append(urlsplit(r.url).netloc)
        if is_cellen(r.url):
            cellen.append(r.url)

    context.on("request", verzoek)
    page = context.new_page()
    page.goto("/fotos/kaart")
    kaart = page.get_by_role("region", name="Kaart met je foto's", exact=True)
    expect(kaart).to_be_visible()
    status = page.locator(".map-page p.sr-only[role=status]")
    expect(status).to_have_text(re.compile(r"^\d+ plek(ken)? met foto's in beeld$"))
    stippen = kaart.locator("[data-dot]")
    labels = [stippen.nth(i).get_attribute("aria-label") for i in range(stippen.count())]
    assert sum(aantal(lab) for lab in labels) == 3, labels
    expect(page.get_by_role("heading", name="Plaatsen", exact=True)).to_be_visible()

    # --- naar de kaart met Tab; + zoomt in, - uit, de pijlen schuiven, Home weer alles ---
    # Na elke toets: wachten tot de kaart niet meer bezig is (aria-busy), zoals iemand die kijkt
    # tot de stippen er staan. Anders vraagt de volgende toets misschien niets nieuws, omdat het
    # vorige antwoord nog niet binnen was en het oude stuk nog past.
    tab_to(page, kaart)
    klaar(kaart)
    assert len(cellen) >= 1
    z0 = z_van(cellen[-1])
    with page.expect_request(lambda r: is_cellen(r.url)) as ingezoomd:
        page.keyboard.press("+")
    assert z_van(ingezoomd.value.url) > z0, (z0, ingezoomd.value.url)
    klaar(kaart)
    with page.expect_request(lambda r: is_cellen(r.url)) as uitgezoomd:
        page.keyboard.press("-")
    assert z_van(uitgezoomd.value.url) < z_van(ingezoomd.value.url)
    klaar(kaart)
    # Een pijl schuift een kwart venster, en de kaart laadt een kwart venster extra aan elke kant:
    # pas na drie keer rechts ligt er iets in beeld dat nog niet geladen is
    with page.expect_request(lambda r: is_cellen(r.url)) as geschoven:
        for _ in range(3):
            page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowDown")
    vak = parse_qs(urlsplit(geschoven.value.url).query)["bbox"][0]
    assert len(vak.split(",")) == 4, vak
    klaar(kaart)
    with page.expect_request(lambda r: is_cellen(r.url)) as thuis:
        page.keyboard.press("Home")
    assert z_van(thuis.value.url) == z0, (z0, thuis.value.url)
    klaar(kaart)
    expect(status).to_have_text(re.compile(r"^\d+ plek(ken)? met foto's in beeld$"))
    expect(kaart).to_be_focused()

    # --- een stip met één foto: Enter opent de viewer, Escape sluit hem ---
    # "Frankrijk, 1 foto" of "1 foto" (zonder plaatsnamen); "11 foto's" past er niet op
    een = kaart.locator('[data-dot][aria-label$="1 foto"]')
    expect(een).to_have_count(1)
    tab_to(page, een.first, limit=10)
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="parijs.jpg", exact=True)
    expect(viewer).to_be_visible()
    assert parse_qs(urlsplit(page.url).query).get("bestand") == [parijs]
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()

    # --- een stip met twee foto's: Enter zoomt in, de focus gaat naar de kaart ---
    twee = kaart.locator('[data-dot][aria-label$="2 foto\'s"]')
    expect(twee).to_have_count(1)
    tab_to(page, twee.first, limit=20)
    z_voor = z_van(cellen[-1])
    with page.expect_request(lambda r: is_cellen(r.url)) as dichterbij:
        page.keyboard.press("Enter")
    assert z_van(dichterbij.value.url) > z_voor
    klaar(kaart)
    expect(kaart).to_be_focused()

    # --- niets in de adresbalk, niets van buiten ---
    assert urlsplit(page.url).path == "/fotos/kaart"
    for woord in ("bbox", "z=", "lat", "lon"):
        assert woord not in urlsplit(page.url).query, page.url
    assert hosts, "geen verzoeken gezien"
    vreemd = sorted({h for h in hosts if h and h != eigen})
    assert vreemd == [], vreemd
