"""Albums, artiesten en afspeellijsten (M9, Keuze 37). Media uit: 503 media_off; zonder login 401.

GET    /api/v1/media/libraries/{id}/albums?sort=&cursor=  → AlbumPage
GET    /api/v1/media/albums/{id}                    → AlbumDetail
GET    /api/v1/media/libraries/{id}/artists         → [Artist]
GET    /api/v1/media/artists/{key}?library=         → ArtistDetail
GET    /api/v1/media/playlists                      → [Playlist]
POST   /api/v1/media/playlists                      PlaylistIn → 201 Playlist
GET    /api/v1/media/playlists/{id}                 → PlaylistDetail
PATCH  /api/v1/media/playlists/{id}                 PlaylistIn → Playlist
DELETE /api/v1/media/playlists/{id}                 → 204
POST   /api/v1/media/playlists/{id}/items           PlaylistItemsIn → PlaylistItemsOut
                                                    | 409 media_playlist_full {max}
PUT    /api/v1/media/playlists/{id}/order           PlaylistOrderIn → 204
DELETE /api/v1/media/playlists/{id}/items/{item_id} → 204

Albums en artiesten met de toegang van een bibliotheek (library.get_seen, Keuze 4): wat je niet
ziet, is 404. Afspeellijsten alleen van jezelf, en een nummer dat je niet meer mag lezen, staat
niet in een antwoord (playlists.py). POST items geeft de regels die erbij kwamen.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from app.config import Settings, get_settings
from app.db import Session
from app.media import items as media_items
from app.media import library as media_library
from app.media import music, playlists, runtime
from app.media.types import (
    AlbumDetail,
    AlbumPage,
    Artist,
    ArtistDetail,
    Playlist,
    PlaylistDetail,
    PlaylistIn,
    PlaylistItemsIn,
    PlaylistItemsOut,
    PlaylistOrderIn,
)
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])

Config = Annotated[Settings, Depends(get_settings)]


@router.get("/libraries/{library_id}/albums", response_model=AlbumPage)
async def albums(
    library_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
    sort: Literal["title", "year", "added"] = "title",
    cursor: Annotated[str | None, Query(max_length=500)] = None,
):
    runtime.require(request)
    found = await media_library.get_seen(session, user, library_id)
    if found.library.kind == "music":
        await music.refresh(session, found.library)
    reader = media_items.Reader(session, settings, user)
    return await music.album_page(reader, found.library, sort, cursor)


@router.get("/albums/{album_id}", response_model=AlbumDetail)
async def album(
    album_id: uuid.UUID, user: CurrentUser, request: Request, session: Session, settings: Config
):
    runtime.require(request)
    return await music.album_detail(media_items.Reader(session, settings, user), album_id)


@router.get("/libraries/{library_id}/artists", response_model=list[Artist])
async def artists(library_id: uuid.UUID, user: CurrentUser, request: Request, session: Session):
    runtime.require(request)
    found = await media_library.get_seen(session, user, library_id)
    if found.library.kind == "music":
        await music.refresh(session, found.library)
    return await music.artists(session, found.library)


@router.get("/artists/{key}", response_model=ArtistDetail)
async def artist(
    key: Annotated[str, Path(max_length=300)],
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
    library: uuid.UUID | None = None,
):
    runtime.require(request)
    reader = media_items.Reader(session, settings, user)
    return await music.artist_detail(reader, key, library)


@router.get("/playlists", response_model=list[Playlist])
async def all_playlists(user: CurrentUser, request: Request, session: Session):
    runtime.require(request)
    return await playlists.summaries(session, user)


@router.post("/playlists", status_code=201, response_model=Playlist)
async def create_playlist(body: PlaylistIn, user: CurrentUser, request: Request, session: Session):
    runtime.require(request)
    return await playlists.create(session, user, body.name)


@router.get("/playlists/{playlist_id}", response_model=PlaylistDetail)
async def playlist(playlist_id: uuid.UUID, user: CurrentUser, request: Request, session: Session):
    runtime.require(request)
    return await playlists.detail(session, user, playlist_id)


@router.patch("/playlists/{playlist_id}", response_model=Playlist)
async def rename_playlist(
    playlist_id: uuid.UUID, body: PlaylistIn, user: CurrentUser, request: Request, session: Session
):
    runtime.require(request)
    return await playlists.rename(session, user, playlist_id, body.name)


@router.delete("/playlists/{playlist_id}", status_code=204)
async def delete_playlist(
    playlist_id: uuid.UUID, user: CurrentUser, request: Request, session: Session
):
    runtime.require(request)
    await playlists.remove(session, user, playlist_id)
    return Response(status_code=204)


@router.post("/playlists/{playlist_id}/items", response_model=PlaylistItemsOut)
async def add_items(
    playlist_id: uuid.UUID,
    body: PlaylistItemsIn,
    user: CurrentUser,
    request: Request,
    session: Session,
):
    runtime.require(request)
    return await playlists.add(session, user, playlist_id, body.node_ids, body.position)


@router.put("/playlists/{playlist_id}/order", status_code=204)
async def reorder(
    playlist_id: uuid.UUID,
    body: PlaylistOrderIn,
    user: CurrentUser,
    request: Request,
    session: Session,
):
    runtime.require(request)
    await playlists.reorder(session, user, playlist_id, body.item_ids)
    return Response(status_code=204)


@router.delete("/playlists/{playlist_id}/items/{item_id}", status_code=204)
async def remove_item(
    playlist_id: uuid.UUID,
    item_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Session,
):
    runtime.require(request)
    await playlists.remove_item(session, user, playlist_id, item_id)
    return Response(status_code=204)
