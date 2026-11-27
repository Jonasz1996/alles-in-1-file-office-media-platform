"""Het sjabloon van de testdatabases op PostgreSQL (tests/conftest.py; release 0.8.2, P3).

Review van golf 2, F6: mislukt het maken van het sjabloon (een migratie die faalt), dan blijft
de database tpl_<uuid> niet staan. Anders maakte elke volgende test die settings vraagt er weer
een, en ging er geen weg (pytest_sessionfinish wist alleen een sjabloon dat gelukt is).
"""

from __future__ import annotations

import uuid

import pytest

from tests import conftest

pytestmark = [pytest.mark.anyio, pytest.mark.pg]


async def _bestaat(name: str) -> bool:
    import asyncpg

    conn = await asyncpg.connect(conftest.PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return bool(
            await conn.fetchval("SELECT count(*) FROM pg_database WHERE datname = $1", name)
        )
    finally:
        await conn.close()


async def test_een_mislukt_sjabloon_blijft_niet_staan(monkeypatch):
    name = "tpl_" + uuid.uuid4().hex

    async def kapot(settings):
        raise RuntimeError("de migratie faalt")

    monkeypatch.setattr(conftest, "upgrade", kapot)
    try:
        with pytest.raises(RuntimeError, match="de migratie faalt"):
            await conftest._pg_make_template(name)
        assert not await _bestaat(name)
    finally:
        await conftest._pg_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
