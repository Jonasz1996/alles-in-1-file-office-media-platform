"""assert_plan (tests/search/plans.py) en de vormen van Keuze 4 en 4a op een boom van bench.py,
met en zonder ANALYZE. De SQL staat hier zoals in het bouwontwerp; Z1 en Z3 zetten in
test_search_plans.py de SQL die hun code echt maakt onder dezelfde eisen."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.search import bench
from tests.search.plans import assert_plan, plan_van

BOOM = bench.Plan(n=1_500, tekens=1_000, seed=4)


@pytest.fixture(scope="module")
def databases(tmp_path_factory) -> dict[bool, Path]:
    data = tmp_path_factory.mktemp("plannen")
    bench.maak(data, BOOM)
    kaal = data / "platform.db"
    met = data / "analyze.db"
    with sqlite3.connect(kaal) as bron, sqlite3.connect(met) as doel:
        bron.backup(doel)
        doel.execute("ANALYZE")
    return {False: kaal, True: met}


@pytest.fixture(params=[False, True], ids=["zonder-analyze", "met-analyze"])
def con(request, databases):
    c = sqlite3.connect(databases[request.param])
    yield c
    c.close()


def _eind(pad: str) -> str:
    """Het einde van het bereik: de laatste / wordt 0 (het teken erna)."""
    return pad[:-1] + "0"


def _wortels(con, n: int) -> list[str]:
    sql = "SELECT mpath FROM node WHERE kind = 'dir' AND dead = 0 ORDER BY num LIMIT ?"
    return [r[0] for r in con.execute(sql, (n,))]


def _bereiken(con, n: int) -> tuple[str, list]:
    """De bereiken van Keuze 4: tot en met 64 een OR, daarboven json_each."""
    paden = _wortels(con, n)
    assert len(paden) == n
    if n <= 64:
        sql = " OR ".join("(n.mpath >= ? AND n.mpath < ?)" for _ in paden)
        return f"({sql})", [x for p in paden for x in (p, _eind(p))]
    sql = ("EXISTS (SELECT 1 FROM json_each(?) AS r "
           "WHERE n.mpath >= r.value ->> '$[0]' AND n.mpath < r.value ->> '$[1]')")  # fmt: skip
    return sql, [json.dumps([[p, _eind(p)] for p in paden])]


def _zoeken(con, wortels: int):
    bereik, args = _bereiken(con, wortels)
    sql = f"""
        SELECT n.id, bm25(fts_node, 10.0, 6.0, 1.0, 0.8, 0.5, 0.8) AS s
        FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid
        WHERE fts_node MATCH ?
          AND n.kind <> 'album' AND n.dead = 0
          AND {bereik}
        ORDER BY s, n.num
        LIMIT ?"""
    return sql, ['"factuur"*', *args, 31]


def _filters_tijd(con):
    bereik, args = _bereiken(con, 3)
    sql = f"""
        SELECT n.id FROM node AS n INDEXED BY ix_node_live_mod
        WHERE n.dead = 0 AND n.kind <> 'album' AND {bereik}
        ORDER BY n.modified_at DESC, n.num DESC
        LIMIT ?"""
    return sql, [*args, 31]


def _filters_paden(con):
    bereik, args = _bereiken(con, 3)
    sql = f"""
        SELECT n.id FROM node AS n
        WHERE n.dead = 0 AND n.kind <> 'album' AND {bereik}
        ORDER BY n.modified_at DESC, n.num DESC
        LIMIT ?"""
    return sql, [*args, 31]


def _filters_soort(con):
    bereik, args = _bereiken(con, 3)
    sql = f"""
        SELECT n.id FROM node AS n
        WHERE n.dead = 0 AND n.ftype = ? AND n.kind <> 'album' AND {bereik}
        ORDER BY n.modified_at DESC, n.num DESC
        LIMIT ?"""
    return sql, ["image", *args, 31]


def _eigenaar(con) -> str:
    return con.execute("SELECT owner_id FROM node WHERE kind = 'file' LIMIT 1").fetchone()[0]


def _kaart(con):
    # Zonder join op node: photo_geo houdt dead en taken_at van de node bij (own_live_geo;
    # review Z0, haalbaar F1 en F2). De cover is de foto met de nieuwste taken_at (max)
    sql = """
        SELECT g.x16 >> ? AS cx, g.y16 >> ? AS cy, count(*) AS n,
               max(g.taken_at) AS t, g.node_id AS cover
        FROM photo_geo AS g
        WHERE g.owner_id = ? AND g.dead = 0
          AND g.x16 BETWEEN ? AND ? AND g.y16 BETWEEN ? AND ?
        GROUP BY cx, cy
        LIMIT 501"""
    return sql, [6, 6, _eigenaar(con), 33_000, 34_000, 21_000, 22_000]


def _dubbel(con):
    sql = """
        SELECT n.blob_id, count(*) FROM node AS n
        WHERE n.owner_id = ? AND n.dead = 0 AND n.kind = 'file'
        GROUP BY n.blob_id HAVING count(*) > 1"""
    return sql, [_eigenaar(con)]


def _bijna(con):
    # De vorm met EXISTS van "Herzien na de spike (S4, S10)": de vorm met IN mine uit Keuze 4a is
    # zonder ANALYZE een kruisproduct (test_spike_plannen.py)
    sql = """
        SELECT p.a, p.b, p.dist, p.level FROM dup_pair AS p
        WHERE EXISTS (SELECT 1 FROM node AS n WHERE n.owner_id = ? AND n.blob_id = p.a
                      AND n.dead = 0 AND n.kind = 'file')
          AND EXISTS (SELECT 1 FROM node AS n WHERE n.owner_id = ? AND n.blob_id = p.b
                      AND n.dead = 0 AND n.kind = 'file')"""
    ik = _eigenaar(con)
    return sql, [ik, ik]


FTS = "fts_node VIRTUAL TABLE"
# vorm -> (maker, de eerste stap, ook nodig, ook verboden)
VORMEN = {
    "zoeken, 3 wortels": (lambda c: _zoeken(c, 3), FTS, ["ix_node_num"], []),
    "zoeken, 64 wortels": (lambda c: _zoeken(c, 64), FTS, ["ix_node_num"], ["json_each"]),
    "zoeken, 65 wortels": (lambda c: _zoeken(c, 65), FTS, ["ix_node_num", "SCAN r"], []),
    "filters langs de tijd": (_filters_tijd, "ix_node_live_mod", [], ["TEMP B-TREE"]),
    "filters vanaf de paden": (_filters_paden, "", ["ix_node_mpath"], []),
    "filter op soort": (_filters_soort, "ix_node_ftype_mod", [], ["TEMP B-TREE"]),
    "kaart": (
        _kaart,
        "COVERING INDEX ix_photo_geo_tile (owner_id=? AND dead=? AND x16>? AND x16<?)",
        [],
        ["node"],
    ),
    "duplicaten": (_dubbel, "ix_node_owner_blob", [], []),
    "bijna-duplicaten": (_bijna, "", ["ix_node_owner_blob"], []),
}


@pytest.mark.parametrize("vorm", sorted(VORMEN))
def test_de_vormen_van_keuze_4_en_4a(con, vorm):
    maak, eerste, nodig, verboden = VORMEN[vorm]
    sql, args = maak(con)
    assert_plan(con, sql, args, require_first=eerste or None, require=nodig,
                forbid=["AUTOMATIC", *verboden])  # fmt: skip
    con.execute(sql, args).fetchall()  # en hij draait


def test_zoeken_vindt_iets_binnen_de_wortels(con):
    sql, args = _zoeken(con, 65)
    assert con.execute(sql, args).fetchall()


# --- assert_plan zelf -----------------------------------------------------------------------


def test_assert_plan_ziet_een_automatische_index(con):
    sql = "SELECT n.id FROM node AS n JOIN share AS s ON s.label = n.name"
    assert "AUTOMATIC" in plan_van(con, sql)
    with pytest.raises(AssertionError, match="AUTOMATIC"):
        assert_plan(con, sql)


def test_assert_plan_ziet_een_boom_aflopen_per_map(con):
    sql = "SELECT id FROM node WHERE parent_id = ? AND name = ?"
    with pytest.raises(AssertionError, match=r"parent_id=\?"):
        assert_plan(con, sql, ["00", "x"])
    assert_plan(con, sql, ["00", "x"], forbid_index_terms=())


def test_assert_plan_ziet_een_verkeerde_eerste_stap(con):
    sql, args = _zoeken(con, 3)
    plan = assert_plan(con, sql, args, require_first=FTS)
    assert FTS in plan.eerste
    # Zonder CROSS JOIN mag SQLite kiezen; met een vraag over node alleen begint hij nooit bij FTS
    with pytest.raises(AssertionError, match="de eerste stap"):
        assert_plan(con, "SELECT id FROM node WHERE num = ?", [1], require_first=FTS)
    with pytest.raises(AssertionError, match="ontbreekt"):
        assert_plan(con, sql, args, require=["ix_bestaat_niet"])
