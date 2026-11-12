from pathlib import Path

import pytest

from app import db
from tests.search.migratie import MADE

HIER = Path(__file__).resolve().parent
# De bestanden over de triggers en de migraties van zoeken (0010), gezichten (0011) en zoeken op
# beeld (0012): marker triggers erbij, zodat ze ook op SQLite 3.46.1 lopen (zoek-perf, slim.sh)
TRIGGERS = ("migration_0010", "migration_0011", "migration_0012", "trigger", "churn")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Elke test hier krijgt marker zoek; die over de triggers en de migratie ook triggers
    (bouwontwerp §4.1). e2e/slim.sh kiest ze met -m "zoek or triggers" in python:3.12-slim
    (SQLite 3.46.1). tryfirst: vóór de selectie op -m."""
    for item in items:
        pad = Path(str(item.path))
        if pad.parent != HIER:
            continue
        item.add_marker(pytest.mark.zoek)
        if any(t in pad.name for t in TRIGGERS):
            item.add_marker(pytest.mark.triggers)


@pytest.fixture(autouse=True)
async def _databases_dicht(anyio_backend):
    """De engines van settings_at dicht na elke test, zoals de fixture settings in
    tests/conftest.py doet: anders sluit aiosqlite hun verbindingen pas als de event loop al weg
    is, en geeft dat een fout in een thread tijdens een latere test."""
    yield
    if MADE:
        while MADE:
            await db.get_engine(MADE.pop()).dispose()
        db._engine.cache_clear()
