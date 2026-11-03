"""tools/zoekbench/bench.py (spike S9): het bevroren schema in de bench is dat van head, en de
bench draait klein, met en zonder triggers, en ruimt op.

De bench is alleen standaardbibliotheek en draait los van de app (`python -I - --los`), dus hij
importeert app.search.schema niet. Deze test houdt de kopie gelijk: verandert een trigger, de
view, een FTS-tabel of een index in 0010, dan faalt hij tot DDL in bench.py is bijgewerkt.
"""

from __future__ import annotations

import json
import signal
import sqlite3

import pytest

from tests.search import spike

# In de bench alleen met de kolommen die de view en de triggers lezen
INGEKORT = {"media_item", "photo_person"}


@pytest.fixture(scope="module")
def bench():
    return spike.zoekbench()


def _objecten(con: sqlite3.Connection) -> dict[str, tuple[str, str, str]]:
    return {
        naam: (soort, tabel, sql)
        for soort, naam, tabel, sql in con.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
    }


def test_het_schema_van_de_bench_is_dat_van_head(bench, tmp_path):
    spike.head_db(tmp_path / "head.db", fts5=True)
    con = sqlite3.connect(tmp_path / "head.db")
    head = _objecten(con)
    con.close()
    con = sqlite3.connect(":memory:")
    bench.maak_schema(con)
    kopie = _objecten(con)
    con.close()

    for naam, (soort, _, sql) in kopie.items():
        if naam in INGEKORT:
            continue
        assert naam in head, f"{soort} {naam} staat in de bench maar niet op head"
        assert sql == head[naam][2], f"{soort} {naam} is anders dan op head"

    # Alles wat zoeken raakt staat erin: elke trigger, de view, de FTS-tabellen en de indexen
    # op node, photo_geo en dup_pair
    def nodig(objecten):
        return {
            naam
            for naam, (soort, tabel, _) in objecten.items()
            if soort in ("trigger", "view")
            or naam in ("fts_node", "fts_name")
            or (soort == "index" and tabel in ("node", "photo_geo", "dup_pair"))
        }

    assert nodig(kopie) == nodig(head)
    triggers = {n for n, (soort, _, _) in head.items() if soort == "trigger"}
    for naam in triggers:
        tabel = head[naam][1]
        assert tabel in kopie, f"trigger {naam} hoort bij {tabel}, en die tabel ontbreekt"


def test_de_bench_draait_klein_en_ruimt_op(bench, tmp_path, capsys):
    uit = tmp_path / "uit.json"
    voor = signal.getsignal(signal.SIGTERM)
    rc = bench.main(["--los", "--n", "2000", "--map", str(tmp_path), "--json", str(uit)])
    assert rc == 0
    assert signal.getsignal(signal.SIGTERM) is voor
    assert sorted(p.name for p in tmp_path.iterdir()) == ["uit.json"]
    data = json.loads(uit.read_text())
    assert data["opgeruimd"] is True
    assert data["bouw"]["nodes"] > 2000
    for naam in ("eigenaar", "alles"):
        z = data["zoeken"][naam]
        assert z["vragen"] > 0
        assert z["per soort"]
    assert data["zoeken"]["eigenaar"]["wortels"] == 1
    assert data["zoeken"]["alles"]["wortels"] == bench.GEBRUIKERS
    tekst = capsys.readouterr().out
    assert "=== zoekbench ===" in tekst
    assert "opgeruimd: ja" in tekst


def test_vullen_via_de_triggers_geeft_dezelfde_index(bench, tmp_path):
    """--triggers (S7): elke rij komt via node_ai en bt_ai; daarna is de index gelijk aan een
    herbouw."""
    pad = tmp_path / "bench.db"
    con = sqlite3.connect(pad, isolation_level=None)
    bench.maak_schema(con)
    boom = bench.Boom(800, seed=7)
    bouw = bench.vul(con, boom, triggers=True)
    assert bouw["nodes"] == len(boom.nodes)
    aantal = con.execute("SELECT count(*) FROM node WHERE num IS NOT NULL").fetchone()[0]
    assert aantal == len(boom.nodes)
    con.close()
    assert spike.herbouw_verschil(pad) == {}
