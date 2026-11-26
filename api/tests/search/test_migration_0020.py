"""Migratie 0020: de rangvector van zoeken op PostgreSQL (0.8.3, W4R-2; docs/fase-6/i3/pg-fts.md).

- PostgreSQL: 0019 → 0020 → 0019 geeft hetzelfde schema, en weer naar 0020 hetzelfde als de
  eerste keer. Een grote rij die er al was, krijgt bij de upgrade rvec en rlen; een kleine niet.
- De trigger zet rvec en rlen bij elke nieuwe of andere tsv (ook via fts_refresh), en haalt ze
  weg als de rij weer klein wordt.
- cf_rank_len telt zoals ts_rank (normalisatie 1): met rvec en rlen geeft de rang precies
  ts_rank(tsv, q, 1) als alle plekken van de vraag vooraan staan.
- cf_rank_head houdt de plekken tot de grens, met hun gewicht.
- SQLite: 0020 doet niets.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic import command

from app import db as app_db
from app.config import Settings
from tests.pgboom import boom, rows, run
from tests.search.migratie import migrate, settings_at
from tests.test_migration_0013 import _schema, _sqlite_state, _version

pytestmark = pytest.mark.anyio

_PATH = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0020_rangvector.py"
_SPEC = importlib.util.spec_from_file_location("m0020", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
M0020 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M0020)

W = "'{0.05, 0.1, 0.6, 1.0}'"
# De rang zoals engine.FtsRank hem op PostgreSQL vraagt (zonder het minteken)
RANK = (
    f"CASE WHEN rvec IS NULL THEN ts_rank({W}, tsv, to_tsquery('simple', :q), 1) "
    f"ELSE (ts_rank({W}, rvec, to_tsquery('simple', :q), 0) / (ln(rlen + 1) / ln(2)))::real END"
)


def _lang(n: int) -> str:
    """Een tekst van n woorden: vooraan 'begin', achteraan 'staart', daartussen woorden die
    elkaar herhalen (zoals een groot document)."""
    words = ["begin"] + [f"woord{i % 997}" for i in range(n - 2)] + ["staart"]
    return " ".join(words)


@pytest.fixture
async def at_0019(tmp_path, database_url):
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8,
                        database_url=database_url)  # fmt: skip
    await migrate(settings, command.upgrade, "0019")
    yield settings
    await app_db.get_engine(settings).dispose()
    app_db._engine.cache_clear()


async def _tekst(settings: Settings, node, body: str) -> None:
    await run(
        settings,
        "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
        "SELECT blob_id, :b, '', '', 0, now() FROM node WHERE id = :n "
        "ON CONFLICT (blob_sha) DO UPDATE SET body = excluded.body",
        n=node,
        b=body,
    )


async def _rij(settings: Settings, node) -> tuple:
    [found] = await rows(
        settings,
        "SELECT f.rvec IS NOT NULL, f.rlen, cf_rank_len(f.tsv) FROM fts_node f "
        "JOIN node n ON n.num = f.rowid WHERE n.id = :n",
        n=node,
    )
    return found


@pytest.mark.pg
async def test_heen_terug_heen(at_0019):
    settings = at_0019
    ids = await boom(settings)
    await _tekst(settings, ids["verslag"], _lang(5000))
    await _tekst(settings, ids["notities"], "kort verslag")
    fresh = await _schema(settings)

    await migrate(settings, command.upgrade, "0020")
    schema = await _schema(settings)
    assert ("fts_node_rank", "fts_node") in schema["triggers"]
    assert {"cf_rank_len", "cf_rank_head", "fts_rank_trg"} <= {f[0] for f in schema["functions"]}
    # De grote rij die er al was, is gevuld; de kleine niet
    big, rlen, total = await _rij(settings, ids["verslag"])
    assert big and rlen == total > M0020.RANK_UPTO
    assert (await _rij(settings, ids["notities"]))[:2] == (False, None)

    await migrate(settings, command.downgrade, "0019")
    assert await _schema(settings) == fresh

    await migrate(settings, command.upgrade, "0020")
    assert await _schema(settings) == schema
    assert (await _rij(settings, ids["verslag"]))[0]


@pytest.mark.pg
async def test_de_trigger_volgt_tsv(at_0019):
    settings = at_0019
    ids = await boom(settings)
    await migrate(settings, command.upgrade, "0020")
    assert (await _rij(settings, ids["notities"]))[:2] == (False, None)
    # Groot via fts_refresh (de trigger van blob_text)
    await _tekst(settings, ids["notities"], _lang(3000))
    big, rlen, total = await _rij(settings, ids["notities"])
    assert big and rlen == total
    # Weer klein: rvec en rlen weg
    await _tekst(settings, ids["notities"], "kort")
    assert (await _rij(settings, ids["notities"]))[:2] == (False, None)


@pytest.mark.pg
async def test_de_rang_van_een_grote_rij(at_0019):
    settings = at_0019
    ids = await boom(settings)
    await migrate(settings, command.upgrade, "0020")
    await _tekst(settings, ids["notities"], _lang(3000))

    async def rang(q: str) -> tuple[float, float]:
        [found] = await rows(
            settings,
            f"SELECT {RANK}, ts_rank({W}, tsv, to_tsquery('simple', :q), 1) FROM fts_node f "
            "JOIN node n ON n.num = f.rowid WHERE n.id = :n",
            q=q,
            n=ids["notities"],
        )
        return found

    # Vooraan (de naam en 'begin'): alle plekken in rvec, dus precies ts_rank(tsv, q, 1)
    for q in ("notities", "begin", "begin & notities", "'begi':*"):
        ours, exact = await rang(q)
        assert ours == pytest.approx(exact, rel=1e-6), q
    # Alleen achteraan: nog een treffer (tsv), maar zonder rang
    ours, exact = await rang("staart")
    assert ours == 0 and exact > 0


@pytest.mark.pg
async def test_cf_rank_head_en_len(at_0019):
    settings = at_0019
    await migrate(settings, command.upgrade, "0020")
    v = "'a':1A,5,2000 'b':3000 'it''s':2B 'x\\\\y':7C 'z'"
    [(head, n)] = await rows(
        settings,
        "SELECT cf_rank_head(CAST(:v AS tsvector), 1024)::text, cf_rank_len(CAST(:v AS tsvector))",
        v=v,
    )
    assert head == "'a':1A,5 'it''s':2B 'x\\\\y':7C"
    # zoals cnt_length: elke plek, een woord zonder plek één keer
    assert n == 3 + 1 + 1 + 1 + 1


async def test_op_sqlite_doet_0020_niets(tmp_path):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0019")
    before = await _sqlite_state(settings)
    await migrate(settings, command.upgrade, "0020")
    assert await _version(settings) == ["0020"]
    await migrate(settings, command.downgrade, "0019")
    assert await _sqlite_state(settings) == before
