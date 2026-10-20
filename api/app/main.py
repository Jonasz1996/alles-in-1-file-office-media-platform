from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import BaseModel

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
