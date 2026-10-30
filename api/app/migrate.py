"""Databasemigraties met Alembic, automatisch bij het starten van de app."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import Settings
from app.db import get_engine

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


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
