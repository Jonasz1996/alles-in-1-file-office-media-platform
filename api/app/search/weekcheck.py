"""De wekelijkse search-check (bouwontwerp fase 6, Keuze 12 en R4; I1): eens per week vergelijkt
rebuild.check() 200 willekeurige nodes van de index met de bron. De uitkomst staat in Setting
"search_check" en op /beheer (AdminSearch.check, de tab "Zoeken en foto's"); herstellen blijft
met de hand (de knop "Index herbouwen", of app.cli search-triggers; runbook zoeken.md).

- Opruimwerk van de Runner (jobs.housekeeping), elk uur: is de vorige uitkomst een week oud, of
  er is er nog geen, dan nu. De week telt zo ook over een herstart heen.
- Niet tijdens de pauze van /beheer (admin.apply_pause zet de rij index stil), niet zonder FTS5
  (de motor is dan like, ook op PostgreSQL) en niet tijdens een herbouw: dan het volgende uur
  opnieuw, zonder uitkomst. Ook zonder nodes geen uitkomst: een lege database is niets
  vergeleken.
- De controle zelf loopt in een eigen draad met een eigen event loop en engine, met PRAGMA
  query_only: hij schrijft niets en houdt de schrijver dus nooit vast, en de FTS5-tabel in het
  geheugen van check() houdt de event loop van de app niet bezig. Hij leest in één
  leestransactie (één momentopname): wat de app intussen schrijft, is geen verschil. Daarna is
  de uitkomst één korte schrijfactie.
- In het log alleen aantallen: geen num, geen naam, geen tekst.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from anyio import to_thread
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import analysis, jobs
from app.db import get_engine
from app.models import Setting, now
from app.search import engine as search_engine
from app.search import rebuild
from app.search.types import AdminCheck

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger("platform.search")

KEY = "search_check"  # Setting
NAME = "search.check"  # jobs.housekeeping
SAMPLE = 200  # nodes per keer (bouwontwerp: "een wekelijkse job draait hem met 200 nodes")
EVERY = timedelta(days=7)
TICK = 3600.0  # zo vaak kijkt het opruimwerk of het weer tijd is


def _stored(value: Any) -> AdminCheck | None:
    if not isinstance(value, dict):
        return None
    at, sampled, bad = value.get("at"), value.get("sampled"), value.get("bad")
    if not isinstance(at, str) or not all(
        isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (sampled, bad)
    ):
        return None
    return AdminCheck(at=at, sampled=sampled, bad=bad)


async def last(db: AsyncSession) -> AdminCheck | None:
    """De laatste uitkomst (GET /admin/search), of None."""
    row = await db.get(Setting, KEY, populate_existing=True)
    return _stored(row.value if row is not None else None)


def _due(found: AdminCheck | None, moment: datetime) -> bool:
    if found is None:
        return True
    try:
        at = datetime.fromisoformat(found.at)
    except ValueError:
        return True
    return at.tzinfo is None or moment - at >= EVERY


def _check(url: str) -> rebuild.Checked:
    """In een eigen draad: een eigen loop en engine, alleen lezen (query_only), in één
    leestransactie. check() leest bron en index in losse SELECTs; pysqlite opent voor een SELECT
    geen transactie (bouwontwerp, Keuze 12), dus zag elke SELECT een andere stand van de WAL. Wat
    de app tussen twee vragen schreef (een naam, de tekst van de backfill bij het starten), telde
    dan als verschil (review I1, F1/B1). Nu leest hij alles in één momentopname: in WAL houdt die
    de schrijver niet tegen, en hij duurt zo lang als de controle."""

    async def run() -> rebuild.Checked:
        engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)

        @event.listens_for(engine.sync_engine, "connect")
        def _read_only(conn, _record):
            conn.isolation_level = None  # geen BEGIN van pysqlite: die komt van _begin
            cur = conn.cursor()
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA query_only=ON")
            cur.close()

        @event.listens_for(engine.sync_engine, "begin")
        def _begin(conn):
            conn.exec_driver_sql("BEGIN")

        try:
            return await rebuild.check(engine, sample=SAMPLE)
        finally:
            await engine.dispose()

    return asyncio.run(run())


async def run(settings: Settings) -> AdminCheck | None:
    """Het opruimwerk: de controle als het tijd is; geeft de nieuwe uitkomst, of None als hij
    niet liep (niet aan de beurt, pauze, geen FTS5, een herbouw, geen nodes)."""
    if "index" in analysis.PAUSED or not settings.db_url.startswith("sqlite"):
        return None
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as db:
        if not _due(await last(db), now()):
            return None
        chosen = await search_engine.engine_for(settings, await db.connection())
        if chosen.name != "fts":
            return None
    found = await to_thread.run_sync(_check, settings.db_url)
    if found.rebuilding or found.sampled == 0:
        return None
    result = AdminCheck(at=now().isoformat(), sampled=found.sampled, bad=len(found.bad))
    async with maker() as db:
        row = await db.get(Setting, KEY)
        if row is None:
            db.add(Setting(key=KEY, value=result.model_dump()))
        else:
            row.value = result.model_dump()
        await db.commit()
    if result.bad:
        log.warning(
            "Zoeken: wekelijkse controle, %d node(s) vergeleken, %d verschil(len) met de bron",
            result.sampled,
            result.bad,
        )
    else:
        log.info(
            "Zoeken: wekelijkse controle, %d node(s) vergeleken, geen verschil", result.sampled
        )
    return result


def register() -> None:
    """Bij de import (admin.py importeert dit bestand); twee keer kan."""
    jobs.housekeeping(NAME, run, TICK)


register()
