"""De schrijfpoort van SQLite (app/dbgate.py; de nacontrole van golf 4, docs/fase-6/z7.md).

SQLite heeft één schrijfslot en geen rij: wie wacht, probeert het in de busy handler steeds
opnieuw (tot 100 ms per stap), en een schrijver die het slot vrijgeeft en meteen weer pakt, wint
elke keer dat de wachter slaapt. De poort zet de schrijvers van het proces in een rij (verzoeken
eerst) en geeft hem meteen door.

- De rij zelf (Gate), zonder database en zonder tijd: wie na wie.
- Op een echte engine: wat schrijft, wanneer de poort teruggaat, de checkpointer.
- De volgorde (rood zonder de poort, groen met): een upload gaat vóór de volgende schrijfactie
  van wie het slot had, ook vóór een achtergrondtaak die steeds terugkomt en vóór de pomp van de
  index. Die tests kijken naar de volgorde, niet naar een tijd.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import threading
import time
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import analysis, db, dbgate, jobs
from app.db import get_engine
from app.extract import step as extract_step
from app.routers import files
from app.search import bench, hooks, rebuild
from app.storage import BlobStore
from tests import test_files
from tests.factories import create_user

pytestmark = pytest.mark.anyio


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def kladblok(engine) -> None:
    """Een tabel om mee te schrijven, met de rijen 1 tot en met 4."""
    async with engine.begin() as c:
        await c.exec_driver_sql("CREATE TABLE kladblok (id INTEGER PRIMARY KEY, n INTEGER)")
        await c.exec_driver_sql("INSERT INTO kladblok VALUES (1, 0), (2, 0), (3, 0), (4, 0)")


async def tot(check, seconds: float = 5.0) -> None:
    """Wacht tot check() waar is (een rij is gevuld); geen meting van tijd."""
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "kwam niet"
        await asyncio.sleep(0.005)


async def login(client, email: str) -> dict:
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "lang-genoeg-123"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# --- de rij zelf ---------------------------------------------------------------------------
def drain(gate: dbgate.Gate, names: dict) -> list[str]:
    """Geeft de poort steeds terug en schrijft op wie hem daarna kreeg."""
    order = []
    while gate.holder is not None:
        gate.release(gate.holder)
        if gate.holder is not None:
            order.append(names[gate.holder])
    return order


async def queue(gate: dbgate.Gate, conn, bg: bool) -> asyncio.Task:
    """Zet conn in de rij (zonder taak: de unit-tests geven zelf terug)."""
    n = len(gate.fg) + len(gate.bg)
    task = asyncio.create_task(gate.acquire(conn, None, bg))
    await tot(lambda: len(gate.fg) + len(gate.bg) == n + 1)
    return task


async def test_een_verzoek_gaat_voor_een_achtergrondschrijver_die_er_eerder_was():
    gate = dbgate.Gate(asyncio.get_running_loop())
    h, b, f = object(), object(), object()
    assert gate.try_take(h, None, False)
    tasks = [await queue(gate, b, True), await queue(gate, f, False)]
    assert drain(gate, {b: "achtergrond", f: "verzoek"}) == ["verzoek", "achtergrond"]
    await asyncio.gather(*tasks)


async def test_binnen_een_rij_wie_eerst_komt():
    gate = dbgate.Gate(asyncio.get_running_loop())
    h = object()
    conns = {object(): f"v{i}" for i in range(3)} | {object(): f"a{i}" for i in range(2)}
    assert gate.try_take(h, None, False)
    tasks = [await queue(gate, c, name.startswith("a")) for c, name in conns.items()]
    assert drain(gate, conns) == ["v0", "v1", "v2", "a0", "a1"]
    await asyncio.gather(*tasks)


async def test_na_acht_verzoeken_op_rij_komt_de_achtergrond_aan_de_beurt():
    gate = dbgate.Gate(asyncio.get_running_loop())
    h, b = object(), object()
    fg = {object(): f"v{i}" for i in range(10)}
    assert gate.try_take(h, None, False)
    tasks = [await queue(gate, b, True)]
    tasks += [await queue(gate, c, False) for c in fg]
    order = drain(gate, {b: "achtergrond", **fg})
    assert dbgate.FG_STREAK == 8
    assert order == [f"v{i}" for i in range(8)] + ["achtergrond", "v8", "v9"]
    await asyncio.gather(*tasks)


async def test_na_een_beurt_van_de_achtergrond_telt_de_reeks_opnieuw():
    """Na de beurt van de achtergrond begint de reeks van FG_STREAK weer bij 0: de volgende
    achtergrondschrijver wacht dus weer acht verzoeken, niet één."""
    gate = dbgate.Gate(asyncio.get_running_loop())
    h, b0, b1 = object(), object(), object()
    fg = {object(): f"v{i}" for i in range(9)}
    assert gate.try_take(h, None, False)
    tasks = [await queue(gate, b0, True), await queue(gate, b1, True)]
    tasks += [await queue(gate, c, False) for c in fg]
    order = drain(gate, {b0: "a0", b1: "a1", **fg})
    assert order == [f"v{i}" for i in range(8)] + ["a0", "v8", "a1"]
    await asyncio.gather(*tasks)


async def test_verzoeken_zonder_wachtende_achtergrond_tellen_niet_mee():
    """De reeks telt alleen verzoeken die vóór een wachtende achtergrondschrijver gingen. Tien
    verzoeken op rij zonder achtergrond (via de rij, en via try_take): een verzoek dat daarna
    samen met een achtergrondschrijver wacht, gaat nog steeds voor."""
    names = {}

    async def daarna(gate: dbgate.Gate) -> list[str]:
        b, f = object(), object()
        names.update({b: "achtergrond", f: "verzoek"})
        tasks = [await queue(gate, b, True), await queue(gate, f, False)]
        order = drain(gate, names)
        await asyncio.gather(*tasks)
        return order

    # Via de rij: tien keer doorgegeven, de tiende heeft hem nog
    gate = dbgate.Gate(asyncio.get_running_loop())
    assert gate.try_take(object(), None, False)
    fg = {object(): f"v{i}" for i in range(10)}
    names.update(fg)
    tasks = [await queue(gate, c, False) for c in fg]
    for _ in range(10):
        gate.release(gate.holder)
    assert names[gate.holder] == "v9"
    assert await daarna(gate) == ["verzoek", "achtergrond"]
    await asyncio.gather(*tasks)
    # Via try_take: tien keer meteen genomen
    gate = dbgate.Gate(asyncio.get_running_loop())
    for _ in range(10):
        c = object()
        assert gate.try_take(c, None, False)
        gate.release(c)
    assert gate.try_take(object(), None, False)
    assert await daarna(gate) == ["verzoek", "achtergrond"]


async def houd_slot(lock: dbgate.BoostLock) -> tuple[asyncio.Task, asyncio.Event]:
    """Een taak heeft lock (zoals een upload BlobStore.lock) tot het teken."""
    holds, go = asyncio.Event(), asyncio.Event()

    async def houder():
        async with lock:
            holds.set()
            await go.wait()

    task = asyncio.create_task(houder())
    await holds.wait()
    return task, go


async def test_een_vrije_poort_blijft_vrij_voor_wie_een_blobslot_heeft():
    """De review van de schrijfpoort (F1): uploads staan in de rij van BlobStore.lock, niet in die
    van de poort. Wie op een BoostLock wacht of hem heeft, gaat zo schrijven; zolang er zo iemand
    is, gaat een vrije poort niet naar de achtergrond. Een verzoek mag er dan meteen in, ook met
    een achtergrondschrijver in de rij; daarna weer vrij; pas als niemand meer een slot heeft of
    erop wacht, de achtergrond."""
    gate = dbgate.Gate(asyncio.get_running_loop())
    upload, go = await houd_slot(dbgate.BoostLock())
    b, f1, f2 = object(), object(), object()
    assert not gate.try_take(b, None, True)  # een vrije poort: niet voor de achtergrond
    waiter = await queue(gate, b, True)
    assert gate.holder is None
    assert gate.try_take(f1, None, False)  # het verzoek wel, meteen
    gate.release(f1)
    assert gate.holder is None  # het slot is er nog: weer vrijgehouden
    assert gate.try_take(f2, None, False)
    gate.release(f2)
    assert gate.holder is None
    go.set()
    await upload  # niemand meer met een slot: nu de achtergrond
    assert gate.holder is b
    await waiter
    gate.release(b)
    assert dbgate.stats(gate)["held_back"] == 3  # na de rij, na f1, na f2
    assert dbgate.stats(gate)["hold_expired"] == 0
    assert not dbgate._boosted


async def test_vrijhouden_duurt_hooguit_hold_back(monkeypatch):
    """Schrijft wie het slot heeft niet, dan krijgt de achtergrond de poort na HOLD_BACK, en na
    zijn beurt wordt de poort weer vrijgehouden (opnieuw hooguit HOLD_BACK)."""
    monkeypatch.setattr(dbgate, "HOLD_BACK", 0.05)
    gate = dbgate.Gate(asyncio.get_running_loop())
    upload, go = await houd_slot(dbgate.BoostLock())
    b1, b2 = object(), object()
    waiter = await queue(gate, b1, True)
    await tot(lambda: gate.holder is b1)
    await waiter
    w2 = await queue(gate, b2, True)
    gate.release(b1)
    assert gate.holder is None
    await tot(lambda: gate.holder is b2)
    await w2
    gate.release(b2)
    assert dbgate.stats(gate)["hold_expired"] == 2
    go.set()
    await upload


async def test_vrijhouden_stopt_na_fg_streak_verzoeken():
    """Ook met een slot dat steeds bezet is: na FG_STREAK verzoeken op rij de achtergrond."""
    gate = dbgate.Gate(asyncio.get_running_loop())
    upload, go = await houd_slot(dbgate.BoostLock())
    b = object()
    waiter = await queue(gate, b, True)
    for _ in range(dbgate.FG_STREAK):
        f = object()
        assert gate.try_take(f, None, False)
        gate.release(f)
    assert gate.holder is b
    assert not gate.try_take(object(), None, False)  # nu wacht het verzoek
    await waiter
    gate.release(b)
    go.set()
    await upload


async def test_wie_te_lang_wacht_krijgt_database_is_locked(settings, monkeypatch, caplog):
    """Na TIMEOUT dezelfde fout als na busy_timeout, en de rij is weer leeg. De waarschuwing
    noemt alleen werkwoord, tabel en taak: geen SQL."""
    monkeypatch.setattr(dbgate, "TIMEOUT", 0.2)
    engine = get_engine(settings)
    await kladblok(engine)
    gate = dbgate.gate_for(engine)
    holds, go = asyncio.Event(), asyncio.Event()

    async def houder():
        async with engine.begin() as c:
            await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")
            holds.set()
            await go.wait()

    task = asyncio.create_task(houder(), name="houder")
    await holds.wait()
    with caplog.at_level(logging.WARNING, logger="platform.dbgate"):
        with pytest.raises(OperationalError, match="database is locked"):
            async with engine.begin() as c:
                await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 2")
    assert not gate.fg and not gate.bg
    assert dbgate.stats(gate)["timeouts"] == 1
    go.set()
    await task
    assert gate.holder is None
    [line] = [r.getMessage() for r in caplog.records if r.name == "platform.dbgate"]
    assert "UPDATE kladblok" in line and "houder" in line
    assert "SET" not in line and "n + 1" not in line and "id = " not in line


async def test_wie_afhaakt_slaat_de_rij_over():
    gate = dbgate.Gate(asyncio.get_running_loop())
    h, w1, w2, w3 = object(), object(), object(), object()
    assert gate.try_take(h, None, False)
    t1 = await queue(gate, w1, False)
    t2 = await queue(gate, w2, False)
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1
    gate.release(h)
    assert gate.holder is w2
    # Net de poort gekregen, en dan afgebroken voordat hij verder kon: door naar de volgende
    t3 = await queue(gate, w3, False)
    gate.release(w2)
    assert gate.holder is w3
    await t2
    t3.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t3
    assert gate.holder is None
    assert not gate.fg and not gate.bg


async def test_een_taak_die_stopt_met_de_poort_geeft_hem_door():
    gate = dbgate.Gate(asyncio.get_running_loop())
    c1, c2 = object(), object()
    started, finish = asyncio.Event(), asyncio.Event()

    async def neem():
        assert gate.try_take(c1, asyncio.current_task(), False)
        started.set()
        await finish.wait()  # en stopt zonder terug te geven

    task = asyncio.create_task(neem())
    await started.wait()
    waiter = await queue(gate, c2, False)
    finish.set()
    await task
    await asyncio.wait_for(waiter, 5)
    assert gate.holder is c2
    assert dbgate.stats(gate)["holder_done"] == 1
    gate.release(c2)


async def test_teruggeven_vanuit_een_thread_gaat_via_de_loop():
    gate = dbgate.Gate(asyncio.get_running_loop())
    c1, c2 = object(), object()
    assert gate.try_take(c1, None, False)
    waiter = await queue(gate, c2, False)
    granted_in: list[str] = []
    grant = gate._grant

    def recorded(*args):
        granted_in.append(threading.current_thread().name)
        return grant(*args)

    gate._grant = recorded
    assert await asyncio.to_thread(gate.release, c1)
    await asyncio.wait_for(waiter, 5)
    assert gate.holder is c2
    assert granted_in == [threading.current_thread().name]


async def test_twee_databases_hebben_elk_een_eigen_poort(settings, tmp_path):
    """Een taak die de poort van database A heeft en wacht op een schrijver van database B:
    met één poort voor allebei liep dat vast (tot TIMEOUT)."""
    engine_a = get_engine(settings)
    await kladblok(engine_a)
    engine_b = db._engine(f"sqlite+aiosqlite:///{tmp_path / 'ander.db'}")
    try:
        await kladblok(engine_b)

        async def schrijf_b():
            async with engine_b.begin() as c:
                await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")

        async with engine_a.begin() as c:
            await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")
            assert dbgate.gate_for(engine_a).holder is not None
            await asyncio.wait_for(asyncio.create_task(schrijf_b()), 2)
        assert dbgate.gate_for(engine_a) is not dbgate.gate_for(engine_b)
    finally:
        await engine_b.dispose()


async def test_een_tweede_verbinding_van_dezelfde_taak_gaat_zoals_vroeger(settings):
    """Geen plek in de rij (dat liep vast): de busy handler van SQLite, zoals voor de poort.
    De conftest laat zo'n test falen; hier wordt hij verwacht en opgehaald."""
    engine = get_engine(settings)
    await kladblok(engine)
    gate = dbgate.gate_for(engine)
    dbgate.take_violations()
    async with engine.connect() as a:
        await a.exec_driver_sql("UPDATE kladblok SET n = 1 WHERE id = 1")
        assert gate.holder is not None
        async with engine.connect() as b:
            await b.exec_driver_sql("PRAGMA busy_timeout=50")
            with pytest.raises(OperationalError, match="database is locked"):
                await b.exec_driver_sql("UPDATE kladblok SET n = 2 WHERE id = 2")
            await b.invalidate()
        await a.rollback()
    assert gate.holder is None
    assert dbgate.stats(gate)["waits"] == 0
    found = dbgate.take_violations()
    assert {v[0] for v in found} == {"same_task"}, found
    assert ("same_task", "UPDATE kladblok") in {v[:2] for v in found}


# --- op een echte engine -------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql, writes",
    [
        ("SELECT 1", False),
        ("  -- uitleg\n/* meer uitleg */ select * from node", False),
        ("VALUES (1)", False),
        ("EXPLAIN QUERY PLAN SELECT 1", False),
        ("WITH t AS (SELECT 1 AS updated) SELECT * FROM t", False),
        (rebuild._NEXT, False),
        (rebuild._TREE, True),
        ("with t as (select 1) delete from node where id in t", True),
        ("INSERT INTO job (id) VALUES (1)", True),
        ("/* x */ UPDATE blob SET ref_count = 1", True),
        ("DELETE FROM blob", True),
        ("REPLACE INTO kladblok VALUES (1, 1)", True),
        ("CREATE TABLE x (y)", True),
        ("DROP INDEX ix", True),
        ("ALTER TABLE x ADD y", True),
        ("VACUUM", True),
        ("BEGIN", False),
        ("BEGIN IMMEDIATE", True),
        ("COMMIT", False),
        ("ROLLBACK", False),
        ("SAVEPOINT sa_savepoint_1", False),
        ("RELEASE SAVEPOINT sa_savepoint_1", False),
        ("PRAGMA foreign_keys", False),
        ("PRAGMA table_info(node)", False),
        ("PRAGMA foreign_keys=ON", True),
        ("PRAGMA busy_timeout = 10", True),
        ("PRAGMA wal_checkpoint", False),
        ("PRAGMA wal_checkpoint(PASSIVE)", False),
        ("PRAGMA wal_checkpoint(TRUNCATE)", True),
        ("PRAGMA optimize", True),
    ],
)
def test_wat_schrijft(sql, writes):
    assert dbgate.is_write(sql) is writes


def test_de_log_krijgt_alleen_werkwoord_en_tabel():
    assert dbgate.what("UPDATE blob SET ref_count = ref_count + 1 WHERE sha = ?") == "UPDATE blob"
    assert dbgate.what('INSERT OR IGNORE INTO "user" (id) VALUES (?)') == "INSERT user"
    assert dbgate.what("DELETE FROM fts_node WHERE rowid = 'geheim'") == "DELETE fts_node"
    assert "geheim" not in dbgate.what("UPDATE x SET y = 'geheim'")


async def test_de_poort_gaat_terug_op_elk_pad(settings):
    engine = get_engine(settings)
    await kladblok(engine)
    gate = dbgate.gate_for(engine)
    m = maker(settings)
    update = text("UPDATE kladblok SET n = n + 1 WHERE id = 1")

    # Een schrijfactie houdt hem tot en met de commit
    async with m() as s:
        await s.execute(text("SELECT n FROM kladblok"))
        assert gate.holder is None  # lezen gaat nooit door de poort
        await s.execute(update)
        assert gate.holder is not None
        await s.commit()
        assert gate.holder is None
    # Rollback
    async with m() as s:
        await s.execute(update)
        assert gate.holder is not None
        await s.rollback()
        assert gate.holder is None
    # Een fout in de schrijfactie: de transactie loopt nog, de rollback geeft hem terug
    async with m() as s:
        with pytest.raises(IntegrityError):
            await s.execute(text("INSERT INTO kladblok VALUES (1, 0)"))
        assert gate.holder is not None
        await s.rollback()
        assert gate.holder is None
    # Sluiten zonder commit
    s = m()
    await s.execute(update)
    assert gate.holder is not None
    await s.close()
    assert gate.holder is None
    # In autocommit (pysqlite opent geen transactie voor DDL en WITH ... UPDATE): meteen na het
    # statement, want dan is het slot van SQLite ook al vrij
    async with engine.connect() as c:
        await c.exec_driver_sql("CREATE TABLE tijdelijk (x)")
        assert gate.holder is None
        await c.exec_driver_sql(
            "WITH t(x) AS (SELECT 1) UPDATE kladblok SET n = n + 1 WHERE id IN (SELECT x FROM t)"
        )
        assert gate.holder is None
        await c.rollback()
    # Ongeldig gemaakt
    async with engine.connect() as c:
        await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 2")
        assert gate.holder is not None
        await c.invalidate()
        assert gate.holder is None
    async with m() as s:
        assert (await s.execute(text("SELECT n FROM kladblok WHERE id = 1"))).scalar() == 2


async def test_de_poort_blijft_tot_de_commit_klaar_is(settings):
    """De poort gaat pas terug als de COMMIT van de driver (met zijn fsync) klaar is: anders
    begint de volgende schrijver terwijl SQLite het slot nog heeft, en wacht hij in de busy
    handler."""
    engine = get_engine(settings)
    await kladblok(engine)
    gate = dbgate.gate_for(engine)
    seen: list[bool] = []
    async with engine.connect() as c:
        await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")
        raw = (await c.get_raw_connection()).dbapi_connection
        driver = raw.driver_connection
        commit = driver.commit

        async def recorded():
            seen.append(gate.holder is raw)
            return await commit()

        driver.commit = recorded
        try:
            await c.commit()
        finally:
            del driver.commit
        assert gate.holder is None
    assert seen == [True]


async def test_een_commit_wekt_de_checkpointer(settings, monkeypatch):
    """Na elke geslaagde schrijftransactie heeft de checkpointer werk (wal_autocheckpoint=0:
    anders groeit de WAL zonder eind)."""
    engine = get_engine(settings)
    await kladblok(engine)
    kicks: list[int] = []
    monkeypatch.setattr(dbgate._Checkpointer, "kick", lambda self, loop: kicks.append(1))
    async with engine.begin() as c:
        await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")
    assert kicks == [1]
    async with maker(settings)() as s:
        await s.execute(text("UPDATE kladblok SET n = n + 1 WHERE id = 2"))
        await s.commit()
    assert kicks == [1, 1]
    async with engine.connect() as c:  # alleen lezen: niets
        await c.exec_driver_sql("SELECT n FROM kladblok")
        await c.commit()
    assert kicks == [1, 1]


async def test_een_mislukte_schrijfactie_buiten_een_transactie_geeft_de_poort_terug(settings):
    """DDL draait in autocommit (pysqlite opent er geen transactie voor). Mislukt hij, dan is er
    geen transactie die hem later teruggeeft: handle_error doet het, ook al blijft de
    verbinding open."""
    engine = get_engine(settings)
    await kladblok(engine)
    gate = dbgate.gate_for(engine)
    async with engine.connect() as c:
        with pytest.raises(OperationalError):
            await c.exec_driver_sql("CREATE TABLE kladblok (id INTEGER PRIMARY KEY)")
        assert gate.holder is None
        async with asyncio.timeout(2):
            await asyncio.create_task(schrijf_ergens(engine, 2))


async def test_een_rollback_op_een_open_verbinding_geeft_de_poort_terug(settings):
    """De rollback van de driver geeft de poort terug, ook als de verbinding nog niet terug in
    de pool is (een sessie doet dat wel; engine.connect() niet)."""
    engine = get_engine(settings)
    await kladblok(engine)
    gate = dbgate.gate_for(engine)
    async with engine.connect() as c:
        await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")
        assert gate.holder is not None
        await c.rollback()
        assert gate.holder is None
        async with asyncio.timeout(2):
            await asyncio.create_task(schrijf_ergens(engine, 2))


async def schrijf_ergens(engine, row: int) -> None:
    async with engine.begin() as c:
        await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = ?", (row,))


async def test_een_taak_die_midden_in_een_transactie_stopt(settings, monkeypatch):
    """De review van de schrijfpoort (HANG-1): een taak schrijft op een sessie en stopt, de
    transactie loopt nog (de aanroeper commit later). SQLite heeft het slot dan nog; gaf de
    poort zich bij het einde van de taak over, dan kreeg een ander hem, wachtte die in de busy
    handler op dit slot en deze sessie op de poort: allebei vast tot een time-out. Zonder poort
    gaat dit gewoon."""
    monkeypatch.setattr(dbgate, "TIMEOUT", 1.0)
    engine = get_engine(settings)
    await kladblok(engine)
    gate = dbgate.gate_for(engine)
    s = maker(settings)()
    done: list[str] = []

    async def eerste():
        await s.execute(text("UPDATE kladblok SET n = n + 1 WHERE id = 1"))

    async def ander():
        await schrijf_ergens(engine, 2)
        done.append("ander")

    other = None
    try:
        await asyncio.create_task(eerste(), name="eerste")  # stopt midden in de transactie
        await asyncio.sleep(0.01)  # de callbacks van het einde van die taak
        other = asyncio.create_task(ander())
        await asyncio.sleep(0.1)
        await s.execute(text("UPDATE kladblok SET n = n + 1 WHERE id = 3"))
        done.append("sessie")
        await s.commit()
        await asyncio.wait_for(other, 5)
    finally:
        await s.close()
        if other is not None and not other.done():
            await asyncio.wait({other}, timeout=6)
    assert done == ["sessie", "ander"]
    assert dbgate.stats(gate)["holder_done"] == 1
    assert dbgate.stats(gate)["timeouts"] == 0
    assert gate.holder is None


def test_een_commit_bij_het_afbreken_laat_geen_checkpointer_achter(tmp_path):
    """De review van de schrijfpoort (HANG-2): asyncio.run breekt een taak af die bij het
    afbreken nog schrijft (zoals jobs._stopped). Een nieuwe checkpointer die daar begon, bleef
    hangen in de gesloten loop ("Task was destroyed but it is pending!")."""
    engine = db._engine.__wrapped__(f"sqlite+aiosqlite:///{tmp_path / 'afbreken.db'}")
    loops: list[asyncio.AbstractEventLoop] = []

    async def afbreken():
        loops.append(asyncio.get_running_loop())
        started = asyncio.Event()

        async def slaper():
            try:
                started.set()
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await schrijf_ergens(engine, 1)
                raise

        asyncio.create_task(slaper())
        await started.wait()  # en klaar: asyncio.run breekt slaper af

    asyncio.run(kladblok(engine))
    asyncio.run(engine.dispose())
    asyncio.run(afbreken())
    try:
        ck = engine.sync_engine._dbgate_ckpts.get(loops[0])
        assert ck is None or ck.task is None or ck.task.done(), ck.task
    finally:
        asyncio.run(engine.dispose())


async def test_de_checkpointer_maakt_geen_bestand_en_stopt_als_er_niets_meer_is(
    settings, tmp_path, monkeypatch
):
    """mode=rw: een database die er niet is, wordt geen nieuw leeg bestand. En zonder nieuwe
    commits stopt de checkpointer na CHECKPOINT_IDLE rondes (tot de volgende commit)."""
    import sqlite3

    missing = tmp_path / "er-niet.db"
    with pytest.raises(sqlite3.OperationalError):
        dbgate._checkpoint(str(missing))
    assert not missing.exists()
    monkeypatch.setattr(dbgate, "CHECKPOINT_EVERY", 0.01)
    engine = get_engine(settings)
    await kladblok(engine)
    ck = dbgate._Checkpointer(engine.url.database)
    ck.kick(asyncio.get_running_loop())
    task = ck.task
    await tot(lambda: task.done(), 2)
    assert not ck.dirty


async def test_wie_de_poort_lang_heeft_komt_in_de_log(settings, monkeypatch, caplog):
    """Langer dan SLOW_HOLD: een waarschuwing met werkwoord, tabel en taak, zonder SQL."""
    monkeypatch.setattr(dbgate, "SLOW_HOLD", 0.0)
    engine = get_engine(settings)
    await kladblok(engine)
    with caplog.at_level(logging.WARNING, logger="platform.dbgate"):
        await asyncio.create_task(schrijf_ergens(engine, 1), name="langzaam")
    [line] = [
        r.getMessage()
        for r in caplog.records
        if r.name == "platform.dbgate" and "langzaam" in r.getMessage()
    ]
    assert "UPDATE kladblok" in line and "had de poort" in line
    assert "SET" not in line and "n + 1" not in line


async def test_sqlite_heeft_de_poort_en_geen_autocheckpoint(settings):
    """De WAL krijgt zijn checkpoint niet meer in een COMMIT, terwijl die het slot heeft."""
    engine = get_engine(settings)
    assert getattr(engine.sync_engine.dialect.do_commit, "_dbgate", False)
    assert getattr(engine.sync_engine.dialect.do_rollback, "_dbgate", False)
    assert dbgate.gate_for(engine) is not None
    async with engine.connect() as c:
        assert (await c.exec_driver_sql("PRAGMA wal_autocheckpoint")).scalar() == 0
        assert (await c.exec_driver_sql("PRAGMA busy_timeout")).scalar() == 5000
        assert (await c.exec_driver_sql("PRAGMA synchronous")).scalar() == 2  # FULL, als altijd


async def test_de_checkpointer_doet_het_buiten_de_engine(settings, monkeypatch):
    """Na een commit binnen een paar seconden een PASSIVE checkpoint, op een eigen verbinding:
    niets daarvan komt langs de engine (de poort, de tellers)."""
    engine = get_engine(settings)
    await kladblok(engine)
    loop = asyncio.get_running_loop()
    calls: list[tuple[int, int, int]] = []
    checkpoint = dbgate._checkpoint

    def recorded(path: str):
        result = checkpoint(path)
        loop.call_soon_threadsafe(calls.append, result)
        return result

    monkeypatch.setattr(dbgate, "_checkpoint", recorded)
    seen: list[str] = []

    def voor(conn, cursor, statement, *args):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", voor)
    try:
        async with engine.begin() as c:
            await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")
        n = len(calls)
        await tot(lambda: len(calls) > n, 3 + dbgate.CHECKPOINT_EVERY)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", voor)
    busy, log, done = calls[-1]
    assert busy == 0 and log == done
    assert not any("checkpoint" in s.lower() for s in seen)
    task = engine.sync_engine._dbgate_ckpts[loop].task
    await engine.dispose()  # stopt de checkpointer
    await tot(lambda: task.done(), 2)


# --- de volgorde: rood zonder de poort, groen met ------------------------------------------
async def test_upload_gaat_voor_de_volgende_schrijfactie(settings, client, admin, monkeypatch):
    """Een schrijver heeft het slot, een upload wacht op zijn eerste schrijfactie (UPDATE blob in
    add_ref), de schrijver commit en begint meteen opnieuw. Zonder poort sliep de upload in de
    busy handler (stappen van 100 ms na 0,3 s wachten) en pakte de schrijver het slot binnen een
    ms weer."""
    headers, _ = admin
    top = (await test_files.root(client, headers))["folder"]["id"]
    engine = get_engine(settings)
    await kladblok(engine)
    order: list[str] = []
    asks = asyncio.Event()
    add_ref = files.add_ref

    async def asking(*args, **kwargs):
        asks.set()  # nu vraagt de upload het slot (UPDATE blob)
        return await add_ref(*args, **kwargs)

    def na(conn, cursor, statement, *args):
        if statement.startswith("UPDATE blob"):
            order.append("upload")
        elif statement.startswith("UPDATE kladblok"):
            order.append("schrijver")

    async def schrijver():
        async with engine.begin() as c:
            await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")
            await asks.wait()
            await asyncio.sleep(0.3)
        async with engine.begin() as c:
            await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")

    monkeypatch.setattr(files, "add_ref", asking)
    event.listen(engine.sync_engine, "after_cursor_execute", na)
    try:
        task = asyncio.create_task(schrijver())
        await tot(lambda: order == ["schrijver"])
        r = await test_files.put(client, headers, top, "tussendoor.txt", b"klein")
        await task
    finally:
        event.remove(engine.sync_engine, "after_cursor_execute", na)
    assert r.status_code == 201, r.text
    assert order == ["schrijver", "upload", "schrijver"], order


class _Commits:
    """Per upload: wanneer hij het slot vroeg (add_ref begint, met UPDATE blob) en wanneer zijn
    commit begon (de ConnectionEvents commit van zijn verbinding, vlak voor de COMMIT)."""

    def __init__(self, engine, monkeypatch):
        self.engine = engine.sync_engine
        self.asked: list[float] = []
        self.committed: list[float] = []
        self._asker = None
        add_ref = files.add_ref

        async def asking(session, *args, **kwargs):
            self.asked.append(time.monotonic())
            result = await add_ref(session, *args, **kwargs)
            self._asker = (await session.connection()).sync_connection
            return result

        monkeypatch.setattr(files, "add_ref", asking)

    def _commit(self, conn):
        if conn is self._asker:
            self.committed.append(time.monotonic())
            self._asker = None

    def __enter__(self):
        event.listen(self.engine, "commit", self._commit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "commit", self._commit)

    def overtaken(self, spans: list[tuple[float, float]]) -> list[int]:
        """Per upload: hoeveel spans begonnen nadat hij vroeg en klaar waren vóór zijn commit."""
        return [
            sum(1 for start, end in spans if start > ask and end < commit)
            for ask, commit in zip(self.asked, self.committed, strict=True)
        ]


async def test_een_upload_wacht_niet_achter_een_schrijver_die_steeds_terugkomt(
    settings, client, admin, monkeypatch
):
    """Een taak in de rij index (een Runner: achtergrond) houdt het slot steeds 0,3 s en pakt
    het na zijn commit meteen weer. Zonder poort won de upload alleen als een poging van de
    busy handler in dat gat van een ms viel, en kreeg hij na 5 s "database is locked"."""
    headers, _ = admin
    top = (await test_files.root(client, headers))["folder"]["id"]
    engine = get_engine(settings)
    await kladblok(engine)
    started, stop = asyncio.Event(), asyncio.Event()
    holds: list[tuple[float, float]] = []

    async def hold(settings, payload):
        while not stop.is_set():
            async with maker(settings)() as s:
                await s.execute(text("UPDATE kladblok SET n = n + 1 WHERE id = 1"))
                begin = time.monotonic()  # nu heeft hij het slot
                started.set()
                await asyncio.sleep(0.3)  # een open schrijftransactie: het slot blijft van hem
                await s.commit()
                holds.append((begin, time.monotonic()))

    jobs.register("test.hold", hold, lane="index", timeout=120)
    async with maker(settings)() as s:
        await jobs.enqueue(s, "test.hold", {})
        await s.commit()
    runner = jobs.Runner(settings, "index")
    codes: list[int] = []
    with _Commits(engine, monkeypatch) as commits:
        await runner.start()
        try:
            await asyncio.wait_for(started.wait(), 10)
            for i in range(5):
                r = await test_files.put(client, headers, top, f"klein-{i}.txt", b"klein %d" % i)
                codes.append(r.status_code)
        finally:
            stop.set()
            await runner.stop()
            jobs._kinds.pop("test.hold", None)
    assert codes == [201] * 5, codes
    assert commits.overtaken(holds) == [0] * 5, (commits.asked, commits.committed, holds)
    assert len(holds) >= 3  # de achtergrond kwam ook aan de beurt


async def test_uploads_tegelijk_wachten_niet_achter_de_achtergrond(
    settings, client, admin, monkeypatch
):
    """De review van de schrijfpoort (F1): drie gebruikers uploaden tegelijk terwijl een taak in
    de rij index het slot steeds 0,3 s houdt. Uploads wachten op elkaar in de rij van
    BlobStore.lock, niet in die van de poort. Ging de poort na elke upload naar de achtergrond
    (de rij van verzoeken was leeg), dan wachtte de volgende upload op die hele schrijfactie,
    en de derde op twee. Per upload: hoeveel schrijfacties van de achtergrond vroegen de poort
    pas nadat de upload om zijn slot vroeg, en waren toch klaar vóór zijn commit. Moet 0 zijn.
    HOLD_BACK ruim, zodat de volgorde niet van de klok afhangt."""
    monkeypatch.setattr(dbgate, "HOLD_BACK", 3.0)
    engine = get_engine(settings)
    await kladblok(engine)
    users = []
    async with maker(settings)() as s:
        for i in range(3):
            await create_user(s, f"tegelijk{i}@example.nl", password="lang-genoeg-123")
        await s.commit()
    for i in range(3):
        h = await login(client, f"tegelijk{i}@example.nl")
        users.append((h, (await test_files.root(client, h))["folder"]["id"]))
    asked: dict[asyncio.Task, float] = {}
    committed: dict[asyncio.Task, float] = {}
    user_lock = files.user_lock

    def vraagt(user_id):
        asked.setdefault(asyncio.current_task(), time.monotonic())
        return user_lock(user_id)

    def commit(conn):
        task = asyncio.current_task()
        if task in asked and task not in committed:
            committed[task] = time.monotonic()

    started, stop = asyncio.Event(), asyncio.Event()
    holds: list[tuple[float, float]] = []

    async def hold(settings, payload):
        while not stop.is_set():
            async with maker(settings)() as s:
                vraag = time.monotonic()
                await s.execute(text("UPDATE kladblok SET n = n + 1 WHERE id = 1"))
                started.set()
                await asyncio.sleep(0.3)  # een open schrijftransactie
                await s.commit()
                holds.append((vraag, time.monotonic()))

    jobs.register("test.hold3", hold, lane="index", timeout=120)
    async with maker(settings)() as s:
        await jobs.enqueue(s, "test.hold3", {})
        await s.commit()
    runner = jobs.Runner(settings, "index")
    monkeypatch.setattr(files, "user_lock", vraagt)
    event.listen(engine.sync_engine, "commit", commit)
    try:
        await runner.start()
        await asyncio.wait_for(started.wait(), 10)
        codes = await asyncio.gather(
            *(
                test_files.put(client, h, root, f"tegelijk-{i}.txt", b"tegelijk %d" % i)
                for i, (h, root) in enumerate(users)
            )
        )
        n = len(holds)
        await tot(lambda: len(holds) > n, 10)  # en de achtergrond gaat daarna gewoon door
    finally:
        event.remove(engine.sync_engine, "commit", commit)
        stop.set()
        await runner.stop()
        jobs._kinds.pop("test.hold3", None)
    assert [r.status_code for r in codes] == [201] * 3, [r.text for r in codes]
    assert len(asked) == len(committed) == 3
    overtaken = [
        sum(1 for vraag, end in holds if vraag > asked[t] and end < committed[t]) for t in asked
    ]
    assert overtaken == [0, 0, 0], (overtaken, asked, committed, holds)
    assert dbgate.stats(dbgate.gate_for(engine))["hold_expired"] == 0


async def test_wat_een_runner_doet_is_achtergrond(settings):
    """Een taak van een Runner, wat die taak zelf start en het opruimwerk schrijven als
    achtergrond; een verzoek (hier de test zelf) als voorgrond."""
    engine = get_engine(settings)
    await kladblok(engine)
    seen: dict[str, bool] = {}
    done = asyncio.Event()

    async def write(name: str) -> None:
        async with maker(settings)() as s:
            await s.execute(text("UPDATE kladblok SET n = n + 1 WHERE id = 1"))
            seen[name] = dbgate.gate_for(engine).holder_bg  # nu heeft hij de poort
            await s.commit()

    async def job(settings, payload):
        await write("taak")
        await asyncio.create_task(write("taak start taak"))
        done.set()

    async def opruimen(settings):
        await write("opruimen")

    jobs.register("test.achtergrond", job, lane="index", timeout=60)
    jobs.housekeeping("test.achtergrond", opruimen, 3600)
    async with maker(settings)() as s:
        await jobs.enqueue(s, "test.achtergrond", {})
        await s.commit()
    runner = jobs.Runner(settings, "index", housekeeping=True)
    await runner.start()
    try:
        await asyncio.wait_for(done.wait(), 10)
        await tot(lambda: "opruimen" in seen, 10)
    finally:
        await runner.stop()
        jobs._kinds.pop("test.achtergrond", None)
        jobs.forget_housekeeping("test.achtergrond")
    await write("verzoek")
    assert seen == {"taak": True, "taak start taak": True, "opruimen": True, "verzoek": False}


async def test_pomp_haalt_een_wachtende_upload_niet_in(settings, client, admin, monkeypatch):
    """De echte pomp (Runner index, de stap text) met een commit van write_text die 100 ms
    langer duurt (een trage fsync). Per upload: hoeveel write_texts begonnen nadat de upload om
    het slot vroeg en waren klaar vóór zijn commit. Zonder poort meer dan 0."""
    analysis.reset()
    hooks.reset()
    extract_step.register()
    try:
        async with maker(settings)() as s:
            await create_user(s, "eva@example.nl", password="lang-genoeg-123")
            await create_user(s, "bram@example.nl", password="lang-genoeg-123")
            await s.commit()
        eva = await login(client, "eva@example.nl")
        bram = await login(client, "bram@example.nl")
        eva_root = (await test_files.root(client, eva))["folder"]["id"]
        bram_root = (await test_files.root(client, bram))["folder"]["id"]
        rng = random.Random("pomp")
        for i in range(40):
            r = await test_files.put(
                client, eva, eva_root, f"doc-{i}.txt", bench.tekst(rng, 2000).encode()
            )
            assert r.status_code == 201, r.text

        class SlowCommit(AsyncSession):
            async def commit(self):  # het slot is al van hem: een trage fsync
                await asyncio.sleep(0.1)
                return await super().commit()

        monkeypatch.setattr(
            extract_step,
            "_maker",
            lambda s: async_sessionmaker(get_engine(s), class_=SlowCommit, expire_on_commit=False),
        )
        texts: list[tuple[float, float]] = []
        write_text = analysis.write_text

        async def timed(*args, **kwargs):
            begin = time.monotonic()
            try:
                return await write_text(*args, **kwargs)
            finally:
                texts.append((begin, time.monotonic()))

        monkeypatch.setattr(analysis, "write_text", timed)
        await analysis.start_backfill(settings)
        runner = jobs.Runner(settings, "index")
        with _Commits(get_engine(settings), monkeypatch) as commits:
            await runner.start()
            try:
                for i in range(12):
                    r = await test_files.put(
                        client, bram, bram_root, f"tussendoor-{i}.txt", f"klein {i}".encode()
                    )
                    assert r.status_code == 201, r.text
                    await asyncio.sleep(0.1)
            finally:
                await runner.stop()
    finally:
        analysis.reset()
        hooks.reset()
        extract_step.register()
    assert len(texts) >= 12  # de pomp liep intussen echt
    assert commits.overtaken(texts) == [0] * 12


@pytest.mark.parametrize("slot", ["blobs", "gebruiker"])
async def test_blobslot_geeft_voorrang(settings, tmp_path, monkeypatch, slot):
    """Wie BlobStore.lock of het slot van een gebruiker (files.user_lock) heeft, schrijft als
    voorgrond: daar kan een upload op wachten. Hier een achtergrondtaak met het slot, achter een
    andere achtergrondschrijver in de rij."""
    monkeypatch.setattr(BlobStore, "_locks", {})
    monkeypatch.setattr(files, "user_locks", {})
    owner = uuid.uuid4()

    def lock():
        return BlobStore(tmp_path / "blobs").lock if slot == "blobs" else files.user_lock(owner)

    engine = get_engine(settings)
    await kladblok(engine)
    gate = dbgate.gate_for(engine)
    order: list[str] = []
    holds, go = asyncio.Event(), asyncio.Event()

    async def schrijf(name: str, row: int) -> None:
        async with engine.begin() as c:
            await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = ?", (row,))
            order.append(name)

    async def houder():
        with dbgate.background():
            async with engine.begin() as c:
                await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 1")
                holds.set()
                await go.wait()

    async def achtergrond():
        with dbgate.background():
            await schrijf("achtergrond", 2)

    async def met_slot():
        with dbgate.background():
            async with lock():
                await schrijf("met slot", 3)

    tasks = [asyncio.create_task(houder())]
    await holds.wait()
    tasks.append(asyncio.create_task(achtergrond()))
    await tot(lambda: len(gate.bg) == 1)
    tasks.append(asyncio.create_task(met_slot()))
    await tot(lambda: len(gate.fg) + len(gate.bg) == 2)
    go.set()
    await asyncio.gather(*tasks)
    assert order == ["met slot", "achtergrond"]

    # Wachten op het slot terwijl de taak de poort heeft: vastgelegd (kan vastlopen)
    dbgate.take_violations()
    async with engine.begin() as c:
        await c.exec_driver_sql("UPDATE kladblok SET n = n + 1 WHERE id = 4")
        async with lock():
            pass
    assert [v[0] for v in dbgate.take_violations()] == ["lock_in_txn"]


async def test_herbouw_schrijft_als_achtergrond(settings, client, admin, monkeypatch):
    """De transacties van de herbouw zelf zijn achtergrond, after_chunk (hier een PATCH, als
    in test_search_rebuild.py) niet. En na het begin (delete-all) eerst een pauze, vóór stuk 1."""
    headers, _ = admin
    top = (await test_files.root(client, headers))["folder"]["id"]
    ids = []
    for i in range(4):
        r = await test_files.put(client, headers, top, f"stuk {i}.txt", b"stuk %d" % i)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    engine = get_engine(settings)
    log: list[tuple[str, bool]] = []
    PAUSE = 0.0123  # alleen de pauzes van de herbouw duren zo lang
    sleep = asyncio.sleep

    async def recorded(delay, *args, **kwargs):
        if delay == PAUSE:
            log.append(("pauze", False))
        return await sleep(delay, *args, **kwargs)

    def voor(conn, cursor, statement, *args):
        if dbgate.is_write(statement):
            log.append((statement.split(" WHERE")[0][:60], dbgate.BACKGROUND.get()))

    async def na_elk_stuk(cursor: int) -> None:
        r = await client.patch(
            f"/api/v1/files/{ids[0]}", json={"name": f"hernoemd {cursor}.txt"}, headers=headers
        )
        assert r.status_code == 200, r.text

    monkeypatch.setattr(asyncio, "sleep", recorded)
    event.listen(engine.sync_engine, "before_cursor_execute", voor)
    try:
        times = await rebuild.rebuild(engine, chunk=1, after_chunk=na_elk_stuk, pause=PAUSE)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", voor)
    assert len(times) >= 3
    eigen = [bg for what, bg in log if "search_state" in what or "INTO fts_" in what]
    assert eigen and all(eigen), log
    patch = [bg for what, bg in log if what.startswith("UPDATE node")]
    assert patch and not any(patch), log
    begin = next(i for i, (what, _) in enumerate(log) if "cursor = 0" in what)
    stuk = next(i for i, (what, _) in enumerate(log) if "cursor = cursor" in what)
    assert begin < [i for i, (what, _) in enumerate(log) if what == "pauze"][0] < stuk, log


async def test_opruimen_zonder_wezen_pakt_het_blobslot_niet(settings, client, admin, monkeypatch):
    """Elke upload eindigt met collect_garbage. Zonder blob met ref_count 0 neemt die het slot
    niet: het wachtte op wie het had (een PATCH met zijn commit). Met een wees wel, en die gaat
    ook van schijf."""
    headers, _ = admin
    top = (await test_files.root(client, headers))["folder"]["id"]
    taken: list[int] = []

    class Counting(dbgate.BoostLock):
        async def __aenter__(self):
            taken.append(1)
            return await super().__aenter__()

    monkeypatch.setattr(BlobStore, "_locks", {settings.blob_dir: Counting()})
    monkeypatch.setattr(files, "MAX_VERSIONS", 1)
    blobs = BlobStore(settings.blob_dir)

    async def put(data: bytes, **params) -> int:
        taken.clear()
        r = await test_files.put(client, headers, top, "a.txt", data, **params)
        assert r.status_code in (200, 201), r.text
        return len(taken)

    assert await put(b"een") == 1  # nieuw bestand
    assert await put(b"twee", conflict="replace") == 1  # "een" wordt een versie
    first = blobs.path(hashlib.sha256(b"een").hexdigest())
    assert first.exists()
    # Met MAX_VERSIONS 1 valt "een" nu weg: een wees, dus opruimen met het slot
    assert await put(b"drie", conflict="replace") == 2
    assert not first.exists()
    async with maker(settings)() as s:
        assert await s.scalar(text("SELECT count(*) FROM blob WHERE ref_count <= 0")) == 0
