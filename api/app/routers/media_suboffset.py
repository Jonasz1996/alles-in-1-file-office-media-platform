"""De timing van een ondertitel verschuiven en bewaren (fase 11, stap 4).

GET /api/v1/media/sub-offset/{node_id}   → [SubOffsetOut] (van deze gebruiker, voor die video)
PUT /api/v1/media/sub-offset/{node_id}   SubOffsetIn → 204 (offset_ms 0 haalt hem weg) | 404

Per gebruiker, video en ondertitel. Alleen voor een bestand dat de gebruiker nu mag lezen
(zoals de kijkvoortgang, media_progress.readable), anders 404; media uit: 503. Hooguit ±10 minuten.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.db import Session
from app.media import runtime
from app.models import SubOffset
from app.routers.media_progress import readable
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media/sub-offset", tags=["media"])

LIMIT_MS = 10 * 60 * 1000
SubId = Annotated[str, Field(pattern=r"^[ex][0-9]{1,3}$")]


class SubOffsetIn(BaseModel):
    sub_id: SubId
    label: str = Field(max_length=255)
    offset_ms: int = Field(ge=-LIMIT_MS, le=LIMIT_MS)


class SubOffsetOut(BaseModel):
    sub_id: str
    label: str
    offset_ms: int


@router.get("/{node_id}", response_model=list[SubOffsetOut])
async def offsets(node_id: uuid.UUID, user: CurrentUser, request: Request, session: Session):
    runtime.require(request)
    await readable(session, user, node_id)
    rows = await session.scalars(
        select(SubOffset).where(SubOffset.user_id == user.id, SubOffset.node_id == node_id)
    )
    return [SubOffsetOut(sub_id=r.sub_id, label=r.label, offset_ms=r.offset_ms) for r in rows]


@router.put("/{node_id}", status_code=204)
async def save(
    node_id: uuid.UUID, body: SubOffsetIn, user: CurrentUser, request: Request, session: Session
):
    runtime.require(request)
    await readable(session, user, node_id)
    if body.offset_ms == 0:
        await session.execute(
            delete(SubOffset).where(
                SubOffset.user_id == user.id,
                SubOffset.node_id == node_id,
                SubOffset.sub_id == body.sub_id,
            )
        )
    else:
        row = await session.get(SubOffset, (user.id, node_id, body.sub_id))
        if row is None:
            row = SubOffset(user_id=user.id, node_id=node_id, sub_id=body.sub_id)
            session.add(row)
        row.label = body.label
        row.offset_ms = body.offset_ms
        row.updated_at = dt.datetime.now(dt.UTC)
    await session.commit()
    return Response(status_code=204)
