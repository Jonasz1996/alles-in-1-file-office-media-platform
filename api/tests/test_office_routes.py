"""De routes van office in O0 (§3.3, §3.4): status is af, de rest zijn stubs die 503
office_off geven; /api/v1/admin/office alleen voor beheerders; /internal/ alleen op de
interne app; en /office/ via de proxy alleen met office_dev_ungated. Zonder office is de app
precies fase 3."""

import json
import secrets
import uuid
from urllib.parse import unquote

import httpx
import pytest

from app.config import Settings
from app.main import app, internal_app
from app.office import engine, proxy, runtime
from app.office.oo import client as ds_client
from app.office.state import OfficeConfig, office_policy, office_state
from tests.factories import code_of
from tests.fake_docserver import FakeConfig, create_app, protocol


def office_settings(settings: Settings, **extra) -> Settings:
    return Settings(
        **{
            **settings.model_dump(),
            "office_url": "http://office",
            "office_app_url": "http://platform:8081",
            "office_secret": secrets.token_urlsafe(32),
            **extra,
        }
    )


@pytest.fixture
def with_office(settings):
    """Zet office-instellingen via runtime (zoals de lifespan dat doet) en ruimt op."""
    made: list[Settings] = []

    def bind(**extra) -> Settings:
        chosen = office_settings(settings, **extra)
        runtime.bind(app, chosen)
        made.append(chosen)
        return chosen

    yield bind
    runtime.unbind()
    engine.reset()
    ds_client.transport = None
    for name in ("office", "office_policy"):
        if hasattr(app.state, name):
            delattr(app.state, name)


@pytest.fixture
async def user_headers(client, admin):
    headers, _ = admin
    new = {"email": "anna@example.nl", "display_name": "Anna", "password": "lang-genoeg-1"}
    r = await client.post("/api/v1/users", json=new, headers=headers)
    assert r.status_code == 201, r.text
    r = await client.post(
        "/api/v1/auth/login", json={"email": new["email"], "password": new["password"]}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class Stream(httpx.AsyncByteStream):
    """Een antwoord van DS in stukken, zoals van het netwerk."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self):
        for start in range(0, len(self.data), 1000):
            yield self.data[start : start + 1000]


def respond(status: int = 200, headers: dict | None = None, body: bytes = b"") -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=Stream(body))


def core(r: httpx.Response) -> dict:
    data = r.json()
    return {k: data[k] for k in ("enabled", "editing", "state")}


# --- status ---


@pytest.mark.anyio
async def test_status_zonder_office(client, admin):
    headers, _ = admin
    r = await client.get("/api/v1/office/status", headers=headers)
    assert r.status_code == 200
    assert core(r) == {"enabled": False, "editing": False, "state": "off"}
    assert (await client.get("/api/v1/office/status")).status_code == 401


@pytest.mark.anyio
async def test_status_verkeerd_ingesteld(client, admin, with_office):
    headers, _ = admin
    with_office(office_secret="te-kort")
    r = await client.get("/api/v1/office/status", headers=headers)
    assert core(r) == {"enabled": False, "editing": False, "state": "misconfigured"}


@pytest.mark.anyio
async def test_status_volgt_de_gezondheid_en_de_beheerder(client, user_headers, with_office):
    with_office()
    state = office_state(app)
    for health, editing in (("starting", False), ("down", False), ("ok", True)):
        state.health = health
        r = await client.get("/api/v1/office/status", headers=user_headers)
        assert core(r) == {"enabled": True, "editing": editing, "state": health}
    # De beheerder zet bewerken uit (OfficeConfig.enabled)
    office_policy(app).config = OfficeConfig(enabled=False)
    r = await client.get("/api/v1/office/status", headers=user_headers)
    assert core(r) == {"enabled": False, "editing": False, "state": "ok"}


# --- stubs ---

NODE = str(uuid.uuid4())
STUBS = [
    ("GET", f"/api/v1/office/files/{NODE}", None),
    ("POST", f"/api/v1/office/files/{NODE}/session", {}),
    ("POST", f"/api/v1/office/files/{NODE}/save", None),
    ("POST", "/api/v1/office/gate", None),
    ("POST", "/api/v1/office/new", {"parent_id": NODE, "kind": "docx", "name": "Nieuw"}),
    ("POST", f"/api/v1/office/files/{NODE}/convert", None),
    ("POST", f"/api/v1/office/files/{NODE}/pdf", None),
    ("GET", f"/api/v1/office/pdf/{NODE}?token=x", None),
]
ADMIN_STUBS = [
    ("GET", "/api/v1/admin/office", None),
    ("PUT", "/api/v1/admin/office", OfficeConfig().model_dump()),
    ("POST", "/api/v1/admin/office/checkpoint", None),
    ("POST", "/api/v1/admin/office/conversions/retry", None),
    ("POST", "/api/v1/admin/office/unsaved/k1-abc/restore", None),
    ("DELETE", "/api/v1/admin/office/unsaved/k1-abc", None),
]


@pytest.mark.anyio
async def test_stubs_geven_office_off(client, user_headers):
    for method, path, body in STUBS:
        r = await client.request(method, path, json=body, headers=user_headers)
        assert r.status_code == 503, (path, r.text)
        assert code_of(r) == "office_off", path
    # De vorm van de bodies hoort bij het bevroren contract
    for path, body in (
        (f"/api/v1/office/files/{NODE}/session", {"type": "tv"}),
        ("/api/v1/office/new", {"parent_id": NODE, "kind": "exe"}),
    ):
        r = await client.post(path, json=body, headers=user_headers)
        assert r.status_code == 422, path


@pytest.mark.anyio
async def test_stubs_vragen_een_login(client):
    for method, path, body in STUBS:
        if path.startswith("/api/v1/office/pdf/"):
            continue  # met een token in het adres, zoals de downloadlinks
        r = await client.request(method, path, json=body)
        assert r.status_code == 401, path


@pytest.mark.anyio
async def test_beheer_van_office_alleen_voor_beheerders(client, admin, user_headers):
    headers, _ = admin
    for method, path, body in ADMIN_STUBS:
        r = await client.request(method, path, json=body, headers=user_headers)
        assert r.status_code == 403, path
        r = await client.request(method, path, json=body)
        assert r.status_code == 401, path
        r = await client.request(method, path, json=body, headers=headers)
        assert r.status_code == 503 and code_of(r) == "office_off", (path, r.text)
    bad = {**OfficeConfig().model_dump(), "max_editors": 0}
    r = await client.put("/api/v1/admin/office", json=bad, headers=headers)
    assert r.status_code == 422


# --- /internal/ en /office/ ---


@pytest.mark.anyio
async def test_internal_alleen_op_de_interne_app(client, settings):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=internal_app), base_url="http://platform:8081"
    ) as internal:
        for method, path in (
            ("GET", "/internal/office/doc/k1-abc"),
            ("POST", "/internal/office/callback"),
            ("GET", "/internal/office/src/abc"),
        ):
            r = await internal.request(method, path)
            assert r.status_code == 503 and code_of(r) == "office_off", path
            r = await client.request(method, path)
            assert r.status_code == 404 and code_of(r) == "not_found", path
        for path in ("/api/v1/health", "/", "/api/v1/office/status", "/office/x"):
            r = await internal.get(path)
            assert r.status_code == 404 and code_of(r) == "not_found", path


@pytest.mark.anyio
async def test_office_zonder_proxy_is_404(client, with_office):
    """Zonder office, en met office maar zonder office_dev_ungated: /office/ is 404."""
    for _ in range(2):
        for method in ("GET", "POST", "PUT", "DELETE", "OPTIONS"):
            for path in ("/office", "/office/", "/office/web-apps/apps/api/documents/api.js"):
                r = await client.request(method, path)
                assert r.status_code == 404, (method, path)
        with_office()


# --- de proxy (kern) ---


@pytest.fixture
def ungated(with_office):
    settings = with_office(office_dev_ungated=True)
    seen: list[httpx.Request] = []

    def use(handler) -> list[httpx.Request]:
        def record(request: httpx.Request):
            seen.append(request)
            return handler(request)

        ds_client.transport = httpx.MockTransport(record)
        return seen

    use.settings = settings
    return use


@pytest.mark.anyio
async def test_proxy_koppen_heen_en_terug(client, ungated):
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Set-Cookie": "ds=1",
            "Server": "nginx",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src *",
            "Strict-Transport-Security": "max-age=1",
            "Clear-Site-Data": '"*"',
            "Service-Worker-Allowed": "/",
            "ETag": '"abc"',
        }
        return respond(200, headers, b"<html></html>")

    seen = ungated(handler)
    r = await client.get(
        "/office/10.0.0-0123abcd/web-apps/apps/documenteditor/main/index.html?lang=nl",
        headers={
            "Cookie": "platform_refresh=geheim; office=ook",
            "Authorization": "Bearer geheim",
            "X-Forwarded-For": "6.6.6.6",
            "Forwarded": "for=6.6.6.6",
            "Accept-Language": "nl",
            "If-None-Match": '"abc"',
        },
    )
    assert r.status_code == 200 and r.text == "<html></html>"
    sent = seen[-1]
    assert (
        sent.url.raw_path
        == b"/10.0.0-0123abcd/web-apps/apps/documenteditor/main/index.html?lang=nl"
    )
    for name in ("cookie", "authorization", "forwarded"):
        assert name not in sent.headers
    assert sent.headers["x-forwarded-prefix"] == "/office"
    assert sent.headers["x-forwarded-host"] == "test"
    assert sent.headers["x-forwarded-proto"] == "http"
    assert sent.headers["x-forwarded-for"] != "6.6.6.6"
    assert sent.headers["accept-language"] == "nl" and sent.headers["if-none-match"] == '"abc"'
    # Het antwoord: zonder de koppen van DS die hier niet horen
    assert "set-cookie" not in r.headers and "clear-site-data" not in r.headers
    assert "service-worker-allowed" not in r.headers
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["content-security-policy"] == proxy.EDITOR_CSP
    assert r.headers["etag"] == '"abc"'


@pytest.mark.anyio
async def test_proxy_corp_svg_en_location(client, ungated):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(".svg"):
            return respond(200, {"Content-Type": "image/svg+xml"}, b"<svg/>")
        if path == "/elders":
            return respond(302, {"Location": "https://evil.example/x"})
        if path == "/hier":
            return respond(302, {"Location": "http://test/office/y"})
        if path == "/relatief":
            return respond(302, {"Location": "/office/z"})
        if path == "/schema-loos":
            return respond(302, {"Location": "//evil.example/x"})
        return respond(200, {"Content-Type": "application/javascript"}, b"1")

    ungated(handler)
    r = await client.get("/office/a.js")
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert "content-security-policy" not in r.headers
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    r = await client.get("/office/plaatje.svg")
    assert r.headers["content-security-policy"] == "sandbox"
    assert "location" not in (await client.get("/office/elders")).headers
    assert "location" not in (await client.get("/office/schema-loos")).headers
    assert (await client.get("/office/hier")).headers["location"] == "http://test/office/y"
    assert (await client.get("/office/relatief")).headers["location"] == "/office/z"


@pytest.mark.parametrize(
    ("location", "kept"),
    [
        ("/office/z", True),
        ("z?a=b", True),
        ("http://test/office/y", True),
        ("HTTPS://TEST/office/y", True),
        ("//evil.example/x", False),
        # Een browser leest bij http(s) een backslash als '/': dit zijn andere hosts
        ("/\\evil.example/x", False),
        ("\\\\evil.example/x", False),
        ("\\/evil.example/x", False),
        ("http:\\\\evil.example/x", False),
        # Tabs, regeleinden en spaties aan de randen laat een browser weg
        ("/\t/evil.example/x", False),
        ("/\n/evil.example/x", False),
        (" //evil.example/x", False),
        ("/office/a b", False),
        ("/office/\x7f", False),
        ("https:evil.example", False),
        ("javascript:alert(1)", False),
        ("http://test@evil.example/", False),
        ("http://evil.example\\@test/", False),
    ],
)
def test_location_alleen_relatief_of_eigen_host(location, kept):
    """S7: Location blijft alleen staan als een browser hem bij ons laat uitkomen."""
    assert proxy._own_location(location, "test") is kept


@pytest.mark.anyio
async def test_proxy_stuurt_ds_precies_het_pad_dat_hij_ziet(client, ungated):
    """S6: DS krijgt het pad van de browser zonder /office, byte voor byte. Een pad waarvoor dat
    niet kan, komt nooit bij DS: 404."""
    seen = ungated(lambda request: respond(200, {"Content-Type": "text/plain"}, b"ok"))
    for path, sent in (
        ("/office", b"/"),
        ("/office/", b"/"),
        ("/office/sdkjs/a.js", b"/sdkjs/a.js"),
        ("/office/a/b%2Fc/%2e%2e/d", b"/a/b%2Fc/%2e%2e/d"),
        (
            "/office/x/downloadas/k1?cmd=%7B%22c%22%3A1%7D&shardkey=k1",
            b"/x/downloadas/k1?cmd=%7B%22c%22%3A1%7D&shardkey=k1",
        ),
    ):
        r = await client.get(path)
        assert r.status_code == 200 and seen[-1].url.raw_path == sent, path
    count = len(seen)
    for path in (
        "/office//sdkjs/healthcheck",  # httpx las //sdkjs als host: DS kreeg /healthcheck
        "/office//evil.example/x",
        "/%6fffice/web-apps/apps/api/documents/api.js",  # ging als ce/web-apps/… naar DS
        "/%6Fffice",
    ):
        r = await client.get(path)
        assert r.status_code == 404 and code_of(r) == "not_found", path
    assert len(seen) == count
    # Wat httpx zelf zou wegwerken (een test-client normaliseert dat al): rechtstreeks via ASGI
    for raw in (
        b"/office/web-apps/../command",
        b"/office/./healthcheck",
        b"/office/a/..",
        b"/office/a#b",
        b"/office/a{b}",
    ):
        status = await asgi_get(raw)
        assert status == 404, raw
    assert len(seen) == count


async def asgi_get(raw_path: bytes) -> int:
    """Een GET met precies deze rauwe bytes, zoals uvicorn hem doorgeeft. De status."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(raw_path.decode("latin-1")),
        "raw_path": raw_path,
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


@pytest.mark.anyio
async def test_proxy_bodies_en_head(client, ungated):
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"len": len(request.content), "method": request.method}
        return respond(200, {"Content-Type": "application/json"}, json.dumps(body).encode())

    seen = ungated(handler)
    r = await client.post("/office/upload", content=b"x" * 300_000)
    assert r.json() == {"len": 300_000, "method": "POST"}
    r = await client.head("/office/a")
    assert r.status_code == 200 and r.content == b"" and seen[-1].method == "HEAD"


@pytest.mark.anyio
async def test_proxy_te_groot_geeft_413(client, ungated, monkeypatch):
    monkeypatch.setattr(proxy, "MAX_BODY", 1000)
    seen = ungated(lambda request: respond())
    r = await client.post("/office/upload", content=b"x" * 1001)
    assert r.status_code == 413 and code_of(r) == "office_too_large"
    assert seen == []

    async def chunks():
        for _ in range(5):
            yield b"x" * 400

    r = await client.post("/office/upload", content=chunks())  # zonder Content-Length
    assert r.status_code == 413 and code_of(r) == "office_too_large"


@pytest.mark.anyio
async def test_proxy_ds_weg_geeft_502(client, ungated):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg")

    ungated(handler)
    r = await client.get("/office/a.js")
    assert r.status_code == 502 and code_of(r) == "office_unavailable"


@pytest.mark.anyio
async def test_proxy_naar_de_nep_ds(client, ungated):
    """Door de proxy naar de nep-DS: api.js met het prefix en de versie erin."""
    fake = create_app(FakeConfig(secret=ungated.settings.office_secret))
    ds_client.transport = httpx.ASGITransport(app=fake)
    r = await client.get("/office/web-apps/apps/api/documents/api.js")
    assert r.status_code == 200
    assert '"/office"' in r.text and protocol.V in r.text
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert (await client.get("/office/healthcheck")).text == "true"
