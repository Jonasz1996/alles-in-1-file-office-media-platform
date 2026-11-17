"""Een album met een regel en een publieke link (fase 6, Z6; Keuze 42) in de browser, alleen
met het toetsenbord: Tab, Enter, Spatie en typen (docs/fase-6/toetsenbord-z6.md).

- De regel "strand" op een album dat nog niet gedeeld is: de twee strandfoto's komen erin, met
  het merkje "alleen voor jou"; de foto die je er zelf in zette, is gedeeld zoals altijd.
- Een publieke link maken (S, Alt+L, Link maken): het deeldialoog telt de items van de regel en
  daarna komt de vraag "Nieuwe foto's ook delen?"; Enter is "Nee, alleen voor mij".
- Wie de link heeft, ziet alleen de foto met de hand. Een nieuwe strandfoto komt na "Nu
  bijwerken" in het album, maar niet achter de link, tot de eigenaar "ja" kiest (het vinkje in
  de regel, Opslaan): dan ziet de link alles en is er geen merkje meer.
- Een regel op een album dat al een link heeft: de vraag komt na Opslaan; "Ja, delen" deelt de
  foto's van de regel meteen. Een andere regel daarna, met het vinkje aan maar niet aangeraakt:
  het vinkje gaat niet mee en de vraag komt opnieuw ("ja" gold voor de regel van toen); een
  vinkje dat je zelf aanzet, gaat wel mee.

De foto's hebben een vaste dag (mtime), niet die van nu.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from PIL import Image
from playwright.sync_api import Locator, Page, expect

pytestmark = pytest.mark.zoek

FOCUSED = '[role=gridcell][tabindex="0"]'
ONLY_YOU = '[role=gridcell][aria-label$=", alleen voor jou"]'
ACTIEF = """() => {
  const a = document.activeElement;
  if (!a) return "-";
  const naam = (a.getAttribute("aria-label") || a.textContent || "").trim();
  return `${a.tagName} ${naam.slice(0, 40)}`;
}"""
# Vaste opnamedagen (de lastModified bij het uploaden), een dag na elkaar
DAYS = {
    "strand-1.png": datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    "strand-2.png": datetime(2026, 5, 2, 10, 0, tzinfo=UTC),
    "bos-1.png": datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
    "strand-3.png": datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
    "zee-1.png": datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
}
COLORS = {
    "strand-1.png": (230, 200, 120),
    "strand-2.png": (210, 180, 100),
    "bos-1.png": (40, 120, 50),
    "strand-3.png": (240, 220, 150),
    "zee-1.png": (60, 120, 200),
}


def tab_to(page: Page, target: Locator, limit: int = 40, back: bool = False) -> int:
    """Tab (of Shift+Tab) tot target de focus heeft; geeft het aantal keer."""
    target.wait_for(state="attached")
    gezien = []
    for i in range(limit):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return i + 1
        gezien.append(page.evaluate(ACTIEF))
    raise AssertionError(f"Tab kwam niet bij {target}: {gezien}")


def toast(page: Page, tekst: str) -> Locator:
    """Een melding (niet de kopie voor de schermlezer)."""
    return page.get_by_role("region", name="Meldingen").get_by_text(tekst, exact=True)


def upload(server, account, name: str) -> str:
    """Een kleine PNG met een vaste dag; geeft het id."""
    image = io.BytesIO()
    Image.new("RGB", (16, 16), COLORS[name]).save(image, format="PNG")
    mtime = int(DAYS[name].timestamp() * 1000)
    r = server.call(
        account, "PUT", f"/api/v1/files/{account.root_id}/upload",
        params={"name": name, "mtime": mtime}, content=image.getvalue(), expect=201,
    )  # fmt: skip
    return r.json()["id"]


def album_met_bos(server, account) -> tuple[str, dict[str, str]]:
    """Het album Zomer met bos-1.png (met de hand) en de twee strandfoto's in de bibliotheek."""
    ids = {name: upload(server, account, name) for name in ("strand-1.png", "strand-2.png")}
    ids["bos-1.png"] = upload(server, account, "bos-1.png")
    album = server.call(account, "POST", "/api/v1/albums", json={"name": "Zomer"}, expect=201)
    album_id = album.json()["id"]
    server.call(
        account, "POST", f"/api/v1/albums/{album_id}/items",
        json={"node_ids": [ids["bos-1.png"]]}, expect=200,
    )  # fmt: skip
    return album_id, ids


def rule_of(server, account, album_id: str) -> dict:
    return server.call(account, "GET", f"/api/v1/albums/{album_id}", expect=200).json()["rule"]


def via_link(new_context, url: str) -> Page:
    """De link openen zonder account; geeft de pagina."""
    page = new_context().new_page()
    page.goto(urlsplit(url).path)
    return page


def link_cells(page: Page) -> Locator:
    grid = page.get_by_role("grid", name="Foto's in Zomer", exact=True)
    return grid.get_by_role("gridcell")


def open_rule(page: Page) -> Locator:
    """De knop Regel met Tab, Enter; geeft het dialoog (de focus staat in Naam of tekst)."""
    tab_to(page, page.get_by_role("button", name="Regel", exact=True), limit=60)
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Regel voor Zomer", exact=True)
    expect(dialog.get_by_label("Naam of tekst", exact=True)).to_be_focused()
    return dialog


def test_nieuwe_foto_pas_achter_de_link_na_ja(server, user_factory, context_as, new_context):
    fien = user_factory(name="Fien Regel")
    album_id, _ = album_met_bos(server, fien)
    page = context_as(fien).new_page()
    page.goto(f"/album/{album_id}")
    expect(page.get_by_role("heading", name="Zomer", level=1)).to_be_visible()
    cells = page.get_by_role("grid", name="Foto's in Zomer", exact=True).get_by_role("gridcell")
    expect(cells).to_have_count(1)

    # --- de regel: "strand" typen, Enter slaat op ---
    dialog = open_rule(page)
    # Op een album dat niet gedeeld is, is er niets te vragen
    expect(dialog.get_by_role("checkbox")).to_have_count(0)
    page.keyboard.type("strand", delay=20)
    with page.expect_response(
        lambda r: r.request.method == "PUT" and r.url.endswith(f"/albums/{album_id}/rule")
    ) as gezet:
        page.keyboard.press("Enter")
    assert gezet.value.status == 200
    assert gezet.value.json() == {"needs_confirm": False}
    expect(toast(page, "De regel is opgeslagen. Het album wordt bijgewerkt.")).to_be_visible()
    expect(dialog).to_be_hidden()
    expect(cells).to_have_count(3)
    expect(page.locator(ONLY_YOU)).to_have_count(2)
    for name in ("strand-1.png", "strand-2.png"):
        expect(page.locator(f'{ONLY_YOU}[aria-label^="{name}"]')).to_have_count(1)
    expect(page.get_by_text("met een regel", exact=False)).to_be_visible()
    rule = rule_of(server, fien, album_id)
    assert (rule["items"], rule["private"], rule["share_new"]) == (2, 2, False)

    # --- een publieke link: S, Alt+L, Link maken; daarna de vraag ---
    tab_to(page, page.locator(FOCUSED), limit=60)
    page.keyboard.press("s")
    share = page.get_by_role("dialog", name="Zomer delen", exact=True)
    expect(share).to_be_visible()
    teller = "2 foto's komen uit de regel; 0 daarvan worden gedeeld."
    expect(share.get_by_role("status").filter(has_text=teller)).to_be_visible()
    page.keyboard.press("Alt+L")
    tab_to(page, share.get_by_role("button", name="Link maken", exact=True), limit=20)
    page.keyboard.press("Enter")
    vraag = page.get_by_role("dialog", name="Nieuwe foto's ook delen?", exact=True)
    expect(vraag).to_be_visible()
    expect(vraag).to_contain_text(
        "2 foto's in dit album komen uit de regel; 2 daarvan ziet nu alleen jij."
    )
    nee = vraag.get_by_role("button", name="Nee, alleen voor mij", exact=True)
    expect(nee).to_be_focused()
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/albums/{album_id}/rule/confirm")
    ) as beantwoord:
        page.keyboard.press("Enter")
    assert beantwoord.value.json() == {"needs_confirm": False}
    assert beantwoord.value.request.post_data_json == {"share_new": False}
    expect(toast(page, "Nieuwe foto's uit de regel ziet alleen jij.")).to_be_visible()
    expect(vraag).to_be_hidden()
    address = share.get_by_label("Adres van Link", exact=True)
    expect(address).to_have_value(re.compile(r"/s/"))
    url = address.input_value()
    page.keyboard.press("Escape")
    expect(share).to_be_hidden()

    # --- wie de link heeft, ziet alleen bos-1 ---
    anoniem = via_link(new_context, url)
    expect(link_cells(anoniem)).to_have_count(1)
    expect(link_cells(anoniem).first).to_have_attribute("aria-label", re.compile(r"^bos-1\.png"))

    # --- een nieuwe strandfoto: na Nu bijwerken in het album, niet achter de link ---
    upload(server, fien, "strand-3.png")
    dialog = open_rule(page)
    # Nu is het album gedeeld: de vraag staat als vinkje in de regel, uit
    delen = dialog.get_by_role("checkbox", name="Nieuwe foto's die passen, ook delen")
    expect(delen).not_to_be_checked()
    tab_to(page, dialog.get_by_role("button", name="Nu bijwerken", exact=True), limit=20)
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/albums/{album_id}/refresh")
    ) as ververst:
        page.keyboard.press("Enter")
    assert ververst.value.status == 202
    expect(toast(page, "Het album wordt bijgewerkt.")).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(cells).to_have_count(4)
    expect(page.locator(ONLY_YOU)).to_have_count(3)
    expect(page.locator(f'{ONLY_YOU}[aria-label^="strand-3.png"]')).to_have_count(1)
    rule = rule_of(server, fien, album_id)
    assert (rule["items"], rule["private"], rule["share_new"]) == (3, 3, False)
    anoniem.reload()
    expect(link_cells(anoniem)).to_have_count(1)

    # --- de eigenaar kiest ja: het vinkje (Spatie), Opslaan ---
    dialog = open_rule(page)
    tab_to(page, delen, limit=20)
    page.keyboard.press("Space")
    expect(delen).to_be_checked()
    tab_to(page, dialog.get_by_role("button", name="Opslaan", exact=True), limit=20)
    with page.expect_response(
        lambda r: r.request.method == "PUT" and r.url.endswith(f"/albums/{album_id}/rule")
    ) as gezet:
        page.keyboard.press("Enter")
    assert gezet.value.json() == {"needs_confirm": False}
    assert gezet.value.request.post_data_json == {"rule": {"q": "strand"}, "share_new": True}
    # (de melding is dezelfde als bij de eerste keer: het bewijs is het antwoord en het album)
    expect(dialog).to_be_hidden()
    expect(page.locator(ONLY_YOU)).to_have_count(0)
    expect(cells).to_have_count(4)
    rule = rule_of(server, fien, album_id)
    assert (rule["items"], rule["private"], rule["share_new"]) == (3, 0, True)
    anoniem.reload()
    expect(link_cells(anoniem)).to_have_count(4)
    names = sorted(
        (c.get_attribute("aria-label") or "").split(",")[0] for c in link_cells(anoniem).all()
    )
    assert names == ["bos-1.png", "strand-1.png", "strand-2.png", "strand-3.png"]


def test_regel_op_een_gedeeld_album_ja_delen(server, user_factory, context_as, new_context):
    fien = user_factory(name="Fien Gedeeld")
    album_id, _ = album_met_bos(server, fien)
    link = server.call(
        fien, "POST", f"/api/v1/files/{album_id}/links", json={"permission": "read"},
        expect=201,
    ).json()  # fmt: skip
    anoniem = via_link(new_context, link["link"]["url"])
    expect(link_cells(anoniem)).to_have_count(1)

    page = context_as(fien).new_page()
    page.goto(f"/album/{album_id}")
    cells = page.get_by_role("grid", name="Foto's in Zomer", exact=True).get_by_role("gridcell")
    expect(cells).to_have_count(1)
    dialog = open_rule(page)
    # Een nieuwe regel: nog geen vinkje, de vraag komt na Opslaan
    expect(dialog.get_by_role("checkbox")).to_have_count(0)
    page.keyboard.type("strand", delay=20)
    with page.expect_response(
        lambda r: r.request.method == "PUT" and r.url.endswith(f"/albums/{album_id}/rule")
    ) as gezet:
        page.keyboard.press("Enter")
    assert gezet.value.json() == {"needs_confirm": True}
    vraag = page.get_by_role("dialog", name="Nieuwe foto's ook delen?", exact=True)
    expect(vraag).to_be_visible()
    expect(vraag).to_contain_text(
        "2 foto's in dit album komen uit de regel; 2 daarvan ziet nu alleen jij."
    )
    # Tot het antwoord ziet de link de foto's van de regel niet
    anoniem.reload()
    expect(link_cells(anoniem)).to_have_count(1)
    ja = vraag.get_by_role("button", name="Ja, delen", exact=True)
    tab_to(page, ja, limit=5)
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/albums/{album_id}/rule/confirm")
    ) as beantwoord:
        page.keyboard.press("Enter")
    assert beantwoord.value.request.post_data_json == {"share_new": True}
    assert beantwoord.value.json() == {"needs_confirm": False}
    expect(toast(page, "De foto's uit de regel worden gedeeld.")).to_be_visible()
    expect(vraag).to_be_hidden()
    expect(cells).to_have_count(3)
    expect(page.locator(ONLY_YOU)).to_have_count(0)
    anoniem.reload()
    expect(link_cells(anoniem)).to_have_count(3)

    # --- een andere regel na ja (nacontrole golf 2, veilig V1): het vinkje staat aan, maar wie
    # het niet aanraakt, stuurt het niet mee; de vraag komt opnieuw en de link ziet niets nieuws ---
    upload(server, fien, "zee-1.png")
    dialog = open_rule(page)
    delen = dialog.get_by_role("checkbox", name="Nieuwe foto's die passen, ook delen")
    expect(delen).to_be_checked()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("zee", delay=20)
    with page.expect_response(
        lambda r: r.request.method == "PUT" and r.url.endswith(f"/albums/{album_id}/rule")
    ) as gezet:
        page.keyboard.press("Enter")
    assert gezet.value.request.post_data_json == {"rule": {"q": "zee"}}
    assert gezet.value.json() == {"needs_confirm": True}
    vraag = page.get_by_role("dialog", name="Nieuwe foto's ook delen?", exact=True)
    expect(vraag).to_contain_text(
        "1 foto's in dit album komen uit de regel; 1 daarvan ziet nu alleen jij."
    )
    nee = vraag.get_by_role("button", name="Nee, alleen voor mij", exact=True)
    expect(nee).to_be_focused()
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/albums/{album_id}/rule/confirm")
    ) as beantwoord:
        page.keyboard.press("Enter")
    assert beantwoord.value.request.post_data_json == {"share_new": False}
    expect(vraag).to_be_hidden()
    expect(cells).to_have_count(2)
    expect(page.locator(f'{ONLY_YOU}[aria-label^="zee-1.png"]')).to_have_count(1)
    rule = rule_of(server, fien, album_id)
    assert (rule["items"], rule["private"], rule["share_new"]) == (1, 1, False)
    anoniem.reload()
    expect(link_cells(anoniem)).to_have_count(1)

    # --- een andere regel en het vinkje zelf aangezet (Spatie): dat antwoord gaat mee ---
    dialog = open_rule(page)
    expect(delen).not_to_be_checked()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("strand", delay=20)
    tab_to(page, delen, limit=20)
    page.keyboard.press("Space")
    expect(delen).to_be_checked()
    tab_to(page, dialog.get_by_role("button", name="Opslaan", exact=True), limit=20)
    with page.expect_response(
        lambda r: r.request.method == "PUT" and r.url.endswith(f"/albums/{album_id}/rule")
    ) as gezet:
        page.keyboard.press("Enter")
    assert gezet.value.request.post_data_json == {"rule": {"q": "strand"}, "share_new": True}
    assert gezet.value.json() == {"needs_confirm": False}
    expect(dialog).to_be_hidden()
    # zee-1 past niet meer en gaat eruit; de strandfoto's komen terug, meteen gedeeld
    expect(cells).to_have_count(3)
    expect(page.locator(ONLY_YOU)).to_have_count(0)
    anoniem.reload()
    expect(link_cells(anoniem)).to_have_count(3)
