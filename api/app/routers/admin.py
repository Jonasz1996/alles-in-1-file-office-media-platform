import shutil
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text

from app.cluster import ClusterStatus, Disk, NodeStatus, cluster_status
from app.config import Settings, get_settings
from app.db import Session
from app.models import Blob, Node, User
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin", tags=["beheer"])


async def http_client():
    async with httpx.AsyncClient() as client:
        yield client


async def standalone_status(session: Session, settings: Settings) -> ClusterStatus:
    messages: list[str] = []
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 - elke databasefout telt als storing
        db_ok = False
        messages.append("De database is niet bereikbaar.")

    settings.blob_dir.mkdir(parents=True, exist_ok=True)
    probe = settings.blob_dir / ".schrijftest"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
        storage_ok = True
    except OSError:
        storage_ok = False
        messages.append("De opslagmap is niet schrijfbaar.")

    usage = shutil.disk_usage(settings.data_dir)
    disk = Disk(total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free)
    if usage.total and usage.used / usage.total > 0.8:
        messages.append(f"{settings.node_name}: datadisk meer dan 80 % vol.")

    node = NodeStatus(
        name=settings.node_name,
        ip="127.0.0.1",
        checks={"database": db_ok, "storage": storage_ok, "api": True},
        db_role="sqlite" if settings.db_url.startswith("sqlite") else "postgresql",
        disk=disk,
    )
    status = "down" if not (db_ok and storage_ok) else ("degraded" if messages else "ok")
    return ClusterStatus(status=status, nodes=[node], database=[], messages=messages)


class Counts(ClusterStatus):
    users: int = 0
    files: int = 0
    stored_bytes: int = 0
    logical_bytes: int = 0


@router.get("/status")
async def admin_status(
    _: AdminUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(http_client)],
) -> Counts:
    """Systeemstatus: onderdelen, schijfruimte en hoeveel er is opgeslagen."""
    if settings.mode == "cluster":
        base = await cluster_status(client, settings.nodes())
    else:
        base = await standalone_status(session, settings)
    users = await session.scalar(select(func.count()).select_from(User))
    files = await session.scalar(select(func.count()).select_from(Node).where(Node.kind == "file"))
    stored = await session.scalar(select(func.coalesce(func.sum(Blob.size), 0)))
    logical = await session.scalar(
        select(func.coalesce(func.sum(Node.size), 0)).where(Node.kind == "file")
    )
    return Counts(
        **base.model_dump(),
        users=users or 0,
        files=files or 0,
        stored_bytes=int(stored or 0),
        logical_bytes=int(logical or 0),
    )
