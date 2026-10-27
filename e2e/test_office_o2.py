"""O2 (de editor) met de nep-DS, via de echte web-app (marker office; §4.4 punten 1, 2 en 6 en
het deel van O2 van punt 9 in docs/fase-4/bouwontwerp.md).

Alles gaat door de echte server: de editorpagina /bewerken/<id>, POST …/session, de proxy en
de tunnel naar de nep-DS (als proces), en de interne routes van de app voor DS (document en
callback). De poort-controle van O1 is er nog niet: de harnas zet PLATFORM_OFFICE_DEV_UNGATED=1
(alleen in de test). Wie eruit gezet is, komt er hier niet weer in omdat de nep-editor na een
drop stopt; dat elk pad naar DS het recht controleert, bewijst O1.
"""

from __future__ import annotations

import os
import re
import signal
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from conftest import Account, create_user
from office_harness import Office, docx_text, fake_info, fixture, frame_editor, type_in_editor

pytestmark = pytest.mark.office

ROW = '#inhoud [role=grid] [role=row][tabindex="0"]'
FAKE_TEXT = 'textarea[aria-label="Nep-editor"]'

WORDS = {
    "nl": {
        "editing": "Bewerken",
        "close": "Sluiten",
        "save": "Nu opslaan",
        "saved": "Opgeslagen om",
        "one": "Bewerken met 1 ander",
    },
    "en": {
        "editing": "Editing",
        "close": "Close",
        "save": "Save now",
        "saved": "Saved at",
        "one": "Editing with 1 other person",
    },
}


@dataclass
class Duo:
    """Anna (eigenaar) en Bram, met de map Samen van Anna met schrijfrecht gedeeld."""

    anna: Account
    bram: Account
    folder: str
    share: str
    node: str


def duo(office: Office, name: str = "verslag.docx") -> Duo:
    server, admin = office.server, office.admin
    tag = uuid.uuid4().hex[:6]
    anna = create_user(server, admin, name=f"Anna {tag}")
    bram = create_user(server, admin, name=f"Bram {tag}")
    folder = server.call(
        anna, "POST", f"/api/v1/files/{anna.root_id}/folders", json={"name": "Samen"}, expect=201
    ).json()["id"]
    share = server.call(
        anna,
        "POST",
        f"/api/v1/files/{folder}/shares",
        json={"user_id": bram.id, "permission": "write"},
        expect=201,
    ).json()["id"]
    node = server.call(
        anna,
        "PUT",
        f"/api/v1/files/{folder}/upload",
        params={"name": name},
        content=fixture("proef.docx"),
        expect=201,
    ).json()["id"]
    return Duo(anna, bram, folder, share, node)


def wait_until(check: Callable[[], Any], seconds: float = 30.0, what: str = "") -> Any:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError(f"niet op tijd: {what or check}")


def uid(office: Office, account: Account) -> str:
    from app.office.oo import tokens

    return tokens.ds_user_id(office.settings(), uuid.UUID(account.id))


def versions(office: Office, account: Account, node: str) -> list[dict]:
    return office.server.call(account, "GET", f"/api/v1/files/{node}/versions", expect=200).json()


def text(office: Office, account: Account, node: str) -> str:
    r = office.server.call(account, "GET", f"/api/v1/files/{node}/content", expect=200)
    return docx_text(r.content)


def children(office: Office, account: Account, folder: str) -> list[dict]:
    r = office.server.call(account, "GET", f"/api/v1/files/{folder}/children", expect=200)
    return r.json()["children"]


def is_session(node: str) -> Callable[[Any], bool]:
    path = f"/api/v1/office/files/{node}/session"
    return lambda r: r.request.method == "POST" and r.url.split("?", 1)[0].endswith(path)


def ready(page: Page, lang: str = "nl") -> None:
    """De editor is klaar: de statusregel zegt Bewerken (of met anderen) en het tekstvak van de
    nep-editor is aan."""
    expect(page.locator(".editor-status")).to_have_text(
        re.compile(rf"^{WORDS[lang]['editing']}"), timeout=30_000
    )
    expect(frame_editor(page).locator(FAKE_TEXT)).to_be_enabled()


def open_page(browsers: Any, account: Account, node: str) -> tuple[Page, str]:
    """Een eigen context als account, /bewerken/<id> openen; geeft de pagina en de key."""
    page = browsers.context_as(account, service_workers="block").new_page()
    with page.expect_response(is_session(node)) as answer:
        page.goto(f"/bewerken/{node}")
    assert answer.value.status == 200, answer.value.text()
    key = answer.value.json()["key"]
    ready(page)
    return page, key


def callback(office: Office, key: str, status: int, seconds: float = 30) -> dict:
    """De callback met deze status die de app aannam (200, error 0)."""
    assert office.fake is not None

    def found() -> dict | None:
        for entry in office.fake.callbacks(key):
            if entry.get("status") == status and entry.get("http") == 200:
                return entry
        return None

    entry = wait_until(found, seconds, f"callback {status} op {key}")
    assert entry["answer"] == {"error": 0}, entry
    return entry


def toast(page: Page, text: str | re.Pattern[str]) -> Any:
    return page.get_by_role("region", name="Meldingen").get_by_text(text)


# --- §4.4 punt 1: samen bewerken ---


def test_samen_bewerken_geeft_een_versie_met_beide_teksten(office: Office, office_browsers) -> None:
    """Anna en Bram openen dezelfde docx in een eigen context: dezelfde key, de nep-DS kent ze
    allebei, wat de een typt ziet de ander meteen. Bram drukt Nu opslaan: een versie erbij.
    Allebei sluiten: status 2, en nog steeds precies één nieuwe versie, met beide teksten."""
    d = duo(office)
    page_a, key = open_page(office_browsers, d.anna, d.node)
    page_b, key_b = open_page(office_browsers, d.bram, d.node)
    assert key_b == key

    # De kopbalk: de naam, de titel van de iframe, en het aantal anderen uit office.presence
    for page in (page_a, page_b):
        expect(page.get_by_role("heading", name="verslag.docx", exact=True)).to_be_visible()
        frame = page.locator("iframe[name=frameEditor]")
        expect(frame).to_have_attribute("title", "Editor: verslag.docx")
        expect(page.locator(".editor-status")).to_have_text(WORDS["nl"]["one"])
    info = fake_info(office, key)
    assert info["open"] is True
    assert sorted(info["users"]) == sorted([uid(office, d.anna), uid(office, d.bram)])

    type_in_editor(page_a, "Anna was hier")
    expect(frame_editor(page_b).locator("#others")).to_contain_text("Anna was hier")
    type_in_editor(page_b, "Bram ook")
    expect(frame_editor(page_a).locator("#others")).to_contain_text("Bram ook")
    assert versions(office, d.anna, d.node) == []

    # Nu opslaan door Bram: status 6, een versie erbij (de inhoud van vóór de sessie)
    page_b.get_by_role("button", name="Nu opslaan", exact=True).click()
    callback(office, key, 6)
    wait_until(lambda: len(versions(office, d.anna, d.node)) == 1, 20, "een versie")
    for page in (page_a, page_b):
        expect(page.locator(".editor-status")).to_contain_text("Opgeslagen om")
    now = text(office, d.anna, d.node)
    assert "Anna was hier" in now and "Bram ook" in now

    # Allebei sluiten: terug naar de map (er was geen stap terug), status 2
    for page in (page_a, page_b):
        page.get_by_role("button", name="Sluiten", exact=True).click()
        expect(page).to_have_url(re.compile(rf"/map/{d.folder}$"))
    callback(office, key, 2)
    assert fake_info(office, key)["open"] is False
    listed = versions(office, d.anna, d.node)
    assert len(listed) == 1, listed  # de opslag bij het sluiten kwam in dezelfde versie
    assert listed[0]["replaced_by"]["id"] in (d.anna.id, d.bram.id)
    final = text(office, d.anna, d.node)
    for part in ("Proefdocument van het platform", "Anna was hier", "Bram ook"):
        assert part in final, final
    # De versie is de inhoud van vóór de sessie
    old = office.server.call(
        d.anna,
        "GET",
        f"/api/v1/files/{d.node}/content",
        params={"version": listed[0]["id"]},
        expect=200,
    )
    assert "Anna was hier" not in docx_text(old.content)


# --- §4.4 punt 2: randgevallen ---


def test_hernoemen_tijdens_het_bewerken(office: Office, office_browsers) -> None:
    """Hernoemd terwijl de editor open is: opgeslagen in het hernoemde bestand (hetzelfde id)."""
    d = duo(office)
    page, key = open_page(office_browsers, d.anna, d.node)
    type_in_editor(page, "voor het hernoemen")
    office.server.call(
        d.anna, "PATCH", f"/api/v1/files/{d.node}", json={"name": "hernoemd.docx"}, expect=200
    )
    page.wait_for_timeout(1500)  # een ronde later: de sessie loopt gewoon door
    type_in_editor(page, "na het hernoemen")
    page.get_by_role("button", name="Sluiten", exact=True).click()
    callback(office, key, 2)
    node = office.server.call(d.anna, "GET", f"/api/v1/files/{d.node}", expect=200).json()
    assert node["name"] == "hernoemd.docx"
    final = text(office, d.anna, d.node)
    assert "voor het hernoemen" in final and "na het hernoemen" in final


def test_prullenbak_terwijl_de_eigenaar_bewerkt(office: Office, office_browsers) -> None:
    """De eigenaar zet het bestand in de prullenbak terwijl hij bewerkt: hij blijft erin, en het
    bestand in de prullenbak heeft daarna de nieuwe inhoud (terugzetten brengt het werk terug).
    Een nieuwe sessie op dat bestand kan niet (422 trashed)."""
    d = duo(office)
    page, key = open_page(office_browsers, d.anna, d.node)
    type_in_editor(page, "in de prullenbak")
    office.server.call(d.anna, "DELETE", f"/api/v1/files/{d.node}", expect=204)
    page.wait_for_timeout(2500)  # twee rondes: de eigenaar mag blijven
    expect(frame_editor(page).locator(FAKE_TEXT)).to_be_enabled()
    type_in_editor(page, "en nog meer")
    r = office.server.call(d.bram, "POST", f"/api/v1/office/files/{d.node}/session", json={})
    assert r.status_code in (404, 422), r.text  # Bram ziet het niet meer
    page.get_by_role("button", name="Sluiten", exact=True).click()
    callback(office, key, 2)
    trash = office.server.call(d.anna, "GET", "/api/v1/trash", expect=200).json()
    assert d.node in [n["id"] for n in trash]
    final = text(office, d.anna, d.node)
    assert "in de prullenbak" in final and "en nog meer" in final
    r = office.server.call(d.anna, "POST", f"/api/v1/office/files/{d.node}/session", json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "trashed", r.text


def test_deling_ingetrokken_terwijl_alleen_bram_bewerkt(office: Office, office_browsers) -> None:
    """Anna trekt de deling in terwijl alleen Bram bewerkt: binnen enkele seconden is hij uit
    de editor, met de melding. Zijn werk komt als herstelkopie in de hoofdmap van Anna, en Anna
    krijgt office.recovered (een melding met Openen). Bram krijgt alleen office.dropped, zonder
    naam: geen melding over de kopie."""
    d = duo(office)
    anna_page = office_browsers.context_as(d.anna, service_workers="block").new_page()
    anna_page.goto("/")
    expect(anna_page.locator(ROW).filter(has_text="Samen")).to_be_visible()
    page, key = open_page(office_browsers, d.bram, d.node)
    type_in_editor(page, "Bram alleen")
    wait_until(lambda: fake_info(office, key).get("dirty"), 10, "de tekst bij de nep-DS")

    office.server.call(d.anna, "DELETE", f"/api/v1/shares/{d.share}", expect=204)
    expect(page.get_by_role("alert")).to_have_text(
        "Je hebt geen toegang meer; de editor is gesloten.", timeout=10_000
    )
    expect(page.locator("iframe[name=frameEditor]")).to_have_count(0)
    expect(page.get_by_role("button", name="Nu opslaan", exact=True)).to_be_disabled()
    callback(office, key, 2)

    copies = wait_until(
        lambda: [n for n in children(office, d.anna, d.anna.root_id) if "hersteld" in n["name"]],
        15,
        "de herstelkopie",
    )
    assert len(copies) == 1, copies
    copy = copies[0]
    assert copy["name"].startswith("verslag (hersteld ") and copy["name"].endswith(".docx")
    assert "Bram alleen" in text(office, d.anna, copy["id"])
    assert "Bram alleen" not in text(office, d.anna, d.node)  # het origineel bleef

    message = toast(anna_page, re.compile(r"Niemand mocht het origineel nog bewerken"))
    expect(message).to_contain_text(copy["name"])
    # Openen gaat naar de kopie in haar map
    region = anna_page.get_by_role("region", name="Meldingen")
    region.get_by_role("button", name="Openen", exact=True).click()
    expect(anna_page).to_have_url(re.compile(rf"bestand={copy['id']}"))
    # Bram: geen melding over de kopie (geen naam, geen id)
    expect(page.get_by_text("hersteld")).to_have_count(0)
    assert copy["id"] not in page.content()


def test_definitief_verwijderd_terwijl_bram_bewerkt(office: Office, office_browsers) -> None:
    """Anna verwijdert het bestand definitief terwijl Bram bewerkt: de ronde zet hem eruit (drop)
    en sluit de sessie; er komt geen kopie."""
    d = duo(office)
    page, key = open_page(office_browsers, d.bram, d.node)
    type_in_editor(page, "dit gaat weg")
    wait_until(lambda: fake_info(office, key).get("dirty"), 10, "de tekst bij de nep-DS")

    office.server.call(d.anna, "DELETE", f"/api/v1/files/{d.node}", expect=204)
    office.server.call(d.anna, "DELETE", f"/api/v1/trash/{d.node}", expect=204)
    expect(frame_editor(page).locator(FAKE_TEXT)).to_be_disabled(timeout=10_000)
    callback(office, key, 2)  # de app nam hem aan en schreef niets
    assert fake_info(office, key)["open"] is False
    page.wait_for_timeout(3000)  # ook het herstel (elke 2 s) maakt niets
    names = [n["name"] for n in children(office, d.anna, d.anna.root_id)]
    assert not [n for n in names if "hersteld" in n], names
    assert office.server.call(d.anna, "GET", f"/api/v1/files/{d.node}").status_code == 404


# --- §4.4 punt 6: DS weg en app weg ---


def test_ds_weg_en_weer_terug(office: Office, office_browsers) -> None:
    """De nep-DS stoppen (op PID): Bewerken zegt dat de editor niet bereikbaar is, het
    voorbeeld in de viewer blijft. Weer starten: de editor werkt weer, zonder de app te
    herstarten."""
    assert office.fake is not None
    d = duo(office)
    page = office_browsers.context_as(d.anna, service_workers="block").new_page()
    office.fake.stop()
    try:
        office.wait_state("down", 15)
        r = office.server.call(d.anna, "POST", f"/api/v1/office/files/{d.node}/session", json={})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "office_unavailable"

        page.goto(f"/map/{d.folder}?bestand={d.node}")
        viewer = page.get_by_role("dialog", name="verslag.docx", exact=True)
        expect(viewer.get_by_text("Vereenvoudigd voorbeeld zonder opmaak")).to_be_visible()
        expect(viewer.get_by_text("Proefdocument van het platform").first).to_be_visible()
        edit = viewer.get_by_role("button", name="Bewerken", exact=True)
        expect(edit).to_have_attribute("aria-disabled", "true")
        expect(edit).to_have_attribute(
            "title", "De editor is nu niet bereikbaar; probeer het later opnieuw"
        )

        # Een link naar de editor die je kreeg: de melding, met Opnieuw laden
        page.goto(f"/bewerken/{d.node}")
        expect(page.get_by_role("alert")).to_contain_text("De editor is nu niet bereikbaar")
        expect(page.get_by_role("button", name="Opnieuw laden", exact=True)).to_be_visible()
    finally:
        office.fake.start()
    office.wait_state("ok", 30)
    with page.expect_response(is_session(d.node)) as answer:
        page.get_by_role("button", name="Opnieuw laden", exact=True).click()
    assert answer.value.status == 200
    ready(page)
    page.get_by_role("button", name="Sluiten", exact=True).click()
    callback(office, answer.value.json()["key"], 4)  # niets veranderd: status 4


def kill_app(office: Office) -> None:
    """De app valt weg: SIGKILL op zijn PID (geen lifespan, dus ook geen checkpoint_all)."""
    proc = office.server.proc
    assert proc is not None
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    office.server.stop()  # het proces is al weg: alleen de client dicht


def forgotten(office: Office) -> list[str]:
    async def ask(ds: Any) -> dict:
        return await ds.command("getForgottenList")

    return list(office.ds(ask).get("keys") or [])


def test_app_weg_tijdens_het_bewerken_het_herstel_zet_het_terug(
    office: Office, office_browsers
) -> None:
    """De app valt weg terwijl Anna bewerkt: de editor zegt "verbinding weg", de nep-DS kan
    status 2 niet kwijt en zet het document bij de forgotten files. De app weer starten: het
    herstel (elke 2 s in de e2e) zet de inhoud in het bestand, en opnieuw openen toont hem."""
    d = duo(office)
    page, key = open_page(office_browsers, d.anna, d.node)
    type_in_editor(page, "Anna voor de val")
    wait_until(lambda: fake_info(office, key).get("dirty"), 10, "de tekst bij de nep-DS")

    kill_app(office)
    try:
        expect(page.get_by_role("alert")).to_contain_text(
            "De verbinding met de editor is weg", timeout=15_000
        )
        expect(page.locator(".editor-status")).to_have_text("Verbinding weg")
        wait_until(lambda: key in forgotten(office), 30, "de forgotten file")
        # De pagina weg vóór de herstart: anders verbindt de nep-editor opnieuw op de oude key
        page.goto("about:blank")
    finally:
        office.server.start()
    office.wait_state("ok", 60)

    wait_until(
        lambda: "Anna voor de val" in text(office, d.anna, d.node), 30, "het herstel van de inhoud"
    )
    wait_until(lambda: key not in forgotten(office), 10, "de forgotten file weg")
    assert len(versions(office, d.anna, d.node)) == 1
    # Opnieuw openen: een nieuwe key, en de tekst staat in het document
    with page.expect_response(is_session(d.node)) as answer:
        page.goto(f"/bewerken/{d.node}")
    assert answer.value.status == 200 and answer.value.json()["key"] != key
    ready(page)
    expect(frame_editor(page).locator("#document")).to_contain_text("Anna voor de val")
    page.get_by_role("button", name="Sluiten", exact=True).click()


# --- §4.4 punt 9, het deel van O2: alleen het toetsenbord, in het Nederlands en het Engels ---


def start(page: Page) -> None:
    page.evaluate("() => document.activeElement && document.activeElement.blur()")


def tab_to(page: Page, target: Any, limit: int = 40, back: bool = False) -> None:
    target.wait_for(state="attached")
    for _ in range(limit):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return
    raise AssertionError(f"Tab kwam niet bij {target}")


def test_toetsenbord_openen_opslaan_en_sluiten(office: Office, office_browsers) -> None:
    """E op het bestand opent de editor; de iframe heeft de focus en een titel; Alt+Shift+Q in
    de iframe zet de focus op Sluiten; Tab naar Nu opslaan en Enter geeft een versie erbij;
    Shift+Tab en Enter op Sluiten gaan terug naar de map. Daarna alles in het Engels."""
    d = duo(office)
    page = office_browsers.context_as(d.anna, service_workers="block").new_page()
    for lang, words in WORDS.items():
        office.server.call(d.anna, "PATCH", "/api/v1/auth/me", json={"locale": lang}, expect=200)
        page.goto(f"/map/{d.folder}")
        start(page)
        tab_to(page, page.locator(ROW).filter(has_text="verslag.docx"))
        with page.expect_response(is_session(d.node)) as answer:
            page.keyboard.press("e")
        key = answer.value.json()["key"]
        expect(page).to_have_url(re.compile(rf"/bewerken/{d.node}$"))
        ready(page, lang)
        frame = page.locator("iframe[name=frameEditor]")
        expect(frame).to_have_attribute("title", "Editor: verslag.docx")
        expect(frame).to_be_focused()
        close = page.get_by_role("button", name=words["close"], exact=True)
        save = page.get_by_role("button", name=words["save"], exact=True)

        # Typen is van de editor zelf: de focus in zijn tekstvak, dan het toetsenbord
        frame_editor(page).locator(FAKE_TEXT).focus()
        page.keyboard.type(f"met het toetsenbord ({lang})")
        page.keyboard.press("Alt+Shift+Q")
        expect(close).to_be_focused()
        page.keyboard.press("Tab")
        expect(save).to_be_focused()
        before = len(versions(office, d.anna, d.node))
        page.keyboard.press("Enter")
        callback(office, key, 6)
        wanted = before + 1
        wait_until(lambda n=wanted: len(versions(office, d.anna, d.node)) == n, 20, "een versie")
        expect(page.locator(".editor-status")).to_contain_text(words["saved"])

        # De focus bleef op Nu opslaan. Shift+Tab naar Sluiten, terug, en Alt+Shift+Q ook
        # vanaf de pagina zelf
        expect(save).to_be_focused()
        page.keyboard.press("Shift+Tab")
        expect(close).to_be_focused()
        page.keyboard.press("Tab")
        page.keyboard.press("Alt+Shift+Q")
        expect(close).to_be_focused()
        page.keyboard.press("Enter")
        expect(page).to_have_url(re.compile(rf"/map/{d.folder}$"))
        callback(office, key, 2)
    final = text(office, d.anna, d.node)
    assert "met het toetsenbord (nl)" in final and "met het toetsenbord (en)" in final
    expect(page.locator("html")).to_have_attribute("lang", "en")
    office.server.call(d.anna, "PATCH", "/api/v1/auth/me", json={"locale": None}, expect=200)
