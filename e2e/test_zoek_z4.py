"""Zoeken in de web (fase 6, Z4): het veld bovenaan, /zoeken en de viewer, in de echte app, met
alleen het toetsenbord.

De lijst van het bouwontwerp (Z4, Tests): Ctrl+K, typen, pijl, Enter, filter, Meer, de viewer met
gemarkeerde woorden. Daarbij wat het nalopen van docs/fase-6/toetsenbord-z4.md vond (stap 1).
Elk antwoord van GET /search en /search/suggest wordt meteen gelezen (expect_response).

Twee apps: de wereld van §4.2 (zoek_wereld, zonder index) en een eigen server met de index aan
(de standaard van de app) met een PDF met tekst en 33 notities, voor de suggesties, de snippet,
"Meer laden" en de tekstlaag van de viewer.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect
from tests.search import fixtures as F

from conftest import Account, Browsers, Server, _browsers_teardown, artifacts_dir, make_admin
from search_harness import ZoekApp

pytestmark = pytest.mark.zoek

API = "/api/v1"
ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"  # zonder de kopregel
PDF = "Kwartaalverslag.pdf"
NOTITIES = 33  # meer dan een pagina van 30: "Meer laden"
WACHT_S = 60.0


def is_zoeken(response) -> bool:
    return "/api/v1/search?" in response.url and response.request.method == "GET"


def is_suggestie(response) -> bool:
    return "/api/v1/search/suggest?" in response.url and response.request.method == "GET"


def test_terug_na_een_datum_zet_hem_er_niet_weer_bij(
    zoek_wereld: ZoekApp, zoek_browsers: Browsers
) -> None:
    """Bram zet met het toetsenbord een datum (Enter in het veld Vanaf) en gaat Terug. Het veld
    toont dan geen datum meer, ook al staat de focus er nog in; wie het daarna verlaat (Tab),
    blijft op dezelfde vraag. Eerder hield het veld de oude datum zolang de focus erin stond, en
    zette blur hem er weer bij: een nieuwe vraag, en Terug werkte niet meer."""
    page = zoek_browsers.context_as(zoek_wereld.accounts["bram"]).new_page()
    with page.expect_response(is_zoeken) as antwoord:
        page.goto("/zoeken?q=utrecht")
    vier = len(antwoord.value.json()["results"])
    assert vier == 4
    expect(page.locator(ROWS)).to_have_count(vier)

    page.locator("summary", has_text="Filters").focus()
    page.keyboard.press("Enter")
    van = page.get_by_label("Vanaf", exact=True)
    van.focus()
    page.keyboard.type("01012024")  # nl-NL: dd-mm-jjjj
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    assert len(antwoord.value.json()["results"]) == vier  # de wereld is van 3 november 2026
    expect(page).to_have_url(re.compile(r"/zoeken\?q=utrecht&from=2024-01-01$"))
    expect(page.get_by_role("button", name="Filter weghalen: Vanaf 2024-01-01")).to_be_visible()
    expect(van).to_be_focused()

    with page.expect_response(is_zoeken):
        page.go_back()
    expect(page).to_have_url(re.compile(r"/zoeken\?q=utrecht$"))
    expect(page.locator(ROWS)).to_have_count(vier)
    expect(van).to_be_focused()
    expect(van).to_have_value("")
    expect(page.get_by_role("button", name=re.compile("^Filter weghalen"))).to_have_count(0)

    # Het veld verlaten: geen nieuwe vraag
    vragen: list[str] = []
    page.on("request", lambda r: vragen.append(r.url) if "/api/v1/search?" in r.url else None)
    # In een datumveld gaat Tab eerst langs dag, maand en jaar; dan naar het volgende veld
    for _ in range(4):
        if not van.evaluate("e => e === document.activeElement"):
            break
        page.keyboard.press("Tab")
    expect(van).not_to_be_focused()
    expect(page.get_by_label("Tot en met", exact=True)).to_be_focused()
    page.wait_for_timeout(500)  # een navigatie na blur komt meteen; een halve seconde is ruim
    expect(page).to_have_url(re.compile(r"/zoeken\?q=utrecht$"))
    assert vragen == []


# --- met de index aan: suggesties, snippet, filter, Meer en de tekstlaag ----------------------


class Z4App:
    def __init__(self, server: Server, admin: Account, pdf: str, notities: list[str]):
        self.server = server
        self.admin = admin
        self.pdf = pdf
        self.notities = notities


def upload(server: Server, account: Account, naam: str, data: bytes) -> str:
    return server.call(
        account,
        "PUT",
        f"{API}/files/{account.root_id}/upload",
        params={"name": naam},
        content=data,
        expect=201,
    ).json()["id"]


def wacht_op_tekst(server: Server, account: Account, ids: list[str]) -> None:
    """Tot de stap text van elk bestand klaar is (zoals in test_zoek_z2.py)."""
    eind = time.monotonic() + WACHT_S
    while True:
        stand = {
            i: server.call(account, "GET", f"{API}/files/{i}/analysis", expect=200).json()["text"]
            for i in ids
        }
        if all(s == "done" for s in stand.values()):
            return
        if time.monotonic() > eind:
            raise AssertionError(f"de tekst kwam niet binnen {WACHT_S:.0f} s: {stand}")
        time.sleep(0.2)


@pytest.fixture(scope="module")
def z4(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Z4App]:
    """Een eigen server met de index aan (de standaard), een beheerder met in zijn hoofdmap een
    PDF van 3 pagina's met tekst (paginawoord<n> op pagina n, tests/search/fixtures.pdf_tekst) en
    33 notities (.txt) met "notitie" in de naam."""
    server = Server(Path(tmp_path_factory.mktemp("zoek-z4")) / "data")
    server.start()
    try:
        admin = make_admin(server)
        pdf = upload(server, admin, PDF, F.pdf_tekst(3))
        notities = [
            upload(server, admin, f"Notitie {n:02d}.txt", f"Notitie nummer {n}.\n".encode())
            for n in range(1, NOTITIES + 1)
        ]
        wacht_op_tekst(server, admin, [pdf, *notities])
        yield Z4App(server, admin, pdf, notities)
    finally:
        server.stop()


@pytest.fixture
def z4_browsers(
    browser: Any,
    z4: Z4App,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Browsers]:
    """Contexts op de server van z4; elke melding laat de test falen."""
    b = Browsers(browser, z4.server)
    yield b
    problems = _browsers_teardown(request, b, artifacts_dir(tmp_path_factory))
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)


def veld(page: Any) -> Any:
    return page.locator("#zoekveld")


def test_ctrl_k_typen_pijl_en_enter_opent_het_bestand(z4: Z4App, z4_browsers: Browsers) -> None:
    """Ctrl+K zet de focus in het veld bovenaan; na "kwartaal" de lijst met het bestand onder
    Namen. ↓ maakt het actief (aria-activedescendant), ↑ gaat terug naar het veld, Enter op het
    bestand opent zijn map met de viewer. De woorden van de pagina liggen eroverheen, zonder
    markering (geen ?q=)."""
    page = z4_browsers.context_as(z4.admin).new_page()
    page.goto("/")
    expect(page.get_by_role("grid")).to_be_visible()
    page.keyboard.press("Control+k")
    expect(veld(page)).to_be_focused()

    with page.expect_response(is_suggestie) as antwoord:
        page.keyboard.type("kwartaal")
    namen = antwoord.value.json()["names"]
    assert [n["name"] for n in namen] == [PDF]
    lijst = page.get_by_role("listbox", name="Suggesties")
    beeld = "Zoek 'kwartaal' op wat er op je foto's staat"
    expect(lijst.get_by_role("option")).to_have_text([PDF, beeld])
    expect(veld(page)).to_have_attribute("aria-expanded", "true")

    page.keyboard.press("ArrowDown")
    eerste = lijst.get_by_role("option").first
    expect(eerste).to_have_attribute("aria-selected", "true")
    expect(veld(page)).to_have_attribute("aria-activedescendant", eerste.get_attribute("id") or "?")
    page.keyboard.press("ArrowUp")
    expect(veld(page)).not_to_have_attribute("aria-activedescendant", re.compile("."))
    expect(veld(page)).to_be_focused()

    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"[?&]bestand={z4.pdf}(&|$)"))
    expect(veld(page)).to_have_value("")
    viewer = page.get_by_role("dialog")
    expect(viewer).to_be_visible()
    # De tekstlaag van pagina 1: de woorden, doorzichtig, zonder <mark>
    laag = viewer.locator(".text-layer").first
    expect(laag.get_by_text("paginawoord1", exact=True)).to_have_count(1)
    expect(viewer.locator(".text-layer mark")).to_have_count(0)
    expect(viewer.locator(".pages-found")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)


def test_van_een_woord_uit_de_tekst_naar_de_viewer_met_de_markering(
    z4: Z4App, z4_browsers: Browsers
) -> None:
    """Ctrl+K, een woord van pagina 3 en Enter: /zoeken met de PDF, de snippet met het woord in
    <mark>. Filters open, Spatie op PDF zet het filter (de chip); Enter op de treffer opent de
    viewer met ?bestand= naast ?q=. De viewer zoekt de eerste pagina met het woord, gaat erheen
    en markeert het; Esc sluit hem en de focus staat weer op de treffer."""
    page = z4_browsers.context_as(z4.admin).new_page()
    woorden: list[str] = []
    page.on("request", lambda r: woorden.append(r.url) if "/words?" in r.url else None)
    page.goto("/")
    expect(page.get_by_role("grid")).to_be_visible()
    page.keyboard.press("Control+k")
    page.keyboard.type("paginawoord3")
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    data = antwoord.value.json()
    assert [(h["node"]["id"], h["match"]) for h in data["results"]] == [(z4.pdf, "text")]
    expect(page).to_have_url(re.compile(r"/zoeken\?q=paginawoord3$"))
    rij = page.locator(ROWS)
    expect(rij).to_have_count(1)
    expect(rij.locator(".snippet mark")).to_have_text(["paginawoord3"])

    # Van de kop met Tab naar Filters: Zoekwoorden, Zoeken, Namen en tekst, Filters
    for naam in ("Zoekwoorden", "Zoeken", "Namen en tekst"):
        page.keyboard.press("Tab")
        expect(page.locator(":focus")).to_have_accessible_name(naam)
    page.keyboard.press("Tab")
    filters = page.locator("summary", has_text="Filters")
    expect(filters).to_be_focused()
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    expect(page.get_by_role("checkbox", name="Mappen", exact=True)).to_be_focused()
    for _ in range(4):  # Afbeeldingen, Video, Audio, PDF
        page.keyboard.press("Tab")
    pdf = page.get_by_role("checkbox", name="PDF", exact=True)
    expect(pdf).to_be_focused()
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Space")
    assert [h["node"]["id"] for h in antwoord.value.json()["results"]] == [z4.pdf]
    expect(page).to_have_url(re.compile(r"/zoeken\?q=paginawoord3&type=pdf$"))
    expect(page.get_by_role("button", name="Filter weghalen: PDF")).to_be_visible()
    expect(pdf).to_be_focused()
    expect(pdf).to_be_checked()

    # De treffers zijn één tabstop; Enter opent de PDF
    page.locator(f"{ROWS}[tabindex='0']").focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"/zoeken\?q=paginawoord3&type=pdf&bestand={z4.pdf}$"))
    viewer = page.get_by_role("dialog")
    expect(viewer.get_by_role("status").filter(has_text="Gevonden op pagina 3")).to_be_visible()
    mark = viewer.locator(".text-layer mark")
    expect(mark).to_have_text(["paginawoord3"])
    expect(mark).to_be_in_viewport()
    # Pagina 1, 2 en 3, elk één keer, met alleen het download-token
    pagina = sorted({int(re.search(r"/pages/(\d+)/words", u).group(1)) for u in woorden})
    assert pagina[:3] == [1, 2, 3], pagina
    assert len(woorden) == len(set(woorden)), woorden
    assert all("paginawoord" not in u and "q=" not in u for u in woorden), woorden

    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/zoeken\?q=paginawoord3&type=pdf$"))
    expect(rij).to_be_focused()


def test_meer_laden_met_het_toetsenbord(z4: Z4App, z4_browsers: Browsers) -> None:
    """33 notities: de eerste 30, End naar de laatste, Tab naar "Meer laden" en Enter: de vraag
    met offset=30, en de focus op treffer 31."""
    page = z4_browsers.context_as(z4.admin).new_page()
    with page.expect_response(is_zoeken) as antwoord:
        page.goto("/zoeken?q=notitie")
    eerste = antwoord.value.json()
    assert len(eerste["results"]) == 30 and eerste["more"] is True, eerste["more"]
    page.locator(f"{ROWS}[tabindex='0']").focus()
    page.keyboard.press("End")
    expect(page.locator(f"{ROWS}[data-index='29']")).to_be_focused()
    page.keyboard.press("Tab")
    meer = page.get_by_role("button", name="Meer laden")
    expect(meer).to_be_focused()
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    assert "offset=30" in antwoord.value.url
    tweede = antwoord.value.json()
    ids = [h["node"]["id"] for h in eerste["results"] + tweede["results"]]
    assert sorted(ids) == sorted(z4.notities) and tweede["more"] is False
    expect(page.locator(f"{ROWS}[data-index='30']")).to_be_focused()
    expect(meer).to_have_count(0)
