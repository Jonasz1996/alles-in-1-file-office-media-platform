"""Afspelen starten (M3, Keuze 19–21). Stub van M0: 503 media_off tot M3 hem vult.

GET  /api/v1/media/{node_id}/codecs   → CodecsOut {candidates, item}
POST /api/v1/media/{node_id}/play     PlayIn → 200 PlayOut
     → 404 not_found | 409 media_probe_pending {reason?: keyframes} | 409 media_unavailable
       | 415 media_unsupported {reason?: too_large} | 422 media_hdr {reason} | 503 media_busy
       {reason} | 503 media_worker_down | 503 media_ffmpeg_missing | 503 media_off
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.media.types import CodecsOut, PlayIn, PlayOut
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])


@router.get("/{node_id}/codecs", response_model=CodecsOut)
async def codecs(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.post("/{node_id}/play", response_model=PlayOut)
async def play(node_id: uuid.UUID, body: PlayIn, user: CurrentUser):
    raise media_errors.off()
