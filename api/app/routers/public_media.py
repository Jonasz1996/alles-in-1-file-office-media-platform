"""Afspelen via een publieke link (M3, Keuze 34). Stub van M0: 503 media_off tot M3 hem vult.

POST /api/v1/public/media/{node_id}/play   met de sleutel van de link; PlayIn → 200 PlayOut
                                           → 403 media_public_transcode | 429 rate_limited
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.media.types import PlayIn, PlayOut
from app.routers.public import CurrentLink

router = APIRouter(prefix="/api/v1/public/media", tags=["publiek"])


@router.post("/{node_id}/play", response_model=PlayOut)
async def play(node_id: uuid.UUID, body: PlayIn, link: CurrentLink):
    raise media_errors.off()
