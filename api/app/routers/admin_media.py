"""Het tabblad Media op /beheer (M10, Keuze 45). Stub van M0: 503 media_off tot M10 hem vult.

GET    /api/v1/admin/media
PUT    /api/v1/admin/media/config
POST   /api/v1/admin/media/bench              → 202
DELETE /api/v1/admin/media/sessions/{id}      → 204
POST   /api/v1/admin/media/retry
POST   /api/v1/admin/media/cache/clear
PUT    /api/v1/admin/media/tmdb
DELETE /api/v1/admin/media/tmdb               → 204
POST   /api/v1/admin/media/tmdb/test
GET    /api/v1/admin/media/import/sources
POST   /api/v1/admin/media/import             → 202
GET    /api/v1/admin/media/loadtest
POST   /api/v1/admin/media/loadtest           → 202
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.media import errors as media_errors
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin/media", tags=["beheer"])


@router.get("")
async def overview(admin: AdminUser):
    raise media_errors.off()


@router.put("/config")
async def config(admin: AdminUser):
    raise media_errors.off()


@router.post("/bench", status_code=202)
async def bench(admin: AdminUser):
    raise media_errors.off()


@router.delete("/sessions/{session_id}", status_code=204)
async def stop_session(session_id: uuid.UUID, admin: AdminUser):
    raise media_errors.off()


@router.post("/retry")
async def retry(admin: AdminUser):
    raise media_errors.off()


@router.post("/cache/clear")
async def clear_cache(admin: AdminUser):
    raise media_errors.off()


@router.put("/tmdb")
async def tmdb_key(admin: AdminUser):
    raise media_errors.off()


@router.delete("/tmdb", status_code=204)
async def tmdb_forget(admin: AdminUser):
    raise media_errors.off()


@router.post("/tmdb/test")
async def tmdb_test(admin: AdminUser):
    raise media_errors.off()


@router.get("/import/sources")
async def import_sources(admin: AdminUser):
    raise media_errors.off()


@router.post("/import", status_code=202)
async def start_import(admin: AdminUser):
    raise media_errors.off()


@router.get("/loadtest")
async def loadtest(admin: AdminUser):
    raise media_errors.off()


@router.post("/loadtest", status_code=202)
async def start_loadtest(admin: AdminUser):
    raise media_errors.off()
