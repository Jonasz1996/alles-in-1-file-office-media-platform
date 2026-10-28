"""Afspelen via een publieke link (M3, Keuze 34). Stub van M0: 503 media_off tot M3 hem vult.

POST /api/v1/public/media/{node_id}/play   met de sleutel van de link; dezelfde body als play
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.routers.public import CurrentLink

router = APIRouter(prefix="/api/v1/public/media", tags=["publiek"])


@router.post("/{node_id}/play")
async def play(node_id: uuid.UUID, link: CurrentLink):
    raise media_errors.off()
