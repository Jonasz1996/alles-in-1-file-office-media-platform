"""Opruimregels op /beheer → Media (fase 11, stap 8; de regels in app.arr.cleanup).

GET  /api/v1/admin/arr/cleanup                → CleanupView (regels en de lijst)
PUT  /api/v1/admin/arr/cleanup                CleanupIn → CleanupView
POST /api/v1/admin/arr/cleanup/run            → CleanupView (nu een ronde: de lijst bijwerken)
POST /api/v1/admin/arr/cleanup/{id}/keep      KeepIn → CleanupView (tegenhouden of weer vrijgeven)
POST /api/v1/admin/arr/cleanup/{id}/now       → CleanupView (meteen naar de prullenbak)
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.arr import cleanup
from app.db import Session
from app.errors import fail
from app.models import ArrCleanup
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin/arr/cleanup", tags=["beheer"])

Days = Annotated[int, Field(ge=7, le=3650)]


class CleanupIn(BaseModel):
    movies: bool = False
    movie_days: Days = 90
    shows: bool = False
    show_days: Days = 365
    grace_days: Annotated[int, Field(ge=1, le=90)] = 7
    watched_only: bool = True


class CleanupItem(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    due_at: dt.datetime
    kept: bool
    done_at: dt.datetime | None = None


class CleanupView(CleanupIn):
    items: list[CleanupItem] = Field(default_factory=list)


async def _view(session) -> CleanupView:
    conf = await cleanup.load(session)
    rows = await session.scalars(
        select(ArrCleanup).order_by(
            ArrCleanup.done_at.is_not(None), ArrCleanup.kept, ArrCleanup.due_at
        )
    )
    return CleanupView(
        movies=conf.movies,
        movie_days=conf.movie_days,
        shows=conf.shows,
        show_days=conf.show_days,
        grace_days=conf.grace_days,
        watched_only=conf.watched_only,
        items=[
            CleanupItem(
                id=r.id,
                kind=r.kind,
                title=r.title,
                due_at=cleanup._aware(r.due_at),
                kept=r.kept,
                done_at=cleanup._aware(r.done_at),
            )
            for r in rows
        ],
    )


async def _row(session, item_id: uuid.UUID) -> ArrCleanup:
    row = await session.get(ArrCleanup, item_id)
    if row is None:
        raise fail(404, "not_found", "Niet gevonden")
    return row


@router.get("", response_model=CleanupView)
async def view(admin: AdminUser, session: Session):
    return await _view(session)


@router.put("", response_model=CleanupView)
async def save(body: CleanupIn, admin: AdminUser, session: Session):
    await cleanup.store(session, cleanup.CleanupConf(**body.model_dump()))
    return await _view(session)


@router.post("/run", response_model=CleanupView)
async def run(admin: AdminUser, session: Session):
    conf = await cleanup.load(session)
    if conf.movies or conf.shows:
        await cleanup.plan(session, conf, dt.datetime.now(dt.UTC))
    return await _view(session)


class KeepIn(BaseModel):
    kept: bool = True


@router.post("/{item_id}/keep", response_model=CleanupView)
async def keep(item_id: uuid.UUID, body: KeepIn, admin: AdminUser, session: Session):
    row = await _row(session, item_id)
    if row.done_at is not None:
        raise fail(409, "conflict", "Ligt al in de prullenbak; zet het daar terug")
    row.kept = body.kept
    if not body.kept:
        conf = await cleanup.load(session)
        row.due_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=conf.grace_days)
    await session.commit()
    return await _view(session)


@router.post("/{item_id}/now", response_model=CleanupView)
async def now(item_id: uuid.UUID, admin: AdminUser, session: Session):
    row = await _row(session, item_id)
    if row.done_at is None:
        await cleanup.remove(session, row, dt.datetime.now(dt.UTC))
    return await _view(session)
