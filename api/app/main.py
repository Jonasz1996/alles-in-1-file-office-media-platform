import logging
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.migrate import upgrade
from app.routers import admin, auth, files

try:
    __version__ = version("platform-api")
except PackageNotFoundError:
    __version__ = "0.2.0"

log = logging.getLogger("platform")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
]
# Alleen voor de web-app zelf; gedownloade bestanden krijgen hun eigen beleid.
WEB_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
)


class SecurityHeaders:
    """Beveiligingsheaders op elk antwoord (voorheen deed Traefik dit)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS if k not in present]
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        await self.app(scope, receive, send_with_headers)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await upgrade(settings)
    log.info("Platform %s gestart (%s), data in %s", __version__, settings.mode, settings.data_dir)
    yield


app = FastAPI(
    title="Platform API",
    version=__version__,
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    lifespan=lifespan,
)
app.add_middleware(SecurityHeaders)
app.include_router(auth.router)
app.include_router(files.router)
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
