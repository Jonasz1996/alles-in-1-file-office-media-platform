"""Zoeken in Z0 (fase 6, §3.6): het harnas in de echte app. Dit is de snelle e2e van
e2e/slim.sh: de wereld van §4.2, zoeken op naam zoals in 0.7.0 (LikeFallback, tot Z1 de enige
zoekmachine), één upload met tekst, en de toetsen van het zoeken die er nu zijn (Ctrl+K en het
veld bovenaan, / in Bestanden, Enter, het veld Zoekwoorden, Enter op een resultaat). En de boom
van 1.000 van app/search/bench.py, met een gebruiker per rol van §4.4.

Z0 bouwt geen functie: deze tests leggen vast dat het harnas de app goed zaait, en dat zoeken
precies geeft wat de referentie (api/tests/search/referentie.py, Referentie.op_naam) zegt. Z1
en verder zetten er hun eigen e2e/test_zoek_*.py naast; met marker zoek draaien die vanzelf
mee in slim.sh.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from tests.search import wereld as W

from conftest import Account, Browsers, Server
from search_harness import ZoekApp, start_boom

pytestmark = pytest.mark.zoek

ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"  # zonder de kopregel
API = "/api/v1"


def zoek(server: Server, account: Account, q: str, **params: Any) -> dict[str, Any]:
    params = {"q": q, **params}
    return server.call(account, "GET", f"{API}/search", params=params, expect=200).json()


def ids(antwoord: dict[str, Any]) -> set[uuid.UUID]:
    return {uuid.UUID(h["node"]["id"]) for h in antwoord["results"]}


def is_zoeken(response: Any) -> bool:
    return response.request.method == "GET" and f"{API}/search?" in response.url


def row(page: Page, name: str) -> Any:
    return page.locator(ROWS).filter(has=page.get_by_text(name, exact=True))


def blur(page: Page) -> None:
    """De focus uit een knop of veld, zodat een sneltoets van één teken werkt."""
    page.evaluate("() => document.activeElement && document.activeElement.blur()")


def expect_focus(page: Page, name: str) -> None:
    page.wait_for_function(
        """(name) => {
          const row = document.activeElement && document.activeElement.closest("[role=row]");
          const el = row && row.querySelector(".name");
          return !!el && el.textContent === name;
        }""",
        arg=name,
        timeout=5000,
    )


def namen(app: ZoekApp, gevonden: set[uuid.UUID]) -> set[str]:
    with app.db() as con:
        return {
            con.execute("SELECT name FROM node WHERE id = ?", (i.hex,)).fetchone()[0]
            for i in gevonden
        }


# --- de wereld ------------------------------------------------------------------------------


def test_iedereen_uit_de_wereld_kan_inloggen(zoek_wereld: ZoekApp) -> None:
    for naam, account in zoek_wereld.accounts.items():
        me = zoek_wereld.server.call(account, "GET", f"{API}/auth/me", expect=200).json()
        assert (me["id"], me["role"]) == (account.id, W.GEBRUIKERS[naam][0]), naam
        assert not me["totp_setup_required"], naam


def test_zoeken_op_naam_volgt_de_referentie(zoek_wereld: ZoekApp) -> None:
    """Elke persoon, elke vraag van wereld.NAAMVRAGEN: de app (een eigen proces, over HTTP)
    geeft precies wat Referentie.op_naam zegt; ook binnen een gedeelde map."""
    app = zoek_wereld
    ref = app.referentie()
    assert app.wereld is not None
    for naam, account in app.accounts.items():
        for q in W.NAAMVRAGEN:
            antwoord = zoek(app.server, account, q, limit=200)
            assert not antwoord["more"], (naam, q)
            assert ids(antwoord) == ref.op_naam(app.wereld.users[naam], q), (naam, q)
    bram, werk = app.accounts["bram"], app.wereld.ids["werk"]
    for q in ("utrecht", "brief", "a"):
        antwoord = zoek(app.server, bram, q, limit=200, **{"in": str(werk)})
        assert ids(antwoord) == ref.op_naam(app.wereld.users["bram"], q, in_=werk), q
    # Iets wat niemand anders mag zien, ziet ook niemand anders
    for naam in ("bram", "cas", "gijs", "dirk", "fien", "hans"):
        assert not ids(zoek(app.server, app.accounts[naam], "privé")), naam


def test_zoeken_met_het_toetsenbord(zoek_wereld: ZoekApp, zoek_browsers: Browsers) -> None:
    """Bram: / in Bestanden, typen, Enter; daarna het veld Zoekwoorden op /zoeken, en Enter op
    een gedeelde map in de resultaten. Elk antwoord van de API wordt meteen gelezen en naast
    de referentie gelegd; de rijen op het scherm zijn die van het antwoord."""
    app = zoek_wereld
    assert app.wereld is not None
    ref = app.referentie()
    bram = app.accounts["bram"]
    page = zoek_browsers.context_as(bram).new_page()
    page.goto("/")
    expect(row(page, "Kopie Utrecht.jpg")).to_be_visible()

    blur(page)
    page.keyboard.press("/")
    expect(page.locator("#zoekveld")).to_be_focused()
    page.keyboard.type("utrecht")
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    data = antwoord.value.json()
    expect(page).to_have_url(re.compile(r"/zoeken\?q=utrecht$"))
    verwacht = ref.op_naam(app.wereld.users["bram"], "utrecht")
    assert ids(data) == verwacht and len(verwacht) == 4
    expect(page.locator(ROWS)).to_have_count(len(verwacht))
    for naam in namen(app, verwacht):
        expect(row(page, naam).first).to_be_visible()

    # Het veld op /zoeken: één tabstop erheen (focus), daarna alleen toetsen
    box = page.get_by_role("searchbox", name="Zoekwoorden")
    expect(box).to_have_value("utrecht")
    box.focus()
    page.keyboard.press("End")
    page.keyboard.press("Shift+Home")
    page.keyboard.type("werk")
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    data = antwoord.value.json()
    assert ids(data) == ref.op_naam(app.wereld.users["bram"], "werk") == {app.wereld.ids["werk"]}
    expect(page.locator(ROWS)).to_have_count(1)
    # De lijst in (één tabstop) en Enter: de gedeelde map gaat open
    page.locator(f'{ROWS}[tabindex="0"]').focus()
    expect_focus(page, "Werk")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(f"/map/{app.wereld.ids['werk']}$"))
    expect(row(page, "Verslag kwartaal.docx")).to_be_visible()


def test_ctrl_k_en_het_zoekveld_bovenaan(zoek_wereld: ZoekApp, zoek_browsers: Browsers) -> None:
    """Bram op Foto's: Ctrl+K zet de focus op het veld bovenaan (Z0-web), ook vanuit een ander
    veld; Escape maakt het leeg en geeft daarna de focus terug; Enter gaat naar /zoeken en
    geeft precies wat de referentie zegt."""
    app = zoek_wereld
    assert app.wereld is not None
    ref = app.referentie()
    page = zoek_browsers.context_as(app.accounts["bram"]).new_page()
    page.goto("/fotos")
    veld = page.get_by_role("search", name="Zoeken in alles").get_by_role("searchbox")
    expect(veld).to_have_attribute("aria-keyshortcuts", "Control+K")

    # Escape: eerst leeg, dan de focus terug naar waar hij stond
    menu = page.get_by_role("link", name="Bestanden", exact=True)
    menu.focus()
    page.keyboard.press("Control+k")
    expect(veld).to_be_focused()
    page.keyboard.type("iets")
    page.keyboard.press("Escape")
    expect(veld).to_have_value("")
    expect(veld).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_be_focused()

    page.keyboard.press("Control+k")
    expect(veld).to_be_focused()
    page.keyboard.type("utrecht")
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    data = antwoord.value.json()
    expect(page).to_have_url(re.compile(r"/zoeken\?q=utrecht$"))
    verwacht = ref.op_naam(app.wereld.users["bram"], "utrecht")
    assert ids(data) == verwacht and len(verwacht) == 4
    expect(page.locator(ROWS)).to_have_count(len(verwacht))
    expect(page.get_by_role("searchbox", name="Zoekwoorden")).to_have_value("utrecht")
    expect(veld).to_have_value("")  # het veld bovenaan is na Enter weer leeg

    # Ook vanuit het veld Zoekwoorden op /zoeken (Ctrl+K typt geen teken)
    page.get_by_role("searchbox", name="Zoekwoorden").focus()
    page.keyboard.press("Control+k")
    expect(veld).to_be_focused()
    expect(page.get_by_role("searchbox", name="Zoekwoorden")).to_have_value("utrecht")


def test_wie_niets_heeft_vindt_niets(zoek_wereld: ZoekApp, zoek_browsers: Browsers) -> None:
    """Dirk: niets gedeeld; de foto's van Eva vindt hij niet, en het scherm zegt dat."""
    dirk = zoek_wereld.accounts["dirk"]
    page = zoek_browsers.context_as(dirk).new_page()
    with page.expect_response(is_zoeken) as antwoord:
        page.goto("/zoeken?q=utrecht")
    data = antwoord.value.json()
    assert data["results"] == [] and not data["more"]
    expect(page.get_by_text("Niets gevonden.", exact=True)).to_be_visible()
    expect(page.locator(ROWS)).to_have_count(0)


def test_een_upload_met_tekst_is_meteen_op_naam_te_vinden(
    zoek_wereld: ZoekApp, zoek_browsers: Browsers
) -> None:
    """Dirk zet een tekstbestand in zijn map. De triggers van 0010 zetten de naam in dezelfde
    transactie in fts_node en fts_name; zoeken op naam vindt hem meteen, voor Dirk alleen. (De
    tekst zelf komt met de job text van Z2; daar gaat deze test niet over.)"""
    app = zoek_wereld
    dirk, eva = app.accounts["dirk"], app.accounts["eva"]
    naam = "Z0 notitie uploadwoordk.txt"
    tekst = b"Een notitie met inhoudwoordm erin.\n"
    node = app.server.call(
        dirk,
        "PUT",
        f"{API}/files/{dirk.root_id}/upload",
        params={"name": naam},
        content=tekst,
        expect=201,
    ).json()
    try:
        node_id = uuid.UUID(node["id"])
        assert ids(zoek(app.server, dirk, "uploadwoordk")) == {node_id}
        assert ids(zoek(app.server, eva, "uploadwoordk")) == set()
        with app.db() as con:
            num, dead, zoeknaam = con.execute(
                "SELECT num, dead, search_name FROM node WHERE id = ?", (node_id.hex,)
            ).fetchone()
            assert dead == 0 and "uploadwoordk" in zoeknaam
            fts = {r[0] for r in con.execute(
                "SELECT rowid FROM fts_node WHERE fts_node MATCH ?", ('name : "uploadwoordk"',)
            )}  # fmt: skip
            trigram = {r[0] for r in con.execute(
                "SELECT rowid FROM fts_name WHERE fts_name MATCH ?", ('"oadwoor"',)
            )}  # fmt: skip
            assert fts == {num} and num in trigram

        page = zoek_browsers.context_as(dirk).new_page()
        page.goto("/")
        expect(row(page, naam)).to_be_visible()
        blur(page)
        page.keyboard.press("/")
        page.keyboard.type("uploadwoordk")
        with page.expect_response(is_zoeken) as antwoord:
            page.keyboard.press("Enter")
        assert ids(antwoord.value.json()) == {node_id}
        expect(page.locator(ROWS)).to_have_count(1)
        expect(row(page, naam)).to_be_visible()
    finally:
        # Naar de prullenbak: de wereld is weer die van §4.2 voor de volgende test
        app.server.call(dirk, "DELETE", f"{API}/files/{node['id']}", expect=204)
    assert ids(zoek(app.server, dirk, "uploadwoordk")) == set()


# --- de boom van 1.000 ----------------------------------------------------------------------

BOOM_VRAGEN = ("pdf", "docx", "img_2024", "klein", "utrecht", "kopie", "a", "2019 jpg")


@pytest.fixture(scope="module")
def boom(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ZoekApp]:
    app = start_boom(tmp_path_factory.mktemp("zoek-boom"), 1_000)
    try:
        yield app
    finally:
        app.stop()


def test_de_boom_van_1000_per_rol(boom: ZoekApp) -> None:
    """Elke rol van §4.4 logt in en zoekt op naam; tot 200 treffers precies de referentie,
    daarboven 200 ervan en "meer"."""
    assert boom.uitkomst is not None
    assert boom.uitkomst.tellingen["bestanden"] == 1_000
    ref = boom.referentie()
    gebruikers = {rol: uuid.UUID(a.id) for rol, a in boom.accounts.items()}
    treffers = 0
    for rol, account in boom.accounts.items():
        me = boom.server.call(account, "GET", f"{API}/auth/me", expect=200).json()
        assert me["role"] == ("guest" if rol == "gast" else "user"), rol
        for q in BOOM_VRAGEN:
            antwoord = zoek(boom.server, account, q, limit=200)
            verwacht = ref.op_naam(gebruikers[rol], q)
            if len(verwacht) <= 200:
                assert ids(antwoord) == verwacht and not antwoord["more"], (rol, q)
            else:
                assert ids(antwoord) < verwacht and antwoord["more"], (rol, q)
                assert len(antwoord["results"]) == 200, (rol, q)
            treffers += len(verwacht)
    assert treffers > 1_000  # de vragen vinden echt iets


def test_de_boom_van_1000_in_de_browser(boom: ZoekApp, browser: Any) -> None:
    """De eigenaar opent zijn map en zoekt met het toetsenbord: de app draait op de database
    van bench.py (zonder bestanden op schijf) zonder één melding in de browser."""
    b = Browsers(browser, boom.server)
    try:
        page = b.context_as(boom.accounts["eigenaar"]).new_page()
        page.goto("/")
        page.locator(ROWS).first.wait_for()
        blur(page)
        page.keyboard.press("/")
        page.keyboard.type("pdf")
        with page.expect_response(is_zoeken) as antwoord:
            page.keyboard.press("Enter")
        data = antwoord.value.json()
        verwacht = boom.referentie().op_naam(uuid.UUID(boom.accounts["eigenaar"].id), "pdf")
        assert ids(data) <= verwacht and len(data["results"]) == min(50, len(verwacht)) > 0
        expect(page.locator(ROWS).first).to_be_visible()
    finally:
        problems = b.close_all()
    assert not problems, "Problemen in de browser:\n" + "\n".join(problems)
