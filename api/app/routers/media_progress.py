"""Kijkvoortgang (M7, Keuze 36).

PUT  /api/v1/media/progress/{node_id}   ProgressIn → 204 | 429 rate_limited | 404 not_found
POST /api/v1/media/progress/batch       ProgressBatchIn → 200 ProgressBatchOut {updated}

Alleen wat de gebruiker nu mag lezen: een bestand (met inhoud) dat hij mag lezen, de eigenaar
niet uit zijn prullenbak (zoals afspelen, media_play.readable_file). Anders 404, ook als hij
het eerder wel mocht. De regels en het geheugen staan in app/media/progress.py; deze router
controleert de toegang, commit zijn sessie (geen transactie open bij het slot van de
gebruiker) en geeft de rest door.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from app import access, errors
from app.config import Settings, get_settings
from app.db import Session
from app.media import items as media_items
from app.media import progress, runtime
from app.media.library import get_seen
from app.media.types import ProgressBatchIn, ProgressBatchOut, ProgressIn
from app.models import MediaItem, MediaShow, Node, User
from app.routers import files
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media/progress", tags=["media"])

Config = Annotated[Settings, Depends(get_settings)]
RETRY_AFTER = "5"  # seconden, de grens op ticks


def not_found() -> HTTPException:
    return errors.fail(404, "not_found", "Niet gevonden")


async def readable(session, user: User, node_id: uuid.UUID) -> Node:
    """Een bestand dat deze gebruiker nu mag lezen, anders 404 (ook bij 403: wie het niet mag
    lezen, hoort niet te weten dat het er is)."""
    try:
        found = await files.get_tree_access(session, user, node_id, "read")
    except HTTPException:
        raise not_found() from None
    node = found.node
    if node.kind != "file" or not node.blob_id:
        raise not_found()
    if found.owner and await access.trashed(session, node.id):
        raise not_found()
    return node


async def readable_many(session, user: User, node_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """De bestanden uit deze lijst die de gebruiker nu mag lezen (de rest valt stil weg)."""
    found = await files.get_tree_accesses(session, user, node_ids, "read")
    ok = {
        node_id: a
        for node_id, a in found.items()
        if isinstance(a, files.Access) and a.node.kind == "file" and a.node.blob_id
    }
    gone = await files.trashed_among(session, [n for n, a in ok.items() if a.owner])
    return [n for n in dict.fromkeys(node_ids) if n in ok and n not in gone]


async def episodes(
    session, settings: Settings, user: User, show_id: uuid.UUID, season: int | None
) -> list[uuid.UUID]:
    """De afleveringen van een serie (en een seizoen) die de gebruiker nu ziet; een serie die
    hij niet ziet: 404."""
    show = await session.get(MediaShow, show_id)
    if show is None:
        raise not_found()
    await get_seen(session, user, show.library_id)  # 404 als hij de bibliotheek niet ziet
    query = select(MediaItem).where(
        MediaItem.show_id == show.id,
        MediaItem.present.is_(True),
        MediaItem.kind == "episode",
    )
    if season is not None:
        query = query.where(MediaItem.season == season)
    rows = list((await session.scalars(query)).all())
    reader = media_items.Reader(session, settings, user)
    return [i.node_id for i in await reader.visible(rows)]


@router.put("/{node_id}", status_code=204)
async def save(
    node_id: uuid.UUID,
    body: ProgressIn,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
):
    runtime.require(request)
    node = await readable(session, user, node_id)
    sha = node.blob_id
    await session.commit()  # geen transactie open bij het slot van de gebruiker
    outcome = await progress.put(settings, user.id, node_id, sha, body)
    if outcome == "rate_limited":
        raise errors.fail(
            429, "rate_limited", "Te veel ticks", headers={"Retry-After": RETRY_AFTER}
        )
    return Response(status_code=204)


@router.post("/batch", response_model=ProgressBatchOut)
async def batch(
    body: ProgressBatchIn,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
):
    runtime.require(request)
    if body.node_ids is not None:
        node_ids = await readable_many(session, user, body.node_ids)
    else:
        assert body.show_id is not None  # ProgressBatchIn: een van beide
        node_ids = await episodes(session, settings, user, body.show_id, body.season)
    await session.commit()
    updated = await progress.batch(settings, user.id, node_ids, body.watched)
    return ProgressBatchOut(updated=updated)
