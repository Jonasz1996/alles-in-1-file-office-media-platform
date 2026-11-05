"""Zoeken in de web (fase 6, Z4): de pagina /zoeken in de echte app, met alleen het toetsenbord.

Stap 1 van Z4 zet hier wat het nalopen van docs/fase-6/toetsenbord-z4.md vond; stap 2 zet er
de rest van de lijst van het bouwontwerp bij (Ctrl+K, typen, pijl, Enter, filter, Meer, de viewer
met gemarkeerde woorden). Elk antwoord van GET /search wordt meteen gelezen (expect_response).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from conftest import Browsers
from search_harness import ZoekApp

pytestmark = pytest.mark.zoek

ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"  # zonder de kopregel


def is_zoeken(response) -> bool:
    return "/api/v1/search?" in response.url and response.request.method == "GET"


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
