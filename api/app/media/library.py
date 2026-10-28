"""Bibliotheken: een map met een soort erop (Keuze 4). Stub van M0; M2 bouwt ze."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaLibrary

MAX_LIBRARIES = 20


async def visible(session: AsyncSession, user_id: uuid.UUID) -> list[MediaLibrary]:
    """De bibliotheken die deze gebruiker ziet (wie de map mag lezen)."""
    return []
