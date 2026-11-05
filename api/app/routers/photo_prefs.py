"""De foto-instellingen van wie ingelogd is (bouwontwerp fase 6, §3.4 en Keuze 44; Z6). De
standaarden en de regels voor gezichten en beeld staan in app/prefs.py.

- GET geeft prefs.photo_settings().
- PATCH neemt alleen wat verandert. Eerst prefs.check_switch(), vóór er iets geschreven wordt,
  met alleen de velden die echt anders worden (z0b.md, Open 5: faces "aan" terwijl het al aan
  staat, is geen verandering en geeft dus ook in de stand off geen 409). Dan schrijven, en daarna
  prefs.switched() in dezelfde transactie: X2 en X3 horen het en zetten hun werk in de rij. Zo
  zet niemand gezichten of beeld aan zonder X2 of X3 en zonder de analyse (review Z0b, veilig V3
  en contract F1 en F4). Een weigering schrijft niets, ook de andere velden niet.
- beeld heeft nog geen kolom in photo_pref (die komt met 0012, A0): tot dan blijft het "uit", en
  "aan" weigert check_switch() zolang X3 zich niet aanmeldt.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from app import prefs
from app.config import Settings, get_settings
from app.db import Session
from app.models import PhotoPref
from app.prefs import PhotoSettings, PhotoSettingsPatch
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])

# De velden met een kolom in photo_pref (beeld nog niet: 0012)
COLUMNS = ("location", "memories", "ocr_photos", "faces")


@router.get("/me/photo-settings")
async def photo_settings(
    user: CurrentUser, session: Session, settings: Annotated[Settings, Depends(get_settings)]
) -> PhotoSettings:
    return await prefs.photo_settings(session, user, analyse_enabled=settings.analyse_enabled)


@router.patch("/me/photo-settings")
async def change_photo_settings(
    body: PhotoSettingsPatch,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> PhotoSettings:
    """Alleen wat verandert. Gezichten of beeld aanzetten: 409 faces_unavailable of
    beeld_not_installed zonder X2, X3 of de analyse; 403 faces_forbidden of 409 beeld_off als de
    beheerder het verbiedt; 403 guest voor een gast."""
    before = await prefs.photo_prefs(session, user.id)
    changes = {
        key: value
        for key, value in body.model_dump(exclude_none=True).items()
        if value != before.get(key)
    }
    patch = PhotoSettingsPatch(**changes)
    await prefs.check_switch(session, user, patch, analyse_enabled=settings.analyse_enabled)
    stored = {key: value for key, value in changes.items() if key in COLUMNS}
    if stored:
        row = await session.get(PhotoPref, user.id)
        if row is None:
            row = PhotoPref(user_id=user.id, **{k: before[k] for k in COLUMNS})
            session.add(row)
        for key, value in stored.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        await session.flush()
    await prefs.switched(session, user, before, patch)
    await session.commit()
    return await prefs.photo_settings(session, user, analyse_enabled=settings.analyse_enabled)
