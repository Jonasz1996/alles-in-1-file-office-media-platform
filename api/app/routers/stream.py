"""De stroom: map, playlists, init en segmenten (M4, Keuze 22–25, 35). De router gaat langs
sessions.guard vóór elke route (regel 6).

GET /api/v1/stream/{sid}/map.json       MediaMap; Cache-Control: no-store
GET /api/v1/stream/{sid}/master.m3u8    application/vnd.apple.mpegurl; no-store
GET /api/v1/stream/{sid}/v.m3u8         no-store
GET /api/v1/stream/{sid}/init.mp4?g=    Cache-Control: private, max-age=3600
                                        → 404 not_found (een generatie die er niet is)
GET /api/v1/stream/{sid}/{n}.m4s        X-Init: {gen}; private, max-age=3600
                                        → 404 not_found (n ≥ het aantal segmenten)
                                        | 503 media_timeout (Retry-After: 2)
                                        | 502 media_transcode_failed

Elk wachten (een segment, een init) gaat via runtime.until_stopped (F3): stopt de app, dan 503
media_busy (worker) met Retry-After: 2. Geen databaseverbinding in deze routes (regel 8): de
guard doet zijn korte controle zelf. Een fout krijgt no-store en CORP van main.StreamHeaders.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from app import errors
from app.media import runtime, sessions, streams
from app.media.types import MediaMap

router = APIRouter(
    prefix="/api/v1/stream", tags=["stream"], dependencies=[Depends(sessions.guarded)]
)

# De koppen van Keuze 35 die M4 zet
NO_STORE = "no-store"
SEGMENT_CACHE = "private, max-age=3600"
PLAYLIST_TYPE = "application/vnd.apple.mpegurl"
MP4 = "video/mp4"


def _not_found():
    return errors.fail(404, "not_found", "Niet gevonden")


def _stream(session: sessions.Session) -> streams.Stream:
    """De stroom van deze sessie; een sessie zonder stroom (direct play) heeft geen kaart."""
    st = streams.get(session)
    if st is None:
        raise _not_found()
    return st


def _playlist(text: str) -> Response:
    return Response(text, media_type=PLAYLIST_TYPE, headers={"Cache-Control": NO_STORE})


@router.get("/{sid}/map.json", response_model=MediaMap)
async def map_json(session: sessions.Guarded, response: Response):
    response.headers["Cache-Control"] = NO_STORE
    return _stream(session).media_map()


@router.get("/{sid}/master.m3u8")
async def master(session: sessions.Guarded):
    return _playlist(_stream(session).master())


@router.get("/{sid}/v.m3u8")
async def variant(session: sessions.Guarded):
    return _playlist(_stream(session).variant_playlist())


@router.get("/{sid}/init.mp4")
async def init(request: Request, session: sessions.Guarded, g: Annotated[int, Query(ge=1)] = 1):
    st = _stream(session)
    data = await runtime.until_stopped(request.app, st.init(g))
    return Response(data, media_type=MP4, headers={"Cache-Control": SEGMENT_CACHE})


@router.get("/{sid}/{n}.m4s")
async def segment(
    request: Request, session: sessions.Guarded, n: Annotated[int, Path(ge=0, le=1_000_000)]
):
    st = _stream(session)
    if n >= st.layout.count:
        raise _not_found()
    gen, data = await runtime.until_stopped(request.app, st.fetch(n))
    return Response(
        data, media_type=MP4, headers={"X-Init": str(gen), "Cache-Control": SEGMENT_CACHE}
    )
