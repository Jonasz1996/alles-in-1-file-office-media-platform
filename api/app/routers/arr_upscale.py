"""AI-upscaling: de rij op /beheer en de API voor de upscaler-container (fase 11, stap 5c).

Beheerders (/api/v1/arr/upscale):
GET    /api/v1/arr/upscale                  → [UpscaleOut] (de nieuwste eerst)
POST   /api/v1/arr/upscale                  UpscaleIn → 201 UpscaleOut | 404
                                            | 409 arr_upscale_exists | 422 arr_upscale_kind
DELETE /api/v1/arr/upscale/{id}             → 204 (in de rij of bezig: geannuleerd)
GET    /api/v1/admin/arr/upscale            → UpscaleSettings (+ window_open, worker_seen_at)
PUT    /api/v1/admin/arr/upscale            UpscaleSettings → UpscaleSettings
POST   /api/v1/admin/arr/upscale/token      → {token} (één keer te zien; het oude vervalt)

De upscaler (/api/v1/upscaler, `Authorization: Bearer <token>`, anders 401):
POST   /api/v1/upscaler/claim               → 200 Claim | 204 (niets te doen, uit, of venster dicht)
GET    /api/v1/upscaler/jobs/{id}/source    → het origineel (Range kan)
POST   /api/v1/upscaler/jobs/{id}/progress  ProgressIn → {seconds_left} | 409 (geannuleerd)
PUT    /api/v1/upscaler/jobs/{id}/result    body = de mkv → 204; naast het origineel als
                                            "<naam> - AI 4K.mkv", van de eigenaar ervan
POST   /api/v1/upscaler/jobs/{id}/fail      FailIn → 204

Werk krijgt de upscaler alleen binnen het venster (app/arr/upscale.py), en per keer één job:
eerst een job die al liep (hervatten bij segments_done), dan de oudste in de rij. Er is maar één
upscaler: een job die loopt, is altijd van hem.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import errors
from app.arr import upscale
from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.models import ArrUpscale, MediaItem, Node, User
from app.security import AdminUser
from app.storage import BlobStore, TooLarge

router = APIRouter(prefix="/api/v1/arr/upscale", tags=["mediabeheer"])
admin_router = APIRouter(prefix="/api/v1/admin/arr/upscale", tags=["beheer"])
worker_router = APIRouter(prefix="/api/v1/upscaler", tags=["upscaler"])
Config = Annotated[Settings, Depends(get_settings)]
State = Literal["queued", "running", "done", "failed", "cancelled"]
SUFFIX = " - AI 4K"
MAX_RESULT_BYTES = 200 * 1024**3  # 200 GB


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(v: dt.datetime | None) -> dt.datetime | None:
    return v if v is None or v.tzinfo else v.replace(tzinfo=dt.UTC)


class UpscaleIn(BaseModel):
    node_id: uuid.UUID
    scale: Literal[2] = 2


class UpscaleOut(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    name: str
    state: State
    segments_done: int
    segments_total: int | None
    reason: str | None
    result_node_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime


class UpscaleSettings(BaseModel):
    enabled: bool = False
    start: int = Field(default=20, ge=0, le=59)
    end: int = Field(default=40, ge=0, le=59)
    vram_mb: int = Field(default=4096, ge=1024, le=8192)


class UpscaleView(UpscaleSettings):
    token_set: bool
    window_open: bool
    worker_seen_at: dt.datetime | None


class Claim(BaseModel):
    id: uuid.UUID
    name: str
    size: int
    scale: int
    segments_done: int
    seconds_left: int
    vram_mb: int


class ProgressIn(BaseModel):
    segments_done: int = Field(ge=0, le=1_000_000)
    segments_total: int | None = Field(default=None, ge=1, le=1_000_000)


class WindowOut(BaseModel):
    seconds_left: int


class FailIn(BaseModel):
    reason: str = Field(max_length=200)


async def _out(session, row: ArrUpscale) -> UpscaleOut:
    node = await session.get(Node, row.node_id)
    return UpscaleOut(
        id=row.id,
        node_id=row.node_id,
        name=node.name if node is not None else "?",
        state=row.state,  # type: ignore[arg-type]
        segments_done=row.segments_done,
        segments_total=row.segments_total,
        reason=row.reason,
        result_node_id=row.result_node_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --- beheerders ---------------------------------------------------------------------------------


@router.get("", response_model=list[UpscaleOut])
async def queue(admin: AdminUser, session: Session):
    rows = await session.scalars(
        select(ArrUpscale).order_by(ArrUpscale.created_at.desc()).limit(200)
    )
    return [await _out(session, r) for r in rows]


@router.post("", status_code=201, response_model=UpscaleOut)
async def add(body: UpscaleIn, admin: AdminUser, session: Session):
    node = await session.get(Node, body.node_id)
    if node is None or node.kind != "file" or not node.blob_id or node.deleted_at is not None:
        raise fail(404, "not_found", "Dat bestand bestaat niet")
    item = await session.get(MediaItem, body.node_id)
    if item is None or item.kind not in ("movie", "episode", "video"):
        raise fail(422, "arr_upscale_kind", "Alleen een film, aflevering of video kan")
    busy = await session.scalar(
        select(ArrUpscale.id).where(
            ArrUpscale.node_id == node.id, ArrUpscale.state.in_(("queued", "running"))
        )
    )
    if busy is not None:
        raise fail(409, "arr_upscale_exists", "Deze video staat al in de rij")
    row = ArrUpscale(node_id=node.id, scale=body.scale, state="queued", created_by=admin.id)
    session.add(row)
    await session.commit()
    return await _out(session, row)


@router.delete("/{job_id}", status_code=204)
async def cancel(job_id: uuid.UUID, admin: AdminUser, session: Session):
    row = await session.get(ArrUpscale, job_id)
    if row is None:
        raise fail(404, "not_found", "Die opdracht bestaat niet")
    if row.state in ("queued", "running"):
        row.state = "cancelled"
        row.updated_at = _now()
        await session.commit()
    else:
        await session.delete(row)
        await session.commit()
    return Response(status_code=204)


async def _view(session) -> UpscaleView:
    conf = await upscale.load(session)
    seen = await session.scalar(select(ArrUpscale.seen_at).order_by(ArrUpscale.seen_at.desc()))
    return UpscaleView(
        enabled=conf.enabled,
        start=conf.start,
        end=conf.end,
        vram_mb=conf.vram_mb,
        token_set=conf.token_sha is not None,
        window_open=upscale.seconds_left(dt.datetime.now(), conf.start, conf.end) > 0,
        worker_seen_at=seen,
    )


@admin_router.get("", response_model=UpscaleView)
async def settings_view(admin: AdminUser, session: Session):
    return await _view(session)


@admin_router.put("", response_model=UpscaleView)
async def settings_save(body: UpscaleSettings, admin: AdminUser, session: Session):
    if body.start == body.end:
        raise fail(422, "invalid_input", "Het venster mag niet leeg zijn", field="end")
    conf = await upscale.load(session)
    await upscale.store(
        session,
        replace(conf, enabled=body.enabled, start=body.start, end=body.end, vram_mb=body.vram_mb),
    )
    return await _view(session)


@admin_router.post("/token")
async def token(admin: AdminUser, session: Session):
    return {"token": await upscale.new_token(session)}


# --- de upscaler --------------------------------------------------------------------------------


async def worker(
    session: Session, authorization: Annotated[str | None, Header()] = None
) -> upscale.UpscaleConf:
    conf = await upscale.load(session)
    given = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not upscale.token_ok(conf, given):
        raise fail(401, "unauthorized", "Geen geldig token van de upscaler")
    return conf


Worker = Annotated[upscale.UpscaleConf, Depends(worker)]


async def _job(session, job_id: uuid.UUID) -> ArrUpscale:
    row = await session.get(ArrUpscale, job_id)
    if row is None:
        raise fail(404, "not_found", "Die opdracht bestaat niet")
    if row.state != "running":
        raise fail(409, "arr_upscale_stopped", "Deze opdracht loopt niet (meer)")
    return row


@worker_router.post("/claim", response_model=Claim)
async def claim(conf: Worker, session: Session):
    left = upscale.seconds_left(dt.datetime.now(), conf.start, conf.end)
    if not conf.enabled or left <= 0:
        return Response(status_code=204)
    now = _now()
    rows = list(
        await session.scalars(
            select(ArrUpscale)
            .where(ArrUpscale.state.in_(("running", "queued")))
            .order_by(ArrUpscale.state.desc(), ArrUpscale.created_at)  # running vóór queued
        )
    )
    # Er is maar één upscaler: een job die loopt, is van hem (hervatten)
    row = next((r for r in rows if r.state == "running"), None) or (rows[0] if rows else None)
    if row is None:
        return Response(status_code=204)
    node = await session.get(Node, row.node_id)
    if node is None or not node.blob_id or node.deleted_at is not None:
        row.state, row.reason, row.updated_at = "failed", "het origineel is weg", now
        await session.commit()
        return Response(status_code=204)
    row.state = "running"
    row.seen_at = row.updated_at = now
    await session.commit()
    return Claim(
        id=row.id,
        name=node.name,
        size=node.size,
        scale=row.scale,
        segments_done=row.segments_done,
        seconds_left=left,
        vram_mb=conf.vram_mb,
    )


@worker_router.get("/jobs/{job_id}/source")
async def source(job_id: uuid.UUID, conf: Worker, session: Session, settings: Config):
    row = await _job(session, job_id)
    node = await session.get(Node, row.node_id)
    if node is None or not node.blob_id:
        raise fail(404, "not_found", "Het origineel is weg")
    path = BlobStore(settings.blob_dir).path(node.blob_id)
    return FileResponse(path, media_type="application/octet-stream", filename=node.name)


@worker_router.post("/jobs/{job_id}/progress", response_model=WindowOut)
async def progress(job_id: uuid.UUID, body: ProgressIn, conf: Worker, session: Session):
    row = await _job(session, job_id)
    row.segments_done = body.segments_done
    if body.segments_total is not None:
        row.segments_total = body.segments_total
    row.seen_at = row.updated_at = _now()
    await session.commit()
    left = upscale.seconds_left(dt.datetime.now(), conf.start, conf.end)
    return WindowOut(seconds_left=left if conf.enabled else 0)


def result_name(name: str) -> str:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{stem}{SUFFIX}.mkv"[:255]


@worker_router.put("/jobs/{job_id}/result", status_code=204)
async def result(
    job_id: uuid.UUID, request: Request, conf: Worker, session: Session, settings: Config
):
    from app.routers.files import place_file

    row = await _job(session, job_id)
    node = await session.get(Node, row.node_id)
    parent = await session.get(Node, node.parent_id) if node and node.parent_id else None
    owner = await session.get(User, node.owner_id) if node else None
    if node is None or parent is None or owner is None or parent.deleted_at is not None:
        raise fail(404, "not_found", "De map van het origineel is weg")
    blobs = BlobStore(settings.blob_dir)
    await session.commit()
    try:
        tmp, sha, size = await blobs.receive(request.stream(), MAX_RESULT_BYTES)
    except TooLarge as exc:
        raise errors.fail(413, "too_large", "Het resultaat is te groot") from exc
    try:
        placed = await place_file(
            session,
            blobs,
            owner,
            parent,
            result_name(node.name),
            "replace",
            sha,
            size,
            tmp,
            actor=owner,
            tz=settings.timezone,
        )
    finally:
        tmp.unlink(missing_ok=True)
    row = await session.get(ArrUpscale, job_id)
    if row is not None:
        row.state = "done"
        row.result_node_id = placed.id
        row.reason = None
        row.seen_at = row.updated_at = _now()
        await session.commit()
    return Response(status_code=204)


@worker_router.post("/jobs/{job_id}/fail", status_code=204)
async def failed(job_id: uuid.UUID, body: FailIn, conf: Worker, session: Session):
    row = await _job(session, job_id)
    row.state = "failed"
    row.reason = body.reason
    row.seen_at = row.updated_at = _now()
    await session.commit()
    return Response(status_code=204)
