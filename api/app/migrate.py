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
    # Wat de database na de migratie kan (de FTS-tabellen van 0010 of niet): één keer lezen,
    # dan hoeft de eerste zoekvraag dat niet te doen. Hier geïmporteerd: zoeken trekt de
    # routers mee, en de CLI importeert dit bestand ook
    from app.search import engine as search_engine

    search_engine.forget(settings.db_url)
    async with get_engine(settings).connect() as conn:
        await search_engine.read_conn_info(conn)
