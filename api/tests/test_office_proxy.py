"""De proxy van O1 (app/office/proxy.py, Keuze 5, 7 en 9, §3.10) met de echte poort en het echte
padbeleid, voor de app met echte sessies (tests/office_world.py). DS is een nep op een
httpx-transport (die ziet precies wat de proxy stuurt), of de nep-DS van O2 als ASGI-app.

- de klassen en de toegang: statisch zonder cookie; de editorpagina alleen met het cookie van
  een lid en niet als pagina (Sec-Fetch-Dest: document 404); themes.json, data en
  editoracties alleen met het cookie van iemand die nu mag (anders 401 office_gate); de rest 404
  en een logregel;
- de koppen heen en terug als vaste verwachting (nooit Cookie naar DS, nooit Set-Cookie,
  Clear-Site-Data of Service-Worker-Allowed terug), CORP, sandbox, de CSP van de editor,
  Location;
- de editoracties: dezelfde key in de query, Authorization alleen als geldig sessietoken op
  upload (A2), 60 per minuut en 200 MB per 10 minuten per gebruiker (429);
- grootte en stroom: boven 100 MB 413, 50 MB in stukken met begrensd geheugen, gzip blijft gzip;
  DS weg 502, de database weg 503."""

import gzip
import json
import logging
import time
import tracemalloc
from urllib.parse import quote, unquote

import httpx
import pytest

from app.main import app
from app.office import gate, proxy
from app.office.oo import paths
from app.security import RateLimiter
from tests.factories import code_of
from tests.fake_docserver import protocol
from tests.office_world import office_world
from tests.test_office_gate import begin, crafted, shared
from tests.test_office_stream import session_token

pytestmark = pytest.mark.anyio

V = protocol.V
PAGE = f"/office/{V}/web-apps/apps/documenteditor/main/index.html?_dc=1&lang=nl"


def respond(status: int = 200, headers: dict | None = None, body: bytes = b"") -> httpx.Response:
    """Een antwoord van DS als stroom (zoals van het netwerk)."""
    return httpx.Response(status, headers=headers or {}, stream=httpx.ByteStream(body))


class DS:
    """De nep-DS op het transport van de app: bewaart elk verzoek; reply(request) antwoordt."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.reply = lambda request: respond(200, {"Content-Type": "text/plain"}, b"ok")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return self.reply(request)

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.seen]


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        gate.forget()
        proxy.action_limiter.reset()
        proxy.action_bytes.reset()
        proxy._refused_log.clear()
        try:
            yield w
        finally:
            gate.forget()
            proxy.action_limiter.reset()
            proxy.action_bytes.reset()
            proxy._refused_log.clear()


@pytest.fixture
async def ds(world) -> DS:
    found = DS()
    await world.use_transport(httpx.MockTransport(found))
    return found


def office(cookie: str | None = None, **headers: str) -> dict[str, str]:
    out = {k.replace("_", "-"): v for k, v in headers.items()}
    if cookie:
        out["Cookie"] = f"office={cookie}"
    return out


def data_path(key: str, name: str = "Editor.bin") -> str:
    return f"/office/cache/files/data/{key}/{name}/{name}?md5=abc&expires=9999999999"


async def asgi(
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body: list[bytes] | None = None,
    sink=None,
) -> list[dict]:
    """Eén verzoek rechtstreeks via ASGI, zoals uvicorn het doorgeeft; de berichten terug (of
    naar sink, voor een groot antwoord)."""
    raw, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": unquote(raw),
        "raw_path": raw.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", b"test"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    chunks = list(body or [b""])
    messages: list[dict] = []

    async def receive():
        if chunks:
            chunk = chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}
        return {"type": "http.disconnect"}

    async def send(message):
        if sink is not None:
            sink(message)
        else:
            messages.append(message)

    await app(scope, receive, send)
    return messages


# --- de klassen en de toegang ---


async def test_klassen_en_toegang(world, ds):
    s = await shared(world)
    other = await world.docx(s.anna, None, "ander.docx", ["x"])
    key_other, _ = await begin(world, s.anna, other)
    cees = await world.user("cees@example.nl")
    outsider = crafted(world, cees.id)  # een geldig cookie, maar in geen enkele sessie
    upload = f"/office/{V}/upload/{s.key}?shardkey={s.key}"
    download = f"/office/{V}/downloadas/{s.key}?shardkey={s.key}"
    polling = f"/office/{V}/doc/{s.key}/c/?EIO=4&transport=polling"
    cases = [
        # STATIC: voor iedereen, alleen GET en HEAD
        ("GET", "/office/web-apps/apps/api/documents/api.js", None, {}, 200, None),
        ("GET", f"/office/{V}/sdkjs/word/sdk-all-min.js", None, {}, 200, None),
        ("HEAD", f"/office/{V}/fonts/000", None, {}, 200, None),
        ("POST", f"/office/{V}/sdkjs/word/sdk-all-min.js", s.a, {}, 404, "not_found"),
        # PAGE: het cookie van een lid, en niet als pagina
        ("GET", PAGE, None, {}, 404, "not_found"),
        ("GET", PAGE, None, {"Sec-Fetch-Dest": "iframe"}, 404, "not_found"),
        ("GET", PAGE, outsider, {"Sec-Fetch-Dest": "iframe"}, 404, "not_found"),
        ("GET", PAGE, s.a, {"Sec-Fetch-Dest": "document"}, 404, "not_found"),
        ("GET", PAGE, s.a, {"Sec-Fetch-Dest": " Document "}, 404, "not_found"),
        ("GET", PAGE, s.a, {"Sec-Fetch-Dest": "image"}, 404, "not_found"),
        ("GET", PAGE, s.a, {"Sec-Fetch-Dest": "iframe"}, 200, None),
        ("GET", PAGE, s.b, {"Sec-Fetch-Dest": "empty"}, 200, None),
        ("GET", PAGE, s.a, {}, 200, None),  # een browser zonder Sec-Fetch-Dest
        ("HEAD", PAGE, s.a, {"Sec-Fetch-Dest": "frame"}, 200, None),
        ("POST", PAGE, s.a, {}, 404, "not_found"),
        # SETTINGS: het cookie van een lid
        ("GET", f"/office/{V}/themes.json", None, {}, 401, "office_gate"),
        ("GET", f"/office/{V}/themes.json", outsider, {}, 401, "office_gate"),
        ("GET", f"/office/{V}/themes.json", s.a, {}, 200, None),
        # DATA: lid van de sessie van die key, met schrijfrecht van nu
        ("GET", data_path(s.key), None, {}, 401, "office_gate"),
        ("GET", data_path(s.key), outsider, {}, 401, "office_gate"),
        ("GET", data_path(key_other), s.b, {}, 401, "office_gate"),
        ("GET", data_path(s.key), s.b, {}, 200, None),
        ("GET", data_path(f"{s.key}_12", "output.docx"), s.a, {}, 200, None),
        ("HEAD", data_path(s.key), s.a, {}, 404, None),
        ("POST", data_path(s.key), s.a, {}, 404, "not_found"),
        # ACTION: POST, lid van de sessie van die key
        ("POST", upload, None, {}, 401, "office_gate"),
        ("POST", upload, outsider, {}, 401, "office_gate"),
        ("POST", upload, s.a, {}, 200, None),
        ("POST", download, s.b, {}, 200, None),
        ("GET", upload, s.a, {}, 404, "not_found"),
        # Al het andere: 404, ook met een goed cookie
        ("GET", polling, s.a, {}, 404, "not_found"),
        ("POST", polling, s.a, {}, 404, "not_found"),
        ("GET", "/office", s.a, {}, 404, "not_found"),
        ("GET", "/office/", s.a, {}, 404, "not_found"),
        ("GET", "/office/healthcheck", s.a, {}, 404, "not_found"),
        ("POST", f"/office/{V}/command", s.a, {}, 404, "not_found"),
        ("POST", "/office/coauthoring/CommandService.ashx", s.a, {}, 404, "not_found"),
        ("POST", f"/office/{V}/converter", s.a, {}, 404, "not_found"),
        ("GET", f"/office/{V}/info/info.json", s.a, {}, 404, "not_found"),
        ("GET", f"/office/{V}/index.html", s.a, {}, 404, "not_found"),
        ("GET", f"/office/{V}/web-apps/apps/api/documents/preload.html", s.a, {}, 404, None),
        ("GET", f"/office/{V}/web-apps/apps/api/documents/cache-scripts.html", s.a, {}, 404, None),
        ("GET", f"/office/{V}/sdkjs/word/a.xml", s.a, {}, 404, "not_found"),
        ("GET", f"/office/cache/files/conv_{s.key}/output.pdf/a.pdf", s.a, {}, 404, None),
        ("GET", f"/office/cache/files/forgotten/{s.key}/output.docx/a.docx", s.a, {}, 404, None),
        ("GET", f"/office/{V}/doc/{s.key}/c/", s.a, {}, 404, "not_found"),
        ("GET", f"/office/{V}/sdkjs/%2e%2e/command", s.a, {}, 404, "not_found"),
        ("GET", f"/office/{V}/sdkjs/word%2fa.js", s.a, {}, 404, "not_found"),
    ]
    for method, path, cookie, extra, status, code in cases:
        before = len(ds.seen)
        r = await world.client.request(method, path, headers=office(cookie, **extra))
        assert r.status_code == status, (method, path, cookie is not None, extra, r.text)
        if code is not None:
            assert code_of(r) == code, (method, path)
        reached = len(ds.seen) > before
        assert reached == (status == 200), (method, path)
        if reached:
            sent = ds.seen[-1]
            assert sent.method == method
            assert sent.url.raw_path == path[len("/office") :].encode(), path


async def test_de_editorpagina_met_elke_editor(world, ds):
    s = await shared(world)
    for editor in ("documenteditor", "spreadsheeteditor", "presentationeditor"):
        for kind in ("main", "mobile"):
            path = f"/office/{V}/web-apps/apps/{editor}/{kind}/index.html"
            r = await world.client.get(path, headers=office(s.a, Sec_Fetch_Dest="iframe"))
            assert r.status_code == 200, path
            r = await world.client.get(path)
            assert r.status_code == 404, path
            r = await world.client.get(path, headers=office(s.a, Sec_Fetch_Dest="document"))
            assert r.status_code == 404, path


# --- de koppen ---


async def test_koppen_heen_als_vaste_verwachting(world, ds):
    s = await shared(world)
    browser = {
        "Accept": "application/octet-stream",
        "Accept-Encoding": "gzip, br",
        "Accept-Language": "nl,en;q=0.5",
        "User-Agent": "Testbrowser/1.0",
        "Range": "bytes=0-99",
        "If-None-Match": '"abc"',
        "If-Modified-Since": "Wed, 21 Oct 2015 07:28:00 GMT",
        "Origin": "http://test",
        "Cookie": f"platform_refresh=geheim; office={s.a}",
        "Authorization": "Bearer geheim",
        "Referer": "http://test/bestanden",
        "Forwarded": "for=6.6.6.6",
        "X-Forwarded-For": "6.6.6.6",
        "X-Forwarded-Host": "evil.example",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Prefix": "/elders",
        "X-Real-IP": "6.6.6.6",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Site": "same-origin",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "text/plain",
    }
    r = await world.client.get(data_path(s.key), headers=browser)
    assert r.status_code == 200
    sent = ds.seen[-1]
    assert dict(sent.headers) == {
        "host": "office",
        "connection": "keep-alive",
        "accept": "application/octet-stream",
        "accept-encoding": "gzip, br",
        "accept-language": "nl,en;q=0.5",
        "user-agent": "Testbrowser/1.0",
        "range": "bytes=0-99",
        "if-none-match": '"abc"',
        "if-modified-since": "Wed, 21 Oct 2015 07:28:00 GMT",
        "origin": "http://test",
        "content-type": "text/plain",
        "x-forwarded-prefix": "/office",
        "x-forwarded-host": "test",
        "x-forwarded-proto": "http",
        "x-forwarded-for": "127.0.0.1",
    }
    # Op elk soort pad: nooit Cookie (ook niet het poort-cookie) naar DS
    for path, extra in (
        (PAGE, {"Sec_Fetch_Dest": "iframe"}),
        (f"/office/{V}/themes.json", {}),
        ("/office/web-apps/apps/api/documents/api.js", {}),
    ):
        r = await world.client.get(path, headers=office(s.a, **extra))
        assert r.status_code == 200
        lowered = {k.lower() for k in ds.seen[-1].headers}
        assert "cookie" not in lowered and "authorization" not in lowered, path
        assert s.a not in str(ds.seen[-1].headers)


DS_HEADERS = {
    "Content-Type": "application/octet-stream",
    "Content-Length": "5",
    "ETag": '"abc"',
    "Cache-Control": "public, max-age=31536000, immutable",
    "Expires": "Thu, 31 Dec 2037 23:55:55 GMT",
    "Pragma": "public",
    "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT",
    "Content-Disposition": 'attachment; filename="Editor.bin"',
    "Accept-Ranges": "bytes",
    "Vary": "Accept-Encoding",
    "Set-Cookie": "ds=1; Path=/",
    "Clear-Site-Data": '"*"',
    "Service-Worker-Allowed": "/",
    "Server": "nginx/1.2",
    "Strict-Transport-Security": "max-age=1",
    "X-Frame-Options": "ALLOWALL",
    "Content-Security-Policy": "default-src *",
    "Content-Security-Policy-Report-Only": "default-src *",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Connection": "close",
    "Keep-Alive": "timeout=5",
    "Date": "Wed, 21 Oct 2015 07:28:00 GMT",
}


async def test_koppen_terug_als_vaste_verwachting(world, ds):
    s = await shared(world)
    ds.reply = lambda request: respond(200, DS_HEADERS, b"12345")
    r = await world.client.get(data_path(s.key), headers=office(s.a))
    assert r.status_code == 200 and r.content == b"12345"
    assert dict(r.headers) == {
        "content-type": "application/octet-stream",
        "content-length": "5",
        "etag": '"abc"',
        "last-modified": "Wed, 21 Oct 2015 07:28:00 GMT",
        "content-disposition": 'attachment; filename="Editor.bin"',
        "accept-ranges": "bytes",
        "vary": "Accept-Encoding",
        # Documentdata blijft nooit in de browser (proxy.CACHE), wat DS ook zegt
        "cache-control": "private, no-store",
        "x-frame-options": "SAMEORIGIN",
        "cross-origin-resource-policy": "same-origin",
        # Van Edge, voor elk antwoord van de app
        "x-content-type-options": "nosniff",
        "referrer-policy": "same-origin",
        "permissions-policy": "camera=(), microphone=(), geolocation=()",
    }
    # De editorpagina: de CSP van Keuze 9 en geen CORP; HTML zonder sandbox
    ds.reply = lambda request: respond(
        200, {**DS_HEADERS, "Content-Type": "text/html; charset=utf-8"}, b"12345"
    )
    r = await world.client.get(PAGE, headers=office(s.a, Sec_Fetch_Dest="iframe"))
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == proxy.EDITOR_CSP
    assert r.headers.get_list("content-security-policy") == [proxy.EDITOR_CSP]
    assert "cross-origin-resource-policy" not in r.headers
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
    for name in ("set-cookie", "clear-site-data", "service-worker-allowed", "server"):
        assert name not in r.headers
    assert "frame-ancestors 'self'" in proxy.EDITOR_CSP and "object-src 'none'" in proxy.EDITOR_CSP
    # Elke keer langs de poort: DS zegt een jaar immutable, de browser vraagt toch opnieuw
    assert r.headers.get_list("cache-control") == ["private, no-cache"]
    assert "expires" not in r.headers and "pragma" not in r.headers
    # Statisch: de Cache-Control van DS blijft
    r = await world.client.get(f"/office/{V}/sdkjs/word/sdk-all-min.js")
    assert r.status_code == 200
    assert r.headers.get_list("cache-control") == ["public, max-age=31536000, immutable"]
    assert r.headers["expires"] == "Thu, 31 Dec 2037 23:55:55 GMT"


async def test_de_editorpagina_opnieuw_vragen_gaat_langs_de_poort(world, ds):
    """Met If-None-Match (de browser had hem nog): de poort beslist, en DS mag 304 geven."""
    s = await shared(world)
    ds.reply = lambda request: respond(304, {"ETag": '"abc"', "Cache-Control": "max-age=9"})
    r = await world.client.get(
        PAGE, headers=office(s.a, Sec_Fetch_Dest="iframe", If_None_Match='"abc"')
    )
    assert r.status_code == 304
    assert r.headers.get_list("cache-control") == ["private, no-cache"]
    assert ds.seen[-1].headers["if-none-match"] == '"abc"'
    before = len(ds.seen)
    r = await world.client.get(
        PAGE, headers=office(s.a, Sec_Fetch_Dest="document", If_None_Match='"abc"')
    )
    assert r.status_code == 404 and len(ds.seen) == before


@pytest.mark.parametrize(
    ("kind", "sandbox"),
    [
        ("image/svg+xml", True),
        ("text/html", True),
        ("application/xhtml+xml", True),
        ("text/xml; charset=utf-8", True),
        ("application/xml", True),
        ("IMAGE/SVG+XML", True),
        ("application/javascript", False),
        ("text/css", False),
        ("image/png", False),
        ("application/json", False),
    ],
)
async def test_statisch_corp_en_sandbox_op_markup(world, ds, kind, sandbox):
    ds.reply = lambda request: respond(200, {"Content-Type": kind}, b"<svg/>")
    r = await world.client.get(f"/office/{V}/web-apps/apps/common/main/resources/img/a.svg")
    assert r.status_code == 200
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers.get("content-security-policy") == ("sandbox" if sandbox else None)


async def test_de_service_worker_komt_van_de_app(world, ds):
    """Nooit die van DS (docs/fase-4/o1.md): DS ziet het verzoek niet, ook niet als HEAD."""
    path = f"/office/{V}/document_editor_service_worker.js"
    r = await world.client.get(path)
    assert r.status_code == 200
    assert r.content == paths.OWN_SERVICE_WORKER
    assert r.headers["content-type"] == "text/javascript; charset=utf-8"
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "service-worker-allowed" not in r.headers
    head = await world.client.head(path)
    assert head.status_code == 200 and head.content == b""
    assert (await world.client.post(path)).status_code == 404
    assert ds.seen == []


async def test_location_alleen_naar_onze_host(world, ds):
    s = await shared(world)
    for location, kept in (
        ("https://evil.example/x", False),
        ("//evil.example/x", False),
        ("/\\evil.example/x", False),
        ("http://test/office/y", True),
        ("/office/z", True),
    ):
        ds.reply = lambda request, location=location: respond(302, {"Location": location})
        r = await world.client.get(data_path(s.key), headers=office(s.a))
        assert r.status_code == 302
        assert r.headers.get("location") == (location if kept else None), location


# --- de editoracties ---


async def test_upload_neemt_alleen_een_geldig_sessietoken_mee(world, ds):
    """A2: op upload/<key> gaat Authorization alleen mee als het een geldig sessietoken van die
    key is; anders gaat het verzoek zonder Authorization naar DS (die weigert dan zelf)."""
    s = await shared(world)
    upload = f"/office/{V}/upload/{s.key}?shardkey={s.key}"
    good = session_token(world.settings, s.key)
    old = session_token(world.settings, s.key, exp=int(time.time()) - 3600)
    other_key = session_token(world.settings, "k1-abc")
    office_signed = session_token(world.settings, s.key, secret=world.settings.office_secret)
    for authorization, forwarded in (
        (f"Bearer {good}", True),
        (f"bearer  {good} ", True),
        (f"Bearer {old}", False),
        (f"Bearer {other_key}", False),
        (f"Bearer {office_signed}", False),  # het config-geheim, geen sessietoken
        (f"Basic {good}", False),
        ("Bearer", False),
        (None, False),
    ):
        headers = office(s.a)
        if authorization is not None:
            headers["Authorization"] = authorization
        r = await world.client.post(upload, content=b"png", headers=headers)
        assert r.status_code == 200, authorization
        sent = ds.seen[-1].headers.get("authorization")
        assert sent == (f"Bearer {good}" if forwarded else None), authorization
    # downloadas neemt nooit Authorization mee
    r = await world.client.post(
        f"/office/{V}/downloadas/{s.key}?shardkey={s.key}",
        headers={**office(s.a), "Authorization": f"Bearer {good}"},
    )
    assert r.status_code == 200 and "authorization" not in ds.seen[-1].headers


async def test_editoractie_over_een_andere_key_is_401(world, ds):
    s = await shared(world)
    other = "aaaabbbbccccddddeeeeffff00001111-fedcba9876543210"
    base = f"/office/{V}/downloadas/{s.key}"

    def cmd(key: str | None, token_key: str | None = None) -> str:
        value = {"c": "save"}
        if key is not None:
            value["id"] = key
        if token_key is not None:
            value["tokenSession"] = session_token(world.settings, token_key)
        return quote(json.dumps(value), safe="")

    for query in (
        f"shardkey={other}",
        f"shardkey={s.key}&shardkey={other}",
        f"cmd={cmd(other)}",
        f"cmd={cmd(None)}",
        f"cmd={cmd(s.key, other)}",
        f"cmd={cmd(s.key)}&cmd={cmd(s.key)}",
        "cmd=kapot",
    ):
        before = len(ds.seen)
        r = await world.client.post(f"{base}?{query}", headers=office(s.a))
        assert (r.status_code, code_of(r)) == (401, "office_gate"), query
        assert len(ds.seen) == before
    good = f"cmd={cmd(s.key, s.key)}"
    r = await world.client.post(f"{base}?{good}&shardkey={s.key}", headers=office(s.a))
    assert r.status_code == 200
    r = await world.client.post(f"/office/{V}/upload/{s.key}?shardkey={other}", headers=office(s.a))
    assert r.status_code == 401


async def test_editoracties_hooguit_60_per_minuut_per_gebruiker(world, ds):
    s = await shared(world)
    assert RateLimiter.enabled
    upload = f"/office/{V}/upload/{s.key}?shardkey={s.key}"
    for i in range(proxy.ACTIONS_PER_MINUTE):
        r = await world.client.post(upload, content=b"x", headers=office(s.a))
        assert r.status_code == 200, i
    before = len(ds.seen)
    r = await world.client.post(upload, content=b"x", headers=office(s.a))
    assert (r.status_code, code_of(r)) == (429, "rate_limited")
    assert int(r.headers["retry-after"]) >= 1
    assert len(ds.seen) == before
    # Een ander heeft zijn eigen grens; statische paden en data hebben er geen
    assert (await world.client.post(upload, content=b"x", headers=office(s.b))).status_code == 200
    assert (await world.client.get(data_path(s.key), headers=office(s.a))).status_code == 200


async def test_editoracties_hooguit_200_mb_per_10_minuten(world, ds, monkeypatch):
    assert proxy.ACTION_BYTES == 200 * 1024**2 and proxy.ACTION_BYTES_SECONDS == 600
    budget = proxy.ByteBudget(1000, 600)
    monkeypatch.setattr(proxy, "action_bytes", budget)
    s = await shared(world)
    upload = f"/office/{V}/upload/{s.key}?shardkey={s.key}"
    ds.reply = lambda request: respond(200, {"Content-Type": "text/plain"}, request.read()[:3])
    r = await world.client.post(upload, content=b"a" * 600, headers=office(s.a))
    assert r.status_code == 200
    # Met Content-Length boven wat er nog over is: meteen 429, DS krijgt niets
    before = len(ds.seen)
    r = await world.client.post(upload, content=b"b" * 600, headers=office(s.a))
    assert (r.status_code, code_of(r)) == (429, "rate_limited")
    assert int(r.headers["retry-after"]) >= 1 and len(ds.seen) == before

    async def chunks():
        for _ in range(6):
            yield b"c" * 100

    # Zonder Content-Length: tijdens het lezen
    r = await world.client.post(upload, content=chunks(), headers=office(s.a))
    assert (r.status_code, code_of(r)) == (429, "rate_limited")
    # Een ander heeft zijn eigen budget
    assert (
        await world.client.post(upload, content=b"d" * 600, headers=office(s.b))
    ).status_code == 200
    # Uit met de rate limits (de e2e-server)
    monkeypatch.setattr(RateLimiter, "enabled", False)
    assert budget.left(str(s.anna.id)) == 1000


def test_byte_budget(monkeypatch):
    budget = proxy.ByteBudget(100, 10)
    now = [1000.0]
    monkeypatch.setattr(proxy.time, "monotonic", lambda: now[0])
    budget.add("a", 60)
    assert budget.left("a") == 40 and budget.left("b") == 100
    assert budget.retry_after("a") == 10 and budget.retry_after("b") == 1
    now[0] += 5
    budget.add("a", 40)
    assert budget.left("a") == 0 and budget.retry_after("a") == 5
    now[0] += 5.5
    assert budget.left("a") == 60
    budget.reset()
    assert budget.left("a") == 100


# --- grootte, stroom, gzip, storingen ---


async def test_boven_100_mb_413(world, ds, monkeypatch):
    s = await shared(world)
    assert proxy.MAX_BODY == 100 * 1024**2
    upload = f"/office/{V}/upload/{s.key}?shardkey={s.key}"
    cookie = (b"cookie", f"office={s.a}".encode())
    # Een Content-Length boven 100 MB: 413 zonder de body te lezen
    length = (b"content-length", str(proxy.MAX_BODY + 1).encode())
    messages = await asgi("POST", upload, [cookie, length], [b"x"])
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 413
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    assert json.loads(body)["detail"]["code"] == "office_too_large"
    assert ds.seen == []
    # Zonder Content-Length: tijdens het lezen (hier met een kleinere grens)
    monkeypatch.setattr(proxy, "MAX_BODY", 1000)

    async def chunks():
        for _ in range(5):
            yield b"x" * 400

    r = await world.client.post(upload, content=chunks(), headers=office(s.a))
    assert (r.status_code, code_of(r)) == (413, "office_too_large")


async def test_50_mb_in_stukken_met_begrensd_geheugen(world, ds):
    """Een antwoord van 50 MB gaat in stukken door: de proxy stuurt elk stuk door voor hij het
    volgende van DS leest, en houdt nooit veel tegelijk vast."""
    s = await shared(world)
    total, size = 50 * 1024**2, 64 * 1024
    piece = b"z" * size
    state = {"made": 0, "sent": 0, "lag": 0, "chunks": 0, "status": None}

    class Big(httpx.AsyncByteStream):
        async def __aiter__(self):
            while state["made"] < total:
                state["made"] += size
                yield piece

    ds.reply = lambda request: httpx.Response(
        200, headers={"Content-Type": "application/octet-stream"}, stream=Big()
    )

    def sink(message: dict) -> None:
        if message["type"] == "http.response.start":
            state["status"] = message["status"]
        elif message["type"] == "http.response.body":
            state["sent"] += len(message.get("body", b""))
            state["chunks"] += 1
            state["lag"] = max(state["lag"], state["made"] - state["sent"])

    cookie = (b"cookie", f"office={s.a}".encode())
    tracemalloc.start()
    try:
        await asgi("GET", data_path(f"{s.key}_3", "output.docx"), [cookie], sink=sink)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert state["status"] == 200 and state["sent"] == total
    assert state["chunks"] >= total // size
    assert state["lag"] <= size  # nooit meer dan één stuk vooruit
    assert peak < 8 * 1024**2, peak


async def test_gzip_blijft_gzip(world, ds):
    raw = gzip.compress(json.dumps({"tekst": "hallo " * 500}).encode())
    ds.reply = lambda request: respond(
        200, {"Content-Type": "application/json", "Content-Encoding": "gzip"}, raw
    )
    path = f"/office/{V}/web-apps/apps/documenteditor/main/locale/nl.json"
    async with world.client.stream("GET", path, headers={"Accept-Encoding": "gzip, br"}) as r:
        assert r.status_code == 200
        assert r.headers["content-encoding"] == "gzip"
        got = b"".join([chunk async for chunk in r.aiter_raw()])
    assert got == raw


async def test_ds_weg_502_en_de_database_weg_503(world, ds, monkeypatch):
    s = await shared(world)

    def down(request):
        raise httpx.ConnectError("weg")

    ds.reply = down
    r = await world.client.get("/office/web-apps/apps/api/documents/api.js")
    assert (r.status_code, code_of(r)) == (502, "office_unavailable")
    r = await world.client.get(data_path(s.key), headers=office(s.a))
    assert (r.status_code, code_of(r)) == (502, "office_unavailable")

    async def broken(*args, **kwargs):
        raise RuntimeError("database weg")

    monkeypatch.setattr(gate, "allowed", broken)
    gate.forget()
    ds.reply = lambda request: respond()
    before = len(ds.seen)
    for path in (data_path(s.key), f"/office/{V}/themes.json"):
        r = await world.client.get(path, headers=office(s.a))
        assert (r.status_code, code_of(r)) == (503, "office_unavailable"), path
    assert len(ds.seen) == before


async def test_logregel_bij_een_geweigerd_pad(world, ds, caplog, monkeypatch):
    s = await shared(world)
    caplog.set_level(logging.WARNING, logger="platform.office")
    r = await world.client.get(f"/office/{V}/info/info.json?geheim=1")
    assert r.status_code == 404
    lines = [rec.getMessage() for rec in caplog.records if "pad geweigerd" in rec.getMessage()]
    assert lines == [f"office: pad geweigerd geen GET /office/{V}/info/info.json"]
    caplog.clear()
    r = await world.client.get(PAGE, headers=office(s.a, Sec_Fetch_Dest="document"))
    assert r.status_code == 404
    assert any("pad geweigerd page GET" in rec.getMessage() for rec in caplog.records)
    # Hooguit zoveel per minuut
    caplog.clear()
    monkeypatch.setattr(proxy, "REFUSED_LOG_PER_MINUTE", 3)
    proxy._refused_log.clear()
    for i in range(6):
        await world.client.get(f"/office/{V}/onbekend{i}")
    assert len([r for r in caplog.records if "pad geweigerd" in r.getMessage()]) == 3


async def test_via_de_nep_ds_van_o2(world):
    """Door de proxy naar de nep-DS van O2 (als ASGI-app): api.js, en een klaargezette opslag als
    documentdata, alleen met het cookie."""
    s = await shared(world)
    r = await world.client.get("/office/web-apps/apps/api/documents/api.js")
    assert r.status_code == 200 and '"/office"' in r.text and V in r.text
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    link = world.stage(s.key, b"inhoud van de opslag")
    path = "/office" + link.split("/office", 1)[1]
    assert path.startswith(f"/office/cache/files/data/{s.key}_")
    r = await world.client.get(path)
    assert (r.status_code, code_of(r)) == (401, "office_gate")
    r = await world.client.get(path, headers=office(s.b))
    assert r.status_code == 200 and r.content == b"inhoud van de opslag"
    await world.revoke(s.anna, s.share)
    gate.forget_user(s.bram.id)  # zoals de ronde na de intrekking
    r = await world.client.get(path, headers=office(s.b))
    assert (r.status_code, code_of(r)) == (401, "office_gate")
    assert (await world.client.get(path, headers=office(s.a))).status_code == 200
