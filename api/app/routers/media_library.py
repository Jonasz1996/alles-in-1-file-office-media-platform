"""Bibliotheken en items (M2, Keuze 4–9). Stub van M0: elke route geeft 503 media_off tot M2 hem
vult. De vorm (paden, methoden, statussen) is die van §2, M2.

GET    /api/v1/media/libraries
POST   /api/v1/media/libraries                 → 201
PATCH  /api/v1/media/libraries/{id}
DELETE /api/v1/media/libraries/{id}            → 204
POST   /api/v1/media/libraries/{id}/scan       → 202
GET    /api/v1/media/home
GET    /api/v1/media/libraries/{id}/items
GET    /api/v1/media/items/{node_id}
GET    /api/v1/media/shows/{show_id}
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])


@router.get("/libraries")
async def libraries(user: CurrentUser):
    raise media_errors.off()


@router.post("/libraries", status_code=201)
async def create_library(user: CurrentUser):
    raise media_errors.off()


@router.patch("/libraries/{library_id}")
async def update_library(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.delete("/libraries/{library_id}", status_code=204)
async def delete_library(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.post("/libraries/{library_id}/scan", status_code=202)
async def scan_library(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/home")
async def home(user: CurrentUser):
    raise media_errors.off()


@router.get("/libraries/{library_id}/items")
async def library_items(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/items/{node_id}")
async def item(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/shows/{show_id}")
async def show(show_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()
