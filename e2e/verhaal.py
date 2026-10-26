"""Het harnas van het verhaal van de integratie (e2e/test_integratie.py, ontwerp §5.4 "Full
e2e"): één eigen server vanaf de eerste start, met de productie-build op localhost.

- Een eigen kopie van web/dist (de PWA-stap doet daarin een nieuwe build) en een eigen datamap.
- PLATFORM_DOWNLOAD_TOKEN_MINUTES=1 voor de hele run: een PDF blijft langer open dan zijn token.
- 2FA is verplicht (zoals na de eerste start): iedereen zet het aan, en elke login (API of
  browser) geeft een code mee: een TOTP-code van een nog niet gebruikte stap, anders een
  herstelcode. Persoon houdt dat bij.
- De contexts blijven over de stappen heen open (de PDF uit de viewerstap, de sessie van Bram
  die later geblokkeerd wordt); de bewaking (CSP, i18n, fouten) wordt na elke stap geleegd en
  laat die stap falen.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import Page

from conftest import WEB_DIST, Account, Browsers, Server

API = "/api/v1"
ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"  # zonder de kopregel
MOD = "ControlOrMeta"
# Zo lang gaat een access-token hier mee voordat het verhaal opnieuw inlogt (server: 15 min)
TOKEN_SECONDS = 10 * 60


@dataclass
class Persoon:
    """Iemand in het verhaal, met zijn 2FA: elke login kost een code."""

    naam: str
    email: str
    password: str
    rol: str
    account: Account | None = None
    secret: str | None = None
    last_step: int = 0
    recovery: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        assert self.account, f"{self.naam} bestaat nog niet"
        return self.account.id

    @property
    def root(self) -> str:
        assert self.account and self.account.root_id, f"{self.naam} heeft geen hoofdmap"
        return self.account.root_id

    def code(self) -> str | None:
        """Een code voor de volgende login: TOTP van een stap die nog niet gebruikt is (de
        server neemt één stap ervoor en erna ook aan), anders een herstelcode."""
        if self.secret is None:
            return None
        from app import totp

        now = totp.current_step()
        step = max(now - totp.WINDOW, self.last_step + 1)
        if step <= now + totp.WINDOW:
            self.last_step = step
            return totp.code_at(self.secret, step)
        assert self.recovery, f"{self.naam} heeft geen herstelcodes meer"
        return self.recovery.pop()


class Verhaal:
    """De server, de mensen en de browsercontexts van het hele verhaal."""

    def __init__(self, base: Path, browser: Any):
        self.dist = base / "dist"
        shutil.copytree(WEB_DIST, self.dist)
        self.server = Server(
            base / "data", web_dist=self.dist, env={"PLATFORM_DOWNLOAD_TOKEN_MINUTES": "1"}
        )
        self.server.start()
        # localhost (niet 127.0.0.1): daar registreert de service worker volgens het ontwerp
        self.base = f"http://localhost:{self.server.port}"
        self.browsers = Browsers(browser, self.server)
        self.mensen: dict[str, Persoon] = {}
        # Wat een stap klaarzet voor een latere (ids, pagina's, tijden)
        self.stand: dict[str, Any] = {}
        self.stappen: set[str] = set()

    @property
    def data_dir(self) -> Path:
        return self.server.data_dir

    def stop(self) -> list[str]:
        problems = self.browsers.close_all()
        self.server.stop()
        return problems

    def nodig(self, *stappen: str) -> None:
        """Deze stap bouwt voort op die stappen: zijn ze niet gelukt, dan heeft deze geen zin."""
        missing = [s for s in stappen if s not in self.stappen]
        assert not missing, f"eerdere stap mislukt of niet gedraaid: {', '.join(missing)}"

    def problemen(self) -> list[str]:
        """Wat de bewaking sinds de vorige keer zag, in alle open contexts."""
        out: list[str] = []
        for context, guard in list(self.browsers._open):
            out.extend(guard.take())
            del context
        return out

    # --- inloggen en de API ---

    def login(self, persoon: Persoon) -> dict[str, Any]:
        body: dict[str, Any] = {"email": persoon.email, "password": persoon.password}
        code = persoon.code()
        if code:
            body["code"] = code
        r = self.server.request("POST", f"{API}/auth/login", json=body, expect=200)
        data = r.json()
        if persoon.account is None:
            user = data["user"]
            persoon.account = Account(
                user["id"], user["email"], persoon.password, persoon.naam, user["role"],
                user["root_id"],
            )  # fmt: skip
        persoon.account.token = data["access_token"]
        persoon.account.token_at = time.monotonic()
        return data

    def token(self, persoon: Persoon) -> str:
        account = persoon.account
        assert account is not None, f"{persoon.naam} bestaat nog niet"
        if account.token is None or time.monotonic() - account.token_at > TOKEN_SECONDS:
            self.login(persoon)
        assert account.token
        return account.token

    def api(self, persoon: Persoon, method: str, path: str, **kw: Any) -> httpx.Response:
        return self.server.request(method, path, token=self.token(persoon), **kw)

    def context(self, persoon: Persoon | None = None, **options: Any) -> Any:
        """Een context op localhost; met persoon ingelogd (refresh-cookie via context.request,
        met een 2FA-code)."""
        context = self.browsers.new_context(base_url=self.base, **options)
        if persoon is not None:
            data: dict[str, Any] = {"email": persoon.email, "password": persoon.password}
            code = persoon.code()
            if code:
                data["code"] = code
            r = context.request.post(f"{self.base}{API}/auth/login", data=data)
            assert r.ok, f"inloggen als {persoon.email} gaf {r.status}: {r.text()}"
        return context

    def page(self, persoon: Persoon | None = None, path: str | None = None, **options: Any) -> Page:
        page = self.context(persoon, **options).new_page()
        if path is not None:
            page.goto(path)
        return page

    # --- bestanden ---

    def mkdir(self, persoon: Persoon, parent: str, name: str) -> str:
        r = self.api(
            persoon, "POST", f"{API}/files/{parent}/folders", json={"name": name}, expect=201
        )
        return r.json()["id"]

    def put(self, persoon: Persoon, parent: str, name: str, body: bytes, **params: Any) -> str:
        r = self.api(
            persoon,
            "PUT",
            f"{API}/files/{parent}/upload",
            params={"name": name, **params},
            content=body,
            expect=201,
        )
        return r.json()["id"]

    def children(self, persoon: Persoon, folder: str) -> dict[str, dict[str, Any]]:
        """De inhoud van een map op naam (alle pagina's)."""
        out: dict[str, dict[str, Any]] = {}
        after: str | None = None
        while True:
            params = {"limit": 1000, **({"after": after} if after else {})}
            data = self.api(
                persoon, "GET", f"{API}/files/{folder}/children", params=params, expect=200
            ).json()
            out.update({c["name"]: c for c in data["children"]})
            after = data.get("next")
            if not after:
                return out

    def blob_path(self, body: bytes) -> Path:
        """Waar de inhoud als blob op schijf staat (app.storage.BlobStore)."""
        from app.storage import BlobStore

        return BlobStore(self.data_dir / "blobs").path(hashlib.sha256(body).hexdigest())

    def wait_for_previews(self, persoon: Persoon, ids: list[str], seconds: float = 180) -> dict:
        """Tot elke preview klaar is (of mislukt), zoals in test_p4_viewer."""
        deadline = time.monotonic() + seconds
        while True:
            got = self.api(persoon, "POST", f"{API}/previews", json={"node_ids": ids}, expect=200)
            previews = got.json()
            if all(previews.get(i, {}).get("state") not in (None, "pending") for i in ids):
                return previews
            if time.monotonic() > deadline:
                raise AssertionError(f"previews niet klaar: {previews}")
            time.sleep(0.5)


def wait_until(check: Callable[[], Any], seconds: float = 30, what: str = "voorwaarde") -> Any:
    """Wacht (synchroon) tot check() iets waars geeft; anders faalt de stap."""
    end = time.monotonic() + seconds
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > end:
            raise AssertionError(f"wachten op {what} duurde te lang")
        time.sleep(0.2)


# --- de pagina ---


def row(page: Page, name: str) -> Any:
    return page.locator(ROWS).filter(has=page.get_by_text(name, exact=True))


def pick(page: Page, name: str, modifiers: list[str] | None = None) -> None:
    """Een rij selecteren met een klik naast de naam (de naam zelf opent)."""
    row(page, name).locator(".cell-date").click(modifiers=modifiers or [])


def enter_list(page: Page) -> None:
    """De lijst in (één tabstop: de rij met tabindex 0)."""
    page.locator(f'{ROWS}[tabindex="0"]').focus()


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


def toasts(page: Page) -> Any:
    return page.get_by_role("region", name=re.compile("^(Meldingen|Notifications)$"))


def toast(page: Page, text: str | re.Pattern[str]) -> Any:
    return toasts(page).get_by_text(text)


def blur(page: Page) -> None:
    """De focus uit een knop of veld, zodat een sneltoets van één letter werkt."""
    page.evaluate("() => document.activeElement && document.activeElement.blur()")


def zip_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        return sorted(z.namelist())
