"""Koppelen en corrigeren (M8, Keuze 42–44). Stub van M0: 503 media_off tot M8 hem vult.

GET    /api/v1/media/items/{node_id}/candidates?q=&year=
POST   /api/v1/media/items/{node_id}/match
DELETE /api/v1/media/items/{node_id}/match
PATCH  /api/v1/media/items/{node_id}
POST   /api/v1/media/shows/{show_id}/match
GET    /api/v1/media/art/{sha}/{size}.webp?exp=&sig=
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])


@router.get("/items/{node_id}/candidates")
async def candidates(node_id: uuid.UUID, user: CurrentUser, q: str = "", year: int | None = None):
    raise media_errors.off()


@router.post("/items/{node_id}/match")
async def match(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.delete("/items/{node_id}/match")
async def unmatch(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.patch("/items/{node_id}")
async def edit(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.post("/shows/{show_id}/match")
async def match_show(show_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/art/{sha}/{size}.webp")
async def art(sha: str, size: str, exp: int = 0, sig: str = ""):
    raise media_errors.off()
