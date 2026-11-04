"""De tijdsgrens van een zoekvraag (bouwontwerp fase 6, Keuze 9; app/search/deadline.py).

Wat hier vastligt:
- een interrupt() die te vroeg komt (vóór de vraag in de thread van aiosqlite begint) stopt de
  vraag toch: deadline() herhaalt hem tot het blok klaar is (docs/fase-6/z0b.md, Open 11). De
  test is deterministisch: de thread van aiosqlite is bezig tot ná de grens, en de vorm met één
  interrupt() uit call_later laat de vraag dan helemaal lopen (dat staat er ook als test);
- een interrupt() die te laat komt, raakt de volgende vraag niet;
- de timer is uit vóór de verbinding terug in de pool gaat, ook na een fout of een annulering;
- een tweede vraag van dezelfde gebruiker stopt de eerste (Stop, Keuze 13), ook tussen twee
  statements door.

De spike-tests van S1 (test_spike_deadline.py) draaien sinds Z1 ook op deze deadline().
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy import exc
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.search import deadline as dl
from app.search.deadline import STOP, Stop, deadline

pytestmark = pytest.mark.anyio

# Tellen in een recursieve CTE: zonder grens ±20 s, en ±0,1–0,5 s
TRAAG = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 100000000) "
    "SELECT count(*) FROM c"
)
HALF = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 1500000) "
    "SELECT count(*) FROM c"
)


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'grens.db'}")
    yield eng
    await eng.dispose()


async def bezet_tot_na_de_grens(conn: AsyncConnection, na_s: float) -> None:
    """Houd de thread van aiosqlite bezig, zonder statement, tot na_s seconden na nu. De grens
    van het blok is eerder gezet en korter, dus gaat (in de volgorde van de timers van de loop)
    altijd eerst af: terwijl er op de verbinding niets loopt."""
    aio = (await conn.get_raw_connection()).driver_connection
    los = threading.Event()
    asyncio.get_running_loop().call_later(na_s, los.set)
    await aio._execute(los.wait, 10)


@dataclass
class EenKeer:
    ms: int
    hit: bool = False


@asynccontextmanager
async def een_interrupt(conn: AsyncConnection, ms: int) -> AsyncIterator[EenKeer]:
    """De vorm van vóór Z1 (spike S1): één interrupt() uit loop.call_later."""
    db = dl.sqlite3_of(await conn.get_raw_connection())
    d = EenKeer(ms)
    handle = asyncio.get_running_loop().call_later(ms / 1000, db.interrupt)
    try:
        yield d
    except exc.OperationalError as e:
        if "interrupted" not in str(e.orig):
            raise
        d.hit = True
    finally:
        handle.cancel()


async def test_een_te_vroege_interrupt_stopt_de_vraag_toch(engine):
    """Open 11 van Z0b: de grens gaat af vóór de vraag in de thread van aiosqlite begint. Met
    één interrupt() liep de vraag dan helemaal (zonder grens); deadline() stopt hem toch."""
    async with engine.connect() as conn:
        start = time.perf_counter()
        async with deadline(conn, 10) as d:
            await bezet_tot_na_de_grens(conn, 0.05)
            await conn.exec_driver_sql(TRAAG)
        ms = (time.perf_counter() - start) * 1000
        assert d.hit
        assert ms < 1000, ms  # TRAAG zonder grens: ±20 s
        # De verbinding werkt daarna gewoon
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000


async def test_met_een_interrupt_loopt_de_vraag_dan_helemaal(engine):
    """Dezelfde opzet met de vorm van vóór Z1 (één interrupt() op de grens): de vraag stopt
    niet. Zo bewijst de test hierboven iets: zonder herhaling faalt hij."""
    async with engine.connect() as conn:
        async with een_interrupt(conn, 10) as d:
            await bezet_tot_na_de_grens(conn, 0.05)
            n = (await conn.exec_driver_sql(HALF)).scalar()
        assert not d.hit
        assert n == 1_500_000


async def test_na_de_grens_stopt_elke_vraag_in_het_blok(engine):
    """Ook een tweede vraag in hetzelfde blok, die na de grens begint, stopt meteen."""
    async with engine.connect() as conn:
        async with deadline(conn, 20) as d:
            await asyncio.sleep(0.05)
            start = time.perf_counter()
            await conn.exec_driver_sql(TRAAG)
        assert d.hit
        assert (time.perf_counter() - start) * 1000 < 1000


async def test_binnen_de_grens_raakt_niets(engine):
    async with engine.connect() as conn:
        async with deadline(conn, 5000) as d:
            n = (await conn.exec_driver_sql(HALF)).scalar()
        assert not d.hit
        assert n == 1_500_000


async def test_een_late_interrupt_raakt_de_volgende_vraag_niet(engine):
    """De grens valt net na het eind van het blok: daarna stopt niets meer. En een interrupt()
    zonder lopende vraag (zoals een late van de timer) doet niets met de volgende vraag."""
    async with engine.connect() as conn:
        db = dl.sqlite3_of(await conn.get_raw_connection())
        async with deadline(conn, 30) as d:
            await conn.exec_driver_sql("SELECT 1")
        assert not d.hit
        await asyncio.sleep(0.06)
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000
        db.interrupt()
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000


async def test_de_timer_is_uit_voor_de_verbinding_terug_in_de_pool_gaat(engine):
    """Na een geraakte grens herhaalt de timer interrupt() elke HERHAAL_MS; staat hij na het blok
    nog aan, dan stopt elke volgende vraag. Na het blok op dezelfde verbinding, en na de pool op
    dezelfde verbinding van sqlite3: geen enkele stopt."""
    async with engine.connect() as conn:
        eerst = dl.sqlite3_of(await conn.get_raw_connection())
        async with deadline(conn, 10) as d:
            await conn.exec_driver_sql(TRAAG)
        assert d.hit
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000
    await asyncio.sleep(0.02)
    async with engine.connect() as conn:
        assert dl.sqlite3_of(await conn.get_raw_connection()) is eerst
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000


async def test_de_timer_is_ook_uit_na_een_fout_of_annulering(engine):
    async with engine.connect() as conn:
        with pytest.raises(exc.OperationalError):
            async with deadline(conn, 5):
                await asyncio.sleep(0.02)  # de grens is af en herhaalt
                await conn.exec_driver_sql("SELECT * FROM bestaat_niet")
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000

        async def blok():
            async with deadline(conn, 5):
                await asyncio.sleep(10)

        taak = asyncio.create_task(blok())
        await asyncio.sleep(0.03)
        taak.cancel()
        with pytest.raises(asyncio.CancelledError):
            await taak
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000


async def test_een_andere_fout_gaat_gewoon_door(engine):
    async with engine.connect() as conn:
        with pytest.raises(exc.OperationalError, match="bestaat_niet"):
            async with deadline(conn, 5000):
                await conn.exec_driver_sql("SELECT * FROM bestaat_niet")


async def test_stop_stopt_de_lopende_vraag(engine):
    """Keuze 13: een tweede vraag van dezelfde gebruiker stopt de eerste. Stop() laat de grens
    van het lopende blok meteen afgaan."""
    async with engine.connect() as conn:
        stop = Stop()
        token = STOP.set(stop)
        try:
            asyncio.get_running_loop().call_later(0.03, stop)
            start = time.perf_counter()
            async with deadline(conn, 60_000) as d:
                await conn.exec_driver_sql(TRAAG)
            assert d.hit
            assert (time.perf_counter() - start) * 1000 < 1000
            # Ook een volgende ronde van dezelfde vraag stopt meteen (tussen twee blokken door)
            assert stop.stopped
            async with deadline(conn, 60_000) as d2:
                await conn.exec_driver_sql(TRAAG)
            assert d2.hit
        finally:
            STOP.reset(token)
        # Een vraag zonder die Stop merkt er niets van
        async with deadline(conn, 60_000) as d3:
            assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000
        assert not d3.hit


async def test_stop_tussen_twee_statements_stopt_het_volgende(engine):
    """Stop() terwijl de thread van aiosqlite tussen twee statements zit: het volgende statement
    stopt toch (de interrupt() wordt herhaald, zoals na de grens)."""
    async with engine.connect() as conn:
        stop = Stop()
        token = STOP.set(stop)
        try:
            async with deadline(conn, 60_000) as d:
                asyncio.get_running_loop().call_later(0.01, stop)
                await bezet_tot_na_de_grens(conn, 0.05)
                await conn.exec_driver_sql(TRAAG)
            assert d.hit
        finally:
            STOP.reset(token)


async def test_de_grens_raakt_alleen_de_eigen_verbinding(engine):
    async with engine.connect() as een, engine.connect() as twee:

        async def met_grens():
            async with deadline(een, 20) as d:
                await een.exec_driver_sql(TRAAG)
            return d.hit

        async def zonder():
            return (await twee.exec_driver_sql(HALF)).scalar()

        hit, n = await asyncio.gather(met_grens(), zonder())
    assert hit
    assert n == 1_500_000
