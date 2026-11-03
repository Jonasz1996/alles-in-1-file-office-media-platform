"""De tijdsgrens van een zoekvraag (bouwontwerp fase 6, §3.3; Keuze 9, spike S1). Z0 legt de
vorm vast; Z1 is de eigenaar en bouwt hem.

deadline() zet loop.call_later(ms, raw.interrupt) op de verbinding; na afloop zegt .hit of de
grens geraakt is. De timer is altijd uit vóór de verbinding terug naar de pool gaat. Een vraag
die stopt, geeft OperationalError("interrupted"), die deadline() opvangt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass
class Deadline:
    ms: int
    hit: bool = False


@asynccontextmanager
async def deadline(conn: AsyncConnection, ms: int) -> AsyncIterator[Deadline]:
    raise NotImplementedError("deadline wordt gebouwd in Z1 (spike S1)")
    yield Deadline(ms)  # pragma: no cover - maakt hier een generator van
