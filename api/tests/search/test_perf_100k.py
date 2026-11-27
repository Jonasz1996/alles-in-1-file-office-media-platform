"""§4.4: zoeken over 100.000 bestanden binnen 200 ms (bouwontwerp fase 6; marker zoek_perf, job
zoek-perf in python:3.12-slim met SQLite 3.46.1). Met PLATFORM_TEST_DATABASE_URL hetzelfde op
PostgreSQL, met de zoekindex van 0014 (release 0.8.2, P5; job zoek-perf-pg met PostgreSQL 17).

1. De boom van §3.6 via de echte migratie (bench.maak, --n 100000 --seed 6), met het profiel
   "groot" erbovenop (200 documenten van 1 MB in de boom van de eigenaar): de poort voor snip
   geldt ook daar, en de andere poorten worden zo strenger, niet losser. Op PostgreSQL in een
   eigen, nieuwe database naast die van PLATFORM_TEST_DATABASE_URL (weg na de test), met
   dezelfde boom en dezelfde INSERTs; de triggers van 0013 en 0014 vullen pad en index.
2. De app zoals in productie: python -m app.serve (WAL, busy_timeout 5000; op PostgreSQL met
   PLATFORM_DATABASE_URL naar die database), met
   PLATFORM_SEARCH_TIMING=1 voor Server-Timing en PLATFORM_RATE_LIMITS=false (een testserver;
   200 vragen na elkaar halen anders de snelheidsgrens per gebruiker).
3. De client in een ander proces: e2e/perf/zoek_100k.py, alleen de standaardbibliotheek. De zes
   rollen, 20 vragen om op te warmen, 3 rondes van de 200 vragen van §4.3 (tests/search/
   vragen.tsv); p95 = de mediaan van de p95 van de rondes. Daarna 4 clients tegelijk met
   /health ernaast, en de eerste vraag na een herstart.

Hard (de poorten van Z1): zoeken p95 ≤ 200; volgende pagina's p95 ≤ 200; alleen filters
p95 ≤ 150 (S10 mat een SQL-p95 ≤ 100: 0,7 ms op 3.46.1); suggesties p95 ≤ 50; snip p95 ≤ 40;
4 tegelijk p95 ≤ 300; /health daarnaast p95 ≤ 50; en geen fout (5xx of 429). Rapport: p99, max,
hoe vaak ronde B of C, de eerste vraag na een herstart (doel ≤ 300).

ZOEK_PERF_N maakt de boom kleiner (alleen om dit bestand zelf te proberen); ZOEK_PERF_UIT is een
map voor de JSON en het verslag (CI: het artefact).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.search import bench
from tests.conftest import PG_URL, _pg_database
from tests.search.server import App, zonder_2fa

pytestmark = pytest.mark.zoek_perf

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "e2e" / "perf" / "zoek_100k.py"
VRAGEN = Path(__file__).with_name("vragen.tsv")
N = int(os.environ.get("ZOEK_PERF_N", "100000"))

POORTEN = {
    "zoeken": 200.0,
    "volgende": 200.0,
    "filters": 150.0,
    "suggesties": 50.0,
}
SNIP = 40.0
TEGELIJK = 300.0
HEALTH = 50.0


@pytest.fixture(scope="module")
def boom(tmp_path_factory):
    data = tmp_path_factory.mktemp("boom100k")
    naam = "zoek100k_" + uuid.uuid4().hex
    url = asyncio.run(_pg_database(naam)) if PG_URL else ""
    try:
        begin = time.monotonic()
        uit = bench.maak(data, bench.Plan(n=N, seed=6, profiel="groot"), database_url=url)
        uit.seconden["totaal"] = time.monotonic() - begin
        zonder_2fa(data / "platform.db", url)
        print(uit.kort())
        yield data, uit, url
    finally:
        if url:
            asyncio.run(_pg_database(naam, drop=True))


def _postgresql(url: str) -> str:
    """De versie van de server. En cf_fold van 0014 moet elke letter kennen (LC_CTYPE van de
    database, runbook cluster): anders mist de index letters buiten ASCII en meet dit een index
    die niet goed is (de app waarschuwt dan alleen). En de app moet met de index van 0014 zoeken
    (engine_for): fts_node heel en geen herbouw die loopt, anders meet dit LIKE."""
    import asyncpg

    async def lees() -> str:
        con = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
        try:
            assert await con.fetchval("SELECT cf_fold('Éλ')") == "eλ", await con.fetchval(
                "SELECT datctype FROM pg_database WHERE datname = current_database()"
            )
            assert await con.fetchval("SELECT cursor FROM search_state WHERE id = 1") is None
            index, nodes = await con.fetchrow(
                "SELECT (SELECT count(*) FROM fts_node), (SELECT count(*) FROM node)"
            )
            assert index == nodes, (index, nodes)
            return await con.fetchval("SELECT current_setting('server_version')")
        finally:
            await con.close()

    return asyncio.run(lees())


def _client(app: App, *args: str, timeout: float) -> dict:
    uit = app.log.with_suffix(".json")
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--url", app.url, "--vragen", str(VRAGEN),
         "--domein", bench.DOMEIN, "--wachtwoord", bench.WACHTWOORD, "--json", str(uit), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )  # fmt: skip
    print(r.stdout)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(uit.read_text())


def test_100k_binnen_200_ms(boom, tmp_path):
    data, gemaakt, url = boom
    env = {"PLATFORM_SEARCH_TIMING": "1"}
    if url:
        database = {"postgresql": _postgresql(url)}
        env["PLATFORM_DATABASE_URL"] = url
    else:
        versie = sqlite3.sqlite_version
        assert tuple(int(x) for x in versie.split(".")) >= (3, 46, 1), versie
        database = {"sqlite": versie}
    app = App(data, tmp_path / "server.log", env=env)
    app.start()
    try:
        alles = _client(app, timeout=3600)
        app.stop()
        app.start()
        alles["eerste"] = _client(app, "--deel", "eerste", timeout=120)["eerste"]
    finally:
        app.stop()
    alles.update(database)
    alles["boom"] = {"n": N, "tellingen": dict(gemaakt.tellingen), "seconden": gemaakt.seconden}
    fout = []
    for rol, r in alles["rollen"].items():
        for soort, grens in POORTEN.items():
            if r[soort]["p95"] > grens:
                fout.append(f"{rol} {soort} p95 {r[soort]['p95']:.1f} > {grens:.0f}")
            if r[soort]["fout"]:
                fout.append(f"{rol} {soort} fouten {r[soort]['fout']}")
        for soort in ("zoeken", "volgende"):
            if r[soort]["snip_p95"] > SNIP:
                fout.append(f"{rol} snip ({soort}) p95 {r[soort]['snip_p95']:.1f} > {SNIP:.0f}")
    t = alles["tegelijk"]
    if t["p95"] > TEGELIJK:
        fout.append(f"4 tegelijk p95 {t['p95']:.1f} > {TEGELIJK:.0f}")
    if t["health_p95"] > HEALTH:
        fout.append(f"/health tijdens 4 tegelijk p95 {t['health_p95']:.1f} > {HEALTH:.0f}")
    if t["fout"]:
        fout.append(f"4 tegelijk fouten {t['fout']}")
    alles["poorten"] = {"gehaald": not fout, "niet": fout}
    verslag = _verslag(alles)
    print(verslag)
    if map_ := os.environ.get("ZOEK_PERF_UIT"):
        Path(map_).mkdir(parents=True, exist_ok=True)
        (Path(map_) / "zoek_100k.json").write_text(json.dumps(alles, indent=2) + "\n")
        (Path(map_) / "zoek_100k.txt").write_text(verslag + "\n")
    assert not fout, fout


def _verslag(alles: dict) -> str:
    if "sqlite" in alles:
        kop, database = "=== §4.4 ===", f"SQLite {alles['sqlite']}"
    else:
        kop, database = "=== §4.4 op PostgreSQL ===", f"PostgreSQL {alles['postgresql']}"
    regels = [
        kop,
        f"{database}, boom n={alles['boom']['n']} seed=6 profiel=groot",
        "rol: zoeken p95/p99/max (B, C) | volgende p95 | filters p95 | suggesties p95 | snip p95",
    ]
    for rol, r in alles["rollen"].items():
        z = r["zoeken"]
        regels.append(
            f"{rol}: {z['p95']:.1f}/{z['p99']:.1f}/{z['max']:.1f} ({z['B']}, {z['C']}) | "
            f"{r['volgende']['p95']:.1f} | {r['filters']['p95']:.1f} | "
            f"{r['suggesties']['p95']:.1f} | {z['snip_p95']:.1f}"
        )
    t = alles["tegelijk"]
    regels.append(
        f"4 tegelijk: p95 {t['p95']:.1f}, p99 {t['p99']:.1f}, max {t['max']:.1f}; /health p95 "
        f"{t['health_p95']:.1f}"
    )
    regels.append(f"eerste vraag na een herstart: {alles['eerste']['ms']:.1f} ms (doel ≤ 300)")
    p = alles["poorten"]
    regels.append("poorten: gehaald" if p["gehaald"] else "poorten: NIET: " + "; ".join(p["niet"]))
    regels.append("=== einde §4.4 ===")
    return "\n".join(regels)
