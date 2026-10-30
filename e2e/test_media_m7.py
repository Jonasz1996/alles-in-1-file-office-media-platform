"""M7: de voortgang in de browser (fase 5, §2 M7 van het bouwontwerp, Keuze 36).

Met media aan en de lokale worker (de fixture media), tegen de echte routes: de speler van M6
meldt zijn stand met PUT /api/v1/media/progress/{node} (player/progress.ts), de server stuurt
playback.progress naar de andere tabbladen van dezelfde gebruiker, en daar bewegen de pagina
van het item en de rijen op /media mee (progressSync.ts, ContinueRow, NextUpRow).

1. Twee contexts van dezelfde gebruiker (twee tabbladen, elk een eigen origin). A speelt tot
   30 s en pauzeert; bij B staat binnen 2 s "Verder op 0:30" op de pagina van het item, en B
   begint daar op 0:30. Verder kijken op /media begint pas voorbij 60 s (Keuze 36): A springt
   naar 1:30 en de rij verschijnt bij B binnen 2 s (m7.md, afwijking 1: de rij met 0:30 uit de
   opdracht kan niet, de regel van 60 s gaat voor).
2. Klaar als: synchroniseren ≤ 2 s, p95 van 20 keer. A (gepauzeerd) springt met de cijfertoetsen
   naar 40 %–80 %; gemeten van de toets bij A tot de kaart bij B de nieuwe stand toont.
3. Verder kijken met het toetsenbord: één tabstop, ← → Home End, Enter kijkt verder op de stand;
   een stand van een ander apparaat (hier een PUT met een eigen origin) zet zijn kaart vooraan.
4. Volgende aflevering: A kijkt S01E01 uit (de stand aan het eind: gezien); bij B staat S01E02
   binnen 2 s klaar. Shift+F10 op de kaart, Gezien markeren: S01E03 schuift door; Enter speelt
   hem af.

Het event gaat alleen naar de gebruiker zelf: dat test test_media_progress_api.py (een tweede
gebruiker krijgt niets). Lokaal VP9 en Opus in .webm (direct, zonder ffmpeg in de app).
"""

from __future__ import annotations

import math
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

from media_harness import open_app
from test_media_m2 import focused_text, menu_to
from test_media_m6 import (
    PLAY_RE,
    loop_mkv,
    pages_away,  # noqa: F401 (autouse: na elke test elke pagina naar about:blank)
    playing_past,
    ready_state,
    scanned,
    state,
    upload_path,
    watch,
)

# Klaar als (§2 M7): synchroniseren binnen 2 s, p95 van 20 keer
SYNC_S = 2.0
RUNS = 20
# De cijfertoetsen van de speler: 4 → 40 % … 8 → 80 % (9 is 90 %: dat is gezien)
DIGITS = ["4", "5", "6", "7", "8"]
PROGRESS_RE = re.compile(r"/api/v1/media/progress/[0-9a-f-]+$")
CONTINUE = "Verder kijken"
NEXT = "Volgende aflevering"


def clock(s: float) -> str:
    """Zoals fmt.duration: 83 → "1:23"."""
    total = max(0, round(s))
    h, rest = divmod(total, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def is_put(response: Any) -> bool:
    return response.request.method == "PUT" and bool(PROGRESS_RE.search(response.url))


def put_event(event: str) -> Any:
    """Het antwoord op de PUT van de speler met dit event."""

    def match(response: Any) -> bool:
        if not is_put(response):
            return False
        body = response.request.post_data_json or {}
        return body.get("event") == event

    return match


def long_movie(media: Any, owner: Any, folder: str, tmp: Path, name: str) -> str:
    """vp9_opus.webm dertig keer achter elkaar (180 s, -c copy) als name in folder; geeft de
    node. Lang genoeg voor Verder kijken (voorbij 60 s) en kort genoeg om niet gezien te zijn."""
    src = loop_mkv(tmp, 30, fixture="vp9_opus.webm")
    path = src.with_name(name)
    src.rename(path)
    return upload_path(media, owner, folder, path)


def row(page: Any, name: str) -> Any:
    return page.get_by_role("region", name=name, exact=True)


def card_label(page: Any, node_id: str) -> str | None:
    return page.evaluate(
        '(id) => { const el = document.querySelector(`[data-progress-card="${id}"]`);'
        " return el ? el.getAttribute('aria-label') : null; }",
        node_id,
    )


def wait_label(page: Any, node_id: str, label: str, timeout_ms: float = 10_000) -> None:
    page.wait_for_function(
        '([id, label]) => { const el = document.querySelector(`[data-progress-card="${id}"]`);'
        " return !!el && el.getAttribute('aria-label') === label; }",
        arg=[node_id, label],
        timeout=timeout_ms,
    )


def at_end(response: Any) -> bool:
    """Een PUT met de stand aan het eind (de speler meldt pause of end met de hele duur)."""
    body = response.request.post_data_json or {}
    duration = body.get("duration_ms") or 0
    return duration > 0 and body.get("position_ms", 0) >= 0.9 * duration


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- 1 en 2: twee tabbladen, en de p95 van 20 keer -------------------------------------------


@pytest.mark.media
def test_twee_tabbladen_verder_kijken_binnen_2_s(media, media_browsers, ffmpeg_count, tmp_path):
    owner = media.user()
    films = media.folder(owner, "Films")
    heat = long_movie(media, owner, films, tmp_path, "Heat.1995.webm")
    scanned(media, owner, films, "movies", [heat])

    # Twee contexts van dezelfde gebruiker: twee tabbladen, elk een eigen origin
    a = media_browsers.context_as(owner).new_page()
    b = media_browsers.context_as(owner).new_page()
    open_app(a)
    open_app(b)

    b.goto(f"/media/item/{heat}")
    expect(b.get_by_role("heading", level=1, name="Heat")).to_be_visible()
    expect(b.locator("[data-resume]")).to_have_count(0)

    # A speelt tot 30 s en pauzeert (K)
    watch(a, heat, 28)
    ready_state(a, "direct")
    playing_past(a, 30.0)
    with a.expect_response(put_event("pause")) as paused:
        pressed = time.monotonic()
        a.keyboard.press("k")
    assert paused.value.status == 204, paused.value.status
    a.wait_for_function("() => document.querySelector('.player-video').paused")
    at = float(state(a)["t"])
    body = paused.value.request.post_data_json
    assert body["origin"] and body["seq"] >= 1 and body["started_at"], body
    assert "token" not in paused.value.url and "access" not in paused.value.url
    assert paused.value.request.all_headers().get("authorization", "").startswith("Bearer ")
    shown = f"Verder op {clock(math.floor(body['position_ms'] / 1000))}"
    assert shown == f"Verder op {clock(math.floor(at))}" == "Verder op 0:30", (shown, at)

    # Bij B binnen 2 s "Verder op 0:30" (de pagina van het item laadt opnieuw op het event)
    resume = b.get_by_role("link", name=shown)
    expect(resume).to_be_visible(timeout=SYNC_S * 1000)
    first_sync = time.monotonic() - pressed
    assert first_sync <= SYNC_S, first_sync

    # B opent het item op die stand en begint op 0:30
    resume.focus()
    with b.expect_request(PLAY_RE) as asked:
        b.keyboard.press("Enter")
    assert asked.value.post_data_json["start_ms"] == 30_000, asked.value.post_data_json
    expect(b).to_have_url(re.compile(f"/kijken/{heat}\\?t=30$"))
    started = playing_past(b, 30.0)
    assert 30.0 <= started < 40.0, started
    b.keyboard.press("k")
    b.wait_for_function("() => document.querySelector('.player-video').paused")
    # De stop bij weggaan gaat met keepalive: Playwright ziet de body daarvan niet, wel het
    # antwoord (B staat stil, dus de enige PUT hierna is de stop)
    with b.expect_response(is_put) as stopped:
        b.keyboard.press("Escape")
    assert stopped.value.status == 204

    # Verder kijken op /media: pas voorbij 60 s. A (gepauzeerd) springt naar 50 % (1:30)
    b.goto("/media")
    expect(b.get_by_role("link", name=re.compile(r"^Films"))).to_be_visible()
    expect(row(b, CONTINUE)).to_have_count(0)
    with a.expect_response(put_event("seek")) as sought:
        pressed = time.monotonic()
        a.keyboard.press("5")
    seek = sought.value.request.post_data_json
    assert sought.value.status == 204 and seek["position_ms"] > 60_000, seek
    label = f"Heat, verder op {clock(math.floor(seek['position_ms'] / 1000))}"
    wait_label(b, heat, label, SYNC_S * 1000)
    assert time.monotonic() - pressed <= SYNC_S
    expect(row(b, CONTINUE).locator(".media-card")).to_have_count(1)

    # Klaar als: 20 keer, p95 ≤ 2 s (een stand van A tot de kaart bij B)
    times: list[float] = []
    for i in range(RUNS):
        digit = DIGITS[i % len(DIGITS)]
        with a.expect_response(put_event("seek")) as sought:
            pressed = time.monotonic()
            a.keyboard.press(digit)
        seek = sought.value.request.post_data_json
        assert sought.value.status == 204, sought.value.status
        label = f"Heat, verder op {clock(math.floor(seek['position_ms'] / 1000))}"
        wait_label(b, heat, label, 10_000)
        times.append(time.monotonic() - pressed)
    print(f"\nM7 synchroniseren (s, {RUNS} keer): " + ", ".join(f"{t:.3f}" for t in times))
    print(f"M7 p95 {p95(times):.3f} s, max {max(times):.3f} s, eerste {first_sync:.3f} s")
    assert p95(times) <= SYNC_S, times
    # A zelf sprong nooit terug naar zijn eigen stand: zijn eigen origin telt niet
    assert state(a)["paused"], state(a)


# --- 3: Verder kijken met het toetsenbord ------------------------------------------------------


@pytest.mark.media
def test_verder_kijken_met_het_toetsenbord(media, media_browsers, ffmpeg_count, tmp_path):
    owner = media.user()
    films = media.folder(owner, "Films")
    heat = long_movie(media, owner, films, tmp_path, "Heat.1995.webm")
    alien = long_movie(media, owner, films, tmp_path, "Alien.1979.webm")
    scanned(media, owner, films, "movies", [heat, alien])

    def put(node: str, position_ms: int, seq: int) -> None:
        media.server.call(
            owner, "PUT", f"/api/v1/media/progress/{node}",
            json={"position_ms": position_ms, "duration_ms": 180_000, "event": "pause",
                  "origin": "e2e-ander-apparaat", "seq": seq, "started_at": now_iso()},
            expect=204,
        )  # fmt: skip

    put(heat, 95_000, 1)
    put(alien, 75_000, 2)

    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    page.goto("/media")
    cards = row(page, CONTINUE).locator(".media-card")
    expect(cards).to_have_count(2)
    # De nieuwste eerst
    expect(cards.first).to_have_attribute("aria-label", "Alien, verder op 1:15")
    expect(cards.nth(1)).to_have_attribute("aria-label", "Heat, verder op 1:35")
    expect(row(page, CONTINUE).locator('.media-card[tabindex="0"]')).to_have_count(1)

    # Een stand van een ander apparaat: Heat vooraan, meteen (zonder opnieuw te laden)
    put(heat, 100_000, 3)
    expect(cards.first).to_have_attribute("aria-label", "Heat, verder op 1:40")

    # Eén tabstop; ← → Home End
    row(page, CONTINUE).locator('.media-card[tabindex="0"]').focus()
    assert focused_text(page).startswith("Heat"), focused_text(page)
    page.keyboard.press("ArrowRight")
    assert focused_text(page).startswith("Alien"), focused_text(page)
    page.keyboard.press("Home")
    assert focused_text(page).startswith("Heat")
    page.keyboard.press("End")
    assert focused_text(page).startswith("Alien")
    page.keyboard.press("ArrowLeft")
    assert focused_text(page).startswith("Heat")

    # Shift+F10: het menu van de kaart, Esc sluit het en de focus komt terug
    page.keyboard.press("Shift+F10")
    menu = page.get_by_role("menu", name=CONTINUE)
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem")).to_have_text(
        ["Verder kijken vanaf 1:40", "Naar het item", "Gezien markeren"]
    )
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    assert focused_text(page).startswith("Heat")

    # Enter: verder kijken op de stand
    with page.expect_request(PLAY_RE) as asked:
        page.keyboard.press("Enter")
    assert asked.value.post_data_json["start_ms"] == 100_000, asked.value.post_data_json
    expect(page).to_have_url(re.compile(f"/kijken/{heat}\\?t=100$"))
    playing_past(page, 100.0)
    page.keyboard.press("k")
    page.wait_for_function("() => document.querySelector('.player-video').paused")


# --- 4: Volgende aflevering --------------------------------------------------------------------


@pytest.mark.media
def test_volgende_aflevering_en_gezien_markeren(media, media_browsers, ffmpeg_count):
    owner = media.user()
    series = media.folder(owner, "Series")
    show = media.folder(owner, "Breaking Bad", series)
    season = media.folder(owner, "Season 1", show)
    eps = [
        media.upload(owner, "vp9_opus.webm", season, name=f"Breaking.Bad.S01E0{n}.webm").id
        for n in (1, 2, 3)
    ]
    scanned(media, owner, series, "shows", eps)

    a = media_browsers.context_as(owner).new_page()
    b = media_browsers.context_as(owner).new_page()
    open_app(a)
    open_app(b)
    b.goto("/media")
    expect(b.get_by_role("link", name=re.compile(r"^Series"))).to_be_visible()
    expect(row(b, NEXT)).to_have_count(0)

    # A kijkt S01E01 uit: de stand aan het eind is gezien (Keuze 36, 90 %). Een aflevering van
    # 6 s heeft geen kaart met aftelling (M6, next.ts): de speler gaat meteen door naar S01E02
    watch(a, eps[0], 4)
    ready_state(a, "direct")
    with a.expect_response(lambda r: is_put(r) and r.url.endswith(eps[0]) and at_end(r)) as end:
        pass
    assert end.value.status == 204
    done = time.monotonic()

    # Bij B: S01E02 in Volgende aflevering, binnen 2 s na het einde
    nxt = row(b, NEXT).locator(".media-card")
    expect(nxt.first).to_have_attribute("data-progress-card", eps[1], timeout=SYNC_S * 1000)
    assert time.monotonic() - done <= SYNC_S
    expect(nxt).to_have_count(1)
    # A is vanzelf door naar S01E02; weg daar (anders kijkt hij ook die uit)
    expect(a).to_have_url(re.compile(f"/kijken/{eps[1]}$"))
    a.goto("about:blank")
    expect(nxt.first).to_contain_text("S01E02")
    expect(row(b, CONTINUE)).to_have_count(0)  # gezien staat niet in Verder kijken

    # Shift+F10 op de kaart: Afspelen, Naar het item, Naar de serie, Gezien markeren
    row(b, NEXT).locator('.media-card[tabindex="0"]').focus()
    b.keyboard.press("Shift+F10")
    menu = b.get_by_role("menu", name=NEXT)
    expect(menu).to_be_visible()
    # (S01E02 kan een stand van een seconde hebben, van de speler bij A: dan staat Verder kijken
    # vanaf 0:01 er nog voor)
    items = menu.get_by_role("menuitem").all_inner_texts()
    assert items[-4:] == ["Afspelen", "Naar het item", "Naar de serie", "Gezien markeren"], items
    menu_to(b, "Gezien markeren")
    with b.expect_response(re.compile(r"/api/v1/media/progress/batch$")) as marked:
        b.keyboard.press("Enter")
    assert marked.value.status == 200 and marked.value.json() == {"updated": 1}
    expect(b.locator(".toast-message", has_text="staat op gezien.")).to_be_visible()

    # S01E03 schuift door; Enter speelt hem af
    expect(nxt.first).to_have_attribute("data-progress-card", eps[2])
    expect(nxt.first).to_contain_text("S01E03")
    nxt.first.focus()
    with b.expect_request(PLAY_RE):
        b.keyboard.press("Enter")
    expect(b).to_have_url(re.compile(f"/kijken/{eps[2]}$"))
    playing_past(b, 0.5)
    b.keyboard.press("k")
    b.wait_for_function("() => document.querySelector('.player-video').paused")
