"""De API-contracten van fase 6 (bouwontwerp §3.4) in Z0: elk eindpunt bestaat met zijn pad,
methode, body, antwoordmodel en status, vraagt eerst de inlog (beheer: de rol; de woorden per
pagina: het download-token) en geeft dan 501 not_built tot zijn pakket het vult. Zoeken zelf
werkt al: via select_engine, met LikeFallback. Sinds Z2 zijn ook de woorden per pagina en de
stand van de analyse gebouwd (BUILT), en sinds Z6 de routes van personen, herinneringen en
slimme albums.

Z0b (Keuze 55) zette de routes van gezichten (X2) en zoeken op beeld (X3) erbij, ook als stub;
zoeken met mode=image of like geeft 501 not_built("X3").
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.routing import iter_route_contexts

from app import prefs
from app.geo import types as g
from app.main import app
from app.people import faces_types as f
from app.people import types as p
from app.search import types as t
from app.security import download_token
from tests.test_files import new_user, put, root

pytestmark = pytest.mark.anyio

ID = str(uuid.uuid4())

# (methode, pad, body, wie: user | admin | token, model van de body, antwoordmodel, status bij
# succes, pakket)
ENDPOINTS = [
    # Zoeken (Z1): gebouwd via select_engine
    ("GET", "/api/v1/search", None, "user", None, t.SearchPage, None, "Z1"),
    ("GET", "/api/v1/search/suggest", None, "user", None, t.Suggestions, None, "Z1"),
    # Tekst (Z2: gebouwd; X1)
    (
        "GET",
        "/api/v1/files/{node_id}/pages/{n}/words",
        None,
        "token",
        None,
        t.PageWords,
        None,
        "Z2",
    ),  # fmt: skip
    ("GET", "/api/v1/files/{node_id}/analysis", None, "user", None, t.AnalysisOut, None, "Z2"),
    ("POST", "/api/v1/files/{node_id}/ocr", None, "user", None, None, 202, "X1"),
    # Foto's, plaats en kaart (Z3)
    ("GET", "/api/v1/files/{node_id}/location", None, "user", None, g.FileLocation, None, "Z3"),
    ("GET", "/api/v1/photos/map", None, "user", None, g.MapCells, None, "Z3"),
    ("GET", "/api/v1/photos/map/items", None, "user", None, g.MapItems, None, "Z3"),
    ("GET", "/api/v1/photos/places", None, "user", None, g.Places, None, "Z3"),
    ("GET", "/api/v1/photos/share-check", None, "user", None, g.ShareCheck, None, "Z3"),
    ("GET", "/api/v1/geo/attribution", None, "user", None, g.GeoAttribution, None, "Z3"),
    # Duplicaten (Z3)
    ("GET", "/api/v1/duplicates", None, "user", None, g.DupePage, None, "Z3"),
    (
        "POST",
        "/api/v1/duplicates/ignore",
        {"key": "abc"},
        "user",
        g.DupeIgnoreIn,
        None,
        204,
        "Z3",
    ),  # fmt: skip
    # Herinneringen en instellingen (Z6)
    ("GET", "/api/v1/photos/memories", None, "user", None, p.Memories, None, "Z6"),
    ("GET", "/api/v1/photos/memories/hide", None, "user", None, p.MemoryHides, None, "Z6"),
    (
        "POST",
        "/api/v1/photos/memories/hide",
        {"kind": "day", "ref": "2024-07-01"},
        "user",
        p.MemoryHide,
        None,
        204,
        "Z6",
    ),  # fmt: skip
    (
        "DELETE",
        "/api/v1/photos/memories/hide/{kind}/{ref}",
        None,
        "user",
        None,
        None,
        204,
        "Z6",
    ),  # fmt: skip
    ("GET", "/api/v1/me/photo-settings", None, "user", None, prefs.PhotoSettings, None, "Z6"),
    (
        "PATCH",
        "/api/v1/me/photo-settings",
        {"location": "uit"},
        "user",
        prefs.PhotoSettingsPatch,
        prefs.PhotoSettings,
        None,
        "Z6",
    ),  # fmt: skip
    # Personen (Z6)
    ("GET", "/api/v1/people", None, "user", None, p.People, None, "Z6"),
    ("POST", "/api/v1/people", {"name": "Oma"}, "user", p.PersonIn, p.Person, 201, "Z6"),
    (
        "PATCH",
        "/api/v1/people/{person_id}",
        {"hidden": True},
        "user",
        p.PersonPatch,
        p.Person,
        None,
        "Z6",
    ),  # fmt: skip
    (
        "POST",
        "/api/v1/people/{person_id}/merge",
        {"into": ID},
        "user",
        p.MergeIn,
        p.Person,
        None,
        "Z6",
    ),  # fmt: skip
    ("DELETE", "/api/v1/people/{person_id}", None, "user", None, None, 204, "Z6"),
    ("GET", "/api/v1/people/{person_id}/photos", None, "user", None, p.PersonPhotos, None, "Z6"),
    ("GET", "/api/v1/files/{node_id}/people", None, "user", None, p.FilePeople, None, "Z6"),
    (
        "POST",
        "/api/v1/files/{node_id}/people",
        {"name": "Oma"},
        "user",
        p.AddPersonIn,
        None,
        201,
        "Z6",
    ),  # fmt: skip
    (
        "DELETE",
        "/api/v1/files/{node_id}/people/{person_id}",
        None,
        "user",
        None,
        None,
        204,
        "Z6",
    ),  # fmt: skip
    # Slimme albums (Z6)
    (
        "PUT",
        "/api/v1/albums/{album_id}/rule",
        {"rule": {"q": "strand"}},
        "user",
        p.AlbumRuleIn,
        p.AlbumRuleSaved,
        None,
        "Z6",
    ),  # fmt: skip
    (
        "POST",
        "/api/v1/albums/{album_id}/rule/confirm",
        {"share_new": False},
        "user",
        p.RuleConfirmIn,
        p.AlbumRuleSaved,
        None,
        "Z6",
    ),  # fmt: skip
    ("DELETE", "/api/v1/albums/{album_id}/rule", None, "user", None, None, 204, "Z6"),
    ("POST", "/api/v1/albums/{album_id}/refresh", None, "user", None, None, 202, "Z6"),
    # Beheer (Z7)
    ("GET", "/api/v1/admin/search", None, "admin", None, t.AdminSearch, None, "Z7"),
    (
        "PATCH",
        "/api/v1/admin/search",
        {"paused": True},
        "admin",
        t.AdminSearchPatch,
        t.AdminSearch,
        None,
        "Z7",
    ),  # fmt: skip
    (
        "POST",
        "/api/v1/admin/search/rebuild",
        {"what": "text"},
        "admin",
        t.RebuildIn,
        None,
        202,
        "Z7",
    ),  # fmt: skip
    ("GET", "/api/v1/admin/search/failed", None, "admin", None, t.FailedPage, None, "Z7"),
    ("POST", "/api/v1/admin/search/retry", None, "admin", None, None, 202, "Z7"),
    # Gezichten automatisch (X2; stubs van Z0b)
    ("GET", "/api/v1/people/faces/status", None, "user", None, f.FacesStatus, None, "X2"),
    (
        "GET",
        "/api/v1/people/faces/suggestions",
        None,
        "user",
        None,
        f.SuggestionsByPerson | f.FacePage,
        None,
        "X2",
    ),  # fmt: skip
    ("GET", "/api/v1/people/faces/groups", None, "user", None, f.FaceGroups, None, "X2"),
    ("GET", "/api/v1/people/faces/groups/{key}", None, "user", None, f.FacePage, None, "X2"),
    (
        "POST",
        "/api/v1/people/faces/confirm",
        {"faces": [ID], "name": "Oma"},
        "user",
        f.FacesConfirmIn,
        None,
        204,
        "X2",
    ),  # fmt: skip
    (
        "POST",
        "/api/v1/people/faces/reject",
        {"faces": [ID], "person_id": ID},
        "user",
        f.FacesRejectIn,
        None,
        204,
        "X2",
    ),  # fmt: skip
    ("POST", "/api/v1/people/faces/hide", {"faces": [ID]}, "user", f.FacesIn, None, 204, "X2"),
    ("POST", "/api/v1/people/faces/reset", {"faces": [ID]}, "user", f.FacesIn, None, 204, "X2"),
    (
        "POST",
        "/api/v1/people/faces/wipe-unnamed",
        None,
        "user",
        None,
        f.FacesQueued,
        202,
        "X2",
    ),  # fmt: skip
    ("GET", "/api/v1/people/faces/{face_id}/crop", None, "user", None, None, None, "X2"),
    ("GET", "/api/v1/files/{node_id}/faces", None, "user", None, list[f.FaceOut], None, "X2"),
    ("POST", "/api/v1/files/{node_id}/faces/rescan", None, "user", None, None, 202, "X2"),
    # Zoeken op beeld (X3; stubs van Z0b). Zoeken zelf: test_zoeken_op_beeld_is_van_x3
    ("GET", "/api/v1/photos/beeld", None, "user", None, t.BeeldStatus, None, "X3"),
    ("DELETE", "/api/v1/admin/search/beeld", None, "admin", None, None, 202, "X3"),
]
BUILT = {
    ("GET", "/api/v1/search"),
    ("GET", "/api/v1/search/suggest"),
    # Z2: de woorden per pagina en de stand van de analyse (tests/test_pages_words.py)
    ("GET", "/api/v1/files/{node_id}/pages/{n}/words"),
    ("GET", "/api/v1/files/{node_id}/analysis"),
    # Z6: personen (tests/people/test_people.py)
    ("GET", "/api/v1/people"),
    ("POST", "/api/v1/people"),
    ("PATCH", "/api/v1/people/{person_id}"),
    ("POST", "/api/v1/people/{person_id}/merge"),
    ("DELETE", "/api/v1/people/{person_id}"),
    ("GET", "/api/v1/people/{person_id}/photos"),
    ("GET", "/api/v1/files/{node_id}/people"),
    ("POST", "/api/v1/files/{node_id}/people"),
    ("DELETE", "/api/v1/files/{node_id}/people/{person_id}"),
    # Z6: herinneringen (tests/photos/test_memories.py)
    ("GET", "/api/v1/photos/memories"),
    ("GET", "/api/v1/photos/memories/hide"),
    ("POST", "/api/v1/photos/memories/hide"),
    ("DELETE", "/api/v1/photos/memories/hide/{kind}/{ref}"),
    # Z6: slimme albums (tests/albums/test_album_rules.py)
    ("PUT", "/api/v1/albums/{album_id}/rule"),
    ("POST", "/api/v1/albums/{album_id}/rule/confirm"),
    ("DELETE", "/api/v1/albums/{album_id}/rule"),
    ("POST", "/api/v1/albums/{album_id}/refresh"),
    # Z6: de foto-instellingen (tests/photos/test_photo_settings.py)
    ("GET", "/api/v1/me/photo-settings"),
    ("PATCH", "/api/v1/me/photo-settings"),
}
# De vraag die een stub nodig heeft om bij de route te komen
QUERY = {
    "/api/v1/search/suggest": {"q": "jaar"},
    "/api/v1/photos/map": {"bbox": "4.8,52.3,5.0,52.4", "z": 10},
    "/api/v1/photos/map/items": {"bbox": "4.8,52.3,5.0,52.4"},
    "/api/v1/photos/share-check": {"node": ID},
}
# De paden van fase 6 (de rest van de app staat in zijn eigen tests)
PREFIXES = (
    "/api/v1/search",
    "/api/v1/photos/map",
    "/api/v1/photos/places",
    "/api/v1/photos/share-check",
    "/api/v1/photos/memories",
    "/api/v1/geo/",
    "/api/v1/duplicates",
    "/api/v1/people",
    "/api/v1/me/photo-settings",
    "/api/v1/admin/search",
    "/api/v1/photos/beeld",
)
SUFFIXES = ("/words", "/analysis", "/ocr", "/location", "/people", "/rule", "/rule/confirm",
            "/refresh", "/faces", "/faces/rescan")  # fmt: skip


def url_of(path: str, node_id: str = ID) -> str:
    url = path.replace("{node_id}", node_id).replace("{n}", "1")
    for name in ("person_id", "album_id", "face_id"):
        url = url.replace("{" + name + "}", ID)
    url = url.replace("{kind}", "day").replace("{ref}", "2024-07-01").replace("{key}", "3:17")
    assert "{" not in url, path
    return url


def fase6_routes() -> dict[tuple[str, str], object]:
    found = {}
    for c in iter_route_contexts(app.routes):
        path = c.path or ""
        ours = path.startswith(PREFIXES) or (
            path.startswith(("/api/v1/files/{node_id}/", "/api/v1/albums/{album_id}/"))
            and path.endswith(SUFFIXES)
        ) or path.startswith("/api/v1/files/{node_id}/people/")  # fmt: skip
        if ours:
            for method in c.methods or ():
                found[(method, path)] = c
    return found


def test_elk_eindpunt_van_paragraaf_3_4_bestaat_met_zijn_vorm():
    routes = fase6_routes()
    assert set(routes) == {(m, path) for m, path, *_ in ENDPOINTS}
    for method, path, _body, _who, body_model, response_model, status, _pkg in ENDPOINTS:
        route = routes[(method, path)]
        body_field = route.body_field
        got = body_field.field_info.annotation if body_field is not None else None
        assert got == body_model, (method, path, got)
        assert route.response_model == response_model, (method, path, route.response_model)
        assert route.status_code == status, (method, path, route.status_code)


def test_de_modellen_staan_in_de_openapi():
    schema = app.openapi()
    for method, path, *_ in ENDPOINTS:
        assert method.lower() in schema["paths"][path], (method, path)
    for name in ("SearchPage", "PageWords", "MapCells", "DupePage", "People", "AdminSearch",
                 "AdminFaces", "AdminBeeld", "BeeldStatus", "FacesStatus", "FaceOut"):  # fmt: skip
        assert name in schema["components"]["schemas"], name


async def test_elke_stub_geeft_501_not_built(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "verslag.pdf", b"%PDF-1.4\n")).json()
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    token = download_token(settings, uuid.UUID(me), uuid.UUID(node["id"]))
    for method, path, body, who, *_rest, package in ENDPOINTS:
        if (method, path) in BUILT:
            continue
        params = dict(QUERY.get(path, {}))
        if who == "token":
            params["token"] = token
        r = await client.request(
            method, url_of(path, node["id"]), json=body, params=params, headers=headers
        )
        assert r.status_code == 501, (method, path, r.status_code, r.text)
        detail = r.json()["detail"]
        assert detail["code"] == "not_built", (method, path)
        assert detail["params"] == {"package": package}, (method, path)


async def test_een_vast_getal_in_de_query_string_komt_bij_de_stub(client, admin):
    """Een keuze uit vaste getallen in de query (?size=96, ?hidden=1) komt als tekst binnen, zoals
    de web hem stuurt (wireQuery). Literal[96, 192] alleen neemt geen "96": dan gaf elke waarde
    422 en kwam niemand bij de route (gevonden bij de samenvoeging van Z0b). Een stub geeft dan
    501, een gebouwde route zijn gewone antwoord (Z6: GET /people?hidden=1)."""
    headers, _ = admin
    schema = app.openapi()["paths"]
    gezien = set()
    for method, path, body, who, _model, _out, status, _package in ENDPOINTS:
        if who == "token":
            continue
        expect = (status or 200) if (method, path) in BUILT else 501
        for param in schema[path][method.lower()].get("parameters", []):
            keuzes = param["schema"].get("enum", [])
            if param["in"] != "query" or not keuzes or not all(type(k) is int for k in keuzes):
                continue
            for keuze in keuzes:
                params = {**QUERY.get(path, {}), param["name"]: str(keuze)}
                r = await client.request(
                    method, url_of(path), json=body, params=params, headers=headers
                )
                assert r.status_code == expect, (method, path, params, r.status_code, r.text)
            params = {**QUERY.get(path, {}), param["name"]: str(max(keuzes) + 1)}
            r = await client.request(
                method, url_of(path), json=body, params=params, headers=headers
            )
            assert r.status_code == 422, (method, path, params, r.status_code)
            # Review Z0b, veilig V1: alleen gewone cijfers worden een getal. De rest geeft de
            # 422 van elke andere fout, zonder de tekst van int() en zonder de waarde zelf.
            for fout in ("abc", "9" * 5000, "9_6", "٩٦", " 96", "+1", "-0", "1e1"):
                params = {**QUERY.get(path, {}), param["name"]: fout}
                r = await client.request(
                    method, url_of(path), json=body, params=params, headers=headers
                )
                assert r.status_code == 422, (method, path, fout, r.status_code)
                detail = r.json()["detail"]
                assert detail["message"] == "Ongeldige invoer", (path, fout, detail["message"])
                assert detail["code"] == "invalid_input"
                assert detail["params"] == {"field": f"query.{param['name']}"}
                assert [e["type"] for e in detail["errors"]] == ["literal_error"], (path, fout)
                assert all("input" not in e for e in detail["errors"])
            for fout in ("abc", "9" * 5000):
                params = {**QUERY.get(path, {}), param["name"]: fout}
                r = await client.request(
                    method, url_of(path), json=body, params=params, headers=headers
                )
                assert fout not in r.text and "int()" not in r.text, (path, fout)
            gezien.add((path, param["name"]))
    assert gezien == {
        ("/api/v1/people", "hidden"),
        ("/api/v1/people/faces/{face_id}/crop", "size"),
    }


async def test_zonder_login_geen_toegang_en_beheer_alleen_voor_beheerders(client, admin):
    headers, _ = admin
    gewoon = await new_user(client, headers, "gewoon@example.nl")
    for method, path, body, who, *_ in ENDPOINTS:
        url, params = url_of(path), QUERY.get(path, {})
        r = await client.request(method, url, json=body, params=params)
        # De woorden per pagina gaan met het token van de viewer, niet met de inlog
        expect = 422 if who == "token" else 401
        assert r.status_code == expect, (method, path, r.status_code)
        if who == "admin":
            r = await client.request(method, url, json=body, params=params, headers=gewoon)
            assert r.status_code == 403, (method, path, r.status_code)
            assert r.json()["detail"]["code"] == "admin_only"


async def test_de_woorden_alleen_met_het_token_van_dit_bestand(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    a = (await put(client, headers, root_id, "a.pdf", b"%PDF-1.4\n")).json()["id"]
    b = (await put(client, headers, root_id, "b.pdf", b"%PDF-1.4\n%b\n")).json()["id"]
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    token = download_token(settings, uuid.UUID(me), uuid.UUID(a))
    r = await client.get(f"/api/v1/files/{b}/pages/1/words", params={"token": token})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "wrong_token"
    r = await client.get(f"/api/v1/files/{a}/pages/1/words", params={"token": "rommel"})
    assert r.status_code == 401
    r = await client.get(f"/api/v1/files/{a}/pages/0/words", params={"token": token})
    assert r.status_code == 422


async def test_zoeken_werkt_via_likefallback(client, admin, settings, monkeypatch):
    """Een vraag zoals in 0.7.0 (q) werkt; het antwoord heeft de velden van fase 6 erbij. Met de
    index uit via LikeFallback, met de index aan (sinds Z1) via FtsEngine: dezelfde treffer,
    met engine fts en een gerangschikte pagina. De grenzen van 0.7.0 (archive als soort, limit
    tot 200) zijn sinds Z4 in beide motoren een 422 ("De overgang" in §3.4)."""
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    await put(client, headers, root_id, "Jaarverslag 2025.pdf", b"%PDF-1.4\n")
    await put(client, headers, root_id, "notities.txt", b"jaarverslag")
    monkeypatch.setattr(settings, "search_index", "uit")
    r = await client.get("/api/v1/search", params={"q": "jaarverslag"}, headers=headers)
    assert r.status_code == 200, r.text
    page = r.json()
    assert [h["node"]["name"] for h in page["results"]] == ["Jaarverslag 2025.pdf"]
    hit = page["results"][0]
    # Het pad zoals in 0.7.0: van de hoofdmap (zonder naam) tot de map waar het in staat
    assert hit["path"] == [{"id": root_id, "name": ""}]
    assert hit["match"] == "name" and hit["snippet"] is None
    assert page["more"] is False and page["engine"] == "like" and page["names_only"] is True
    assert page["partial"] is False and page["ranked"] is False and page["offset"] == 0
    # De grenzen van 0.7.0: archive als soort en limit tot 200 zijn sinds Z4 een 422
    for params, veld in (
        ({"type": "archive"}, "query.type.0"),
        ({"q": "x", "limit": 200}, "query.limit"),
    ):
        r = await client.get("/api/v1/search", params=params, headers=headers)
        assert r.status_code == 422, (params, r.text)
        assert r.json()["detail"]["params"] == {"field": veld}, params
    r = await client.get("/api/v1/search/suggest", params={"q": "jaar"}, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"names": [], "people": [], "places": [], "tags": [], "albums": []}
    r = await client.get("/api/v1/search/suggest", params={"q": ""}, headers=headers)
    assert r.status_code == 422

    # Met de index aan: FtsEngine (Z1), met hetzelfde pad. Een ruime grens: een volle ronde van
    # de garbage collector in ronde A gaf anders terecht een gedeeltelijke pagina (Keuze 9)
    monkeypatch.setattr(settings, "search_index", "aan")
    monkeypatch.setattr(settings, "search_deadline_ms", 10_000)
    r = await client.get("/api/v1/search", params={"q": "jaarverslag"}, headers=headers)
    assert r.status_code == 200, r.text
    page = r.json()
    assert [h["node"]["name"] for h in page["results"]] == ["Jaarverslag 2025.pdf"]
    hit = page["results"][0]
    assert hit["path"] == [{"id": root_id, "name": ""}]
    assert hit["match"] == "name" and hit["snippet"] is None
    assert page["more"] is False and page["engine"] == "fts" and page["names_only"] is False
    assert page["partial"] is False and page["ranked"] is True and page["offset"] == 0
    assert page["after"] is None and page["mode"] == "text" and page["fallback"] is None
    # Ook met de index aan: geen tak meer naar LikeFallback, maar dezelfde 422
    for params, veld in (
        ({"type": "archive"}, "query.type.0"),
        ({"q": "x", "limit": 200}, "query.limit"),
    ):
        r = await client.get("/api/v1/search", params=params, headers=headers)
        assert r.status_code == 422, (params, r.text)
        assert r.json()["detail"]["params"] == {"field": veld}, params


async def test_zoeken_op_beeld_is_van_x3(client, admin):
    """mode=image (met q of like) en like alleen: eerst de inlog, dan 501 not_built("X3"). Een
    andere mode: 422."""
    headers, _ = admin
    for params in (
        {"mode": "image", "q": "strand"},
        {"mode": "image", "like": ID},
        {"like": ID},
        {"mode": "image", "q": "strand", "type": "image", "limit": 10},
    ):
        r = await client.get("/api/v1/search", params=params)
        assert r.status_code == 401, (params, r.status_code)
        r = await client.get("/api/v1/search", params=params, headers=headers)
        assert r.status_code == 501, (params, r.text)
        assert r.json()["detail"]["code"] == "not_built"
        assert r.json()["detail"]["params"] == {"package": "X3"}
    for params in ({"mode": "foto", "q": "x"}, {"mode": "image", "like": "geen-id"}):
        r = await client.get("/api/v1/search", params=params, headers=headers)
        assert r.status_code == 422, (params, r.status_code)


async def test_gezichten_bevestigen_met_id_of_naam_niet_allebei(client, admin):
    """De bodies van X2 worden gelezen voor de stub antwoordt: een fout geeft 422."""
    headers, _ = admin
    url = "/api/v1/people/faces/confirm"
    for body in (
        {"faces": [ID]},
        {"faces": [ID], "name": "Oma", "person_id": ID},
        {"faces": [], "name": "Oma"},
        {"faces": [ID] * (f.MAX_FACES + 1), "name": "Oma"},
        {"faces": [ID], "name": "Oma", "rest": True},
    ):
        r = await client.post(url, json=body, headers=headers)
        assert r.status_code == 422, (body, r.status_code)
    r = await client.post(url, json={"faces": [ID], "person_id": ID}, headers=headers)
    assert r.status_code == 501
    r = await client.get(f"/api/v1/people/faces/{ID}/crop", params={"size": 128}, headers=headers)
    assert r.status_code == 422


async def test_een_uitsnede_alleen_met_de_inlog_in_de_header(client, admin):
    """Review Z0b, veilig V2 en contract F2: de uitsnede van X2 wil de inlog als Bearer-header,
    zoals elke route van X2. Een <img> stuurt die niet mee (401), en het token in de query telt
    niet; de web haalt hem daarom met een fetch (faceCrop in web/src/photos/faces/api.ts)."""
    headers, _ = admin
    token = headers["Authorization"].removeprefix("Bearer ")
    url = f"/api/v1/people/faces/{ID}/crop"
    for params in ({"size": "96"}, {"size": "96", "token": token}, {"size": "96", "sig": "x"}):
        r = await client.get(url, params=params)
        assert r.status_code == 401, (params, r.status_code)
        assert r.json()["detail"]["code"] == "not_logged_in"
    r = await client.get(url, params={"size": "96"}, headers=headers)
    assert r.status_code == 501 and r.json()["detail"]["params"] == {"package": "X2"}


async def test_met_de_index_uit_ook_like(client, admin, settings, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(settings, "search_index", "uit")
    r = await client.get("/api/v1/search", params={"q": "iets"}, headers=headers)
    assert r.status_code == 200 and r.json()["engine"] == "like"


def test_de_gebeurtenissen_van_paragraaf_3_4():
    assert t.EVENTS == {
        "search.ready": t.SearchReadyEvent,
        "people.changed": t.PeopleChangedEvent,
        "search.progress": t.SearchProgressEvent,
    }
    fields = {name: set(model.model_fields) for name, model in t.EVENTS.items()}
    assert fields == {
        "search.ready": {"nodes", "step"},
        "people.changed": set(),
        "search.progress": {"what", "done"},
    }
    data = t.event_data(
        t.EVENT_SEARCH_READY, t.SearchReadyEvent(nodes=[uuid.UUID(ID)], step="text")
    )
    assert data == {"nodes": [ID], "step": "text"}
    with pytest.raises(TypeError):
        t.event_data(t.EVENT_SEARCH_READY, t.PeopleChangedEvent())


def test_een_persoon_toevoegen_met_id_of_naam_niet_allebei():
    assert p.AddPersonIn(name="Oma").name == "Oma"
    assert p.AddPersonIn(person_id=ID, box=(0.1, 0.2, 0.3, 0.4)).box == (0.1, 0.2, 0.3, 0.4)
    for body in ({}, {"name": "Oma", "person_id": ID}):
        with pytest.raises(ValueError):
            p.AddPersonIn.model_validate(body)
