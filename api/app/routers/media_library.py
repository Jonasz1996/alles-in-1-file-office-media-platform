"""Bibliotheken en items (M2, Keuze 4–9). Stub van M0: elke route geeft 503 media_off tot M2 hem
vult. De vorm (paden, methoden, bodies, antwoorden, statussen) is die van §2 M2.

GET    /api/v1/media/libraries                 → [Library]
POST   /api/v1/media/libraries                 LibraryIn → 201 Library | 403 media_not_owner
                                               | 409 media_library_nested | 422
                                               media_library_not_folder | 422 media_library_limit
PATCH  /api/v1/media/libraries/{id}            LibraryPatch → 200 Library
DELETE /api/v1/media/libraries/{id}            → 204 (de bestanden blijven)
POST   /api/v1/media/libraries/{id}/scan       → 202
GET    /api/v1/media/home                      → MediaHome
GET    /api/v1/media/libraries/{id}/items      ?type=&sort=&cursor=&limit=60 → CardPage
GET    /api/v1/media/items/{node_id}           → Item
GET    /api/v1/media/shows/{show_id}           → ShowDetail
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.media import errors as media_errors
from app.media.types import (
    ITEMS_PAGE,
    CardPage,
    Item,
    Library,
    LibraryIn,
    LibraryPatch,
    MediaHome,
    ShowDetail,
)
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])


@router.get("/libraries", response_model=list[Library])
async def libraries(user: CurrentUser):
    raise media_errors.off()


@router.post("/libraries", status_code=201, response_model=Library)
async def create_library(body: LibraryIn, user: CurrentUser):
    raise media_errors.off()


@router.patch("/libraries/{library_id}", response_model=Library)
async def update_library(library_id: uuid.UUID, body: LibraryPatch, user: CurrentUser):
    raise media_errors.off()


@router.delete("/libraries/{library_id}", status_code=204)
async def delete_library(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.post("/libraries/{library_id}/scan", status_code=202)
async def scan_library(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/home", response_model=MediaHome)
async def home(user: CurrentUser):
    raise media_errors.off()


@router.get("/libraries/{library_id}/items", response_model=CardPage)
async def library_items(
    library_id: uuid.UUID,
    user: CurrentUser,
    type: Literal["movie", "show", "video"] | None = None,
    sort: Literal["title", "added", "year"] = "title",
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = ITEMS_PAGE,
):
    raise media_errors.off()


@router.get("/items/{node_id}", response_model=Item)
async def item(node_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/shows/{show_id}", response_model=ShowDetail)
async def show(show_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()
