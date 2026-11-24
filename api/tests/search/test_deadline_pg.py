"""De grens van een zoekvraag op PostgreSQL (0.8.2; app/search/deadline.py, _pg_deadline).

Een savepoint met SET LOCAL statement_timeout; Stop (Keuze 13: een nieuwere vraag van dezelfde
gebruiker) stopt de vraag met pg_cancel_backend via een andere verbinding (_PgCancel).

Wat hier vastligt, alleen op PostgreSQL (marker pg):
- Stop terwijl een vraag in het blok loopt: de vraag stopt, .hit, en de transactie eromheen
  blijft bruikbaar.
- Een vraag op de grens: .hit, en na het blok geldt de grens niet meer.
- Een pg_cancel_backend die nog onderweg is als het blok klaar is (w3.md, open punt): PostgreSQL
  voert hem toch uit, ook als de taak die hem stuurde geannuleerd is. Kwam hij aan terwijl het
  volgende statement op de verbinding liep, dan stopte hij dat. Nu wacht het blok op een die
  onderweg is; hij komt dan aan terwijl de verbinding op een statement wacht, en dan negeert
  PostgreSQL hem. Lokaal is dat venster kleiner dan een milliseconde (1200 keer Stop vlak voor
  het eind van een blok: nooit geraakt); in een cluster duurt de weg naar de database langer.
  OpWeg maakt het venster 50 ms, zodat de test elke keer hetzelfde doet.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import get_engine
from app.search import deadline as dl
from app.search.deadline import STOP, Stop, deadline

pytestmark = [pytest.mark.anyio, pytest.mark.pg]

ONDERWEG_S = 0.05


class OpWeg:
    """De engine van _PgCancel, met een statement dat ONDERWEG_S onderweg is naar de server: de
    taak die het stuurt annuleren haalt het niet terug (zoals een statement dat al over het
    netwerk ging). Telt wat er aankwam."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.sent = 0

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[OpWeg]:
        yield self

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        async def send() -> None:
            await asyncio.sleep(ONDERWEG_S)
            async with self.engine.connect() as other:
                await other.execute(statement, params)
            self.sent += 1

        await asyncio.shield(asyncio.ensure_future(send()))


@asynccontextmanager
async def stop_van(conn: AsyncConnection) -> AsyncIterator[Stop]:
    """Een Stop voor de vraag op conn, zoals limits.admit hem zet."""
    stop = Stop()
    token = STOP.set(stop)
    try:
        yield stop
    finally:
        STOP.reset(token)


async def test_stop_stopt_de_lopende_vraag_en_de_transactie_blijft(settings):
    async with get_engine(settings).connect() as conn:
        await conn.begin()
        async with stop_van(conn) as stop:
            asyncio.get_running_loop().call_later(0.2, stop)
            async with deadline(conn, 30_000) as d:
                await conn.execute(text("SELECT pg_sleep(20)"))
        assert d.hit
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1


async def test_de_grens_geldt_alleen_in_het_blok(settings):
    async with get_engine(settings).connect() as conn:
        await conn.begin()
        async with deadline(conn, 100) as d:
            await conn.execute(text("SELECT pg_sleep(5)"))
        assert d.hit
        async with deadline(conn, 5_000) as d:
            await conn.execute(text("SELECT 1"))
        assert not d.hit
        assert (await conn.execute(text("SHOW statement_timeout"))).scalar() == "0"
        await conn.execute(text("SELECT pg_sleep(0.2)"))


async def test_een_stop_die_nog_onderweg_is_raakt_het_volgende_statement_niet(
    settings, monkeypatch
):
    made: list[OpWeg] = []
    echte = dl._PgCancel

    def met_vertraging(engine: Any, pid: int) -> Any:
        made.append(OpWeg(engine))
        return echte(made[-1], pid)

    monkeypatch.setattr(dl, "_PgCancel", met_vertraging)
    async with get_engine(settings).connect() as conn:
        await conn.begin()
        async with stop_van(conn) as stop:
            async with deadline(conn, 30_000) as d:
                await conn.execute(text("SELECT 1"))
                stop()  # een nieuwere vraag, net voor het eind van het blok
                await asyncio.sleep(0.01)  # pg_cancel_backend is onderweg
        assert not d.hit
        # Het volgende statement op deze verbinding loopt langer dan de weg van de annulering
        await conn.execute(text("SELECT pg_sleep(0.3)"))
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1
    assert [m.sent for m in made] == [1]
