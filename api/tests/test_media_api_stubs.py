"""De API-contracten van fase 5 (§2 en §3.4) in M0: elk eindpunt van §2 bestaat met het juiste pad,
de juiste methode, de body en het antwoordmodel uit app/media/types.py, en geeft 503 media_off
tot zijn pakket het vult. Ook met media aan geeft een stub 503 media_off (er is nog niets).

De routes onder /api/v1/stream/ staan in test_media_routes_guarded.py.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.routing import iter_route_contexts

from app.config import MediaConfig
from app.main import app
from app.media import types as t
from app.media.state import MediaState
from tests.test_files import put, root
from tests.test_public import new_link, open_link

ID = str(uuid.uuid4())
SHA = "a" * 64
PROFILE = {"v": 1}
NOW = "2026-10-28T12:00:00Z"

# (methode, pad van de route, body, wie: user | admin | link | none, model van de body,
# antwoordmodel, status bij succes). link: met de sleutel van een publieke link (CurrentLink)
ENDPOINTS = [
    # M2: bibliotheken en items
    ("GET", "/api/v1/media/libraries", None, "user", None, list[t.Library], None),
    (
        "POST",
        "/api/v1/media/libraries",
        {"root_id": ID, "kind": "movies"},
        "user",
        t.LibraryIn,
        t.Library,
        201,
    ),
    (
        "PATCH",
        "/api/v1/media/libraries/{library_id}",
        {"name": "Films"},
        "user",
        t.LibraryPatch,
        t.Library,
        None,
    ),
    ("DELETE", "/api/v1/media/libraries/{library_id}", None, "user", None, None, 204),
    ("POST", "/api/v1/media/libraries/{library_id}/scan", None, "user", None, None, 202),
    ("GET", "/api/v1/media/home", None, "user", None, t.MediaHome, None),
    ("GET", "/api/v1/media/libraries/{library_id}/items", None, "user", None, t.CardPage, None),
    ("GET", "/api/v1/media/items/{node_id}", None, "user", None, t.Item, None),
    ("GET", "/api/v1/media/shows/{show_id}", None, "user", None, t.ShowDetail, None),
    # M3: afspelen
    ("GET", "/api/v1/media/{node_id}/codecs", None, "user", None, t.CodecsOut, None),
    (
        "POST",
        "/api/v1/media/{node_id}/play",
        {"profile": PROFILE},
        "user",
        t.PlayIn,
        t.PlayOut,
        None,
    ),
    (
        "POST",
        "/api/v1/public/media/{node_id}/play",
        {"profile": PROFILE},
        "link",
        t.PlayIn,
        t.PlayOut,
        None,
    ),
    # M7: voortgang
    (
        "PUT",
        "/api/v1/media/progress/{node_id}",
        {
            "position_ms": 1000,
            "duration_ms": 60000,
            "event": "tick",
            "origin": "tab-1",
            "seq": 1,
            "started_at": NOW,
        },
        "user",
        t.ProgressIn,
        None,
        204,
    ),
    (
        "POST",
        "/api/v1/media/progress/batch",
        {"node_ids": [ID], "watched": True},
        "user",
        t.ProgressBatchIn,
        t.ProgressBatchOut,
        None,
    ),
    # M8: koppelen
    (
        "GET",
        "/api/v1/media/items/{node_id}/candidates",
        None,
        "user",
        None,
        list[t.MatchCandidate],
        None,
    ),
    (
        "POST",
        "/api/v1/media/items/{node_id}/match",
        {"provider": "tmdb", "id": "movie:603"},
        "user",
        t.MatchIn,
        t.Item,
        None,
    ),
    ("DELETE", "/api/v1/media/items/{node_id}/match", None, "user", None, t.Item, None),
    (
        "PATCH",
        "/api/v1/media/items/{node_id}",
        {"title": "Film"},
        "user",
        t.ItemPatch,
        t.Item,
        None,
    ),
    (
        "POST",
        "/api/v1/media/shows/{show_id}/match",
        {"provider": "tmdb", "id": "tv:1399"},
        "user",
        t.MatchIn,
        t.ShowDetail,
        None,
    ),
    ("GET", "/api/v1/media/art/{sha}/{size}.webp", None, "none", None, None, None),
    # M9: muziek
    ("GET", "/api/v1/media/libraries/{library_id}/albums", None, "user", None, t.AlbumPage, None),
    ("GET", "/api/v1/media/albums/{album_id}", None, "user", None, t.AlbumDetail, None),
    (
        "GET",
        "/api/v1/media/libraries/{library_id}/artists",
        None,
        "user",
        None,
        list[t.Artist],
        None,
    ),
    ("GET", "/api/v1/media/artists/{key}", None, "user", None, t.ArtistDetail, None),
    ("GET", "/api/v1/media/playlists", None, "user", None, list[t.Playlist], None),
    (
        "POST",
        "/api/v1/media/playlists",
        {"name": "Lijst"},
        "user",
        t.PlaylistIn,
        t.Playlist,
        201,
    ),
    (
        "GET",
        "/api/v1/media/playlists/{playlist_id}",
        None,
        "user",
        None,
        t.PlaylistDetail,
        None,
    ),
    (
        "PATCH",
        "/api/v1/media/playlists/{playlist_id}",
        {"name": "Andere naam"},
        "user",
        t.PlaylistIn,
        t.Playlist,
        None,
    ),
    ("DELETE", "/api/v1/media/playlists/{playlist_id}", None, "user", None, None, 204),
    (
        "POST",
        "/api/v1/media/playlists/{playlist_id}/items",
        {"node_ids": [ID]},
        "user",
        t.PlaylistItemsIn,
        t.PlaylistItemsOut,
        None,
    ),
    (
        "PUT",
        "/api/v1/media/playlists/{playlist_id}/order",
        {"item_ids": [ID]},
        "user",
        t.PlaylistOrderIn,
        None,
        204,
    ),
    (
        "DELETE",
        "/api/v1/media/playlists/{playlist_id}/items/{item_id}",
        None,
        "user",
        None,
        None,
        204,
    ),
    # M10: beheer
    ("GET", "/api/v1/admin/media", None, "admin", None, t.MediaAdmin, None),
    (
        "PUT",
        "/api/v1/admin/media/config",
        {"max_transcodes": 2},
        "admin",
        t.MediaConfigIn,
        MediaConfig,
        None,
    ),
    ("POST", "/api/v1/admin/media/bench", None, "admin", None, t.Accepted, 202),
    ("DELETE", "/api/v1/admin/media/sessions/{session_id}", None, "admin", None, None, 204),
    ("POST", "/api/v1/admin/media/retry", None, "admin", None, None, None),
    (
        "POST",
        "/api/v1/admin/media/cache/clear",
        {"what": "sessions"},
        "admin",
        t.CacheClearIn,
        None,
        204,
    ),
    ("PUT", "/api/v1/admin/media/tmdb", {"key": "x" * 32}, "admin", t.TmdbKeyIn, None, 204),
    ("DELETE", "/api/v1/admin/media/tmdb", None, "admin", None, None, 204),
    ("POST", "/api/v1/admin/media/tmdb/test", None, "admin", None, None, 204),
    (
        "GET",
        "/api/v1/admin/media/import/sources",
        None,
        "admin",
        None,
        list[t.ImportSource],
        None,
    ),
    (
        "POST",
        "/api/v1/admin/media/import",
        {"source": "films", "target_id": ID, "owner_id": ID},
        "admin",
        t.ImportIn,
        t.Accepted,
        202,
    ),
    ("GET", "/api/v1/admin/media/loadtest", None, "admin", None, t.LoadtestView, None),
    (
        "POST",
        "/api/v1/admin/media/loadtest",
        {"streams": 1},
        "admin",
        t.LoadtestIn,
        t.Accepted,
        202,
    ),
]

VALUES = {"sha": SHA, "size": "w342", "key": "abba"}


def url_of(path: str) -> str:
    url = path
    for name in ("library_id", "node_id", "show_id", "album_id", "playlist_id", "item_id"):
        url = url.replace("{" + name + "}", ID)
    url = url.replace("{session_id}", ID)
    for name, value in VALUES.items():
        url = url.replace("{" + name + "}", value)
    assert "{" not in url, path
    return url


def media_routes() -> dict[tuple[str, str], object]:
    found = {}
    for c in iter_route_contexts(app.routes):
        path = c.path or ""
        if path.startswith(("/api/v1/media", "/api/v1/public/media", "/api/v1/admin/media")):
            for method in c.methods or ():
                found[(method, path)] = c
    return found


def test_elk_eindpunt_van_paragraaf_2_bestaat_met_zijn_vorm():
    routes = media_routes()
    expected = {(m, p) for m, p, *_ in ENDPOINTS}
    assert set(routes) == expected
    for method, path, _body, _who, body_model, response_model, status in ENDPOINTS:
        route = routes[(method, path)]
        body_field = route.body_field
        got = body_field.field_info.annotation if body_field is not None else None
        assert got == body_model, (method, path, got)
        assert route.response_model == response_model, (method, path, route.response_model)
        assert route.status_code == status, (method, path, route.status_code)


def test_de_modellen_staan_in_de_openapi():
    schema = app.openapi()
    paths = schema["paths"]
    for method, path, *_ in ENDPOINTS:
        assert method.lower() in paths[path], (method, path)
    for name in ("Library", "Card", "PlayOut", "MediaMap", "MediaAdmin", "PlaylistDetail"):
        assert name in schema["components"]["schemas"], name


async def shared(client, headers) -> tuple[str, str]:
    """Een bestand met een publieke link: (node_id, sleutel)."""
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "film.mkv", b"x" * 100)).json()
    link = await new_link(client, headers, node["id"], permission="read")
    return node["id"], (await open_link(client, link))["key"]


async def call(client, headers, link, method, path, body, who):
    if who == "link":
        node_id, key = link
        url = path.replace("{node_id}", node_id) + f"?key={key}"
        return await client.request(method, url, json=body)
    extra = headers if who in ("admin", "user") else {}
    return await client.request(method, url_of(path), json=body, headers=extra)


# De eindpunten die hun pakket al bouwde (M3): met media aan geven die geen media_off meer.
# Hun eigen tests staan bij het pakket (test_media_sessions.py, test_media_public.py)
BUILT = {
    ("GET", "/api/v1/media/{node_id}/codecs"),
    ("POST", "/api/v1/media/{node_id}/play"),
    ("POST", "/api/v1/public/media/{node_id}/play"),
}


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True])
async def test_elke_stub_geeft_media_off(client, admin, monkeypatch, enabled):
    headers, _ = admin
    link = await shared(client, headers)
    monkeypatch.setattr(app.state, "media", MediaState(enabled=enabled), raising=False)
    for method, path, body, who, *_ in ENDPOINTS:
        if enabled and (method, path) in BUILT:
            continue
        r = await call(client, headers, link, method, path, body, who)
        assert r.status_code == 503, (method, path, r.status_code, r.text)
        assert r.json()["detail"]["code"] == "media_off", (method, path)


@pytest.mark.anyio
async def test_zonder_login_geen_toegang(client):
    """Wie niet ingelogd is, krijgt 401 voor de eindpunten van een gebruiker en van beheer, ook
    zolang ze een stub zijn; zonder sleutel van een link ook de publieke route. Alleen art (een
    ondertekend adres) vraagt geen login."""
    for method, path, body, who, *_ in ENDPOINTS:
        if who == "none":
            continue
        r = await client.request(method, url_of(path), json=body)
        assert r.status_code == 401, (method, path, r.status_code)


def test_de_gebeurtenissen_van_paragraaf_3_4():
    assert t.EVENTS == {
        "playback.progress": t.PlaybackProgressEvent,
        "media.library": t.LibraryEvent,
        "media.item": t.ItemEvent,
        "media.import": t.ImportEvent,
    }
    fields = {name: set(model.model_fields) for name, model in t.EVENTS.items()}
    assert fields["playback.progress"] == {
        "node_id",
        "position_ms",
        "duration_ms",
        "watched",
        "origin",
        "updated_at",
    }
    assert fields["media.library"] == {"library_id", "scan_state", "item_count"}
    assert fields["media.item"] == {"node_id", "library_id"}
    assert fields["media.import"] == {"job", "state", "done", "total", "skipped", "bytes"}
    data = t.event_data(t.EVENT_ITEM, t.ItemEvent(node_id=uuid.UUID(ID), library_id=uuid.UUID(ID)))
    assert data == {"node_id": ID, "library_id": ID}
