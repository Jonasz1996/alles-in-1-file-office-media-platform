"""De foto-instellingen van een gebruiker (bouwontwerp fase 6, §3.3 en §3.4). Z0, vast.

Zonder rij in photo_pref gelden de standaarden van PHOTO_PREFS. location "mij": de plek van je
eigen foto's zie alleen jij (Vraag 4); "uit": ook jij niet, en geen plaatsen. faces blijft "uit"
zolang X2 er niet is (Vraag 3); de API toont hem niet.

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
}


class PhotoSettings(BaseModel):
    """Het antwoord van GET /me/photo-settings."""

    location: Literal["mij", "uit"]
    memories: bool
    ocr_photos: bool


class PhotoSettingsPatch(BaseModel):
    """De body van PATCH /me/photo-settings: alleen wat verandert."""

    model_config = ConfigDict(extra="forbid")

    location: Literal["mij", "uit"] | None = None
    memories: bool | None = None
    ocr_photos: bool | None = None


async def photo_prefs(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Alle vier, met de standaard voor wat er niet is."""
    row = await db.get(PhotoPref, user_id)
    if row is None:
        return dict(PHOTO_PREFS)
    return {
        "location": row.location,
        "memories": bool(row.memories),
        "ocr_photos": bool(row.ocr_photos),
        "faces": row.faces,
    }
