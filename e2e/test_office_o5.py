"""O5 (beheer en bewaking) met de nep-DS, via de echte web-app (marker office; §4.4 punt 7 van
docs/fase-4/bouwontwerp.md).

- Het tabblad Office met Alt+7. Terwijl Anna en Bram één docx bewerken (zoals punt 1), toont
  het 2 verbindingen, 2 mensen en 1 document; daarna weer 0, en de piek blijft.
- De grens op 1 (met het formulier op het tabblad): de tweede bewerker krijgt de melding.
- Twee niet-opgeslagen documenten (quotum vol): het ene verwijderen (met de bevestiging), het
  andere terugzetten na het vrijmaken van ruimte. Alles met het toetsenbord.
- Office uit en weer aan met alleen het toetsenbord, en het tabblad in het Engels.

Elke sessie die een test opent, gaat ook weer dicht (Sluiten in de editor): een sessie die
open blijft, houdt de conversies van de andere tests tegen (sessions.busy is globaal).
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from conftest import create_user
from office_harness import Office, docx_text, fake_info, fixture, type_in_editor
from test_office_o2 import (
    callback,
    children,
    duo,
    forgotten,
    is_session,
    open_page,
    start,
    tab_to,
    wait_until,
)

pytestmark = pytest.mark.office

ADMIN = "/api/v1/admin/office"
DEFAULTS = {"enabled": True, "max_editors": 20, "checkpoint_minutes": 5, "version_minutes": 30}


def office_tab(office: Office, browsers: Any) -> Page:
    """/beheer als de beheerder, dan Alt+7: het tabblad Office."""
    page = browsers.context_as(office.admin, service_workers="block").new_page()
    page.goto("/beheer")
    tabs = page.get_by_role("navigation", name="Onderdelen van beheer")
    link = tabs.get_by_role("link", name="Office", exact=True)
    expect(link).to_be_visible()
    page.keyboard.press("Alt+7")
    expect(page).to_have_url(re.compile(r"/beheer/office$"))
    expect(link).to_have_attribute("aria-current", "page")
    expect(page.get_by_role("heading", name="Office: Gezond", exact=True)).to_be_visible()
    return page


def count(page: Page, what: str) -> Any:
    """Een getal van de kaarten onder Nu (Verbindingen, Mensen, Documenten)."""
    return page.locator(f'[data-office="{what}"]')


def toast(page: Page, text: str | re.Pattern[str]) -> Any:
    return page.get_by_role("region", name="Meldingen").get_by_text(text)


def save_with_enter(page: Page, field: Any) -> None:
    """Enter in een veld van het formulier, en wachten op het antwoord van de PUT zelf. De toast
    van een vorige keer opslaan kan nog staan (5 s): die zegt niet dat deze keer klaar is."""
    with page.expect_response(
        lambda r: r.request.method == "PUT" and r.url.split("?", 1)[0].endswith(ADMIN)
    ) as put:
        field.press("Enter")
    assert put.value.status == 200, put.value.text()


def close_editor(page: Page) -> None:
    page.get_by_role("button", name="Sluiten", exact=True).click()
    expect(page).not_to_have_url(re.compile(r"/bewerken/"))


# --- §4.4 punt 7: het tabblad, en 2 verbindingen tijdens punt 1 ---


def test_tabblad_office_toont_twee_verbindingen(office: Office, office_browsers) -> None:
    tag = uuid.uuid4().hex[:6]
    name = f"samen-{tag}.docx"
    d = duo(office, name)
    tab = office_tab(office, office_browsers)
    # De document server zoals health.py hem zag: de vastgepinde versie, geen limiet, tokens
    facts = tab.get_by_role("list", name="De document server", exact=True)
    expect(facts).to_contain_text("10.0.0.169")
    expect(facts).to_contain_text("geen limiet")
    expect(facts).to_contain_text("geëist")
    expect(count(tab, "connections")).to_have_text("0")

    page_a, key = open_page(office_browsers, d.anna, d.node)
    page_b, key_b = open_page(office_browsers, d.bram, d.node)
    assert key_b == key
    assert len(fake_info(office, key)["users"]) == 2

    # Het tabblad ververst elke 5 s: 2 verbindingen, 2 mensen, 1 document
    expect(count(tab, "connections")).to_have_text("2", timeout=20_000)
    expect(count(tab, "people")).to_have_text("2")
    expect(count(tab, "documents")).to_have_text("1")
    row = tab.get_by_role("row").filter(has_text=name)
    expect(row).to_contain_text(d.anna.display_name)  # de eigenaar
    expect(row).to_contain_text(f"{d.anna.display_name}, {d.bram.display_name}")  # de bewerkers
    expect(row).to_contain_text("Open")
    admin = office.server.call(office.admin, "GET", ADMIN, expect=200).json()
    assert (admin["connections"], admin["people"], admin["documents"]) == (2, 2, 1), admin
    # Nu tussentijds opslaan zonder wijzigingen: niets nieuws, maar er stond wel iets open
    tab.get_by_role("button", name="Nu tussentijds opslaan", exact=True).click()
    expect(toast(tab, "Er was niets nieuws om op te slaan.")).to_be_visible()

    # Allebei sluiten: weer 0, de piek blijft
    close_editor(page_a)
    close_editor(page_b)
    callback(office, key, 4)  # niets veranderd: status 4
    expect(count(tab, "connections")).to_have_text("0", timeout=20_000)
    expect(tab.get_by_text("Er is nu geen document open in de editor.", exact=True)).to_be_visible()
    peak = office.server.call(office.admin, "GET", ADMIN, expect=200).json()["peak"]
    assert peak is not None and peak["connections"] >= 2, peak


def test_grens_op_1_de_tweede_bewerker_krijgt_de_melding(office: Office, office_browsers) -> None:
    d = duo(office, f"grens-{uuid.uuid4().hex[:6]}.docx")
    tab = office_tab(office, office_browsers)
    limit = tab.get_by_label("Hooguit zoveel bewerkers op één document", exact=True)
    expect(limit).to_have_value("20")
    try:
        # Met het toetsenbord: invullen en Enter (het formulier)
        limit.fill("1")
        save_with_enter(tab, limit)
        expect(toast(tab, "Instellingen opgeslagen.")).to_be_visible()
        assert office.server.call(office.admin, "GET", ADMIN).json()["max_editors"] == 1

        page_a, key = open_page(office_browsers, d.anna, d.node)
        page_b = office_browsers.context_as(d.bram, service_workers="block").new_page()
        with page_b.expect_response(is_session(d.node)) as answer:
            page_b.goto(f"/bewerken/{d.node}")
        assert answer.value.status == 429, answer.value.text()
        assert answer.value.json()["detail"]["code"] == "office_too_many"
        expect(page_b.get_by_role("alert")).to_contain_text("te veel mensen tegelijk")
        assert len(fake_info(office, key)["users"]) == 1
        close_editor(page_a)
        callback(office, key, 4)

        # Terug naar 20 met het formulier: Bram kan weer
        limit.fill("20")
        save_with_enter(tab, limit)
        expect(toast(tab, "Instellingen opgeslagen.").last).to_be_visible()
        assert office.server.call(office.admin, "GET", ADMIN).json()["max_editors"] == 20
    finally:
        office.server.call(office.admin, "PUT", ADMIN, json=DEFAULTS, expect=200)


def test_na_opslaan_ziet_de_eigen_app_het_meteen(office: Office, office_browsers) -> None:
    """Zet de beheerder office uit en gaat hij meteen (zonder te herladen) naar Bestanden, dan is
    Nieuw er niet meer, alleen Nieuwe map; weer aan en terug: Nieuw is er meteen weer. De status
    van office in de app gold eerst nog tot een minuut na het opslaan."""
    tab = office_tab(office, office_browsers)
    nav = tab.get_by_role("navigation", name="Hoofdmenu")
    switch = tab.get_by_label("Bewerken en omzetten naar PDF aan", exact=True)
    save = tab.get_by_role("button", name="Opslaan", exact=True)
    new = tab.get_by_role("button", name="Nieuw", exact=True)
    folder = tab.get_by_role("button", name="Nieuwe map", exact=True)
    try:
        nav.get_by_role("link", name="Bestanden", exact=True).click()
        expect(new).to_be_visible()  # office aan: het menu Nieuw
        nav.get_by_role("link", name="Beheer", exact=True).click()
        tab.get_by_role("navigation", name="Onderdelen van beheer").get_by_role(
            "link", name="Office", exact=True
        ).click()
        switch.uncheck()
        save.click()
        expect(toast(tab, "Instellingen opgeslagen.")).to_be_visible()
        nav.get_by_role("link", name="Bestanden", exact=True).click()
        expect(folder).to_be_visible()
        expect(new).to_have_count(0)

        nav.get_by_role("link", name="Beheer", exact=True).click()
        tab.get_by_role("navigation", name="Onderdelen van beheer").get_by_role(
            "link", name="Office", exact=True
        ).click()
        switch.check()
        save.click()
        expect(toast(tab, "Instellingen opgeslagen.").last).to_be_visible()
        nav.get_by_role("link", name="Bestanden", exact=True).click()
        expect(new).to_be_visible()
        expect(folder).to_have_count(0)
    finally:
        office.server.call(office.admin, "PUT", ADMIN, json=DEFAULTS, expect=200)


def test_niet_bereikbaar_leest_de_schermlezer_een_keer_voor(
    office: Office, office_browsers
) -> None:
    """Met DS weg staat "Niet bereikbaar sinds …" als melding (role alert) op het tabblad. Het
    tabblad ververst elke 5 s; stond "… geleden" in de melding, dan las een schermlezer hem elke
    keer opnieuw voor. Nu staat in de melding alleen de vaste tijd, en "geleden" ernaast."""
    assert office.fake is not None
    office.fake.stop()
    try:
        office.wait_state("down", 15)
        page = office_browsers.context_as(office.admin, service_workers="block").new_page()
        page.goto("/beheer/office")
        expect(
            page.get_by_role("heading", name="Office: Niet bereikbaar", exact=True)
        ).to_be_visible(timeout=20_000)
        alert = page.get_by_role("alert").filter(has_text="Niet bereikbaar sinds")
        expect(alert).to_have_count(1)
        expect(alert).not_to_contain_text("geleden")
        expect(page.get_by_text(re.compile(r"geleden"))).to_be_visible()
    finally:
        office.fake.start()
    office.wait_state("ok", 30)


# --- §4.4 punt 7: niet opgeslagen (quotum vol), terugzetten na het vrijmaken van ruimte ---


def used_bytes(office: Office, user_id: str) -> int:
    users = office.server.call(office.admin, "GET", "/api/v1/users", expect=200).json()
    return next(u["used_bytes"] for u in users if u["id"] == user_id)


def set_quota(office: Office, user_id: str, quota: int) -> None:
    office.server.call(
        office.admin, "PATCH", f"/api/v1/users/{user_id}", json={"quota_bytes": quota}, expect=200
    )


def after_recovery_round(office: Office) -> None:
    """Wacht tot het herstel (elke 2 s in de e2e) net een forgotten file probeerde: een nieuwe
    getForgotten in de log van de nep-DS. Daarna heeft de test ruim een seconde voordat de
    volgende ronde begint; zo zet de knop Terugzetten het terug, en niet het herstel zelf."""
    assert office.fake is not None
    since = time.time()

    def tried() -> bool:
        return any(
            e.get("kind") == "command" and e.get("c") == "getForgotten" and e["t"] > since
            for e in office.fake.log()
        )

    wait_until(tried, 15, "een ronde van het herstel")
    time.sleep(0.3)  # de ronde loopt nog even (ophalen, het quotum is vol)


def upload(office: Office, account: Any, name: str) -> str:
    return office.server.call(
        account,
        "PUT",
        f"/api/v1/files/{account.root_id}/upload",
        params={"name": name},
        content=fixture("proef.docx"),
        expect=201,
    ).json()["id"]


def lose(office: Office, browsers: Any, account: Any, node: str, words: str) -> str:
    """Bewerken met een vol quotum en sluiten: de opslag past niet, de app weigert hem, en de
    nep-DS bewaart hem als forgotten file. Geeft de key."""
    page, key = open_page(browsers, account, node)
    type_in_editor(page, words)
    wait_until(lambda: fake_info(office, key).get("dirty"), 10, "de tekst bij de nep-DS")
    close_editor(page)
    wait_until(lambda: key in forgotten(office), 30, "de forgotten file")
    return key


def test_niet_opgeslagen_terugzetten_na_ruimte(office: Office, office_browsers) -> None:
    """Anna bewerkt twee documenten met een vol quotum: allebei niet opgeslagen. Het tabblad
    toont ze met de reden. Terugzetten zonder ruimte geeft de fout; na het vrijmaken van ruimte
    zet Terugzetten het eerste terug als kopie bij Anna. Het tweede gaat weg met Verwijderen
    (en de bevestiging). Alles met het toetsenbord."""
    server, admin = office.server, office.admin
    tag = uuid.uuid4().hex[:6]
    name, other = f"vol-{tag}.docx", f"weg-{tag}.docx"
    anna = create_user(server, admin, name=f"Anna {tag}")
    node = upload(office, anna, name)
    second = upload(office, anna, other)
    set_quota(office, anna.id, used_bytes(office, anna.id))  # vol: geen byte erbij
    try:
        key = lose(office, office_browsers, anna, node, f"past niet {tag}")
        key_other = lose(office, office_browsers, anna, second, f"mag weg {tag}")

        tab = office_tab(office, office_browsers)
        row = tab.get_by_role("row").filter(has_text=name)
        expect(row).to_contain_text("quotum vol", timeout=20_000)
        expect(row).to_contain_text(anna.display_name)
        restore = row.get_by_role("button", name=f"{name} terugzetten", exact=True)
        # De melding op het overzicht
        status = office.server.call(admin, "GET", "/api/v1/admin/status", expect=200).json()
        assert "office_unsaved" in [n["code"] for n in status["notices"]]

        # Verwijderen: Tab naar de knop, Enter; in de bevestiging staat de focus op Annuleren,
        # Tab naar Verwijderen en Enter
        start(tab)
        tab_to(tab, tab.get_by_role("button", name=f"{other} verwijderen", exact=True), limit=60)
        tab.keyboard.press("Enter")
        dialog = tab.get_by_role("dialog", name="Niet-opgeslagen werk verwijderen?")
        expect(dialog).to_contain_text(other)
        expect(dialog.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
        tab.keyboard.press("Tab")
        expect(dialog.get_by_role("button", name="Verwijderen", exact=True)).to_be_focused()
        tab.keyboard.press("Enter")
        expect(toast(tab, "Verwijderd.")).to_be_visible()
        expect(tab.get_by_role("row").filter(has_text=other)).to_have_count(0)
        assert key_other not in forgotten(office)
        # De rij met de knop is weg: de focus staat op de kop van het blok, niet op <body>
        heading = tab.get_by_role("heading", name="Niet opgeslagen", exact=True)
        expect(heading).to_be_focused()

        # Zonder ruimte lukt Terugzetten ook niet: de fout staat erbij, de rij blijft. Vanaf de
        # kop is Terugzetten de volgende Tab.
        tab.keyboard.press("Tab")
        expect(restore).to_be_focused()
        tab.keyboard.press("Enter")
        unsaved = tab.get_by_role("region", name="Niet opgeslagen", exact=True)
        expect(unsaved.get_by_role("alert")).to_have_text("Onvoldoende quotum")
        assert key in forgotten(office)

        after_recovery_round(office)
        set_quota(office, anna.id, 0)  # ruimte vrij (onbeperkt)
        expect(restore).to_be_focused()
        tab.keyboard.press("Enter")
        expect(toast(tab, re.compile(r"^Teruggezet als vol-"))).to_be_visible()
        expect(row).to_have_count(0, timeout=10_000)
        expect(tab.get_by_text("Alles uit de editor is opgeslagen.", exact=True)).to_be_visible()
        expect(heading).to_be_focused()
        assert key not in forgotten(office)
    finally:
        set_quota(office, anna.id, 0)

    copies = [n for n in children(office, anna, anna.root_id) if "hersteld" in n["name"]]
    assert len(copies) == 1, copies
    copy = copies[0]
    assert copy["name"].startswith(f"vol-{tag} (hersteld ") and copy["name"].endswith(".docx")
    content = server.call(anna, "GET", f"/api/v1/files/{copy['id']}/content", expect=200)
    assert f"past niet {tag}" in docx_text(content.content)
    original = server.call(anna, "GET", f"/api/v1/files/{node}/content", expect=200)
    assert f"past niet {tag}" not in docx_text(original.content)
    gone = server.call(anna, "GET", f"/api/v1/files/{second}/content", expect=200)
    assert f"mag weg {tag}" not in docx_text(gone.content)


# --- §4.4 punt 9, het deel van O5: alleen het toetsenbord, en in het Engels ---


def test_toetsenbord_en_engels(office: Office, office_browsers) -> None:
    """Het tabblad met alleen het toetsenbord: Tab naar de schakelaar, Spatie zet office uit,
    Enter in een veld slaat op; en weer aan. Daarna het tabblad in het Engels."""
    tab = office_tab(office, office_browsers)
    switch = tab.get_by_label("Bewerken en omzetten naar PDF aan", exact=True)
    save = tab.get_by_role("button", name="Opslaan", exact=True)
    try:
        start(tab)
        tab_to(tab, switch, limit=60)
        expect(switch).to_be_checked()
        tab.keyboard.press("Space")
        expect(switch).not_to_be_checked()
        tab_to(tab, save)  # langs de drie velden
        tab.keyboard.press("Enter")
        expect(toast(tab, "Instellingen opgeslagen.")).to_be_visible()
        expect(tab.get_by_text("Bewerken staat uit:", exact=False)).to_be_visible()
        status = office.server.call(office.admin, "GET", "/api/v1/office/status").json()
        assert (status["enabled"], status["editing"]) == (False, False), status

        # Weer aan: Shift+Tab terug naar de schakelaar, Spatie, en Enter in het eerste veld
        tab_to(tab, switch, back=True)
        tab.keyboard.press("Space")
        expect(switch).to_be_checked()
        tab.keyboard.press("Tab")
        expect(
            tab.get_by_label("Hooguit zoveel bewerkers op één document", exact=True)
        ).to_be_focused()
        tab.keyboard.press("Enter")
        expect(tab.get_by_text("Bewerken staat uit:", exact=False)).to_have_count(0)
        status = office.server.call(office.admin, "GET", "/api/v1/office/status").json()
        assert (status["enabled"], status["editing"]) == (True, True), status

        # In het Engels: dezelfde plek, Alt+7 vanaf het overzicht
        office.server.call(
            office.admin, "PATCH", "/api/v1/auth/me", json={"locale": "en"}, expect=200
        )
        tab.goto("/beheer")
        tabs = tab.get_by_role("navigation", name="Admin sections")
        expect(tabs.get_by_role("link", name="Office", exact=True)).to_be_visible()
        tab.keyboard.press("Alt+7")
        expect(tab).to_have_url(re.compile(r"/beheer/office$"))
        expect(tab.get_by_role("heading", name="Office: Healthy", exact=True)).to_be_visible()
        facts = tab.get_by_role("list", name="The document server", exact=True)
        expect(facts).to_contain_text("no limit")
        expect(facts).to_contain_text("required")
        expect(tab.get_by_label("Editing and PDF conversion on", exact=True)).to_be_checked()
        expect(
            tab.get_by_role("heading", name="Turning office on or off on the server")
        ).to_be_visible()
        expect(tab.locator("code").filter(has_text="install.sh | OFFICE=uit bash")).to_be_visible()
        expect(tab.locator("html")).to_have_attribute("lang", "en")
    finally:
        office.server.call(office.admin, "PATCH", "/api/v1/auth/me", json={"locale": None})
        office.server.call(office.admin, "PUT", ADMIN, json=DEFAULTS, expect=200)
