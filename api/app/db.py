import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app import dbgate
from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


# Op elke verbinding met de database (Keuze 64, K1-03; bouwontwerp fase 6, "Het wissen"):
# - secure_delete: wat gewist wordt, zet SQLite op nul, ook in vrije pagina's. Debian bouwt
#   SQLite al met SECURE_DELETE (3.46.1 in python:3.12-slim); dit maakt het onafhankelijk van de
#   bouw.
# - temp_store=MEMORY: een sortering zonder index en een tijdelijke tabel blijven in het
#   geheugen. Anders schrijft SQLite die rijen naar een tijdelijk bestand op schijf, buiten de
#   database en de WAL.
PRIVACY = ("PRAGMA secure_delete=1", "PRAGMA temp_store=2")


def privacy(conn) -> None:
    """De pragma's van PRIVACY op een DBAPI-verbinding (sqlite3, of de adapter van aiosqlite).
    Ook voor een verbinding buiten de pool: tests/test_db_pragmas.py loopt ze allemaal na."""
    cur = conn.cursor()
    for sql in PRIVACY:
        cur.execute(sql)
    cur.close()


@lru_cache
def _engine(url: str) -> AsyncEngine:
    # hide_parameters: een DBAPIError (database is locked, schijf vol) zet de parameters niet
    # in zijn tekst, dus ook de zoekvraag q en bestandsnamen niet in de log (review Z0,
    # veilig F6)
    # pool_pre_ping niet bij SQLite: een verbinding met een bestand valt niet weg, en de ping
    # (SELECT 1) kostte met aiosqlite bij elk verzoek vier keer heen en weer naar zijn thread
    # (review golf 1 van fase 6, H2)
    sqlite = url.startswith("sqlite")
    engine = create_async_engine(url, pool_pre_ping=not sqlite, hide_parameters=True)
    if sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(conn, _record):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            # Het checkpoint van de WAL niet in een COMMIT, terwijl die het schrijfslot heeft:
            # dat doet de checkpointer van app/dbgate.py, op een eigen verbinding
            cur.execute("PRAGMA wal_autocheckpoint=0")
            cur.close()
            privacy(conn)

        # Eén schrijver tegelijk, op volgorde (de nacontrole van golf 4, app/dbgate.py)
        dbgate.install(engine)

    return engine


def get_engine(settings: Settings) -> AsyncEngine:
    return _engine(settings.db_url)


async def get_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


# --- De WAL leegmaken ------------------------------------------------------------------------
#
# Na een migratie (app/migrate.py, app/cli.py) en eens per uur (app/jobs.py) gaat de WAL leeg:
# met secure_delete staan gewiste bytes dan nergens meer, ook niet in -wal (bouwontwerp §3.1a en
# "Het wissen"). Altijd op een eigen verbinding met busy_timeout 0: met de gewone 5 s laat een
# checkpoint dat op een lezer wacht alle schrijvers ±5 s wachten (K2-08). Eerst PASSIVE, dat het
# schrijfslot niet neemt; dan heeft TRUNCATE bijna niets meer te kopiëren en heeft hij het slot
# maar even. Een lezer of schrijver in de weg: busy = 1, en er wacht niemand.

BACKOFF_FIRST = 0.05  # s: de eerste wachttijd na busy; daarna steeds het dubbele
BACKOFF_MAX = 5.0  # s


@dataclass(frozen=True)
class Checkpoint:
    """De uitkomst van PRAGMA wal_checkpoint: busy 1 als iemand in de weg zat (de WAL is dan
    niet leeg), log de pagina's in de WAL en done hoeveel daarvan in de database staan (-1:
    onbekend). Alleen getallen: zo kan hij in een log."""

    busy: int
    log: int
    done: int


def sqlite_path(url: str) -> Path | None:
    """Het bestand van een SQLite-database, of None (PostgreSQL, in het geheugen)."""
    found = make_url(url)
    name = found.database or ""
    if found.get_backend_name() != "sqlite" or not name or name == ":memory:":
        return None
    if name.startswith("file:"):
        return None
    return Path(name)


def wal_truncate(path: Path) -> Checkpoint:
    """Eén keer de WAL leeg, zonder te wachten (zie boven). Synchroon: in een thread draaien."""
    uri = Path(path).absolute().as_uri() + "?mode=rw"
    con = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
    try:
        privacy(con)
        try:
            con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            busy, log, done = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.OperationalError as exc:
            # Een ander checkpoint (app/dbgate.py) heeft het slot van het checkpoint
            if exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                return Checkpoint(1, -1, -1)
            raise
        return Checkpoint(int(busy), int(log), int(done))
    finally:
        con.close()


async def wal_truncate_retry(path: Path, budget: float) -> Checkpoint:
    """wal_truncate tot busy = 0, opnieuw na 50 ms, dan steeds het dubbele tot 5 s, en hooguit
    budget seconden; de laatste uitkomst. Wachten gebeurt hier, met asyncio.sleep, nooit in
    SQLite."""
    wait = BACKOFF_FIRST
    deadline = time.monotonic() + budget
    while True:
        found = await asyncio.to_thread(wal_truncate, path)
        left = deadline - time.monotonic()
        if not found.busy or left <= 0:
            return found
        await asyncio.sleep(min(wait, left))
        wait = min(wait * 2, BACKOFF_MAX)
