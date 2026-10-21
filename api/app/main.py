import asyncio
import logging
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
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from app import __version__, errors, events, jobs, photometa, previews
from app.certs import CertManager
from app.config import Settings, get_settings
from app.migrate import upgrade
from app.routers import (
    admin,
    albums,
    archive,
    auth,
    batch,
    files,
    folder_tree,
    groups,
    jobs_admin,
    myshares,
    photos,
    public,
    search,
    shares,
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
# (/s/<token>) is de toegang zelf; die waarden horen niet in de toegangslog
SECRET_PARAMS = re.compile(r"([?&](?:token|sig|key)=)[^&\s\"]*")
LINK_PATH = re.compile(r"((?:^|[\s\"])/s/)[^/?#\s\"]+")


def _hide(value: str) -> str:
    return LINK_PATH.sub(r"\1***", SECRET_PARAMS.sub(r"\1***", value))


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
]
HSTS = (b"strict-transport-security", b"max-age=31536000")
ACME_PATH = "/.well-known/acme-challenge/"
# Vangrail voor een body die stilvalt (headers binnen, daarna niets meer): zonder proxy
# ervoor zou zo'n verbinding anders voor altijd openblijven. Uploads geven zelf al na 60 s
# op (storage.CHUNK_TIMEOUT); dit geldt voor alle andere verzoeken.
BODY_TIMEOUT = 90.0
# Alleen voor de web-app zelf; gedownloade bestanden krijgen hun eigen beleid.
WEB_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
)


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
    # Eerst wat bij een crash bleef liggen terug in de rij en previews voor oudere inhoud,
    # dan pas de workers
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
    runner = jobs.Runner(settings)
    await runner.start()
    log.info("Platform %s gestart (%s), data in %s", __version__, settings.mode, settings.data_dir)
    with _streams_end_on_signal():
        yield
    events.hub.close()  # wat nog openstond afronden (na een signaal al gebeurd)
    events.hub = events.Hub()  # een volgende start in hetzelfde proces (tests) kan weer
    await runner.stop()
    cleaner.cancel()
    await certs.stop()


app = FastAPI(
    title="Platform API",
    version=__version__,
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    lifespan=lifespan,
)
app.add_middleware(Edge)
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


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def web_app(path: str, settings: Annotated[Settings, Depends(get_settings)]):
    """Serveert de gebouwde web-app; onbekende paden krijgen index.html (client-side routing)."""
    if path.startswith("api/"):
        raise errors.fail(404, "not_found", "Niet gevonden")
    root = settings.web_dist.resolve()
    index = root / "index.html"
    if not index.exists():
        raise errors.fail(404, "web_app_missing", "Web-app niet gebouwd")
    candidate = (root / path).resolve()
    if path and candidate.is_file() and candidate.is_relative_to(root):
        immutable = Path(path).parts[:1] == ("assets",)
        cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
        return FileResponse(
            candidate, headers={"Cache-Control": cache, "Content-Security-Policy": WEB_CSP}
        )
    return FileResponse(
        index, headers={"Cache-Control": "no-cache", "Content-Security-Policy": WEB_CSP}
    )
