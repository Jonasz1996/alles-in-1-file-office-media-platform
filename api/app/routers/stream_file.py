"""Direct play via de sessie (M3, Keuze 21). Stub van M0: de router gaat langs sessions.guard
vóór elke route (regel 6), en elke route geeft dan 503 media_off tot M3 hem vult.

GET|HEAD /api/v1/stream/{sid}/file   Range, If-Range, de sha als ETag; de koppen van een download
                                     (private, max-age=0, must-revalidate). Houdt geen
                                     databaseverbinding vast (regel 8) en stuurt de stukken door
                                     sessions.guarded_body.
DELETE   /api/v1/stream/{sid}        → 204
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.media import errors as media_errors
from app.media import sessions

router = APIRouter(
    prefix="/api/v1/stream", tags=["stream"], dependencies=[Depends(sessions.guarded)]
)

FILE_CACHE = "private, max-age=0, must-revalidate"


@router.get("/{sid}/file")
@router.head("/{sid}/file", include_in_schema=False)  # één operatie in de OpenAPI
async def file(session: sessions.Guarded):
    raise media_errors.off()


@router.delete("/{sid}", status_code=204)
async def close(session: sessions.Guarded):
    raise media_errors.off()
