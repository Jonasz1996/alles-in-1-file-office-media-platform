"""De import uit /data/import (Keuze 2), als job media.import in de rij import. Stub van M0; M2
bouwt hem."""

from __future__ import annotations

import uuid

from app.config import Settings
from app.media import errors as media_errors

IMPORT_ROOT = "/data/import"


async def sources() -> list[str]:
    """De mappen onder /data/import die een beheerder kan kiezen."""
    return []


async def start(
    source: str, target_id: uuid.UUID, owner_id: uuid.UUID, *, move: bool = False
) -> uuid.UUID:
    """Zet de import klaar en geeft het id van de job."""
    raise media_errors.off()


async def start_cli(
    settings: Settings, source: str, target: str, owner_email: str, *, move: bool = False
) -> uuid.UUID:
    """Voor app.cli media-import: de doelmap als pad (/Films) en de eigenaar als e-mailadres;
    zet dezelfde import klaar als /beheer. Fouten als die van de API (HTTPException)."""
    raise media_errors.off()
