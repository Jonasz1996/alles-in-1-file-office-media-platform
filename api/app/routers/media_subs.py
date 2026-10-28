"""Ondertitels en sprites van een sessie (M5, Keuze 29, 30). Stub van M0: elke route gaat eerst
langs sessions.guard (regel 6), en geeft dan 503 media_off tot M5 hem vult.

GET /api/v1/stream/{sid}/subs/{id}.vtt
GET /api/v1/stream/{sid}/trick.vtt
GET /api/v1/stream/{sid}/trick/{i}.jpg
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.media import errors as media_errors
from app.media import sessions

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])


@router.get("/{sid}/subs/{track}.vtt")
async def subtitle(sid: str, track: str, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()


@router.get("/{sid}/trick.vtt")
async def trick_vtt(sid: str, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()


@router.get("/{sid}/trick/{i}.jpg")
async def trick_jpg(sid: str, i: int, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()
