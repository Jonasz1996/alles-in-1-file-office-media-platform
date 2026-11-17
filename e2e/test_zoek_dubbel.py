"""Dubbele bestanden op /dubbel (fase 6, Z5; Keuze 34–36) in de browser, alleen met het
toetsenbord: Tab, de pijlen, Home en End, Spatie en Enter (docs/fase-6/toetsenbord-z5.md).

- Precies gelijk: strand.jpg in de hoofdmap en dezelfde bytes als strand-kopie.jpg in de map
  Kopie. Niets staat vooraf aangevinkt en "Naar de prullenbak" doet nog niets. De pijlen gaan
  binnen de groep van bestand naar bestand; Spatie vinkt de kopie aan, Enter op "1 bestand naar
  de prullenbak" stuurt alleen die naar de prullenbak (de bestaande route POST /batch/trash). De
  groep is weg; de focus staat op de kop van de pagina.
- Lijken erop (?soort=lijkt): berg.jpg en een verkleinde kopie. Ook hier niets aangevinkt;
  "Niet gelijk" (POST /duplicates/ignore, 204) haalt de groep weg, ook na opnieuw laden.

De foto's komen via de API binnen (PUT upload). Draait na de samenvoeging met Z3: zonder diens
/duplicates is de pagina een fout.
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
ACTIEF = """() => {
  const a = document.activeElement;
  if (!a) return "-";
  const naam = (a.getAttribute("aria-label") || a.textContent || "").trim();
  return `${a.tagName} ${naam.slice(0, 40)}`;
}"""


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


def wacht_op_groep(server: Server, account: Account, kind: str, ids: set[str]) -> str:
    """Tot GET /duplicates?kind= een groep met precies deze bestanden geeft; geeft de key."""
    eind = time.monotonic() + WACHT_S
    gezien: Any = None
    while time.monotonic() < eind:
        r = server.call(account, "GET", f"{API}/duplicates", params={"kind": kind})
        assert r.status_code == 200, (r.status_code, r.text)
        gezien = r.json()
        for g in gezien["groups"]:
            if {it["node"]["id"] for it in g["items"]} == ids:
                return g["key"]
        time.sleep(0.3)
    raise AssertionError(f"geen groep {kind} met {sorted(ids)} binnen {WACHT_S:.0f} s: {gezien}")


def is_dubbel(kind: str):
    def match(r: Any) -> bool:
        if r.request.method != "GET" or "/api/v1/duplicates?" not in r.url:
            return False
        return parse_qs(urlsplit(r.url).query).get("kind") == [kind]

    return match


def toast(page: Page, tekst: str) -> Locator:
    """Een melding (niet de kopie voor de schermlezer)."""
    return page.get_by_role("region", name="Meldingen").get_by_text(tekst, exact=True)


def in_de_prullenbak(server: Server, account: Account) -> list[str]:
    return [n["id"] for n in server.call(account, "GET", f"{API}/trash", expect=200).json()]


def niets_aangevinkt(groep: Locator, n: int) -> None:
    vinkjes = groep.get_by_role("checkbox")
    expect(vinkjes).to_have_count(n)
    for i in range(n):
        expect(vinkjes.nth(i)).not_to_be_checked()
    expect(groep.get_by_role("button", name="Naar de prullenbak", exact=True)).to_be_disabled()
    expect(groep.get_by_text("Vink eerst aan wat weg mag.", exact=True)).to_be_visible()


def test_dubbel_met_alleen_het_toetsenbord_en_niets_vooraf_aangevinkt(
    server: Server, user_factory, context_as
) -> None:
    dirk = user_factory(name="Dirk Dubbel")
    strand_bytes = F.foto_zonder_gps("strand")
    strand = upload(server, dirk, dirk.root_id, "strand.jpg", strand_bytes)
    kopie_map = mkdir(server, dirk, dirk.root_id, "Kopie")
    kopie = upload(server, dirk, kopie_map, "strand-kopie.jpg", strand_bytes)
    berg_bytes = F.foto_zonder_gps("berg")
    berg = upload(server, dirk, dirk.root_id, "berg.jpg", berg_bytes)
    berg_klein = upload(server, dirk, dirk.root_id, "berg-klein.jpg", F.foto_klein(berg_bytes))
    wacht_op_groep(server, dirk, "exact", {strand, kopie})
    bijna = wacht_op_groep(server, dirk, "near", {berg, berg_klein})

    page = context_as(dirk).new_page()
    with page.expect_response(is_dubbel("exact")) as geladen:
        page.goto("/dubbel")
    assert geladen.value.status == 200
    titel = page.get_by_role("heading", name="Dubbele bestanden", level=1)
    expect(titel).to_be_visible()
    exact_tab = page.get_by_role("tab", name="Precies gelijk", exact=True)
    expect(exact_tab).to_have_attribute("aria-selected", "true")

    # --- precies gelijk: niets aangevinkt, de pijlen binnen de groep ---
    groep = page.locator("section.dupe-group").filter(has_text="strand-kopie.jpg")
    expect(groep).to_have_count(1)
    expect(groep.get_by_role("heading", level=2)).to_contain_text("Precies gelijk")
    expect(groep.get_by_role("heading", level=2)).to_contain_text("2 bestanden")
    niets_aangevinkt(groep, 2)
    expect(groep.get_by_role("link", name="Mijn bestanden / Kopie", exact=True)).to_be_visible()
    bekijk = groep.locator('[data-dupe="view"]')
    tab_to(page, bekijk.first, limit=20)
    page.keyboard.press("ArrowRight")
    expect(bekijk.nth(1)).to_be_focused()
    page.keyboard.press("Home")
    expect(bekijk.first).to_be_focused()
    page.keyboard.press("End")
    expect(bekijk.nth(1)).to_be_focused()

    # --- Spatie vinkt alleen de kopie aan; Enter stuurt alleen die naar de prullenbak ---
    vink = groep.get_by_role("checkbox", name="strand-kopie.jpg kiezen voor de prullenbak")
    tab_to(page, vink, limit=10)
    page.keyboard.press(" ")
    expect(vink).to_be_checked()
    ander = groep.get_by_role("checkbox", name="strand.jpg kiezen voor de prullenbak")
    expect(ander).not_to_be_checked()
    weg = groep.get_by_role("button", name="1 bestand naar de prullenbak", exact=True)
    tab_to(page, weg, limit=10)
    with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/api/v1/batch/trash")
    ) as verzoek:
        page.keyboard.press("Enter")
    assert verzoek.value.post_data_json == {"ids": [kopie]}
    expect(toast(page, "1 bestand staat in de prullenbak")).to_be_visible()
    expect(groep).to_have_count(0)
    expect(titel).to_be_focused()
    expect(page.get_by_text("Geen bestanden die precies gelijk zijn", exact=True)).to_be_visible()
    assert in_de_prullenbak(server, dirk) == [kopie]

    # --- lijken erop: met de pijl naar het tweede tabblad; ook daar niets aangevinkt ---
    tab_to(page, exact_tab, limit=20)
    with page.expect_response(is_dubbel("near")) as bijna_geladen:
        page.keyboard.press("ArrowRight")
    assert bijna_geladen.value.status == 200
    near_tab = page.get_by_role("tab", name="Lijken erop", exact=True)
    expect(near_tab).to_have_attribute("aria-selected", "true")
    assert parse_qs(urlsplit(page.url).query).get("soort") == ["lijkt"]
    groep = page.locator("section.dupe-group").filter(has_text="berg-klein.jpg")
    expect(groep).to_have_count(1)
    kop = groep.get_by_role("heading", level=2)
    expect(kop).to_contain_text("2 bestanden")
    assert ("Bijna zeker gelijk" in kop.inner_text()) or ("Lijkt erop" in kop.inner_text())
    niets_aangevinkt(groep, 2)

    # --- Niet gelijk: de groep weg, ook na opnieuw laden ---
    niet = groep.get_by_role("button", name="Niet gelijk", exact=True)
    tab_to(page, niet, limit=20)
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/api/v1/duplicates/ignore")
    ) as genegeerd:
        page.keyboard.press("Enter")
    assert genegeerd.value.status == 204
    assert genegeerd.value.request.post_data_json == {"key": bijna}
    expect(toast(page, "Deze groep komt niet meer terug.")).to_be_visible()
    expect(groep).to_have_count(0)
    expect(titel).to_be_focused()
    with page.expect_response(is_dubbel("near")):
        page.reload()
    expect(page.get_by_role("tab", name="Lijken erop", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.get_by_text("Geen foto's die erop lijken", exact=True)).to_be_visible()
    # "Niet gelijk" gooit niets weg: in de prullenbak staat nog steeds alleen de kopie
    assert in_de_prullenbak(server, dirk) == [kopie]
