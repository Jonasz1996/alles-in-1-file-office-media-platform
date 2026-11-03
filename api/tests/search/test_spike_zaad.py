"""Spike S7 (bouwontwerp fase 6, §3.7 en §4.4): het zaad van 100.000 in CI, binnen 3 minuten.

Via de echte migratie (head_db) en de echte triggers: de boom van tools/zoekbench/bench.py (S9)
met gewone INSERTs, de tekst vóór de nodes, en elke node_ai schrijft zijn rij in FTS. Daarna is
de index heel (de integrity-check van FTS5). Marker zoek_perf: alleen in de job zoek-perf.

Het zaad van §3.6 zelf (app/search/bench.py) komt van Z0-e2e; zolang dat niet op deze tak staat,
meet de job zoek-perf het hier met dezelfde vorm. Staat het er wel, dan meet de job het ook
(`python -m app.search.bench`), met de tijd in het blok `=== kort ===`.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from tests.search import spike

GRENS_S = 180


@pytest.mark.zoek_perf
def test_het_zaad_van_100000_via_de_triggers(tmp_path):
    bench = spike.zoekbench()
    pad = tmp_path / "platform.db"
    start = time.perf_counter()
    spike.head_db(pad, fts5=True)
    migratie = time.perf_counter() - start
    con = sqlite3.connect(pad, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    boom = bench.Boom(100_000, seed=6)
    plan = time.perf_counter() - start - migratie
    bouw = bench.vul(con, boom, triggers=True)
    con.close()
    duur = time.perf_counter() - start
    mb = sum(f.stat().st_size for f in tmp_path.iterdir()) / 1e6
    print(
        f"\nS7 op SQLite {sqlite3.sqlite_version}: {bouw['nodes']} nodes, "
        f"{bouw['tekens'] / 1e6:.0f} M tekens in {duur:.1f} s (migratie {migratie:.1f} s, "
        f"boom {plan:.1f} s, tekst {bouw['tekst s']} s, nodes met triggers {bouw['nodes s']} s), "
        f"{mb:.0f} MB"
    )
    assert spike.herbouw_verschil(pad, termen=False) == {}
    assert duur < GRENS_S, f"het zaad duurde {duur:.1f} s, de grens is {GRENS_S} s"
