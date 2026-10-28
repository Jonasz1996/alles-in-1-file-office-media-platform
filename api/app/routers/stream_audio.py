"""Eén nummer als fMP4 voor gapless (M9, Keuze 38). Stub van M0: eerst sessions.guard (regel 6),
dan 503 media_off tot M9 hem vult.

GET /api/v1/stream/{sid}/audio.mp4
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.media import errors as media_errors
from app.media import sessions

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])


@router.get("/{sid}/audio.mp4")
async def audio(sid: str, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()
