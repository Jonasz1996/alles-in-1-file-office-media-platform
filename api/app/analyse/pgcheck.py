"""De analyse op PostgreSQL: kijkt bij de start of de triggers van 0013 er zijn (0.8.2, P4).

Op SQLite wissen de triggers van 0011 en 0012 de gezichten en de beeldvectoren van een node
die weggaat; op PostgreSQL doen de triggers van 0013 dat (migrations/versions/0013_postgres.py,
TRIGGERS). Ontbreekt er een, of staat er een uit (ALTER TABLE … DISABLE TRIGGER), dan blijven
gezichten van gewiste foto's staan. Daarom staat de analyse op PostgreSQL alleen aan als
check() ze allemaal aan zag (Settings.analyse_pg_block: anders no_pg_triggers).

check() draait in de lifespan direct na de migratie, en in de cli-commando's die de analyse
gebruiken. Op SQLite, of zonder PLATFORM_ANALYSE_ENABLED en PLATFORM_ANALYSE_POSTGRES, vraagt
hij niets aan de database. In het log alleen de namen van de triggers.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from app.config import Settings, pg_triggers_seen
from app.db import get_engine

log = logging.getLogger("platform.analyse")

# Gelijk aan TRIGGERS van migratie 0013 (test_analyse_pg.py legt dat vast): (naam, tabel)
TRIGGERS: tuple[tuple[str, str], ...] = (
    ("node_bs_tree", "node"),
    ("node_bi_tree", "node"),
    ("node_au_move", "node"),
    ("node_au_trash", "node"),
    ("node_au_content", "node"),
    ("node_ai_beeld", "node"),
    ("node_ad_beeld", "node"),
    ("node_au_taken", "node"),
    ("geo_biu", "photo_geo"),
    ("person_bd_face", "person"),
)

# De triggers die aan staan (tgenabled 'D' is uit), op de tabellen in het schema van de app
FOUND = text(
    """
    SELECT t.tgname, c.relname
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE NOT t.tgisinternal AND t.tgenabled <> 'D' AND n.nspname = current_schema()
    """
)


def missing(found: set[tuple[str, str]]) -> list[str]:
    """De namen uit TRIGGERS die niet (aan) in found staan."""
    return [name for name, table in TRIGGERS if (name, table) not in found]


async def check(settings: Settings) -> bool:
    """True als de analyse op deze database niet aan de triggers hoeft te wachten: op SQLite
    altijd, zonder vraag. Op PostgreSQL met PLATFORM_ANALYSE_ENABLED en
    PLATFORM_ANALYSE_POSTGRES: kijkt in pg_trigger en onthoudt de uitkomst per database
    (config.pg_triggers_seen); ontbreekt er een, dan een fout in het log en False."""
    if settings.db_url.startswith("sqlite"):
        return True
    if not (settings.analyse_enabled and settings.analyse_postgres):
        return False
    async with get_engine(settings).connect() as conn:
        rows = (await conn.execute(FOUND)).all()
    gone = missing({(row[0], row[1]) for row in rows})
    pg_triggers_seen(settings.db_url, not gone)
    if gone:
        log.error(
            "analyse blijft uit op PostgreSQL: de triggers van 0013 ontbreken of staan uit: %s",
            ", ".join(gone),
        )
    return not gone


def check_cli(settings: Settings) -> None:
    """Voor een cli-commando dat de analyse gebruikt (analyse-status, analyse-bench,
    analyse-kwaliteit, beeld-*): check() in een eigen lus, met de database daarna weer dicht.
    Op SQLite, of zonder de twee instellingen, niets."""
    if settings.db_url.startswith("sqlite"):
        return
    if not (settings.analyse_enabled and settings.analyse_postgres):
        return

    async def run() -> None:
        try:
            await check(settings)
        finally:
            await get_engine(settings).dispose()

    asyncio.run(run())
