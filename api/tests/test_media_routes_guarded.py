"""Regel 6 van fase 5 (§2.0): elk eindpunt onder /api/v1/stream/ gaat langs sessions.guard.

De test loopt ALLE routes onder /api/v1/stream/ langs, ook de stubs van M0, en kijkt:
- dat de lijst precies die van §2 is (een nieuwe route valt op en moet hier bij);
- dat sessions.guarded een dependency van de route zelf is (van de router), zodat de guard vóór
  de rest van de route loopt, ook vóór de controle van de andere parameters;
- dat elke route bij elk verzoek guard aanroept met de sid uit het pad;
- met media uit 503 media_off, met media aan (geen sessies in M0) 410 media_session_gone;
- de koppen van Keuze 35 op elk antwoord: Cross-Origin-Resource-Policy: same-origin, nooit
  Content-Disposition, en no-store op een fout.
"""

from __future__ import annotations

import pytest
from fastapi.routing import RouteContext, iter_route_contexts

from app.errors import fail
from app.main import app
from app.media import sessions
from app.media.state import MediaState

PREFIX = "/api/v1/stream/"
# Ook een route op /api/v1/stream zelf zou meetellen
ROOT = "/api/v1/stream"
SID = "s1.geheim-deel"

# §2 M3, M4, M5 en M9: (methode, pad)
EXPECTED = {
    ("GET", "/api/v1/stream/{sid}/map.json"),
    ("GET", "/api/v1/stream/{sid}/master.m3u8"),
    ("GET", "/api/v1/stream/{sid}/v.m3u8"),
    ("GET", "/api/v1/stream/{sid}/init.mp4"),
    ("GET", "/api/v1/stream/{sid}/{n}.m4s"),
    ("GET", "/api/v1/stream/{sid}/subs/{track}.vtt"),
    ("GET", "/api/v1/stream/{sid}/trick.vtt"),
    ("GET", "/api/v1/stream/{sid}/trick/{i}.jpg"),
    ("GET", "/api/v1/stream/{sid}/audio.mp4"),
    ("GET", "/api/v1/stream/{sid}/file"),
    ("HEAD", "/api/v1/stream/{sid}/file"),
    ("DELETE", "/api/v1/stream/{sid}"),
}

# Een geldige waarde voor elke andere parameter in het pad
DUMMY = {"n": "0", "i": "0", "track": "e0"}


def stream_routes() -> list[RouteContext]:
    """Alle routes onder /api/v1/stream, ook die in een ingesloten router (FastAPI 0.150 houdt
    een router met include_router als één element in app.routes)."""
    found = [c for c in iter_route_contexts(app.routes) if (c.path or "").startswith(ROOT)]
    assert len(found) >= 11, "de routes onder /api/v1/stream zijn niet gevonden"
    return found


def calls() -> list[tuple[str, str, str]]:
    """(methode, route, url) voor elke methode van elke route onder /api/v1/stream/."""
    out = []
    for route in stream_routes():
        url = route.path.replace("{sid}", SID)
        for name, value in DUMMY.items():
            url = url.replace("{" + name + "}", value)
        assert "{" not in url, f"geen testwaarde voor een parameter van {route.path}"
        for method in sorted(route.methods):
            out.append((method, route.path, url))
    return out


def all_methods() -> set[tuple[str, str]]:
    return {(m, r.path) for r in stream_routes() for m in r.methods}


def test_de_routes_onder_stream_zijn_die_van_paragraaf_2():
    assert all_methods() == EXPECTED


def test_guarded_is_een_dependency_van_elke_route():
    for route in stream_routes():
        # De dependencies van de router komen eerst: de guard loopt vóór alles van de route
        first = route.dependant.dependencies[0]
        assert first.call is sessions.guarded, route.path
        assert [p.name for p in first.path_params] == ["sid"], route.path


def check_headers(response, path: str) -> None:
    assert response.headers.get("cross-origin-resource-policy") == "same-origin", path
    assert "content-disposition" not in response.headers, path
    assert response.headers.get("cache-control") == "no-store", path


@pytest.mark.anyio
async def test_media_uit_geeft_media_off_op_elke_route(client, monkeypatch):
    monkeypatch.setattr(app.state, "media", MediaState(enabled=False), raising=False)
    for method, path, url in calls():
        r = await client.request(method, url)
        assert r.status_code == 503, (method, path, r.status_code, r.text)
        if method != "HEAD":
            assert r.json()["detail"]["code"] == "media_off", (method, path)
        check_headers(r, path)


@pytest.mark.anyio
async def test_media_aan_zonder_sessie_geeft_session_gone(client, monkeypatch):
    monkeypatch.setattr(app.state, "media", MediaState(enabled=True), raising=False)
    for method, path, url in calls():
        r = await client.request(method, url)
        assert r.status_code == 410, (method, path, r.status_code, r.text)
        if method != "HEAD":
            assert r.json()["detail"]["code"] == "media_session_gone", (method, path)
        check_headers(r, path)


@pytest.mark.anyio
async def test_elke_route_roept_guard_aan_met_de_sid(client, monkeypatch):
    seen: list[str] = []

    async def guard(request, sid):
        seen.append(sid)
        raise fail(410, "media_session_gone", "weg")

    monkeypatch.setattr(sessions, "guard", guard)
    for method, path, url in calls():
        before = len(seen)
        r = await client.request(method, url)
        assert r.status_code == 410, (method, path)
        # Precies één keer per verzoek (de dependency van de router en die van de route
        # delen hun uitkomst)
        assert seen[before:] == [SID], (method, path, seen[before:])


@pytest.mark.anyio
async def test_guard_gaat_voor_de_controle_van_de_andere_parameters(client, monkeypatch):
    """Zonder sessie zegt een fout in een parameter niets: eerst de guard, dan pas een 422."""
    monkeypatch.setattr(app.state, "media", MediaState(enabled=True), raising=False)
    for url in (
        f"{PREFIX}{SID}/abc.m4s",
        f"{PREFIX}{SID}/-1.m4s",
        f"{PREFIX}{SID}/init.mp4?g=0",
        f"{PREFIX}{SID}/subs/zz.vtt",
        f"{PREFIX}{SID}/trick/x.jpg",
    ):
        r = await client.get(url)
        assert r.status_code == 410, (url, r.status_code, r.text)
        check_headers(r, url)


@pytest.mark.anyio
async def test_een_route_met_sessie_komt_wel_bij_de_route(client, monkeypatch):
    """Met een sessie (hier nagebootst) roept de route zelf zijn pakket aan: de stub geeft dan
    503 media_off. Zo weet je dat de guard niet alles tegenhoudt."""
    monkeypatch.setattr(app.state, "media", MediaState(enabled=True), raising=False)

    async def guard(request, sid):
        return object()

    monkeypatch.setattr(sessions, "guard", guard)
    for method, path, url in calls():
        r = await client.request(method, url)
        assert r.status_code == 503, (method, path, r.status_code)
        check_headers(r, path)


@pytest.mark.anyio
async def test_de_middleware_haalt_weg_wat_een_route_zelf_zet():
    """Een route die zelf Content-Disposition of een andere CORP zet: StreamHeaders haalt het
    weg en zet same-origin. De Cache-Control van de route blijft staan."""
    from httpx import ASGITransport, AsyncClient
    from starlette.responses import Response

    from app.main import StreamHeaders

    async def inner(scope, receive, send):
        response = Response(
            b"x",
            headers={
                "Content-Disposition": "attachment; filename=film.mkv",
                "Cross-Origin-Resource-Policy": "cross-origin",
                "Cache-Control": "private, max-age=3600",
            },
        )
        await response(scope, receive, send)

    transport = ASGITransport(app=StreamHeaders(inner))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"{PREFIX}{SID}/0.m4s")
        assert r.headers.get_list("cross-origin-resource-policy") == ["same-origin"]
        assert "content-disposition" not in r.headers
        assert r.headers.get_list("cache-control") == ["private, max-age=3600"]
        # Buiten /stream/ verandert er niets
        other = await c.get("/api/v1/files/x/content")
        assert other.headers["content-disposition"].startswith("attachment")
        assert other.headers["cross-origin-resource-policy"] == "cross-origin"


@pytest.mark.anyio
async def test_buiten_stream_geen_corp(client):
    r = await client.get("/api/v1/media/libraries")
    assert "cross-origin-resource-policy" not in r.headers
