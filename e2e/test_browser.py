"""De bestandsbrowser (P2): een map van 10.000, selecteren, acties, slepen, menu's en raster.

Lezen gebeurt in de grote map van seed_browser.seed_big (één keer per module); wat iets
verandert, gebeurt in een eigen kleine boom (seed_browser.small_tree) van een eigen gebruiker.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from conftest import Account, Server
from seed_browser import BigFolder, big_name, children, seed_big, small_tree

pytestmark = pytest.mark.p2

ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"  # zonder de kopregel
MOD = "ControlOrMeta"


@pytest.fixture(scope="module")
def big(server: Server, admin: Account) -> BigFolder:
    return seed_big(server, admin)


def open_folder(context: Any, folder_id: str) -> Page:
    page = context.new_page()
    page.goto(f"/map/{folder_id}")
    page.locator(ROWS).first.wait_for()
    return page


def row(page: Page, name: str) -> Any:
    return page.locator(ROWS).filter(has=page.get_by_text(name, exact=True))


def pick(page: Page, name: str, modifiers: list[str] | None = None) -> None:
    """Een rij selecteren met een klik naast de naam (de naam zelf opent)."""
    row(page, name).locator(".cell-date").click(modifiers=modifiers or [])


def active_row_index(page: Page) -> int | None:
    return page.evaluate(
        """() => {
          const row = document.activeElement && document.activeElement.closest("[role=row]");
          return row ? Number(row.getAttribute("aria-rowindex")) : null;
        }"""
    )


def wait_complete(page: Page) -> None:
    """Tot de hele map geladen is (de voortgang "… van … geladen" is weg)."""
    expect(page.get_by_text(re.compile(r"geladen…$"))).to_have_count(0, timeout=30_000)


# --- de grote map (alleen lezen) ---


def test_tienduizend_openen_scrollen_en_selecteren(
    big: BigFolder, context_as: Callable[..., Any]
) -> None:
    page = open_folder(context_as(big.owner), big.folder_id)
    grid = page.locator("#inhoud [role=grid][aria-rowcount]")
    expect(grid).to_have_attribute("aria-rowcount", str(big.n + 1))
    assert page.locator(ROWS).count() <= 200
    # Mappen eerst, dan op naam
    expect(row(page, "Map 00000")).to_be_visible()

    # Naar het eind met het toetsenbord
    pick(page, "Map 00000")
    page.keyboard.press("End")
    last = page.locator(f'{ROWS}[aria-rowindex="{big.n + 1}"]')
    expect(last).to_be_visible()
    expect(last).to_contain_text("foto-09999.webp", timeout=30_000)
    assert active_row_index(page) == big.n + 1
    assert page.locator(ROWS).count() <= 200
    page.keyboard.press("Home")
    expect(row(page, "Map 00000")).to_be_focused()

    # Een reeks met Shift+klik
    pick(page, "Map 00020")
    pick(page, "Map 00080", ["Shift"])
    expect(page.locator(f"{ROWS}[aria-selected=true]")).to_have_count(4)
    count = page.get_by_role("toolbar", name="Selectie").locator(".selection-count")
    expect(count).to_have_text("4 geselecteerd")

    # Alles (als alles geladen is)
    wait_complete(page)
    page.keyboard.press(f"{MOD}+a")
    expect(count).to_have_text("10.000 geselecteerd")
    page.keyboard.press("Escape")
    expect(count).to_have_count(0)


def test_wiel_en_pijl_omlaag_houden_de_focus_op_de_rij(
    big: BigFolder, context_as: Callable[..., Any]
) -> None:
    page = open_folder(context_as(big.owner), big.folder_id)
    pick(page, "Map 00000")
    assert active_row_index(page) == 2
    page.mouse.move(640, 400)
    for _ in range(8):
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(50)
    page.wait_for_timeout(300)
    # De rij met de focus staat er nog (buiten beeld), en ArrowDown gaat naar de volgende
    page.keyboard.press("ArrowDown")
    assert active_row_index(page) == 3
    focused = page.locator(f'{ROWS}[aria-rowindex="3"]')
    expect(focused).to_be_focused()
    expect(focused).to_be_in_viewport()
    expect(focused).to_have_attribute("tabindex", "0")


def test_raster_met_g(big: BigFolder, context_as: Callable[..., Any]) -> None:
    page = open_folder(context_as(big.owner), big.folder_id)
    page.keyboard.press("g")
    grid = page.locator("#inhoud [data-view=grid]")
    expect(grid).to_be_visible()
    # Eerst 500 mappen (zonder thumbnail); aan het eind de bestanden
    grid.locator('[role=gridcell][tabindex="0"]').focus()
    page.keyboard.press("End")
    expect(grid.locator("img[src*='/api/v1/previews/']").first).to_be_visible()
    assert page.locator("#inhoud [role=gridcell][data-index]").count() <= 200
    # De knop zegt wat er aan staat
    expect(page.get_by_role("button", name="Raster", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    page.keyboard.press("g")
    expect(page.locator("#inhoud [data-view=list]")).to_be_visible()
    # Onthouden na herladen
    page.keyboard.press("g")
    page.reload()
    expect(page.locator("#inhoud [data-view=grid]")).to_be_visible()


def test_contextmenu_rechtsklik_shift_f10_en_lang_indrukken(
    big: BigFolder, context_as: Callable[..., Any]
) -> None:
    page = open_folder(context_as(big.owner), big.folder_id)
    name = big_name(20)  # Map 00020 (bovenaan staan de mappen)
    target = row(page, name)
    target.locator(".cell-date").click(button="right")
    menu = page.get_by_role("menu", name=f"Acties voor {name}")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem", name="Downloaden als zip", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect(target).to_be_focused()

    page.keyboard.press("Shift+F10")
    expect(menu).to_be_visible()
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)

    # Lang indrukken op een touchscreen
    touch = open_folder(context_as(big.owner, has_touch=True), big.folder_id)
    box = row(touch, name).locator(".cell-date").bounding_box()
    assert box
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    cdp = touch.context.new_cdp_session(touch)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    touch.wait_for_timeout(800)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    expect(touch.get_by_role("menu", name=f"Acties voor {name}")).to_be_visible()


# --- een kleine boom (acties die iets veranderen) ---


def test_prullenbak_met_delete_en_mod_backspace_en_ongedaan_maken(
    server: Server, user_factory: Callable[..., Account], context_as: Callable[..., Any]
) -> None:
    user = user_factory()
    tree = small_tree(server, user)
    page = open_folder(context_as(user), tree.work)

    pick(page, "a.txt")
    page.keyboard.press("Delete")
    expect(row(page, "a.txt")).to_have_count(0)
    toast = page.locator(".toast").filter(has_text="a.txt staat in de prullenbak")
    expect(toast).to_be_visible()
    toast.get_by_role("button", name="Ongedaan maken", exact=True).click()
    expect(row(page, "a.txt")).to_be_visible()
    assert "a.txt" in children(server, user, tree.work)

    pick(page, "b.txt")
    page.keyboard.press(f"{MOD}+Backspace")
    expect(row(page, "b.txt")).to_have_count(0)
    assert "b.txt" not in children(server, user, tree.work)


def test_hernoemen_met_f2_en_nieuwe_map_met_n(
    server: Server, user_factory: Callable[..., Account], context_as: Callable[..., Any]
) -> None:
    user = user_factory()
    tree = small_tree(server, user)
    page = open_folder(context_as(user), tree.work)

    pick(page, "c.txt")
    page.keyboard.press("F2")
    dialog = page.get_by_role("dialog", name="Naam wijzigen")
    field = dialog.get_by_label("Nieuwe naam")
    expect(field).to_have_value("c.txt")
    # Alleen de naam is geselecteerd, niet de extensie
    assert field.evaluate("(el) => [el.selectionStart, el.selectionEnd]") == [0, 1]
    page.keyboard.type("hernoemd")
    page.keyboard.press("Enter")
    expect(row(page, "hernoemd.txt")).to_be_visible()
    assert "hernoemd.txt" in children(server, user, tree.work)

    page.keyboard.press("n")
    dialog = page.get_by_role("dialog", name="Nieuwe map")
    dialog.get_by_label("Naam van de map").fill("Nieuw")
    page.keyboard.press("Enter")
    expect(row(page, "Nieuw")).to_be_visible()
    expect(row(page, "Nieuw")).to_have_attribute("aria-selected", "true")
    assert "Nieuw" in children(server, user, tree.work)


def test_knippen_plakken_en_kopieren(
    server: Server, user_factory: Callable[..., Account], context_as: Callable[..., Any]
) -> None:
    user = user_factory()
    tree = small_tree(server, user)
    page = open_folder(context_as(user), tree.work)

    pick(page, "a.txt")
    page.keyboard.press(f"{MOD}+x")
    expect(row(page, "a.txt")).to_have_class(re.compile(r"\bcut\b"))
    row(page, "Doel").get_by_text("Doel", exact=True).click()
    expect(page).to_have_url(re.compile(f"/map/{tree.target}"))
    expect(row(page, "Sub")).to_be_visible()
    page.keyboard.press(f"{MOD}+v")
    expect(row(page, "a.txt")).to_be_visible()
    assert "a.txt" not in children(server, user, tree.work)
    assert "a.txt" in children(server, user, tree.target)

    page.go_back()
    expect(row(page, "b.txt")).to_be_visible()
    expect(row(page, "a.txt")).to_have_count(0)
    pick(page, "b.txt")
    page.keyboard.press(f"{MOD}+c")
    row(page, "Doel").get_by_text("Doel", exact=True).click()
    expect(row(page, "Sub")).to_be_visible()
    page.keyboard.press(f"{MOD}+v")
    expect(row(page, "b.txt")).to_be_visible()
    assert "b.txt" in children(server, user, tree.work)
    assert "b.txt" in children(server, user, tree.target)


def test_zip_downloaden(
    server: Server, user_factory: Callable[..., Account], context_as: Callable[..., Any]
) -> None:
    user = user_factory()
    tree = small_tree(server, user)
    page = open_folder(context_as(user, accept_downloads=True), tree.work)
    pick(page, "a.txt")
    pick(page, "b.txt", [MOD])
    with page.expect_download() as info:
        page.keyboard.press("d")
    data = info.value.path().read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["a.txt", "b.txt"]
        assert z.read("a.txt") == b"inhoud van a.txt\n"


def test_slepen_naar_een_map_en_naar_het_pad(
    server: Server, user_factory: Callable[..., Account], context_as: Callable[..., Any]
) -> None:
    user = user_factory()
    tree = small_tree(server, user)
    page = open_folder(context_as(user), tree.work)

    row(page, "a.txt").drag_to(row(page, "Doel"), timeout=15_000)
    expect(row(page, "a.txt")).to_have_count(0)
    assert "a.txt" in children(server, user, tree.target)

    # In Doel: terug naar Werk via het pad bovenaan
    page.goto(f"/map/{tree.target}")
    expect(row(page, "a.txt")).to_be_visible()
    crumb = page.get_by_role("navigation", name="Pad").get_by_role(
        "link", name=tree.name, exact=True
    )
    row(page, "a.txt").drag_to(crumb, timeout=15_000)
    expect(row(page, "a.txt")).to_have_count(0)
    assert "a.txt" in children(server, user, tree.work)


def test_delen_op_een_selectie_opent_de_deeldialoog(
    server: Server, user_factory: Callable[..., Account], context_as: Callable[..., Any]
) -> None:
    user = user_factory()
    tree = small_tree(server, user)
    page = open_folder(context_as(user), tree.work)
    pick(page, "a.txt")
    bar = page.get_by_role("toolbar", name="Selectie")
    bar.get_by_role("button", name="Delen…", exact=True).click()
    expect(page.get_by_role("dialog", name='"a.txt" delen')).to_be_visible()
