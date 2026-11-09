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
- De focus valt nooit naar de body (review golf 3, web F1–F4): + en de pijlen op een stip, Enter
  op "Alle plaatsen" boven de kaart, "Op de kaart" in de viewer en Uitzoomen op de grens.
- De lijst Plaatsen (review golf 3, web F5), met een places.sqlite uit de kleine bronnen van
  api/tests/geo; en de kaart vraagt bij het openen geen cellen twee keer (web F6).

De foto's komen via de API binnen (PUT upload); photometa (Z3) leest hun plek. Draait na de
samenvoeging met Z3: zonder diens /photos/map is de kaart een fout.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
import time
from pathlib import Path
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
SYDNEY = (-33.8688, 151.2093)
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


def op_de_kaart(kaart: Locator) -> int:
    """Hoeveel foto's de stippen samen tellen."""
    stippen = kaart.locator("[data-dot]")
    return sum(aantal(stippen.nth(i).get_attribute("aria-label")) for i in range(stippen.count()))


def wacht_op_aantal(kaart: Locator, n: int, seconden: float = 15.0) -> None:
    """Tot de stippen samen n foto's tellen. Na een ander filter laadt de kaart eerst de cellen
    van het venster van nu (aria-busy gaat dan al weg), en pas daarna zet Home al je foto's in
    beeld; klaar() alleen is dan te vroeg."""
    eind = time.monotonic() + seconden
    while (gezien := op_de_kaart(kaart)) != n:
        if time.monotonic() > eind:
            raise AssertionError(f"{gezien} foto's op de kaart, niet {n}")
        time.sleep(0.1)


def plaats_van(url: str) -> list[str]:
    return parse_qs(urlsplit(url).query).get("place", [])


def met_plaatsen(data_dir: Path) -> None:
    """places.sqlite in de datamap, gebouwd uit de kleine bronnen van api/tests/geo (Amsterdam,
    Utrecht, … in een Nederland van vier hoeken). De meta noemt de bronnen van deze versie, dus
    de server bouwt hem bij de start niet opnieuw (load.current); de bronnen zelf komen niet in
    de datamap."""
    from tests.geo import bronnen

    from app.geo import load

    pad = data_dir / "geo" / "places.sqlite"
    with tempfile.TemporaryDirectory() as tmp:
        b = bronnen.maak(Path(tmp))
        load.build(Path(tmp), pad, b.landen, b.plaatsen)
    db = sqlite3.connect(pad)
    try:
        db.execute("UPDATE meta SET value = ? WHERE key = 'sha_landen'", (load.LANDEN.sha256,))
        db.execute("UPDATE meta SET value = ? WHERE key = 'sha_plaatsen'", (load.PLAATSEN.sha256,))
        db.commit()
    finally:
        db.close()
    assert load.current(pad)


def zonder_plaatsen(data_dir: Path) -> None:
    (data_dir / "geo" / "places.sqlite").unlink(missing_ok=True)


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


def test_de_focus_valt_niet_naar_de_body(server: Server, user_factory, context_as) -> None:
    """Review golf 3, web F1, F3, F4 en F6, alleen met het toetsenbord.

    - F6: bij het openen vraagt de kaart geen cellen twee keer (Home vraagt zoom 0 voor de hele
      wereld, en met foto's in Amsterdam en Sydney vraagt het eerste venster precies hetzelfde).
    - F1: + of een pijl op een stip: de stip is daarna weg, de kaart krijgt de focus.
    - F4: Uitzoomen tot de grens: de knop houdt de focus (aria-disabled, niet disabled).
    - F3: "Op de kaart" in de viewer die op de kaart openging: de viewer sluit en de kaart
      krijgt de focus, met de ring om de plek.
    """
    eva = user_factory(name="Eva Ver")
    ams = [
        upload(server, eva, f"amsterdam-{i}.jpg", F.foto_gps(lat, lon, zaad=f"eva-ams{i}"))["id"]
        for i, (lat, lon) in enumerate(AMSTERDAM, start=1)
    ]
    sydney = upload(server, eva, "sydney.jpg", F.foto_gps(*SYDNEY, zaad="eva-sydney"))["id"]
    wacht_op_plek(server, eva, *ams, sydney)

    context = context_as(eva)
    cellen: list[str] = []
    context.on("request", lambda r: cellen.append(r.url) if is_cellen(r.url) else None)
    page = context.new_page()
    page.goto("/fotos/kaart")
    kaart = page.get_by_role("region", name="Kaart met je foto's", exact=True)
    status = page.locator(".map-page p.sr-only[role=status]")
    expect(status).to_have_text(re.compile(r"^\d+ plek(ken)? met foto's in beeld$"))
    klaar(kaart)
    wacht_op_aantal(kaart, 3)

    # --- F6: geen verzoek om cellen twee keer bij het openen ---
    dubbel = sorted({u for u in cellen if cellen.count(u) > 1})
    assert dubbel == [], cellen

    # --- F1: een pijl en + op een stip; daarna Home op de kaart ---
    stip = kaart.locator("[data-dot]").first
    for toets in ("ArrowLeft", "+"):
        tab_to(page, stip, limit=10)
        page.keyboard.press(toets)
        expect(kaart).to_be_focused()
        klaar(kaart)
        page.keyboard.press("Home")
        klaar(kaart)
        expect(kaart).to_be_focused()

    # --- F4: inzoomen op de kaart, dan Uitzoomen tot de grens; de focus blijft op de knop ---
    page.keyboard.press("+")
    klaar(kaart)
    page.keyboard.press("+")
    klaar(kaart)
    uit = page.get_by_role("button", name="Uitzoomen", exact=True)
    tab_to(page, uit, limit=20)
    expect(uit).not_to_have_attribute("aria-disabled", "true")
    for _ in range(12):
        if uit.get_attribute("aria-disabled") == "true":
            break
        page.keyboard.press("Enter")
        klaar(kaart)
        expect(uit).to_be_focused()
    expect(uit).to_have_attribute("aria-disabled", "true")
    page.keyboard.press("Enter")
    expect(uit).to_be_focused()
    assert page.evaluate("() => !!document.activeElement.closest('.map-controls')")

    # --- F3: de viewer van de stip in Sydney, de rij Plek, "Op de kaart" ---
    een = kaart.locator('[data-dot][aria-label$="1 foto"]')
    expect(een).to_have_count(1)
    tab_to(page, een.first, limit=20, back=True)
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="sydney.jpg", exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press("i")
    naar_kaart = viewer.get_by_role("link", name="Op de kaart", exact=True)
    tab_to(page, naar_kaart, limit=40)
    page.keyboard.press("Enter")
    expect(viewer).to_be_hidden()
    expect(kaart).to_be_focused()
    expect(kaart.locator(".map-mark")).to_have_count(1)
    assert urlsplit(page.url).path == "/fotos/kaart"
    assert urlsplit(page.url).query == "", page.url


def test_plaatsen_met_alleen_het_toetsenbord(
    server: Server, user_factory, context_as, seeded
) -> None:
    """Review golf 3, web F5 en F2: de lijst Plaatsen in de e2e van CI. Met places.sqlite krijgen
    de twee foto's in Amsterdam de plaats Amsterdam (Parijs ligt niet in de kleine bronnen: geen
    plaats). Enter op Amsterdam zet het filter place (aria-pressed, het verzoek om cellen heeft
    place=<id>, "Alleen Amsterdam" boven de kaart); "Alle plaatsen" in de lijst haalt het weg, en
    "Alle plaatsen" boven de kaart ook, waarna de kaart de focus heeft (de knop zelf is weg)."""
    seeded(met_plaatsen)
    try:
        lin = user_factory(name="Lin Lijst")
        ams = [
            upload(server, lin, f"amsterdam-{i}.jpg", F.foto_gps(lat, lon, zaad=f"lin{i}"))["id"]
            for i, (lat, lon) in enumerate(AMSTERDAM, start=1)
        ]
        parijs = upload(server, lin, "parijs.jpg", F.foto_gps(*PARIJS, zaad="lin-parijs"))["id"]
        wacht_op_plek(server, lin, *ams, parijs)
        lijst = server.call(lin, "GET", f"{API}/photos/places").json()["places"]
        assert [(p["name"], p["n"]) for p in lijst] == [("Amsterdam", 2)], lijst
        amsterdam = str(lijst[0]["id"])

        context = context_as(lin)
        page = context.new_page()
        page.goto("/fotos/kaart")
        kaart = page.get_by_role("region", name="Kaart met je foto's", exact=True)
        status = page.locator(".map-page p.sr-only[role=status]")
        expect(status).to_have_text(re.compile(r"^\d+ plek(ken)? met foto's in beeld$"))
        klaar(kaart)
        wacht_op_aantal(kaart, 3)
        plaatsen = page.get_by_role("region", name="Plaatsen", exact=True)
        knop = plaatsen.locator("button.map-place", has_text="Amsterdam")
        alle = plaatsen.get_by_role("button", name="Alle plaatsen", exact=True)
        expect(alle).to_have_attribute("aria-pressed", "true")
        alleen = page.locator(".map-only")

        # --- Enter op Amsterdam: alleen die foto's ---
        tab_to(page, knop)
        with page.expect_request(lambda r: is_cellen(r.url) and bool(plaats_van(r.url))) as vraag:
            page.keyboard.press("Enter")
        assert plaats_van(vraag.value.url) == [amsterdam], vraag.value.url
        expect(knop).to_have_attribute("aria-pressed", "true")
        expect(alle).to_have_attribute("aria-pressed", "false")
        expect(alleen).to_have_text(re.compile(r"^Alleen Amsterdam"))
        klaar(kaart)
        expect(kaart.locator("[data-dot]").first).to_be_attached()
        wacht_op_aantal(kaart, 2)

        # --- "Alle plaatsen" in de lijst ---
        tab_to(page, alle, back=True)
        with page.expect_request(lambda r: is_cellen(r.url) and not plaats_van(r.url)):
            page.keyboard.press("Enter")
        expect(alle).to_have_attribute("aria-pressed", "true")
        expect(knop).to_have_attribute("aria-pressed", "false")
        expect(alleen).to_have_count(0)
        expect(alle).to_be_focused()
        klaar(kaart)
        expect(status).to_have_text(re.compile(r"^\d+ plek(ken)? met foto's in beeld$"))
        wacht_op_aantal(kaart, 3)

        # --- weer Amsterdam, dan "Alle plaatsen" boven de kaart: de kaart krijgt de focus ---
        tab_to(page, knop)
        page.keyboard.press("Enter")
        expect(alleen).to_have_text(re.compile(r"^Alleen Amsterdam"))
        klaar(kaart)
        boven = alleen.get_by_role("button", name="Alle plaatsen", exact=True)
        tab_to(page, boven, back=True)
        with page.expect_request(lambda r: is_cellen(r.url) and not plaats_van(r.url)):
            page.keyboard.press("Enter")
        expect(alleen).to_have_count(0)
        expect(kaart).to_be_focused()
        expect(alle).to_have_attribute("aria-pressed", "true")
        klaar(kaart)
        wacht_op_aantal(kaart, 3)
    finally:
        seeded(zonder_plaatsen)
