"""Zoeken en foto's op /beheer (bouwontwerp fase 6, §3.4 en Keuze 51; Z7). Stubs van Z0: 501
not_built, alleen voor beheerders.

Gezichten (X2) en beeld (X3) vult Z7 via app.search.hooks: admin_block() voor AdminSearch.faces
en .beeld, admin_switch() voor faces_allowed en beeld (ADMIN_SWITCHES), admin_rebuild() voor
what faces en beeld. Zonder aanmelding geven die laatste twee 501 not_built("X2") of ("X3")
(review Z0b, contract F4).
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.routers.stubs import not_built
from app.search.types import AdminSearch, AdminSearchPatch, FailedPage, RebuildIn
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin", tags=["beheer"])


@router.get("/search")
async def admin_search(admin: AdminUser) -> AdminSearch:
    raise not_built("Z7")


@router.patch("/search")
async def change_admin_search(body: AdminSearchPatch, admin: AdminUser) -> AdminSearch:
    raise not_built("Z7")


@router.post("/search/rebuild", status_code=202)
async def rebuild(body: RebuildIn, admin: AdminUser) -> None:
    """409 search_rebuild_running als er al een loopt."""
    raise not_built("Z7")


@router.get("/search/failed")
async def failed(
    admin: AdminUser, cursor: Annotated[str | None, Query(max_length=200)] = None
) -> FailedPage:
    raise not_built("Z7")


@router.post("/search/retry", status_code=202)
async def retry(admin: AdminUser) -> None:
    raise not_built("Z7")
