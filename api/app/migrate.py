"""Databasemigraties met Alembic, automatisch bij het starten van de app."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import Settings
from app.db import get_engine, sqlite_path, wal_truncate_retry

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
# Zo lang hooguit probeert checkpoint() de WAL leeg te maken (bij het starten is er geen lezer)
CHECKPOINT_BUDGET = 10.0  # s

log = logging.getLogger("platform.migrate")


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def upgrade(settings: Settings) -> None:
    cfg = alembic_config(settings.db_url)

    def _run(connection) -> None:
        cfg.attributes["connection"] = connection
        # Voor migraties die tijden omzetten (0006: de terugvaldatum van foto's)
        cfg.attributes["timezone"] = settings.timezone
        command.upgrade(cfg, "head")

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(_run)
    await checkpoint(settings)
    # Wat de database na de migratie kan (de FTS-tabellen van 0010 of niet): één keer lezen,
    # dan hoeft de eerste zoekvraag dat niet te doen. Hier geïmporteerd: zoeken trekt de
    # routers mee, en de CLI importeert dit bestand ook
    from app.search import engine as search_engine

    search_engine.forget(settings.db_url)
    async with get_engine(settings).connect() as conn:
        await search_engine.read_conn_info(conn)


async def checkpoint(settings: Settings) -> bool:
    """Na een migratie de WAL leeg (§3.1a): alle migraties lopen in één transactie, dus dit kan
    pas na de commit. Zo staat na een downgrade van 0011 niets van de gezichten meer in -wal.
    False als een lezer het tegenhield (in de log alleen de getallen)."""
    path = sqlite_path(settings.db_url)
    if path is None:
        return True
    found = await wal_truncate_retry(path, CHECKPOINT_BUDGET)
    if found.busy:
        log.warning(
            "checkpoint na de migratie niet gelukt: busy=%d log=%d done=%d",
            found.busy,
            found.log,
            found.done,
        )
    return not found.busy
