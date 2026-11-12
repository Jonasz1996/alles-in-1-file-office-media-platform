"""Zoeken en foto's op /beheer (bouwontwerp fase 6, §3.4 en Keuze 51; Z7), alleen voor
beheerders. Het werk zelf staat in app/search/admin.py.

GET    /api/v1/admin/search            → AdminSearch (de stand van Keuze 51)
PATCH  /api/v1/admin/search            AdminSearchPatch → AdminSearch: paused (de rijen index en
                                       ocr), ocr (X1), faces_allowed (X2) en beeld (X3)
POST   /api/v1/admin/search/rebuild    RebuildIn → 202 | 409 search_rebuild_running
GET    /api/v1/admin/search/failed     ?cursor= → FailedPage (sha, stap, code, tijd)
POST   /api/v1/admin/search/retry      → 202: alle mislukte opnieuw in de rij

De delen van X1 (ocr), X2 (faces) en X3 (beeld) komen via app.search.hooks: admin_block() voor
het blok, admin_switch() voor de schakelaar (ADMIN_SWITCHES) en admin_rebuild() voor POST
rebuild met what ocr, faces of beeld. Zonder aanmelding geven die laatste twee 501
not_built("X1"), ("X2") of ("X3") (review Z0b, contract F4); het blok ocr zegt dan "niet
geïnstalleerd", faces en beeld zijn null.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.db import Session
from app.search import admin as search_admin
from app.search.types import AdminSearch, AdminSearchPatch, FailedPage, RebuildIn
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin", tags=["beheer"])
Config = Annotated[Settings, Depends(get_settings)]


@router.get("/search")
async def admin_search(admin: AdminUser, session: Session, settings: Config) -> AdminSearch:
    return await search_admin.state(session, settings)


@router.patch("/search")
async def change_admin_search(
    body: AdminSearchPatch, admin: AdminUser, session: Session, settings: Config
) -> AdminSearch:
    await search_admin.change(session, body)
    return await search_admin.state(session, settings)


@router.post("/search/rebuild", status_code=202)
async def rebuild(body: RebuildIn, admin: AdminUser, session: Session, settings: Config) -> None:
    """409 search_rebuild_running als er al een van dezelfde soort loopt."""
    await search_admin.start_rebuild(session, settings, body.what)


@router.get("/search/failed")
async def failed(
    admin: AdminUser,
    session: Session,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> FailedPage:
    return await search_admin.failed(session, cursor)


@router.post("/search/retry", status_code=202)
async def retry(admin: AdminUser, session: Session) -> None:
    await search_admin.retry(session)
