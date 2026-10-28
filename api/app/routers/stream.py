"""De stroom: map, playlists, init en segmenten (M4, Keuze 22–25). Stub van M0: elke route gaat
eerst langs sessions.guard (regel 6), en geeft dan 503 media_off tot M4 hem vult.

GET /api/v1/stream/{sid}/map.json
GET /api/v1/stream/{sid}/master.m3u8
GET /api/v1/stream/{sid}/v.m3u8
GET /api/v1/stream/{sid}/init.mp4?g={gen}
GET /api/v1/stream/{sid}/{n}.m4s
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.media import errors as media_errors
from app.media import sessions

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])


@router.get("/{sid}/map.json")
async def map_json(sid: str, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()


@router.get("/{sid}/master.m3u8")
async def master(sid: str, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()


@router.get("/{sid}/v.m3u8")
async def variant(sid: str, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()


@router.get("/{sid}/init.mp4")
async def init(sid: str, request: Request, g: int = 1):
    await sessions.guard(request, sid)
    raise media_errors.off()


@router.get("/{sid}/{n}.m4s")
async def segment(sid: str, n: int, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()
