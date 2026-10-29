"""M2: bibliotheken, de scanner en de import in de browser (fase 5, §2 M2 van het bouwontwerp).

Met media aan, de lokale worker en de tik van de scanner op 0,5 s (§3.6). Alles in de browser met
het toetsenbord (docs/fase-5/toetsenbord-m2.md); alleen het binnenkomen in een lijst of raster
gaat met focus() (één tabstop), en een nieuwe pagina met goto:

- een map met fixtures wordt een bibliotheek via het menu van de map (Shift+F10) en de dialoog
  (het soort met de pijltjes, Enter); de items verschijnen vanzelf met de titels uit de namen;
  in het raster de pijltjes, Home en End; Enter opent een film, Afspelen en "Verder op 0:03"
  gaan naar /kijken (met ?t=), waar de speler van M6 de titel van het item toont en speelt (K
  pauzeert, Esc gaat terug naar het item; sinds het samenvoegen van golf 3); een upload daarna
  verschijnt binnen de grens van Klaar als;
- een bibliotheek met series: de rij Net toegevoegd met ← en →, de serie met haar seizoen en
  afleveringen, Afspelen S01E01, en Opnieuw scannen;
- Nieuwe bibliotheek op /media (de lege start): de mappenkiezer en de dialoog met het
  toetsenbord; daarna een import uit /data/import met `app.cli media-import` (de knop op /beheer
  is van M10): de items verschijnen in de bibliotheek op de doelmap, de bron blijft (kopiëren),
  en de taak media.import eindigt als done.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

from media_harness import run_async

ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"
# Klaar als: een item verschijnt binnen 35 s na de upload (met de tik van 30 s; hier 0,5 s)
APPEAR_S = 35.0
# De speler (M6): play van een item, en "speelt hij, voorbij t"
PLAY_RE = re.compile(r"/api/v1/media/[^/]+/play$")
PLAYING_JS = """(t) => {
  const v = document.querySelector(".player-video");
  return !!v && !v.error && !v.paused && v.readyState >= 3 && v.currentTime >= t;
}"""


def focused_text(page: Any) -> str:
    return page.evaluate(
        "() => (document.activeElement && document.activeElement.textContent) || ''"
    )


def expect_focus_text(page: Any, text: str) -> None:
    page.wait_for_function(
        "(t) => !!document.activeElement && (document.activeElement.textContent || '').includes(t)",
        arg=text,
        timeout=5000,
    )


def tab_to(page: Any, selector: str, presses: int = 12) -> None:
    """Tab tot het element van selector de focus heeft (hooguit presses keer)."""
    for _ in range(presses):
        page.keyboard.press("Tab")
        if page.evaluate(
            "(s) => !!document.activeElement && document.activeElement.matches(s)", selector
        ):
            return
    raise AssertionError(f"{selector} niet bereikt met Tab; focus op {focused_text(page)!r}")


def menu_to(page: Any, label: str, presses: int = 30) -> None:
    """In een open menu ↓ tot het item met deze naam de focus heeft."""
    for _ in range(presses):
        if focused_text(page).strip() == label:
            return
        page.keyboard.press("ArrowDown")
    raise AssertionError(f"{label} niet gevonden in het menu")


def libraries(media: Any, account: Any) -> list[dict[str, Any]]:
    return media.server.call(account, "GET", "/api/v1/media/libraries", expect=200).json()


def items(media: Any, account: Any, library_id: str, **params: Any) -> list[dict[str, Any]]:
    r = media.server.call(
        account, "GET", f"/api/v1/media/libraries/{library_id}/items", params=params, expect=200
    )
    return r.json()["items"]


def wait_items(media: Any, account: Any, library_id: str, count: int, **params: Any) -> list[dict]:
    end = time.monotonic() + APPEAR_S
    found: list[dict[str, Any]] = []
    while time.monotonic() < end:
        found = items(media, account, library_id, **params)
        if len(found) >= count:
            return found
        time.sleep(0.25)
    raise AssertionError(f"na {APPEAR_S} s {len(found)} van de {count} items: {found}")


def make_library(media: Any, account: Any, root_id: str, kind: str) -> dict[str, Any]:
    r = media.server.call(
        account,
        "POST",
        "/api/v1/media/libraries",
        json={"root_id": root_id, "kind": kind},
        expect=201,
    )
    return r.json()


def set_progress(
    media: Any, user_id: str, node_id: str, position_ms: int, duration_ms: int
) -> None:
    """De stand van kijken in playback_state (PUT …/progress is van M7)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app import db
    from app.models import PlaybackState

    settings = media.settings()

    async def go() -> None:
        engine = db.get_engine(settings)
        try:
            async with async_sessionmaker(engine)() as session:
                session.add(
                    PlaybackState(
                        user_id=uuid.UUID(user_id),
                        node_id=uuid.UUID(node_id),
                        position_ms=position_ms,
                        duration_ms=duration_ms,
                    )  # fmt: skip
                )
                await session.commit()
        finally:
            await engine.dispose()
            db._engine.cache_clear()

    run_async(go)


def import_jobs(media: Any) -> list[tuple[str, str]]:
    uri = f"file:{media.server.data_dir / 'platform.db'}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        return conn.execute(
            "SELECT status, payload FROM job WHERE type = 'media.import'"
        ).fetchall()


# --- de tests -------------------------------------------------------------------------------------


@pytest.mark.media
def test_bibliotheek_maken_en_kijken_met_het_toetsenbord(media, media_browsers, ffmpeg_count):
    owner = media.user()
    films = media.folder(owner, "Films")
    matrix = media.upload(
        owner, "vp9_opus.webm", films, name="The.Matrix.1999.1080p.BluRay.x264.webm"
    )
    media.upload(owner, "h264_aac.mp4", films, name="Alien (1979).mp4")

    page = media_browsers.context_as(owner).new_page()
    page.goto("/")
    # De haak is er pas als media aan staat (de status): het menu-item Media staat er dan
    expect(page.get_by_role("link", name="Media", exact=True)).to_be_visible()
    page.locator(ROWS).first.wait_for()
    page.locator(f'{ROWS}[tabindex="0"]').focus()
    page.keyboard.press("Shift+F10")
    menu = page.get_by_role("menu", name="Acties voor Films")
    expect(menu).to_be_visible()
    menu_to(page, "Als bibliotheek gebruiken")
    page.keyboard.press("Enter")

    dialog = page.get_by_role("dialog", name="Als bibliotheek gebruiken")
    expect(dialog).to_be_visible()
    movies = dialog.get_by_role("radio", name=re.compile(r"^Films"))
    expect(movies).to_be_focused()  # het voorstel uit de naam van de map
    expect(movies).to_be_checked()
    page.keyboard.press("ArrowDown")
    expect(dialog.get_by_role("radio", name=re.compile(r"^Series"))).to_be_checked()
    page.keyboard.press("ArrowUp")
    expect(movies).to_be_checked()
    page.keyboard.press("Tab")
    name = dialog.get_by_label("Naam", exact=True)
    expect(name).to_be_focused()
    expect(name).to_have_value("Films")
    created = time.monotonic()
    page.keyboard.press("Enter")
    expect(
        page.locator(".toast-message", has_text="Bibliotheek Films gemaakt. De scan loopt.")
    ).to_be_visible()
    expect(dialog).to_have_count(0)
    [library] = libraries(media, owner)
    assert library["kind"] == "movies" and library["root_id"] == films and library["mine"]

    # /media: de bibliotheek als link; Enter opent het raster
    page.goto("/media")
    link = page.get_by_role("link", name=re.compile(r"^Films"))
    expect(link).to_be_visible()
    link.focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(f"/media/bibliotheek/{library['id']}$"))
    grid = page.get_by_role("grid", name="Inhoud van Films")
    titles = grid.locator(".media-card-title")
    expect(titles).to_have_text(["Alien", "The Matrix"], timeout=APPEAR_S * 1000)
    appeared = time.monotonic() - created
    assert appeared <= APPEAR_S, appeared
    expect(grid.locator(".media-card-sub")).to_have_text(["1979", "1999"])

    # Het raster: één tabstop, de pijltjes, Home en End
    grid.locator('[tabindex="0"]').focus()
    expect_focus_text(page, "Alien")
    page.keyboard.press("ArrowRight")
    expect_focus_text(page, "The Matrix")
    page.keyboard.press("Home")
    expect_focus_text(page, "Alien")
    page.keyboard.press("End")
    expect_focus_text(page, "The Matrix")

    # Een upload daarna verschijnt vanzelf (de scanner en media.item; de lijst laadt opnieuw)
    uploaded = time.monotonic()
    media.upload(owner, "vp9_opus.webm", films, name="Heat.1995.webm")
    expect(titles).to_have_text(["Alien", "Heat", "The Matrix"], timeout=APPEAR_S * 1000)
    assert time.monotonic() - uploaded <= APPEAR_S

    # Enter opent de film; Afspelen gaat naar /kijken
    grid.locator('[tabindex="0"]').focus()
    page.keyboard.press("End")
    expect_focus_text(page, "The Matrix")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(f"/media/item/{matrix.id}$"))
    expect(page.get_by_role("heading", level=1, name="The Matrix")).to_be_visible()
    expect(page.locator(".media-item .media-meta")).to_contain_text("1999")
    expect(page.locator("[data-resume]")).to_have_count(0)
    tab_to(page, "[data-play]")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(f"/kijken/{matrix.id}$"))
    # De speler van M6 (samenvoegen golf 3; §4.3 #9): de titel uit het item van M2, spelen,
    # K pauzeert, en Esc gaat terug naar het item
    expect(page.locator(".player")).to_have_attribute("data-state", "ready", timeout=30_000)
    expect(page.locator(".player-title")).to_have_text("The Matrix")
    page.wait_for_function(PLAYING_JS, arg=0.3, timeout=20_000)
    page.keyboard.press("k")
    page.wait_for_function("() => document.querySelector('.player-video').paused")
    page.keyboard.press("Escape")
    expect(page).to_have_url(re.compile(f"/media/item/{matrix.id}$"))

    # Verder op 0:03 (de stand van M7 hier als rij in de database)
    set_progress(media, owner.id, matrix.id, 3_400, 6_000)
    page.goto(f"/media/item/{matrix.id}")
    resume = page.get_by_role("link", name="Verder op 0:03")
    expect(resume).to_be_visible()
    tab_to(page, "[data-resume]")
    with page.expect_request(PLAY_RE) as asked:
        page.keyboard.press("Enter")
    assert asked.value.post_data_json["start_ms"] == 3_000, asked.value.post_data_json
    expect(page).to_have_url(re.compile(f"/kijken/{matrix.id}\\?t=3$"))
    page.wait_for_function(PLAYING_JS, arg=3.1, timeout=20_000)


@pytest.mark.media
def test_series_de_start_en_opnieuw_scannen(media, media_browsers, ffmpeg_count):
    owner = media.user()
    series = media.folder(owner, "Series")
    show = media.folder(owner, "Breaking Bad", series)
    season = media.folder(owner, "Season 1", show)
    first = media.upload(owner, "vp9_opus.webm", season, name="Breaking.Bad.S01E01.webm")
    media.upload(owner, "vp9_opus.webm", season, name="Breaking.Bad.S01E02.webm")
    library = make_library(media, owner, series, "shows")
    found = wait_items(media, owner, library["id"], 1)
    assert [(c["kind"], c["title"]) for c in found] == [("show", "Breaking Bad")], found

    page = media_browsers.context_as(owner).new_page()
    page.goto("/media")
    # Net toegevoegd: twee afleveringen, één tabstop, ← en →
    recent = page.get_by_role("region", name="Net toegevoegd")
    cards = recent.locator(".media-card")
    expect(cards).to_have_count(2, timeout=APPEAR_S * 1000)
    recent.locator('.media-card[tabindex="0"]').focus()
    before = focused_text(page)
    page.keyboard.press("ArrowRight")
    after = focused_text(page)
    assert after != before and re.search(r"S01E0[12]", after), (before, after)
    page.keyboard.press("ArrowLeft")
    assert focused_text(page) == before
    page.keyboard.press("End")
    assert focused_text(page) == after

    # De bibliotheek: één kaart, de serie; Enter opent haar
    link = page.get_by_role("link", name=re.compile(r"^Series"))
    link.focus()
    page.keyboard.press("Enter")
    grid = page.get_by_role("grid", name="Inhoud van Series")
    expect(grid.locator(".media-card-title")).to_have_text(["Breaking Bad"])
    grid.locator('[tabindex="0"]').focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/media/serie/[0-9a-f-]+$"))
    expect(page.get_by_role("heading", level=1, name="Breaking Bad")).to_be_visible()
    expect(page.get_by_role("heading", level=2, name="Seizoen 1")).to_be_visible()
    episodes = page.locator(".media-episode a")
    expect(episodes).to_have_count(2)
    expect(episodes.first).to_contain_text("S01E01")
    expect(episodes.nth(1)).to_contain_text("S01E02")
    play = page.get_by_role("link", name="Afspelen S01E01")
    expect(play).to_have_attribute("href", f"/kijken/{first.id}")
    tab_to(page, ".media-episode a")
    expect_focus_text(page, "S01E01")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(f"/media/item/{first.id}$"))
    expect(page.get_by_role("link", name="Naar de serie")).to_be_visible()

    # Opnieuw scannen (de eigenaar), met Tab en Enter
    page.goto(f"/media/bibliotheek/{library['id']}")
    expect(page.get_by_role("grid", name="Inhoud van Series")).to_be_visible()
    tab_to(page, "[data-rescan]")
    with page.expect_response(re.compile(f"/api/v1/media/libraries/{library['id']}/scan$")) as got:
        page.keyboard.press("Enter")
    assert got.value.status == 202
    expect(
        page.locator(".toast-message", has_text="De scan van Series is gestart.")
    ).to_be_visible()


@pytest.mark.media
def test_import_uit_data_import_met_de_cli(media, media_browsers, ffmpeg_count):
    from tests.media import fixtures

    owner = media.user()
    target = media.folder(owner, "Import")

    # De lege start: Nieuwe bibliotheek, de map kiezen, het soort Video's, Enter
    page = media_browsers.context_as(owner).new_page()
    page.goto("/media")
    expect(page.get_by_role("heading", name="Nog geen bibliotheken")).to_be_visible()
    tab_to(page, "[data-new-library]")
    page.keyboard.press("Enter")
    picker = page.get_by_role("dialog", name="Kies de map voor de bibliotheek")
    expect(picker).to_be_visible()
    expect_focus_text(page, "Import")  # de eerste map
    page.keyboard.press("Enter")  # de map openen
    use = picker.get_by_role("button", name="Deze map gebruiken")
    expect(use).to_be_focused()  # geen mappen erin: de knop om hier te kiezen
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Als bibliotheek gebruiken")
    expect(dialog).to_be_visible()
    expect(picker).to_have_count(0)
    expect(dialog).to_contain_text("De map Import wordt een bibliotheek")
    expect(dialog.get_by_role("radio", name=re.compile(r"^Films"))).to_be_focused()
    for _ in range(3):
        page.keyboard.press("ArrowDown")
    expect(dialog.get_by_role("radio", name=re.compile(r"^Video's"))).to_be_checked()
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(
        page.locator(".toast-message", has_text="Bibliotheek Import gemaakt. De scan loopt.")
    ).to_be_visible()
    [library] = libraries(media, owner)
    assert (library["kind"], library["root_id"]) == ("videos", target), library
    # De start toont de bibliotheek nu, en de focus staat weer op de knop
    expect(page.get_by_role("link", name=re.compile(r"^Import"))).to_be_visible()
    expect(page.locator("[data-new-library]")).to_be_focused()

    source = media.server.data_dir / "import" / f"batch-{uuid.uuid4().hex[:8]}"
    source.mkdir(parents=True)
    try:
        old = time.time() - 600  # ouder dan MIN_AGE_S: niet meer "in gebruik"
        names = {"Vakantie 2019.webm": "vp9_opus.webm", "Verjaardag.mp4": "h264_aac.mp4"}
        for name, fixture in names.items():
            shutil.copyfile(fixtures.make(fixture), source / name)
            os.utime(source / name, (old, old))
        done = media.cli(
            "media-import", str(source), "--naar", "/Import", "--eigenaar", owner.email
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "De import staat klaar" in done.stdout, done.stdout
        found = wait_items(media, owner, library["id"], 2)
        assert sorted(c["title"] for c in found) == ["Vakantie 2019", "Verjaardag"], found
        # kopiëren: de bron blijft staan
        assert sorted(p.name for p in source.iterdir()) == sorted(names)
        end = time.monotonic() + 30
        while time.monotonic() < end and [s for s, _ in import_jobs(media)][-1:] != ["done"]:
            time.sleep(0.25)
        assert [s for s, _ in import_jobs(media)][-1:] == ["done"], import_jobs(media)

        # In de browser: de bibliotheek met de twee video's
        page.goto(f"/media/bibliotheek/{library['id']}")
        grid = page.get_by_role("grid", name="Inhoud van Import")
        expect(grid.locator(".media-card-title")).to_have_text(["Vakantie 2019", "Verjaardag"])
        assert Path(source).is_dir()
    finally:
        shutil.rmtree(source, ignore_errors=True)
