"""Ondertitels en sprites van een sessie (bouwontwerp fase 5, Keuze 29, 30). M5.

De router gaat langs sessions.guard vóór elke route (regel 6): onbekend of verlopen 410,
ingetrokken 403, en elke keer de toegang van nu (met de cache van 15 s).

GET /api/v1/stream/{sid}/subs/{id}.vtt   text/vtt; 202 + Retry-After: 5 zolang hij gemaakt wordt
                                         id = e<index> | x<i> (plek in de lijst van de sessie)
GET /api/v1/stream/{sid}/trick.vtt       text/vtt; private, max-age=3600
GET /api/v1/stream/{sid}/trick/{i}.jpg   image/jpeg; private, max-age=3600

- `e<index>`: een ingebed tekstspoor van de gepinde inhoud. Staat het nog niet in de cache, dan
  pakt een taak de tekstsporen van die blob uit (subtitles.ensure_embedded); het verzoek wacht
  hooguit FAST_WAIT s en zegt anders 202 met Retry-After: 5. Een beeldspoor (burn) of een spoor
  dat er niet is: 404. Lukte het blijvend niet: 409 media_unavailable met reden subtitle.
- `x<i>`: plek i in de lijst die play vastlegde (sessions.external: 404 erbuiten). Bij elk
  verzoek kijkt de app opnieuw of de kijker dat bestand nog mag lezen, en of de node nog naar de
  vastgelegde inhoud wijst (subtitles.readable); anders 404. De staat in de lijst van de sessie
  gaat mee: pending, dan ready (of failed).
- Elke VTT gaat als text/vtt met X-Content-Type-Options: nosniff. De sprites zijn alleen JPEG's
  van onze eigen encoder (trick.py).

Elk wachten (de database, een taak van de worker) gaat via runtime.until_stopped (F3), en geen
databaseverbinding blijft open terwijl er op de worker gewacht wordt (regel 8).
"""

from __future__ import annotations

import asyncio
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response

from app import errors
from app.media import errors as media_errors
from app.media import runtime, sessions, subtitles, trick
from app.storage import BlobStore

router = APIRouter(
    prefix="/api/v1/stream", tags=["stream"], dependencies=[Depends(sessions.guarded)]
)

VTT_TYPE = "text/vtt; charset=utf-8"
JPEG_TYPE = "image/jpeg"
PENDING_RETRY = 5  # Retry-After bij 202 zolang een ondertitel gemaakt wordt
FAST_WAIT = 2.0  # zo lang wacht een verzoek op een taak die net begon (een los bestand)
CACHED = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}


def not_found():
    return errors.fail(404, **sessions.NOT_FOUND)


async def _read(path: FilePath) -> bytes | None:
    try:
        return await asyncio.to_thread(path.read_bytes)
    except (FileNotFoundError, NotADirectoryError):
        return None


def pending() -> JSONResponse:
    return JSONResponse(
        {"state": "pending"},
        status_code=202,
        headers={
            "Retry-After": str(PENDING_RETRY),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _settle(app, task: asyncio.Task | None) -> None:
    """Even wachten op de taak (hooguit FAST_WAIT); een fout van de worker wordt een API-fout.
    Afbreken van het verzoek (of het stoppen van de app) laat de taak doorlopen."""
    if task is None:
        return
    try:
        await runtime.until_stopped(app, asyncio.wait_for(asyncio.shield(task), FAST_WAIT))
    except TimeoutError:
        pass
    except Exception:
        if not task.done():  # niet de taak zelf (de app stopt): die fout gaat door
            raise
    if task.done() and not task.cancelled() and task.exception() is not None:
        raise media_errors.from_worker(task.exception())  # type: ignore[arg-type]


@router.get("/{sid}/subs/{track}.vtt")
async def subtitle(
    request: Request,
    session: sessions.Guarded,
    track: Annotated[str, Path(pattern=r"^[ex][0-9]{1,3}$")],
):
    settings = session.settings or runtime.settings()
    entry: dict | None = None
    if track.startswith("e"):
        index = int(track[1:])
        probe = session.probe
        stream = next((s for s in probe.subtitles() if s.index == index), None) if probe else None
        if probe is None or stream is None or not subtitles.is_text(stream) or session.path is None:
            raise not_found()
        sha, name = session.sha, track
        state, task = subtitles.ensure_embedded(settings, sha, session.path, probe, index)
    else:
        entry = sessions.external(session, track)
        node = await runtime.until_stopped(
            request.app, subtitles.readable(settings, session, entry)
        )
        kind = subtitles.loose_kind(node.name) if node is not None else None
        if node is None or kind is None:
            raise not_found()
        sha, name = str(entry["sha"]), "file"
        path = BlobStore(settings.blob_dir).path(sha)
        state, task = subtitles.ensure_loose(settings, sha, path, kind)
    if task is not None:
        if entry is not None:
            entry["state"] = "pending"
        await _settle(request.app, task)
        state = subtitles.state(settings, sha, name)
    if entry is not None:
        entry["state"] = state
    if state == "failed":
        raise media_errors.unavailable("subtitle")
    if state == "ready":
        data = await _read(subtitles.cached(settings, sha, name))
        if data is not None:
            return Response(data, media_type=VTT_TYPE, headers=CACHED)
    return pending()


@router.get("/{sid}/trick.vtt")
async def trick_vtt(session: sessions.Guarded):
    settings = session.settings or runtime.settings()
    try:
        data = await _read(trick.vtt_path(settings, session.sha))
    except ValueError:
        data = None
    if data is None:
        raise not_found()
    return Response(data, media_type=VTT_TYPE, headers=CACHED)


@router.get("/{sid}/trick/{i}.jpg")
async def trick_jpg(session: sessions.Guarded, i: Annotated[int, Path(ge=0, le=100_000)]):
    settings = session.settings or runtime.settings()
    try:
        data = await _read(trick.tile_path(settings, session.sha, i))
    except ValueError:
        data = None
    if data is None:
        raise not_found()
    return Response(data, media_type=JPEG_TYPE, headers=CACHED)
