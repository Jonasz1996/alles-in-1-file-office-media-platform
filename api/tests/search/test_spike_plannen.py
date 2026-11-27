"""Spike S4 (bouwontwerp fase 6, §3.7; J12, Keuze 4 en 4a): de plannen van de vragen, met en
zonder ANALYZE, op de SQLite van deze python en in de job zoek-perf in python:3.12-slim (3.46.1).

De boom en de SQL komen uit tools/zoekbench/bench.py (S9): dezelfde vormen die de bench meet.
Elke vorm: de eerste stap ligt vast (FTS, of de index die de vorm noemt), geen AUTOMATIC (een
index die SQLite per vraag zelf bouwt), en nooit een zoekstap op parent_id (een boom per map
aflopen). Z0-e2e heeft assert_plan in tests/search/plans.py; Z1 en Z3 zetten hun echte SQL
daaronder. Deze test is het bewijs van de spike, met de bevindingen erbij:

- de vorm voor bijna-duplicaten uit Keuze 4a is zonder ANALYZE een kruisproduct (de twee
  IN-lijsten worden gelijkheden op de sleutel (a, b) van dup_pair); de vorm met EXISTS niet;
- zonder INDEXED BY kiest de planner voor "alleen filters" na ANALYZE een ander plan. De code
  kiest daarom zelf (Keuze 4a), met INDEXED BY.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.search import spike

VERBODEN = ("AUTOMATIC",)
FTS = "SCAN fts_node VIRTUAL TABLE"
NAMEN = "SCAN fts_name VIRTUAL TABLE"
NUM = "SEARCH n USING INDEX ix_node_num (num=?)"


@pytest.fixture(scope="module")
def databases(tmp_path_factory):
    bench = spike.zoekbench()
    data = tmp_path_factory.mktemp("plannen")
    kaal, met = data / "kaal.db", data / "analyze.db"
    con = sqlite3.connect(kaal, isolation_level=None)
    bench.maak_schema(con)
    bench.vul(con, bench.Boom(3_000, seed=4))
    con.close()
    with sqlite3.connect(kaal) as bron, sqlite3.connect(met) as doel:
        bron.backup(doel)
        doel.execute("ANALYZE")
    return {False: kaal, True: met}


@pytest.fixture(params=[False, True], ids=["zonder-analyze", "met-analyze"])
def con(request, databases):
    c = sqlite3.connect(databases[request.param])
    yield c
    c.close()


def plan(con, sql: str, args) -> list[str]:
    return [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args)]


def eis(con, sql: str, args, *, eerst: str, nodig=(), verboden=()) -> list[str]:
    regels = plan(con, sql, args)
    tekst = "\n".join(regels)
    stappen = [r for r in regels if r.startswith(("SCAN ", "SEARCH "))]
    assert stappen and eerst in stappen[0], tekst
    for stuk in nodig:
        assert stuk in tekst, f"{stuk!r} ontbreekt\n{tekst}"
    for stuk in (*VERBODEN, *verboden):
        assert stuk not in tekst, f"{stuk!r} staat erin\n{tekst}"
    for r in regels:
        assert not (r.startswith("SEARCH ") and "parent_id=" in r), tekst
    con.execute(sql, args).fetchall()  # en hij draait
    return regels


def _paden(con) -> dict[str, list[str]]:
    wortels = dict(
        con.execute("SELECT u.display_name, n.mpath FROM user u JOIN node n ON n.id = u.root_id")
    )
    mappen = [
        r[0]
        for r in con.execute(
            "SELECT mpath FROM node WHERE kind = 'dir' AND dead = 0 ORDER BY num LIMIT 65"
        )
    ]
    assert len(mappen) == 65
    return {
        "1 wortel": [wortels["b00"]],
        "30 wortels": [wortels[f"b{i:02}"] for i in range(30)],
        "65 wortels": mappen,
    }


def _ik(con) -> str:
    return con.execute("SELECT id FROM user WHERE display_name = 'b00'").fetchone()[0]


@pytest.mark.parametrize("wortels", ["1 wortel", "30 wortels", "65 wortels"])
def test_zoeken_begint_bij_fts(con, wortels):
    bench = spike.zoekbench()
    bereik, args = bench.bereiken(_paden(con)[wortels])
    json = ("SCAN r ",) if wortels == "65 wortels" else ()
    geen_json = () if json else ("SCAN r ",)
    vraag = {**args, "expr": '"factuur"*', "names": '"fac"', "n": 31}
    eis(con, bench.sql_a(bereik), vraag, eerst=FTS, nodig=(NUM, *json), verboden=geen_json)
    eis(con, bench.sql_b(bereik), vraag, eerst=FTS, nodig=(NUM, *json), verboden=geen_json)
    eis(con, bench.sql_c(bereik), vraag, eerst=NAMEN, nodig=(NUM, *json), verboden=geen_json)


FILTERS = {
    "langs de tijd": ("ix_node_live_mod", ""),
    "type": ("ix_node_ftype_mod", "AND n.ftype = 'pdf'"),
    "laatste jaar": ("ix_node_live_mod", "AND n.modified_at >= '2025-11-03'"),
}


@pytest.mark.parametrize("wortels", ["1 wortel", "30 wortels", "65 wortels"])
@pytest.mark.parametrize("filt", list(FILTERS))
def test_alleen_filters_met_indexed_by(con, wortels, filt):
    """Langs de tijd: de partiële index geeft de volgorde, dus geen TEMP B-TREE en een stop na
    genoeg treffers."""
    bench = spike.zoekbench()
    index, extra = FILTERS[filt]
    bereik, args = bench.bereiken(_paden(con)[wortels])
    sql = (f"SELECT n.id FROM node AS n INDEXED BY {index} WHERE n.dead = 0 "
           f"AND n.kind <> 'album' AND {bereik} {extra} "
           "ORDER BY n.modified_at DESC, n.num DESC LIMIT 31")  # fmt: skip
    eis(con, sql, args, eerst=f"n USING INDEX {index}", verboden=("TEMP B-TREE",))


@pytest.mark.parametrize("wortels", ["1 wortel", "30 wortels", "65 wortels"])
def test_alleen_filters_vanaf_de_paden(con, wortels):
    """Het kleine bereik (≤ 20.000 nodes): vanaf de paden, en dan sorteren. Boven 64 wortels
    (json_each) is dat geen bereik meer maar de hele index: dan altijd langs de tijd."""
    bench = spike.zoekbench()
    bereik, args = bench.bereiken(_paden(con)[wortels])
    sql = (f"SELECT n.id FROM node AS n INDEXED BY ix_node_mpath WHERE n.dead = 0 "
           f"AND n.kind <> 'album' AND {bereik} "
           "ORDER BY n.modified_at DESC, n.num DESC LIMIT 31")  # fmt: skip
    stap = "SCAN" if wortels == "65 wortels" else "SEARCH"
    eis(con, sql, args, eerst=f"{stap} n USING INDEX ix_node_mpath")


def test_de_kaart(con):
    """Zonder join op node (review Z0, haalbaar F1 en F2): photo_geo houdt dead en taken_at van
    de node bij. Eén bereik in ix_photo_geo_tile, die alles heeft (dekkend): geen rij per foto."""
    sql = (
        "SELECT g.x16 >> :s AS cx, g.y16 >> :s AS cy, count(*) AS n, max(g.taken_at) AS t, "
        "g.node_id AS cover FROM photo_geo AS g WHERE g.owner_id = :me AND g.dead = 0 "
        "AND g.x16 BETWEEN :x0 AND :x1 AND g.y16 BETWEEN :y0 AND :y1 GROUP BY cx, cy LIMIT 501"
    )
    args = {"me": _ik(con), "s": 6, "x0": 0, "x1": 65535, "y0": 0, "y1": 65535}
    tegel = "g USING COVERING INDEX ix_photo_geo_tile (owner_id=? AND dead=? AND x16>? AND x16<?)"
    eis(con, sql, args, eerst=tegel, verboden=("node",))


def test_precies_gelijk(con):
    sql = (
        "SELECT n.blob_id, count(*) FROM node AS n WHERE n.owner_id = :me AND n.dead = 0 "
        "AND n.kind = 'file' GROUP BY n.blob_id HAVING count(*) > 1"
    )
    eis(con, sql, {"me": _ik(con)}, eerst="SEARCH n USING INDEX ix_node_owner_blob",
        verboden=("TEMP B-TREE",))  # fmt: skip


BIJNA_EXISTS = (
    "SELECT p.a, p.b, p.dist, p.level FROM dup_pair AS p WHERE EXISTS (SELECT 1 FROM node AS n "
    "WHERE n.owner_id = :me AND n.blob_id = p.a AND n.dead = 0 AND n.kind = 'file') AND EXISTS "
    "(SELECT 1 FROM node AS n WHERE n.owner_id = :me AND n.blob_id = p.b AND n.dead = 0 "
    "AND n.kind = 'file')"
)
BIJNA_ONTWERP = (
    "WITH mine(sha) AS (SELECT DISTINCT blob_id FROM node WHERE owner_id = :me AND dead = 0 "
    "AND kind = 'file' AND ftype = 'image') "
    "SELECT p.a, p.b, p.dist, p.level FROM dup_pair AS p WHERE p.a IN mine AND p.b IN mine"
)


def test_bijna_gelijk_met_exists(con):
    """Alle paren langs (dup_pair is klein: een paar per bijna-gelijke foto), per kant één
    zoekstap in ix_node_owner_blob. Bij 55.000 foto's ±5 ms (S10)."""
    eis(con, BIJNA_EXISTS, {"me": _ik(con)}, eerst="SCAN p",
        nodig=("ix_node_owner_blob (owner_id=? AND blob_id=?)",),
        verboden=("a=? AND b=?",))  # fmt: skip


def test_bijna_gelijk_zoals_in_het_ontwerp_is_een_kruisproduct(databases):
    """Zonder ANALYZE (zo draait de app) maakt SQLite van de twee IN-lijsten gelijkheden op de
    sleutel (a, b): voor elke a uit mine elke b uit mine. Bij 55.000 foto's stopte die vraag
    niet binnen 2 s (S10). Daarom de vorm met EXISTS."""
    con = sqlite3.connect(databases[False])
    try:
        tekst = "\n".join(plan(con, BIJNA_ONTWERP, {"me": _ik(con)}))
        assert "SEARCH p USING PRIMARY KEY (a=? AND b=?)" in tekst, tekst
        assert tekst.count("LIST SUBQUERY") == 2, tekst
        # Ze geven wel dezelfde paren
        ik = {"me": _ik(con)}
        assert sorted(con.execute(BIJNA_ONTWERP, ik)) == sorted(con.execute(BIJNA_EXISTS, ik))
    finally:
        con.close()


def test_zonder_indexed_by_kiest_de_planner_zelf(databases, capsys):
    """Geen eis, een meting voor spike.md: het plan voor "alleen filters" zonder INDEXED BY,
    met en zonder ANALYZE."""
    uit = {}
    for analyze, pad in databases.items():
        con = sqlite3.connect(pad)
        try:
            for filt, (_, extra) in FILTERS.items():
                bench = spike.zoekbench()
                bereik, args = bench.bereiken(_paden(con)["1 wortel"])
                sql = (
                    "SELECT n.id FROM node AS n WHERE n.dead = 0 AND n.kind <> 'album' "
                    f"AND {bereik} {extra} ORDER BY n.modified_at DESC, n.num DESC LIMIT 31"
                )
                uit[(filt, analyze)] = plan(con, sql, args)[0]
        finally:
            con.close()
    with capsys.disabled():
        print(f"\nS4 op SQLite {sqlite3.sqlite_version}, alleen filters zonder INDEXED BY:")
        for (filt, analyze), eerste in sorted(uit.items()):
            print(f"  {filt}, {'met' if analyze else 'zonder'} ANALYZE: {eerste}")
