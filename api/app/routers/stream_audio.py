"""Eén nummer als fMP4 voor gapless (M9, Keuze 38). Stub van M0: de router gaat langs
sessions.guard (regel 6), en de route geeft dan 503 media_off tot M9 hem vult. Een lange respons:
M9 stuurt hem door sessions.guarded_body (per stuk de toegang, Keuze 21).

GET /api/v1/stream/{sid}/audio.mp4
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.media import errors as media_errors
from app.media import sessions

router = APIRouter(
    prefix="/api/v1/stream", tags=["stream"], dependencies=[Depends(sessions.guarded)]
)


@router.get("/{sid}/audio.mp4")
async def audio(session: sessions.Guarded):
    raise media_errors.off()
