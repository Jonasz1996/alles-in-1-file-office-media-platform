"""De checklist van docs/fase-3/toetsenbord-p6.md met alleen het toetsenbord: Tab, Shift+Tab,
Enter, pijltjes, de sneltoetsen en typen; nergens een klik. Ook waar de focus na een actie staat:
in een dialoog nooit op <body> (dan werkt Tab nog wel, maar een schermlezer zegt niets meer en
de volgende Enter doet niets). De tabbladen van /beheer (Alt+1 … Alt+6) en Groepen (N) lopen al
met het toetsenbord in test_admin."""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from conftest import PASSWORD, unique_email

pytestmark = pytest.mark.p6

ROW = '#inhoud [role=grid] [role=row][tabindex="0"]'


def start(page: Page) -> None:
    """Het beginpunt: de focus nergens (zoals na het laden), zodat een sneltoets werkt."""
    page.evaluate("() => document.activeElement && document.activeElement.blur()")


def tab_to(page: Page, target: Locator, limit: int = 40, back: bool = False) -> int:
    """Tab (of Shift+Tab) tot target de focus heeft; geeft het aantal keer."""
    target.wait_for(state="attached")
    seen = []
    for i in range(limit):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return i + 1
        seen.append(page.evaluate(FOCUSED))
    raise AssertionError(f"Tab kwam niet bij {target}: {seen}")


FOCUSED = """() => {
  const a = document.activeElement;
  if (!a) return "-";
  const name = (a.getAttribute("aria-label") || a.textContent || "").trim();
  return `${a.tagName} ${name.slice(0, 40)}`;
}"""


def not_on_body(page: Page) -> None:
    assert page.evaluate("() => document.activeElement && document.activeElement !== document.body")


def toast(page: Page, text: str | re.Pattern[str]) -> Locator:
    region = page.get_by_role("region", name="Meldingen")
    if isinstance(text, str):
        return region.get_by_text(text, exact=True)
    return region.get_by_text(text)


def wait_until(check, seconds: float = 30.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.25)
    raise AssertionError("het gebeurde niet op tijd")


def upload(server, account, name: str, data: bytes) -> dict[str, Any]:
    return server.call(
        account,
        "PUT",
        f"/api/v1/files/{account.root_id}/upload",
        params={"name": name},
        content=data,
        expect=201,
    ).json()


def test_gebruiker_toevoegen_blokkeren_en_verwijderen(context_as, server, user_factory):
    """Gebruikers: N, de velden typen, Enter voegt toe; / zoekt; Tab naar Wijzigen, Enter.
    Blokkeren en verwijderen met alleen Tab, Enter en typen."""
    admin = user_factory("admin", name="Toets Beheerder")
    page = context_as(admin).new_page()
    page.goto("/beheer/gebruikers")
    expect(page.get_by_role("heading", name="Gebruikers", exact=True)).to_be_visible()
    start(page)
    page.keyboard.press("n")
    dialog = page.get_by_role("dialog", name="Nieuwe gebruiker", exact=True)
    expect(dialog.get_by_label("Naam", exact=True)).to_be_focused()
    email = unique_email("toets")
    page.keyboard.type("Kees Toets")
    page.keyboard.press("Tab")
    page.keyboard.type(email)
    page.keyboard.press("Tab")
    page.keyboard.type(PASSWORD)
    page.keyboard.press("Enter")
    expect(dialog).to_be_hidden()
    expect(toast(page, "Kees Toets is toegevoegd.")).to_be_visible()
    users = server.call(admin, "GET", "/api/v1/users", expect=200).json()
    kees = next(u for u in users if u["email"] == email)

    start(page)
    page.keyboard.press("/")
    expect(page.get_by_label("Zoek op naam of e-mailadres", exact=True)).to_be_focused()
    page.keyboard.type("Kees Toets")
    tab_to(page, page.get_by_role("button", name="Kees Toets wijzigen", exact=True))
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Kees Toets wijzigen", exact=True)
    expect(dialog).to_be_visible()

    # Blokkeren: de vraag begint op Annuleren; Tab, Enter. Daarna staat de focus op dezelfde
    # knop, die nu "Blokkade opheffen" heet (niet op <body>)
    tab_to(page, dialog.get_by_role("button", name="Blokkeren", exact=True))
    page.keyboard.press("Enter")
    ask = page.get_by_role("dialog", name="Kees Toets blokkeren?", exact=True)
    expect(ask.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(ask.get_by_role("button", name="Blokkeren", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(ask).to_be_hidden()
    unblock = dialog.get_by_role("button", name="Blokkade opheffen", exact=True)
    expect(unblock).to_be_visible()
    expect(unblock).to_be_focused()
    users = server.call(admin, "GET", "/api/v1/users", expect=200).json()
    assert next(u for u in users if u["id"] == kees["id"])["disabled"] is True

    # Verwijderen: Enter doet pas iets als het e-mailadres er staat
    tab_to(page, dialog.get_by_role("button", name="Verwijderen…", exact=True))
    page.keyboard.press("Enter")
    ask = page.get_by_role("dialog", name="Kees Toets definitief verwijderen?", exact=True)
    expect(ask.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    field = ask.get_by_role("textbox")
    tab_to(page, field, limit=6)
    page.keyboard.press("Enter")
    expect(ask).to_be_visible()
    expect(field).to_be_focused()
    page.keyboard.type(email)
    page.keyboard.press("Enter")
    expect(dialog).to_be_hidden()
    expect(toast(page, "Kees Toets wordt op de achtergrond verwijderd.")).to_be_visible()

    def gone() -> bool:
        users = server.call(admin, "GET", "/api/v1/users", expect=200).json()
        return all(u["id"] != kees["id"] for u in users)

    wait_until(gone)


def failed_job(data_dir) -> None:
    import seed
    from app.models import Job

    now = datetime.now(UTC)
    with seed.connect(data_dir) as conn:
        conn.execute(
            Job.__table__.insert(),
            {
                "id": uuid.uuid4(),
                "type": "e2e.kapot",
                "key": f"e2e:kapot-{uuid.uuid4().hex[:6]}",
                "status": "failed",
                "payload": {},
                "attempts": 3,
                "max_attempts": 3,
                "run_after": now,
                "started_at": now,
                "finished_at": now,
                "error": "e2e: toetsenbord mislukt",
                "created_at": now,
            },
        )


def test_taken_alles_opnieuw(context_as, user_factory, seeded):
    """Taken: Shift+R, Tab naar "Alles opnieuw", Enter."""
    seeded(failed_job)
    admin = user_factory("admin", name="Toets Taken")
    page = context_as(admin).new_page()
    page.goto("/beheer/taken")
    expect(page.get_by_role("row").filter(has_text="e2e: toetsenbord mislukt")).to_have_count(1)
    start(page)
    page.keyboard.press("Shift+R")
    ask = page.get_by_role("dialog", name="Alle mislukte taken opnieuw proberen?", exact=True)
    expect(ask.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    tab_to(page, ask.get_by_role("button", name="Alles opnieuw", exact=True), limit=4)
    page.keyboard.press("Enter")
    expect(toast(page, re.compile(r"weer in de wachtrij\.$"))).to_be_visible()


def test_account_taal_met_pijltjes_tab_en_enter(context_as, server, user_factory):
    """Account: de taal kiezen met de pijltjes (dat herlaadt nog niet), Tab naar "Taal
    wijzigen", Enter."""
    user = user_factory(name="Toets Taal")
    page = context_as(user).new_page()
    page.goto("/account")
    expect(page.get_by_role("heading", name="Opslag", exact=True)).to_be_visible()
    start(page)
    language = page.get_by_label("Taal", exact=True)
    tab_to(page, language, limit=60)
    page.keyboard.press("ArrowDown")
    expect(language).to_have_value("en")
    page.wait_for_timeout(300)
    expect(page.locator("html")).to_have_attribute("lang", "nl")  # nog niet gewisseld
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Taal wijzigen", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Storage", exact=True)).to_be_visible()
    me = server.call(user, "GET", "/api/v1/auth/me", expect=200).json()
    assert me["locale"] == "en"


def test_prullenbak_terugzetten_verwijderen_en_legen(context_as, server, user_factory):
    """Prullenbak: Tab naar de lijst, ↓ en Shift+↓, Enter zet terug; Mod+A en
    Shift+Mod+Backspace verwijderen (Tab, Enter in de vraag); Shift+L leegt."""
    user = user_factory(name="Toets Prullenbak")
    for name in ["a.txt", "b.txt", "c.txt", "d.txt"]:
        node = upload(server, user, name, name.encode() * 10)
        server.call(user, "DELETE", f"/api/v1/files/{node['id']}", expect=204)
    page = context_as(user).new_page()
    page.goto("/prullenbak")
    grid = page.get_by_role("grid", name="Inhoud van de prullenbak")
    expect(grid.get_by_role("row")).to_have_count(5)
    start(page)
    tab_to(page, grid.locator('[role=row][tabindex="0"]'))
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Shift+ArrowDown")
    expect(page.get_by_text("2 geselecteerd", exact=True)).to_be_visible()
    page.keyboard.press("Enter")
    expect(toast(page, "2 items teruggezet.")).to_be_visible()
    expect(grid.get_by_role("row")).to_have_count(3)
    not_on_body(page)

    page.keyboard.press("ControlOrMeta+a")
    expect(page.get_by_text("2 geselecteerd", exact=True)).to_be_visible()
    page.keyboard.press("Shift+ControlOrMeta+Backspace")
    ask = page.get_by_role("dialog", name="2 items definitief verwijderen?", exact=True)
    expect(ask.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(ask.get_by_role("button", name="Definitief verwijderen", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(toast(page, "2 items definitief verwijderd.")).to_be_visible()
    expect(page.get_by_text("De prullenbak is leeg.", exact=True)).to_be_visible()

    for name in ["e.txt", "f.txt"]:
        node = upload(server, user, name, b"x" * 10)
        server.call(user, "DELETE", f"/api/v1/files/{node['id']}", expect=204)
    page.reload()
    expect(grid.get_by_role("row")).to_have_count(3)
    start(page)
    page.keyboard.press("Shift+L")
    ask = page.get_by_role("dialog", name="De prullenbak legen?", exact=True)
    expect(ask.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(ask.get_by_role("button", name="Prullenbak legen", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.get_by_text("De prullenbak is leeg.", exact=True)).to_be_visible()
    assert server.call(user, "GET", "/api/v1/trash", expect=200).json() == []


def test_versies_uploaden_annuleren_bekijken_en_terugzetten(context_as, server, user_factory):
    """Versies: V, U, een bestand kiezen; de focus staat dan op "Upload annuleren" (Enter
    annuleert) en daarna weer op "Nieuwe versie uploaden"; Bekijken en Terugzetten met Tab en
    Enter."""
    user = user_factory(name="Toets Versies")
    node = upload(server, user, "verslag.txt", b"eerste versie\n")
    server.call(user, "PUT", f"/api/v1/files/{node['id']}/content", content=b"tweede\n", expect=200)
    page = context_as(user).new_page()
    page.goto("/")
    start(page)
    tab_to(page, page.locator(ROW).filter(has_text="verslag.txt"))
    page.keyboard.press("v")
    dialog = page.get_by_role("dialog", name="Versies van verslag.txt", exact=True)
    new_version = dialog.get_by_role("button", name="Nieuwe versie uploaden", exact=True)
    expect(new_version).to_be_focused()

    # De eerste upload houden we tegen: dan is er tijd om te annuleren
    content = f"**/api/v1/files/{node['id']}/content"
    held = []
    page.route(content, lambda route: held.append(route))
    with page.expect_file_chooser() as chooser:
        page.keyboard.press("u")
    body = b"derde versie, langer\n" * 50
    chooser.value.set_files({"name": "verslag.txt", "mimeType": "text/plain", "buffer": body})
    expect(dialog.get_by_role("progressbar", name="Voortgang van de upload")).to_be_visible()
    cancel = dialog.get_by_role("button", name="Upload annuleren", exact=True)
    expect(cancel).to_be_focused()
    page.keyboard.press("Enter")
    expect(toast(page, "Upload geannuleerd.")).to_be_visible()
    expect(new_version).to_be_focused()
    page.unroute(content)

    with page.expect_file_chooser() as chooser:
        page.keyboard.press("Enter")  # op "Nieuwe versie uploaden"
    chooser.value.set_files({"name": "verslag.txt", "mimeType": "text/plain", "buffer": body})
    expect(toast(page, "Nieuwe versie opgeslagen; de vorige staat hieronder.")).to_be_visible()
    versions = dialog.get_by_role("list", name="Eerdere versies")
    expect(versions.get_by_role("listitem")).to_have_count(2)
    expect(new_version).to_be_focused()

    # Bekijken: de viewer bovenop de dialoog; Escape brengt je terug op dezelfde knop
    view = versions.get_by_role("button", name=re.compile(r"^Versie van .* bekijken$")).first
    tab_to(page, view)
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="verslag.txt", exact=True)
    expect(viewer.locator(".viewer-stage")).to_contain_text("tweede")
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    expect(view).to_be_focused()

    # Terugzetten (de versie "tweede"): de vraag met Tab en Enter; de focus blijft in de dialoog
    tab_to(page, versions.get_by_role("listitem").first.get_by_role("button", name="Terugzetten"))
    page.keyboard.press("Enter")
    ask = page.get_by_role("dialog", name="Deze versie terugzetten?", exact=True)
    expect(ask.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(ask.get_by_role("button", name="Terugzetten", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(toast(page, "Versie teruggezet.")).to_be_visible()
    expect(ask).to_be_hidden()
    page.wait_for_function(
        "() => !!document.activeElement && !!document.activeElement.closest('dialog[open]')",
        timeout=5000,
    )
    current = server.call(user, "GET", f"/api/v1/files/{node['id']}", expect=200).json()
    assert current["size"] == len(b"tweede\n")


def test_delen_met_alt_p_en_een_link_met_alt_l(context_as, server, user_factory):
    """Delen: S op de rij, Alt+P, typen, ↓, Enter; Alt+L, Tab naar "Link maken", Enter."""
    owner = user_factory(name="Toets Deler")
    user_factory(name="Toets Ontvanger")
    server.call(
        owner,
        "POST",
        f"/api/v1/files/{owner.root_id}/folders",
        json={"name": "Plannen"},
        expect=201,
    )
    page = context_as(owner).new_page()
    page.goto("/")
    start(page)
    tab_to(page, page.locator(ROW).filter(has_text="Plannen"))
    page.keyboard.press("s")
    dialog = page.get_by_role("dialog", name="Plannen delen", exact=True)
    expect(dialog).to_be_visible()
    page.keyboard.press("Alt+P")
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    expect(box).to_be_focused()
    page.keyboard.type("Toets Ontvanger", delay=20)
    expect(dialog.get_by_role("option", name=re.compile("Toets Ontvanger"))).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(toast(page, "Gedeeld met Toets Ontvanger.")).to_be_visible()
    expect(box).to_be_focused()

    page.keyboard.press("Alt+L")
    expect(dialog.get_by_label("Soort", exact=True)).to_be_focused()
    tab_to(page, dialog.get_by_role("button", name="Link maken", exact=True), limit=10)
    page.keyboard.press("Enter")
    expect(dialog.get_by_label("Adres van Link", exact=True)).to_have_value(re.compile(r"/s/"))
    # Het formulier is weg; de focus staat op de knop die ervoor in de plaats kwam
    expect(dialog.get_by_role("button", name="Nieuwe link", exact=True)).to_be_focused()
