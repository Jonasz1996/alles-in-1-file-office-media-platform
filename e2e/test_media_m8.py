"""M8: metadata online, met de nep-providers als proces (fase 5, §2 M8 van het bouwontwerp,
Keuzes 39–43).

Klaar als (§2 M8): met de nep-server krijgt een bibliotheek van 20 films met scene-namen er
≥ 18 vanzelf goed, en de rest staat onder "Nakijken". Handmatig koppelen werkt. De sleutel
verlaat de server alleen in de header naar TMDB.

1. De sleutel: er is geen route voor (/beheer is van M10), dus zet een hulpproces met de
   omgeving van de app hem met `secrets.save_tmdb` (zoals de cli dat zou doen), de sleutel via
   stdin. Eerst een foute: die wordt niet bewaard. Dan de goede (van de nep-providers).
2. 20 films met scene-namen (dezelfde lijst als api/tests/test_media_match.py) in een
   bibliotheek met online opzoeken. Niemand duwt: de haak op de wijziging en de rij media_net
   van de app doen het. ≥ 18 gekoppeld met het goede id van TMDB, met een poster via ons eigen
   ondertekende adres; de andere 2 onder Nakijken (API en browser).
3. Met het toetsenbord: in Nakijken naar "The Fake Storm", Enter, Tab naar Koppelen. De
   eerste zoekopdracht krijgt een 429 met Retry-After 3 (de nep-server): het venster toont de
   fout en blijft open; na de Retry-After zoekt Enter in het zoekveld opnieuw. Tab naar de
   kandidaat van 1998, Enter: gekoppeld (manual), met "Gegevens: TMDB" en de zin van TMDB.
   Bewerken: de titel krijgt de focus, een nieuwe titel zet het vinkje Vastzetten aan, Enter
   bewaart. Terug op de bibliotheek staat er nog 1 onder Nakijken.
4. De sleutel: alle verzoeken naar TMDB met `Authorization: Bearer`, geen `api_key` in een
   adres, niets naar de plaatjes, MusicBrainz of CAA met een sleutel; de sleutel staat niet in
   de log van de server, niet in de database (versleuteld), niet in de pagina en niet in de
   uitvoer van het hulpproces.

Na de test is de sleutel weer weg (de fixture): de andere mediatests draaien zonder provider.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import closing
from typing import Any

import pytest
from playwright.sync_api import expect

from conftest import API_DIR
from media_harness import open_app
from test_media_m2 import tab_to
from test_media_m3 import upload_bytes

# De haak wacht SOON (10 s) na de laatste wijziging; dan het opzoeken en de plaatjes
MATCH_S = 50.0
STORM = "The.Fake.Storm.720p.HDTV.x264-NEP.mkv"
GLASS = "Glass.Town.2009.1080p.BluRay.x264-NEP.mkv"
STORM_1998 = "movie:900019"
CANDIDATES_RE = re.compile(r"/api/v1/media/items/[0-9a-f-]+/candidates")
MATCH_RE = re.compile(r"/api/v1/media/items/[0-9a-f-]+/match$")
ITEM_RE = re.compile(r"/api/v1/media/items/[0-9a-f-]+$")
ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."
LOADED_JS = "(img) => img.complete && img.naturalWidth > 0"
ART = "/api/v1/media/art/"

# Het hulpproces: de omgeving van de app (dezelfde datamap en hetzelfde app-geheim), de
# uitgaande client van de app, en secrets van M8. De sleutel komt via stdin, nooit in argv.
HELPER = r"""
import asyncio, json, sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_engine
from app.media.providers import http, secrets
from app.media.providers.base import ProviderAuth, ProviderUnavailable


async def main(action):
    settings = get_settings()
    out = http.Outbound(settings)
    http.use(out)
    engine = get_engine(settings)
    result = "ok"
    try:
        async with async_sessionmaker(engine)() as session:
            if action == "save":
                try:
                    await secrets.save_tmdb(session, settings, sys.stdin.read())
                except ProviderAuth:
                    result = "auth"
                except ProviderUnavailable:
                    result = "unavailable"
            elif action == "forget":
                await secrets.forget_tmdb(session)
            state = await secrets.status(session, settings)
    finally:
        await out.aclose()
        await engine.dispose()
    print(json.dumps({"result": result, "status": state}))


asyncio.run(main(sys.argv[1]))
"""


def tmdb_secret(media: Any, action: str, key: str = "") -> dict[str, Any]:
    """save (met de sleutel op stdin) of forget, in een eigen proces met de omgeving van de app.
    Geeft {"result", "status"}; de uitvoer mag de sleutel nooit bevatten."""
    proc = subprocess.run(
        [sys.executable, "-c", HELPER, action],
        cwd=API_DIR,
        env=media.server.environment(),
        input=key,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if key:
        assert key not in proc.stdout and key not in proc.stderr, "de sleutel in de uitvoer"
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture
def tmdb_key(media: Any, fake_providers: Any) -> Iterator[str]:
    """Eerst een foute sleutel (niet bewaard), dan de goede van de nep-providers; na de test
    weer weg, en de nep-server zonder fouten."""
    fake_providers.clear_faults()
    fake_providers.clear_log()
    wrong = "e2e-fout-" + secrets.token_urlsafe(24)
    out = tmdb_secret(media, "save", wrong)
    assert out == {"result": "auth", "status": {"set": False, "set_at": None, "ok": None}}
    tried = [e for e in fake_providers.log() if e["service"] == "tmdb"]
    assert tried and all(e["auth"] == "bearer" and not e["auth_ok"] for e in tried), tried
    assert {e["status"] for e in tried} == {401}
    fake_providers.clear_log()

    key = fake_providers.tmdb_key
    out = tmdb_secret(media, "save", key)
    assert out["result"] == "ok"
    assert out["status"]["set"] is True and out["status"]["ok"] is True
    try:
        yield key
    finally:
        fake_providers.clear_faults()
        gone = tmdb_secret(media, "forget")
        assert gone["status"]["set"] is False
        log = media.server.log_path.read_text(errors="replace")
        assert wrong not in log


def rows(media: Any, nodes: set[str]) -> dict[str, dict[str, Any]]:
    """match_state, ids en art van deze items, uit de database van de app (alleen lezen)."""
    uri = f"file:{media.server.data_dir / 'platform.db'}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        found = conn.execute("SELECT node_id, match_state, ids, art FROM media_item").fetchall()
    out: dict[str, dict[str, Any]] = {}
    for node, state, ids, art in found:
        nid = str(uuid.UUID(bytes=node) if isinstance(node, bytes) else uuid.UUID(str(node)))
        if nid in nodes:
            out[nid] = {
                "state": state,
                "ids": json.loads(ids or "{}"),
                "art": json.loads(art or "{}"),
            }
    return out


def wait_decided(media: Any, nodes: dict[str, str], seconds: float) -> dict[str, dict[str, Any]]:
    """Tot elk item gekoppeld of onder Nakijken staat, en elk gekoppeld item zijn poster heeft
    (de taak media.art)."""
    end = time.monotonic() + seconds
    found: dict[str, dict[str, Any]] = {}
    while time.monotonic() < end:
        found = rows(media, set(nodes))
        done = len(found) == len(nodes) and all(
            r["state"] == "review" or (r["state"] == "matched" and r["art"].get("poster"))
            for r in found.values()
        )
        if done:
            return found
        time.sleep(0.5)
    states = {nodes[n]: (r["state"], bool(r["art"].get("poster"))) for n, r in found.items()}
    raise AssertionError(f"na {seconds:.0f} s nog niet klaar: {states}\n{media.server.log_tail()}")


@pytest.mark.media
def test_twintig_films_vanzelf_en_met_de_hand(media, media_browsers, fake_providers, tmdb_key):
    from tests.test_media_match import FILMS

    owner = media.user()
    folder = media.folder(owner, "Films M8")
    r = media.server.call(
        owner, "POST", "/api/v1/media/libraries",
        json={"root_id": folder, "kind": "movies", "online": True}, expect=201,
    )  # fmt: skip
    library = r.json()
    assert library["online"] is True
    nodes: dict[str, str] = {}
    for name, _expected in FILMS:
        nodes[upload_bytes(media, owner, folder, name, f"nep {name}".encode())] = name
    uploaded = time.monotonic()

    # --- 2. vanzelf: ≥ 18 goed, de rest onder Nakijken ---
    found = wait_decided(media, nodes, MATCH_S)
    decided_s = time.monotonic() - uploaded
    expected = dict(FILMS)
    matched = {n: r for n, r in found.items() if r["state"] == "matched"}
    wrong = {
        nodes[n]: r["ids"].get("tmdb")
        for n, r in matched.items()
        if r["ids"].get("tmdb") != str(expected[nodes[n]])
    }
    assert wrong == {}, wrong
    assert len(matched) >= 18, sorted(nodes[n] for n in matched)
    review = sorted(nodes[n] for n, r in found.items() if r["state"] == "review")
    assert review == sorted(n for n, e in FILMS if e is None)
    print(f"20 films: {len(matched)} gekoppeld en {len(review)} nakijken na {decided_s:.1f} s")

    by_name = {name: node for node, name in nodes.items()}
    storm, glass = by_name[STORM], by_name[GLASS]
    r = media.server.call(
        owner, "GET", f"/api/v1/media/libraries/{library['id']}/review", expect=200
    )
    assert sorted(c["node_id"] for c in r.json()["items"]) == sorted([storm, glass])
    nephaven = by_name[FILMS[0][0]]
    item = media.server.call(owner, "GET", f"/api/v1/media/items/{nephaven}", expect=200).json()
    assert item["match_state"] == "matched" and item["year"] == 2004
    assert item["overview"] and item["poster"].startswith(ART)
    poster = media.server.call(owner, "GET", item["poster"], expect=200)
    assert poster.headers["content-type"] == "image/webp"

    # --- 3. Nakijken en koppelen met het toetsenbord ---
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    page.goto(f"/media/bibliotheek/{library['id']}")
    section = page.get_by_role("region", name=re.compile(r"^Nakijken"))
    expect(section).to_be_visible()
    expect(section.locator("[data-review]")).to_have_count(2)
    listed_texts = sorted(section.locator("[data-review]").all_inner_texts())
    assert listed_texts == ["Glass Town (2009)", "The Fake Storm"], listed_texts
    grid = page.get_by_role("grid", name="Inhoud van Films M8")
    expect(grid.locator(".media-card-title").first).to_be_visible()
    first_poster = grid.locator("img").first
    expect(first_poster).to_be_visible()
    page.wait_for_function(LOADED_JS, arg=first_poster.element_handle())

    tab_to(page, f'[data-review] a[href$="/media/item/{storm}"]', presses=60)
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(f"/media/item/{storm}$"))
    expect(page.get_by_role("heading", level=1, name="The Fake Storm")).to_be_visible()

    fake_providers.fault("429", "/3/search/movie", host="tmdb", retry_after=3)
    tab_to(page, "[data-match]", presses=60)
    with page.expect_response(lambda r: bool(CANDIDATES_RE.search(r.url))) as first:
        page.keyboard.press("Enter")
    slowed = first.value
    body = slowed.json()
    assert slowed.status == 503 and body["detail"]["code"] == "media_provider_rate", body
    retry = float(slowed.headers["retry-after"])
    assert 0 < retry <= 60
    dialog = page.get_by_role("dialog", name="Koppelen met TMDB")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("alert")).to_have_text("De provider remt af; probeer het zo opnieuw")
    query = dialog.locator("[data-match-query]")
    expect(query).to_be_focused()
    expect(query).to_have_value("The Fake Storm")
    expect(dialog.locator(f"[data-candidate='{STORM_1998}']")).to_have_count(0)

    # Na de Retry-After: Enter in het zoekveld zoekt opnieuw
    time.sleep(retry + 0.2)
    with page.expect_response(lambda r: bool(CANDIDATES_RE.search(r.url))) as again:
        page.keyboard.press("Enter")
    listed = again.value
    candidates = listed.json()
    assert listed.status == 200, candidates
    assert {c["id"] for c in candidates} >= {STORM_1998, "movie:900039"}
    assert all((c["poster"] or ART).startswith(ART) for c in candidates), candidates
    expect(dialog.get_by_role("alert")).to_have_count(0)
    choice = dialog.locator(f"[data-candidate='{STORM_1998}']")
    expect(choice).to_have_accessible_name("Koppelen met The Fake Storm (1998)")
    page.wait_for_function(LOADED_JS, arg=choice.locator("img").element_handle())
    expect(dialog.locator("[data-attribution]")).to_contain_text(ATTRIBUTION)

    tab_to(page, f"[data-candidate='{STORM_1998}']")
    with page.expect_response(
        lambda r: r.request.method == "POST" and bool(MATCH_RE.search(r.url))
    ) as chosen:
        page.keyboard.press("Enter")
    picked = chosen.value
    item = picked.json()
    bodies = [json.dumps(candidates), json.dumps(item)]
    assert picked.status == 200, item
    assert item["match_state"] == "manual" and item["year"] == 1998, item
    assert item["overview"] == "Een verzonnen film over the fake storm."
    expect(dialog).to_have_count(0)
    toast = page.locator(".toast-message", has_text="The Fake Storm (1998) is gekoppeld.")
    expect(toast).to_be_visible()
    expect(page.locator(".media-item .media-meta")).to_contain_text("1998")
    attribution = page.locator(".media-item [data-attribution]")
    expect(attribution).to_contain_text(ATTRIBUTION)
    expect(attribution.get_by_role("link", name="Gegevens: TMDB")).to_have_attribute(
        "href", "https://www.themoviedb.org/"
    )
    expect(page.locator(".media-item dd", has_text="Met de hand")).to_be_visible()

    # Bewerken: de titel krijgt de focus; wat je wijzigt, gaat vast
    tab_to(page, "[data-edit]", presses=60)
    page.keyboard.press("Enter")
    edit = page.get_by_role("dialog", name="Gegevens bewerken")
    title = edit.get_by_label("Titel", exact=True)
    expect(title).to_be_focused()
    expect(title).to_have_value("The Fake Storm")
    lock = edit.locator("[data-lock='title']")
    expect(lock).not_to_be_checked()
    page.keyboard.press("Control+A")
    page.keyboard.type("De Storm")
    expect(lock).to_be_checked()
    with page.expect_response(
        lambda r: r.request.method == "PATCH" and bool(ITEM_RE.search(r.url))
    ) as saved:
        page.keyboard.press("Enter")
    patched = saved.value
    item = patched.json()
    bodies.append(json.dumps(item))
    assert patched.status == 200, item
    assert item["title"] == "De Storm" and "title" in item["locked"], item
    assert item["match_state"] == "manual"
    expect(edit).to_have_count(0)
    expect(page.locator(".toast-message", has_text="De gegevens zijn opgeslagen.")).to_be_visible()
    expect(page.get_by_role("heading", level=1, name="De Storm")).to_be_visible()

    page.goto(f"/media/bibliotheek/{library['id']}")
    section = page.get_by_role("region", name=re.compile(r"^Nakijken"))
    expect(section.locator("[data-review]")).to_have_text(["Glass Town (2009)"])
    grid = page.get_by_role("grid", name="Inhoud van Films M8")
    expect(grid.locator(".media-card-title", has_text="De Storm")).to_have_count(1)
    html = page.content()

    # --- 4. de sleutel verlaat de server alleen in de header naar TMDB ---
    log = fake_providers.log()
    to_tmdb = [e for e in log if e["service"] == "tmdb"]
    assert len(to_tmdb) >= 20, len(to_tmdb)
    assert all(e["auth"] == "bearer" and e["auth_ok"] for e in to_tmdb)
    assert all(e["auth"] is None for e in log if e["service"] != "tmdb"), [
        (e["service"], e["inner"]) for e in log if e["service"] != "tmdb" and e["auth"]
    ]
    assert all("api_key" not in e["query"] for e in log)
    assert tmdb_key not in json.dumps(log)  # de kop Authorization staat niet in het logboek
    assert any(e["service"] == "image" for e in log)  # de posters
    server_log = media.server.log_path.read_text(errors="replace")
    assert tmdb_key not in server_log
    assert tmdb_key not in html
    for name in ("platform.db", "platform.db-wal"):
        path = media.server.data_dir / name
        if path.exists():
            assert tmdb_key.encode() not in path.read_bytes(), name
    for text in [r.text, *bodies]:
        assert tmdb_key not in text
