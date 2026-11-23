"""Migratie 0014: de zoekindex op PostgreSQL (release 0.8.2, P5; docs/fase-6/i3/w3.md).

- PostgreSQL, een lege database: 0013 → 0014 → 0013 geeft hetzelfde schema als een nieuwe 0013
  (ook de tekst van fase6_fill, die 0014 vervangt), en weer naar 0014 een index die klopt.
- PostgreSQL, met rijen (de boom van pgboom, tekst, OCR van een foto en een titel): na 0014 staat
  elke node in fts_node, gelijk aan wat fts_refresh opnieuw schrijft; terug naar 0013 blijven alle
  rijen, en weer naar 0014 is de index weer heel.
- Geen extensie: 0014 maakt er geen (plan 0.8.2, vraag 1).
- SQLite: 0014 doet niets, heen en terug.
"""

from __future__ import annotations

import uuid

import pytest
from alembic import command

from app import db as app_db
from app.config import Settings
from tests.pgboom import boom, rows, run
from tests.search.migratie import check_fts, data, fill_0009, migrate, settings_at
from tests.test_migration_0013 import _schema, _sqlite_state, _version

pytestmark = pytest.mark.anyio

# De zes triggers van 0014 (TRIGGERS in de migratie)
FTS_TRIGGERS = {
    ("fts_node_ai", "node"),
    ("fts_node_au", "node"),
    ("fts_node_ad", "node"),
    ("fts_bt_aiud", "blob_text"),
    ("fts_po_aiud", "photo_ocr"),
    ("fts_mi_aiud", "media_item"),
}
FILL = "SELECT pg_get_functiondef('fase6_fill(boolean)'::regprocedure)"


@pytest.fixture
async def at_0013(tmp_path, database_url):
    """Een eigen database (PostgreSQL met PLATFORM_TEST_DATABASE_URL) op revisie 0013."""
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8,
                        database_url=database_url)  # fmt: skip
    await migrate(settings, command.upgrade, "0013")
    yield settings
    await app_db.get_engine(settings).dispose()
    app_db._engine.cache_clear()


async def _fts_ok(settings: Settings) -> None:
    async with app_db.get_engine(settings).connect() as conn:
        await conn.run_sync(check_fts)


async def _extensions(settings: Settings) -> set:
    return set(await rows(settings, "SELECT extname FROM pg_extension"))


@pytest.mark.pg
async def test_heen_terug_heen_op_een_lege_database(at_0013):
    settings = at_0013
    fresh = await _schema(settings)
    fill_0013 = await rows(settings, FILL)
    extensions = await _extensions(settings)

    await migrate(settings, command.upgrade, "0014")
    schema = await _schema(settings)
    assert FTS_TRIGGERS <= schema["triggers"]
    assert {"cf_fold", "cf_tsv", "fts_refresh"} <= {f[0] for f in schema["functions"]}
    assert await rows(settings, FILL) != fill_0013
    assert await _extensions(settings) == extensions
    await _fts_ok(settings)

    await migrate(settings, command.downgrade, "0013")
    assert await _schema(settings) == fresh
    assert await rows(settings, FILL) == fill_0013
    assert await rows(settings, "SELECT to_regclass('fts_node'), to_regclass('fts_name')") == [
        (None, None)
    ]

    await migrate(settings, command.upgrade, "0014")
    assert await _schema(settings) == schema
    await _fts_ok(settings)


@pytest.mark.pg
async def test_heen_terug_heen_met_rijen(at_0013):
    settings = at_0013
    ids = await boom(settings)
    # Tekst van het verslag, OCR van het strand (alleen in otsv) en een titel van media
    await run(
        settings,
        "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
        "SELECT blob_id, 'jaarcijfers en notulen', '', 'auteur eva', 0, now() FROM node "
        "WHERE id = :n",
        n=ids["verslag"],
    )
    await run(
        settings,
        "INSERT INTO photo_ocr (node_id, owner_id, text, words, angle, updated_at) "
        "VALUES (:n, :o, 'strandpaviljoen', '\\x00'::bytea, 0, now())",
        n=ids["strand"],
        o=ids["eva"],
    )
    lib = uuid.uuid4()
    await run(
        settings,
        "INSERT INTO media_library (id, owner_id, root_id, kind, name, created_at) "
        "VALUES (:lib, :o, :r, 'movies', 'Films', now())",
        lib=lib,
        o=ids["eva"],
        r=ids["root"],
    )
    await run(
        settings,
        "INSERT INTO media_item (node_id, library_id, kind, title, sort_title, added_at, "
        "updated_at) VALUES (:n, :lib, 'movie', 'Jaarverslag Film', 'jaarverslag', now(), now())",
        n=ids["verslag"],
        lib=lib,
    )
    before = await data(settings)
    nodes = await rows(settings, "SELECT id, num, mpath, dead FROM node ORDER BY id")

    async def found(word: str, column: str = "tsv") -> set:
        got = await rows(
            settings,
            f"SELECT n.id FROM fts_node f JOIN node n ON n.num = f.rowid "
            f"WHERE f.{column} @@ to_tsquery('simple', :w)",
            w=word,
        )
        return {r[0] for r in got}

    async def check() -> None:
        await _fts_ok(settings)
        [(indexed,)] = await rows(settings, "SELECT count(*) FROM fts_node")
        [(live,)] = await rows(settings, "SELECT count(*) FROM node WHERE num IS NOT NULL")
        assert indexed == live
        assert await found("jaarcijfers") == {ids["verslag"]}
        assert await found("jaarverslag") == {ids["verslag"]}
        assert await found("strandpaviljoen") == set()
        assert await found("strandpaviljoen", "otsv") == {ids["strand"]}

    await migrate(settings, command.upgrade, "0014")
    await check()

    await migrate(settings, command.downgrade, "0013")
    assert await data(settings) == before
    assert await rows(settings, "SELECT id, num, mpath, dead FROM node ORDER BY id") == nodes
    assert await rows(settings, "SELECT to_regclass('fts_node')") == [(None,)]

    await migrate(settings, command.upgrade, "0014")
    await check()
    assert await data(settings) == before


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_op_sqlite_doet_0014_niets(tmp_path, fts5):
    """Niet pg: altijd een eigen SQLite-bestand. Heen en terug blijven schema en data gelijk;
    alleen alembic_version verandert."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    await fill_0009(settings)
    await migrate(settings, command.upgrade, "0013", fts5=fts5)
    before = await _sqlite_state(settings)
    await migrate(settings, command.upgrade, "0014", fts5=fts5)
    assert await _version(settings) == ["0014"]
    assert await _sqlite_state(settings) == before
    await migrate(settings, command.downgrade, "0013", fts5=fts5)
    assert await _version(settings) == ["0013"]
    assert await _sqlite_state(settings) == before
