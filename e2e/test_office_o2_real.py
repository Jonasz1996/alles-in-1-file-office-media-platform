"""O2 (de editor) met de echte DS, via de echte web-app (marker office_real; §4.5 punten 1 en 4
en §4.6 punt 1 van docs/fase-4/bouwontwerp.md).

Draait in de job office van CI, en lokaal zo (docs/fase-4/e2e.md):

    bash e2e/office_ds.sh start && bash e2e/office_ds.sh wait
    cd web && npm run build
    cd api && E2E_REAL_DS=http://127.0.0.1:18080 \\
      E2E_REAL_DS_SECRET_FILE=/tmp/office-ds/jwt.secret \\
      uv run --with playwright pytest ../e2e -m office_real

Zonder E2E_REAL_DS wordt hij overgeslagen. Sinds O1 geldt de echte poort (het cookie office en
het recht van nu); het harnas zet PLATFORM_OFFICE_DEV_UNGATED niet meer.
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from conftest import Account, artifacts_dir, create_user, enable_2fa, totp_code
from office_harness import Office, docx_text, fixture, session_row, type_in_editor

pytestmark = pytest.mark.office_real

READY_SECONDS = 120


@dataclass
class Duo:
    anna: Account
    bram: Account
    folder: str
    node: str


def wait_until(check: Callable[[], Any], seconds: float = 60.0, what: str = "") -> Any:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"niet op tijd: {what or check}")


def duo(office: Office, name: str = "verslag.docx", data: bytes | None = None) -> Duo:
    """Anna (eigenaar) en Bram, de map Samen van Anna met schrijfrecht gedeeld, en een
    bestand daarin."""
    server, admin = office.server, office.admin
    tag = uuid.uuid4().hex[:6]
    anna = create_user(server, admin, name=f"Anna {tag}")
    bram = create_user(server, admin, name=f"Bram {tag}")
    folder = server.call(
        anna, "POST", f"/api/v1/files/{anna.root_id}/folders", json={"name": "Samen"}, expect=201
    ).json()["id"]
    server.call(
        anna,
        "POST",
        f"/api/v1/files/{folder}/shares",
        json={"user_id": bram.id, "permission": "write"},
        expect=201,
    )
    node = server.call(
        anna,
        "PUT",
        f"/api/v1/files/{folder}/upload",
        params={"name": name},
        content=data if data is not None else fixture("proef.docx"),
        expect=201,
    ).json()["id"]
    return Duo(anna, bram, folder, node)


def login_with_2fa(office: Office, browsers: Any, account: Account, two: dict) -> Any:
    """Een context die met wachtwoord en een TOTP-code is ingelogd (§4.6 punt 1.1)."""
    context = browsers.new_context(service_workers="block")
    code, _ = totp_code(two["secret"], after_step=two["step"])
    r = context.request.post(
        f"{office.server.url}/api/v1/auth/login",
        data={"email": account.email, "password": account.password, "code": code},
    )
    assert r.ok, f"inloggen met 2FA gaf {r.status}: {r.text()}"
    return context


def uid(office: Office, account: Account) -> str:
    from app.office.oo import tokens

    return tokens.ds_user_id(office.settings(), uuid.UUID(account.id))


def versions(office: Office, account: Account, node: str) -> list[dict]:
    return office.server.call(account, "GET", f"/api/v1/files/{node}/versions", expect=200).json()


def text(office: Office, account: Account, node: str) -> str:
    r = office.server.call(account, "GET", f"/api/v1/files/{node}/content", expect=200)
    return docx_text(r.content)


def is_session(node: str) -> Callable[[Any], bool]:
    path = f"/api/v1/office/files/{node}/session"
    return lambda r: r.request.method == "POST" and r.url.split("?", 1)[0].endswith(path)


def ready(page: Page, seconds: float = READY_SECONDS) -> int:
    """Wacht tot de echte editor klaar is (de statusregel zegt Bewerken). Kwam hij niet klaar
    (A22: de pagina laadt de iframe zelf al één keer opnieuw, daarna "De editor kwam niet
    klaar" met Opnieuw laden), dan drukt de test één keer op Opnieuw laden, zoals een gebruiker.
    Een fout van de editor faalt meteen. Geeft hoe vaak er opnieuw geladen is."""
    status = page.locator(".editor-status")
    not_ready = page.get_by_text("De editor kwam niet klaar", exact=False)
    failed = page.get_by_text("De editor meldt een fout", exact=False)
    reloads = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if (status.text_content() or "").startswith("Bewerken"):
            return reloads
        if failed.count():
            raise AssertionError(f"de editor meldt een fout: {failed.first.text_content()}")
        if not_ready.count():
            if reloads:
                raise AssertionError("de editor kwam ook na opnieuw laden niet klaar")
            reloads += 1
            page.get_by_role("button", name="Opnieuw laden", exact=True).click()
        page.wait_for_timeout(500)
    raise AssertionError(f"de editor niet klaar binnen {seconds:.0f} s: {status.text_content()}")


def open_via_button(page: Page, d: Duo, node: str, name: str) -> tuple[str, int]:
    """De viewer van het bestand openen en op Bewerken drukken (§4.6 punt 1.2); geeft de key en
    hoe vaak er opnieuw geladen is."""
    page.goto(f"/map/{d.folder}?bestand={node}")
    viewer = page.get_by_role("dialog", name=name, exact=True)
    with page.expect_response(is_session(node)) as answer:
        viewer.get_by_role("button", name="Bewerken", exact=True).click()
    assert answer.value.status == 200, answer.value.text()
    expect(page).to_have_url(re.compile(rf"/bewerken/{node}$"))
    key = answer.value.json()["key"]
    # De key van de sessie is de key in de config die de editor krijgt (§4.6 punt 1.2)
    assert answer.value.json()["config"]["document"]["key"] == key
    reloads = ready(page)
    # api.js zet op zijn iframe onmousewheel=""; onder onze CSP een schending, die Chrome 156
    # meteen meldt (de bewaking vangt hem dan) en Chromium 141 pas als de handler gebruikt wordt.
    # De editorpagina laat zulke attributen weg (withoutInlineHandlers in office/loadApi.ts).
    handlers = page.evaluate(
        """() => [...document.querySelectorAll("iframe")]
            .flatMap((f) => [...f.attributes].map((a) => a.name))
            .filter((n) => n.toLowerCase().startsWith("on"))"""
    )
    assert handlers == [], f"inline handlers op de iframe van de editor: {handlers}"
    return key, reloads


# Schrijft op welke gebeurtenissen de iframe van DS aan de pagina meldt (onAppReady,
# onDocumentReady, onError, ...). api.js krijgt ze als postMessage met een JSON-tekst
# {"frameEditorId": …, "event": …} [gemeten in api.js van 10.0.0.1]. Alleen meeluisteren: de
# pagina en api.js merken er niets van. Alleen in het bovenste venster, niet in de iframes.
DS_EVENTS_SCRIPT = """
(() => {
  if (window.top !== window) return;
  const seen = [];
  Object.defineProperty(window, "__dsEvents", { value: seen });
  window.addEventListener("message", (e) => {
    let m = e.data;
    if (typeof m === "string") {
      try { m = JSON.parse(m); } catch { return; }
    }
    if (m && typeof m.event === "string" && typeof m.frameEditorId === "string") {
      seen.push(m.event);
    }
  });
})();
"""


def ds_events(page: Page) -> list[str]:
    """De gebeurtenissen die de editor in deze pagina meldde (zie DS_EVENTS_SCRIPT)."""
    return page.evaluate("() => [...(window.__dsEvents || [])]")


def info_users(office: Office, key: str) -> list[str]:
    """info op de key, getekend met het geheim van de run (zonder beheerpagina)."""

    async def ask(ds: Any) -> dict:
        return await ds.command("info", key=key)

    answer = office.ds(ask)
    assert answer.get("error") == 0, answer
    return [u for u in answer.get("users") or [] if isinstance(u, str)]


def test_twee_gebruikers_een_docx(real_office: Office, real_browsers, tmp_path_factory) -> None:
    """§4.6 punt 1: Anna en Bram (elk ingelogd met 2FA) bewerken tegelijk één docx in de echte
    editor. Nu opslaan door Bram geeft een docx met beide woorden; na het sluiten komt status 2,
    en er is precies één versie bij (de inhoud van vóór de sessie). Het bewijs komt ook in
    o2-echt-bewijs.json in de map met artefacten, naast de schermafdrukken
    (docs/fase-4/klaar.md)."""
    office = real_office
    d = duo(office)
    out = artifacts_dir(tmp_path_factory)
    proof: dict[str, Any] = {"test": "test_office_o2_real.py::test_twee_gebruikers_een_docx"}
    # De tokens voor de API-aanroepen van de test, vóór 2FA aangaat
    for account in (d.anna, d.bram):
        office.server.token(account)
    two_a = enable_2fa(office.server, d.anna)
    two_b = enable_2fa(office.server, d.bram)
    context_a = login_with_2fa(office, real_browsers, d.anna, two_a)
    context_b = login_with_2fa(office, real_browsers, d.bram, two_b)
    for context in (context_a, context_b):
        context.add_init_script(DS_EVENTS_SCRIPT)
    page_a = context_a.new_page()
    page_b = context_b.new_page()
    proof["ingelogd_met_2fa"] = True

    key, reloads_a = open_via_button(page_a, d, d.node, "verslag.docx")
    key_b, reloads_b = open_via_button(page_b, d, d.node, "verslag.docx")
    assert key_b == key  # dezelfde key in beide configs
    proof["key_anna"], proof["key_bram"] = key, key_b
    # Allebei onDocumentReady van de echte editor (ready() keek naar de statusregel, die pas na
    # onDocumentReady "Bewerken" zegt; dit is de gebeurtenis zelf)
    events_a, events_b = ds_events(page_a), ds_events(page_b)
    assert "onDocumentReady" in events_a, events_a
    assert "onDocumentReady" in events_b, events_b
    assert "onError" not in events_a and "onError" not in events_b, (events_a, events_b)
    proof["gebeurtenissen_anna"], proof["gebeurtenissen_bram"] = events_a, events_b
    expect(page_a.locator(".editor-status")).to_have_text("Bewerken met 1 ander", timeout=30_000)
    expect(page_b.locator(".editor-status")).to_have_text("Bewerken met 1 ander", timeout=30_000)
    users = wait_until(lambda: info_users(office, key), 30, "info met gebruikers")
    assert sorted(users) == sorted([uid(office, d.anna), uid(office, d.bram)]), users
    proof["info_gebruikers"] = sorted(users)
    proof["ds_uid_anna"], proof["ds_uid_bram"] = uid(office, d.anna), uid(office, d.bram)

    # De echte editor slikt soms de eerste toets na de klik in het document (gezien: "nna-…"
    # voor "Anna-…"); de test zoekt daarom het willekeurige deel
    word_a = f"anna{secrets.token_hex(4)}"
    word_b = f"bram{secrets.token_hex(4)}"
    type_in_editor(page_a, f"Anna {word_a}")
    type_in_editor(page_b, f"Bram {word_b}")
    page_a.wait_for_timeout(3000)  # de wijzigingen van Anna bij DS en bij Bram
    proof["woord_anna"], proof["woord_bram"] = word_a, word_b

    before = len(versions(office, d.anna, d.node))
    proof["versies_voor"] = before
    page_b.get_by_role("button", name="Nu opslaan", exact=True).click()
    saved_from = time.monotonic()
    saved = wait_until(
        lambda: (lambda t: t if word_a in t and word_b in t else None)(
            text(office, d.anna, d.node)
        ),
        60,
        "de docx met beide woorden na Nu opslaan",
    )
    proof["nu_opslaan_tot_beide_woorden_s"] = round(time.monotonic() - saved_from, 1)
    assert word_a in saved and word_b in saved
    assert len(versions(office, d.anna, d.node)) == before + 1
    expect(page_b.locator(".editor-status")).to_contain_text("Opgeslagen om", timeout=30_000)

    page_a.screenshot(path=str(out / "o2-echt-anna.png"))
    page_b.screenshot(path=str(out / "o2-echt-bram.png"))

    # Beide sluiten: terug naar waar ze vandaan kwamen (de viewer), binnen 30 s status 2
    for page in (page_a, page_b):
        page.get_by_role("button", name="Sluiten", exact=True).click()
        expect(page).to_have_url(re.compile(rf"/map/{d.folder}\?bestand={d.node}$"))
    closed_from = time.monotonic()

    def closed() -> bool:
        r = office.server.call(d.anna, "GET", f"/api/v1/office/files/{d.node}", expect=200)
        return r.json()["open"] is None

    wait_until(closed, 30, "status 2 en de sessie dicht")
    proof["sluiten_tot_status_2_s"] = round(time.monotonic() - closed_from, 1)
    # open is None: de sessie is dicht (sessions.live). Dicht als saved doet alleen status 2;
    # status 4 geeft no_changes (saving.py)
    assert session_row(office.server, key) == ("closed", "saved")
    proof["sessie"] = list(session_row(office.server, key))
    listed = versions(office, d.anna, d.node)
    assert len(listed) == before + 1, listed
    assert listed[0]["replaced_by"]["id"] in (d.anna.id, d.bram.id)
    proof["versies_na"] = len(listed)
    proof["replaced_by"] = "Anna" if listed[0]["replaced_by"]["id"] == d.anna.id else "Bram"
    final = text(office, d.anna, d.node)
    assert word_a in final and word_b in final, final
    proof["beide_woorden_in_de_docx"] = True
    old = office.server.call(
        d.anna,
        "GET",
        f"/api/v1/files/{d.node}/content",
        params={"version": listed[0]["id"]},
        expect=200,
    )
    assert word_a not in docx_text(old.content)
    # De versie is precies het bestand van vóór de sessie
    assert word_b not in docx_text(old.content)
    assert old.content == fixture("proef.docx")
    proof["versie_is_de_inhoud_van_voor_de_sessie"] = True
    proof["opnieuw_geladen"] = {"anna": reloads_a, "bram": reloads_b}
    (out / "o2-echt-bewijs.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(f"opnieuw geladen (A22): Anna {reloads_a}, Bram {reloads_b}", file=sys.stderr)
    print(f"bewijs: {json.dumps(proof)}", file=sys.stderr)


def test_herstart_van_de_app_terwijl_anna_bewerkt(real_office: Office, real_browsers) -> None:
    """§4.5 punt 4: de server op PID stoppen en weer starten terwijl Anna bewerkt, dan opnieuw
    laden: wat ze typte, staat in het bestand. Via het opslaan bij het stoppen, de pogingen van
    DS of het herstel (A21: alle uitkomsten zijn goed, zolang er niets kwijt is)."""
    office = real_office
    d = duo(office)
    page = real_browsers.context_as(d.anna, service_workers="block").new_page()
    key, _ = open_via_button(page, d, d.node, "verslag.docx")
    word = f"herstart{secrets.token_hex(4)}"
    type_in_editor(page, f"Na de herstart {word}")
    page.wait_for_timeout(3000)  # de wijziging bij DS

    office.server.restart()
    office.wait_state("ok", 60)
    wait_until(lambda: word in text(office, d.anna, d.node), 180, "de tekst na de herstart")

    with page.expect_response(is_session(d.node)) as answer:
        page.reload()
    assert answer.value.status in (200, 409), answer.value.text()
    ready(page)
    print(
        f"herstart: oude key {key}, nieuwe {answer.value.json().get('key')}, "
        f"versies {len(versions(office, d.anna, d.node))}",
        file=sys.stderr,
    )
    page.get_by_role("button", name="Sluiten", exact=True).click()
    assert word in text(office, d.anna, d.node)


@pytest.mark.parametrize("name", ["proef.xlsx", "proef.pptx"])
def test_werkblad_en_presentatie_openen(real_office: Office, real_browsers, name: str) -> None:
    """Een xlsx en een pptx openen in de echte editor via de app (A22: een pptx opende in de
    spike 4 van 22 keer niet via de proxy; de pagina laadt de iframe dan zelf opnieuw). Sluiten
    zonder wijziging: de sessie gaat dicht met status 4 (no_changes), geen versie erbij.

    Onderweg geeft /office/ nergens 404. De presentatie-editor heeft zijn ingebouwde ontwerpen:
    die komen uit sdkjs/slide/themes//themes.js (met '//'), dat via de app eerst 404 gaf
    (metingen deel 1, bevinding 1; o1.md, afwijking 19)."""
    office = real_office
    d = duo(office, name=name, data=fixture(name))
    page = real_browsers.context_as(d.anna, service_workers="block").new_page()
    missing: list[str] = []
    page.on(
        "response",
        lambda r: (
            missing.append(r.url)
            if r.status == 404 and urlsplit(r.url).path.startswith("/office/")
            else None
        ),
    )
    key, reloads = open_via_button(page, d, d.node, name)
    expect(page.locator("iframe[name=frameEditor]")).to_have_attribute("title", f"Editor: {name}")
    if name.endswith(".pptx"):
        frame = page.frame(name="frameEditor")
        assert frame is not None
        themes = wait_until(
            lambda: frame.evaluate(
                "() => (window.AscCommon && window.AscCommon.g_defaultThemes || []).length"
            ),
            30,
            "de ingebouwde ontwerpen van de presentatie-editor (g_defaultThemes)",
        )
        print(f"{name}: {themes} ingebouwde ontwerpen", file=sys.stderr)
    assert missing == [], f"404 op /office/ bij het openen: {missing}"
    page.get_by_role("button", name="Sluiten", exact=True).click()
    expect(page).to_have_url(re.compile(rf"/map/{d.folder}\?bestand={d.node}$"))

    def closed() -> bool:
        r = office.server.call(d.anna, "GET", f"/api/v1/office/files/{d.node}", expect=200)
        return r.json()["open"] is None

    # Status 4 komt na de wachttijd van DS; pas dan zegt "geen versie" iets
    wait_until(closed, 60, "status 4 en de sessie dicht")
    assert session_row(office.server, key) == ("closed", "no_changes")
    assert versions(office, d.anna, d.node) == []
    print(f"{name}: opnieuw geladen (A22) {reloads}", file=sys.stderr)
