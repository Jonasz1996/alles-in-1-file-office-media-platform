"""De tab "Zoeken en foto's" op /beheer (fase 6, Z7; Keuze 54), alleen met het toetsenbord
(docs/fase-6/toetsenbord-z7.md). Een eigen app met FTS aan en de boom van app/search/bench.py
(3.000 bestanden: de herbouw loopt dan in twee stukken van rebuild.CHUNK).

1. De beheerder: Alt+9 opent de tab; de tellers kloppen met GET /admin/search. Na een upload
   staan er vanzelf meer (de tab peilt elke 5 s). Pauzeren met Enter; daarna de index herbouwen:
   Esc in de bevestiging doet niets, Herbouwen start hem (202). Met de pauze blijft hij na het
   eerste stuk staan, met zijn voortgang in procenten; zoeken gaat zolang met LIKE. De lijst van
   mislukte toont alleen de sha256, nooit de naam; "Mislukte opnieuw" zet ze terug in de rij.
   Hervatten: de herbouw en de mislukte lopen af, alles is weer klaar. De twee knoppen van de
   foto's vragen eerst; Esc doet niets (geen POST) en de focus blijft op de knop.
2. Een gewone gebruiker: geen Beheer in het menu, /beheer/zoeken is "Geen toegang" zonder één
   aanroep van /admin/search, en elke route van Z7 geeft hem 403.

De mislukte inhoud is een docx die wel te lezen is: de test zet zijn stap text met de server uit
op failed. Na "Mislukte opnieuw" en hervatten leest de app hem echt opnieuw (done).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from tests.search import fixtures as F

from app.search import bench
from app.security import hash_password
from conftest import Account, Browsers, Server, _browsers_teardown, artifacts_dir, make_admin
from seed_browser import upload

pytestmark = pytest.mark.zoek

API = "/api/v1"
ADMIN = f"{API}/admin/search"
WACHT_S = 60.0
N = 3_000  # meer nodes dan rebuild.CHUNK (2.000): de herbouw heeft twee stukken
WOORD = "notulenwoordbeheer"  # alleen in de tekst van de docx die op failed komt
ACTIEF = """() => {
  const a = document.activeElement;
  if (!a) return "-";
  const naam = (a.getAttribute("aria-label") || a.textContent || "").trim();
  return `${a.tagName} ${naam.slice(0, 40)}`;
}"""


@dataclass
class Beheer:
    server: Server
    admin: Account
    eigenaar: Account
    mislukt_sha: str  # de inhoud waarvan de stap text op failed staat
    mislukt_naam: str


def zaai(data_dir: Path) -> bench.Uitkomst:
    """De boom van bench.py, met de stap text klaar (zoals search_harness.start_boom)."""
    con = sqlite3.connect(data_dir / "platform.db")
    try:
        plan = bench.Plan(n=N, seed=6)
        wachtwoord = hash_password(bench.WACHTWOORD)
        uit = bench.vul(con, plan, wachtwoord_hash=wachtwoord, blob_standaard=bench.app_blob())
        bench.analyse_klaar(con)
    finally:
        con.close()
    return uit


def analyse(server: Server, account: Account, node_id: str) -> dict[str, Any]:
    return server.call(account, "GET", f"{API}/files/{node_id}/analysis", expect=200).json()


def wacht_op_tekst(server: Server, account: Account, node_id: str) -> None:
    eind = time.monotonic() + WACHT_S
    stand: dict[str, Any] = {}
    while time.monotonic() < eind:
        stand = analyse(server, account, node_id)
        if stand["text"] == "done":
            return
        time.sleep(0.2)
    raise AssertionError(f"de tekst kwam niet binnen {WACHT_S:.0f} s: {stand}")


def stand(beheer: Beheer) -> dict[str, Any]:
    return beheer.server.call(beheer.admin, "GET", ADMIN, expect=200).json()


def wacht_op_stand(beheer: Beheer, klaar: Any, wat: str) -> dict[str, Any]:
    eind = time.monotonic() + WACHT_S
    gezien: dict[str, Any] = {}
    while time.monotonic() < eind:
        gezien = stand(beheer)
        if klaar(gezien):
            return gezien
        time.sleep(0.2)
    raise AssertionError(f"{wat} niet binnen {WACHT_S:.0f} s: {gezien}")


@pytest.fixture(scope="module")
def beheer(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Beheer]:
    server = Server(
        Path(tmp_path_factory.mktemp("zoek-beheer")) / "data",
        env={"PLATFORM_SEARCH_INDEX": "aan"},
    )
    server.start()
    try:
        admin = make_admin(server)  # vóór het zaaien: /auth/setup wil een lege database
        uit = server.seeded(zaai)
        g = uit.gebruikers[uit.rollen["eigenaar"]]
        eigenaar = Account(
            str(uuid.UUID(g["id"])),
            g["email"],
            bench.WACHTWOORD,
            "Eigenaar",
            g["rol"],
            str(uuid.UUID(g["root"])),
        )
        naam = "Notulen van de raad.docx"
        data = F.docx(f"De notulen met {WOORD} erin.")
        node = upload(server, eigenaar, eigenaar.root_id, naam, data)
        wacht_op_tekst(server, eigenaar, node)
        sha = hashlib.sha256(data).hexdigest()

        def mislukt(data_dir: Path) -> None:
            con = sqlite3.connect(data_dir / "platform.db")
            try:
                cur = con.execute(
                    "UPDATE analysis SET state = 'failed', code = 'unreadable', tries = 3 "
                    "WHERE blob_sha = ? AND step = 'text' AND state = 'done'",
                    (sha,),
                )
                assert cur.rowcount == 1
                con.commit()
            finally:
                con.close()

        server.seeded(mislukt)
        yield Beheer(server, admin, eigenaar, sha, naam)
    finally:
        server.stop()


@pytest.fixture
def browsers(
    browser: Any,
    beheer: Beheer,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Browsers]:
    """Contexts op de eigen server; elke melding van de bewaking laat de test falen."""
    b = Browsers(browser, beheer.server)
    yield b
    problems = _browsers_teardown(request, b, artifacts_dir(tmp_path_factory))
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)


def tab_to(page: Page, target: Locator, limit: int = 60) -> int:
    """Tab tot target de focus heeft; geeft het aantal keer."""
    target.wait_for(state="attached")
    gezien = []
    for i in range(limit):
        page.keyboard.press("Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return i + 1
        gezien.append(page.evaluate(ACTIEF))
    raise AssertionError(f"Tab kwam niet bij {target}: {gezien}")


def toast(page: Page, tekst: str) -> Locator:
    """Een melding (niet de kopie voor de schermlezer)."""
    return page.get_by_role("region", name="Meldingen").get_by_text(tekst, exact=True)


def nl(n: int) -> str:
    """Een getal zoals Intl.NumberFormat("nl-NL") het schrijft."""
    return f"{n:,}".replace(",", ".")


def bestanden(done: int, total: int) -> str:
    return f"{nl(done)} van {nl(total)} {'bestand' if total == 1 else 'bestanden'}"


def is_admin_search(method: str, suffix: str = ""):
    def match(r: Any) -> bool:
        path = r.url.split("?", 1)[0]
        return r.request.method == method and path.endswith(f"{ADMIN}{suffix}")

    return match


# --- 1. de beheerder, alleen met het toetsenbord -----------------------------------------------


def test_de_tab_met_alleen_het_toetsenbord(beheer: Beheer, browsers: Browsers) -> None:
    server = beheer.server
    page = browsers.context_as(beheer.admin).new_page()
    page.goto("/beheer")
    tabs = page.get_by_role("navigation", name="Onderdelen van beheer")
    link = tabs.get_by_role("link", name="Zoeken en foto's", exact=True)
    expect(link).to_be_visible()
    with page.expect_response(is_admin_search("GET")) as eerste:
        page.keyboard.press("Alt+9")
    assert eerste.value.status == 200
    begin = eerste.value.json()
    expect(page).to_have_url(re.compile(r"/beheer/zoeken$"))
    expect(link).to_have_attribute("aria-current", "page")
    expect(page.get_by_role("heading", name="Zoeken en foto's", level=2)).to_be_visible()

    # --- de tellers zijn die van GET /admin/search ---
    total = begin["index"]["text_total"]
    assert total > 0
    assert begin["engine"] == "fts" and begin["paused"] is False and begin["rebuild"] is None
    # De mislukte telt als gedaan: alleen wat nog wacht of loopt, telt niet
    assert begin["index"] == {
        "names_done": True, "text_done": total, "text_total": total, "failed": 1,
    }  # fmt: skip
    engine = page.locator('[data-search="engine"]')
    names = page.locator('[data-search="names"]')
    tekst = page.locator('[data-search="text"]')
    failed = page.locator('[data-search="failed"]')
    gepauzeerd = page.locator('[data-search="paused"]')
    expect(engine).to_have_text("FTS5 (snel)")
    expect(names).to_have_text("klaar")
    expect(tekst).to_have_text(bestanden(total, total))
    expect(failed).to_have_text("1 inhoud")
    expect(gepauzeerd).to_have_text("loopt")
    expect(page.locator('[data-search="rebuild"]')).to_have_count(0)

    # --- na een upload staan er vanzelf meer (de tab peilt) ---
    node = upload(
        server, beheer.eigenaar, beheer.eigenaar.root_id, "Agenda.docx", F.docx("De agenda.")
    )
    wacht_op_tekst(server, beheer.eigenaar, node)
    expect(tekst).to_have_text(bestanden(total + 1, total + 1), timeout=20_000)
    total += 1

    # --- pauzeren met Enter ---
    pauze = page.locator('[data-search="pause-button"]')
    expect(pauze).to_have_text("Achtergrondwerk pauzeren")
    expect(pauze).to_have_attribute("aria-pressed", "false")
    tab_to(page, pauze)
    with page.expect_response(is_admin_search("PATCH")) as patch:
        page.keyboard.press("Enter")
    assert patch.value.status == 200
    assert patch.value.json()["paused"] is True
    expect(gepauzeerd).to_have_text("gepauzeerd")
    expect(pauze).to_have_text("Achtergrondwerk hervatten")
    expect(pauze).to_have_attribute("aria-pressed", "true")
    expect(pauze).to_be_focused()
    expect(toast(page, "Het achtergrondwerk staat op pauze.")).to_be_visible()

    # --- de index herbouwen: Esc doet niets, Herbouwen start hem ---
    posts: list[str] = []
    page.on(
        "request", lambda r: posts.append(r.url) if r.method == "POST" and ADMIN in r.url else None
    )
    herbouw = page.get_by_role("button", name="Index herbouwen", exact=True)
    tab_to(page, herbouw)
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="De index herbouwen?")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(herbouw).to_be_focused()
    assert posts == []

    page.keyboard.press("Enter")
    expect(dialog).to_be_visible()
    page.keyboard.press("Tab")
    expect(dialog.get_by_role("button", name="Herbouwen", exact=True)).to_be_focused()
    with page.expect_response(is_admin_search("POST", "/rebuild")) as gestart:
        page.keyboard.press("Enter")
    assert gestart.value.status == 202, gestart.value.text()
    expect(toast(page, "Het herbouwen is gestart.")).to_be_visible()
    # De knop is weg zolang de herbouw loopt; de focus staat op de kop van het blok
    expect(herbouw).to_have_count(0)
    expect(page.get_by_role("heading", name="Index", level=3, exact=True)).to_be_focused()

    # Met de pauze blijft hij na het eerste stuk staan, met zijn voortgang
    nu = wacht_op_stand(
        beheer,
        lambda s: s["rebuild"] is not None and 0 < s["rebuild"]["done"] < 1,
        "het eerste stuk van de herbouw",
    )
    assert nu["rebuild"]["what"] == "text" and nu["engine"] == "like"
    assert nu["index"]["names_done"] is False
    time.sleep(3)  # de wacht tussen de stukken kijkt elke seconde
    later = stand(beheer)
    assert later["rebuild"] == nu["rebuild"], (nu, later)
    procent = round(nu["rebuild"]["done"] * 100)
    status = page.locator('[data-search="rebuild"]')
    expect(status).to_have_text(re.compile(rf"^De index: {procent}\s?%, sinds \S"))
    expect(status).to_have_attribute("role", "status")
    expect(page.get_by_role("progressbar", name="Voortgang van het herbouwen")).to_be_visible()
    expect(engine).to_have_text("LIKE (langzamer)")
    expect(names).to_have_text("worden opnieuw opgebouwd")

    # --- de mislukte: alleen de sha256, nooit de naam ---
    lijst_knop = page.get_by_role("button", name="Lijst van mislukte tonen", exact=True)
    tab_to(page, lijst_knop)
    with page.expect_response(is_admin_search("GET", "/failed")) as lijst:
        page.keyboard.press("Enter")
    assert lijst.value.status == 200
    items = lijst.value.json()["items"]
    assert [(i["sha"], i["step"], i["code"]) for i in items] == [
        (beheer.mislukt_sha, "text", "unreadable")
    ]
    tabel = page.locator('[data-search="failed-list"]')
    rij = tabel.locator("tbody tr")
    expect(rij).to_have_count(1)
    expect(rij.locator("td").nth(0)).to_have_text(beheer.mislukt_sha)
    expect(rij.locator("td").nth(1)).to_have_text("Tekst")
    expect(rij.locator("td").nth(2)).to_have_text("unreadable")
    assert beheer.mislukt_naam not in page.locator("main").inner_text()
    toggle = page.locator('[data-search="failed-toggle"]')
    expect(toggle).to_have_text("Lijst verbergen")
    expect(toggle).to_have_attribute("aria-expanded", "true")

    # "Mislukte opnieuw": terug in de rij (met de pauze wacht hij); de lijst gaat dicht
    opnieuw = page.get_by_role("button", name="Mislukte opnieuw", exact=True)
    tab_to(page, opnieuw)
    with page.expect_response(is_admin_search("POST", "/retry")) as retry:
        page.keyboard.press("Enter")
    assert retry.value.status == 202, retry.value.text()
    expect(toast(page, "De mislukte staan weer in de rij.")).to_be_visible()
    expect(failed).to_have_text("0 inhouden")
    expect(tekst).to_have_text(bestanden(total - 1, total))
    expect(page.get_by_text("Niets mislukt.", exact=True)).to_be_visible()
    expect(opnieuw).to_have_count(0)
    expect(tabel).to_have_count(0)
    expect(
        page.get_by_role("heading", name="Mislukte inhouden", level=3, exact=True)
    ).to_be_focused()
    assert analyse(server, beheer.eigenaar, node)["text"] == "done"

    # --- hervatten: de herbouw en de mislukte lopen af ---
    tab_to(page, pauze)
    expect(pauze).to_have_text("Achtergrondwerk hervatten")
    with page.expect_response(is_admin_search("PATCH")) as hervat:
        page.keyboard.press("Enter")
    assert hervat.value.status == 200
    assert hervat.value.json()["paused"] is False
    expect(gepauzeerd).to_have_text("loopt")
    expect(pauze).to_be_focused()
    expect(toast(page, "Het achtergrondwerk loopt weer.")).to_be_visible()
    eind = wacht_op_stand(
        beheer,
        lambda s: s["rebuild"] is None and s["index"]["text_done"] == s["index"]["text_total"],
        "het einde van de herbouw en de mislukte",
    )
    assert eind["engine"] == "fts" and eind["paused"] is False
    assert eind["index"] == {
        "names_done": True, "text_done": total, "text_total": total, "failed": 0,
    }  # fmt: skip
    expect(page.locator('[data-search="rebuild"]')).to_have_count(0, timeout=20_000)
    expect(engine).to_have_text("FTS5 (snel)")
    expect(names).to_have_text("klaar")
    expect(tekst).to_have_text(bestanden(total, total))
    expect(page.get_by_role("button", name="Index herbouwen", exact=True)).to_be_visible()
    # Zoeken vindt weer met FTS, ook de tekst van de opnieuw gelezen docx
    found = server.call(
        beheer.eigenaar, "GET", f"{API}/search", params={"q": WOORD}, expect=200
    ).json()
    assert found["engine"] == "fts"
    assert [h["node"]["name"] for h in found["results"]] == [beheer.mislukt_naam]

    # --- de foto's: elke knop vraagt eerst; Esc doet niets en de focus blijft op de knop ---
    for knop_naam in ("Duplicaten opnieuw berekenen", "Plek, camera en datum opnieuw lezen"):
        knop = page.get_by_role("button", name=knop_naam, exact=True)
        tab_to(page, knop)
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog", name=f"{knop_naam}?")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
        expect(dialog.get_by_role("button", name="Starten", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        expect(knop).to_be_focused()
    # Alleen de herbouw van de index en "Mislukte opnieuw" gingen naar de server
    assert [p.split("?", 1)[0].rsplit(ADMIN, 1)[1] for p in posts] == ["/rebuild", "/retry"]


# --- 2. een gewone gebruiker: geen tab en 403 --------------------------------------------------


def test_een_gewone_gebruiker_ziet_geen_tab_en_krijgt_403(
    beheer: Beheer, browsers: Browsers
) -> None:
    server, eigenaar = beheer.server, beheer.eigenaar
    page = browsers.context_as(eigenaar).new_page()
    gevraagd: list[str] = []
    page.on("request", lambda r: gevraagd.append(r.url) if ADMIN in r.url else None)
    page.goto("/")
    nav = page.get_by_role("navigation", name="Hoofdmenu")
    expect(nav.get_by_role("link", name="Bestanden", exact=True)).to_be_visible()
    expect(nav.get_by_role("link", name="Beheer", exact=True)).to_have_count(0)

    page.goto("/beheer/zoeken")
    expect(page.get_by_role("heading", name="Geen toegang", level=1)).to_be_visible()
    expect(page.get_by_text("Je hebt geen toegang tot deze pagina.", exact=True)).to_be_visible()
    expect(page.get_by_role("navigation", name="Onderdelen van beheer")).to_have_count(0)
    expect(page.get_by_role("link", name="Zoeken en foto's", exact=True)).to_have_count(0)
    page.keyboard.press("Alt+9")
    expect(page.get_by_role("heading", name="Geen toegang", level=1)).to_be_visible()
    assert gevraagd == []

    for method, path, body in (
        ("GET", ADMIN, None),
        ("PATCH", ADMIN, {"paused": True}),
        ("POST", f"{ADMIN}/rebuild", {"what": "text"}),
        ("GET", f"{ADMIN}/failed", None),
        ("POST", f"{ADMIN}/retry", None),
    ):
        r = server.call(eigenaar, method, path, json=body)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    # Niets veranderd: geen pauze, geen herbouw
    na = stand(beheer)
    assert na["paused"] is False and na["rebuild"] is None
