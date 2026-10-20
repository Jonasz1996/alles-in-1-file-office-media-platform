import asyncio
import logging
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from app.certs import CertManager
from app.config import Settings, get_settings
from app.migrate import upgrade
from app.routers import admin, auth, files, search, uploads

try:
    __version__ = version("platform-api")
except PackageNotFoundError:
    __version__ = "0.3.0"

log = logging.getLogger("platform")

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await upgrade(settings)
    certs = CertManager(settings)
    await certs.start()
    app.state.certs = certs
    cleaner = asyncio.create_task(uploads.cleanup_loop(settings), name="uploads opruimen")
    log.info("Platform %s gestart (%s), data in %s", __version__, settings.mode, settings.data_dir)
    yield
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
app.include_router(auth.router)
app.include_router(files.router)
app.include_router(uploads.router)
app.include_router(search.router)
app.include_router(admin.router)


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
        raise HTTPException(404, "Onbekende challenge")
    return PlainTextResponse(answer)


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def web_app(path: str, settings: Annotated[Settings, Depends(get_settings)]):
    """Serveert de gebouwde web-app; onbekende paden krijgen index.html (client-side routing)."""
    if path.startswith("api/"):
        raise HTTPException(404, "Niet gevonden")
    root = settings.web_dist.resolve()
    index = root / "index.html"
    if not index.exists():
        raise HTTPException(404, "Web-app niet gebouwd")
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
