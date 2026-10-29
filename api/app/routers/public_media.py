"""Afspelen via een publieke link (M3, Keuze 34).

POST /api/v1/public/media/{node_id}/play   met de sleutel van de link; PlayIn → 200 PlayOut
                                           → 403 media_public_transcode | 429 rate_limited

Zoals /public/download: het bestand ligt in de link (inside), en de link staat lezen toe. Alleen
direct en remux; transcode alleen met public_transcode (dan hooguit 720 en 1 per link, nooit de
laatste plek). 2 sessies per link, play hooguit 30 per minuut per IP. De sessie hoort bij de
link (link_id) met de eigenaar als gebruiker en zonder login; de guard kijkt bij elk verzoek of
de link nog leeft en of het wachtwoord hetzelfde is (de vingerafdruk). Geen voortgang.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app import errors
from app.config import Settings, get_settings
from app.db import Session
from app.media import public as media_public
from app.media import runtime
from app.media.types import PlayIn, PlayOut
from app.models import Node, User
from app.routers import media_play
from app.routers.public import NOT_FOUND, CurrentLink, inside, readable
from app.security import RateLimiter, client_ip, password_print

router = APIRouter(prefix="/api/v1/public/media", tags=["publiek"])

# play hooguit 30 per minuut per IP (Keuze 34)
plays = RateLimiter(limit=media_public.PUBLIC_PLAYS_PER_MINUTE, window_seconds=60)


@router.post("/{node_id}/play", response_model=PlayOut)
async def play(
    node_id: uuid.UUID,
    body: PlayIn,
    link: CurrentLink,
    request: Request,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
):
    runtime.require(request)
    plays.check(client_ip(request))
    readable(link)
    if node_id not in await inside(session, link, [node_id]):
        raise errors.fail(404, **NOT_FOUND)
    node = await session.get(Node, node_id)
    if node is None or node.kind != "file" or not node.blob_id:
        raise errors.fail(404, **NOT_FOUND)
    owner = await session.get(User, link.share.owner_id)
    if owner is None:
        raise errors.fail(404, **NOT_FOUND)
    viewer = media_play.Viewer(
        user=owner,
        family=None,
        link_id=link.share.id,
        link_print=password_print(link.share.password_hash),
    )

    async def within(ids: list[uuid.UUID]):
        return await inside(session, link, ids)

    found = await media_play.gather(request, session, settings, viewer, node, within)
    return await media_play.start(request, session, settings, viewer, body, found)
