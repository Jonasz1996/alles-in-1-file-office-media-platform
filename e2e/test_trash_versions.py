"""Prullenbak en versies in de browser (P6): selecteren en terugzetten met het toetsenbord,
definitief verwijderen met Shift+Mod+Backspace, de prullenbak legen; een nieuwe versie uploaden
met voortgang en annuleren, een versie bekijken en terugzetten."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.p6


def upload(server, account, name: str, data: bytes) -> dict:
    return server.call(
        account,
        "PUT",
        f"/api/v1/files/{account.root_id}/upload",
        params={"name": name},
        content=data,
        expect=201,
    ).json()


def toast(page, text: str):
    """Een melding (toast) rechtsonder; de tekst staat ook in de live region voor schermlezers."""
    return page.get_by_role("region", name="Meldingen").get_by_text(text, exact=True)


def trash_names(server, account) -> list[str]:
    return sorted(
        n["name"] for n in server.call(account, "GET", "/api/v1/trash", expect=200).json()
    )


def test_prullenbak_selecteren_terugzetten_verwijderen_legen(context_as, server, user_factory):
    user = user_factory(name="Prullenbak Test")
    ids = {}
    for name in ["a.txt", "b.txt", "c.txt", "d.txt"]:
        ids[name] = upload(server, user, name, name.encode() * 100)["id"]
        server.call(user, "DELETE", f"/api/v1/files/{ids[name]}", expect=204)

    page = context_as(user).new_page()
    page.goto("/prullenbak")
    grid = page.get_by_role("grid", name="Inhoud van de prullenbak")
    expect(grid.get_by_role("row")).to_have_count(5)  # kopregel + 4
    expect(page.get_by_text("4 items, samen", exact=False)).to_be_visible()
    expect(grid.get_by_role("columnheader", name="Was in", exact=True)).to_be_visible()

    # Klik op de eerste rij, Shift+↓ erbij: twee geselecteerd; Enter zet ze terug
    rows = grid.get_by_role("row")
    rows.nth(1).click()
    page.keyboard.press("Shift+ArrowDown")
    expect(page.get_by_text("2 geselecteerd", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Terugzetten (2)", exact=True)).to_be_visible()
    page.keyboard.press("Enter")
    expect(toast(page, "2 items teruggezet.")).to_be_visible()
    expect(grid.get_by_role("row")).to_have_count(3)
    assert len(trash_names(server, user)) == 2

    # Mod+A en Shift+Mod+Backspace: na de vraag definitief weg
    grid.get_by_role("row").nth(1).click()
    page.keyboard.press("ControlOrMeta+a")
    expect(page.get_by_text("2 geselecteerd", exact=True)).to_be_visible()
    page.keyboard.press("Shift+ControlOrMeta+Backspace")
    ask = page.get_by_role("dialog", name="2 items definitief verwijderen?", exact=True)
    expect(ask).to_be_visible()
    ask.get_by_role("button", name="Definitief verwijderen", exact=True).click()
    expect(toast(page, "2 items definitief verwijderd.")).to_be_visible()
    expect(page.get_by_text("De prullenbak is leeg.", exact=True)).to_be_visible()
    assert trash_names(server, user) == []

    # Legen: twee nieuwe in de prullenbak, Shift+L, bevestigen
    for name in ["e.txt", "f.txt"]:
        node = upload(server, user, name, b"x" * 10)
        server.call(user, "DELETE", f"/api/v1/files/{node['id']}", expect=204)
    page.reload()
    expect(grid.get_by_role("row")).to_have_count(3)
    page.keyboard.press("Shift+L")
    ask = page.get_by_role("dialog", name="De prullenbak legen?", exact=True)
    expect(ask).to_be_visible()
    ask.get_by_role("button", name="Prullenbak legen", exact=True).click()
    expect(page.get_by_text("De prullenbak is leeg.", exact=True)).to_be_visible()
    assert trash_names(server, user) == []


def open_versions(page, name: str):
    """Versies… van een bestand in Bestanden: via het menu Meer… (P0) of het contextmenu (P2)."""
    more = page.get_by_role("combobox", name=f"Meer acties voor {name}", exact=True)
    if more.count():
        more.select_option(label="Versies…")
    else:
        page.locator("#inhoud").get_by_text(name).first.click(button="right")
        page.get_by_role("menuitem", name=re.compile(r"^Versies")).click()
    dialog = page.get_by_role("dialog", name=f"Versies van {name}", exact=True)
    expect(dialog).to_be_visible()
    return dialog


def test_versie_uploaden_bekijken_terugzetten(context_as, server, user_factory):
    user = user_factory(name="Versie Test")
    node = upload(server, user, "verslag.txt", b"eerste versie\n")
    server.call(user, "PUT", f"/api/v1/files/{node['id']}/content", content=b"tweede\n", expect=200)

    page = context_as(user).new_page()
    page.goto("/")
    expect(page.locator("#inhoud").get_by_text("verslag.txt").first).to_be_visible()
    dialog = open_versions(page, "verslag.txt")
    versions = dialog.get_by_role("list", name="Eerdere versies")
    expect(versions.get_by_role("listitem")).to_have_count(1)

    # U: uploaden. De eerste houden we tegen: voortgang en Annuleren
    held = []
    page.route(f"**/api/v1/files/{node['id']}/content", lambda route: held.append(route))
    with page.expect_file_chooser() as chooser:
        page.keyboard.press("u")
    chooser.value.set_files(
        {"name": "verslag.txt", "mimeType": "text/plain", "buffer": b"derde versie, langer\n" * 50}
    )
    expect(dialog.get_by_role("progressbar", name="Voortgang van de upload")).to_be_visible()
    dialog.get_by_role("button", name="Upload annuleren", exact=True).click()
    expect(toast(page, "Upload geannuleerd.")).to_be_visible()
    expect(dialog.get_by_role("progressbar")).to_have_count(0)
    # De browser brak het verzoek zelf af (XHR abort): de route is dan al afgehandeld
    page.unroute(f"**/api/v1/files/{node['id']}/content")
    assert len(held) == 1

    # Opnieuw, nu echt
    with page.expect_file_chooser() as chooser:
        dialog.get_by_role("button", name="Nieuwe versie uploaden", exact=True).click()
    chooser.value.set_files(
        {"name": "verslag.txt", "mimeType": "text/plain", "buffer": b"derde versie, langer\n" * 50}
    )
    expect(toast(page, "Nieuwe versie opgeslagen; de vorige staat hieronder.")).to_be_visible()
    expect(versions.get_by_role("listitem")).to_have_count(2)
    current = server.call(user, "GET", f"/api/v1/files/{node['id']}", expect=200).json()
    assert current["size"] == len(b"derde versie, langer\n" * 50)

    # Bekijken opent de viewer (bovenop de dialoog); Sluiten brengt je terug
    versions.get_by_role("button", name=re.compile(r"^Versie van .* bekijken$")).first.click()
    viewer = page.get_by_role("dialog", name="verslag.txt", exact=True)
    expect(viewer).to_be_visible()
    viewer.get_by_role("button", name="Sluiten", exact=True).first.click()
    expect(viewer).to_be_hidden()
    expect(dialog).to_be_visible()

    # Terugzetten van de oudste (de eerste inhoud) na een vraag
    versions.get_by_role("listitem").last.get_by_role("button", name="Terugzetten").click()
    ask = page.get_by_role("dialog", name="Deze versie terugzetten?", exact=True)
    ask.get_by_role("button", name="Terugzetten", exact=True).click()
    expect(toast(page, "Versie teruggezet.")).to_be_visible()
    restored = server.call(user, "GET", f"/api/v1/files/{node['id']}", expect=200).json()
    assert restored["size"] == len(b"eerste versie\n")
