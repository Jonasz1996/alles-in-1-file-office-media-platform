"""Bibliotheken en items (M2, Keuze 4–9). Alles met de toegang van Keuze 4 (library.get_seen):
een bibliotheek die je niet ziet, of een item daaruit, is 404. Media uit: 503 media_off.

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

from fastapi import APIRouter, Depends, Query, Request, Response

from app.config import Settings, get_settings
from app.db import Session
from app.media import items as media_items
from app.media import library as media_library
from app.media import progress, runtime, scanner
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

Config = Annotated[Settings, Depends(get_settings)]


@router.get("/libraries", response_model=list[Library])
async def libraries(user: CurrentUser, request: Request, session: Session):
    runtime.require(request)
    found = await media_library.seen(session, user)
    counts = await media_library.counts(session, [s.library.id for s in found])
    return [media_library.view(s, counts.get(s.library.id, 0)) for s in found]


@router.post("/libraries", status_code=201, response_model=Library)
async def create_library(body: LibraryIn, user: CurrentUser, request: Request, session: Session):
    runtime.require(request)
    found = await media_library.create(session, user, body)
    return media_library.view(found, 0)


@router.patch("/libraries/{library_id}", response_model=Library)
async def update_library(
    library_id: uuid.UUID, body: LibraryPatch, user: CurrentUser, request: Request, session: Session
):
    runtime.require(request)
    found = await media_library.update(session, user, library_id, body)
    counts = await media_library.counts(session, [library_id])
    return media_library.view(found, counts.get(library_id, 0))


@router.delete("/libraries/{library_id}", status_code=204)
async def delete_library(
    library_id: uuid.UUID, user: CurrentUser, request: Request, session: Session
):
    runtime.require(request)
    await media_library.delete(session, user, library_id)
    return Response(status_code=204)


@router.post("/libraries/{library_id}/scan", status_code=202)
async def scan_library(
    library_id: uuid.UUID, user: CurrentUser, request: Request, session: Session
):
    """Opnieuw scannen (Keuze 5): een volledige ronde, die ook de namen opnieuw ontleedt. Wie
    mag schrijven."""
    runtime.require(request)
    found = await media_library.get_seen(session, user, library_id, "write")
    await scanner.queue_full(session, found.library.id)
    await session.commit()
    scanner.activate()
    return {"library_id": str(library_id)}


@router.get("/home", response_model=MediaHome)
async def home(user: CurrentUser, request: Request, session: Session, settings: Config):
    """De start: bibliotheken, Verder kijken en Volgende aflevering (M7), en Net toegevoegd. Zolang
    M7 geen recent geeft, komt die rij van hier."""
    runtime.require(request)
    found = await media_library.seen(session, user)
    ids = [s.library.id for s in found]
    counts = await media_library.counts(session, ids)
    rows = await progress.home_rows(session, user.id, ids)
    reader = media_items.Reader(session, settings, user)
    recent = rows.get("recent") or await media_items.recent(reader, ids)
    return MediaHome(
        libraries=[media_library.view(s, counts.get(s.library.id, 0)) for s in found],
        continue_=rows.get("continue") or [],
        next_up=rows.get("next_up") or [],
        recent=recent,
    )


@router.get("/libraries/{library_id}/items", response_model=CardPage)
async def library_items(
    library_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
    type: Literal["movie", "show", "video"] | None = None,
    sort: Literal["title", "added", "year"] = "title",
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = ITEMS_PAGE,
):
    runtime.require(request)
    found = await media_library.get_seen(session, user, library_id)
    reader = media_items.Reader(session, settings, user)
    return await media_items.page(reader, found.library, type, sort, cursor, limit)


@router.get("/items/{node_id}", response_model=Item)
async def item(
    node_id: uuid.UUID, user: CurrentUser, request: Request, session: Session, settings: Config
):
    runtime.require(request)
    return await media_items.detail(media_items.Reader(session, settings, user), node_id)


@router.get("/shows/{show_id}", response_model=ShowDetail)
async def show(
    show_id: uuid.UUID, user: CurrentUser, request: Request, session: Session, settings: Config
):
    runtime.require(request)
    return await media_items.show_detail(media_items.Reader(session, settings, user), show_id)
