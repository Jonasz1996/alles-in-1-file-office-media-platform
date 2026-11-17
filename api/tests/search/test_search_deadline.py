"""De tijdsgrens van een zoekvraag (bouwontwerp fase 6, Keuze 9; app/search/deadline.py).

Wat hier vastligt:
- een interrupt() die te vroeg komt (vóór de vraag in de thread van aiosqlite begint) stopt de
  vraag toch: deadline() herhaalt hem tot het blok klaar is (docs/fase-6/z0b.md, Open 11). De
  test is deterministisch: de thread van aiosqlite is bezig tot ná de grens, en de vorm met één
  interrupt() uit call_later laat de vraag dan helemaal lopen (dat staat er ook als test);
- een interrupt() die te laat komt, raakt de volgende vraag niet;
- de timer is uit vóór de verbinding terug in de pool gaat, ook na een fout of een annulering;
- een tweede vraag van dezelfde gebruiker stopt de eerste (Stop, Keuze 13), ook tussen twee
  statements door;
- in FtsEngine: een vraag die de grens raakt, geeft ≥ 1 rij als er een zichtbare treffer is in
  de eerste rijen van ronde B of in de namen (ronde C), en niets van een ander.

De spike-tests van S1 (test_spike_deadline.py) draaien sinds Z1 ook op deze deadline().
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy import exc, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app import security
from app.search import deadline as dl
from app.search import engine as engines
from app.search.deadline import STOP, Stop, deadline
from tests.factories import create_user, maker
from tests.test_files import put, root

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
        # De fout komt na de grens, zonder statement: een statement na de grens mag deadline()
        # stoppen ("interrupted" telt dan als de grens, Keuze 9), en SQLite kijkt ook tijdens het
        # parsen naar de vlag. Met SELECT * FROM bestaat_niet was de test zo af en toe rood (de
        # nacontrole van golf 1, "Open na de review" in z1.md). Een echte fout van SQLite die
        # gewoon doorgaat: test_een_andere_fout_gaat_gewoon_door.
        eigen = exc.OperationalError(
            "SELECT * FROM bestaat_niet",
            {},
            sqlite3.OperationalError("no such table: bestaat_niet"),
        )
        with pytest.raises(exc.OperationalError, match="bestaat_niet"):
            async with deadline(conn, 5):
                await asyncio.sleep(0.02)  # de grens is af en herhaalt
                raise eigen
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


async def test_een_annulering_stopt_ook_de_vraag_in_de_thread(engine):
    """N1: de taak wordt geannuleerd (de browser sluit) terwijl een vraag in de thread van
    aiosqlite loopt. SQLAlchemy maakt de verbinding dan ongeldig en sluit hem, maar aiosqlite
    sluit pas na het lopende statement: de vraag liep door (de finally zette alleen de timer uit),
    ±20 s. Nu stopt deadline() hem: de thread is binnen enkele HERHAAL_MS klaar, en een volgende
    vraag uit de pool loopt helemaal."""
    gestart = asyncio.Event()
    aio = None

    async def blok() -> None:
        nonlocal aio
        async with engine.connect() as conn:
            aio = (await conn.get_raw_connection()).driver_connection
            async with deadline(conn, 60_000):
                gestart.set()
                await conn.exec_driver_sql(TRAAG)

    async def vrij() -> None:
        """De thread van aiosqlite doet niets meer: dicht en gestopt, of een stap in zijn rij
        komt meteen aan de beurt."""
        if aio._running:
            await aio._execute(lambda: None)
        else:
            await asyncio.to_thread(aio._thread.join)

    taak = asyncio.create_task(blok())
    await gestart.wait()
    await asyncio.sleep(0.05)  # de vraag loopt in de thread
    begin = time.monotonic()
    taak.cancel()
    with pytest.raises(asyncio.CancelledError):
        await taak
    await asyncio.wait_for(vrij(), 2)
    vrij_ms = (time.monotonic() - begin) * 1000
    assert vrij_ms < 100 * dl.HERHAAL_MS, vrij_ms
    async with engine.connect() as conn:
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000


async def test_na_een_fout_stopt_deadline_ook_wat_nog_in_de_thread_loopt(engine):
    """Verlaat een fout het blok terwijl er nog een statement in de thread van aiosqlite loopt
    (hier een dat niet via SQLAlchemy wacht), dan stopt deadline() ook dat: interrupt() tot de
    thread voorbij dat statement is (stop_after). Daarna loopt een volgende vraag op dezelfde
    verbinding helemaal (de nacontrole van golf 1: zonder stop_after liep het statement door,
    ±12 s, en bleef geen test rood)."""
    async with engine.connect() as conn:
        aio = (await conn.get_raw_connection()).driver_connection
        lopend = None
        with pytest.raises(RuntimeError, match="weg"):
            async with deadline(conn, 60_000):
                lopend = asyncio.ensure_future(aio.execute(TRAAG))
                await asyncio.sleep(0.05)  # het statement loopt in de thread
                raise RuntimeError("weg")
        begin = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            await asyncio.wait_for(lopend, 2)
        klaar_ms = (time.monotonic() - begin) * 1000
        assert klaar_ms < 100 * dl.HERHAAL_MS, klaar_ms
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


# --- De rondes in FtsEngine (Keuze 9) ---------------------------------------------------------
#
# Een vraag die de grens raakt, geeft ≥ 1 rij als er een zichtbare treffer is in de eerste rijen
# van ronde B of in de namen (ronde C). Welke ronde de grens raakt, zet de test zelf: die ronde
# krijgt een vraag die alleen eindigt als ze stopt. De ronde die het moet halen, krijgt een ruime
# grens: het gaat om wat er uit de rondes komt, niet om de tijd (een volle ronde van de garbage
# collector kan 40 ms terecht raken; de echte grenzen: test_spike_deadline.py, zoek_perf).

EINDELOOS = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
    "SELECT x AS id, x AS num, x AS owner_id, '' AS search_name, x AS s FROM c WHERE x < 0"
)


def _eindeloos(*args, **kwargs):
    return text(EINDELOOS)


@pytest.fixture
async def met_een_ander(client, admin, settings) -> dict[str, str]:
    """De beheerder heeft "Jaarverslag 2025.pdf"; een ander heeft "Jaarverslag geheim.pdf",
    dat de beheerder niet mag zien."""
    headers, _ = admin
    await put(client, headers, (await root(client, headers))["folder"]["id"],
              "Jaarverslag 2025.pdf", b"%PDF-1.4\n1")  # fmt: skip
    async with maker(settings)() as s:
        ander = await create_user(s, "ander@example.nl")
        await s.commit()
    kop = {"Authorization": f"Bearer {security.access_token(settings, ander, uuid.uuid4())}"}
    await put(client, kop, (await root(client, kop))["folder"]["id"],
              "Jaarverslag geheim.pdf", b"%PDF-1.4\n2")  # fmt: skip
    return headers


async def _zoek(client, headers, q: str) -> dict:
    r = await client.get("/api/v1/search", params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_ronde_b_geeft_de_zichtbare_treffer(client, met_een_ander, settings, monkeypatch):
    monkeypatch.setattr(settings, "search_deadline_ms", 50)
    monkeypatch.setattr(engines, "ranked_select", _eindeloos)
    monkeypatch.setattr(engines, "B_MS", 10_000)
    begin = time.monotonic()
    page = await _zoek(client, met_een_ander, "jaarverslag")
    assert time.monotonic() - begin < 5
    assert [h["node"]["name"] for h in page["results"]] == ["Jaarverslag 2025.pdf"]
    assert page["partial"] is True and page["ranked"] is False
    assert page["names_only"] is False and page["engine"] == "fts"


@pytest.mark.parametrize("q", ["jaarv", "ja"], ids=["trigram", "kort"])
async def test_ronde_c_geeft_de_zichtbare_naam(client, met_een_ander, settings, monkeypatch, q):
    """A en B raken de grens: dan de namen, binnen de toegang. "ja" heeft geen woord van ≥ 3
    tekens: dan zonder trigram, langs de paden (Keuze 6)."""
    monkeypatch.setattr(settings, "search_deadline_ms", 50)
    monkeypatch.setattr(engines, "ranked_select", _eindeloos)
    monkeypatch.setattr(engines, "stream_select", _eindeloos)
    monkeypatch.setattr(engines, "C_MS", 10_000)
    page = await _zoek(client, met_een_ander, q)
    assert [h["node"]["name"] for h in page["results"]] == ["Jaarverslag 2025.pdf"]
    assert page["partial"] is True and page["ranked"] is False and page["names_only"] is True
    # Niets zichtbaars in de namen: een eerlijke lege pagina
    page = await _zoek(client, met_een_ander, "geheim")
    assert page["results"] == [] and page["partial"] is True and page["names_only"] is True
