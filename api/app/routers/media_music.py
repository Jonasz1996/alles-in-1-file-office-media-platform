"""Albums, artiesten en afspeellijsten (M9, Keuze 37). Stub van M0: 503 media_off tot M9 hem
vult.

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
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query

from app.media import errors as media_errors
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


@router.get("/libraries/{library_id}/albums", response_model=AlbumPage)
async def albums(
    library_id: uuid.UUID,
    user: CurrentUser,
    sort: Literal["title", "year", "added"] = "title",
    cursor: Annotated[str | None, Query(max_length=500)] = None,
):
    raise media_errors.off()


@router.get("/albums/{album_id}", response_model=AlbumDetail)
async def album(album_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/libraries/{library_id}/artists", response_model=list[Artist])
async def artists(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/artists/{key}", response_model=ArtistDetail)
async def artist(
    key: Annotated[str, Path(max_length=300)],
    user: CurrentUser,
    library: uuid.UUID | None = None,
):
    raise media_errors.off()


@router.get("/playlists", response_model=list[Playlist])
async def playlists(user: CurrentUser):
    raise media_errors.off()


@router.post("/playlists", status_code=201, response_model=Playlist)
async def create_playlist(body: PlaylistIn, user: CurrentUser):
    raise media_errors.off()


@router.get("/playlists/{playlist_id}", response_model=PlaylistDetail)
async def playlist(playlist_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.patch("/playlists/{playlist_id}", response_model=Playlist)
async def rename_playlist(playlist_id: uuid.UUID, body: PlaylistIn, user: CurrentUser):
    raise media_errors.off()


@router.delete("/playlists/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.post("/playlists/{playlist_id}/items", response_model=PlaylistItemsOut)
async def add_items(playlist_id: uuid.UUID, body: PlaylistItemsIn, user: CurrentUser):
    raise media_errors.off()


@router.put("/playlists/{playlist_id}/order", status_code=204)
async def reorder(playlist_id: uuid.UUID, body: PlaylistOrderIn, user: CurrentUser):
    raise media_errors.off()


@router.delete("/playlists/{playlist_id}/items/{item_id}", status_code=204)
async def remove_item(playlist_id: uuid.UUID, item_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()
