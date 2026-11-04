"""Zoeken op beeld (bouwontwerp fase 6, X3; Keuze 78–82). Stubs van Z0b (Keuze 55): 501
not_built("X3"), na de inlog (en voor wissen de rol). De vormen staan in app/search/types.py.

Zoeken zelf gaat via GET /search?mode=image (routers/search.py); aan- en uitzetten per gebruiker
via PATCH /me/photo-settings, voor iedereen via PATCH /admin/search.
"""

from fastapi import APIRouter

from app.routers.stubs import not_built
from app.search.types import BeeldStatus
from app.security import AdminUser, CurrentUser

router = APIRouter(prefix="/api/v1", tags=["zoeken"])


@router.get("/photos/beeld")
async def beeld_status(user: CurrentUser) -> BeeldStatus:
    """De stand voor wie vraagt; warmt zijn bestand op in de container (Keuze 78)."""
    raise not_built("X3")


@router.delete("/admin/search/beeld", status_code=202, tags=["beheer"])
async def wipe_beeld(admin: AdminUser) -> None:
    """De beeldindex wissen: alle vectoren, de cache en het geheugen van de container (Keuze
    82)."""
    raise not_built("X3")
