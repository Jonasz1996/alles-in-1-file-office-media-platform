"""Het tabblad Media op /beheer (M10, Keuze 45). Dun: het werk staat in media/admin.py en
media/loadtest.py.

GET    /api/v1/admin/media                    → MediaAdmin
PUT    /api/v1/admin/media/config             MediaConfigIn → MediaConfig
POST   /api/v1/admin/media/bench              → 202 Accepted | 409 media_bench_busy
DELETE /api/v1/admin/media/sessions/{id}      → 204 | 404
POST   /api/v1/admin/media/retry              → {retried}
POST   /api/v1/admin/media/libraries/{id}/scan → 202 (M10: opnieuw scannen, ook van een ander)
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
POST   /api/v1/admin/media/loadtest/play      LoadtestPlayIn → LoadtestPlay | 409
                                              media_probe_pending (de bron wordt gemaakt)

Alleen beheerders. Zonder PLATFORM_MEDIA_ENABLED geeft alles 503 media_off (precies 0.6.0).
Staat media op /beheer uit (MediaConfig.enabled false), dan werken het overzicht, de
instellingen, Stop, opruimen, opnieuw en TMDB gewoon: daar zet de beheerder media weer aan. De
meting, de import en de belastingtest hebben media aan nodig (runtime.require).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app import errors
from app.config import MediaConfig, Settings, get_settings
from app.db import Session
from app.media import admin as media_admin
from app.media import errors as media_errors
from app.media import importer, loadtest, runtime
from app.media.state import media_state
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
from app.security import AdminUser, TokenFamily

router = APIRouter(prefix="/api/v1/admin/media", tags=["beheer"])
media_admin.install()  # de redenen per dag tellen vanaf de eerste sessie

Config = Annotated[Settings, Depends(get_settings)]
SOURCE_RETRY = 5  # s: Retry-After zolang een bron van de belastingtest gemaakt wordt


def on(request: Request) -> None:
    """Media staat aan in de omgeving (PLATFORM_MEDIA_ENABLED); anders 503 media_off."""
    if not media_state(request.app).enabled:
        raise media_errors.off()


@router.get("", response_model=MediaAdmin)
async def overview(admin: AdminUser, request: Request, session: Session, settings: Config):
    on(request)
    return await media_admin.overview(request.app, session, settings)


@router.put("/config", response_model=MediaConfig)
async def config(
    body: MediaConfigIn, admin: AdminUser, request: Request, session: Session, settings: Config
):
    on(request)
    return await media_admin.save_config(request.app, session, settings, body)


@router.post("/bench", status_code=202, response_model=Accepted)
async def bench(admin: AdminUser, request: Request, settings: Config):
    runtime.require(request)
    return Accepted(job=media_admin.start_bench(request.app, settings))


@router.delete("/sessions/{session_id}", status_code=204)
async def stop_session(session_id: uuid.UUID, admin: AdminUser, request: Request):
    on(request)
    if not media_admin.stop_session(session_id):
        raise errors.fail(404, "not_found", "Die sessie is er niet (meer)")
    return Response(status_code=204)


@router.post("/retry")
async def retry(admin: AdminUser, request: Request, session: Session):
    on(request)
    return {"retried": await media_admin.retry_failed(session)}


@router.post("/libraries/{library_id}/scan", status_code=202)
async def rescan_library(
    library_id: uuid.UUID, admin: AdminUser, request: Request, session: Session
):
    """Opnieuw scannen vanaf /beheer, ook de bibliotheek van een ander (M10; de route van M2
    vraagt schrijfrecht)."""
    runtime.require(request)
    if not await media_admin.rescan(session, library_id):
        raise errors.fail(404, "not_found", "Die bibliotheek is er niet (meer)")
    return {"library_id": str(library_id)}


@router.post("/cache/clear", status_code=204)
async def clear_cache(body: CacheClearIn, admin: AdminUser, request: Request, settings: Config):
    on(request)
    await media_admin.clear_cache(settings, body.what)
    return Response(status_code=204)


@router.put("/tmdb", status_code=204)
async def tmdb_key(
    body: TmdbKeyIn, admin: AdminUser, request: Request, session: Session, settings: Config
):
    on(request)
    await media_admin.tmdb_store(session, settings, body.key)
    return Response(status_code=204)


@router.delete("/tmdb", status_code=204)
async def tmdb_forget(admin: AdminUser, request: Request, session: Session, settings: Config):
    on(request)
    await media_admin.tmdb_clear(session, settings)
    return Response(status_code=204)


@router.post("/tmdb/test", status_code=204)
async def tmdb_test(admin: AdminUser, request: Request, session: Session, settings: Config):
    on(request)
    await media_admin.tmdb_test(session, settings)
    return Response(status_code=204)


@router.get("/import/sources", response_model=list[ImportSource])
async def import_sources(admin: AdminUser, request: Request, settings: Config):
    """De mappen onder /data/import, elk met hoeveel er staat en wat de app niet kan lezen of
    niet kan verplaatsen (dan stelt /beheer kopiëren voor)."""
    runtime.require(request)
    names = await importer.sources()
    found = await asyncio.gather(
        *(importer.survey(settings, name, move=True) for name in names), return_exceptions=True
    )
    return [f for f in found if isinstance(f, ImportSource)]


@router.post("/import", status_code=202, response_model=Accepted)
async def start_import(body: ImportIn, admin: AdminUser, request: Request):
    runtime.require(request)
    job = await importer.start(body.source, body.target_id, body.owner_id, move=body.move)
    return Accepted(job=job)


@router.get("/loadtest", response_model=LoadtestView)
async def loadtest_view(admin: AdminUser, request: Request):
    on(request)
    return loadtest.view()


@router.post("/loadtest", status_code=202, response_model=Accepted)
async def start_loadtest(body: LoadtestIn, admin: AdminUser, request: Request, settings: Config):
    runtime.require(request)
    return Accepted(job=loadtest.start(request.app, settings, admin, body))


_making: dict[str, asyncio.Task] = {}


@router.post("/loadtest/play", response_model=loadtest.LoadtestPlay)
async def loadtest_play(
    body: loadtest.LoadtestPlayIn,
    admin: AdminUser,
    family: TokenFamily,
    request: Request,
    settings: Config,
):
    """Een sessie op een bron van de belastingtest, voor de spelers buiten de app (fase c). Is
    de bron er nog niet, dan maakt de app hem op de achtergrond, en is het antwoord 409
    media_probe_pending met Retry-After (de speler vraagt het dan opnieuw)."""
    runtime.require(request)
    if not settings.media_loadtest:
        raise loadtest.loadtest_off()
    if media_admin.transcodes_running() or media_admin.bench_running():
        raise media_errors.bench_busy()  # echte kijkers of een meting: niet erbij
    kind, seed, seconds, height = loadtest.specs(3, body.seconds)[body.source]
    path = loadtest.loadtest_dir(settings) / loadtest.source_name(kind, seed, seconds, height)
    if not path.is_file():
        task = _making.get(path.name)
        if task is None or task.done():
            _making[path.name] = asyncio.ensure_future(
                loadtest.make_source(settings, kind, seed, seconds, height)
            )
        raise media_errors.probe_pending("loadtest", SOURCE_RETRY)
    return await loadtest.play_source(
        request.app, settings, admin, family, loadtest.as_file(path), codec=body.codec,
        short=loadtest.short_run(body.seconds), start_ms=body.start_ms, replace=body.replace,
        player=body.player,
    )  # fmt: skip
