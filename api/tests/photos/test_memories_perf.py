"""§4.4: GET /photos/memories p95 ≤ 50 ms (bouwontwerp fase 6, Keuze 41; Z6; marker zoek_perf,
job zoek-perf in python:3.12-slim met SQLite 3.46.1).

1. De boom van §3.6 via de echte migratie (bench.maak, --n 100000 --seed 6). De eigenaar (b00)
   krijgt er EXTRA foto's bij, verspreid over 20 jaar (zo heeft elke dag in elk jaar foto's,
   en zijn er dagen met meer dan PER_DAY), en drie regels om te verbergen: een dag, een map en
   een persoon. Zo meet de poort ook de regels en de grens per dag, niet alleen een lege lijst.
2. De app zoals in productie: python -m app.serve (tests/search/server.py), rate limits uit.
3. De client in dit proces (de server is een ander proces): 20 vragen om op te warmen, dan 3
   rondes van 100 dagen van 2026 (vaste seed, ook 28 en 29 februari); p95 = de mediaan van de p95
   van de rondes. Hard: p95 ≤ 50 ms en geen fout. Rapport: p50, p99, max, foto's per antwoord.

ZOEK_PERF_N maakt de boom kleiner en ZOEK_PERF_HERINNERINGEN het aantal extra foto's (alleen om
dit bestand zelf te proberen); ZOEK_PERF_UIT is een map voor de JSON en het verslag (CI: het
artefact).
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import sqlite3
import statistics
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from app.search import bench
from app.text import fold
from tests.search.server import App, zonder_2fa

pytestmark = pytest.mark.zoek_perf

N = int(os.environ.get("ZOEK_PERF_N", "100000"))
EXTRA = int(os.environ.get("ZOEK_PERF_HERINNERINGEN", "20000"))
POORT = 50.0
RONDES = 3
PER_RONDE = 100
JAREN = 20


def _p(waarden: list[float], q: float) -> float:
    rij = sorted(waarden)
    return rij[min(len(rij) - 1, max(0, round(q * len(rij)) - 1))]


def _meer_fotos(db: Path, naam: str, n: int) -> dict[str, int]:
    """n foto's erbij voor naam (in een nieuwe map Herinneringen, met de inhoud van een van zijn
    foto's), en drie regels om te verbergen. Rechtstreeks in SQLite: de triggers van 0010 zetten
    mpath, dead en de index."""
    rng = random.Random("herinneringen")
    nu = bench.DAG.strftime("%Y-%m-%d %H:%M:%S.%f")
    with contextlib.closing(sqlite3.connect(db)) as con, con:
        owner, root = con.execute(
            "SELECT id, root_id FROM user WHERE email = ?", (f"{naam}@{bench.DOMEIN}",)
        ).fetchone()
        sha = con.execute(
            "SELECT blob_id FROM node WHERE owner_id = ? AND mime = 'image/jpeg' LIMIT 1", (owner,)
        ).fetchone()[0]
        mappen = [uuid.uuid4().hex for _ in range(5)]
        for i, map_ in enumerate(mappen):
            con.execute(
                "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, "
                "modified_at, search_name) VALUES (?, ?, ?, ?, 'dir', 0, ?, ?, ?)",
                (map_, root, owner, f"Herinneringen {i}", nu, nu, f"herinneringen {i}"),
            )
        rijen = []
        for i in range(n):
            genomen = bench.DAG - timedelta(days=rng.uniform(0, 365 * JAREN), seconds=i)
            naam_ = genomen.strftime(f"HER_%Y%m%d_%H%M%S_{i}.jpg")
            rijen.append((
                uuid.uuid4().hex, rng.choice(mappen), owner, naam_, "file", "image/jpeg",
                2_000_000, sha, nu, nu, fold(naam_),
                genomen.strftime("%Y-%m-%d %H:%M:%S.%f"), int(genomen.strftime("%Y%m%d")),
            ))  # fmt: skip
        con.executemany(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
            "created_at, modified_at, search_name, taken_at, taken_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rijen,
        )
        persoon = con.execute(
            "SELECT id FROM person WHERE owner_id = ? ORDER BY id LIMIT 1", (owner,)
        ).fetchone()
        regels = [("day", "2019-03-14"), ("folder", str(uuid.UUID(mappen[0])))]
        if persoon:
            regels.append(("person", str(uuid.UUID(persoon[0]))))
        con.executemany(
            "INSERT INTO memory_hide (user_id, kind, ref) VALUES (?, ?, ?)",
            [(owner, kind, ref) for kind, ref in regels],
        )
        fotos = con.execute(
            "SELECT count(*) FROM node WHERE owner_id = ? AND taken_day IS NOT NULL AND dead = 0",
            (owner,),
        ).fetchone()[0]
    return {"extra": n, "fotos_eigenaar": fotos, "regels": len(regels)}


@pytest.fixture(scope="module")
def boom(tmp_path_factory):
    data = tmp_path_factory.mktemp("herinneringen")
    begin = time.monotonic()
    uit = bench.maak(data, bench.Plan(n=N, seed=6))
    zonder_2fa(data / "platform.db")
    extra = _meer_fotos(data / "platform.db", bench.ROLLEN["eigenaar"], EXTRA)
    extra["seconden"] = round(time.monotonic() - begin, 1)
    print(uit.kort())
    return data, extra


def _dagen(seed: int) -> list[date]:
    rng = random.Random(f"dagen:{seed}")
    begin = date(2026, 1, 1)
    dagen = [begin + timedelta(days=rng.randrange(365)) for _ in range(PER_RONDE - 2)]
    return [*dagen, date(2026, 2, 28), date(2028, 2, 29)]


def test_herinneringen_binnen_50_ms(boom, tmp_path):
    data, extra = boom
    versie = sqlite3.sqlite_version
    app = App(data, tmp_path / "server.log")
    app.start()
    rondes: list[list[float]] = []
    aantallen: list[int] = []
    fout: list[str] = []
    try:
        with httpx.Client(base_url=app.url, trust_env=False, timeout=30) as client:
            login = {
                "email": f"{bench.ROLLEN['eigenaar']}@{bench.DOMEIN}",
                "password": bench.WACHTWOORD,
            }
            r = client.post("/api/v1/auth/login", json=login)
            assert r.status_code == 200, r.text
            client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            for dag in _dagen(0)[:20]:
                client.get("/api/v1/photos/memories", params={"day": dag.isoformat()})
            for ronde in range(1, RONDES + 1):
                tijden = []
                for dag in _dagen(ronde):
                    begin = time.perf_counter()
                    r = client.get("/api/v1/photos/memories", params={"day": dag.isoformat()})
                    tijden.append((time.perf_counter() - begin) * 1000)
                    if r.status_code != 200:
                        fout.append(f"{dag}: {r.status_code}")
                        continue
                    aantallen.append(sum(len(y["items"]) for y in r.json()["years"]))
                rondes.append(tijden)
    finally:
        app.stop()
    p95 = statistics.median(_p(t, 0.95) for t in rondes)
    alle = [t for ronde in rondes for t in ronde]
    uitkomst = {
        "sqlite": versie,
        "boom": {"n": N, **extra},
        "p95": p95,
        "p95_per_ronde": [_p(t, 0.95) for t in rondes],
        "p50": _p(alle, 0.50),
        "p99": _p(alle, 0.99),
        "max": max(alle),
        "fotos_per_antwoord": {
            "gemiddeld": statistics.mean(aantallen) if aantallen else 0,
            "max": max(aantallen, default=0),
        },
        "fout": fout,
    }
    niet = []
    if p95 > POORT:
        niet.append(f"p95 {p95:.1f} > {POORT:.0f}")
    if fout:
        niet.append(f"fouten {len(fout)}")
    uitkomst["poort"] = {"gehaald": not niet, "niet": niet}
    verslag = "\n".join([
        "=== herinneringen ===",
        f"SQLite {versie}, boom n={N} seed=6, eigenaar met {extra['fotos_eigenaar']} foto's "
        f"({extra['extra']} extra over {JAREN} jaar), {extra['regels']} regels om te verbergen",
        f"GET /photos/memories: p95 {p95:.1f} ms (rondes "
        + ", ".join(f"{x:.1f}" for x in uitkomst["p95_per_ronde"])
        + f"), p50 {uitkomst['p50']:.1f}, p99 {uitkomst['p99']:.1f}, max {uitkomst['max']:.1f}",
        f"foto's per antwoord: gemiddeld {uitkomst['fotos_per_antwoord']['gemiddeld']:.0f}, "
        f"max {uitkomst['fotos_per_antwoord']['max']}",
        "poort (p95 ≤ 50 ms): gehaald" if not niet else "poort: NIET: " + "; ".join(niet),
        "=== einde herinneringen ===",
    ])  # fmt: skip
    print(verslag)
    if map_ := os.environ.get("ZOEK_PERF_UIT"):
        Path(map_).mkdir(parents=True, exist_ok=True)
        (Path(map_) / "herinneringen.json").write_text(json.dumps(uitkomst, indent=2) + "\n")
        (Path(map_) / "herinneringen.txt").write_text(verslag + "\n")
    assert aantallen and max(aantallen) > 0, "de meting moet foto's teruggeven"
    assert not niet, niet
