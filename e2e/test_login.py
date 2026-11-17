"""Inloggen (P1): 2FA met Terug, geen verbinding, de blokkade na foute codes, een geblokkeerd
account en Engels. Elk 2FA-geval heeft een eigen gebruiker (zie docs/fase-3/e2e.md)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

import pytest
from playwright.sync_api import expect

from conftest import enable_2fa, totp_code

pytestmark = pytest.mark.p1

FORGOT_NL = "Wachtwoord vergeten? Vraag je beheerder om een nieuw wachtwoord."
FORGOT_EN = "Forgot your password? Ask your administrator for a new one."
LOCKED_NL = re.compile(r"^Te veel foute codes\. Probeer het over \d+ \w+ opnieuw\.$")


def open_login(page, url: str = "/") -> None:
    page.goto(url)
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()


def fill_login(page, account) -> None:
    page.get_by_label("E-mailadres", exact=True).fill(account.email)
    page.get_by_label("Wachtwoord", exact=True).fill(account.password)
    page.get_by_role("button", name="Inloggen", exact=True).click()


def expect_logged_in(page) -> None:
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()


def test_inloggen_met_code_en_terug(server, user_factory, page):
    account = user_factory(name="Twee Factor")
    two = enable_2fa(server, account)
    open_login(page)
    # Het formulier staat er meteen, zonder /auth/status (de refresh wist het al)
    assert page.evaluate("performance.getEntriesByName('app:ready:login').length") == 1
    statuses = [
        e
        for e in page.evaluate("performance.getEntriesByType('resource').map((r) => r.name)")
        if e.endswith("/api/v1/auth/status")
    ]
    assert statuses == []

    # Wachtwoord tonen: met het vinkje en met Alt+Shift+P
    password = page.get_by_label("Wachtwoord", exact=True)
    password.fill(account.password)
    assert password.get_attribute("type") == "password"
    page.get_by_label("Wachtwoord tonen", exact=True).check()
    assert password.get_attribute("type") == "text"
    password.press("Alt+Shift+P")
    assert password.get_attribute("type") == "password"
    expect(page.get_by_text(FORGOT_NL)).to_be_visible()

    page.get_by_label("E-mailadres", exact=True).fill(account.email)
    page.get_by_role("button", name="Inloggen", exact=True).click()
    expect(page.get_by_role("heading", name="Code invoeren", exact=True)).to_be_visible()
    code_field = page.get_by_label("Code uit je authenticator-app", exact=True)
    expect(code_field).to_be_focused()

    # Terug met de knop: weer het wachtwoordformulier, het e-mailadres staat er nog
    page.get_by_role("button", name="Terug", exact=True).click()
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    expect(page.get_by_label("E-mailadres", exact=True)).to_have_value(account.email)
    expect(page.get_by_label("Wachtwoord", exact=True)).to_be_focused()

    # Nog eens, en nu terug met Escape (vanuit het codeveld)
    page.get_by_role("button", name="Inloggen", exact=True).click()
    expect(code_field).to_be_focused()
    code_field.press("Escape")
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()

    # Een foute code, dan de goede
    page.get_by_role("button", name="Inloggen", exact=True).click()
    code_field.fill("000000")
    page.get_by_role("button", name="Inloggen", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("De code klopt niet")
    expect(code_field).to_have_value("")
    code, _ = totp_code(two["secret"], after_step=two["step"])
    code_field.fill(code)
    code_field.press("Enter")
    expect_logged_in(page)


def test_geen_verbinding(page):
    # Inloggen zelf lukt niet: een melding, het formulier blijft staan
    page.route("**/api/v1/auth/login", lambda route: route.abort())
    open_login(page)
    page.get_by_label("E-mailadres", exact=True).fill("iemand@voorbeeld.nl")
    page.get_by_label("Wachtwoord", exact=True).fill("wat-dan-ook-123")
    page.get_by_role("button", name="Inloggen", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Geen verbinding met de server")
    expect(page.get_by_role("button", name="Inloggen", exact=True)).to_be_enabled()


def test_geen_verbinding_bij_status_en_weer_online(page):
    """Zei de refresh niet of er al een account is, dan vraagt het scherm /auth/status. Lukt dat
    niet, dan een eigen scherm dat het opnieuw probeert zodra de browser online komt."""
    body = json.dumps({"detail": {"message": "Niet ingelogd", "code": "not_logged_in"}})
    page.route(
        "**/api/v1/auth/refresh",
        lambda route: route.fulfill(status=401, body=body, content_type="application/json"),
    )
    page.route("**/api/v1/auth/status", lambda route: route.abort())
    page.goto("/")
    expect(page.get_by_role("heading", name="Geen verbinding met de server")).to_be_visible()
    expect(page.get_by_role("button", name="Opnieuw proberen")).to_be_visible()
    page.unroute("**/api/v1/auth/status")
    page.evaluate("window.dispatchEvent(new Event('online'))")
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()


def test_blokkade_na_vijf_foute_codes(server, user_factory, page):
    account = user_factory(name="Raadt Mis")
    enable_2fa(server, account)
    open_login(page)
    fill_login(page, account)
    code_field = page.get_by_label("Code uit je authenticator-app", exact=True)
    expect(code_field).to_be_visible()
    for _ in range(5):
        code_field.fill("000000")
        page.get_by_role("button", name="Inloggen", exact=True).click()
        expect(page.get_by_role("alert")).to_have_text("De code klopt niet")
        expect(code_field).to_have_value("")
    # De zesde: geblokkeerd, met wanneer het weer kan (params.until via fmt.relative)
    code_field.fill("000000")
    page.get_by_role("button", name="Inloggen", exact=True).click()
    alert = page.get_by_role("alert")
    expect(alert).to_have_text(LOCKED_NL)
    # Terug en opnieuw met het wachtwoord: de server meldt de blokkade al vóór de code
    page.get_by_role("button", name="Terug", exact=True).click()
    page.get_by_role("button", name="Inloggen", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text(re.compile(r"^Te veel foute codes\."))


def test_geblokkeerd_account(server, user_factory, page, seeded):
    from sqlalchemy import update

    import seed
    from app.models import User

    account = user_factory(name="Geblokkeerd")

    def block(data_dir):
        with seed.connect(data_dir) as conn:
            conn.execute(
                update(User)
                .where(User.id == uuid.UUID(account.id))
                .values(disabled_at=datetime.now(UTC))
            )

    seeded(block)
    open_login(page)
    fill_login(page, account)
    expect(page.get_by_role("alert")).to_contain_text("geblokkeerd")
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()


def test_engels(new_context, user_factory):
    account = user_factory(name="English")
    page = new_context(locale="en-US").new_page()
    page.goto("/")
    expect(page.get_by_role("heading", name="Log in", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.lang") == "en"
    page.get_by_label("Email address", exact=True).fill(account.email)
    page.get_by_label("Password", exact=True).fill("helemaal-fout-123")
    expect(page.get_by_text(FORGOT_EN)).to_be_visible()
    page.get_by_role("button", name="Log in", exact=True).click()
    # De fout op code vertaald (de server stuurt Nederlands)
    expect(page.get_by_role("alert")).to_have_text("Email address or password is incorrect")
    page.get_by_label("Password", exact=True).fill(account.password)
    page.get_by_role("button", name="Log in", exact=True).click()
    expect(page.get_by_role("navigation", name="Main menu")).to_be_visible()
