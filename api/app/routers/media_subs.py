"""Ondertitels en sprites van een sessie (M5, Keuze 29, 30). Stub van M0: de router gaat langs
sessions.guard vóór elke route (regel 6), en elke route geeft dan 503 media_off tot M5 hem vult.

GET /api/v1/stream/{sid}/subs/{id}.vtt   text/vtt; 202 + Retry-After: 5 zolang hij gemaakt wordt
                                         id = e<index> | x<i> (plek in de lijst van de sessie)
GET /api/v1/stream/{sid}/trick.vtt       text/vtt; private, max-age=3600
GET /api/v1/stream/{sid}/trick/{i}.jpg   image/jpeg; private, max-age=3600
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.media import errors as media_errors
from app.media import sessions

router = APIRouter(
    prefix="/api/v1/stream", tags=["stream"], dependencies=[Depends(sessions.guarded)]
)

VTT_TYPE = "text/vtt; charset=utf-8"
PENDING_RETRY = 5  # Retry-After bij 202 zolang een ondertitel gemaakt wordt


@router.get("/{sid}/subs/{track}.vtt")
async def subtitle(
    session: sessions.Guarded,
    track: Annotated[str, Path(pattern=r"^[ex][0-9]{1,3}$")],
):
    raise media_errors.off()


@router.get("/{sid}/trick.vtt")
async def trick_vtt(session: sessions.Guarded):
    raise media_errors.off()


@router.get("/{sid}/trick/{i}.jpg")
async def trick_jpg(session: sessions.Guarded, i: Annotated[int, Path(ge=0, le=100_000)]):
    raise media_errors.off()
