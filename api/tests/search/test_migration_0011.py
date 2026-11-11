"""Migratie 0011 (bouwontwerp fase 6, §3.1a; Keuze 61, 64 en 69): van 0010 met data heen, terug
en weer heen, met en zonder FTS5. De tabellen, indexen en triggers zijn precies die van
app/search/schema.py; de bestanden zelf veranderen niet; terug is precies 0010 (zodat 0.8.0 weer
start: app.cli db-downgrade 0010). Terug wist de gezichten met secure_delete: na de downgrade en
het checkpoint van app.migrate staat er geen merkteken meer in platform.db of -wal.

En de plannen: wat X2 leest, met own_live op node erbij, gaat via ix_face_open, ix_face_new en
ix_face_scan_todo, zonder te sorteren (K1-03)."""

import uuid

import pytest
import sqlalchemy as sa
from alembic import command

from app.db import get_engine
from app.migrate import checkpoint
from app.search import schema
from app.search.live import OWN_LIVE_SQL
from tests.search.gezichten import fill_faces, marks, rows
from tests.search.migratie import NOW, data, fill_0009, migrate, settings_at
from tests.search.test_migration_0010 import objects
from tests.test_migration_0007 import schema as schema_of

pytestmark = pytest.mark.anyio


def _zonder(found: dict, *keys: str) -> dict:
    return {k: v for k, v in found.items() if k not in keys}


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_van_0010_met_data_heen_terug_en_weer_heen(tmp_path, fts5):
    settings = settings_at(tmp_path, "oud")
    fresh = settings_at(tmp_path, "vers")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "0010", fts5=fts5)
    await migrate(fresh, command.upgrade, "0010", fts5=fts5)
    before = await schema_of(settings)
    assert before == await schema_of(fresh)
    files = await data(settings)
    triggers_0010 = schema.TRIGGERS_0010 if fts5 else schema.TRIGGERS_NO_FTS_0010

    await migrate(settings, command.upgrade, "0011", fts5=fts5)
    after = await schema_of(settings)
    assert after["alembic_version"] == ["0011"]
    assert set(after) - set(before) == set(schema.TABLES_0011)
    # Geen bestaande tabel veranderd (niets herschrijft node); alleen de nieuwe indexen erbij
    old = set(before) - {"alembic_version", "index_sql"}
    assert {t: after[t] for t in old} == {t: before[t] for t in old}
    assert set(before["index_sql"]) <= set(after["index_sql"])
    assert await objects(settings, "trigger") == {**triggers_0010, **schema.TRIGGERS_0011}
    indexes = await objects(settings, "index")
    assert {k: indexes[k] for k in schema.INDEXES_0011} == schema.INDEXES_0011
    assert await data(settings) == files

    made = await fill_faces(settings, ids)
    assert len(await rows(settings, "face")) == 4
    assert len(await rows(settings, "node_origin")) == 2
    assert sum((marks(settings)).values()) > 0
    files = await data(settings)  # met de nieuwe foto

    await migrate(settings, command.downgrade, "0010")
    assert await checkpoint(settings)
    assert await schema_of(settings) == before
    assert await schema_of(settings) == await schema_of(fresh)
    assert await objects(settings, "trigger") == triggers_0010
    assert await data(settings) == files  # de foto's en de persoon blijven
    async with get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT name FROM person")).scalars().all() == ["Oma"]
    # De gezichten zijn biometrische gegevens: nergens meer een byte van (Keuze 64)
    assert marks(settings) == {"platform.db": 0, "platform.db-wal": 0}

    # En weer heen: leeg, en de triggers doen weer mee
    await migrate(settings, command.upgrade, "0011", fts5=fts5)
    assert (await schema_of(settings))["alembic_version"] == ["0011"]
    for table in schema.TABLES_0011:
        assert await rows(settings, table) == []
    assert await objects(settings, "trigger") == {**triggers_0010, **schema.TRIGGERS_0011}
    assert made["foto"]


async def test_terug_met_een_lezer_open_lukt_het_checkpoint_later(tmp_path, monkeypatch):
    """Een lezer met een open snapshot houdt TRUNCATE tegen: checkpoint() geeft dan False na zijn
    budget (en wacht niet langer), en lukt zodra de lezer weg is. Tot dan staat het merkteken
    nog in -wal; daarna nergens."""
    import sqlite3

    from app import migrate as migrate_mod

    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "0011")
    await fill_faces(settings, ids)
    assert await checkpoint(settings)  # alles in platform.db, -wal leeg

    lezer = sqlite3.connect(settings.data_dir / "platform.db")
    try:
        lezer.execute("BEGIN")
        assert lezer.execute("SELECT count(*) FROM face").fetchone() == (4,)
        await migrate(settings, command.downgrade, "0010")
        monkeypatch.setattr(migrate_mod, "CHECKPOINT_BUDGET", 0.3)
        assert not await checkpoint(settings)
    finally:
        lezer.close()
    assert await checkpoint(settings)
    assert marks(settings) == {"platform.db": 0, "platform.db-wal": 0}


# --- De plannen ------------------------------------------------------------------------------

LIVE = OWN_LIVE_SQL.format(n="n")
PLANS = {
    # "Wie is dit?": de voorstellen voor één persoon, de zekerste eerst
    "ix_face_open": (
        "SELECT f.id FROM face f JOIN node n ON n.id = f.node_id WHERE f.owner_id = :me "
        f"AND f.suggest_id = :person AND f.hidden = 0 AND {LIVE} "
        "ORDER BY f.suggest_score DESC LIMIT 200"
    ),
    # De vectoren voor een groepsronde, de nieuwste eerst (K1-03)
    "ix_face_new": (
        "SELECT f.id, f.vec FROM face f JOIN node n ON n.id = f.node_id WHERE f.owner_id = :me "
        f"AND f.hidden = 0 AND {LIVE} ORDER BY f.created_at DESC LIMIT 60000"
    ),
    # De volgende foto's om te scannen, de nieuwste eerst
    "ix_face_scan_todo": (
        "SELECT s.node_id FROM face_scan s JOIN node n ON n.id = s.node_id "
        f"WHERE s.state = 'wait' AND s.owner_id = :me AND {LIVE} ORDER BY s.prio DESC LIMIT 4"
    ),
}


def _bulk(conn, ids: dict[str, uuid.UUID], person: uuid.UUID) -> None:
    """2.000 foto's van Eva en 2.000 van Bram, elk met twee gezichten en een scan."""
    nodes, faces, scans = [], [], []
    for owner, root in ((ids["eva"], ids["root"]), (ids["bram"], ids["bram_root"])):
        for i in range(2000):
            node = uuid.uuid4().hex
            nodes.append((node, root.hex, owner.hex, f"f{i}.jpg", NOW, NOW, f"f{i}.jpg"))
            day = f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}"
            scans.append((node, owner.hex, "wait" if i % 3 else "done", day, NOW, NOW))
            for k in range(2):
                suggest = person.hex if owner == ids["eva"] and (i + k) % 5 == 0 else None
                faces.append((uuid.uuid4().hex, node, owner.hex, b"\0" * 540, suggest,
                              0.5 if suggest else None, i % 7 == 0, day))  # fmt: skip
    conn.exec_driver_sql(
        "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, created_at, "
        "modified_at, search_name) VALUES (?, ?, ?, ?, 'file', 'image/jpeg', 1, ?, ?, ?)",
        nodes,
    )
    conn.exec_driver_sql(
        "INSERT INTO face_scan (node_id, owner_id, version, state, prio, queued_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?, ?, ?)",
        scans,
    )
    conn.exec_driver_sql(
        "INSERT INTO face (id, node_id, owner_id, x, y, w, h, score, px, vec, version, "
        "suggest_id, suggest_score, hidden, created_at) VALUES (?, ?, ?, 0.1, 0.1, 0.2, 0.2, "
        "0.9, 80, ?, 1, ?, ?, ?, ?)",
        faces,
    )
    conn.exec_driver_sql("ANALYZE")


async def test_de_plannen_gebruiken_de_indexen_van_0011(tmp_path):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "0011")
    made = await fill_faces(settings, ids)
    async with get_engine(settings).begin() as conn:
        await conn.run_sync(_bulk, ids, made["person"])
    params = {"me": ids["eva"].hex, "person": made["person"].hex}
    async with get_engine(settings).connect() as conn:
        for index, sql in PLANS.items():
            plan = " | ".join(
                r[3]
                for r in (await conn.execute(sa.text("EXPLAIN QUERY PLAN " + sql), params)).all()
            )
            assert f"USING INDEX {index} " in plan or f"USING COVERING INDEX {index} " in plan, (
                index,
                plan,
            )
            assert "TEMP B-TREE" not in plan, (index, plan)  # niets gesorteerd (K1-03)
            assert (await conn.execute(sa.text(sql), params)).all()
