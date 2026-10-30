"""De bestandsbrowser (P2): een map van 10.000, selecteren, acties, slepen, menu's en raster.

Lezen gebeurt in de grote map van seed_browser.seed_big (één keer per module); wat iets
verandert, gebeurt in een eigen kleine boom (seed_browser.small_tree) van een eigen gebruiker.
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, expect

from conftest import Account, Server, create_user
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


VISIBLE_PHOTOS = """() => {
  const out = [];
  for (const row of document.querySelectorAll("#inhoud [role=grid] [role=row][data-index]")) {
    const box = row.getBoundingClientRect();
    if (box.bottom <= 0 || box.top >= innerHeight) continue;
    const name = row.querySelector(".name")?.textContent ?? "";
    if (!name.startsWith("foto-")) continue;
    const thumb = row.querySelector(".thumb");
    out.push([name, !!thumb && thumb.tagName === "IMG" && !!thumb.getAttribute("src")]);
  }
  return out;
}"""


def photos_with_thumbnail(page: Page, at_least: int) -> list[str]:
    """Wacht tot elke foto die in beeld is zijn thumbnail heeft (en dat het er minstens zoveel
    zijn); geeft hun namen."""
    end = time.monotonic() + 15
    while True:
        shown = page.evaluate(VISIBLE_PHOTOS)
        if len(shown) >= at_least and all(ok for _, ok in shown):
            return [name for name, _ in shown]
        if time.monotonic() > end:
            raise AssertionError(f"foto's zonder thumbnail: {shown}")
        page.wait_for_timeout(200)


def test_thumbnails_na_eigen_sortering_en_een_tweede_zoekopdracht(
    server: Server, admin: Account, context_as: Callable[..., Any]
) -> None:
    """Previews worden per blok gevraagd. Ook als de nodes in dat blok veranderen zonder dat het
    blok of het aantal verandert, komen hun thumbnails: na een eigen sortering (wat bovenaan
    komt, lag buiten het eerste blok) en na een tweede zoekopdracht met evenveel resultaten.
    Anders bleef zo'n foto een leeg vlak, zonder thumbnail en zonder icoon."""
    import seed

    owner = create_user(server, admin, name="Sorteerder")

    def fill(data_dir: Path) -> str:
        shas = seed.make_blobs(data_dir, 50)
        return seed.seed_folder(data_dir, owner, 1000, shas, name="Duizend")

    folder = server.seeded(fill)
    page = open_folder(context_as(owner), folder)
    wait_complete(page)

    # Op naam aflopend (in de browser: de hele map is er): de mappen eerst, dan foto-00999 …;
    # die stonden in de volgorde van de server achteraan
    page.locator("#inhoud .file-head .cell-name .sort-button").click()
    expect(page.locator("#inhoud .file-head .cell-name")).to_have_attribute(
        "aria-sort", "descending"
    )
    page.mouse.move(640, 500)
    for _ in range(6):
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(100)
    expect(row(page, "foto-00999.webp")).to_be_in_viewport()
    assert "foto-00997.webp" in photos_with_thumbnail(page, 2)

    # Twee zoekopdrachten met elk vijf resultaten
    page.goto("/zoeken?q=foto-0001")
    expect(row(page, "foto-00011.webp")).to_be_visible()
    assert sorted(photos_with_thumbnail(page, 5)) == [f"foto-0001{i}.webp" for i in (1, 3, 5, 7, 9)]
    box = page.get_by_role("searchbox", name="Zoekwoorden")
    box.fill("foto-0002")
    box.press("Enter")
    expect(row(page, "foto-00021.webp")).to_be_visible()
    assert sorted(photos_with_thumbnail(page, 5)) == [f"foto-0002{i}.webp" for i in (1, 3, 5, 7, 9)]


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
    expect(page.get_by_role("dialog", name="a.txt delen", exact=True)).to_be_visible()


DROP_FILE_JS = """
(name) => {
  // Een bestand van buiten de browser loslaten op de pagina (synthetische DataTransfer)
  const dt = new DataTransfer();
  dt.items.add(new File(["gesleept"], name, { type: "text/plain" }));
  const target = document.querySelector("#inhoud [role=grid]");
  for (const type of ["dragenter", "dragover", "drop"]) {
    const init = { bubbles: true, cancelable: true, dataTransfer: dt };
    target.dispatchEvent(new DragEvent(type, init));
  }
  return dt.types.includes("Files");
}
"""


def test_uploaden_met_knop_mapknop_en_loslaten_komt_in_de_lijst(
    server: Server,
    user_factory: Callable[..., Account],
    context_as: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """Uploaden vanuit de bestandsbrowser (P3 in P2): wat klaar is, staat zonder herladen in de
    lijst, en de lade (niet de pagina) toont de uploads."""
    user = user_factory()
    tree = small_tree(server, user)
    page = open_folder(context_as(user), tree.work)

    # De knop Uploaden
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files([{"name": "los.txt", "mimeType": "text/plain", "buffer": b"los"}])
    expect(row(page, "los.txt")).to_be_visible()

    # De knop Map uploaden
    root = tmp_path / "Reis"
    (root / "Dag 1").mkdir(parents=True)
    (root / "plan.txt").write_text("plan")
    (root / "Dag 1" / "foto.txt").write_text("foto")
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Map uploaden", exact=True).click()
    assert chooser.value.element.get_attribute("webkitdirectory") is not None
    chooser.value.set_files(root)
    expect(row(page, "Reis")).to_be_visible()
    lade = page.get_by_role("region", name="Uploads", exact=True)
    expect(lade).to_have_count(0, timeout=30_000)
    names = children(server, user, tree.work)
    assert "Reis" in names and "los.txt" in names

    # Loslaten van buiten de browser, op de lijst
    assert page.evaluate(DROP_FILE_JS, "gesleept.txt") is True
    expect(row(page, "gesleept.txt")).to_be_visible()
    expect(lade).to_have_count(0, timeout=30_000)
    assert "gesleept.txt" in children(server, user, tree.work)
    assert page.locator("#inhoud .upload-name, #inhoud ul.uploads").count() == 0

    # In de nieuwe map staat de inhoud, ook als lijst
    row(page, "Reis").get_by_text("Reis", exact=True).click()
    expect(row(page, "Dag 1")).to_be_visible()
    expect(row(page, "plan.txt")).to_be_visible()


def test_duizend_uploaden_geeft_een_children_verzoek_en_geen_eigen_lijst(
    server: Server,
    user_factory: Callable[..., Account],
    context_as: Callable[..., Any],
    tmp_path: Path,
) -> None:
    """Klaar-als van P2: 1.000 bestanden uploaden in de open map geeft één children-verzoek
    (plus pagina's op de achtergrond); wat binnenkomt, komt via lookup in de lijst. De
    bestandsbrowser toont zelf geen lijst van uploads: dat doet de lade (P3)."""
    n = 1000
    user = user_factory()
    tree = small_tree(server, user)
    names = [f"f-{i:04d}.txt" for i in range(n)]
    for name in names:
        (tmp_path / name).write_text(name)

    context = context_as(user)
    page = context.new_page()
    first: list[str] = []  # children zonder after: het begin van een (her)laadronde
    pages: list[str] = []  # children met after: pagina's op de achtergrond
    lookups: list[str] = []
    prefix = f"/api/v1/files/{tree.work}/children"

    def seen(request: Any) -> None:
        url = urlparse(request.url)
        if request.method == "GET" and url.path == prefix:
            (pages if "after" in parse_qs(url.query) else first).append(request.url)
        elif request.method == "POST" and url.path == "/api/v1/files/lookup":
            lookups.append(request.url)

    page.on("request", seen)
    page.goto(f"/map/{tree.work}")
    expect(row(page, "a.txt")).to_be_visible()
    assert len(first) == 1

    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Uploaden", exact=True).click()
    chooser.value.set_files([tmp_path / name for name in names])
    started = time.monotonic()

    # De lade toont de uploads; de bestandsbrowser zelf niet (geen eigen lijst in #inhoud)
    lade = page.get_by_role("region", name="Uploads", exact=True)
    expect(lade).to_be_visible()
    assert page.locator("#inhoud .upload-name, #inhoud ul.uploads").count() == 0

    grid = page.locator("#inhoud [role=grid][aria-rowcount]")
    total = 5 + n  # Doel, Archief, a.txt, b.txt, c.txt en de nieuwe
    expect(grid).to_have_attribute("aria-rowcount", str(total + 1), timeout=180_000)
    expect(lade).to_have_count(0, timeout=60_000)
    assert len(children(server, user, tree.work)) == total
    expect(row(page, "f-0000.txt")).to_be_visible()
    assert page.locator(ROWS).count() <= 200
    assert page.locator("#inhoud .upload-name, #inhoud ul.uploads").count() == 0

    # Nog even wachten op late gebeurtenissen: ook die mogen geen herlaadronde geven
    time.sleep(1)
    elapsed = time.monotonic() - started
    assert len(first) == 1, f"{len(first)} children-verzoeken zonder after: {first}"
    # Gebeurtenissen worden 150 ms verzameld voor één lookup: hooguit één lookup per 150 ms
    # (plus wat speling voor een lookup die al liep), niet één per bestand
    assert 0 < len(lookups) <= elapsed / 0.15 + 10, f"{len(lookups)} lookups in {elapsed:.1f} s"
    print(f"\nchildren: 1 + {len(pages)} achtergrond, lookups: {len(lookups)} in {elapsed:.1f} s")
