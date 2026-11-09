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
- De filters van Z3 (place, country, camera; app/search/filters_photo.py) zoeken per node op de
  sleutel van photo_geo of blob_meta, met een vraag en zonder; nooit een SCAN van die tabellen.
- De duplicaten van Z3 (app/dupes/groups.py): precies gelijk en de leden van een groep via
  ix_node_owner_blob, de paren van bijna gelijk met per kant één zoekstap daarin (de vorm met
  EXISTS van S10, nooit de vorm met IN mine).
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

from app import photo_hash
from app.config import Settings
from app.db import get_engine
from app.dupes import groups as G
from app.dupes import phash
from app.models import User
from app.search import bench, filters_photo
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


@pytest.fixture(autouse=True)
def _filters():
    """Een test eerder in de run kan filters.reset() gedaan hebben (test_contracts.py)."""
    filters_photo.register()


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
    # Z3 (app/search/filters_photo.py): eigen, levende bestanden met een plek of een camera
    "plaats": {"place": 2759794},
    "land": {"country": "NL"},
    "camera": {"camera": "canon eos"},
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


# De filters van Z3 kijken per node op de sleutel van hun tabel, nooit een SCAN ervan
PER_NODE = {
    "plaats": ("photo_geo", "(node_id=?)"),
    "land": ("photo_geo", "(node_id=?)"),
    "camera": ("blob_meta", "(blob_sha=?)"),
}


def _per_node(plan, filt: str) -> None:
    tabel, sleutel = PER_NODE[filt]
    regels = [t for _, _, t in plan.regels]
    assert any(t.startswith(f"SEARCH {tabel} ") and sleutel in t for t in regels), plan.tekst()
    assert not any(t.startswith(f"SCAN {tabel}") for t in regels), plan.tekst()


@pytest.mark.parametrize("wortels", WORTELS)
@pytest.mark.parametrize("filt", list(PER_NODE))
async def test_de_filters_van_z3_per_node(conn, wortels, filt):
    """Met een vraag (ronde A, begint bij FTS) en met alleen filters (langs de tijd, en vanaf de
    paden als het bereik klein is)."""
    p = SearchParams(q="factuur", **FILTERS[filt])
    scope = await _scope(conn, wortels, True, p)
    eisen = {"require_first": "fts_node VIRTUAL TABLE", "require": ["ix_node_num (num=?)"]}
    _per_node(await _plan(conn, E.ranked_select(parse(p.q), scope, K), **eisen), filt)
    scope = await _scope(conn, wortels, True, SearchParams(**FILTERS[filt]))
    sel = E.filters_select(scope, K, small=False)
    _per_node(await _plan(conn, sel, require_first="ix_node_live_mod"), filt)
    if wortels <= E.MAX_OR:
        _per_node(await _plan(conn, E.filters_select(scope, K, small=True)), filt)


async def _laatste_plan(conn, **eisen):
    sql, args = conn.info["gezien"][-1]
    return await conn.run_sync(lambda c: assert_plan(c, sql, args, **eisen))


async def test_de_duplicaten_van_z3(conn):
    """De vragen van GET /duplicates zoals groups.py ze stelt, voor de eigenaar van de bench."""
    rij = (
        await conn.exec_driver_sql(
            'SELECT id FROM "user" WHERE email = ?',
            (f"{bench.ROLLEN['eigenaar']}@{bench.DOMEIN}",),
        )
    ).first()
    user = User(id=uuid.UUID(hex=rij[0]) if isinstance(rij[0], str) else rij[0], role="user")
    gezien = conn.info["gezien"]
    # Precies gelijk: GROUP BY over zijn eigen, levende bestanden, langs (owner_id, blob_id)
    gezien.clear()
    exact = await G.exact(conn, user)
    await _laatste_plan(conn, require_first="INDEX ix_node_owner_blob (owner_id=?)")
    # Bijna gelijk: alle paren langs, per kant één SEARCH op (owner_id, blob_id)
    gezien.clear()
    paren = await G.near_pairs(conn, user)
    plan = await _laatste_plan(conn)
    zoeken = [t for _, _, t in plan.regels if "ix_node_owner_blob (owner_id=? AND blob_id=?)" in t]
    assert len(zoeken) == 2, plan.tekst()
    assert not any("LIST SUBQUERY" in t for _, _, t in plan.regels), plan.tekst()
    # De leden van een pagina: per inhoud één SEARCH op (owner_id, blob_id)
    shas = [g.shas[0] for g in exact[:5]] + [s for a, b, _d, _lv in paren[:5] for s in (a, b)]
    assert shas, "de bench heeft duplicaten bij de eigenaar"
    gezien.clear()
    await G.members(conn, user, shas)
    await _laatste_plan(conn, require_first="INDEX ix_node_owner_blob (owner_id=? AND blob_id=?)")


async def test_de_kandidaten_van_de_phash(conn):
    """photo_hash._scan (review golf 3, veilig F3), zoals candidates() hem vraagt: langs de vier
    indexen van de stukken (MULTI-INDEX OR) en dan sorteren op sha, nooit de hele blob_hash langs
    de primaire sleutel om de volgorde. Met een eigenaar per rij één SEARCH op ix_node_blob."""
    rij = (
        await conn.exec_driver_sql(
            'SELECT id FROM "user" WHERE email = ?',
            (f"{bench.ROLLEN['eigenaar']}@{bench.DOMEIN}",),
        )
    ).first()
    eigenaar = uuid.UUID(hex=rij[0]) if isinstance(rij[0], str) else rij[0]
    (waarde,) = (
        await conn.exec_driver_sql(
            "SELECT h.phash FROM blob_hash AS h JOIN node AS n ON n.blob_id = h.blob_sha "
            "WHERE h.state = 'ok' AND n.owner_id = ? LIMIT 1",
            (rij[0],),
        )
    ).first()
    gezien = conn.info["gezien"]
    for owner in (None, eigenaar):
        gezien.clear()
        gevonden = await photo_hash._scan(conn, waarde & phash.MASK, owner)
        assert gevonden, owner
        plan = await _laatste_plan(conn, require=("MULTI-INDEX OR",))
        for i in range(4):
            assert f"INDEX ix_hash_{i} (h{i}=?)" in plan, plan.tekst()
        assert not any(t.startswith("SCAN") for _, _, t in plan.regels), plan.tekst()
        if owner is not None:
            assert "INDEX ix_node_blob (blob_id=?)" in plan, plan.tekst()


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
