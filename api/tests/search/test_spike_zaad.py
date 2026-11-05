"""Spike S7 (bouwontwerp fase 6, §3.7 en §4.4): het zaad van 100.000 in CI, binnen 3 minuten.

Het zaad van §3.6 zelf: app/search/bench.py (Z0-e2e), via de echte migratie (maak() draait
upgrade) en de echte triggers, met gewone INSERTs; elke node_ai schrijft zijn rij in FTS.
Daarna is de index heel (de integrity-check van FTS5). Marker zoek_perf: alleen in de job
zoek-perf. (Tot de samenvoeging van Z0 mat de spike dit met de boom van tools/zoekbench, omdat
app/search/bench.py nog niet op zijn tak stond.)
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from app.search import bench
from tests.search import spike

GRENS_S = 180


@pytest.mark.zoek_perf
def test_het_zaad_van_100000_via_de_triggers(tmp_path):
    start = time.perf_counter()
    uit = bench.maak(tmp_path, bench.Plan(n=100_000, seed=6))
    duur = time.perf_counter() - start
    t, s = uit.tellingen, uit.seconden
    mb = sum(f.stat().st_size for f in tmp_path.iterdir() if f.is_file()) / 1e6
    print(
        f"\nS7 op SQLite {sqlite3.sqlite_version}: {t['bestanden']} bestanden, {t['mappen']} "
        f"mappen, {t['tekens'] / 1e6:.0f} M tekens in {duur:.1f} s ("
        + ", ".join(f"{k} {v:.1f} s" for k, v in s.items())
        + f"), {mb:.0f} MB"
    )
    assert t["bestanden"] == 100_000
    assert spike.herbouw_verschil(tmp_path / "platform.db", termen=False) == {}
    assert duur < GRENS_S, f"het zaad duurde {duur:.1f} s, de grens is {GRENS_S} s"
