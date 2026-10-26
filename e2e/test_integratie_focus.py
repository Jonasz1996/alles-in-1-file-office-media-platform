"""Integratie (fase 3, ontwerp §5.4): wat pas tussen de pakketten zichtbaar wordt. Vooral de
focus met alleen het toetsenbord (een knop die verdwijnt, een lijst die opnieuw laadt), de hulp
(?) in een venster en op een publieke link, de pijltjes in de viewer bij tekst die opzij
scrollt, uitloggen en weer inloggen in hetzelfde tabblad, en de vraag voor het uitloggen van
een ander apparaat."""

from __future__ import annotations

import re
import time
import uuid

from playwright.sync_api import Page, expect

from conftest import Account
from seed_browser import mkdir, small_tree, upload

ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"
MOD = "ControlOrMeta"


def row(page: Page, name: str):
    return page.locator(ROWS).filter(has=page.get_by_text(name, exact=True))


def enter_list(page: Page) -> None:
    page.locator(f'{ROWS}[tabindex="0"]').focus()


def expect_focus(page: Page, name: str) -> None:
    """De focus staat (zo meteen) op de rij met deze naam."""
    page.wait_for_function(
        """(name) => {
          const row = document.activeElement && document.activeElement.closest("[role=row]");
          const el = row && row.querySelector(".name");
          return !!el && el.textContent === name;
        }""",
        arg=name,
        timeout=5000,
    )


def toasts(page: Page):
    return page.get_by_role("region", name="Meldingen")


def wait_for_previews(server, owner: Account, ids: list[str], seconds: float = 120) -> None:
    """Tot elke preview klaar is (of mislukt), zoals in test_p4_viewer."""
    deadline = time.monotonic() + seconds
    while True:
        got = server.call(owner, "POST", "/api/v1/previews", json={"node_ids": ids}, expect=200)
        previews = got.json()
        if all(previews.get(i, {}).get("state") not in (None, "pending") for i in ids):
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"previews niet klaar: {previews}")
        time.sleep(0.5)


def login_via_form(page: Page, account: Account) -> None:
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    page.get_by_label("E-mailadres", exact=True).fill(account.email)
    page.get_by_label("Wachtwoord", exact=True).fill(account.password)
    page.get_by_role("button", name="Inloggen", exact=True).click()


def test_focus_na_de_selectiebalk_en_in_de_mapkiezer(server, user_factory, context_as):
    user = user_factory(name="Focus Volger")
    tree = small_tree(server, user)
    page = context_as(user).new_page()
    page.goto(f"/map/{tree.work}")
    page.locator(ROWS).first.wait_for()

    # Selectie opheffen met de knop in de balk: de balk verdwijnt, de focus gaat terug naar
    # de rij (niet naar <body>), en de pijltjes werken meteen weer
    enter_list(page)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    expect_focus(page, "a.txt")  # de pijltjes selecteren mee
    bar = page.get_by_role("toolbar", name="Selectie", exact=True)
    bar.get_by_role("button", name="Selectie opheffen", exact=True).focus()
    page.keyboard.press("Enter")
    expect(bar).to_have_count(0)
    expect_focus(page, "a.txt")
    page.keyboard.press("ArrowDown")
    expect_focus(page, "b.txt")

    # Verplaatsen naar… (M): de focus op de eerste map zodra die er is; Enter opent een map en
    # de focus staat op de eerste map daarin, zodat ↓ en Enter weer werken
    page.keyboard.press("ArrowUp")
    page.keyboard.press("m")
    picker = page.get_by_role("dialog", name="a.txt verplaatsen naar", exact=True)
    folders = picker.get_by_role("list", name=re.compile("."))
    expect(folders.get_by_role("button", name="Archief", exact=True)).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(folders.get_by_role("button", name="Doel", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(picker.get_by_role("button", name="Sub", exact=True)).to_be_focused()

    # ? werkt ook in een venster: één hulp, en daarna weer terug in de kiezer
    page.keyboard.press("?")
    help_ = page.get_by_role("dialog", name="Sneltoetsen", exact=True)
    expect(help_).to_have_count(1)
    expect(help_.get_by_role("heading", name="In een venster", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(help_).to_have_count(0)
    expect(picker.get_by_role("button", name="Sub", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(picker).to_have_count(0)


def test_hulp_in_de_deeldialoog_toont_zijn_sneltoetsen(server, user_factory, context_as):
    user = user_factory(name="Hulp Deler")
    tree = small_tree(server, user)
    page = context_as(user).new_page()
    page.goto(f"/map/{tree.work}")
    page.locator(ROWS).first.wait_for()
    enter_list(page)
    page.keyboard.press("End")
    expect_focus(page, "c.txt")
    page.keyboard.press("s")
    dialog = page.get_by_role("dialog", name="c.txt delen", exact=True)
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Sluiten", exact=True).first.focus()
    page.keyboard.press("?")
    help_ = page.get_by_role("dialog", name="Sneltoetsen", exact=True)
    expect(help_).to_have_count(1)
    # De sneltoetsen van het delen onder hun eigen kop, niet onder "Deze pagina"
    expect(help_.get_by_role("heading", name="Delen", exact=True)).to_be_visible()
    expect(help_.get_by_text("Alt+P", exact=True)).to_be_visible()
    expect(help_.get_by_role("heading", name="Deze pagina", exact=True)).to_have_count(0)
    page.keyboard.press("Escape")
    expect(help_).to_have_count(0)
    expect(dialog).to_be_visible()


def test_zoekveld_houdt_de_focus_als_delen_mislukt(server, user_factory, context_as):
    """Delen met iemand met wie het intussen al gedeeld is (een ander apparaat): de fout staat
    er, en de focus blijft in het zoekveld (een uitgeschakeld veld zou hem kwijtraken)."""
    owner = user_factory(name="Deler Twee")
    other = user_factory(name=f"Ontvanger {uuid.uuid4().hex[:6]}")
    tree = small_tree(server, owner)
    page = context_as(owner).new_page()
    page.goto(f"/map/{tree.work}")
    page.locator(ROWS).first.wait_for()
    enter_list(page)
    page.keyboard.press("End")
    expect_focus(page, "c.txt")
    page.keyboard.press("s")
    dialog = page.get_by_role("dialog", name="c.txt delen", exact=True)
    expect(dialog).to_be_visible()  # het venster laadt apart; Alt+P werkt pas als het er is
    page.keyboard.press("Alt+P")
    box = dialog.get_by_role("combobox", name="Zoek een persoon of groep")
    expect(box).to_be_focused()
    box.press_sequentially(other.display_name, delay=20)
    expect(dialog.get_by_role("option", name=re.compile(other.display_name))).to_be_visible()
    server.call(
        owner, "POST", f"/api/v1/files/{tree.files['c.txt']}/shares",
        json={"user_id": other.id, "permission": "read"}, expect=201,
    )  # fmt: skip
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(dialog.get_by_role("alert")).to_contain_text("Al gedeeld")
    expect(box).to_be_focused()


def test_een_hulp_op_een_publieke_link(server, user_factory, new_context):
    owner = user_factory(name="Link Hulp")
    folder = mkdir(server, owner, owner.root_id, f"Openbaar {uuid.uuid4().hex[:6]}")
    upload(server, owner, folder, "lees.txt", b"hallo\n")
    url = server.call(
        owner, "POST", f"/api/v1/files/{folder}/links", json={"permission": "read"}, expect=201
    ).json()["link"]["url"]
    page = new_context().new_page()
    page.goto(url)
    expect(page.get_by_role("heading", level=1)).to_have_text(re.compile("^Openbaar"))
    page.locator("body").focus()
    page.keyboard.press("?")
    expect(page.get_by_role("dialog", name="Sneltoetsen", exact=True)).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_pijltjes_scrollen_opzij_in_tekst_en_tabel(server, user_factory, context_as):
    user = user_factory(name="Breed Lezer")
    folder = mkdir(server, user, user.root_id, f"Breed {uuid.uuid4().hex[:6]}")
    # Cellen breken af (overflow-wrap), dus pas met veel kolommen wordt de tabel breder dan
    # het venster
    csv = "\n".join(",".join(f"r{r}k{c}" for c in range(150)) for r in range(5))
    ids = {
        "breed.csv": upload(server, user, folder, "breed.csv", csv.encode()),
        "lang.txt": upload(server, user, folder, "lang.txt", ("woord " * 800 + "\n").encode()),
        "z.txt": upload(server, user, folder, "z.txt", b"kort\n"),
    }
    wait_for_previews(server, user, list(ids.values()))
    page = context_as(user).new_page()

    for name, after in (("breed.csv", "lang.txt"), ("lang.txt", "z.txt")):
        page.goto(f"/map/{folder}?bestand={ids[name]}")
        viewer = page.get_by_role("dialog", name=name, exact=True)
        expect(viewer).to_be_visible()
        region = viewer.get_by_role("region", name="Inhoud van het bestand", exact=True)
        expect(region).to_be_visible()
        region.focus()
        # → scrollt het gebied met de focus opzij zolang dat kan; de viewer blijft hier
        for _ in range(3):
            page.keyboard.press("ArrowRight")
        expect(viewer).to_be_visible()
        page.wait_for_function("(el) => el.scrollLeft > 0", arg=region.element_handle())
        # Shift+→ gaat altijd naar het volgende bestand
        page.keyboard.press("Shift+ArrowRight")
        expect(page.get_by_role("dialog", name=after, exact=True)).to_be_visible()


def test_uitloggen_leegt_het_klembord(server, user_factory, new_context):
    """Wat A knipte, is niet te plakken voor B die daarna in hetzelfde tabblad inlogt."""
    anna = user_factory(name="Anna Knipper")
    bram = user_factory(name="Bram Plakker")
    tree = small_tree(server, anna)
    mkdir(server, bram, bram.root_id, "Eigen map")
    page = new_context().new_page()
    page.goto("/")
    login_via_form(page, anna)
    expect(page.get_by_role("button", name=anna.display_name, exact=True)).to_be_visible()
    page.goto(f"/map/{tree.work}")
    page.locator(ROWS).first.wait_for()
    enter_list(page)
    page.keyboard.press("End")
    expect_focus(page, "c.txt")
    page.keyboard.press(f"{MOD}+x")
    page.get_by_role("button", name="Meer acties", exact=True).first.click()
    menu = page.get_by_role("menu")
    expect(menu.get_by_role("menuitem", name=re.compile("^Plakken"))).to_have_count(1)
    page.keyboard.press("Escape")

    page.get_by_role("button", name=anna.display_name, exact=True).click()
    page.get_by_role("menuitem", name="Uitloggen", exact=True).click()
    login_via_form(page, bram)
    expect(row(page, "Eigen map")).to_be_visible()
    page.get_by_role("button", name="Meer acties", exact=True).first.click()
    menu = page.get_by_role("menu")
    expect(menu.get_by_role("menuitem").first).to_be_visible()
    expect(menu.get_by_role("menuitem", name=re.compile("^Plakken"))).to_have_count(0)
    page.keyboard.press("Escape")
    # Mod+V doet niets (geen poging met de bestanden van Anna, dus ook geen fout)
    enter_list(page)
    page.keyboard.press(f"{MOD}+v")
    page.wait_for_timeout(500)
    expect(toasts(page).get_by_text(re.compile("mislukt|niet", re.I))).to_have_count(0)
    assert "c.txt" not in {
        c["name"]
        for c in server.call(bram, "GET", "/api/v1/files/root", expect=200).json()["children"]
    }


def test_een_ander_apparaat_uitloggen_vraagt_eerst(server, user_factory, context_as):
    user = user_factory(name="Twee Apparaten")
    server.token(user)  # een tweede sessie (httpx), naast die van de browser
    page = context_as(user).new_page()
    page.goto("/account")
    rows = page.get_by_role("row").filter(has=page.get_by_role("button", name="Uitloggen"))
    other = rows.filter(has_not=page.get_by_text("Dit apparaat", exact=True)).first
    expect(other).to_be_visible()
    before = len(server.call(user, "GET", "/api/v1/auth/sessions", expect=200).json())

    other.get_by_role("button", name=re.compile("uitloggen$")).click()
    ask = page.get_by_role("dialog", name=re.compile(r"uitloggen\?$"))
    expect(ask).to_be_visible()
    ask.get_by_role("button", name="Annuleren", exact=True).click()
    expect(ask).to_have_count(0)
    assert len(server.call(user, "GET", "/api/v1/auth/sessions", expect=200).json()) == before

    other.get_by_role("button", name=re.compile("uitloggen$")).click()
    ask = page.get_by_role("dialog", name=re.compile(r"uitloggen\?$"))
    ask.get_by_role("button", name="Uitloggen", exact=True).click()
    expect(toasts(page).get_by_text(re.compile("is uitgelogd\\.$")).first).to_be_visible()


def test_prullenbak_telt_de_kopregel_mee(server, user_factory, context_as):
    user = user_factory(name="Rijen Teller")
    ids = [upload(server, user, user.root_id, f"weg-{i}.txt", b"x") for i in range(4)]
    server.call(user, "POST", "/api/v1/batch/trash", json={"ids": ids}, expect=200)
    page = context_as(user).new_page()
    page.goto("/prullenbak")
    grid = page.get_by_role("grid", name="Inhoud van de prullenbak", exact=True)
    expect(grid.locator('[role=row][aria-rowindex="5"]')).to_be_visible()
    # Vier rijen plus de kopregel: rij 5 van 5
    expect(grid).to_have_attribute("aria-rowcount", "5")
