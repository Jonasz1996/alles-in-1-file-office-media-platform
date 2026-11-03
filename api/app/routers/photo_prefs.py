"""De foto-instellingen van wie ingelogd is (bouwontwerp fase 6, §3.4; Z6). Stubs van Z0: 501
not_built. De standaarden staan in app/prefs.py.
"""

from fastapi import APIRouter

from app.prefs import PhotoSettings, PhotoSettingsPatch
from app.routers.stubs import not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])


@router.get("/me/photo-settings")
async def photo_settings(user: CurrentUser) -> PhotoSettings:
    raise not_built("Z6")


@router.patch("/me/photo-settings")
async def change_photo_settings(body: PhotoSettingsPatch, user: CurrentUser) -> PhotoSettings:
    raise not_built("Z6")
