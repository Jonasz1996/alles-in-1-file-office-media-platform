"""M10: het tabblad Media op /beheer in de browser (fase 5, §2 M10 van het bouwontwerp).

Met media aan en de lokale worker (de fixture media), tegen de echte routes van admin_media.py:

1. **Het tabblad met het toetsenbord** (docs/fase-5/toetsenbord-m10.md): Alt+8 op /beheer gaat
   naar /beheer/media; de blokken staan er (de worker, ffmpeg, wie er kijkt, de cache, de import,
   de instellingen, de belastingtest); met Tab naar Opruimen bij de voorbeeldbeelden, Enter opent
   de bevestiging (de focus op Annuleren), Esc sluit hem zonder iets te doen en de focus staat
   weer op de knop; daarna Tab naar Opruimen in de bevestiging en Enter: de toast, en de focus
   blijft op de knop. De belastingtest starten geeft in de harnas (zonder PLATFORM_MEDIA_LOADTEST)
   503 media_off met reden loadtest, en het blok zegt hoe hij aan gaat.
2. **Een sessie stoppen:** iemand kijkt een remux van 150 s; de beheerder ziet de sessie (wie, wat,
   hoe), stopt hem met het toetsenbord en bevestigt; de speler van de kijker stopt en zegt "De
   beheerder heeft het afspelen gestopt." (403 media_access_gone met reden stopped), vraagt geen
   nieuwe sessie, en er loopt daarna geen ffmpeg meer. De focus gaat naar de kop van het blok.
3. **Een import starten:** een bronmap in /data/import met een oud bestand en een dat nog
   geschreven kan worden (jonger dan MIN_AGE_S); de beheerder kiest de map, een andere eigenaar
   (dan de hoofdmap van die persoon) en Verplaatsen, en start. De voortgang komt live; het oude
   bestand staat daarna bij de eigenaar en is weg uit de bron, het nieuwe staat onder Overgeslagen
   met de reden "in gebruik" en blijft in de bron.

De import naar een eigen map met de mappenkiezer en kopiëren toetst test_media_m2.py (sinds M10
via /beheer; m2.md, afwijking 1).
"""

from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from typing import Any

import pytest
from playwright.sync_api import expect

from test_media_m6 import PLAYER, Plays, loop_mkv, playing_past, ready_state, setup, upload_path

STOP_TEXT = "De beheerder heeft het afspelen gestopt."
ADMIN_POLL_S = 5  # MediaTab ververst elke 5 s


def focused(page: Any) -> str:
    """De naam van wat de focus heeft: aria-label, anders de tekst."""
    return page.evaluate(
        """() => {
          const el = document.activeElement;
          if (!el) return "";
          return el.getAttribute("aria-label") || (el.textContent || "").trim();
        }"""
    )


def tab_until(page: Any, name: str | re.Pattern[str], presses: int = 80) -> None:
    """Tab tot het element met deze naam (aria-label of tekst) de focus heeft."""
    for _ in range(presses):
        page.keyboard.press("Tab")
        now = focused(page)
        if name.search(now) is not None if isinstance(name, re.Pattern) else now == name:
            return
    raise AssertionError(f"{name!r} niet bereikt met Tab; focus op {focused(page)!r}")


def admin_page(media: Any, media_browsers: Any) -> Any:
    page = media_browsers.context_as(media.admin).new_page()
    page.goto("/beheer/media")
    expect(page.locator("[data-media=worker-state]")).to_be_visible()
    return page


def children(media: Any, account: Any, folder: str) -> list[str]:
    r = media.server.call(account, "GET", f"/api/v1/files/{folder}/children", expect=200)
    return sorted(c["name"] for c in r.json()["children"])


# --- 1. het tabblad met het toetsenbord -----------------------------------------------------------


@pytest.mark.media
def test_het_tabblad_met_het_toetsenbord(media, media_browsers, ffmpeg_count):
    page = media_browsers.context_as(media.admin).new_page()
    page.goto("/beheer")
    tabs = page.get_by_role("navigation", name="Onderdelen van beheer")
    expect(tabs.get_by_role("link", name="Overzicht", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    # Alt+8 werkt pas als het tabblad er is (GET /api/v1/admin/media antwoordde, Beheer.tsx);
    # zonder dit wachten valt de toets soms vóór dat antwoord en doet hij terecht niets
    expect(tabs.get_by_role("link", name="Media", exact=True)).to_be_visible()
    page.keyboard.press("Alt+8")
    expect(page).to_have_url(re.compile(r"/beheer/media$"))
    expect(tabs.get_by_role("link", name="Media", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    # De kop met de staat van de worker (de lokale worker van de harnas: gezond)
    expect(page.locator("[data-media=worker-state]")).to_have_text("gezond", timeout=30_000)
    expect(page.locator("[data-media=worker-mode]")).to_contain_text("Lokaal, in de app")
    for heading in (
        "De worker", "ffmpeg", "Meting", "Wie er nu kijkt", "Cache", "Importeren",
        "Instellingen", "Belastingtest",
    ):  # fmt: skip
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()

    # Opruimen met het toetsenbord: Esc doet niets, daarna bevestigen
    button = page.get_by_role("button", name="Voorbeeldbeelden opruimen", exact=True)
    expect(button).to_be_visible()
    tab_until(page, "Voorbeeldbeelden opruimen")
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Voorbeeldbeelden opruimen?")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(button).to_be_focused()
    expect(page.locator(".toast-message", has_text="opgeruimd")).to_have_count(0)

    page.keyboard.press("Enter")
    expect(dialog).to_be_visible()
    page.keyboard.press("Tab")
    expect(dialog.get_by_role("button", name="Opruimen", exact=True)).to_be_focused()
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/api/v1/admin/media/cache/clear")
    ) as cleared:
        page.keyboard.press("Enter")
    assert cleared.value.status == 204, cleared.value.text()
    expect(page.locator(".toast-message", has_text="Voorbeeldbeelden: opgeruimd.")).to_be_visible()
    expect(button).to_be_focused()

    # De belastingtest staat in de harnas uit (geen PLATFORM_MEDIA_LOADTEST): starten met het
    # toetsenbord geeft 503 media_off met reden loadtest, en het blok zegt hoe hij aan gaat
    tab_until(page, "Belastingtest starten")
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/api/v1/admin/media/loadtest")
    ) as refused:
        page.keyboard.press("Enter")
    assert refused.value.status == 503, refused.value.text()
    assert refused.value.json()["detail"]["params"]["reason"] == "loadtest"
    off = page.get_by_role("alert").filter(has_text="De belastingtest staat uit op deze server")
    expect(off).to_be_visible()
    expect(page.get_by_role("button", name="Belastingtest starten")).to_have_count(0)


# --- 2. een sessie stoppen ------------------------------------------------------------------------


@pytest.mark.media
def test_een_sessie_stoppen(media, media_browsers, ffmpeg_count, tmp_path):
    owner, folder, context, page = setup(media, media_browsers, "M10 stoppen")
    plays = Plays(context)
    path = loop_mkv(tmp_path, 25)  # 150 s remux: de speler vraagt elke 2 s een segment
    node = upload_path(media, owner, folder, path)
    path.unlink()
    page.goto(f"/kijken/{node}")
    expect(page.locator(PLAYER)).to_be_visible()
    ready_state(page, "remux")
    playing_past(page, 1.0)

    admin = admin_page(media, media_browsers)
    label = f"{path.name} van {owner.display_name} stoppen"
    stop = admin.get_by_role("button", name=label, exact=True)
    expect(stop).to_be_visible(timeout=(ADMIN_POLL_S + 5) * 1000)
    row = admin.locator("[data-media=sessions] tr", has=stop)
    expect(row).to_contain_text(owner.display_name)
    expect(row).to_contain_text(path.name)
    expect(row).to_contain_text("ompakken")

    tab_until(admin, label)
    admin.keyboard.press("Enter")
    dialog = admin.get_by_role("dialog", name="Afspelen stoppen?")
    expect(dialog).to_contain_text(owner.display_name)
    admin.keyboard.press("Tab")
    expect(dialog.get_by_role("button", name="Stop", exact=True)).to_be_focused()
    with admin.expect_response(
        lambda r: r.request.method == "DELETE" and "/api/v1/admin/media/sessions/" in r.url
    ) as stopped:
        admin.keyboard.press("Enter")
    assert stopped.value.status == 204, stopped.value.text()
    done = f"Gestopt: {owner.display_name} krijgt het te zien."
    expect(admin.locator(".toast-message", has_text=done)).to_be_visible()
    expect(stop).to_have_count(0)
    # De knop is weg: de focus staat op de kop van het blok, niet op <body>
    expect(admin.get_by_role("heading", name="Wie er nu kijkt", exact=True)).to_be_focused()

    # De speler van de kijker stopt en zegt waarom; geen nieuwe sessie
    expect(page.locator(PLAYER)).to_have_attribute("data-state", "stopped", timeout=20_000)
    expect(page.locator(".player-overlay[role=alert]")).to_have_text(STOP_TEXT)
    assert page.evaluate("() => document.querySelector('.player-video').paused")
    page.wait_for_timeout(3000)
    assert len(plays.calls) == 1, plays.calls
    assert not ffmpeg_count.wait_zero(15), "de producent loopt nog na de stop"


# --- 3. een import starten ------------------------------------------------------------------------


@pytest.mark.media
def test_een_import_starten(media, media_browsers, ffmpeg_count):
    from tests.media import fixtures

    other = media.user(name=f"Importeur {uuid.uuid4().hex[:6]}")
    name = f"m10-{uuid.uuid4().hex[:8]}"
    source = media.server.data_dir / "import" / name
    source.mkdir(parents=True)
    try:
        old = time.time() - 600  # ouder dan MIN_AGE_S (60 s)
        shutil.copyfile(fixtures.make("vp9_opus.webm"), source / "Oud.webm")
        os.utime(source / "Oud.webm", (old, old))
        shutil.copyfile(fixtures.make("vp9_opus.webm"), source / "Nieuw.webm")  # net geschreven

        page = admin_page(media, media_browsers)
        form = page.locator("[data-media=import-form]")
        expect(form).to_be_visible()
        form.get_by_label("Bronmap", exact=True).select_option(name)
        expect(form.get_by_label("Bronmap", exact=True)).to_have_value(name)
        form.get_by_label("Eigenaar", exact=True).select_option(other.id)
        # Een ander: de import gaat in diens hoofdmap; kiezen kan alleen in je eigen bestanden
        expect(form.locator("[data-media=import-target]")).to_have_text(
            f"De hoofdmap van {other.display_name}"
        )
        expect(form.get_by_role("button", name="Kies een map", exact=True)).to_have_count(0)
        # Kopiëren is de standaard; ↓ kiest Verplaatsen (keuzerondjes)
        copy = form.get_by_role("radio", name=re.compile(r"^Kopiëren"))
        expect(copy).to_be_checked()
        copy.focus()
        page.keyboard.press("ArrowDown")
        expect(form.get_by_role("radio", name=re.compile(r"^Verplaatsen"))).to_be_checked()
        tab_until(page, "Importeren")
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/api/v1/admin/media/import")
        ) as started:
            page.keyboard.press("Enter")
        assert started.value.status == 202, started.value.text()
        expect(page.locator(".toast-message", has_text="De import is begonnen.")).to_be_visible()

        # De voortgang live (event media.import), tot klaar
        progress = page.locator("[data-media=import-progress]")
        expect(progress.locator("[data-media=import-state]")).to_have_text("klaar", timeout=30_000)
        expect(progress).to_contain_text(f"Nu: {name}")
        expect(progress).to_contain_text("1 overgeslagen")
        summary = progress.locator("summary", has_text="Overgeslagen")
        summary.focus()
        page.keyboard.press("Enter")
        expect(progress.locator("details li")).to_have_text(re.compile(r"Nieuw\.webm: in gebruik$"))

        # Verplaatst: bij de eigenaar, en weg uit de bron; het nieuwe bestand blijft staan
        assert "Oud.webm" in children(media, other, other.root_id)
        assert sorted(p.name for p in source.iterdir()) == ["Nieuw.webm"]
    finally:
        shutil.rmtree(source, ignore_errors=True)
