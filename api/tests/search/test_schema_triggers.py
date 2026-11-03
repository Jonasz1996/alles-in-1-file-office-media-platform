"""Wachter (§4.1): de triggers, de view en de FTS-tabellen in de database zijn precies wat
app/search/schema.py zegt, en de bevroren kopie in migratie 0010 is daaraan gelijk. Wie een
trigger verandert, verandert dus beide en schrijft een nieuwe migratie."""

import importlib.util
from pathlib import Path

import pytest

from app.search import schema

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0010_zoeken.py"


def migration_0010():
    spec = importlib.util.spec_from_file_location("migratie_0010", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def master(settings, kind: str) -> dict[str, str]:
    from app.db import get_engine

    async with get_engine(settings).connect() as conn:
        rows = await conn.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = ?", (kind,)
        )
        return {r[0]: r[1] for r in rows.all()}


@pytest.mark.anyio
async def test_triggers_in_de_database_zijn_schema_triggers(settings):
    assert await master(settings, "trigger") == schema.TRIGGERS


@pytest.mark.anyio
async def test_view_en_fts_tabellen_in_de_database(settings):
    assert await master(settings, "view") == {"search_src": schema.VIEW_SQL}
    tables = await master(settings, "table")
    for name, sql in schema.FTS_TABLES.items():
        assert tables[name] == sql
    for name, sql in schema.SEQ_TABLES.items():
        assert tables[name] == sql
    indexes = await master(settings, "index")
    for name, sql in schema.NODE_INDEXES.items():
        assert indexes[name] == sql
    # De virtuele kolom staat in de CREATE van node, zoals ALTER TABLE hem toevoegde
    assert f"GENERATED ALWAYS AS ({schema.FTYPE_SQL}) VIRTUAL" in tables["node"]


def test_de_migratie_is_een_kopie_van_schema():
    m = migration_0010()
    assert m.TRIGGERS == schema.TRIGGERS
    assert m.TRIGGERS_NO_FTS == schema.TRIGGERS_NO_FTS
    for name in (
        "FTYPE_SQL",
        "NODE_COLUMNS",
        "NODE_INDEXES",
        "ALBUM_ITEM_COLUMNS",
        "SEQ_TABLES",
        "VIEW_SQL",
        "FTS_TABLES",
        "FILL",
        "FTS_FILL",
        "NEW_TABLES",
    ):
        assert getattr(m, name) == getattr(schema, name), name


def test_zonder_fts_alleen_nummer_pad_dead_en_foto_rijen():
    """De vorm zonder FTS5 raakt geen FTS-tabel en geen view; de triggers die de foto-rijen
    opruimen en de paden bijhouden, zijn er wel."""
    assert set(schema.TRIGGERS_NO_FTS) == {
        "node_ai",
        "node_bu_text",
        "node_au_owner",
        "node_au_move",
        "node_au_trash",
    }
    for sql in schema.TRIGGERS_NO_FTS.values():
        assert "fts_" not in sql and "search_src" not in sql
    assert set(schema.TRIGGERS_NO_FTS) < set(schema.TRIGGERS)
    for name in ("node_au_owner", "node_au_move", "node_au_trash"):
        assert schema.TRIGGERS_NO_FTS[name] == schema.TRIGGERS[name]


def test_elke_trigger_op_een_bekende_tabel():
    """bt_* op blob_text, po_* op photo_ocr, mi_* op media_item, node_* op node; en geen
    trigger schrijft met INSERT OR REPLACE (dan klopt de 'delete' in FTS niet meer)."""
    tables = {"node": "node", "bt": "blob_text", "po": "photo_ocr", "mi": "media_item"}
    for name, sql in schema.TRIGGERS.items():
        assert f" ON {tables[name.split('_')[0]]}" in sql.split("BEGIN")[0], name
        assert "OR REPLACE" not in sql and "OR IGNORE" not in sql, name
