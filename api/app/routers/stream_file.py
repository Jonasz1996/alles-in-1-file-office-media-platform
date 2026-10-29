"""Direct play via de sessie (M3, Keuze 21). De router gaat langs sessions.guard vóór elke route
(regel 6).

GET|HEAD /api/v1/stream/{sid}/file   Range, If-Range, de sha als ETag; de koppen van een download
                                     (private, max-age=0, must-revalidate). Houdt geen
                                     databaseverbinding vast (regel 8) en stuurt de stukken door
                                     sessions.guarded_body.
DELETE   /api/v1/stream/{sid}        → 204

Eén bereik per verzoek (wat een <video> vraagt). Meer bereiken, of een kop die niet klopt: het
hele bestand (200), zoals RFC 9110 toestaat. Een bereik buiten het bestand: 416 met
Content-Range: bytes */grootte. If-Range met een andere ETag (of een datum): het hele bestand.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from app import errors
from app.media import sessions

router = APIRouter(
    prefix="/api/v1/stream", tags=["stream"], dependencies=[Depends(sessions.guarded)]
)

FILE_CACHE = "private, max-age=0, must-revalidate"
PIECE = 256 * 1024  # een stuk van de respons; guarded_body kijkt per 4 MB of 5 s
NOT_FOUND = {"message": "Niet gevonden", "code": "not_found"}

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def byte_range(header: str | None, size: int) -> tuple[int, int] | None | bool:
    """Het bereik uit de Range-kop: (begin, eind) met eind erbij, None voor het hele bestand
    (geen kop, meer bereiken, of een kop die niet klopt), of False als het bereik buiten het
    bestand valt (416)."""
    if not header:
        return None
    match = _RANGE.match(header.strip().replace(" ", ""))
    if match is None:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # de laatste N bytes
        n = int(last)
        if n == 0 or size == 0:
            return False
        return max(0, size - n), size - 1
    start = int(first)
    if start >= size:
        return False
    end = min(int(last), size - 1) if last else size - 1
    if end < start:
        return None
    return start, end


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or etag in tags or f"W/{etag}" in tags


async def read_range(path: Path, start: int, end: int) -> AsyncIterator[bytes]:
    """De bytes van start tot en met end, in stukken van 256 KB; het bestand gaat pas open als
    de eerste byte nodig is, en weer dicht als de iterator stopt."""
    remaining = end - start + 1
    async with await anyio.open_file(path, "rb") as f:
        await f.seek(start)
        while remaining > 0:
            chunk = await f.read(min(PIECE, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


@router.get("/{sid}/file")
@router.head("/{sid}/file", include_in_schema=False)  # één operatie in de OpenAPI
async def file(
    request: Request,
    session: sessions.Guarded,
    range_: Annotated[str | None, Header(alias="range")] = None,
    if_range: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
):
    if session.plan.mode != "direct" or session.path is None:
        raise errors.fail(404, **NOT_FOUND)
    etag = f'"{session.sha}"'
    size = session.size
    headers = {"ETag": etag, "Cache-Control": FILE_CACHE, "Accept-Ranges": "bytes"}
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    status = 200
    start, end = 0, size - 1
    if if_range is None or if_range.strip() == etag:
        found = byte_range(range_, size)
        if found is False:
            headers["Content-Range"] = f"bytes */{size}"
            return Response(status_code=416, headers=headers)
        if found:
            start, end = found
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(max(0, end - start + 1))
    media_type = session.mime or "application/octet-stream"
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=media_type)
    body = sessions.guarded_body(session, read_range(session.path, start, end))
    return StreamingResponse(body, status_code=status, headers=headers, media_type=media_type)


@router.delete("/{sid}", status_code=204)
async def close(session: sessions.Guarded):
    sessions.close(session)
    return Response(status_code=204)
