import asyncio
import gzip
import logging
import mimetypes
import os
import re
import signal
import threading
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

from app import __version__, errors, events, jobs, photometa, previews
from app.certs import CertManager
from app.config import Settings, get_settings
from app.media import analysis as media_analysis  # noqa: F401 - registreert media.analyse
from app.media import runtime as media_runtime
from app.migrate import upgrade
from app.office import convert as office_convert
from app.office import engine as office_engine
from app.office import health as office_health
from app.office import recovery as office_recovery
from app.office import runtime as office_runtime
from app.office import sweep as office_sweep
from app.office.proxy import OfficeProxy
from app.office.state import OfficeState
from app.routers import (
    admin,
    admin_media,
    admin_office,
    albums,
    archive,
    auth,
    batch,
    files,
    folder_tree,
    groups,
    jobs_admin,
    media_library,
    media_match,
    media_music,
    media_play,
    media_progress,
    media_subs,
    myshares,
    office,
    office_internal,
    office_new,
    office_pdf,
    photos,
    public,
    public_media,
    search,
    shares,
    stream,
    stream_audio,
    stream_file,
    trash,
    uploads,
    viewer,
)
from app.routers import events as live_events
from app.routers import previews as preview_routes
from app.security import RateLimiter

log = logging.getLogger("platform")

# Downloadlinks, previewadressen en de sleutel van een publieke link dragen hun toegang in
# het adres (een <img> of <video> kan geen header sturen), en het adres van een publieke link
# (/s/<token>) is de toegang zelf; die waarden horen niet in de toegangslog. Net zo de
# opslaglinks van de document server (md5), de keys van office (shardkey) en cmd van
# downloadas: die JSON draagt het sessietoken van DS (tokenSession, 30 dagen geldig; spike
# A6). cmd loopt door tot & of witruimte, ook over aanhalingstekens heen, voor het geval een
# client de JSON rauw stuurt.
SECRET_PARAMS = re.compile(
    r"([?&](?:token|sig|key|md5|shardkey|cmd)=)(?:(?<=cmd=)[^&\s]*|[^&\s\"]*)"
)
LINK_PATH = re.compile(r"((?:^|[\s\"])/s/)[^/?#\s\"]+")
# De sid van een afspeelsessie staat in het pad (fase 5, Keuze 35): /api/v1/stream/<id>.<geheim>/…
# Het deel na de eerste punt is de toegang zelf; het id ervoor mag blijven (om te volgen)
STREAM_PATH = re.compile(r"(/api/v1/stream/[^/.\s\"]+\.)[^/?\s\"]+")


def _hide(value: str) -> str:
    value = SECRET_PARAMS.sub(r"\1***", value)
    return STREAM_PATH.sub(r"\1***", LINK_PATH.sub(r"\1***", value))


class HideSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_hide(a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(HideSecrets())

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]
HSTS = (b"strict-transport-security", b"max-age=31536000")
ACME_PATH = "/.well-known/acme-challenge/"
# Vangrail voor een body die stilvalt (headers binnen, daarna niets meer): zonder proxy
# ervoor zou zo'n verbinding anders voor altijd openblijven. Uploads geven zelf al na 60 s
# op (storage.CHUNK_TIMEOUT); dit geldt voor alle andere verzoeken.
BODY_TIMEOUT = 90.0
# Alleen voor de web-app zelf; gedownloade bestanden krijgen hun eigen beleid. Geen
# 'unsafe-inline' voor stijlen: de app zet geen <style> of style-attribuut in de HTML (React
# style-props lopen via CSSOM en vallen daar niet onder). media-src blob: (fase 5, F4): de
# speler zet een object-URL van MediaSource in video.src; zonder blob: speelt geen MSE-video.
# Dezelfde CSP staat 5 keer in web/nginx.conf en in de CI-job images (test_csp_nginx.py).
WEB_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
    "manifest-src 'self'; worker-src 'self'"
)
# Bestanden van de web-app en hun media-type; de rest via mimetypes
WEB_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".txt": "text/plain",
}
# Voorgecomprimeerde varianten (vite.config.ts, precompress), in volgorde van voorkeur
ENCODINGS = (("br", ".br"), ("gzip", ".gz"))
IMMUTABLE = "public, max-age=31536000, immutable"


def _bounded(receive):
    """receive() met een tijdslimiet zolang de body nog binnenkomt. Daarna (een download
    die op het sluiten van de verbinding let) wacht receive() weer onbeperkt."""
    state = "body"

    async def wrapped():
        nonlocal state
        if state == "weg":
            return {"type": "http.disconnect"}
        if state == "klaar":
            return await receive()
        try:
            with anyio.fail_after(BODY_TIMEOUT):
                message = await receive()
        except TimeoutError:
            state = "weg"  # de app ziet een verbroken verbinding en rondt af
            return {"type": "http.disconnect"}
        if message["type"] != "http.request" or not message.get("more_body", False):
            state = "klaar"
        return message

    return wrapped


def _host(scope) -> str:
    for key, value in scope.get("headers", []):
        if key == b"host":
            host = value.decode("latin-1").lower()
            # [::1]:443 of voorbeeld.nl:80 -> zonder poort
            return host.rsplit(":", 1)[0] if host.rfind(":") > host.rfind("]") else host
    return ""


class Edge:
    """Wat voorheen Traefik deed: http doorsturen naar https zodra er een certificaat
    is, HSTS over https, beveiligingsheaders op elk antwoord en een grens aan een body
    die stilvalt."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        certs: CertManager | None = getattr(scope["app"].state, "certs", None)
        secure = scope.get("scheme") == "https"
        if (
            not secure
            and certs is not None
            and certs.serving
            and certs.active
            and not scope["path"].startswith(ACME_PATH)
            and _host(scope) == certs.config.domain
        ):
            path = scope.get("raw_path") or quote(scope["path"]).encode()
            query = scope.get("query_string", b"")
            url = f"https://{certs.config.domain}{path.decode('latin-1')}"
            if query:
                url += "?" + query.decode("latin-1")
            return await RedirectResponse(url, status_code=308)(scope, receive, send)

        extra = list(SECURITY_HEADERS)
        if secure and certs is not None and certs.active:
            extra.append(HSTS)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = [
                    *message.get("headers", []),
                    *((k, v) for k, v in extra if k not in present),
                ]
            await send(message)

        await self.app(scope, _bounded(receive), send_with_headers)


STREAM_PREFIX = "/api/v1/stream/"
CORP = (b"cross-origin-resource-policy", b"same-origin")
NO_STORE = (b"cache-control", b"no-store")


class StreamHeaders:
    """De koppen van Keuze 35 op elk antwoord onder /api/v1/stream/, ook op een fout:
    Cross-Origin-Resource-Policy: same-origin, nooit Content-Disposition, en no-store als de
    route zelf geen Cache-Control zet (de routes van M3, M4, M5 en M9 zetten hun eigen)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(STREAM_PREFIX):
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in (b"content-disposition", CORP[0])
                ]
                headers.append(CORP)
                if not any(k.lower() == NO_STORE[0] for k, _ in headers):
                    headers.append(NO_STORE)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


def accepted_codings(header: str) -> dict[str, float]:
    """Accept-Encoding als {codering: q}; "*" staat voor elke codering die niet genoemd is."""
    out: dict[str, float] = {}
    for part in header.split(","):
        name, _, params = part.strip().partition(";")
        name = name.strip().lower()
        if not name:
            continue
        q = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        out[name] = max(0.0, min(q, 1.0))
    return out


def accepts(codings: dict[str, float], coding: str) -> float:
    """De q-waarde voor deze codering (0 = niet welkom)."""
    if coding in codings:
        return codings[coding]
    return codings.get("*", 0.0)


# --- JSON comprimeren (alleen lijsten zonder geheimen) ---

# Alleen lijstantwoorden zonder tokens of ondertekende adressen (BREACH): nooit /auth, previews
# of de live stroom
JSON_COMPRESSIBLE = re.compile(
    r"^/api/v1/(files/[^/]+/children|files/root|search|trash(/overview)?|shared-with-me"
    r"|files/lookup)$"
)
JSON_MIN_BYTES = 8 * 1024
# Daarboven comprimeert een thread (zlib laat de GIL los), niet de event loop
JSON_THREAD_BYTES = 64 * 1024
JSON_LEVEL = 6


def _gzip(body: bytes) -> bytes:
    # mtime=0: altijd dezelfde uitvoer, en gzip.compress laat het dan in één keer aan zlib over
    return gzip.compress(body, compresslevel=JSON_LEVEL, mtime=0)


class JsonCompression:
    """Comprimeert grote JSON-lijsten met gzip als de client dat wil (bouwontwerp keuze 22)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] not in ("GET", "POST")
            or not JSON_COMPRESSIBLE.match(scope["path"])
            or accepts(accepted_codings(_header(scope, b"accept-encoding")), "gzip") <= 0
        ):
            return await self.app(scope, receive, send)

        start: dict | None = None
        parts: list[bytes] = []
        passthrough = False

        async def wrapped(message):
            nonlocal start, passthrough
            if passthrough:
                return await send(message)
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                kind = headers.get(b"content-type", b"").split(b";")[0].strip()
                if kind != b"application/json" or b"content-encoding" in headers:
                    passthrough = True
                    return await send(message)
                start = message
                return None
            if message["type"] != "http.response.body" or start is None:
                return await send(message)
            parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return None
            body = b"".join(parts)
            headers = [
                (k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"
            ]
            if len(body) >= JSON_MIN_BYTES:
                if len(body) > JSON_THREAD_BYTES:
                    body = await anyio.to_thread.run_sync(_gzip, body)
                else:
                    body = _gzip(body)
                vary = [v for k, v in headers if k.lower() == b"vary"]
                headers = [(k, v) for k, v in headers if k.lower() != b"vary"]
                vary_value = b", ".join([*vary, b"Accept-Encoding"])
                headers += [(b"content-encoding", b"gzip"), (b"vary", vary_value)]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return None

        await self.app(scope, receive, wrapped)


@contextmanager
def _streams_end_on_signal():
    """Open gebeurtenisstromen afronden zodra het stoppen begint, met welke server dan ook.
    Uvicorn wacht bij stoppen eerst op lopende antwoorden en sluit pas daarna de lifespan af;
    een stroom liep zo door tot het token verliep, en `uvicorn --reload` bleef zolang op de
    oude worker hangen. Uvicorn zet zijn signaalafhandeling vóór de lifespan neer: deze gaat
    ervoor en roept daarna die van uvicorn aan. (python -m app.serve sluit de hub zelf ook.)"""
    if threading.current_thread() is not threading.main_thread():
        yield  # signalen komen alleen in de hoofdthread binnen
        return
    loop = asyncio.get_running_loop()
    installed = {}

    def chained(previous):
        def handler(sig, frame):
            hub = events.hub  # die van nu, niet een nieuwe van een volgende start
            with suppress(RuntimeError):  # de loop is al dicht
                loop.call_soon_threadsafe(hub.close)
            previous(sig, frame)

        return handler

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)
        if callable(previous):  # bij SIG_DFL stopt het proces toch meteen, bij SIG_IGN nooit
            handler = chained(previous)
            signal.signal(sig, handler)
            installed[sig] = handler, previous
    try:
        yield
    finally:
        for sig, (handler, previous) in installed.items():
            if signal.getsignal(sig) is handler:  # niet weghalen wat een ander intussen neerzette
                signal.signal(sig, previous)


async def _office_start(app: FastAPI, settings: Settings) -> None:
    """Office (fase 4), alleen als PLATFORM_OFFICE_URL gezet is: OfficeState, en met geldige
    instellingen de taken gezondheid, ronde en herstel. Zonder office gebeurt hier niets."""
    if not settings.office_url:
        return
    state = app.state.office = OfficeState(problem=settings.office_problem())
    office_runtime.bind(app, settings)
    if state.problem:
        state.health = "misconfigured"
        log.error("Office staat uit: %s", state.problem)
        return
    state.health = "starting"
    await office_health.start(app)
    await office_sweep.start(app)
    await office_recovery.start(app)


async def _office_stop(app: FastAPI) -> None:
    if getattr(app.state, "office", None) is None:
        return
    await office_health.stop(app)  # ook de taken van de ronde en het herstel
    await office_engine.close()
    office_runtime.unbind()
    del app.state.office


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.started_at = datetime.now(UTC)
    RateLimiter.enabled = settings.rate_limits
    if not settings.rate_limits:
        log.warning("Rate limits staan uit (PLATFORM_RATE_LIMITS=false): alleen voor tests")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await upgrade(settings)
    if events.hub.closed:  # een eerdere keer gestopt in hetzelfde proces (tests)
        events.hub = events.Hub()
    certs = CertManager(settings)
    await certs.start()
    app.state.certs = certs
    cleaner = asyncio.create_task(uploads.cleanup_loop(settings), name="uploads opruimen")
    # Eerst wat bij een crash bleef liggen terug in de rij (en weg, voor tussenbestanden) en
    # previews voor oudere inhoud, dan pas de workers
    try:
        await office_convert.clean_tmp(settings)
    except Exception:  # noqa: BLE001 - dan blijven ze nog even staan
        log.exception("Tussenbestanden van een vorige keer opruimen mislukt")
    await jobs.recover(settings)
    try:
        await previews.enqueue_missing(settings)
    except Exception:  # noqa: BLE001 - zonder previews werkt de rest gewoon
        log.exception("Previews klaarzetten voor bestaande bestanden mislukt")
    try:
        await previews.enqueue_outdated(settings)
    except Exception:  # noqa: BLE001 - zie hierboven
        log.exception("Verouderde previews klaarzetten mislukt")
    try:
        await photometa.enqueue_backfill(settings)
    except Exception:  # noqa: BLE001 - zonder opnamedatums werkt de rest gewoon
        log.exception("Metadata van bestaande foto's klaarzetten mislukt")
    # Een Runner per rij (Keuze 14 van fase 5); het opruimwerk alleen in default
    runners = await jobs.start_runners(settings)
    await _office_start(app, settings)
    # Media (fase 5): zonder PLATFORM_MEDIA_ENABLED gebeurt hier niets (geen worker, elk
    # media-eindpunt 503 media_off)
    media_runtime.bind(app, settings)
    await media_runtime.start(app, settings)
    log.info("Platform %s gestart (%s), data in %s", __version__, settings.mode, settings.data_dir)
    with _streams_end_on_signal():
        yield
    events.hub.close()  # wat nog openstond afronden (na een signaal al gebeurd)
    events.hub = events.Hub()  # een volgende start in hetzelfde proces (tests) kan weer
    await _office_stop(app)
    await jobs.stop_runners(runners)
    await media_runtime.stop(app)  # na de rijen: een taak van media gebruikt de worker nog
    media_runtime.unbind()
    cleaner.cancel()
    await certs.stop()
    log.info("Platform %s gestopt", __version__)


app = FastAPI(
    title="Platform API",
    version=__version__,
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    lifespan=lifespan,
)
# De binnenste: /office/ naar de document server (alleen als office aan staat), vóór de
# catch-all van de web-app
app.add_middleware(OfficeProxy)
app.add_middleware(JsonCompression)
app.add_middleware(StreamHeaders)
app.add_middleware(Edge)  # de buitenste: beveiligingsheaders op elk antwoord
app.add_exception_handler(RequestValidationError, errors.invalid_input)
app.include_router(auth.router)
app.include_router(files.router)
app.include_router(trash.router)
app.include_router(batch.router)
app.include_router(archive.router)
app.include_router(folder_tree.router)
app.include_router(uploads.router)
app.include_router(search.router)
app.include_router(shares.router)
app.include_router(groups.router)
app.include_router(public.router)
app.include_router(admin.router)
app.include_router(preview_routes.router)
app.include_router(viewer.router)
app.include_router(photos.router)
app.include_router(albums.router)
app.include_router(jobs_admin.router)
app.include_router(myshares.router)
app.include_router(live_events.router)
app.include_router(office.router)
app.include_router(office_pdf.router)
app.include_router(office_new.router)
app.include_router(admin_office.router)
# Media (fase 5, §2.0): stubs van M0 die 503 media_off geven tot hun pakket ze vult. Alles onder
# /api/v1/stream/ gaat eerst langs sessions.guard (regel 6, de dependency van elke router)
app.include_router(media_library.router)
app.include_router(media_play.router)
app.include_router(public_media.router)
app.include_router(media_progress.router)
app.include_router(media_match.router)
app.include_router(media_music.router)
app.include_router(admin_media.router)
app.include_router(stream.router)
app.include_router(media_subs.router)
app.include_router(stream_audio.router)
app.include_router(stream_file.router)

# Wat de document server aanroept, op een eigen luisteraar (Keuze 8, app/serve.py): de
# publieke poorten kennen /internal/ niet. Deelt app.state (database, blob-opslag,
# OfficeState) met de hoofd-app.
internal_app = FastAPI(title="Platform intern", openapi_url=None, docs_url=None, redoc_url=None)
internal_app.add_middleware(Edge)
internal_app.add_exception_handler(RequestValidationError, errors.invalid_input)
internal_app.include_router(office_internal.router)
internal_app.include_router(office_pdf.internal_router)
internal_app.state = app.state

ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@internal_app.api_route("/{path:path}", methods=ALL_METHODS, include_in_schema=False)
async def internal_unknown(path: str):
    raise errors.fail(404, "not_found", "Niet gevonden")


class Status(BaseModel):
    status: str


class Info(BaseModel):
    name: str
    version: str
    environment: str
    node: str
    mode: str


@app.get("/health", tags=["systeem"])
async def health() -> Status:
    """Liveness: het proces draait."""
    return Status(status="ok")


@app.get("/api/v1/info", tags=["systeem"])
async def info(settings: Annotated[Settings, Depends(get_settings)]) -> Info:
    return Info(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment,
        node=settings.node_name,
        mode=settings.mode,
    )


@app.get(ACME_PATH + "{token}", include_in_schema=False)
async def acme_challenge(token: str, request: Request) -> PlainTextResponse:
    """Antwoord op de HTTP-01-challenge van Let's Encrypt."""
    certs: CertManager | None = getattr(request.app.state, "certs", None)
    answer = certs.challenges.get(token) if certs else None
    if answer is None:
        raise errors.fail(404, "not_found", "Onbekende challenge")
    return PlainTextResponse(answer)


def _etag(st: os.stat_result) -> str:
    return f'W/"{st.st_mtime_ns}-{st.st_size}"'


def _etag_matches(header: str, etag: str) -> bool:
    """If-None-Match vergelijkt zwak (W/ telt niet mee); "*" past altijd."""
    wanted = etag.removeprefix("W/")
    for tag in header.split(","):
        tag = tag.strip()
        if tag == "*" or tag.removeprefix("W/") == wanted:
            return True
    return False


def _web_file(request: Request, file: Path, rel: str) -> Response:
    """Eén bestand van de web-app: voorgecomprimeerd als dat kan, met ETag en 304."""
    media_type = WEB_TYPES.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0]
    immutable = Path(rel).parts[:1] == ("assets",)
    headers = {
        "Cache-Control": IMMUTABLE if immutable else "no-cache",
        "Content-Security-Policy": WEB_CSP,
    }
    original = file.stat()
    chosen, stat_result = file, original
    variants = []
    for coding, ext in ENCODINGS:
        try:
            st = file.with_name(file.name + ext).stat()
        except OSError:
            continue
        # Een variant die ouder is dan het origineel (het origineel is daarna veranderd) niet
        if st.st_mtime_ns >= original.st_mtime_ns:
            variants.append((coding, file.with_name(file.name + ext), st))
    if variants:
        headers["Vary"] = "Accept-Encoding"
        codings = accepted_codings(request.headers.get("accept-encoding", ""))
        # Bij gelijke q houdt max() de eerste: br vóór gzip
        coding, path, st = max(variants, key=lambda v: accepts(codings, v[0]))
        if accepts(codings, coding) > 0:
            chosen, stat_result = path, st
            headers["Content-Encoding"] = coding
    etag = _etag(stat_result)
    headers["ETag"] = etag
    if _etag_matches(request.headers.get("if-none-match", ""), etag):
        return Response(status_code=304, headers=headers)
    return FileResponse(
        chosen,
        media_type=media_type or "application/octet-stream",
        headers=headers,
        stat_result=stat_result,
    )


@app.api_route("/office", methods=ALL_METHODS, include_in_schema=False)
@app.api_route("/office/{path:path}", methods=ALL_METHODS, include_in_schema=False)
@app.api_route("/internal", methods=ALL_METHODS, include_in_schema=False)
@app.api_route("/internal/{path:path}", methods=ALL_METHODS, include_in_schema=False)
async def office_closed(path: str = ""):
    """/office/ zonder proxy (office uit, of de proxy liet het door) en /internal/ op de
    publieke poort: 404, wat de methode ook is."""
    raise errors.fail(404, "not_found", "Niet gevonden")


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def web_app(
    path: str, request: Request, settings: Annotated[Settings, Depends(get_settings)]
):
    """Serveert de gebouwde web-app; onbekende paden krijgen index.html (client-side routing).
    Een ontbrekend bestand onder assets/ of met een extensie geeft 404: een oud chunk na een
    nieuwe versie moet hard falen (de app herlaadt dan), niet als index.html terugkomen."""
    if path in ("api", "office", "internal") or path.startswith(("api/", "office/", "internal/")):
        raise errors.fail(404, "not_found", "Niet gevonden")
    root = settings.web_dist.resolve()
    index = root / "index.html"
    if not index.exists():
        raise errors.fail(404, "web_app_missing", "Web-app niet gebouwd")
    candidate = (root / path).resolve()
    if path and candidate.is_relative_to(root) and candidate.is_file():
        return _web_file(request, candidate, path)
    if Path(path).parts[:1] == ("assets",) or Path(path).suffix:
        raise errors.fail(404, "not_found", "Niet gevonden")
    return _web_file(request, index, "index.html")
