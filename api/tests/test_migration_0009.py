"""Migratie 0009 (docs/fotos-indexeren.md): twee indexen, verder niets. Een database van 0008
met data, omhoog, de indexen met hun kolommen, de rest ongewijzigd, en weer terug naar precies
0008 (zodat v0.7.0 weer start: db-downgrade 0008). Plus: claim gebruikt de index echt (geen
sortering van de hele rij bij elke claim)."""

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import jobs, previews
from app.db import get_engine
from app.models import Base, Job
from tests.test_migration_0006 import migrate
from tests.test_migration_0007 import fill_0006, schema, settings_at

INDEXES = {
    "job": {"ix_job_queue": ["status", "type", "run_after", "created_at"]},
    "blob": {"ix_blob_ref_count": ["ref_count"]},
}


@pytest.mark.anyio
async def test_van_0008_naar_0009_en_terug(tmp_path):
    settings = settings_at(tmp_path, "oud")
    fresh = settings_at(tmp_path, "vers")
    await migrate(settings, command.upgrade, "0008")
    await migrate(fresh, command.upgrade, "0008")
    await fill_0006(settings)
    before = await schema(settings)
    assert before == await schema(fresh)

    await migrate(settings, command.upgrade, "0009")
    after = await schema(settings)
    assert after["alembic_version"] == ["0009"]
    for table, wanted in INDEXES.items():
        found = after[table]["indexes"]
        for name, cols in wanted.items():
            assert found[name][3] == cols, (table, name)
            assert found[name][0] == 0, (table, name)  # niet uniek
    # Verder verandert er niets: geen kolommen, geen andere indexen, geen tabellen
    assert set(after) == set(before)
    for table, info in before.items():
        if table in ("index_sql", "alembic_version"):
            continue
        assert after[table]["columns"] == info["columns"], table
        assert after[table]["fks"] == info["fks"], table
        extra = set(after[table]["indexes"]) - set(info["indexes"])
        assert extra == set(INDEXES.get(table, {})), table

    await migrate(settings, command.downgrade, "0008")
    assert await schema(settings) == before
    async with get_engine(settings).connect() as conn:
        assert await conn.scalar(sa.text("SELECT count(*) FROM blob")) == 1

    # En weer omhoog gaat ook
    await migrate(settings, command.upgrade, "head")
    assert (await schema(settings))["alembic_version"] == ["0009"]


@pytest.mark.anyio
async def test_modellen_gelijk_aan_de_migratie(tmp_path):
    """De indexen in models.py zijn precies wat 0009 maakt."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head")

    def diff(connection):
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": False}
        )
        return compare_metadata(context, Base.metadata)

    async with get_engine(settings).connect() as conn:
        changes = await conn.run_sync(diff)
    assert [c for c in changes if "ix_job_queue" in repr(c) or "ix_blob_ref_count" in repr(c)] == []


@pytest.mark.anyio
async def test_claim_zoekt_via_de_index(tmp_path):
    """De query van claim in de rij preview (beide ronden) en die van collect_garbage lopen via
    de nieuwe indexen, zonder de hele rij te sorteren (gemeten: 70 ms per claim met 50.000
    previews in de rij zonder index, 14 ms met)."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head")
    plans = []
    async with get_engine(settings).connect() as conn:
        for lane_filter in jobs._claim_filters(previews.LANE):
            query = (
                sa.select(Job.id)
                .where(Job.status == "queued", Job.run_after <= jobs.now(), lane_filter)
                .order_by(Job.run_after, Job.created_at)
                .limit(10)
            )
            compiled = query.compile(conn.sync_engine, compile_kwargs={"literal_binds": True})
            rows = (await conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {compiled}")).all()
            plans.append(" | ".join(r[-1] for r in rows))
        rows = (
            await conn.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT sha256 FROM blob WHERE ref_count <= 0"
            )
        ).all()
        garbage = " | ".join(r[-1] for r in rows)
    assert len(plans) == 2  # eerst thumbnails, dan de grote
    for plan in plans:
        assert "ix_job_queue" in plan, plan
        assert "TEMP B-TREE" not in plan, plan
    assert "ix_blob_ref_count" in garbage, garbage
