"""Rooktest (P0, frozen na P0): het hele rondje door de app zoals een gebruiker het doet.
Inloggen → Bestanden → een bestand uploaden → openen in de viewer → thema wisselen →
uitloggen. Plus een controle dat de bewaking (CSP, i18n) echt iets vangt."""

from __future__ import annotations

import re

from playwright.sync_api import expect

DARK_BG = "#121212"


def login_via_form(page, account) -> None:
    page.goto("/")
    expect(page.get_by_role("heading", name="Inloggen")).to_be_visible()
    page.get_by_label("E-mailadres").fill(account.email)
    page.get_by_label("Wachtwoord").fill(account.password)
    page.get_by_role("button", name="Inloggen").click()


def open_account_menu(page, account):
    page.get_by_role("button", name=account.display_name).click()
    menu = page.get_by_role("menu", name=f"Accountmenu van {account.display_name}")
    expect(menu).to_be_visible()
    return menu


def choose_theme(page, account, label: str) -> None:
    menu = open_account_menu(page, account)
    menu.get_by_role("menuitem", name="Thema").click()
    page.get_by_role("menuitemcheckbox", name=label).click()


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
    expect(nav.get_by_role("link", name="Bestanden")).to_have_attribute("aria-current", "page")
    expect(page).to_have_title(re.compile("Bestanden"))
    expect(page.get_by_text("Deze map is leeg.")).to_be_visible()

    # Uploaden via de knop (de bestandskiezer van de browser)
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden").click()
    chooser.value.set_files(
        {"name": "hallo.txt", "mimeType": "text/plain", "buffer": b"Hallo vanuit e2e\n"}
    )
    row_button = page.get_by_role("button", name="hallo.txt", exact=True)
    expect(row_button).to_be_visible()
    listing = server.call(user, "GET", "/api/v1/files/root", expect=200).json()
    assert [n["name"] for n in listing["children"]] == ["hallo.txt"]

    # Openen: de viewer (stub van P0) met ?bestand= in het adres; sluiten haalt het weg
    row_button.click()
    viewer = page.get_by_role("dialog", name="hallo.txt")
    expect(viewer).to_be_visible()
    expect(page).to_have_url(re.compile(r"[?&]bestand="))
    viewer.get_by_role("button", name="Sluiten").click()
    expect(viewer).to_be_hidden()
    expect(page).not_to_have_url(re.compile(r"[?&]bestand="))

    # Thema: donker, blijft na herladen (boot.js), dan terug naar het systeem
    choose_theme(page, user, "Donker")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert css_var(page, "--bg") == DARK_BG
    assert page.evaluate("localStorage.getItem('platform-theme')") == "dark"
    page.reload()
    expect(page.get_by_role("button", name=user.display_name)).to_be_visible()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    choose_theme(page, user, "Licht")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    choose_theme(page, user, "Systeem")
    expect(page.locator("html")).not_to_have_attribute("data-theme", re.compile("."))
    assert page.evaluate("localStorage.getItem('platform-theme')") is None

    # Uitloggen: terug bij het formulier, en herladen logt niet weer in
    menu = open_account_menu(page, user)
    menu.get_by_role("menuitem", name="Uitloggen").click()
    expect(page.get_by_role("heading", name="Inloggen")).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name="Inloggen")).to_be_visible()


def test_ingelogde_context(context_as, user_factory):
    """context_as logt in via de request van de context: de app start meteen ingelogd."""
    user = user_factory(name="Context Test")
    page = context_as(user).new_page()
    page.goto("/")
    expect(page.get_by_role("button", name=user.display_name)).to_be_visible()
    expect(page.get_by_text("Deze map is leeg.")).to_be_visible()


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
    expect(page.locator("tbody tr")).to_have_count(200)
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
    expect(page.get_by_role("heading", name="Inloggen")).to_be_visible()
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
