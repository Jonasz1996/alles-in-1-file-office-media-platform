"""Zoeken op beeld (bouwontwerp fase 6, X3; Keuze 78–82; X3b). De vormen staan in
app/search/types.py, het werk in app/beeld/.

Alleen met de analyse aan (FACTS van golf 6): anders blijft elke route na de inlog (en voor
wissen de rol) 501 not_built("X3"), zoals de stubs van Z0b in 0.8.0.

Zoeken zelf gaat via GET /search?mode=image (routers/search.py); aan- en uitzetten per gebruiker
via PATCH /me/photo-settings, voor iedereen via PATCH /admin/search.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.beeld import admin as beeld_admin
from app.beeld import jobs as beeld_jobs
from app.config import Settings, get_settings
from app.db import Session
from app.routers.stubs import not_built
from app.search.types import BeeldStatus
from app.security import AdminUser, CurrentUser

router = APIRouter(prefix="/api/v1", tags=["zoeken"])


@router.get("/photos/beeld")
async def beeld_status(
    user: CurrentUser, session: Session, settings: Annotated[Settings, Depends(get_settings)]
) -> BeeldStatus:
    """De stand voor wie vraagt; warmt zijn bestand op in de container (Keuze 78), hooguit
    eens per minuut."""
    if beeld_jobs.enabled() is None:
        raise not_built("X3")
    return await beeld_admin.status(session, user, settings)


@router.delete("/admin/search/beeld", status_code=202, tags=["beheer"])
async def wipe_beeld(admin: AdminUser, session: Session) -> None:
    """De beeldindex wissen: alle vectoren, de cache en het geheugen van de container (Keuze
    82). De job beeld.wipe doet het werk."""
    if beeld_jobs.enabled() is None:
        raise not_built("X3")
    await beeld_admin.wipe(session)
    await session.commit()
