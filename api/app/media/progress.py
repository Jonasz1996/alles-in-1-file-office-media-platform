"""Kijkvoortgang (Keuze 36). Stub van M0; M7 bouwt hem: tot dan lege rijen."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.media.types import Card

ROWS = ("continue", "next_up", "recent")


async def home_rows(
    session: AsyncSession, user_id: uuid.UUID, library_ids: list[uuid.UUID]
) -> dict[str, list[Card]]:
    return {row: [] for row in ROWS}
