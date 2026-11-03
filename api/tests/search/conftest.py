import pytest

from app import db
from tests.search.migratie import MADE


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
