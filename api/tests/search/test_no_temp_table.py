"""Geen tijdelijke tabel in app/search (bouwontwerp fase 6, Keuze 14; §4.1).

Boven 64 wortels kwamen de bereiken eerst in een tijdelijke tabel op een verbinding uit de pool:
een volgende gebruiker kon zo andermans wortels krijgen (S18). Nu gaan ze als één
JSON-parameter met json_each. Deze wachter houdt CREATE TEMP(ORARY) en een Table met
prefixes=["TEMPORARY"] uit app/search.
"""

from __future__ import annotations

import re
from pathlib import Path

SEARCH = Path(__file__).resolve().parents[2] / "app" / "search"
TEMP = re.compile(r"CREATE\s+TEMP(ORARY)?\b|prefixes\s*=\s*\[\s*['\"]TEMP", re.IGNORECASE)


def temp_tables(source: str) -> list[str]:
    return [m.group(0) for m in TEMP.finditer(source)]


def test_geen_tijdelijke_tabel_in_app_search():
    files = sorted(SEARCH.rglob("*.py"))
    assert len(files) > 10
    found = {str(f.relative_to(SEARCH)): temp_tables(f.read_text(encoding="utf-8")) for f in files}
    assert {k: v for k, v in found.items() if v} == {}


def test_de_wachter_ziet_ze():
    assert temp_tables("CREATE TEMP TABLE r(x)")
    assert temp_tables("create  temporary table r (x)")
    assert temp_tables('sa.Table("r", meta, prefixes=["TEMPORARY"])')
    assert not temp_tables("CREATE VIRTUAL TABLE m USING fts5(x)")
    assert not temp_tables("SELECT value FROM json_each(:roots)")
