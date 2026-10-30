"""Kijkvoortgang (M7, Keuze 36). Stub van M0: 503 media_off tot M7 hem vult.

PUT  /api/v1/media/progress/{node_id}   ProgressIn → 204 | 429 rate_limited | 404 not_found
POST /api/v1/media/progress/batch       ProgressBatchIn → 200 ProgressBatchOut {updated}
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.media.types import ProgressBatchIn, ProgressBatchOut, ProgressIn
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media/progress", tags=["media"])


@router.put("/{node_id}", status_code=204)
async def save(node_id: uuid.UUID, body: ProgressIn, user: CurrentUser):
    raise media_errors.off()


@router.post("/batch", response_model=ProgressBatchOut)
async def batch(body: ProgressBatchIn, user: CurrentUser):
    raise media_errors.off()
