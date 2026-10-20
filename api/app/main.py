import hmac
from collections.abc import AsyncIterator
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.cluster import ClusterStatus, cluster_status, single_status
from app.config import Settings, get_settings

try:
    __version__ = version("platform-api")
except PackageNotFoundError:
    __version__ = "0.1.0"

app = FastAPI(
    title="Platform API",
    version=__version__,
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
)


class Status(BaseModel):
    status: str


class Readiness(Status):
    checks: dict[str, str]


class Info(BaseModel):
    name: str
    version: str
    environment: str
    node: str


@app.get("/health", tags=["systeem"])
async def health() -> Status:
    """Liveness: het proces draait."""
    return Status(status="ok")


@app.get("/ready", tags=["systeem"])
async def ready() -> Readiness:
    """Readiness: klaar voor verkeer. Database- en S3-checks volgen in fase 2."""
    return Readiness(status="ok", checks={})


@app.get("/api/v1/info", tags=["systeem"])
async def info(settings: Annotated[Settings, Depends(get_settings)]) -> Info:
    return Info(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment,
        node=settings.node_name,
    )


async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def require_admin(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.admin_token:
        raise HTTPException(503, "Beheertoken is niet ingesteld")
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token.encode(), settings.admin_token.encode()):
        raise HTTPException(401, "Ongeldig beheertoken")


@app.get("/api/v1/admin/status", tags=["beheer"], dependencies=[Depends(require_admin)])
async def admin_status(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(http_client)],
) -> ClusterStatus:
    """Systeemstatus van alle nodes: database, opslag, rand en schijfruimte."""
    if settings.mode == "single":
        name, ip = settings.nodes()[0]
        return await single_status(client, name, ip)
    return await cluster_status(client, settings.nodes())
