"""Previews opvragen en tonen, en de achtergrondtaken op /beheer."""

import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import access, jobs, previews
from app.config import Settings, get_settings
from app.db import Session
from app.models import Blob, Job, Node
from app.security import AdminUser, CurrentUser
from app.storage import BlobStore

router = APIRouter(prefix="/api/v1", tags=["previews"])
MAX_NODES = 500
# Het adres verandert als het verloopt; tot die tijd hoeft de browser niets opnieuw te vragen
CACHE = "private, max-age=86400, immutable"


class PreviewRequest(BaseModel):
    node_ids: list[uuid.UUID] = Field(max_length=MAX_NODES)


class PreviewOut(BaseModel):
    # pending, ready, none (geen preview mogelijk) of failed
    state: str
    thumb: str | None
    large: str | None
    meta: dict[str, Any]


@router.post("/previews")
async def preview_info(
    body: PreviewRequest,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, PreviewOut]:
    """Previews van (hooguit 500) bestanden; wat niet bestaat of wat je niet mag zien (niet
    van jou en niet met je gedeeld), ontbreekt in het antwoord. thumb en large zijn
    ondertekende adressen voor een <img>."""
    ids = list(dict.fromkeys(body.node_ids))
    loaded = (await session.scalars(select(Node).where(Node.id.in_(ids)))).all() if ids else []
    # Wat van een ander is: in één query nagaan wat met je gedeeld is
    others = [n.id for n in loaded if n.owner_id != user.id]
    grants = await access.resolve(session, user.id, others) if others else {}
    visible = {
        n.id: n
        for n in loaded
        if (n.owner_id == user.id and user.role != "guest") or n.id in grants
    }
    nodes = [
        visible[i] for i in ids if i in visible and visible[i].kind == "file" and visible[i].blob_id
    ]
    shas = {n.blob_id for n in nodes}
    blobs = {}
    if shas:
        rows = await session.scalars(select(Blob).where(Blob.sha256.in_(shas)))
        blobs = {b.sha256: b for b in rows.all()}
    secret, exp = previews.url_key(settings), previews.expiry()
    return {
        str(n.id): PreviewOut(**previews.info(secret, blobs.get(n.blob_id), exp)) for n in nodes
    }


@router.get("/previews/{sha}/{size}.webp")
async def preview_image(
    sha: str,
    size: int,
    settings: Annotated[Settings, Depends(get_settings)],
    exp: int = 0,
    sig: str = "",
) -> FileResponse:
    """De preview zelf, zonder inloggen maar alleen met een geldige handtekening (zo werkt
    een <img>). 403 bij een verkeerde of verlopen handtekening, 404 als hij er niet is."""
    if not previews.SHA.fullmatch(sha) or size not in previews.SIZES:
        raise HTTPException(404, "Niet gevonden")
    if not previews.valid(previews.url_key(settings), sha, size, exp, sig):
        raise HTTPException(403, "Deze link is ongeldig of verlopen")
    path = previews.directory(BlobStore(settings.blob_dir), sha) / f"{size}.webp"
    if not await anyio.Path(path).is_file():
        raise HTTPException(404, "Geen preview")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": CACHE})


class FailedJob(BaseModel):
    id: uuid.UUID
    type: str
    key: str | None
    attempts: int
    error: str | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class JobsOverview(BaseModel):
    counts: dict[str, int]
    failed: list[FailedJob]


class Retried(BaseModel):
    count: int


@router.get("/admin/jobs", tags=["beheer"])
async def admin_jobs(_: AdminUser, session: Session) -> JobsOverview:
    """Hoeveel taken er klaarstaan, lopen, klaar en mislukt zijn, en de laatste 100 mislukte."""
    counts = await jobs.counts(session)
    failed = [FailedJob.model_validate(j) for j in await jobs.failed(session, 100)]
    return JobsOverview(counts=counts, failed=failed)


@router.post("/admin/jobs/retry-failed", tags=["beheer"])
async def retry_failed(_: AdminUser, session: Session) -> Retried:
    """Alle mislukte taken opnieuw, elk weer met alle pogingen."""
    count = await jobs.retry_failed(session)
    await session.commit()
    return Retried(count=count)


@router.post("/admin/jobs/{job_id}/retry", tags=["beheer"], status_code=204)
async def retry_job(job_id: uuid.UUID, _: AdminUser, session: Session) -> None:
    if not await jobs.retry(session, job_id):
        exists = await session.get(Job, job_id)
        await session.commit()
        if exists is None:
            raise HTTPException(404, "Taak niet gevonden")
        raise HTTPException(409, {"message": "Deze taak is niet mislukt", "code": "not_failed"})
    await session.commit()
