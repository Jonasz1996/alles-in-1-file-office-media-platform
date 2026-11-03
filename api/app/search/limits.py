"""Hoeveel zoekvragen tegelijk (bouwontwerp fase 6, §3.3; Keuze 13). Z0 legt de vorm vast; Z1
is de eigenaar en bouwt hem.

admit(user) laat hooguit settings.search_inflight vragen tegelijk door voor de hele app, met
settings.search_queue wachtenden; daarboven 429 search_busy (met Retry-After: 1), en per
gebruiker een grens (429 rate_limited).

    async with admit(user):
        ...
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.models import User


@asynccontextmanager
async def admit(user: User) -> AsyncIterator[None]:
    raise NotImplementedError("admit wordt gebouwd in Z1")
    yield  # pragma: no cover - maakt hier een generator van
