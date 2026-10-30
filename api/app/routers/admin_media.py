"""Het tabblad Media op /beheer (M10, Keuze 45). Stub van M0: 503 media_off tot M10 hem vult.

GET    /api/v1/admin/media                    → MediaAdmin
PUT    /api/v1/admin/media/config             MediaConfigIn → MediaConfig
POST   /api/v1/admin/media/bench              → 202 Accepted | 409 media_bench_busy
DELETE /api/v1/admin/media/sessions/{id}      → 204
POST   /api/v1/admin/media/retry              → {retried}
POST   /api/v1/admin/media/cache/clear        CacheClearIn → 204
PUT    /api/v1/admin/media/tmdb               TmdbKeyIn → 204 | 422 media_provider_auth
DELETE /api/v1/admin/media/tmdb               → 204
POST   /api/v1/admin/media/tmdb/test          → 204 | 422 media_provider_auth | 503
                                              media_provider_unavailable
GET    /api/v1/admin/media/import/sources     → [ImportSource]
POST   /api/v1/admin/media/import             ImportIn → 202 Accepted | 409 media_import_busy
                                              | 422 media_import_path
GET    /api/v1/admin/media/loadtest           → LoadtestView
POST   /api/v1/admin/media/loadtest           LoadtestIn → 202 Accepted | 409 media_bench_busy
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.config import MediaConfig
from app.media import errors as media_errors
from app.media.types import (
    Accepted,
    CacheClearIn,
    ImportIn,
    ImportSource,
    LoadtestIn,
    LoadtestView,
    MediaAdmin,
    MediaConfigIn,
    TmdbKeyIn,
)
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin/media", tags=["beheer"])


@router.get("", response_model=MediaAdmin)
async def overview(admin: AdminUser):
    raise media_errors.off()


@router.put("/config", response_model=MediaConfig)
async def config(body: MediaConfigIn, admin: AdminUser):
    raise media_errors.off()


@router.post("/bench", status_code=202, response_model=Accepted)
async def bench(admin: AdminUser):
    raise media_errors.off()


@router.delete("/sessions/{session_id}", status_code=204)
async def stop_session(session_id: uuid.UUID, admin: AdminUser):
    raise media_errors.off()


@router.post("/retry")
async def retry(admin: AdminUser):
    raise media_errors.off()


@router.post("/cache/clear", status_code=204)
async def clear_cache(body: CacheClearIn, admin: AdminUser):
    raise media_errors.off()


@router.put("/tmdb", status_code=204)
async def tmdb_key(body: TmdbKeyIn, admin: AdminUser):
    raise media_errors.off()


@router.delete("/tmdb", status_code=204)
async def tmdb_forget(admin: AdminUser):
    raise media_errors.off()


@router.post("/tmdb/test", status_code=204)
async def tmdb_test(admin: AdminUser):
    raise media_errors.off()


@router.get("/import/sources", response_model=list[ImportSource])
async def import_sources(admin: AdminUser):
    raise media_errors.off()


@router.post("/import", status_code=202, response_model=Accepted)
async def start_import(body: ImportIn, admin: AdminUser):
    raise media_errors.off()


@router.get("/loadtest", response_model=LoadtestView)
async def loadtest(admin: AdminUser):
    raise media_errors.off()


@router.post("/loadtest", status_code=202, response_model=Accepted)
async def start_loadtest(body: LoadtestIn, admin: AdminUser):
    raise media_errors.off()
