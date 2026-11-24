"""De SQL van de zoekvragen op SQLite is die van 0.8.1 (review van PR #6, F17).

0.8.2 bouwt de vragen met stukken per dialect (FtsMatch, InRanges, …). Op SQLite moet de tekst
precies blijven wat hij was: dezelfde vraag, dezelfde haakjes, voor wie op één node draait. De
verwachte tekst hieronder komt van de code van 0.8.1 (05c3f391^), met dezelfde invoer. Alleen
compileren, zonder database: deze test draait ook in de PostgreSQL-run.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import User
from app.search.access import Ranges
from app.search.engine import (
    Scope,
    filters_select,
    is_small,
    names_select,
    ranked_select,
    stream_select,
)
from app.search.query import parse
from app.search.types import SearchParams

pytestmark = pytest.mark.anyio

U = User(id=uuid.UUID(int=1), root_id=uuid.UUID(int=2))
EEN = Ranges([("a/", "a0")], shared=False)
TWEE = Ranges([("a/", "a0"), ("b/", "b0")], shared=True)


def _sql(sel) -> str:
    return str(sel.compile(dialect=sqlite.dialect()))


def _scope(roots: Ranges, q: str, **kw) -> Scope:
    return Scope(user=U, roots=roots, p=SearchParams(q=q, **kw))


def test_ronde_a_met_een_gedeelde_map():
    sel = ranked_select(parse("rode fiets"), _scope(TWEE, "rode fiets"), 30)
    assert _sql(sel) == (
        "SELECT n.id, n.num, n.owner_id, n.search_name, CASE WHEN (n.owner_id = ?) THEN "
        "bm25(fts_node, 10.0, 6.0, 1.0, 0.8, 0.5, 0.8) ELSE bm25(fts_node, 10.0, 6.0, 1.0, 0.8, "
        "0.5, 0.0) END AS s \n"
        "FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid \n"
        "WHERE (fts_node MATCH ?) AND n.dead = 0 AND n.kind != 'album' AND ((n.mpath >= ? AND "
        "n.mpath < ?) OR (n.mpath >= ? AND n.mpath < ?)) AND n.id != ? AND (n.owner_id = ? OR "
        "NOT (EXISTS (SELECT * \n"
        "FROM photo_ocr \n"
        "WHERE photo_ocr.node_id = n.id)) OR EXISTS (SELECT 1 FROM fts_node AS f2 WHERE f2.rowid "
        "= n.num AND f2.fts_node MATCH ?)) ORDER BY s, n.num\n"
        " LIMIT ? OFFSET ?"
    )


def test_ronde_b_met_een_filter():
    sel = stream_select(parse("zo"), _scope(EEN, "zo", types=["image"]), 30, 5, 0)
    assert _sql(sel) == (
        "SELECT n.id, n.num, n.owner_id, n.search_name \n"
        "FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid \n"
        "WHERE (fts_node MATCH ?) AND fts_node.rowid > ? AND n.dead = 0 AND n.kind != 'album' AND "
        "((n.mpath >= ? AND n.mpath < ?)) AND n.id != ? AND n.ftype = ?\n"
        " LIMIT ? OFFSET ?"
    )


def test_stukken_van_namen_via_fts_name():
    sel = names_select(parse("brief"), _scope(EEN, "brief"), 30, 0, small=False)
    assert _sql(sel) == (
        "SELECT n.id, n.num, n.owner_id, n.search_name \n"
        "FROM fts_name CROSS JOIN node AS n ON n.num = fts_name.rowid \n"
        "WHERE (fts_name MATCH ?) AND NOT EXISTS (SELECT 1 FROM json_each(?) AS w WHERE "
        "instr(n.search_name, w.value) = 0) AND n.dead = 0 AND n.kind != 'album' AND ((n.mpath "
        ">= ? AND n.mpath < ?)) AND n.id != ? ORDER BY fts_name.rowid\n"
        " LIMIT ? OFFSET ?"
    )


def test_stukken_van_namen_zonder_index_op_de_naam():
    sel = names_select(parse("zo"), _scope(EEN, "zo", types=["image"]), 30, 0, [3, 4], small=True)
    assert _sql(sel) == (
        "SELECT n.id, n.num, n.owner_id, n.search_name \n"
        "FROM node AS n INDEXED BY ix_node_mpath \n"
        "WHERE NOT EXISTS (SELECT 1 FROM json_each(?) AS w WHERE instr(n.search_name, w.value) = "
        "0) AND n.num NOT IN (SELECT value FROM json_each(?)) AND n.dead = 0 AND n.kind != "
        "'album' AND ((n.mpath >= ? AND n.mpath < ?)) AND n.id != ? AND n.ftype = ? ORDER BY "
        "n.num\n"
        " LIMIT ? OFFSET ?"
    )


def test_alleen_filters():
    sel = filters_select(_scope(EEN, ""), 30, False)
    assert _sql(sel) == (
        "SELECT n.id, n.num, n.owner_id, n.search_name \n"
        "FROM node AS n INDEXED BY ix_node_live_mod \n"
        "WHERE n.dead = 0 AND n.kind != 'album' AND ((n.mpath >= ? AND n.mpath < ?)) AND n.id != "
        "? ORDER BY n.modified_at DESC, n.num DESC\n"
        " LIMIT ? OFFSET ?"
    )


async def test_klein_telt_zoals_in_081():
    db = create_async_engine("sqlite+aiosqlite://")
    gezien: list[str] = []
    event.listen(db.sync_engine, "before_cursor_execute", lambda *a: gezien.append(a[2]))
    try:
        async with db.connect() as conn:
            await conn.exec_driver_sql("CREATE TABLE node (mpath TEXT)")
            gezien.clear()
            assert await is_small(conn, TWEE)
    finally:
        await db.dispose()
    assert gezien == [
        "SELECT count(*) FROM (SELECT 1 FROM node AS n WHERE ((n.mpath >= ? AND n.mpath < ?) OR "
        "(n.mpath >= ? AND n.mpath < ?)) LIMIT 20001)"
    ]
