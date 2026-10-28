"""Items van een bibliotheek (Keuze 6–9). Stub van M0; M2 bouwt ze."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def apply_metadata(
    session: AsyncSession, node_id: uuid.UUID, fields: dict[str, Any], source: str
) -> None:
    """Metadata op een item zetten; velden in locked blijven staan (Keuze 43). De stub doet
    niets."""
    return None
