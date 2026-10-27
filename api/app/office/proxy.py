"""De proxy voor /office/ over http (Keuze 5 en 7, §3.9 #3–#7, §3.10).

Een ASGI-middleware binnen Edge en JsonCompression, vóór de catch-all van de web-app. Zonder
office (niet ingesteld) gaat /office/ door naar de app, en die geeft 404.

Per verzoek:
1. Het pad: de klasse komt van de editor (Engine.classify, bij OnlyOffice oo/paths.py, het
   padbeleid van §3.10). Geen klasse: 404 not_found, met een logregel
   ("office: pad geweigerd <klasse> <methode> <pad>", zonder query), zodat een nieuw pad van
   een nieuwe DS opvalt.
2. De toegang (Keuze 7, gate.check):
   - STATIC: zonder cookie;
   - PAGE: Sec-Fetch-Dest, als de browser hem stuurt, alleen iframe, frame of empty; en het
     poort-cookie van iemand die nu in een sessie zit. Anders 404 (geen hint);
   - SETTINGS: het cookie, lid van een sessie die niet dicht is. Anders 401 office_gate;
   - DATA en ACTION: het cookie, lid van de sessie van de key in het pad, niet banned, sessie
     niet dicht, en nu schrijfrecht. Anders 401 office_gate;
   - ACTION ook: de query gaat over dezelfde key (Engine.action_authorization; anders 401),
     per gebruiker hooguit 60 per minuut en 200 MB per 10 minuten (429 rate_limited), en
     alleen op upload/<key> gaat een geldig sessietoken als Authorization mee (A2).
3. Doorgeven. Het prefix /office gaat eraf; naar DS gaan alleen een vaste lijst verzoekkoppen
   plus X-Forwarded-Prefix, -Host, -Proto en -For (nooit Cookie, Authorization behalve 2.,
   Referer, Forwarded of X-Forwarded-* van de client). Het pad gaat byte voor byte naar DS; kan
   dat niet ('//', '.', '..', een prefix in procenttekens), dan 404. Bodies gaan in stukken in
   beide richtingen (hooguit MAX_BODY: 413 office_too_large), gzip blijft gzip. DS niet
   bereikbaar: 502 office_unavailable.
4. Het antwoord: Set-Cookie, Clear-Site-Data, HSTS, Server, Service-Worker-Allowed en de
   X-Frame-Options en CSP van DS gaan weg. Erbij: X-Frame-Options: SAMEORIGIN (anders zet Edge
   DENY en laadt de iframe van de editor niet); op de editorpagina de CSP van Keuze 9; op al
   het andere Cross-Origin-Resource-Policy: same-origin, en op HTML, XML en svg daarbuiten
   Content-Security-Policy: sandbox (zoals send_file bij svg). Location blijft alleen als hij
   relatief is of naar onze eigen host wijst (een '\\' telt als '/').
"""

import logging
import math
import time
from collections import defaultdict, deque
from urllib.parse import quote, urlsplit

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from app import errors
from app.office import engine, gate, runtime
from app.office.engine import PathClass, TokenError
from app.security import RateLimiter

log = logging.getLogger("platform.office")

PREFIX = "/office"
MAX_BODY = 100 * 1024**2
# Editoracties met een body, per gebruiker (Keuze 5)
ACTIONS_PER_MINUTE = 60
ACTION_BYTES = 200 * 1024**2
ACTION_BYTES_SECONDS = 600.0
# Sec-Fetch-Dest op de editorpagina (spike stap 17): iframe koud, empty via de service worker
PAGE_DESTS = frozenset({"iframe", "frame", "empty"})
# Logregels over geweigerde paden: hooguit zoveel per minuut
REFUSED_LOG_PER_MINUTE = 60

EDITOR_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
    "connect-src 'self'; worker-src 'self' blob:; frame-src 'self'; media-src 'self' blob:; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)
# Wat een browser als pagina met scripts kan tonen; buiten de editorpagina: sandbox
MARKUP_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/xml",
        "application/xml",
        "text/xsl",
        "application/xslt+xml",
        "image/svg+xml",
        "multipart/x-mixed-replace",
    }
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

GATE = "Je toegang tot de editor is voorbij; open het document opnieuw"
ACTIONS = "Te veel acties in de editor; probeer het zo opnieuw"


class BodyTooLarge(Exception):
    pass


class OverBudget(Exception):
    pass


class ByteBudget:
    """Hooguit limit bytes per sleutel in window seconden (in het geheugen, één proces). Uit
    als de rate limits uit staan (RateLimiter.enabled)."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._used: dict[str, deque[tuple[float, int]]] = defaultdict(deque)

    def used(self, key: str) -> int:
        now = time.monotonic()
        if len(self._used) > 10_000:
            for old in [k for k, v in self._used.items() if not v or now - v[-1][0] > self.window]:
                del self._used[old]
        spent = self._used[key]
        while spent and now - spent[0][0] > self.window:
            spent.popleft()
        return sum(n for _, n in spent)

    def left(self, key: str) -> int:
        if not RateLimiter.enabled:
            return self.limit
        return max(0, self.limit - self.used(key))

    def add(self, key: str, n: int) -> None:
        if RateLimiter.enabled and n:
            self._used[key].append((time.monotonic(), n))

    def retry_after(self, key: str) -> int:
        spent = self._used.get(key)
        if not spent:
            return 1
        return max(1, math.ceil(self.window - (time.monotonic() - spent[0][0])))

    def reset(self) -> None:
        self._used.clear()


action_limiter = RateLimiter(ACTIONS_PER_MINUTE, 60, {"message": ACTIONS, "code": "rate_limited"})
action_bytes = ByteBudget(ACTION_BYTES, ACTION_BYTES_SECONDS)

_refused_log: deque[float] = deque()


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


def raw_path(scope) -> str:
    """Het pad zoals de browser het stuurde (met procenttekens, zonder query)."""
    return (scope.get("raw_path") or quote(scope["path"]).encode()).decode("latin-1")


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


def response_headers(
    headers: httpx.Headers, host: str, page: bool = False
) -> list[tuple[bytes, bytes]]:
    """De koppen naar de browser. page: de editorpagina (de CSP van Keuze 9)."""
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
    if page:
        out.append((b"content-security-policy", EDITOR_CSP.encode()))
    else:
        out.append((b"cross-origin-resource-policy", b"same-origin"))
        if kind in MARKUP_TYPES:
            out.append((b"content-security-policy", b"sandbox"))
    return out


def _refused(kind: str, method: str, path: str) -> None:
    now = time.monotonic()
    while _refused_log and now - _refused_log[0] > 60:
        _refused_log.popleft()
    if len(_refused_log) < REFUSED_LOG_PER_MINUTE:
        _refused_log.append(now)
        log.warning("office: pad geweigerd %s %s %s", kind, method, path[:200])


def _response(exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


def _unavailable() -> JSONResponse:
    return _response(errors.fail(502, "office_unavailable", "De editor is nu niet bereikbaar"))


def _too_large() -> JSONResponse:
    return _response(errors.fail(413, "office_too_large", "Dit is te groot voor de editor"))


def _no_gate() -> JSONResponse:
    return _response(errors.fail(401, "office_gate", GATE))


def _busy() -> JSONResponse:
    return _response(errors.fail(503, "office_unavailable", "De editor is nu niet bereikbaar"))


def _over(key: str) -> JSONResponse:
    retry = str(action_bytes.retry_after(key))
    return _response(errors.fail(429, "rate_limited", ACTIONS, headers={"Retry-After": retry}))


class OfficeProxy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not ours(scope["path"]):
            return await self.app(scope, receive, send)
        settings = runtime.settings()
        eng = engine.current() if settings.office_configured else None
        if eng is None:
            return await self.app(scope, receive, send)  # de catch-all geeft 404
        await self.handle(eng, scope, receive, send)

    async def handle(self, eng, scope, receive, send) -> None:
        method = scope["method"]
        path = raw_path(scope)
        kind = eng.classify(method, path, False) if ds_target(scope) is not None else None
        if kind is None:
            _refused("geen", method, path)
            return await self.app(scope, receive, send)  # de catch-all geeft 404
        headers = Headers(scope=scope)
        extra: list[tuple[str, str]] = []
        budget: str | None = None
        try:
            if kind == PathClass.PAGE:
                dest = headers.get("sec-fetch-dest")
                if dest is not None and dest.strip().lower() not in PAGE_DESTS:
                    _refused("page", method, path)
                    return await self.app(scope, receive, send)
                if await gate.check(headers) is None:
                    return await self.app(scope, receive, send)  # 404: geen hint
            elif kind == PathClass.SETTINGS:
                if await gate.check(headers) is None:
                    return await _no_gate()(scope, receive, send)
            elif kind in (PathClass.DATA, PathClass.ACTION):
                key = eng.session_key(path)
                user = await gate.check(headers, key) if key is not None else None
                if user is None:
                    return await _no_gate()(scope, receive, send)
                if kind == PathClass.ACTION:
                    query = scope.get("query_string", b"").decode("latin-1")
                    try:
                        passed = eng.action_authorization(path, query, headers.get("authorization"))
                    except TokenError:
                        log.info("office: editoractie over een andere key geweigerd")
                        return await _no_gate()(scope, receive, send)
                    if passed:
                        extra.append(("Authorization", passed))
                    budget = str(user.user_id)
                    action_limiter.check(budget)
                    length = _header(scope, b"content-length")
                    if length.isdigit() and int(length) > action_bytes.left(budget):
                        return await _over(budget)(scope, receive, send)
            elif kind != PathClass.STATIC:
                _refused(str(kind), method, path)
                return await self.app(scope, receive, send)
        except HTTPException as exc:  # de grens per gebruiker (429)
            return await _response(exc)(scope, receive, send)
        except Exception:  # noqa: BLE001 - de database: liever dicht dan open
            log.exception("office: de toegangscontrole mislukte")
            return await _busy()(scope, receive, send)
        await self.forward(
            scope, receive, send, page=kind == PathClass.PAGE, extra=extra, budget=budget
        )

    async def forward(
        self,
        scope,
        receive,
        send,
        *,
        page: bool = False,
        extra: list[tuple[str, str]] | None = None,
        budget: str | None = None,
    ) -> None:
        target = ds_target(scope)
        if target is None:
            return await self.app(scope, receive, send)  # de catch-all geeft 404
        length = _header(scope, b"content-length")
        if length.isdigit() and int(length) > MAX_BODY:
            return await _too_large()(scope, receive, send)
        method = scope["method"]
        body = None
        if method not in ("GET", "HEAD"):
            body = _body(receive, budget)
        server = engine.upstream()
        if server is None:
            return await _unavailable()(scope, receive, send)
        try:
            upstream = await server.open(
                method, target, [*request_headers(scope), *(extra or [])], body
            )
        except ValueError:
            # Een pad dat DS anders zou krijgen dan wij het zien ('//', '.', '..'): nooit naar DS
            return await self.app(scope, receive, send)
        except BodyTooLarge:
            return await _too_large()(scope, receive, send)
        except OverBudget:
            assert budget is not None
            return await _over(budget)(scope, receive, send)
        except httpx.HTTPError as exc:
            log.warning("office: de document server antwoordt niet (%r)", exc)
            return await _unavailable()(scope, receive, send)
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": upstream.status_code,
                    "headers": response_headers(
                        upstream.headers, _header(scope, b"host"), page=page
                    ),
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


async def _body(receive, budget: str | None = None):
    """De body van de browser in stukken; BodyTooLarge boven MAX_BODY, OverBudget boven wat
    deze gebruiker nog aan editoracties mag (budget: zijn id)."""
    seen = 0
    left = action_bytes.left(budget) if budget is not None else None
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise httpx.ReadError("de browser ging weg")
        chunk = message.get("body", b"")
        seen += len(chunk)
        if seen > MAX_BODY:
            raise BodyTooLarge
        if budget is not None:
            action_bytes.add(budget, len(chunk))
            if left is not None and seen > left:
                raise OverBudget
        if chunk:
            yield chunk
        if not message.get("more_body", False):
            return
