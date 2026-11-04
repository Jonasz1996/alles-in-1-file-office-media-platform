"""De tijdsgrens van een zoekvraag (bouwontwerp fase 6, §3.3; Keuze 9, spike S1). Z0 legde de
vorm vast; Z1 bouwt hem.

deadline() zet een timer in de event loop (loop.call_later) die interrupt() aanroept op de
sqlite3-verbinding waar aiosqlite in zijn thread mee werkt; na afloop zegt .hit of de grens
geraakt is. Een vraag die stopt, geeft OperationalError("interrupted"), die deadline() opvangt.

    async with deadline(conn, 120) as d:
        rows = (await conn.execute(...)).all()
    if d.hit:
        ...  # ronde B of C

Wat de spike (S1) en de nacontrole van Z0b (docs/fase-6/z0b.md, Open 11) vonden:

- interrupt() gebeurt in de callback van de timer zelf, niet via de coroutine van aiosqlite:
  die kan pas lopen als de vraag al klaar is en de volgende al draait;
- SQLite negeert interrupt() als er op dat moment geen statement loopt, ook voor het statement
  dat daarna begint. Eén interrupt() op de grens is dus niet genoeg: begint de vraag in de thread
  van aiosqlite pas na de grens (die thread was nog bezig, of de loop was traag), dan stopt hij
  niet te laat maar helemaal niet. Daarom herhaalt de timer interrupt() elke HERHAAL_MS tot het
  blok klaar is: na de grens stopt elke vraag in het blok, wanneer hij ook begint;
- de timer is altijd uit vóór de verbinding verder gaat (finally, in de thread van de loop, dus
  zonder race met de callback). Een interrupt() die net daarvoor afging en geen statement meer
  trof, doet niets met de volgende vraag: SQLite wist de vlag als er een statement begint terwijl
  er geen loopt (V3).

Een tweede vraag van dezelfde gebruiker stopt de eerste (Keuze 13): limits.admit() zet daarvoor
een Stop in STOP; deadline() meldt zich daar aan, en Stop() laat de lopende en elke volgende
grens van die vraag meteen afgaan.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import exc
from sqlalchemy.ext.asyncio import AsyncConnection

# Hoe vaak de timer na de grens opnieuw interrupt() aanroept, tot het blok klaar is
HERHAAL_MS = 1.0


@dataclass
class Deadline:
    ms: int
    hit: bool = False


def sqlite3_of(raw) -> sqlite3.Connection:
    """De sqlite3-verbinding achter een verbinding uit de pool. aiosqlite houdt hem in _conn;
    interrupt() mag vanuit elke thread."""
    return raw.driver_connection._conn


class Timer:
    """interrupt() op één verbinding: eerst op de grens (of meteen met now()), daarna elke
    HERHAAL_MS, tot stop(). Alles in de thread van de event loop."""

    def __init__(self, db: sqlite3.Connection, loop: asyncio.AbstractEventLoop) -> None:
        self._db = db
        self._loop = loop
        self._handle: asyncio.TimerHandle | None = None
        self._uit = False

    def at(self, ms: float) -> None:
        self._handle = self._loop.call_later(ms / 1000, self._fire)

    def now(self) -> None:
        if self._uit:
            return
        if self._handle is not None:
            self._handle.cancel()
        self._fire()

    def _fire(self) -> None:
        if self._uit:
            return
        self._db.interrupt()
        self._handle = self._loop.call_later(HERHAAL_MS / 1000, self._fire)

    def stop(self) -> None:
        self._uit = True
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None


class Stop:
    """Een zoekvraag die van buiten te stoppen is (Keuze 13: een nieuwere vraag van dezelfde
    gebruiker). Na Stop() is .stopped waar, en gaat elke grens van deze vraag meteen af."""

    def __init__(self) -> None:
        self.stopped = False
        self._timers: set[Timer] = set()

    def __call__(self) -> None:
        self.stopped = True
        for t in list(self._timers):
            t.now()

    def add(self, t: Timer) -> None:
        self._timers.add(t)
        if self.stopped:
            t.now()

    def discard(self, t: Timer) -> None:
        self._timers.discard(t)


# De Stop van de vraag die nu loopt (gezet door limits.admit)
STOP: ContextVar[Stop | None] = ContextVar("zoek_stop", default=None)


@asynccontextmanager
async def deadline(conn: AsyncConnection, ms: int) -> AsyncIterator[Deadline]:
    """De grens van ms milliseconden voor alles wat in het blok op conn draait. Een vraag die de
    grens raakt, eindigt het blok zonder fout; .hit zegt dat het gebeurde."""
    raw = await conn.get_raw_connection()
    d = Deadline(ms)
    timer = Timer(sqlite3_of(raw), asyncio.get_running_loop())
    stop = STOP.get()
    timer.at(max(0, ms))
    if stop is not None:
        stop.add(timer)
    try:
        yield d
    except exc.OperationalError as e:
        if "interrupted" not in str(e.orig):
            raise
        d.hit = True
    finally:
        timer.stop()
        if stop is not None:
            stop.discard(timer)
