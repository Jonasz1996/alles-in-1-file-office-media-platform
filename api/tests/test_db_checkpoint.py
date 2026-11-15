"""De WAL leegmaken (bouwontwerp fase 6, §3.1a en "Het wissen"; K2-08; A0): app.db.wal_truncate,
het checkpoint van het uur (housekeeping db.checkpoint in app.jobs) en dat na een migratie
(app.migrate.checkpoint).

- Zonder lezer is -wal daarna 0 bytes.
- Met een lezer die een momentopname vasthoudt, lukt het niet: busy = 1, meteen terug, en in de
  log alleen de getallen. Het volgende uur weer; na een migratie opnieuw tot het budget op is.
- Een schrijver wacht er niet op: naast een checkpoint zo vaak als de app het ooit doet, wacht
  geen schrijfactie 100 ms of langer op het schrijfslot (K2-08), ook met een lezer die TRUNCATE
  tegenhoudt.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from app import db, jobs
from app import migrate as migrate_mod
from app.db import Checkpoint, get_engine, sqlite_path, wal_truncate, wal_truncate_retry

pytestmark = pytest.mark.anyio


def _wal(path: Path) -> int:
    wal = path.with_name(path.name + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def _path(settings) -> Path:
    path = sqlite_path(settings.db_url)
    assert path is not None
    return path


async def _write(settings, n: int = 20) -> None:
    """Rijen in een eigen tabel proef, via de pool: zo staat er iets in -wal."""
    async with get_engine(settings).begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS proef (id INTEGER PRIMARY KEY, data BLOB)"
        )
        for _ in range(n):
            await conn.exec_driver_sql("INSERT INTO proef (data) VALUES (?)", (b"x" * 500,))


def _reader(path: Path) -> sqlite3.Connection:
    """Een lezer met een open momentopname: hij houdt TRUNCATE tegen."""
    con = sqlite3.connect(path)
    con.execute("BEGIN")
    con.execute("SELECT count(*) FROM node").fetchone()
    return con


def test_het_pad_van_de_database():
    assert sqlite_path("sqlite+aiosqlite:////data/platform.db") == Path("/data/platform.db")
    assert sqlite_path("sqlite+aiosqlite:///:memory:") is None
    assert sqlite_path("sqlite+aiosqlite://") is None
    assert sqlite_path("postgresql+asyncpg://u@db/platform") is None


def test_een_keer_per_uur_bij_het_opruimwerk():
    item = jobs._housekeeping[jobs.CHECKPOINT]
    assert jobs.CHECKPOINT == "db.checkpoint"
    assert item.every == jobs.HOUSEKEEPING == 3600.0


async def test_het_checkpoint_van_het_uur_maakt_de_wal_leeg(settings, caplog):
    path = _path(settings)
    await _write(settings)
    assert _wal(path) > 0
    with caplog.at_level(logging.INFO, logger="platform.jobs"):
        assert jobs.CHECKPOINT in await jobs.run_housekeeping(settings)
    assert _wal(path) == 0
    assert not [r for r in caplog.records if "WAL-checkpoint" in r.getMessage()]


async def test_met_een_lezer_geeft_het_op_en_logt_alleen_getallen(settings, caplog):
    path = _path(settings)
    lezer = _reader(path)
    try:
        await _write(settings)
        before = _wal(path)
        start = time.monotonic()
        with caplog.at_level(logging.INFO, logger="platform.jobs"):
            await jobs._checkpoint(settings)
        assert time.monotonic() - start < 0.5  # geen busy_timeout: meteen terug
        assert _wal(path) == before > 0
    finally:
        lezer.close()
    [record] = [r for r in caplog.records if "WAL-checkpoint" in r.getMessage()]
    assert record.levelno == logging.INFO
    assert re.fullmatch(
        r"WAL-checkpoint van het uur bezet: busy=1 log=-?\d+ done=-?\d+", record.getMessage()
    )
    # Het volgende uur, zonder lezer: leeg
    await jobs._checkpoint(settings)
    assert _wal(path) == 0


async def test_na_een_migratie_opnieuw_tot_het_budget_op_is(settings, monkeypatch, caplog):
    path = _path(settings)
    tries: list[Checkpoint] = []
    real = db.wal_truncate

    def counted(p):
        found = real(p)
        tries.append(found)
        return found

    monkeypatch.setattr(db, "wal_truncate", counted)
    monkeypatch.setattr(migrate_mod, "CHECKPOINT_BUDGET", 0.4)
    lezer = _reader(path)
    try:
        await _write(settings)
        start = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="platform.migrate"):
            assert not await migrate_mod.checkpoint(settings)
        took = time.monotonic() - start
    finally:
        lezer.close()
    assert 0.35 < took < 1.5, took
    assert len(tries) >= 3 and all(t.busy for t in tries)
    [record] = caplog.records
    assert re.fullmatch(
        r"checkpoint na de migratie niet gelukt: busy=1 log=-?\d+ done=-?\d+",
        record.getMessage(),
    )
    tries.clear()
    assert await migrate_mod.checkpoint(settings)
    assert len(tries) == 1 and _wal(path) == 0


async def test_retry_stopt_zodra_het_lukt(settings):
    path = _path(settings)
    lezer = _reader(path)
    await _write(settings)
    # De lezer gaat na 0,2 s weg (in deze draad: daar is hij gemaakt)
    asyncio.get_running_loop().call_later(0.2, lezer.close)
    start = time.monotonic()
    found = await wal_truncate_retry(path, 5.0)
    assert not found.busy
    assert time.monotonic() - start < 2.0
    assert _wal(path) == 0


def _writes(path: Path, n: int) -> list[float]:
    """n korte schrijfacties zoals de app ze doet (busy_timeout 5 s); per schrijfactie hoe lang
    hij op het schrijfslot wachtte (BEGIN IMMEDIATE)."""
    con = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    waited = []
    try:
        for _ in range(n):
            start = time.monotonic()
            con.execute("BEGIN IMMEDIATE")
            waited.append(time.monotonic() - start)
            con.execute("INSERT INTO proef (data) VALUES (?)", (b"y" * 2000,))
            con.execute("COMMIT")
            time.sleep(0.005)
    finally:
        con.close()
    return waited


@pytest.mark.parametrize("met_lezer", [False, True], ids=["zonder-lezer", "met-lezer"])
async def test_een_schrijver_wacht_niet_op_het_checkpoint(settings, met_lezer):
    """K2-08: het checkpoint heeft busy_timeout 0 en neemt het schrijfslot hooguit even. 150
    schrijfacties, met ernaast een checkpoint zo vaak als de app het ooit doet (opnieuw na
    BACKOFF_FIRST): geen enkele wacht 100 ms of langer op het schrijfslot.

    Gemeten is het wachten op het slot, zoals in K2-08 ("de schrijver wachtte"), niet de COMMIT:
    dat is de fsync van de schrijver zelf, en die piekt op een drukke schijf ook zonder
    checkpoint (gemeten tot 275 ms). En niet een lus zonder pauze: die neemt het slot duizenden
    keren per seconde, en dan grijpt de busy-handler van SQLite (opnieuw na 1, 2, 5, 10, 15, 20,
    25 ... ms) soms een paar keer achter elkaar mis (gemeten: 54, 79 en 104 ms). Zo vaak doet de
    app het nooit."""
    path = _path(settings)
    await _write(settings)
    assert max(_writes(path, 20)) < 0.1  # zonder checkpoint: niets in de weg

    stop = threading.Event()
    found: list[Checkpoint] = []

    def loop() -> None:
        while not stop.is_set():
            found.append(wal_truncate(path))
            stop.wait(db.BACKOFF_FIRST)

    lezer = _reader(path) if met_lezer else None
    worker = threading.Thread(target=loop)
    worker.start()
    try:
        waited = _writes(path, 150)
    finally:
        stop.set()
        worker.join()
        if lezer is not None:
            lezer.close()
    # Het checkpoint liep echt naast de schrijver, en maakte zonder lezer de WAL echt leeg
    assert len(found) >= 10
    if met_lezer:
        assert all(f.busy for f in found)
    else:
        assert sum(not f.busy for f in found) >= 10
    assert max(waited) < 0.1, sorted(waited)[-5:]
