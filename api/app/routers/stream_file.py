"""Direct play via de sessie (M3, Keuze 21). Stub van M0: elke route gaat eerst langs
sessions.guard (regel 6), en geeft dan 503 media_off tot M3 hem vult.

GET|HEAD /api/v1/stream/{sid}/file   Range, If-Range, de sha als ETag
DELETE   /api/v1/stream/{sid}        → 204
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.media import errors as media_errors
from app.media import sessions

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])


@router.api_route("/{sid}/file", methods=["GET", "HEAD"])
async def file(sid: str, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()


@router.delete("/{sid}", status_code=204)
async def close(sid: str, request: Request):
    await sessions.guard(request, sid)
    raise media_errors.off()
