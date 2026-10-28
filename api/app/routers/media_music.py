"""Albums, artiesten en afspeellijsten (M9, Keuze 37). Stub van M0: 503 media_off tot M9 hem
vult.

GET    /api/v1/media/libraries/{id}/albums
GET    /api/v1/media/albums/{id}
GET    /api/v1/media/libraries/{id}/artists
GET    /api/v1/media/artists/{key}?library=
GET    /api/v1/media/playlists
POST   /api/v1/media/playlists                      → 201
GET    /api/v1/media/playlists/{id}
PATCH  /api/v1/media/playlists/{id}
DELETE /api/v1/media/playlists/{id}                 → 204
POST   /api/v1/media/playlists/{id}/items
PUT    /api/v1/media/playlists/{id}/order           → 204
DELETE /api/v1/media/playlists/{id}/items/{item_id} → 204
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/media", tags=["media"])


@router.get("/libraries/{library_id}/albums")
async def albums(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/albums/{album_id}")
async def album(album_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/libraries/{library_id}/artists")
async def artists(library_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.get("/artists/{key}")
async def artist(key: str, user: CurrentUser, library: uuid.UUID | None = None):
    raise media_errors.off()


@router.get("/playlists")
async def playlists(user: CurrentUser):
    raise media_errors.off()


@router.post("/playlists", status_code=201)
async def create_playlist(user: CurrentUser):
    raise media_errors.off()


@router.get("/playlists/{playlist_id}")
async def playlist(playlist_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.patch("/playlists/{playlist_id}")
async def rename_playlist(playlist_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.delete("/playlists/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.post("/playlists/{playlist_id}/items")
async def add_items(playlist_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.put("/playlists/{playlist_id}/order", status_code=204)
async def reorder(playlist_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()


@router.delete("/playlists/{playlist_id}/items/{item_id}", status_code=204)
async def remove_item(playlist_id: uuid.UUID, item_id: uuid.UUID, user: CurrentUser):
    raise media_errors.off()
