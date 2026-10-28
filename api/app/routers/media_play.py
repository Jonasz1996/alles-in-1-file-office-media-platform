"""Afspelen starten (M3, Keuze 19–21). Stub van M0: 503 media_off tot M3 hem vult.

GET  /api/v1/media/{node_id}/codecs   → {candidates: [string], item: [string]}
POST /api/v1/media/{node_id}/play     → 200 {sid, mode, reasons[], urls, mime, …}
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])


@router.get("/{node_id}/codecs")
async def codecs(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.post("/{node_id}/play")
async def play(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()
