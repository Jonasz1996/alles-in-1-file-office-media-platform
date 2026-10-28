"""De stroom: map, playlists, init en segmenten (M4, Keuze 22–25). Stub van M0: de router gaat
langs sessions.guard vóór elke route (regel 6), en elke route geeft dan 503 media_off tot M4 hem
vult.

GET /api/v1/stream/{sid}/map.json       MediaMap; Cache-Control: no-store
GET /api/v1/stream/{sid}/master.m3u8    application/vnd.apple.mpegurl; no-store
GET /api/v1/stream/{sid}/v.m3u8         no-store
GET /api/v1/stream/{sid}/init.mp4?g=    Cache-Control: private, max-age=3600
GET /api/v1/stream/{sid}/{n}.m4s        X-Init: {gen}; private, max-age=3600
                                        → 503 media_timeout (Retry-After: 2)
                                        | 502 media_transcode_failed
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.media import errors as media_errors
from app.media import sessions
from app.media.types import MediaMap

router = APIRouter(
    prefix="/api/v1/stream", tags=["stream"], dependencies=[Depends(sessions.guarded)]
)

# De koppen van Keuze 35 die M4 zet
NO_STORE = "no-store"
SEGMENT_CACHE = "private, max-age=3600"
PLAYLIST_TYPE = "application/vnd.apple.mpegurl"


@router.get("/{sid}/map.json", response_model=MediaMap)
async def map_json(session: sessions.Guarded):
    raise media_errors.off()


@router.get("/{sid}/master.m3u8")
async def master(session: sessions.Guarded):
    raise media_errors.off()


@router.get("/{sid}/v.m3u8")
async def variant(session: sessions.Guarded):
    raise media_errors.off()


@router.get("/{sid}/init.mp4")
async def init(session: sessions.Guarded, g: Annotated[int, Query(ge=1)] = 1):
    raise media_errors.off()


@router.get("/{sid}/{n}.m4s")
async def segment(session: sessions.Guarded, n: Annotated[int, Path(ge=0, le=1_000_000)]):
    raise media_errors.off()
