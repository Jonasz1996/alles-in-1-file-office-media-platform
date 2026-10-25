"""Rooktest (P0, frozen na P0): het hele rondje door de app zoals een gebruiker het doet.
Inloggen → Bestanden → een bestand uploaden → openen in de viewer → thema wisselen →
uitloggen. Plus een controle dat de bewaking (CSP, i18n) echt iets vangt.

De selectors volgen het rooktest-DOM-contract in docs/fase-3/e2e.md: alleen exacte
toegankelijke namen en teksten, en geen rollen of elementen die een pakket mag veranderen
(een rij is een <tr> of role=row, een bestand opent met een klik op zijn naam)."""

from __future__ import annotations

import re

from playwright.sync_api import expect

DARK_BG = "#121212"


def login_via_form(page, account) -> None:
    page.goto("/")
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    page.get_by_label("E-mailadres", exact=True).fill(account.email)
    page.get_by_label("Wachtwoord", exact=True).fill(account.password)
    page.get_by_role("button", name="Inloggen", exact=True).click()


def open_account_menu(page, account):
    page.get_by_role("button", name=account.display_name, exact=True).click()
    menu = page.get_by_role("menu", name=f"Accountmenu van {account.display_name}")
    expect(menu).to_be_visible()
    return menu


def choose_theme(page, account, label: str) -> None:
    menu = open_account_menu(page, account)
    menu.get_by_role("menuitem", name="Thema", exact=True).click()
    page.get_by_role("menuitemradio", name=label, exact=True).click()


def css_var(page, name: str) -> str:
    return page.evaluate(
        "(n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()", name
    )


def test_rondje_door_de_app(new_context, user_factory, server):
    user = user_factory(name="Rook Test")
    page = new_context().new_page()

    # Inloggen met het formulier (2FA staat uit: geen code)
    login_via_form(page, user)
    nav = page.get_by_role("navigation", name="Hoofdmenu")
    expect(nav.get_by_role("link", name="Bestanden", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    expect(page).to_have_title(re.compile("Bestanden"))
    expect(page.get_by_text("Deze map is leeg.", exact=True)).to_be_visible()

    # Uploaden via de knop (de bestandskiezer van de browser)
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files(
        {"name": "hallo.txt", "mimeType": "text/plain", "buffer": b"Hallo vanuit e2e\n"}
    )
    # Rolneutraal gezocht (het is een rij in het grid van de bestandsbrowser). Eén keer in de
    # pagina (#inhoud) zodra de upload klaar is: de voortgangsregel is dan weg
    item = page.locator("#inhoud").get_by_text("hallo.txt")
    expect(item).to_have_count(1)
    expect(item).to_be_visible()
    listing = server.call(user, "GET", "/api/v1/files/root", expect=200).json()
    assert [n["name"] for n in listing["children"]] == ["hallo.txt"]

    # Openen: de viewer met ?bestand= in het adres; sluiten haalt het weg
    item.click()
    viewer = page.get_by_role("dialog", name="hallo.txt", exact=True)
    expect(viewer).to_be_visible()
    expect(page).to_have_url(re.compile(r"[?&]bestand="))
    viewer.get_by_role("button", name="Sluiten", exact=True).click()
    expect(viewer).to_be_hidden()
    expect(page).not_to_have_url(re.compile(r"[?&]bestand="))

    # Thema: donker, blijft na herladen (boot.js), dan terug naar het systeem
    choose_theme(page, user, "Donker")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert css_var(page, "--bg") == DARK_BG
    assert page.evaluate("localStorage.getItem('platform-theme')") == "dark"
    page.reload()
    expect(page.get_by_role("button", name=user.display_name, exact=True)).to_be_visible()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    choose_theme(page, user, "Licht")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    choose_theme(page, user, "Systeem")
    expect(page.locator("html")).not_to_have_attribute("data-theme", re.compile("."))
    assert page.evaluate("localStorage.getItem('platform-theme')") is None

    # Uitloggen: terug bij het formulier, en herladen logt niet weer in
    menu = open_account_menu(page, user)
    menu.get_by_role("menuitem", name="Uitloggen", exact=True).click()
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()


def test_ingelogde_context(context_as, user_factory):
    """context_as logt in via de request van de context: de app start meteen ingelogd."""
    user = user_factory(name="Context Test")
    page = context_as(user).new_page()
    page.goto("/")
    expect(page.get_by_role("button", name=user.display_name, exact=True)).to_be_visible()
    expect(page.get_by_text("Deze map is leeg.", exact=True)).to_be_visible()


def test_publieke_link_uit_seed(new_context, user_factory, seeded):
    """seed.py en de publieke pagina: de link van seed_public opent zonder inloggen, met 200
    items en echte thumbnails, en zonder i18n-fout bij de paginatitel."""
    import seed

    owner = user_factory(name="Deler")
    url = seeded(lambda data_dir: seed.seed_public(data_dir, owner, seed.make_blobs(data_dir, 60)))
    assert url.startswith("/s/")
    page = new_context().new_page()
    page.goto(url)
    expect(page.get_by_role("heading", level=1)).to_have_text(re.compile(r"^Openbaar "))
    # Elk item is een rij (<tr> of role=row); een kopregel (met columnheader) telt niet mee
    items = page.get_by_role("row").filter(has_not=page.get_by_role("columnheader"))
    expect(items).to_have_count(200)
    expect(page).to_have_title(re.compile(r"^Openbaar "))
    page.wait_for_function(
        """() => [...document.images].some(
          (i) => i.complete && i.naturalWidth > 0 && i.src.includes("/api/v1/previews/"))"""
    )


def test_bewaking_vangt_csp_en_i18n(browsers):
    """De bewaking zelf: een inline script (CSP) en een i18n-fout komen allebei binnen."""
    context = browsers.new_context()
    page = context.new_page()
    page.goto("/")
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    page.evaluate(
        """() => {
          const s = document.createElement("script");
          s.textContent = "window.__nietToegestaan = 1";
          document.head.appendChild(s);
          console.error("i18n: gebied 'test' is niet geladen");
        }"""
    )
    guard = browsers.guard(context)
    for _ in range(50):
        if len(guard.problems) >= 2:
            break
        page.wait_for_timeout(100)
    problems = guard.take()  # verwacht: niet laten falen bij het opruimen
    assert any(p.startswith("csp: ") and "script-src" in p for p in problems), problems
    assert any(p.startswith("i18n: ") for p in problems), problems
    assert page.evaluate("window.__nietToegestaan") is None


def test_bewaking_vangt_fouten(browsers):
    """Een gelogde fout (zoals een foutgrens die logt) en een onafgevangen fout komen binnen."""
    context = browsers.new_context()
    page = context.new_page()
    page.goto("/")
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    page.evaluate(
        """() => {
          console.error(new Error("gevangen door een foutgrens"), "\\n    in UploadSlot");
          setTimeout(() => { throw new Error("niet gevangen"); }, 0);
        }"""
    )
    guard = browsers.guard(context)
    for _ in range(50):
        if len(guard.problems) >= 2:
            break
        page.wait_for_timeout(100)
    problems = guard.take()
    assert any("gevangen door een foutgrens" in p for p in problems), problems
    assert any("niet gevangen" in p for p in problems), problems


def test_uploadlade_tijdens_een_trage_upload(new_context, user_factory):
    """Zolang er een upload loopt, staat de lade (lazy, P3) in de schil. Die mag niet vallen: de
    bewaking vangt de fout die een foutgrens logt (zoals React #306, een lazy die op een lazy
    uitkomt)."""
    user = user_factory(name="Trage Upload")
    page = new_context().new_page()
    login_via_form(page, user)

    held = []

    def hold(route):  # de tus-verzoeken ophouden: de wachtrij blijft zo bezig
        held.append(route)

    page.route("**/api/v1/tus**", hold)
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files({"name": "traag.txt", "mimeType": "text/plain", "buffer": b"x" * 1000})
    page.wait_for_timeout(1500)  # de lade laadt terwijl de upload wacht
    assert held, "geen tus-verzoek gezien"
    page.unroute("**/api/v1/tus**", hold)  # laat ook de opgehouden verzoeken verder gaan
    expect(page.locator("#inhoud").get_by_text("traag.txt", exact=True)).to_have_count(1)
