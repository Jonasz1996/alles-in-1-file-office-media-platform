"""De proxy voor /office/ over http (Keuze 5). Kern van O0; O1 maakt hem af.

Een ASGI-middleware binnen Edge en JsonCompression, vóór de catch-all van de web-app. Het
prefix /office gaat eraf; naar DS gaan alleen een vaste lijst verzoekkoppen plus
X-Forwarded-Prefix, -Host, -Proto en -For. Bodies gaan in stukken in beide richtingen (hooguit
MAX_BODY), gzip blijft gzip. Van het antwoord gaan Set-Cookie, Clear-Site-Data, HSTS, Server,
Service-Worker-Allowed en de X-Frame-Options en CSP van DS weg; erbij komen X-Frame-Options:
SAMEORIGIN (anders zet Edge DENY en laadt de iframe van de editor niet), op HTML de CSP van
Keuze 9 en op al het andere Cross-Origin-Resource-Policy: same-origin. Location blijft alleen
als hij relatief is of naar onze eigen host wijst (een '\\' telt als '/'). Het pad gaat byte
voor byte naar DS; kan dat niet ('//', '.', '..', een prefix in procenttekens), dan 404. DS
niet bereikbaar: 502 office_unavailable.

De kern heeft nog geen padbeleid en geen toegangscontrole (O1: klassen, poort-cookie, recht
van nu, Sec-Fetch-Dest, grenzen per gebruiker). Daarom laat hij alleen door met
office_dev_ungated; anders gaat /office/ door naar de app, en die geeft 404. Zo is de basis
nooit open.
"""

import logging
from urllib.parse import quote, urlsplit

import httpx
from fastapi.responses import JSONResponse

from app import errors
from app.office import engine, runtime

log = logging.getLogger("platform.office")

PREFIX = "/office"
MAX_BODY = 100 * 1024**2

EDITOR_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
    "connect-src 'self'; worker-src 'self' blob:; frame-src 'self'; media-src 'self' blob:; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)

REQUEST_HEADERS = frozenset(
    {
        b"accept",
        b"accept-encoding",
        b"accept-language",
        b"content-type",
        b"content-length",
        b"if-none-match",
        b"if-modified-since",
        b"range",
        b"user-agent",
        b"origin",
    }
)
DROP_RESPONSE = frozenset(
    {
        "set-cookie",
        "clear-site-data",
        "strict-transport-security",
        "server",
        "service-worker-allowed",
        "x-frame-options",
        "content-security-policy",
        "content-security-policy-report-only",
        "cross-origin-resource-policy",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "proxy-connection",
        "upgrade",
        "te",
        "trailer",
        "date",
    }
)


class BodyTooLarge(Exception):
    pass


def ours(path: str) -> bool:
    return path == PREFIX or path.startswith(PREFIX + "/")


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


def request_headers(scope) -> list[tuple[str, str]]:
    """Wat naar DS gaat: de vaste lijst, plus de X-Forwarded-koppen."""
    out = [
        (key.decode("latin-1"), value.decode("latin-1"))
        for key, value in scope.get("headers", [])
        if key in REQUEST_HEADERS
    ]
    client = scope.get("client")
    out += [
        ("X-Forwarded-Prefix", PREFIX),
        ("X-Forwarded-Host", _header(scope, b"host")),
        ("X-Forwarded-Proto", scope.get("scheme", "http")),
    ]
    if client:
        out.append(("X-Forwarded-For", client[0]))
    return out


def ds_target(scope) -> str | None:
    """Pad en query aan de kant van DS: de bytes van de browser zonder het prefix. None als
    de rauwe bytes zelf niet met /office beginnen (zoals /%6fffice/…): ours() keek naar het
    gedecodeerde pad, en DS zou dan iets anders krijgen dan wat we zagen."""
    raw = scope.get("raw_path") or quote(scope["path"]).encode()
    prefix = PREFIX.encode()
    if raw == prefix:
        path = b"/"
    elif raw.startswith(prefix + b"/"):
        path = raw[len(prefix) :]
    else:
        return None
    query = scope.get("query_string", b"")
    return path.decode("latin-1") + ("?" + query.decode("latin-1") if query else "")


def _own_location(location: str, host: str) -> bool:
    """Relatief, of naar onze eigen host. Een browser leest bij http(s) een '\\' als '/' en
    laat tabs, regeleinden en spaties aan de randen weg; '/\\evil.example/x' is dus een andere
    host. Daarom eerst '\\' gelijk aan '/', en een Location met witruimte of controletekens
    blijft nooit staan."""
    if any(ch <= " " or ch == "\x7f" for ch in location):
        return False
    location = location.replace("\\", "/")
    parts = urlsplit(location)
    if not parts.scheme and not parts.netloc:
        return not location.startswith("//")
    return parts.scheme in ("http", "https") and parts.netloc.lower() == host.lower()


def response_headers(headers: httpx.Headers, host: str) -> list[tuple[bytes, bytes]]:
    kind = headers.get("content-type", "").split(";")[0].strip().lower()
    out = []
    for key, value in headers.multi_items():
        name = key.lower()
        if name in DROP_RESPONSE:
            continue
        if name == "location" and not _own_location(value, host):
            continue
        out.append((name.encode("latin-1"), value.encode("latin-1")))
    out.append((b"x-frame-options", b"SAMEORIGIN"))
    if kind == "text/html":
        out.append((b"content-security-policy", EDITOR_CSP.encode()))
    else:
        out.append((b"cross-origin-resource-policy", b"same-origin"))
        if kind == "image/svg+xml":
            out.append((b"content-security-policy", b"sandbox"))
    return out


def _unavailable() -> JSONResponse:
    exc = errors.fail(502, "office_unavailable", "De editor is nu niet bereikbaar")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _too_large() -> JSONResponse:
    exc = errors.fail(413, "office_too_large", "Dit is te groot voor de editor")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


class OfficeProxy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not ours(scope["path"]):
            return await self.app(scope, receive, send)
        settings = runtime.settings()
        if not (settings.office_configured and settings.office_dev_ungated):
            return await self.app(scope, receive, send)  # de catch-all geeft 404
        await self.forward(scope, receive, send)

    async def forward(self, scope, receive, send) -> None:
        target = ds_target(scope)
        if target is None:
            return await self.app(scope, receive, send)  # de catch-all geeft 404
        length = _header(scope, b"content-length")
        if length.isdigit() and int(length) > MAX_BODY:
            return await _too_large()(scope, receive, send)
        method = scope["method"]
        body = None
        if method not in ("GET", "HEAD"):
            body = _body(receive)
        server = engine.upstream()
        if server is None:
            return await _unavailable()(scope, receive, send)
        try:
            upstream = await server.open(method, target, request_headers(scope), body)
        except ValueError:
            # Een pad dat DS anders zou krijgen dan wij het zien ('//', '.', '..'): nooit naar DS
            return await self.app(scope, receive, send)
        except BodyTooLarge:
            return await _too_large()(scope, receive, send)
        except httpx.HTTPError as exc:
            log.warning("office: de document server antwoordt niet (%r)", exc)
            return await _unavailable()(scope, receive, send)
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": upstream.status_code,
                    "headers": response_headers(upstream.headers, _header(scope, b"host")),
                }
            )
            if method != "HEAD":
                async for chunk in upstream.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        except httpx.HTTPError:
            log.warning("office: antwoord van de document server brak af")
            # De kop is al weg; de browser ziet een afgebroken antwoord
            await send({"type": "http.response.body", "body": b""})
        finally:
            await upstream.aclose()


async def _body(receive):
    seen = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise httpx.ReadError("de browser ging weg")
        chunk = message.get("body", b"")
        seen += len(chunk)
        if seen > MAX_BODY:
            raise BodyTooLarge
        if chunk:
            yield chunk
        if not message.get("more_body", False):
            return
