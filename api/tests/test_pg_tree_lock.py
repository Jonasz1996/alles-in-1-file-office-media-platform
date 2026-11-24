"""Het slot van de boom per transactie (db.lock_tree; 0.8.2, review van PR #6, open punt in
docs/fase-6/i3/w3.md).

lock_tree onthoudt per transactie dat ze het slot heeft, en stuurt dan geen tweede statement.
Een advisory lock die in een savepoint genomen is, laat PostgreSQL los als dat savepoint
terugrolt. Eerst onthield lock_tree de transactie zelf: na zo'n savepoint dacht hij het slot nog
te hebben, en nam hij het niet opnieuw. Nu geldt wat hij onthield alleen in de transactie of het
savepoint waarin hij het nam, of daaronder.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.db import lock_tree
from tests.factories import maker

pytestmark = [pytest.mark.anyio, pytest.mark.pg]

HELD = sa.text(
    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
    "AND pid = pg_backend_pid()"
)


async def test_na_een_savepoint_dat_terugrolt_neemt_lock_tree_het_slot_opnieuw(settings):
    async with maker(settings)() as session:
        await session.execute(sa.text("SELECT 1"))
        savepoint = await session.begin_nested()
        await lock_tree(session)
        assert await session.scalar(HELD) == 1
        await savepoint.rollback()
        assert await session.scalar(HELD) == 0  # PostgreSQL liet het slot los
        await lock_tree(session)
        assert await session.scalar(HELD) == 1
        await session.rollback()


async def test_een_slot_van_de_transactie_geldt_ook_in_een_savepoint(settings):
    """Geen tweede statement als de transactie het slot al heeft, ook niet in een savepoint
    eronder (test_photometa.py telt de statements van een stuk)."""
    seen: list[str] = []
    async with maker(settings)() as session:
        engine = session.get_bind()

        def on_execute(_conn, _cursor, statement, _params, _context, _executemany):
            if "node_lock_tree()" in statement:
                seen.append(statement)

        sa.event.listen(engine, "before_cursor_execute", on_execute)
        try:
            await lock_tree(session)
            async with session.begin_nested():
                await lock_tree(session)
            await lock_tree(session)
        finally:
            sa.event.remove(engine, "before_cursor_execute", on_execute)
        assert len(seen) == 1
        assert await session.scalar(HELD) == 1
        await session.rollback()
