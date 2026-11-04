"""De foto-instellingen van een gebruiker (bouwontwerp fase 6, §3.3 en §3.4). Z0, vast.

Zonder rij in photo_pref gelden de standaarden van PHOTO_PREFS. location "mij": de plek van je
eigen foto's zie alleen jij (Vraag 4); "uit": ook jij niet, en geen plaatsen. faces blijft "uit"
zolang X2 er niet is (Vraag 2).

Z0b (Keuze 55, 66 en 80) zette faces (X2) en beeld (X3) in de API, allebei standaard "uit" per
gebruiker (Vraag 11). beeld heeft nog geen kolom (die komt met 0012, A0): tot dan geeft
photo_prefs() de standaard. faces_forbidden en faces_available rekent de route uit (de schakelaar
van de beheerder en de container analyse, Keuze 59); zonder X2 zijn ze False.

De route GET en PATCH /me/photo-settings is van Z6 (routers/photo_prefs.py).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PhotoPref

PHOTO_PREFS: dict[str, Any] = {
    "location": "mij",
    "memories": True,
    "ocr_photos": False,
    "faces": "uit",
    "beeld": "uit",
}

# Aan of uit, zoals photo_pref het bewaart (gezichten en beeld)
OnOff = Literal["aan", "uit"]


class PhotoSettings(BaseModel):
    """Het antwoord van GET /me/photo-settings."""

    location: Literal["mij", "uit"]
    memories: bool
    ocr_photos: bool
    # Z0b: gezichten (X2) en zoeken op beeld (X3), met een standaard
    faces: OnOff = "uit"
    faces_forbidden: bool = False  # de beheerder verbiedt het (Keuze 64)
    faces_available: bool = False  # de container analyse is er (Keuze 59)
    beeld: OnOff = "uit"


class PhotoSettingsPatch(BaseModel):
    """De body van PATCH /me/photo-settings: alleen wat verandert. faces en beeld: X2 en X3."""

    model_config = ConfigDict(extra="forbid")

    location: Literal["mij", "uit"] | None = None
    memories: bool | None = None
    ocr_photos: bool | None = None
    faces: OnOff | None = None
    beeld: OnOff | None = None


async def photo_prefs(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Alle vijf, met de standaard voor wat er niet is (beeld: altijd, tot 0012)."""
    row = await db.get(PhotoPref, user_id)
    if row is None:
        return dict(PHOTO_PREFS)
    return {
        "location": row.location,
        "memories": bool(row.memories),
        "ocr_photos": bool(row.ocr_photos),
        "faces": row.faces,
        "beeld": PHOTO_PREFS["beeld"],
    }
