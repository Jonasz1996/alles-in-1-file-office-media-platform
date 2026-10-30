"""De bestandsbrowser (P2) alleen met het toetsenbord (docs/fase-3/toetsenbord-p2.md).

Eén rondje in een kleine boom (seed_browser.small_tree): de lijst in, pijltjes, Shift+End,
Mod+A, Escape, Spatie, ?, F2, N, Mod+X/Mod+V, Mod+C, Delete met Mod+Z, Mod+Backspace,
Shift+F10, G en Mod+F. Alleen het binnenkomen in de lijst gaat met focus() (één tabstop; hoeveel
keer Tab daarvoor nodig is, hangt van de schil af).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from conftest import Account, Server
from seed_browser import children, small_tree

pytestmark = pytest.mark.p2

ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"  # zonder de kopregel
MOD = "ControlOrMeta"


def row(page: Page, name: str) -> Any:
    return page.locator(ROWS).filter(has=page.get_by_text(name, exact=True))


def expect_focus(page: Page, name: str) -> None:
    """De focus staat (zo meteen: hij verhuist in het volgende frame) op de rij met deze naam."""
    page.wait_for_function(
        """(name) => {
          const row = document.activeElement && document.activeElement.closest("[role=row]");
          const el = row && row.querySelector(".name");
          return !!el && el.textContent === name;
        }""",
        arg=name,
        timeout=5000,
    )


def selected(page: Page) -> Any:
    return page.locator(f"{ROWS}[aria-selected=true]")


def enter_list(page: Page) -> None:
    page.locator(f'{ROWS}[tabindex="0"]').focus()


def test_rondje_met_alleen_het_toetsenbord(
    server: Server, user_factory: Callable[..., Account], context_as: Callable[..., Any]
) -> None:
    user = user_factory()
    tree = small_tree(server, user)
    page = context_as(user).new_page()
    page.goto(f"/map/{tree.work}")
    page.locator(ROWS).first.wait_for()
    enter_list(page)
    expect_focus(page, "Archief")  # mappen eerst

    # Pijltjes selecteren, Shift+End een reeks, Mod+A alles, Escape niets
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    expect_focus(page, "a.txt")
    expect(selected(page)).to_have_count(1)
    page.keyboard.press("Shift+End")
    expect(selected(page)).to_have_count(3)
    expect_focus(page, "c.txt")
    page.keyboard.press("Escape")
    expect(selected(page)).to_have_count(0)
    page.keyboard.press(f"{MOD}+a")
    expect(selected(page)).to_have_count(5)
    page.keyboard.press("Escape")
    expect(selected(page)).to_have_count(0)

    # Spatie zet de rij met de focus erbij en eraf
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press(" ")
    expect(selected(page)).to_have_count(0)
    page.keyboard.press(" ")
    expect(row(page, "a.txt")).to_have_attribute("aria-selected", "true")

    # ? toont de acties die nu kunnen
    page.keyboard.press("?")
    help_ = page.get_by_role("dialog", name="Sneltoetsen")
    expect(help_).to_be_visible()
    for label in (
        "Naam wijzigen",
        "Knippen",
        "Kopiëren",
        "Verplaatsen naar…",
        "Delen…",
        "Naar prullenbak",
    ):
        expect(help_.get_by_text(label, exact=True).first).to_be_visible()
    page.keyboard.press("Escape")
    expect(help_).to_have_count(0)

    # F2: hernoemen
    page.keyboard.press("F2")
    dialog = page.get_by_role("dialog", name="Naam wijzigen")
    expect(dialog).to_be_visible()
    page.keyboard.type("x")
    page.keyboard.press("Enter")
    expect(row(page, "x.txt")).to_be_visible()
    expect(dialog).to_have_count(0)

    # Mod+X, de map Doel in met Enter, Mod+V
    enter_list(page)
    expect_focus(page, "x.txt")
    page.keyboard.press(f"{MOD}+x")
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    expect_focus(page, "Doel")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(f"/map/{tree.target}"))
    expect(row(page, "Sub")).to_be_visible()
    page.keyboard.press(f"{MOD}+v")
    expect(row(page, "x.txt")).to_be_visible()
    assert "x.txt" in children(server, user, tree.target)
    assert "x.txt" not in children(server, user, tree.work)

    # Delete en Mod+Z (ongedaan maken), daarna Mod+Backspace
    enter_list(page)
    page.keyboard.press("End")
    expect_focus(page, "x.txt")
    page.keyboard.press("Delete")
    expect(row(page, "x.txt")).to_have_count(0)
    page.keyboard.press(f"{MOD}+z")
    expect(row(page, "x.txt")).to_be_visible()
    enter_list(page)
    page.keyboard.press("End")
    page.keyboard.press(f"{MOD}+Backspace")
    expect(row(page, "x.txt")).to_have_count(0)
    assert "x.txt" not in children(server, user, tree.target)

    # Terug; Mod+C en in Doel Mod+V: een kopie
    page.go_back()
    expect(row(page, "b.txt")).to_be_visible()
    enter_list(page)
    page.keyboard.press("End")
    page.keyboard.press("ArrowUp")
    expect_focus(page, "b.txt")
    page.keyboard.press(f"{MOD}+c")
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(row(page, "Sub")).to_be_visible()
    page.keyboard.press(f"{MOD}+v")
    expect(row(page, "b.txt")).to_be_visible()
    assert "b.txt" in children(server, user, tree.work)

    # Shift+F10: het menu bij de rij met de focus; Escape: terug naar de rij
    enter_list(page)
    page.keyboard.press("End")
    page.keyboard.press("Shift+F10")
    menu = page.get_by_role("menu", name="Acties voor b.txt")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect_focus(page, "b.txt")

    # G: raster en terug
    page.keyboard.press("g")
    expect(page.locator("#inhoud [data-view=grid]")).to_be_visible()
    page.keyboard.press("g")
    expect(page.locator("#inhoud [data-view=list]")).to_be_visible()

    # Mod+F: het filter
    page.keyboard.press(f"{MOD}+f")
    expect(page.get_by_label("Filter in deze map")).to_be_focused()
    page.keyboard.type("su")
    expect(page.locator(ROWS)).to_have_count(1)
    expect(row(page, "Sub")).to_be_visible()
    page.keyboard.press(f"{MOD}+a")  # in het veld: de tekst, niet de rijen
    page.keyboard.press("Backspace")
    expect(page.locator(ROWS)).to_have_count(2)

    # N: een nieuwe map (vanuit de lijst)
    enter_list(page)
    page.keyboard.press("n")
    dialog = page.get_by_role("dialog", name="Nieuwe map")
    expect(dialog.get_by_label("Naam van de map")).to_be_focused()
    page.keyboard.type("Klaar")
    page.keyboard.press("Enter")
    expect(row(page, "Klaar")).to_be_visible()
    assert "Klaar" in children(server, user, tree.target)
