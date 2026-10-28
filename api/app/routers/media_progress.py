"""Kijkvoortgang (M7, Keuze 36). Stub van M0: 503 media_off tot M7 hem vult.

PUT  /api/v1/media/progress/{node_id}   → 204
POST /api/v1/media/progress/batch       → 200 {updated}
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media/progress", tags=["media"])


@router.put("/{node_id}", status_code=204)
async def save(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.post("/batch")
async def batch(user: CurrentUser):
    raise media_errors.off()
