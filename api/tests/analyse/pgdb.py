"""De database van een test voor Settings met de analyse aan (0.8.2, P4).

Met PLATFORM_TEST_DATABASE_URL draait elke test op een eigen PostgreSQL-database (de fixture
database_url_head in tests/conftest.py: een kopie van het gemigreerde sjabloon van de run, dezelfde
database als van de fixture settings). Settings van de analyse (FakeAnalyseWorker.settings) moeten
dan die database krijgen, en PLATFORM_ANALYSE_POSTGRES aan; daarna zegt pgcheck.check of de
triggers van 0013 er zijn (Settings.analyse_on). Op SQLite verandert er niets: de database blijft
platform.db in data_dir.
"""

from __future__ import annotations

from typing import Any

from app.analyse import pgcheck
from app.config import Settings


def on_db(database_url: str) -> dict[str, Any]:
    """Wat Settings erbij krijgt: op PostgreSQL de database van de test en
    PLATFORM_ANALYSE_POSTGRES aan; op SQLite niets."""
    if database_url.startswith("sqlite"):
        return {}
    return {"database_url": database_url, "analyse_postgres": True}


async def ready(settings: Settings) -> None:
    """Na upgrade: de triggers van 0013 gezien (op PostgreSQL), zodat analyse_on klopt."""
    assert await pgcheck.check(settings), "de triggers van 0013 ontbreken"
