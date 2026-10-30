"""Koppelen en corrigeren (M8, Keuze 42–44). Stub van M0: 503 media_off tot M8 hem vult.

GET    /api/v1/media/items/{node_id}/candidates?q=&year=  → [MatchCandidate]
POST   /api/v1/media/items/{node_id}/match   MatchIn → Item | 409 media_provider_off
                                             | 503 media_provider_unavailable
                                             | 503 media_provider_rate
DELETE /api/v1/media/items/{node_id}/match   → Item (alleen lokaal)
PATCH  /api/v1/media/items/{node_id}         ItemPatch → Item
POST   /api/v1/media/shows/{show_id}/match   MatchIn → ShowDetail
GET    /api/v1/media/art/{sha}/{size}.webp?exp=&sig=   ondertekend adres (12 uur)
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.media import errors as media_errors
from app.media.types import Item, ItemPatch, MatchCandidate, MatchIn, ShowDetail
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])

ART_SIZES = ("w200", "w500", "w1280")


@router.get("/items/{node_id}/candidates", response_model=list[MatchCandidate])
async def candidates(
    node_id: uuid.UUID,
    user: CurrentUser,
    q: Annotated[str, Query(max_length=300)] = "",
    year: Annotated[int | None, Query(ge=1800, le=2200)] = None,
):
    raise media_errors.off()


@router.post("/items/{node_id}/match", response_model=Item)
async def match(node_id: uuid.UUID, body: MatchIn, user: CurrentUser):
    raise media_errors.off()


@router.delete("/items/{node_id}/match", response_model=Item)
async def unmatch(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.patch("/items/{node_id}", response_model=Item)
async def edit(node_id: uuid.UUID, body: ItemPatch, user: CurrentUser):
    raise media_errors.off()


@router.post("/shows/{show_id}/match", response_model=ShowDetail)
async def match_show(show_id: uuid.UUID, body: MatchIn, user: CurrentUser):
    raise media_errors.off()


@router.get("/art/{sha}/{size}.webp")
async def art(
    sha: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    size: Annotated[str, Path(pattern=r"^w[0-9]{3,4}$")],
    exp: int = 0,
    sig: Annotated[str, Query(max_length=200)] = "",
):
    raise media_errors.off()
