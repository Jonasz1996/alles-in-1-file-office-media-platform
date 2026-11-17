"""M8: metadata online, met de nep-providers als proces (fase 5, §2 M8 van het bouwontwerp,
Keuzes 39–43).

Klaar als (§2 M8): met de nep-server krijgt een bibliotheek van 20 films met scene-namen er
≥ 18 vanzelf goed, en de rest staat onder "Nakijken". Handmatig koppelen werkt. De sleutel
verlaat de server alleen in de header naar TMDB.

1. De sleutel, zoals Jonas hem invult (Keuze 40): /beheer → Media → Online metadata (M10), met
   Tab naar het veld, de sleutel typen en Enter. Eerst een foute: 422 `media_provider_auth`, de
   melding in het blok, en er is niets bewaard. Dan de goede (van de nep-providers): bewaard, het
   veld weer leeg, de sleutel nergens in de pagina, en de bronvermelding van TMDB staat erbij.
   (Tot het samenvoegen van golf 5 deed een hulpproces dit met `secrets.save_tmdb`.)
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
   de log van de server, niet in de database (versleuteld), niet in de pagina's (ook niet op
   /beheer) en niet in een antwoord van de API.

Na de test is de sleutel weer weg (de fixture, met DELETE …/admin/media/tmdb): de andere
mediatests draaien zonder provider.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import closing
from typing import Any

import pytest
from playwright.sync_api import expect

from media_harness import open_app
from test_media_m2 import tab_to
from test_media_m3 import upload_bytes

# Na de scan zet de haak (scanner.after_scan, integratie punt 6) het opzoeken klaar; dan het
# opzoeken en de plaatjes
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

# De sleutel op /beheer → Media → Online metadata (M10; Keuze 40)
TMDB_FIELD = "input[name=tmdb-key]"
TMDB_ROUTE = "/api/v1/admin/media/tmdb"
AUTH_TEXT = "De sleutel van de provider werkt niet"


def tmdb_on_beheer(page: Any, key: str) -> tuple[int, str]:
    """Met Tab naar het veld van de sleutel (als de focus er nog niet staat), wat er staat
    selecteren en overtypen, Enter: (status, body) van PUT …/tmdb, de body meteen gelezen."""
    here = "(s) => !!document.activeElement && document.activeElement.matches(s)"
    if not page.evaluate(here, TMDB_FIELD):
        tab_to(page, TMDB_FIELD, presses=200)
    page.keyboard.press("ControlOrMeta+A")  # een foute sleutel van daarnet blijft staan
    page.keyboard.type(key)
    with page.expect_response(
        lambda r: r.request.method == "PUT" and r.url.endswith(TMDB_ROUTE)
    ) as saved:
        page.keyboard.press("Enter")
    response = saved.value
    # Een 204 heeft geen body; Chromium geeft er dan ook geen (getResponseBody faalt)
    return response.status, "" if response.status == 204 else response.text()


def tmdb_view(media: Any) -> dict[str, Any]:
    """De regel van TMDB in het overzicht van /beheer (nooit de sleutel zelf)."""
    r = media.server.call(media.admin, "GET", "/api/v1/admin/media", expect=200)
    [tmdb] = [p for p in r.json()["providers"] if p["name"] == "tmdb"]
    return tmdb


@pytest.fixture
def tmdb_key(media: Any, media_browsers: Any, fake_providers: Any) -> Iterator[str]:
    """Op /beheer: eerst een foute sleutel (niet bewaard), dan de goede van de nep-providers; na
    de test weer weg, en de nep-server zonder fouten."""
    fake_providers.clear_faults()
    fake_providers.clear_log()
    page = media_browsers.context_as(media.admin).new_page()
    page.goto("/beheer/media")
    expect(page.locator("[data-media=worker-state]")).to_be_visible()
    block = page.locator("section[aria-labelledby=admin-media-providers]")
    expect(block.locator("[data-attribution]")).to_contain_text(ATTRIBUTION)
    expect(block.get_by_role("link", name="Gegevens: TMDB")).to_have_attribute(
        "href", "https://www.themoviedb.org/"
    )

    wrong = "e2e-fout-" + secrets.token_urlsafe(24)
    status, body = tmdb_on_beheer(page, wrong)
    assert status == 422 and json.loads(body)["detail"]["code"] == "media_provider_auth", body
    assert wrong not in body
    expect(block.get_by_role("alert")).to_have_text(AUTH_TEXT)
    assert tmdb_view(media)["key_set"] is False
    tried = [e for e in fake_providers.log() if e["service"] == "tmdb"]
    assert tried and all(e["auth"] == "bearer" and not e["auth_ok"] for e in tried), tried
    assert {e["status"] for e in tried} == {401}
    fake_providers.clear_log()

    key = fake_providers.tmdb_key
    status, body = tmdb_on_beheer(page, key)
    assert status == 204, body
    saved = page.locator(".toast-message", has_text="De sleutel is bewaard en werkt.")
    expect(saved).to_be_visible()
    expect(page.locator(TMDB_FIELD)).to_have_value("")
    expect(block.get_by_role("alert")).to_have_count(0)
    shown = tmdb_view(media)
    assert shown["key_set"] is True and shown["last_error"] is None, shown
    assert key not in page.content()
    page.close()
    try:
        yield key
    finally:
        fake_providers.clear_faults()
        media.server.call(media.admin, "DELETE", TMDB_ROUTE, expect=204)
        assert tmdb_view(media)["key_set"] is False
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

    # Bewerken: de titel krijgt de focus; wat je wijzigt, gaat vast. Het venster leest `locked`
    # met GET …/items/{id}, niet meer met een lege PATCH (integratie, punt 6)
    patches: list[str] = []

    def on_request(request) -> None:
        if request.method == "PATCH":
            patches.append(request.url)

    page.on("request", on_request)
    tab_to(page, "[data-edit]", presses=60)
    with page.expect_response(
        lambda r: r.request.method == "GET" and bool(ITEM_RE.search(r.url))
    ) as opened:
        page.keyboard.press("Enter")
    fresh = opened.value.json()
    assert opened.value.status == 200 and fresh["sort_title"], fresh
    edit = page.get_by_role("dialog", name="Gegevens bewerken")
    title = edit.get_by_label("Titel", exact=True)
    expect(title).to_be_focused()
    page.remove_listener("request", on_request)
    assert patches == [], patches
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
