"""Eén schrijver tegelijk op SQLite, op volgorde, binnen het proces van de app.

SQLite in WAL-stand heeft één schrijfslot. Wie het niet krijgt, wacht in de busy handler van
SQLite (busy_timeout, app/db.py): die slaapt 1, 2, 5, 10 ... 100 ms en probeert het dan opnieuw.
Er is geen rij. Geeft een schrijver het slot vrij terwijl een ander slaapt, en pakt hij het een
paar ms later weer (de pomp van de index: write_text na write_text, de herbouw: stuk na stuk),
dan mist die ander die kans en wacht hij nog een stap, en nog een. Met een trage fsync (elke
commit doet er een, terwijl hij het slot heeft) wachtte een upload zo seconden (de nacontrole
van golf 4, docs/fase-6/z7.md).

Deze poort zet de schrijvers van het proces in een rij, vóór SQLite:

- Een schrijftransactie neemt de poort vlak voor zijn eerste schrijvende statement
  (before_cursor_execute, in de greenlet van SQLAlchemy) en geeft hem terug zodra de COMMIT of
  ROLLBACK van de driver klaar is (dialect.do_commit en do_rollback), of als de pool de
  verbinding terugneemt, sluit of ongeldig maakt. Dat is de tijd dat SQLite het schrijfslot
  heeft. Lezen gaat nooit door de poort.
- De poort gaat bij het vrijgeven meteen naar de volgende in de rij: niemand pakt hem ertussen.
- Twee rijen: verzoeken (voorgrond) eerst, achtergrondwerk daarna. Na FG_STREAK verzoeken op rij
  komt een wachtende achtergrondschrijver aan de beurt, dus niets wacht eeuwig. Achtergrond is
  wat een Runner draait (mark_background bovenaan zijn taken) en de herbouw van de index
  (background()). Wie BlobStore.lock of een gebruikersslot heeft (BoostLock), is voorgrond
  zolang hij dat heeft: daar kan een upload op wachten.
- Wie op een BoostLock wacht of hem heeft, gaat zo schrijven (uploads staan in de rij van
  BlobStore.lock, niet in die van de poort). Zolang er zo iemand is, gaat een vrije poort niet
  naar de achtergrond: hij blijft vrij voor dat verzoek, hooguit HOLD_BACK per keer en binnen
  FG_STREAK. Anders kreeg de achtergrond de poort na elke upload, en wachtte de volgende upload
  achter het slot op die hele schrijfactie (vier uploaders: tot drie na elkaar, de review van
  de schrijfpoort).
- Wachten op de poort duurt hooguit TIMEOUT (5 s, zoals busy_timeout); daarna dezelfde fout als
  nu: OperationalError "database is locked".
- Eindigt de taak die de poort heeft zonder hem terug te geven, dan gaat hij alsnog door.
- Het checkpoint van de WAL zit niet meer in een COMMIT (wal_autocheckpoint=0 in app/db.py):
  hooguit eens per seconde, zolang er commits komen, doet een eigen verbinding van sqlite3 in een
  thread een PASSIVE checkpoint. Dat neemt het schrijfslot niet en houdt niemand op.

Alleen voor SQLite (install vanuit app/db.py); PostgreSQL heeft een eigen rij voor zijn sloten.
Een schrijver buiten dit proces (de CLI) gaat zoals altijd via de busy handler van SQLite. In
de log komen alleen het werkwoord en de tabel van een statement, de naam van een taak en tijden:
nooit SQL, parameters, een zoekvraag of tekst.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import functools
import logging
import re
import sqlite3
import threading
import time
import weakref
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.util.concurrency import await_, in_greenlet

log = logging.getLogger("platform.dbgate")

TIMEOUT = 5.0  # s: zo lang als busy_timeout; daarna "database is locked", zoals nu
FG_STREAK = 8  # zoveel verzoeken op rij, dan een wachtende achtergrondschrijver
SLOW_HOLD = 2.0  # s: een waarschuwing als iemand de poort langer heeft
HOLD_BACK = 0.2  # s: zo lang hooguit blijft een vrije poort vrij voor een verzoek dat eraan komt
CHECKPOINT_EVERY = 1.0  # s
CHECKPOINT_IDLE = 3  # rondes zonder commit, dan stopt de checkpointer (tot de volgende commit)

# Achtergrondwerk schrijft na de verzoeken (zie boven). Standaard voorgrond.
BACKGROUND: ContextVar[bool] = ContextVar("dbgate_background", default=False)

READ, WRITE, TXN = 0, 1, 2


@contextlib.contextmanager
def background() -> Iterator[None]:
    """Wat hierbinnen schrijft, is achtergrondwerk."""
    token = BACKGROUND.set(True)
    try:
        yield
    finally:
        BACKGROUND.reset(token)


def mark_background() -> None:
    """De hele taak is achtergrondwerk. Alleen bovenaan de eigen coroutine van een taak
    (Runner._work en Runner._housekeeping): de waarde blijft tot de taak stopt, en gaat mee naar
    de taken die hij start."""
    BACKGROUND.set(True)


# Wat vóór het eerste woord mag staan: wit en commentaar
_LEAD = re.compile(r"(?:\s+|--[^\n]*(?:\n|$)|/\*.*?(?:\*/|\Z))*", re.S)
_WORD = re.compile(r"[A-Za-z_]+")
_DML = re.compile(r"\b(?:INSERT|UPDATE|DELETE|REPLACE)\b", re.I)
_READS = frozenset({"SELECT", "VALUES", "EXPLAIN"})
_TXNS = frozenset({"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"})
# PRAGMA's zonder "=" die toch kunnen schrijven
_PRAGMA_WRITES = re.compile(r"\b(?:optimize|incremental_vacuum)\b", re.I)
_CHECKPOINT = re.compile(r"\bwal_checkpoint\s*(?:\(\s*(\w*)\s*\))?", re.I)


@functools.lru_cache(maxsize=4096)
def kind(statement: str) -> int:
    """READ, WRITE of TXN (BEGIN, COMMIT, SAVEPOINT ...). Voorzichtig: wat niet duidelijk leest,
    schrijft. WITH ... UPDATE schrijft (pysqlite doet daar geen BEGIN voor), DDL ook."""
    rest = statement[_LEAD.match(statement).end() :]
    m = _WORD.match(rest)
    word = m.group(0).upper() if m else ""
    if word in _READS:
        return READ
    if word == "WITH":
        return WRITE if _DML.search(rest) else READ
    if word in _TXNS:
        if word == "BEGIN" and re.match(r"BEGIN\s+(?:IMMEDIATE|EXCLUSIVE)\b", rest, re.I):
            return WRITE
        return TXN
    if word == "PRAGMA":
        if "=" in rest or _PRAGMA_WRITES.search(rest):
            return WRITE
        ck = _CHECKPOINT.search(rest)
        if ck and (ck.group(1) or "PASSIVE").upper() != "PASSIVE":
            return WRITE  # FULL, RESTART en TRUNCATE wachten op schrijvers
        return READ
    return WRITE


def is_write(statement: str) -> bool:
    return kind(statement) == WRITE


@functools.lru_cache(maxsize=1024)
def what(statement: str) -> str:
    """Alleen het werkwoord en de tabel ("UPDATE blob", "INSERT job"): voor de log."""
    rest = statement[_LEAD.match(statement).end() :]
    m = re.match(
        r"(\w+)(?:\s+OR\s+\w+)?(?:\s+(?:INTO|FROM|TABLE|INDEX|TRIGGER|VIEW))?"
        r"(?:\s+IF\s+(?:NOT\s+)?EXISTS)?\s+\"?(\w+)",
        rest,
        re.I,
    )
    if m is None:
        word = _WORD.match(rest)
        return word.group(0).upper()[:20] if word else "?"
    return f"{m.group(1).upper()} {m.group(2)}"[:60]


# Overtredingen voor de tests (tests/conftest.py): (soort, werkwoord en tabel, taak)
_violations: collections.deque[tuple[str, str, str]] = collections.deque(maxlen=200)


def _violation(kind_: str, detail: str, task: asyncio.Task | None) -> None:
    name = task.get_name() if task is not None else "-"
    _violations.append((kind_, detail, name))
    log.debug("Schrijfpoort: %s (%s, taak %s)", kind_, detail, name)


def take_violations() -> list[tuple[str, str, str]]:
    """Geeft de vastgelegde overtredingen en vergeet ze: ungated (geschreven buiten de poort,
    in een transactie), same_task (een tweede verbinding van de taak die de poort heeft: die
    gaat zoals vroeger via de busy handler van SQLite) en lock_in_txn (wachten op een
    BoostLock terwijl de taak de poort heeft)."""
    out = list(_violations)
    _violations.clear()
    return out


@dataclass(eq=False)
class _Entry:
    fut: asyncio.Future
    conn: object
    task: asyncio.Task | None
    bg: bool
    what: str


_all_gates: weakref.WeakSet[Gate] = weakref.WeakSet()

# Per loop: hoeveel keer er op een BoostLock gewacht wordt of er een vastgehouden wordt
_boosted: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, int] = weakref.WeakKeyDictionary()


def _boost(loop: asyncio.AbstractEventLoop, n: int) -> None:
    left = _boosted.get(loop, 0) + n
    if left > 0:
        _boosted[loop] = left
        return
    _boosted.pop(loop, None)
    # Niemand meer: een poort die vrij werd gehouden, gaat nu naar de achtergrond
    for gate in list(_all_gates):
        if gate._loop() is loop and gate.holder is None:
            gate._dispatch()


class Gate:
    """De rij van één engine in één event loop. Alles hierin gebeurt in de thread van die loop
    (release vanuit een andere thread gaat via call_soon_threadsafe)."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = weakref.ref(loop)
        self._thread = threading.get_ident()
        self.holder: object | None = None  # de DBAPI-verbinding die de poort heeft
        self.holder_task: asyncio.Task | None = None
        self.holder_bg = False
        self.holder_what = ""
        self.since = 0.0
        self.grant_id = 0
        self._done_cb: Any = None
        self.fg: collections.deque[_Entry] = collections.deque()
        self.bg: collections.deque[_Entry] = collections.deque()
        self.streak = 0
        self._timer: asyncio.TimerHandle | None = None  # het einde van HOLD_BACK
        self._expired = False
        self.counts: collections.Counter[str] = collections.Counter()
        _all_gates.add(self)

    # --- nemen ---------------------------------------------------------------------------
    def _live(self) -> bool:
        for q in (self.fg, self.bg):
            while q and q[0].fut.done():
                q.popleft()
        return bool(self.fg or self.bg)

    def try_take(self, conn: object, task: asyncio.Task | None, bg: bool, what: str = "") -> bool:
        """Neemt de poort meteen, als niemand hem heeft en niemand voor gaat. Een verzoek mag ook
        als alleen de achtergrond wacht (de poort werd voor hem vrijgehouden), zolang die niet
        aan de beurt is (FG_STREAK). De achtergrond niet als hij vrijgehouden wordt."""
        if self.holder is not None:
            return False
        self._live()
        if bg:
            if self.fg or self.bg or self._holding_back():
                return False
            self.streak = 0
        else:
            if self.fg or (self.bg and self.streak >= FG_STREAK):
                return False
            self.streak = self.streak + 1 if self.bg else 0
        self._grant(conn, task, bg, what)
        return True

    async def acquire(
        self, conn: object, task: asyncio.Task | None, bg: bool, what: str = ""
    ) -> None:
        """Wacht in de rij tot de poort naar conn gaat; na TIMEOUT "database is locked"."""
        loop = asyncio.get_running_loop()
        entry = _Entry(loop.create_future(), conn, task, bg, what)
        (self.bg if bg else self.fg).append(entry)
        self.counts["waits"] += 1
        if self.holder is None:
            self._dispatch()  # vrij (vrijgehouden): wie gaat er nu voor
        try:
            async with asyncio.timeout(TIMEOUT):
                await entry.fut
        except BaseException as e:
            if entry.fut.done() and not entry.fut.cancelled():
                self.release(conn)  # net gekregen, maar we gaan weg: door naar de volgende
            else:
                entry.fut.cancel()
                with contextlib.suppress(ValueError):
                    (self.bg if bg else self.fg).remove(entry)
            if isinstance(e, TimeoutError):
                self.counts["timeouts"] += 1
                held = time.monotonic() - self.since if self.holder is not None else 0.0
                log.warning(
                    "Schrijfpoort: %.1f s gewacht op %s (%s, taak %s); de poort was %.1f s bij "
                    "%s (%s, taak %s)",
                    TIMEOUT,
                    what,
                    "achtergrond" if bg else "voorgrond",
                    task.get_name() if task is not None else "-",
                    held,
                    self.holder_what or "-",
                    "achtergrond" if self.holder_bg else "voorgrond",
                    self.holder_task.get_name() if self.holder_task is not None else "-",
                )
                # Dezelfde fout als de busy handler van SQLite na busy_timeout; SQLAlchemy
                # verpakt hem als OperationalError (zonder parameters, hide_parameters)
                raise sqlite3.OperationalError("database is locked") from None
            raise

    def _grant(self, conn: object, task: asyncio.Task | None, bg: bool, what: str) -> None:
        self.holder, self.holder_task, self.holder_bg, self.holder_what = conn, task, bg, what
        self.since = time.monotonic()
        self.grant_id += 1
        self.counts["grants"] += 1
        self._done_cb = None
        self._disarm()
        if task is not None and not task.done():
            # Eindigt de taak met de poort nog in handen, dan gaat hij alsnog door
            cb = functools.partial(self._task_done, conn, self.grant_id)
            task.add_done_callback(cb)
            self._done_cb = cb

    def _task_done(self, conn: object, grant_id: int, task: asyncio.Task) -> None:
        if self.holder is conn and self.grant_id == grant_id:
            self.counts["holder_done"] += 1
            log.warning(
                "Schrijfpoort: taak %s stopte met de poort (%s) nog in handen; die gaat door",
                task.get_name(),
                self.holder_what or "-",
            )
            self.release(conn)

    # --- teruggeven ----------------------------------------------------------------------
    def release(self, conn: object) -> bool:
        """Geeft de poort terug als conn hem heeft, en geeft hem meteen aan de volgende.
        True als conn hem had (vanuit een andere thread: als hij hem net had)."""
        if self.holder is not conn:
            return False
        if threading.get_ident() != self._thread:
            loop = self._loop()
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self.release, conn)
            return True
        held = time.monotonic() - self.since
        if held > SLOW_HOLD:
            log.warning(
                "Schrijfpoort: %s (%s, taak %s) had de poort %.1f s",
                self.holder_what or "-",
                "achtergrond" if self.holder_bg else "voorgrond",
                self.holder_task.get_name() if self.holder_task is not None else "-",
                held,
            )
        task, cb = self.holder_task, self._done_cb
        self.holder = self.holder_task = self._done_cb = None
        self.holder_what = ""
        if task is not None and cb is not None:
            task.remove_done_callback(cb)
        self._dispatch()
        return True

    def _dispatch(self) -> None:
        """De poort is vrij: naar de volgende, of vrij houden voor een verzoek dat eraan komt."""
        while self.holder is None:
            self._live()
            if self.fg and (not self.bg or self.streak < FG_STREAK):
                entry = self.fg.popleft()
                self.streak = self.streak + 1 if self.bg else 0
            elif self.bg and not self._holding_back():
                entry = self.bg.popleft()
                self.streak = 0
            else:
                if self.bg:
                    self._arm()
                else:
                    self._disarm()
                return
            if entry.fut.done():
                continue
            self._grant(entry.conn, entry.task, entry.bg, entry.what)
            entry.fut.set_result(None)

    # --- vrij houden voor een verzoek ----------------------------------------------------
    def _holding_back(self) -> bool:
        """Een vrije poort niet aan de achtergrond: iemand wacht op een BoostLock of heeft er
        een (een upload achter BlobStore.lock), de achtergrond is nog niet aan de beurt en
        HOLD_BACK is nog niet om."""
        loop = self._loop()
        return (
            not self._expired
            and self.streak < FG_STREAK
            and loop is not None
            and _boosted.get(loop, 0) > 0
        )

    def _arm(self) -> None:
        if self._timer is None and not self._expired:
            loop = self._loop()
            if loop is not None and not loop.is_closed():
                self.counts["held_back"] += 1
                self._timer = loop.call_later(HOLD_BACK, self._expire)

    def _disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._expired = False

    def _expire(self) -> None:
        self._timer = None
        self._expired = True
        self.counts["hold_expired"] += 1
        if self.holder is None:
            self._dispatch()


def stats(gate: Gate) -> dict[str, int]:
    """Tellers voor de tests: grants, waits, holder_done, timeouts, held_back (de poort bleef
    vrij voor een verzoek) en hold_expired (HOLD_BACK was om)."""
    keys = ("grants", "waits", "holder_done", "timeouts", "held_back", "hold_expired")
    return {k: gate.counts[k] for k in keys}


def _holds_gate(task: asyncio.Task) -> bool:
    return any(g.holder is not None and g.holder_task is task for g in list(_all_gates))


class BoostLock(asyncio.Lock):
    """Een asyncio.Lock (BlobStore.lock, het slot per gebruiker van files.py) waarvan de houder
    voorgrond is zolang hij het heeft: een upload die op dit slot wacht, wacht anders ook op de
    beurt van een achtergrondtaak achter de rij verzoeken. Wie erop wacht of hem heeft, telt
    als een verzoek dat zo gaat schrijven: een vrije poort blijft dan vrij (Gate._holding_back).
    Wachten op dit slot terwijl de taak de poort heeft, legt het vast als lock_in_txn (kan
    vastlopen tot TIMEOUT)."""

    def __init__(self) -> None:
        super().__init__()
        self._token: Any = None
        self._boost_loop: asyncio.AbstractEventLoop | None = None

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if task is not None and _holds_gate(task):
            _violation("lock_in_txn", type(self).__name__, task)
        loop = asyncio.get_running_loop()
        _boost(loop, 1)
        try:
            await self.acquire()
        except BaseException:
            _boost(loop, -1)
            raise
        self._boost_loop = loop
        self._token = BACKGROUND.set(False)

    async def __aexit__(self, *exc: object) -> None:
        token, self._token = self._token, None
        loop, self._boost_loop = self._boost_loop, None
        if token is not None:
            with contextlib.suppress(ValueError):
                BACKGROUND.reset(token)
        self.release()
        if loop is not None:
            _boost(loop, -1)


# --- het checkpoint van de WAL -------------------------------------------------------------
def _checkpoint(path: str) -> tuple[int, int, int]:
    """Eén PASSIVE checkpoint op een eigen, korte verbinding (de sqlite3 van de stdlib, dezelfde
    als die van aiosqlite). Neemt het schrijfslot niet; mode=rw maakt nooit een bestand.
    Geeft (busy, pagina's in de WAL, daarvan in de database)."""
    uri = Path(path).absolute().as_uri() + "?mode=rw"
    con = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
    try:
        busy, wal, done = con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return busy, wal, done
    finally:
        con.close()


class _Checkpointer:
    def __init__(self, path: str):
        self.path = path
        self.dirty = False
        self.task: asyncio.Task | None = None

    def kick(self, loop: asyncio.AbstractEventLoop) -> None:
        self.dirty = True
        if self.task is None or self.task.done():
            self.task = loop.create_task(self._run(), name="wal-checkpoint")

    async def _run(self) -> None:
        idle = 0
        while idle < CHECKPOINT_IDLE:
            await asyncio.sleep(CHECKPOINT_EVERY)
            if not self.dirty:
                idle += 1
                continue
            self.dirty, idle = False, 0
            try:
                await asyncio.to_thread(_checkpoint, self.path)
            except sqlite3.Error as e:
                log.debug("WAL-checkpoint niet gelukt: %s", type(e).__name__)

    def stop(self) -> None:
        task = self.task
        if task is None or task.done():
            return
        loop = task.get_loop()
        if loop.is_closed():
            return
        try:
            same = asyncio.get_running_loop() is loop
        except RuntimeError:
            same = False
        if same:
            task.cancel()
        else:
            loop.call_soon_threadsafe(task.cancel)


# --- aan de engine -------------------------------------------------------------------------
def _raw(dbapi_connection: Any) -> Any:
    """De DBAPI-verbinding achter een verbinding van de pool, of zichzelf."""
    return getattr(dbapi_connection, "dbapi_connection", dbapi_connection)


def _in_txn(raw: Any) -> bool:
    try:
        return bool(raw.driver_connection.in_transaction)
    except Exception:  # noqa: BLE001 - dicht of weg: dan zeker geen transactie
        return False


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def gate_for(engine: AsyncEngine | Any, loop: asyncio.AbstractEventLoop | None = None):
    """De poort van deze engine in deze loop (standaard de lopende), of None zonder poort."""
    sync = getattr(engine, "sync_engine", engine)
    gates = getattr(sync, "_dbgate_gates", None)
    if gates is None:
        return None
    loop = loop or asyncio.get_running_loop()
    gate = gates.get(loop)
    if gate is None:
        gate = gates[loop] = Gate(loop)
    return gate


def install(engine: AsyncEngine) -> None:
    """Zet de poort en de checkpointer op deze engine. Alleen voor SQLite (app/db.py)."""
    sync = engine.sync_engine
    dialect = sync.dialect
    gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Gate] = weakref.WeakKeyDictionary()
    sync._dbgate_gates = gates  # type: ignore[attr-defined]
    path = engine.url.database or ""
    ckpts: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Checkpointer] = (
        weakref.WeakKeyDictionary()
    )
    sync._dbgate_ckpts = ckpts  # type: ignore[attr-defined]

    def release(raw: Any) -> bool:
        held = False
        for gate in list(gates.values()):
            held = gate.release(raw) or held
        return held

    def written() -> None:
        """Na een geslaagde schrijftransactie: de checkpointer heeft werk."""
        if not path or path == ":memory:" or path.startswith("file:"):
            return
        loop = _running_loop()
        if loop is None:
            return
        ck = ckpts.get(loop)
        if ck is None:
            ck = ckpts[loop] = _Checkpointer(path)
        ck.kick(loop)

    @event.listens_for(sync, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        if kind(statement) != WRITE or not in_greenlet():
            return
        raw = conn.connection.dbapi_connection
        loop = asyncio.get_running_loop()
        gate = gates.get(loop)
        if gate is None:
            gate = gates[loop] = Gate(loop)
        if gate.holder is raw:
            return
        task = asyncio.current_task()
        if task is not None and gate.holder is not None and gate.holder_task is task:
            # Een tweede verbinding van de taak die de poort heeft: wachten op de poort liep
            # vast. Zoals vroeger: de busy handler van SQLite (en na 5 s database is locked)
            _violation("same_task", what(statement), task)
            return
        bg = BACKGROUND.get()
        if not gate.try_take(raw, task, bg, what(statement)):
            await_(gate.acquire(raw, task, bg, what(statement)))

    @event.listens_for(sync, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        k = kind(statement)
        if k == READ:
            return
        loop = _running_loop()
        gate = gates.get(loop) if loop is not None else None
        if gate is None:
            return
        raw = conn.connection.dbapi_connection
        in_txn = _in_txn(raw)
        if gate.holder is raw:
            if not in_txn:
                # In autocommit geschreven (DDL, WITH ... UPDATE) of een COMMIT/RELEASE als
                # statement: het slot van SQLite is al weer vrij
                gate.release(raw)
                if k == WRITE:
                    written()
        elif k == WRITE and in_txn and in_greenlet():
            task = asyncio.current_task()
            if task is None or gate.holder_task is not task:
                _violation("ungated", what(statement), task)

    @event.listens_for(sync, "handle_error")
    def _error(ctx):
        conn = ctx.connection
        if conn is None:
            return
        try:
            raw = conn.connection.dbapi_connection
        except Exception:  # noqa: BLE001 - al dicht of ongeldig: de pool geeft hem vrij
            return
        if raw is not None and not _in_txn(raw):
            release(raw)

    orig_commit, orig_rollback = dialect.do_commit, dialect.do_rollback

    def do_commit(dbapi_connection):
        raw = _raw(dbapi_connection)
        try:
            orig_commit(dbapi_connection)
        except BaseException:
            if not _in_txn(raw):
                release(raw)
            raise  # anders geeft de rollback hem vrij
        # Pas nu: de COMMIT en zijn fsync zijn klaar
        if release(raw):
            written()

    def do_rollback(dbapi_connection):
        try:
            orig_rollback(dbapi_connection)
        finally:
            release(_raw(dbapi_connection))

    do_commit._dbgate = True  # type: ignore[attr-defined]
    do_rollback._dbgate = True  # type: ignore[attr-defined]
    dialect.do_commit = do_commit
    dialect.do_rollback = do_rollback

    def _gone(dbapi_connection, *args):
        if dbapi_connection is not None:
            release(_raw(dbapi_connection))

    for name in ("checkin", "invalidate", "soft_invalidate", "close", "close_detached", "detach"):
        event.listen(sync.pool, name, _gone)

    @event.listens_for(sync, "engine_disposed")
    def _disposed(_engine):
        for ck in list(ckpts.values()):
            ck.stop()
