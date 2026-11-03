"""Het harnas voor de e2e van zoeken (fase 6, §3.6 van docs/fase-6/bouwontwerp.md).

Uitleg en gebruik: docs/fase-6/z0-e2e.md. Kort:

- start_wereld(map): een eigen Server met in zijn database de wereld van §4.2
  (api/tests/search/wereld.py: Eva, Bram, Cas, Gijs, Dirk, Ada, Fien, Hans en Iris, met hun
  bestanden in de BlobStore van de app). ZoekApp.accounts heeft een Account per persoon
  ("eva", "bram", …), ZoekApp.wereld de ids van §4.2;
- start_boom(map, n): dezelfde app met de boom van app/search/bench.py (1.000 voor de snelle
  e2e, 100.000 voor de metingen van §4.4), alleen in de database: geen bestanden op schijf.
  ZoekApp.accounts heeft een Account per rol van §4.4 ("eigenaar", "ontvanger5", "ontvanger20",
  "groepslid", "gast", "alles"), ZoekApp.uitkomst wat bench.vul() maakte;
- app.referentie(): de referentie (api/tests/search/referentie.py) over de database zoals hij
  nu is. Elke aanroep leest opnieuw, dus ook na een upload of een wijziging van de test zelf
  (voor 100.000 duurt dat lang: alleen als de test hem echt nodig heeft);
- app.db(): een alleen-lezen sqlite3-verbinding naast de draaiende server (WAL), om in de index
  te kijken; app.wijzig(fn): de server stoppen, fn(conn, wereld) in één transactie (zoals
  wereld.intrekken en wereld.verhuis), en weer starten op dezelfde poort.

Ingelogd zijn gaat zoals in de rest van de e2e: server.call(account, …) voor de API en
Browsers.context_as(account) voor de browser (de fixture zoek_browsers in conftest.py). 2FA is
niet verplicht (setting "security", zoals make_admin hem zet), dus inloggen kan zonder code.

De datums komen van een vaste dag (wereld.DAG en bench.DAG: 3 november 2026), niet van nu.
Alles stopt op pid (Server.stop). conftest importeert deze module pas in een fixture.
"""

from __future__ import annotations

import contextlib
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select

from conftest import API_DIR, Account, Server
from seed import connect

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.search import wereld as W  # noqa: E402
from tests.search.referentie import Referentie  # noqa: E402

from app.config import Settings  # noqa: E402
from app.models import Setting  # noqa: E402
from app.search import bench  # noqa: E402
from app.security import SECURITY_KEY, hash_password  # noqa: E402


@dataclass
class ZoekApp:
    """Een draaiende app met een gezaaide database, en wie erin kan."""

    server: Server
    accounts: dict[str, Account]
    wereld: W.Wereld | None = None
    uitkomst: bench.Uitkomst | None = None
    seconden: dict[str, float] = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return self.server.data_dir

    @property
    def blob_dir(self) -> Path:
        return Settings(data_dir=self.data_dir).blob_dir

    def id(self, sleutel: str) -> str:
        """Het id van een node of map uit de wereld (wereld.ids), als tekst voor de API."""
        assert self.wereld is not None, "alleen in de wereld"
        return str(self.wereld.ids[sleutel])

    @contextlib.contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        """Alleen lezen, naast de draaiende server (die schrijft in WAL)."""
        pad = self.data_dir / "platform.db"
        con = sqlite3.connect(f"file:{pad}?mode=ro", uri=True, timeout=10)
        try:
            yield con
        finally:
            con.close()

    def referentie(self) -> Referentie:
        """De referentie over de database van nu (de server mag blijven draaien)."""
        with self.db() as con:
            return Referentie.lees(_Verbinding(con))

    def wijzig(self, fn: Callable[[Any, W.Wereld], Any]) -> Any:
        """Server stoppen, fn(conn, wereld) in één transactie, weer starten."""
        assert self.wereld is not None, "alleen in de wereld"
        w = self.wereld

        def doe(data_dir: Path) -> Any:
            with connect(data_dir) as conn:
                return fn(conn, w)

        return self.server.seeded(doe)

    def stop(self) -> None:
        self.server.stop()


class _Verbinding:
    """sqlite3 met exec_driver_sql, zoals Referentie.lees verwacht."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def exec_driver_sql(self, sql: str, args: Any = ()) -> Any:
        return _Rijen(self.con.execute(sql, args))


class _Rijen:
    def __init__(self, cur: sqlite3.Cursor):
        self.cur = cur

    def __iter__(self) -> Iterator[Any]:
        return iter(self.cur)

    def all(self) -> list[Any]:
        return self.cur.fetchall()


def _zonder_2fa(conn: Any) -> None:
    """2FA niet verplicht, zoals make_admin dat via /admin/security doet."""
    if conn.execute(select(Setting).where(Setting.key == SECURITY_KEY)).first() is None:
        conn.execute(insert(Setting).values(key=SECURITY_KEY, value={"require_2fa": False}))


def _nieuwe_server(base: Path) -> Server:
    """Een eigen server; één keer gestart en gestopt, zodat de migraties gedraaid hebben."""
    server = Server(Path(base) / "data")
    server.start()
    return server


def start_wereld(base: Path) -> ZoekApp:
    """De app met de wereld van §4.2, gestart; een Account per persoon."""
    klok = time.monotonic()
    server = _nieuwe_server(base)
    blob_dir = Settings(data_dir=server.data_dir).blob_dir

    def zaai(data_dir: Path) -> W.Wereld:
        with connect(data_dir) as conn:
            w = W.bouw(conn, blob_dir)
            _zonder_2fa(conn)
        return w

    try:
        w = server.seeded(zaai)
    except BaseException:
        server.stop()
        raise
    accounts = {
        naam: Account(
            str(w.users[naam]), W.email(naam), W.WACHTWOORD, weergave, rol, str(w.roots[naam])
        )
        for naam, (rol, weergave) in W.GEBRUIKERS.items()
    }
    return ZoekApp(server, accounts, wereld=w, seconden={"start": time.monotonic() - klok})


def start_boom(base: Path, n: int = 1_000, *, seed: int = 6, profiel: str = "basis") -> ZoekApp:
    """De app met de boom van bench.py (n bestanden), gestart; een Account per rol van §4.4.
    Bij n = 100.000 duurt het zaaien ±1 minuut (bench.Uitkomst.seconden)."""
    klok = time.monotonic()
    server = _nieuwe_server(base)
    plan = bench.Plan(n=n, seed=seed, profiel=profiel)

    def zaai(data_dir: Path) -> bench.Uitkomst:
        con = sqlite3.connect(data_dir / "platform.db")
        try:
            wachtwoord = hash_password(bench.WACHTWOORD)
            uit = bench.vul(con, plan, wachtwoord_hash=wachtwoord, blob_standaard=bench.app_blob())
        finally:
            con.close()
        with connect(data_dir) as conn:
            _zonder_2fa(conn)
        return uit

    try:
        uit = server.seeded(zaai)
    except BaseException:
        server.stop()
        raise
    accounts = {}
    for rol, sleutel in uit.rollen.items():
        g = uit.gebruikers[sleutel]
        user_id, root = str(uuid.UUID(g["id"])), str(uuid.UUID(g["root"]))
        naam = sleutel.capitalize()
        accounts[rol] = Account(user_id, g["email"], bench.WACHTWOORD, naam, g["rol"], root)
    seconden = {**uit.seconden, "start": time.monotonic() - klok}
    return ZoekApp(server, accounts, uitkomst=uit, seconden=seconden)
