"""De plannen van de SQL die FtsEngine echt maakt (bouwontwerp fase 6, Keuze 4 en 4a; §3.6),
met en zonder ANALYZE, voor beide vormen van de bereiken: tot en met 64 wortels een OR, daarboven
json_each. Elke vraag komt van de bouwers in app/search/engine.py en gaat echt door E.run; het
plan is dat van precies die tekst met die parameters, onder assert_plan (tests/search/plans.py):
nooit AUTOMATIC, nooit een SEARCH op parent_id.

- Ronde A, ronde B (ook verder na een rowid) en de stukken van namen via trigram beginnen bij FTS
  (fts_node of fts_name) en zoeken de node op via ix_node_num; ook met filters erbij, en met en
  zonder iets van een ander in de bereiken (de wacht op de OCR van foto's).
- Stukken van namen met alleen woorden van 1–2 tekens (geen trigram): de code kiest, zoals bij
  alleen filters. Vanaf de paden (ix_node_mpath) als het bereik klein is; anders op num, zonder
  sorteren, zodat hij stopt na genoeg rijen.
- Alleen filters (Keuze 4a): de code kiest, niet de planner. Langs de tijd (ix_node_live_mod),
  bij één soort ix_node_ftype_mod, en vanaf de paden (ix_node_mpath) als het bereik klein is,
  ook met een filter op soort of datum (zonder INDEXED BY koos de planner daar zelf de index op
  soort of tijd).
- is_small telt via ix_node_mpath; boven 64 wortels telt hij niet (dan altijd langs de tijd).
- De eigen albums voor suggesties langs (parent_id, kind): een album staat buiten de boom. Met
  owner_id erbij koos de planner ix_node_owner_taken, alle nodes van de eigenaar langs.

e2e/slim.sh draait dit ook in python:3.12-slim (SQLite 3.46.1).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import event

from app.config import Settings
from app.db import get_engine
from app.models import User
from app.search import bench
from app.search import engine as E
from app.search import suggest as S
from app.search.access import Ranges, end_of
from app.search.query import parse
from app.search.types import SearchParams
from tests.search.migratie import MADE
from tests.search.plans import assert_plan

pytestmark = pytest.mark.anyio

BOOM = bench.Plan(n=1_500, tekens=1_000, seed=4)
RUIM_MS = 60_000
K = 31
WORTELS = [3, 64, 65]


@pytest.fixture(scope="module")
def databases(tmp_path_factory) -> dict[bool, Path]:
    """Twee mappen met dezelfde boom: zonder en met ANALYZE."""
    kaal = tmp_path_factory.mktemp("plannen-kaal")
    met = tmp_path_factory.mktemp("plannen-analyze")
    bench.maak(kaal, BOOM)
    with (
        sqlite3.connect(kaal / "platform.db") as bron,
        sqlite3.connect(met / "platform.db") as doel,
    ):
        bron.backup(doel)
        doel.execute("ANALYZE")
    return {False: kaal, True: met}


@pytest.fixture(params=[False, True], ids=["zonder-analyze", "met-analyze"])
async def conn(request, databases):
    """Een verbinding van de app (get_engine, met zijn PRAGMA's) die de SQL bijhoudt."""
    settings = Settings(data_dir=databases[request.param], jwt_secret="test" * 8)
    MADE.append(settings)
    engine = get_engine(settings)
    gezien: list[tuple[str, tuple]] = []

    def bij(conn, cursor, statement, parameters, context, executemany):
        gezien.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", bij)
    try:
        async with engine.connect() as c:
            c.info["gezien"] = gezien
            yield c
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", bij)


async def _scope(conn, wortels: int, shared: bool, p: SearchParams) -> E.Scope:
    sql = "SELECT mpath, owner_id FROM node WHERE kind = 'dir' AND dead = 0 ORDER BY num LIMIT ?"
    rijen = (await conn.exec_driver_sql(sql, (wortels,))).all()
    assert len(rijen) == wortels
    eigenaar = rijen[0][1]
    user = User(id=uuid.UUID(hex=eigenaar) if isinstance(eigenaar, str) else eigenaar, role="user")
    return E.Scope(user, Ranges(sorted((p_, end_of(p_)) for p_, _ in rijen), shared), p)


async def _plan(conn, sel, **eisen):
    """Voer sel uit zoals de app (E.run), en eis het plan van de laatste vraag."""
    gezien = conn.info["gezien"]
    gezien.clear()
    ronde = await E.run(conn, sel, RUIM_MS)
    assert not ronde.hit
    sql, args = gezien[-1]
    return await conn.run_sync(lambda c: assert_plan(c, sql, args, **eisen))


def _vanaf_de_paden(plan, wortels: int) -> None:
    """De buitenste lus is een MULTI-INDEX OR met per wortel één SEARCH op ix_node_mpath.
    (Plan.eerste kijkt alleen naar SCAN en SEARCH op het bovenste niveau; dit plan heeft daar
    de OR, met de SEARCH eronder.)"""
    boven = [(id_, tekst) for id_, ouder, tekst in plan.regels if ouder == 0]
    assert boven and boven[0][1] == "MULTI-INDEX OR", plan.tekst()
    onder = {id_ for id_, ouder, _ in plan.regels if ouder == boven[0][0]}
    zoeken = [tekst for _, ouder, tekst in plan.regels if ouder in onder]
    assert len(zoeken) == wortels, plan.tekst()
    for tekst in zoeken:
        assert (
            tekst.startswith("SEARCH n USING ")
            and "INDEX ix_node_mpath (mpath>? AND mpath<?)" in tekst
        ), plan.tekst()


FILTERS = {
    "geen": {},
    "pdf": {"types": ["pdf"]},
    "pdf-en-foto": {"types": ["pdf", "image"]},
    "datum": {"date_from": date(2020, 1, 1), "date_to": date(2026, 12, 31)},
    "tag": {"tags": [uuid.UUID(int=7)]},
}


@pytest.mark.parametrize("wortels", WORTELS)
@pytest.mark.parametrize("shared", [False, True], ids=["eigen", "gedeeld"])
@pytest.mark.parametrize("q", ["factuur", "de", "rapp jaar"])
@pytest.mark.parametrize("filt", ["geen", "pdf", "datum"])
async def test_ronde_a_en_b_beginnen_bij_fts(conn, wortels, shared, q, filt):
    scope = await _scope(conn, wortels, shared, SearchParams(q=q, **FILTERS[filt]))
    parsed = parse(q)
    eisen = {"require_first": "fts_node VIRTUAL TABLE", "require": ["ix_node_num (num=?)"]}
    plan = await _plan(conn, E.ranked_select(parsed, scope, K), **eisen)
    # Boven 64 wortels de bereiken als json_each (r), per node één keer gevraagd
    assert ("SCAN r VIRTUAL TABLE" in plan) is (wortels > E.MAX_OR), plan.tekst()
    await _plan(conn, E.stream_select(parsed, scope, K, None, 0), **eisen)
    plan = await _plan(conn, E.stream_select(parsed, scope, K, 500, K), **eisen)
    # Verder na een rowid: FTS5 begint daar (de > in de index van de virtuele tabel)
    assert plan.eerste.endswith(">"), plan.tekst()


@pytest.mark.parametrize("wortels", WORTELS)
@pytest.mark.parametrize("shared", [False, True], ids=["eigen", "gedeeld"])
@pytest.mark.parametrize("filt", ["geen", "pdf"])
async def test_stukken_van_namen(conn, wortels, shared, filt):
    p = SearchParams(q="aalcij", **FILTERS[filt])
    scope = await _scope(conn, wortels, shared, p)
    parsed = parse(p.q)
    assert parsed.names is not None
    for without in ((), (1, 2, 3)):
        await _plan(
            conn,
            E.names_select(parsed, scope, K, 0, without),
            require_first="fts_name VIRTUAL TABLE",
            require=["ix_node_num (num=?)"],
        )


@pytest.mark.parametrize("wortels", WORTELS)
@pytest.mark.parametrize("filt", ["geen", "pdf", "datum"])
async def test_stukken_van_namen_met_korte_woorden(conn, wortels, filt):
    """Geen woord van ≥ 3 tekens: geen trigram (Keuze 5 en 6). Dan kiest de code, zoals bij
    alleen filters: vanaf de paden als het bereik klein is, anders op num zonder sorteren (de
    LIMIT stopt hem na genoeg rijen, en bij de grens zijn er al rijen). Zonder INDEXED BY koos
    de planner bij 64 wortels zelf de scan op num, en bij 3 de paden."""
    p = SearchParams(q="de", **FILTERS[filt])
    scope = await _scope(conn, wortels, True, p)
    parsed = parse(p.q)
    assert parsed.names is None
    small = await E.is_small(conn, scope.roots)
    assert small is (wortels <= E.MAX_OR)
    if small:
        sel = E.names_select(parsed, scope, K, 0, (1, 2), small=True)
        _vanaf_de_paden(await _plan(conn, sel), wortels)
    sel = E.names_select(parsed, scope, K, 0, (1, 2), small=False)
    plan = await _plan(conn, sel, require_first="SCAN n USING INDEX ix_node_num")
    assert "TEMP B-TREE" not in plan, plan.tekst()


@pytest.mark.parametrize("wortels", WORTELS)
@pytest.mark.parametrize("filt", list(FILTERS))
async def test_alleen_filters_langs_de_tijd(conn, wortels, filt):
    p = SearchParams(**FILTERS[filt])
    scope = await _scope(conn, wortels, True, p)
    index = "ix_node_ftype_mod (ftype=?)" if filt == "pdf" else "ix_node_live_mod"
    plan = await _plan(conn, E.filters_select(scope, K, small=False), require_first=index)
    # De index geeft de volgorde: sorteren hoeft niet, hij stopt na genoeg treffers
    assert "TEMP B-TREE" not in plan, plan.tekst()


@pytest.mark.parametrize("wortels", [w for w in WORTELS if w <= E.MAX_OR])
@pytest.mark.parametrize("filt", list(FILTERS))
async def test_alleen_filters_vanaf_de_paden(conn, wortels, filt):
    p = SearchParams(**FILTERS[filt])
    scope = await _scope(conn, wortels, True, p)
    assert await E.is_small(conn, scope.roots)
    _vanaf_de_paden(await _plan(conn, E.filters_select(scope, K, small=True)), wortels)


@pytest.mark.parametrize("wortels", WORTELS)
async def test_is_small_telt_langs_de_paden(conn, wortels):
    scope = await _scope(conn, wortels, True, SearchParams())
    gezien = conn.info["gezien"]
    gezien.clear()
    klein = await E.is_small(conn, scope.roots)
    if wortels > E.MAX_OR:
        # Boven 64 wortels zou tellen een SCAN over heel ix_node_mpath zijn (S10): niet tellen
        assert klein is False and gezien == []
        return
    assert klein is True
    sql, args = gezien[-1]
    plan = await conn.run_sync(lambda c: assert_plan(c, sql, args))
    # De telling zelf staat in een subquery (CO-ROUTINE), met de OR daaronder
    zoeken = [t for _, _, t in plan.regels if t.startswith("SEARCH ")]
    assert len(zoeken) == wortels, plan.tekst()
    assert all("COVERING INDEX ix_node_mpath (mpath>? AND mpath<?)" in t for t in zoeken), (
        plan.tekst()
    )


async def test_eigen_albums_langs_het_hoogste_niveau(conn):
    rij = (await conn.exec_driver_sql("SELECT owner_id FROM node WHERE kind = 'dir'")).first()
    eigenaar = uuid.UUID(hex=rij[0]) if isinstance(rij[0], str) else rij[0]
    gezien = conn.info["gezien"]
    gezien.clear()
    await conn.execute(S.own_albums(eigenaar))
    sql, args = gezien[-1]
    # parent_id=? is hier juist de bedoeling: alleen het hoogste niveau, waar de albums staan
    await conn.run_sync(
        lambda c: assert_plan(
            c,
            sql,
            args,
            require_first="INDEX ix_node_parent_order (parent_id=? AND kind=?)",
            forbid_index_terms=(),
        )
    )
