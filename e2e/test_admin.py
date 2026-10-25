"""Beheer en account in de browser (P6): een gebruiker maken, wijzigen, blokkeren en
verwijderen; de quotumbalk; de tabbladen van /beheer met het toetsenbord; de taal van het
account naar het Engels (en dan ook beheer in het Engels)."""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime

import pytest
from playwright.sync_api import expect

from conftest import PASSWORD, Account, unique_email

pytestmark = pytest.mark.p6


def wait_until(check, seconds: float = 30.0, step: float = 0.25) -> None:
    """Wacht tot check() waar is (een achtergrondtaak); anders faalt de test."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(step)
    raise AssertionError("het gebeurde niet op tijd")


def users_of(server, admin) -> dict[str, dict]:
    return {u["id"]: u for u in server.call(admin, "GET", "/api/v1/users", expect=200).json()}


@pytest.fixture
def ui_admin(user_factory):
    """Een eigen beheerder per test: de sessie-beheerder (fixture admin) blijft zoals hij is."""
    return user_factory("admin", name="UI Beheerder")


def test_gebruiker_maken_wijzigen_blokkeren_verwijderen(context_as, server, ui_admin):
    admin = ui_admin
    page = context_as(admin).new_page()
    page.goto("/beheer/gebruikers")
    expect(page.get_by_role("heading", name="Gebruikers", exact=True)).to_be_visible()

    # N: een nieuwe gebruiker, met een quotum van 1 GB
    page.keyboard.press("n")
    dialog = page.get_by_role("dialog", name="Nieuwe gebruiker", exact=True)
    expect(dialog).to_be_visible()
    email = unique_email("beheer-ui")
    dialog.get_by_label("Naam", exact=True).fill("Ans Admintest")
    dialog.get_by_label("E-mailadres", exact=True).fill(email)
    dialog.get_by_label("Wachtwoord", exact=True).fill(PASSWORD)
    dialog.get_by_label("Quotum in GB", exact=True).fill("1")
    dialog.get_by_role("button", name="Toevoegen", exact=True).click()
    expect(dialog).to_be_hidden()
    row = page.get_by_role("row").filter(has_text=email)
    expect(row).to_be_visible()
    expect(row).to_contain_text("Actief")
    created = next(u for u in users_of(server, admin).values() if u["email"] == email)
    assert created["quota_bytes"] == 1024**3

    # Iets opslaan als die gebruiker, zodat de balk iets toont
    ans = Account(created["id"], email, PASSWORD, "Ans Admintest", "user", created["root_id"])
    server.call(
        ans,
        "PUT",
        f"/api/v1/files/{created['root_id']}/upload",
        params={"name": "notities.txt"},
        content=b"x" * 50_000,
        expect=201,
    )

    # Zoeken met / en wijzigen: naam en quotum; de quotumbalk staat in de dialoog
    page.reload()
    expect(page.get_by_role("row").filter(has_text=email)).to_be_visible()
    page.keyboard.press("/")
    expect(page.get_by_label("Zoek op naam of e-mailadres", exact=True)).to_be_focused()
    page.keyboard.type("Admintest")
    row = page.get_by_role("row").filter(has_text=email)
    expect(row).to_contain_text(re.compile(r"van 1(,0)? GB"))
    row.get_by_role("button", name="Ans Admintest wijzigen", exact=True).click()
    dialog = page.get_by_role("dialog", name="Ans Admintest wijzigen", exact=True)
    meter = dialog.locator("meter")
    expect(meter).to_have_count(1)
    assert float(meter.get_attribute("value") or 0) >= 50_000
    expect(dialog.get_by_text("Opslag van Ans Admintest", exact=True)).to_be_visible()
    dialog.get_by_label("Naam", exact=True).fill("Ans Bewerkt")
    dialog.get_by_label("Quotum in GB", exact=True).fill("2")
    dialog.get_by_role("button", name="Opslaan", exact=True).click()
    expect(dialog).to_be_hidden()
    changed = users_of(server, admin)[created["id"]]
    assert changed["display_name"] == "Ans Bewerkt"
    assert changed["quota_bytes"] == 2 * 1024**3

    # Blokkeren na een vraag: daarna kan Ans niet meer inloggen
    page.get_by_label("Zoek op naam of e-mailadres", exact=True).fill("Bewerkt")
    page.get_by_role("button", name="Ans Bewerkt wijzigen", exact=True).click()
    dialog = page.get_by_role("dialog", name="Ans Bewerkt wijzigen", exact=True)
    remove = dialog.get_by_role("button", name="Verwijderen…", exact=True)
    expect(remove).to_be_disabled()  # eerst blokkeren
    dialog.get_by_role("button", name="Blokkeren", exact=True).click()
    ask = page.get_by_role("dialog", name="Ans Bewerkt blokkeren?", exact=True)
    expect(ask).to_be_visible()
    ask.get_by_role("button", name="Blokkeren", exact=True).click()
    expect(dialog.get_by_role("button", name="Blokkade opheffen", exact=True)).to_be_visible()
    r = server.request("POST", "/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code in (401, 403)
    assert r.json()["detail"]["code"] == "account_disabled"

    # Verwijderen: alleen na het typen van het e-mailadres; daarna op de achtergrond weg
    expect(remove).to_be_enabled()
    remove.click()
    ask = page.get_by_role("dialog", name="Ans Bewerkt definitief verwijderen?", exact=True)
    confirm_button = ask.get_by_role("button", name="Definitief verwijderen", exact=True)
    expect(confirm_button).to_be_disabled()
    ask.get_by_role("textbox").fill(email)
    confirm_button.click()
    expect(dialog).to_be_hidden()
    wait_until(lambda: created["id"] not in users_of(server, admin))
    page.reload()
    page.get_by_label("Zoek op naam of e-mailadres", exact=True).fill(email)
    expect(page.get_by_text("Niemand gevonden.", exact=True)).to_be_visible()


def test_tabbladen_met_het_toetsenbord(context_as, ui_admin):
    page = context_as(ui_admin).new_page()
    page.goto("/beheer")
    tabs = page.get_by_role("navigation", name="Onderdelen van beheer")
    expect(tabs.get_by_role("link", name="Overzicht", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    expect(page.get_by_role("heading", name="Meldingen", exact=True)).to_be_visible()
    steps = [
        ("Alt+2", "/beheer/gebruikers", "Gebruikers", "Gebruikers"),
        ("Alt+3", "/beheer/opslag", "Opslag", "Opslag"),
        ("Alt+4", "/beheer/taken", "Taken", "Achtergrondtaken"),
        ("Alt+5", "/beheer/groepen", "Groepen", "Groepen"),
        ("Alt+6", "/beheer/beveiliging", "Beveiliging", "Tweestapsverificatie"),
        ("Alt+1", "/beheer", "Overzicht", "Meldingen"),
    ]
    for key, path, tab, heading in steps:
        page.keyboard.press(key)
        expect(page).to_have_url(re.compile(re.escape(path) + r"$"))
        expect(tabs.get_by_role("link", name=tab, exact=True)).to_have_attribute(
            "aria-current", "page"
        )
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()

    # Opslag: een balk per node en de bewaartermijn
    page.goto("/beheer/opslag")
    expect(page.get_by_role("heading", name="Datadisk per node", exact=True)).to_be_visible()
    expect(page.locator(".storage-bar")).to_have_count(1)
    retention = page.get_by_label("Dagen bewaren (0 = tot iemand hem leegt)", exact=True)
    expect(retention).to_have_value("0")


def test_account_in_het_engels(context_as, server, user_factory):
    """Een beheerder zet zijn account op Engels: na het herladen is alles Engels, ook /beheer."""
    other = user_factory("admin", name="Engelse Beheerder")
    page = context_as(other).new_page()
    page.goto("/account")
    expect(page.get_by_role("heading", name="Opslag", exact=True)).to_be_visible()
    expect(page.get_by_text("Gebruikt", exact=True)).to_be_visible()
    page.get_by_label("Taal", exact=True).select_option("en")
    page.get_by_role("button", name="Taal wijzigen", exact=True).click()
    expect(page.get_by_role("heading", name="Storage", exact=True)).to_be_visible()
    expect(page.locator("html")).to_have_attribute("lang", "en")
    nav = page.get_by_role("navigation", name="Main menu")
    expect(nav.get_by_role("link", name="Files", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Devices and sessions", exact=True)).to_be_visible()
    me = server.call(other, "GET", "/api/v1/auth/me", expect=200).json()
    assert me["locale"] == "en"

    page.goto("/beheer/gebruikers")
    tabs = page.get_by_role("navigation", name="Admin sections")
    expect(tabs.get_by_role("link", name="Users", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    expect(page.get_by_role("button", name="New user", exact=True)).to_be_visible()
    page.keyboard.press("Alt+4")
    expect(page.get_by_role("heading", name="Background jobs", exact=True)).to_be_visible()
    page.keyboard.press("Alt+1")
    expect(page.get_by_role("heading", name="Notices", exact=True)).to_be_visible()


def failed_job(data_dir) -> None:
    """Een mislukte taak van een soort die niet bestaat, rechtstreeks in de database."""
    import seed
    from app.models import Job

    now = datetime.now(UTC)
    with seed.connect(data_dir) as conn:
        conn.execute(
            Job.__table__.insert(),
            {
                "id": uuid.uuid4(),
                "type": "e2e.kapot",
                "key": "e2e:kapot",
                "status": "failed",
                "payload": {},
                "attempts": 3,
                "max_attempts": 3,
                "run_after": now,
                "started_at": now,
                "finished_at": now,
                "error": "e2e: expres mislukt",
                "created_at": now,
            },
        )


def test_taken_en_groepen_met_het_toetsenbord(context_as, ui_admin, seeded):
    """Shift+R zet mislukte taken terug in de wachtrij (na een vraag); N maakt een groep."""
    seeded(failed_job)
    page = context_as(ui_admin).new_page()
    page.goto("/beheer/taken")
    expect(page.get_by_role("row").filter(has_text="e2e: expres mislukt")).to_have_count(1)
    page.keyboard.press("Shift+R")
    ask = page.get_by_role("dialog", name="Alle mislukte taken opnieuw proberen?", exact=True)
    expect(ask).to_be_visible()
    ask.get_by_role("button", name="Alles opnieuw", exact=True).click()
    notes = page.get_by_role("region", name="Meldingen")
    expect(notes.get_by_text(re.compile(r"weer in de wachtrij\.$"))).to_be_visible()

    page.keyboard.press("Alt+5")
    expect(page.get_by_role("heading", name="Groepen", exact=True)).to_be_visible()
    page.keyboard.press("n")
    field = page.get_by_label("Naam van een nieuwe groep", exact=True)
    expect(field).to_be_focused()
    name = f"Toetsenbordgroep {uuid.uuid4().hex[:6]}"
    page.keyboard.type(name)
    page.keyboard.press("Enter")
    expect(page.get_by_role("row").filter(has_text=name)).to_have_count(1)
