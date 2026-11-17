"""De echte editor in Chromium, tegen het geïnstalleerde platform (stap S van
infra/single/tests/test_install_update.sh).

Inloggen met wachtwoord en 2FA-code (de login van install_api.py), het bestand openen in de
viewer, op Bewerken drukken, wachten tot de editor klaar is en tekst typen zoals een gebruiker
(type_in_editor uit het harnas). Daarna schrijft het script het getypte woord in <map>/getypt en
wacht het tot <map>/sluit bestaat: intussen stopt de test het platform met de editor open. Dan
een schermafdruk en de browser dicht. Uit api/:

    INSTALL_API_URL=http://127.0.0.1:<poort> INSTALL_API_STATE=<werkmap>/sessie.json \\
      uv run --with playwright python ../e2e/install_editor.py <map> <map-id> <bestand-id> <naam>
"""

from __future__ import annotations

import secrets
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

import install_api
from conftest import launch_chromium
from office_harness import type_in_editor

READY_SECONDS = 180
# Zo lang wacht het hooguit op de test (stoppen, kijken)
CLOSE_SECONDS = 900


def ready(page: Any) -> int:
    """Wacht tot de statusregel Bewerken zegt. Kwam de editor niet klaar (A22: de pagina laadt
    de iframe zelf al één keer opnieuw), dan één keer Opnieuw laden, zoals een gebruiker (net
    als test_office_o2_real.py). Geeft hoe vaak er opnieuw geladen is."""
    status = page.locator(".editor-status")
    not_ready = page.get_by_text("De editor kwam niet klaar", exact=False)
    failed = page.get_by_text("De editor meldt een fout", exact=False)
    reloads = 0
    end = time.monotonic() + READY_SECONDS
    while time.monotonic() < end:
        if (status.text_content() or "").startswith("Bewerken"):
            return reloads
        if failed.count():
            raise SystemExit(f"de editor meldt een fout: {failed.first.text_content()}")
        if not_ready.count():
            if reloads:
                raise SystemExit("de editor kwam ook na opnieuw laden niet klaar")
            reloads += 1
            page.get_by_role("button", name="Opnieuw laden", exact=True).click()
        page.wait_for_timeout(500)
    raise SystemExit(f"de editor niet klaar binnen {READY_SECONDS} s: {status.text_content()}")


def main(out: Path, folder: str, node: str, name: str) -> int:
    state = install_api.load()
    base = install_api.base()
    with sync_playwright() as pw:
        browser = launch_chromium(pw.chromium)
        try:
            context = browser.new_context(
                base_url=base,
                locale="nl-NL",
                timezone_id="Europe/Amsterdam",
                viewport={"width": 1280, "height": 800},
                service_workers="block",
            )
            context.set_default_timeout(30_000)
            login = {
                "email": state["email"],
                "password": state["password"],
                "code": install_api.next_code(state),
            }
            r = context.request.post(f"{base}/api/v1/auth/login", data=login)
            if not r.ok:
                print(f"inloggen met 2FA gaf {r.status}", file=sys.stderr)
                return 1
            page = context.new_page()
            page.goto(f"/map/{folder}?bestand={node}")
            viewer = page.get_by_role("dialog", name=name, exact=True)
            path = f"/api/v1/office/files/{node}/session"

            def is_session(r: Any) -> bool:
                return r.request.method == "POST" and r.url.split("?", 1)[0].endswith(path)

            with page.expect_response(is_session) as answer:
                viewer.get_by_role("button", name="Bewerken", exact=True).click()
            if answer.value.status != 200:
                print(f"de sessie gaf {answer.value.status}", file=sys.stderr)
                return 1
            reloads = ready(page)
            # De echte editor laat soms de eerste toets vallen (o2.md, afwijking 32): de test
            # zoekt dus alleen het willekeurige woord
            word = f"gestopt{secrets.token_hex(4)}"
            type_in_editor(page, f"Voor de stop {word}")
            page.wait_for_timeout(3000)  # de wijziging bij DS
            page.screenshot(path=str(out / "editor-open.png"))
            (out / "getypt").write_text(word)
            print(f"getypt: {word}; opnieuw geladen (A22): {reloads}")
            end = time.monotonic() + CLOSE_SECONDS
            while not (out / "sluit").exists():
                if time.monotonic() > end:
                    print("de test zei niet op tijd dat het klaar was", file=sys.stderr)
                    return 1
                time.sleep(0.5)
            page.screenshot(path=str(out / "editor-na-de-stop.png"))
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), *sys.argv[2:5]))
