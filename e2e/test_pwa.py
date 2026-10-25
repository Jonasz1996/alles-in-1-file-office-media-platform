"""De PWA (P1): de service worker neemt de pagina over, offline werkt de schil (met de balk) en
een lazy chunk dat je al eens opende, een nieuwe build geeft "Nieuwe versie beschikbaar" en
"Vernieuwen" (of Shift+R) laadt hem, en het manifest en de iconen zijn op te halen.

Elke test heeft een eigen server met een eigen kopie van web/dist: die kan hij stoppen (offline)
of aanpassen (een nieuwe build) zonder de andere tests te raken. 127.0.0.1 is net als localhost
een secure context, dus de service worker registreert hier.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

from conftest import WEB_DIST, Account, Browsers, Server, create_user, make_admin

pytestmark = pytest.mark.p1

CONTROLLED_JS = "() => !!navigator.serviceWorker && !!navigator.serviceWorker.controller"
CACHES_JS = "() => caches.keys()"
BUILD_JS = r"""
() => new Promise((resolve) => {
  const channel = new MessageChannel();
  channel.port1.onmessage = (e) => resolve(e.data.build);
  navigator.serviceWorker.controller.postMessage({ type: "build" }, [channel.port2]);
})
"""
UPDATE_TEXT = "Nieuwe versie beschikbaar"
UPDATE_JS = "() => navigator.serviceWorker.getRegistration().then((r) => r.update())"


class PwaSite:
    def __init__(self, base: Path, browsers: Browsers):
        self.dist = base / "dist"
        shutil.copytree(WEB_DIST, self.dist)
        self.server = Server(base / "data", web_dist=self.dist)
        self.server.start()
        self.admin = make_admin(self.server)
        self.browsers = browsers

    def context(self, account: Account | None = None) -> Any:
        context = self.browsers.new_context(base_url=self.server.url)
        if account is not None:
            r = context.request.post(
                f"{self.server.url}/api/v1/auth/login",
                data={"email": account.email, "password": account.password},
            )
            assert r.ok, r.text()
        return context

    def user(self) -> Account:
        return create_user(self.server, self.admin, name="Offline")

    def rebuild(self, marker: str) -> str:
        """Doet alsof er een nieuwe build is uitgerold: een andere build-id in sw.js (dus nieuwe
        caches) en een herkenbare index.html. Geeft de nieuwe build-id."""
        sw = self.dist / "sw.js"
        code = sw.read_text()
        old = re.search(r"shell-([0-9a-f]{12})", code)
        assert old, "geen build-id in sw.js"
        new = marker.encode().hex()[:12].ljust(12, "0")
        sw.write_text(code.replace(old.group(1), new))
        index = self.dist / "index.html"
        html = index.read_text()
        html = re.sub(r'<meta name="e2e-build"[^>]*>', "", html)
        meta = f'<meta name="e2e-build" content="{marker}">'
        index.write_text(html.replace("</head>", f"{meta}</head>"))
        for f in (sw, index):
            for ext in (".br", ".gz"):
                f.with_name(f.name + ext).unlink(missing_ok=True)
        return new

    def stop(self) -> None:
        self.server.stop()


@pytest.fixture
def site(tmp_path: Path, browsers: Browsers) -> Iterator[PwaSite]:
    s = PwaSite(tmp_path, browsers)
    try:
        yield s
    finally:
        s.stop()


def toasts(page) -> Any:
    return page.get_by_role("region", name="Meldingen")


def wait_controlled(page) -> None:
    # Registreren gebeurt pas na load, het meetpunt van de pagina en een rustig moment
    page.wait_for_function(CONTROLLED_JS, timeout=30_000)


def test_service_worker_neemt_over_en_manifest(site: PwaSite):
    page = site.context().new_page()
    page.goto("/")
    expect(page.get_by_role("heading", name="Inloggen", exact=True)).to_be_visible()
    wait_controlled(page)
    names = page.evaluate(CACHES_JS)
    assert any(n.startswith("platform-shell-") for n in names), names
    # De schil staat in de precache
    cached = page.evaluate(
        """async () => {
          const out = {};
          for (const u of ["/index.html", "/boot.js", "/manifest.webmanifest", "/icons/icon.svg"]) {
            out[u] = !!(await caches.match(u));
          }
          return out;
        }"""
    )
    assert all(cached.values()), cached

    # Manifest en iconen
    href = page.locator('link[rel="manifest"]').get_attribute("href")
    assert href == "/manifest.webmanifest"
    r = page.request.get(href)
    assert r.ok
    assert r.headers["content-type"].startswith("application/manifest+json")
    manifest = r.json()
    assert manifest["name"] == "Platform"
    assert manifest["start_url"] == "/" and manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["lang"] == "nl"
    assert {s["url"] for s in manifest["shortcuts"]} == {"/gedeeld", "/zoeken"}
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in manifest["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in manifest["icons"]:
        r = page.request.get(icon["src"])
        assert r.ok, icon["src"]
        assert r.headers["content-type"] == icon["type"] or r.headers["content-type"].startswith(
            icon["type"]
        )
    for path in ("/favicon.ico", "/favicon.svg", "/icons/apple-touch-icon.png"):
        assert page.request.get(path).ok, path
    # De worker zelf: geen import of export, en nooit uit de HTTP-cache
    r = page.request.get("/sw.js")
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "no-cache"
    assert not re.search(r"(^|[;}])\s*(import|export)\b\s*[\w{*\"']", r.text())


def test_offline_schil_balk_en_lazy_chunk(site: PwaSite):
    user = site.user()
    context = site.context(user)
    page = context.new_page()
    page.goto("/")
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()
    wait_controlled(page)
    # Nu de worker de pagina bedient: een lazy chunk openen (de prullenbak)
    page.get_by_role("link", name="Prullenbak", exact=True).click()
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    chunks = page.evaluate(
        "() => performance.getEntriesByType('resource').map((r) => r.name)"
        ".filter((n) => /\\/assets\\/Trash-[^/]+\\.js$/.test(n))"
    )
    assert chunks, "geen chunk van de prullenbak geladen"

    # Offline: de server weg en de browser offline
    site.stop()
    context.set_offline(True)
    page.reload()
    # De schil uit de cache: het scherm "Geen verbinding" van de app plus de offline-balk
    expect(page.get_by_role("heading", name="Geen verbinding", exact=True)).to_be_visible()
    banner = page.get_by_role("status").filter(has_text="Je bent offline")
    expect(banner).to_be_visible()
    # De stylesheet kwam ook uit de cache (anders geen opmaak)
    assert page.evaluate("getComputedStyle(document.body).margin") == "0px"
    # Het lazy chunk van daarnet laadt ook zonder server
    ok = page.evaluate("(url) => import(url).then(() => true, (e) => String(e))", chunks[0])
    assert ok is True, ok
    # Een chunk dat nooit geopend is, niet (bewijst dat dit echt offline is)
    never = page.evaluate(
        "(u) => fetch(u).then((r) => r.status, () => 'weg')", "/assets/bestaat-niet.js"
    )
    assert never == "weg"
    context.set_offline(False)


def test_nieuwe_versie_en_vernieuwen(site: PwaSite):
    user = site.user()
    context = site.context(user)
    page = context.new_page()
    page.goto("/")
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()
    wait_controlled(page)
    first = page.evaluate(BUILD_JS)

    # Ronde 1: een nieuwe build, de melding, en "Vernieuwen" met de muis
    second = site.rebuild("twee")
    assert second != first
    page.evaluate(UPDATE_JS)
    expect(toasts(page).get_by_text(UPDATE_TEXT, exact=True)).to_be_visible(timeout=30_000)
    with page.expect_event("load"):
        toasts(page).get_by_role("button", name="Vernieuwen", exact=True).click()
    expect(page.locator('meta[name="e2e-build"]')).to_have_attribute("content", "twee")
    page.wait_for_function(CONTROLLED_JS)
    assert page.evaluate(BUILD_JS) == second
    names = page.evaluate(CACHES_JS)
    assert f"platform-shell-{second}" in names
    assert f"platform-shell-{first}" not in names, names  # de oude cache is opgeruimd
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()

    # Ronde 2: nog een build, nu met het toetsenbord (Shift+R)
    third = site.rebuild("drie")
    page.evaluate(UPDATE_JS)
    expect(toasts(page).get_by_text(UPDATE_TEXT, exact=True)).to_be_visible(timeout=30_000)
    page.locator("body").focus()
    with page.expect_event("load"):
        page.keyboard.press("Shift+R")
    expect(page.locator('meta[name="e2e-build"]')).to_have_attribute("content", "drie")
    page.wait_for_function(CONTROLLED_JS)
    assert page.evaluate(BUILD_JS) == third
