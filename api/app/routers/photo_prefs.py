"""De foto-instellingen van wie ingelogd is (bouwontwerp fase 6, §3.4; Z6). Stubs van Z0: 501
not_built. De standaarden staan in app/prefs.py.

Z6 bouwt GET met prefs.photo_settings(), en PATCH met eerst prefs.check_switch() (vóór er iets
geschreven wordt) en na het schrijven prefs.switched(): zo zet niemand gezichten of beeld aan
zonder X2 of X3 en zonder de analyse (review Z0b, veilig V3 en contract F1 en F4).
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
