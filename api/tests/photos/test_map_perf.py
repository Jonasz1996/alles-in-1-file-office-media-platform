"""§4.4: GET /photos/map p95 ≤ 150 ms bij 55.000 foto's met GPS van één eigenaar (bouwontwerp
fase 6, Keuze 4a en 32; Z3; marker zoek_perf, job zoek-perf in python:3.12-slim met SQLite
3.46.1).

1. De boom van §3.6 via de echte migratie (bench.maak, --n 100000 --seed 7). De eigenaar (b00)
   krijgt er foto's met een plek bij tot hij er 55.000 levende heeft: de plekken van de bench,
   reizen over de hele wereld (ook over 180°), en 2% in een map in de prullenbak (dead via de
   triggers van 0010, dus rijen die de kaart moet overslaan).
2. De app zoals in productie: python -m app.serve (tests/search/server.py), rate limits uit,
   zonder plaatsnamen (PLACES=uit: de kaart heeft ze niet nodig).
3. De client in dit proces: een scherm van 1280 × 800 punten, zoals de kaart van Z5 vraagt: een
   midden (meestal bij zijn foto's, soms ergens op de wereld) en een zoom van 0 tot 14, en
   daaruit het venster (over 180° als het midden daar ligt). 20 vragen om op te warmen, dan 3
   rondes van 100 (vaste seed); p95 = de mediaan van de p95 van de rondes. Hard: p95 ≤ 150 ms en
   geen fout. Rapport: p50, p99, max, cellen per antwoord, de items van het venster, en het
   ergste venster (de hele wereld op zoom 14: grotere cellen tot het past).
4. Met een filter van Keuze 43 (review golf 3, contract F1): dezelfde vensters, om de beurt met
   één van de filters van _filters() (plaats, land, datum, soort, map, camera, en twee samen).
   De rijen van de eigenaar hebben daarvoor een plaats en een land (zoals photometa ze zet: de
   plekken van de bench elk hun plaats, een reis een plaats per graad), en de foto's een inhoud
   met een camera in blob_meta. Twee poorten (Keuze 4a): de filters die photo_geo zelf heeft
   (datum, plaats, land) dezelfde als zonder filter, p95 ≤ 150 ms; die met de join op node
   (soort, map, camera) een eigen poort, p95 ≤ POORT_NODE. Geen fout; het rapport per filter.

ZOEK_PERF_N maakt de boom kleiner en ZOEK_PERF_KAART het aantal foto's met een plek (alleen om
dit bestand zelf te proberen); ZOEK_PERF_UIT is een map voor de JSON en het verslag (CI: het
artefact).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import random
import sqlite3
import statistics
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.geo import tiles
from app.search import bench
from app.text import fold
from tests.search.server import App, zonder_2fa

pytestmark = pytest.mark.zoek_perf

N = int(os.environ.get("ZOEK_PERF_N", "100000"))
MET_PLEK = int(os.environ.get("ZOEK_PERF_KAART", "55000"))
POORT = 150.0
# De eigen poort van de filters met de join op node (Keuze 4a): per foto in het venster een rij
# uit node, ook bij de hele wereld op zoom 0–2. Gemeten p95 310 ms op 3.53.1 en 340 ms op 3.46.1;
# de poort ×1,5 daarboven (docs/fase-6/z3.md, De review (golf 3))
POORT_NODE = 500.0
RONDES = 3
PER_RONDE = 100
SCHERM = (1280, 800)
PRULLENBAK = 0.02
# Een reis over 180°: Fiji aan beide kanten
OVER_180 = ((-17.7, 179.6), (-16.8, -179.8))
# Het land van de plekken van de bench (bench.PLEKKEN, in die volgorde), en van een reis
LAND_PLEK = ("NL", "NL", "NL", "NL", "NL", "FR", "DE", "ES", "IT", "PT")
LANDEN = ("US", "BR", "JP", "AU", "ZA", "IN", "CA", "MX", "NZ", "FJ", "NO", "GR", "TH", "AR")
PLAATS_PLEK = 1000  # + de plek in bench.PLEKKEN
PLAATS_REIS = 100_000  # + een graad (lat, lon)


def _p(waarden: list[float], q: float) -> float:
    rij = sorted(waarden)
    return rij[min(len(rij) - 1, max(0, round(q * len(rij)) - 1))]


def _plek(rng: random.Random, reizen: list[tuple[float, float]]) -> tuple[float, float]:
    """60% bij de plekken van de bench (Nederland en Europa), 40% op een reis ergens."""
    if rng.random() < 0.6:
        _naam, lat, lon = rng.choice(bench.PLEKKEN)
        return lat + rng.gauss(0, 0.03), lon + rng.gauss(0, 0.05)
    lat, lon = rng.choice(reizen)
    lat = max(-80.0, min(80.0, lat + rng.gauss(0, 0.2)))
    lon = (lon + rng.gauss(0, 0.2) + 180.0) % 360.0 - 180.0
    return lat, lon


def _plaats(lat: float, lon: float) -> tuple[int, str]:
    """(place_id, land) van een plek: de dichtstbijzijnde plek van de bench als die binnen een
    halve graad ligt, anders een plaats per graad op de wereld."""
    afstand, i = min(
        ((p_lat - lat) ** 2 + (p_lon - lon) ** 2, i)
        for i, (_naam, p_lat, p_lon) in enumerate(bench.PLEKKEN)
    )
    if afstand < 0.25:
        return PLAATS_PLEK + i, LAND_PLEK[i]
    graad = math.floor(lat + 90) * 360 + math.floor(lon + 180)
    return PLAATS_REIS + graad, LANDEN[math.floor(lon + 180) // 30 % len(LANDEN)]


def _meer_plekken(db: Path, naam: str, doel: int) -> dict[str, Any]:
    """Foto's met een plek erbij voor naam tot hij er doel levende heeft. Rechtstreeks in
    SQLite: de triggers van 0010 zetten mpath, dead, de index, en dead en taken_at in photo_geo.
    Een deel in een map die daarna naar de prullenbak gaat."""
    rng = random.Random("kaart")
    nu = bench.DAG.strftime("%Y-%m-%d %H:%M:%S.%f")
    reizen = [(rng.uniform(-50, 65), rng.uniform(-180, 180)) for _ in range(200)]
    reizen += list(OVER_180) * 5
    with contextlib.closing(sqlite3.connect(db)) as con, con:
        owner, root = con.execute(
            "SELECT id, root_id FROM user WHERE email = ?", (f"{naam}@{bench.DOMEIN}",)
        ).fetchone()
        # De inhoud van zijn eigen foto's (met een camera in blob_meta), vast gekozen: een
        # eigen rng, zodat de plekken dezelfde blijven
        shas = [r[0] for r in con.execute(
            "SELECT DISTINCT n.blob_id FROM node n JOIN blob_meta m ON m.blob_sha = n.blob_id "
            "WHERE n.owner_id = ? AND n.mime = 'image/jpeg' ORDER BY n.blob_id LIMIT 500",
            (owner,),
        )]  # fmt: skip
        inhoud = random.Random("kaart:inhoud")
        al = con.execute(
            "SELECT count(*) FROM photo_geo WHERE owner_id = ? AND dead = 0", (owner,)
        ).fetchone()[0]
        mappen = [uuid.uuid4().hex for _ in range(6)]
        for i, map_ in enumerate(mappen):
            con.execute(
                "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, "
                "modified_at, search_name) VALUES (?, ?, ?, ?, 'dir', 0, ?, ?, ?)",
                (map_, root, owner, f"Reizen {i}", nu, nu, f"reizen {i}"),
            )
        weg = mappen[-1]  # deze map gaat naar de prullenbak
        extra = max(0, round((doel - al) / (1 - PRULLENBAK)))
        nodes, plekken = [], []
        for i in range(extra):
            genomen = bench.DAG - timedelta(days=rng.uniform(0, 365 * 12), seconds=i)
            naam_ = genomen.strftime(f"REIS_%Y%m%d_%H%M%S_{i}.jpg")
            map_ = weg if rng.random() < PRULLENBAK else rng.choice(mappen[:-1])
            node = uuid.uuid4().hex
            nodes.append((
                node, map_, owner, naam_, "file", "image/jpeg", 2_000_000, inhoud.choice(shas),
                nu, nu,
                fold(naam_), genomen.strftime("%Y-%m-%d %H:%M:%S.%f"),
                int(genomen.strftime("%Y%m%d")),
            ))  # fmt: skip
            lat, lon = _plek(rng, reizen)
            x, y = tiles.tile16(lat, lon)
            plekken.append((node, owner, lat, lon, x, y, "exif"))
        con.executemany(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
            "created_at, modified_at, search_name, taken_at, taken_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            nodes,
        )
        con.executemany(
            "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            plekken,
        )
        con.execute("UPDATE node SET deleted_at = ? WHERE id = ?", (nu, weg))
        # Een plaats en een land voor elke rij van hem (zoals photometa ze zet)
        con.executemany(
            "UPDATE photo_geo SET place_id = ?, country = ? WHERE node_id = ?",
            [(*_plaats(lat, lon), node) for node, lat, lon in con.execute(
                "SELECT node_id, lat, lon FROM photo_geo WHERE owner_id = ?", (owner,)
            ).fetchall()],
        )  # fmt: skip
        levend, dood = con.execute(
            "SELECT sum(dead = 0), sum(dead = 1) FROM photo_geo WHERE owner_id = ?", (owner,)
        ).fetchone()
        # 2.000 middens uit zijn foto's, steeds dezelfde (de ids zijn willekeurig, de plekken niet)
        alle = con.execute(
            "SELECT lat, lon FROM photo_geo WHERE owner_id = ? AND dead = 0 ORDER BY lat, lon",
            (owner,),
        ).fetchall()
        midden = random.Random("midden").sample(alle, min(2000, len(alle)))
    return {
        "al": al, "extra": extra, "levend": levend, "dood": dood, "midden": midden,
        "map": mappen[0],
    }  # fmt: skip


@pytest.fixture(scope="module")
def boom(tmp_path_factory):
    data = tmp_path_factory.mktemp("kaart")
    begin = time.monotonic()
    uit = bench.maak(data, bench.Plan(n=N, seed=7))
    zonder_2fa(data / "platform.db")
    extra = _meer_plekken(data / "platform.db", bench.ROLLEN["eigenaar"], MET_PLEK)
    extra["seconden"] = round(time.monotonic() - begin, 1)
    print(uit.kort())
    return data, extra


def venster(lat: float, lon: float, z: int) -> str:
    """Het venster van een scherm van SCHERM punten rond (lat, lon) op zoom z (Webmercator, 256
    punten per tegel). Breder dan de wereld: de hele wereld; over 180°: west > oost."""
    breed = 256.0 * 2**z
    span = SCHERM[0] / breed * 360.0
    if span >= 360.0:
        w, e = -180.0, 180.0
    else:
        w, e = lon - span / 2, lon + span / 2
        if w < -180.0:
            w += 360.0
        if e > 180.0:
            e -= 360.0
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2
    y0 = max(0.0, y * breed - SCHERM[1] / 2) / breed
    y1 = min(breed, y * breed + SCHERM[1] / 2) / breed

    def lat_van(t: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * t))))

    return f"{w:.5f},{lat_van(y1):.5f},{e:.5f},{lat_van(y0):.5f}"


def _vragen(seed: int, midden: list[tuple[float, float]], n: int) -> list[tuple[str, int]]:
    """n vensters: 80% rond een eigen foto, 20% ergens op de wereld; zoom 0 tot 14."""
    rng = random.Random(f"kaart:{seed}")
    uit = []
    for _ in range(n):
        if rng.random() < 0.8:
            lat, lon = rng.choice(midden)
        else:
            lat, lon = rng.uniform(-60, 70), rng.uniform(-180, 180)
        uit.append((venster(lat, lon, z := rng.randint(0, 14)), z))
    return uit


def test_de_vensters_van_een_scherm():
    """De vensters die de meting vraagt kloppen: binnen de wereld, over 180° als het moet."""
    assert venster(52.09, 5.12, 0) == venster(10.0, -170.0, 0)
    w, s, e, n = map(float, venster(52.09, 5.12, 0).split(","))
    assert (w, e) == (-180.0, 180.0) and -85.06 < s < n < 85.06
    w, s, e, n = map(float, venster(-17.0, 179.9, 8).split(","))
    assert w > e, "over 180°"
    w, s, e, n = map(float, venster(52.09, 5.12, 14).split(","))
    assert w < 5.12 < e and s < 52.09 < n and e - w < 0.12
    for tekst in (venster(lat, lon, z) for lat, lon, z in ((0, 0, 3), (80, -179.99, 5))):
        tiles.ranges(*tiles.parse_bbox(tekst))  # geen BadBox


def test_de_kaart_binnen_150_ms(boom, tmp_path):
    data, extra = boom
    versie = sqlite3.sqlite_version
    app = App(data, tmp_path / "server.log", env={"PLATFORM_PLACES": "uit"})
    app.start()
    rondes: list[list[float]] = []
    cellen: list[int] = []
    fout: list[str] = []
    over_180 = 0
    items: list[float] = []
    ergste: list[float] = []
    ergste_cellen = 0
    try:
        with httpx.Client(base_url=app.url, trust_env=False, timeout=30) as client:
            login = {
                "email": f"{bench.ROLLEN['eigenaar']}@{bench.DOMEIN}",
                "password": bench.WACHTWOORD,
            }
            r = client.post("/api/v1/auth/login", json=login)
            assert r.status_code == 200, r.text
            client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            for bbox, z in _vragen(0, extra["midden"], 20):
                client.get("/api/v1/photos/map", params={"bbox": bbox, "z": z})
            for ronde in range(1, RONDES + 1):
                tijden = []
                for bbox, z in _vragen(ronde, extra["midden"], PER_RONDE):
                    begin = time.perf_counter()
                    r = client.get("/api/v1/photos/map", params={"bbox": bbox, "z": z})
                    tijden.append((time.perf_counter() - begin) * 1000)
                    if r.status_code != 200:
                        fout.append(f"z={z}: {r.status_code}")
                        continue
                    cellen.append(len(r.json()["cells"]))
                    w, _s, e, _n = map(float, bbox.split(","))
                    over_180 += w > e
                rondes.append(tijden)
            # Rapport: de eerste pagina van de items, en het ergste venster
            for bbox, _z in _vragen(9, extra["midden"], 20):
                begin = time.perf_counter()
                r = client.get("/api/v1/photos/map/items", params={"bbox": bbox})
                items.append((time.perf_counter() - begin) * 1000)
                if r.status_code != 200:
                    fout.append(f"items: {r.status_code}")
            for _ in range(5):
                begin = time.perf_counter()
                r = client.get("/api/v1/photos/map", params={"bbox": "-180,-85,180,85", "z": 14})
                ergste.append((time.perf_counter() - begin) * 1000)
                if r.status_code != 200:
                    fout.append(f"ergste: {r.status_code}")
                    continue
                ergste_cellen = len(r.json()["cells"])
    finally:
        app.stop()
    p95 = statistics.median(_p(t, 0.95) for t in rondes)
    alle = [t for ronde in rondes for t in ronde]
    uitkomst = {
        "sqlite": versie,
        "boom": {"n": N, **{k: v for k, v in extra.items() if k != "midden"}},
        "p95": p95,
        "p95_per_ronde": [_p(t, 0.95) for t in rondes],
        "p50": _p(alle, 0.50),
        "p99": _p(alle, 0.99),
        "max": max(alle),
        "cellen_per_antwoord": {
            "gemiddeld": statistics.mean(cellen) if cellen else 0,
            "max": max(cellen, default=0),
        },
        "over_180": over_180,
        "items_p95": _p(items, 0.95) if items else None,
        "ergste_venster": {"p50": _p(ergste, 0.5), "max": max(ergste), "cellen": ergste_cellen},
        "fout": fout,
    }
    niet = []
    if p95 > POORT:
        niet.append(f"p95 {p95:.1f} > {POORT:.0f}")
    if fout:
        niet.append(f"fouten {len(fout)}")
    uitkomst["poort"] = {"gehaald": not niet, "niet": niet}
    verslag = "\n".join([
        "=== kaart ===",
        f"SQLite {versie}, boom n={N} seed=7, eigenaar met {extra['levend']} levende foto's met "
        f"een plek ({extra['extra']} erbij, {extra['dood']} in de prullenbak)",
        f"GET /photos/map: p95 {p95:.1f} ms (rondes "
        + ", ".join(f"{x:.1f}" for x in uitkomst["p95_per_ronde"])
        + f"), p50 {uitkomst['p50']:.1f}, p99 {uitkomst['p99']:.1f}, max {uitkomst['max']:.1f}",
        f"cellen per antwoord: gemiddeld {uitkomst['cellen_per_antwoord']['gemiddeld']:.0f}, "
        f"max {uitkomst['cellen_per_antwoord']['max']}; over 180°: {over_180} vensters",
        f"GET /photos/map/items (rapport): p95 {uitkomst['items_p95']:.1f} ms",
        f"ergste venster, de wereld op zoom 14 (rapport): p50 {_p(ergste, 0.5):.1f} ms, "
        f"max {max(ergste):.1f}, {ergste_cellen} cellen",
        "poort (p95 ≤ 150 ms): gehaald" if not niet else "poort: NIET: " + "; ".join(niet),
        "=== einde kaart ===",
    ])  # fmt: skip
    print(verslag)
    if map_ := os.environ.get("ZOEK_PERF_UIT"):
        Path(map_).mkdir(parents=True, exist_ok=True)
        (Path(map_) / "fotokaart.json").write_text(json.dumps(uitkomst, indent=2) + "\n")
        (Path(map_) / "fotokaart.txt").write_text(verslag + "\n")
    assert extra["levend"] >= MET_PLEK, extra["levend"]
    assert cellen and max(cellen) > 0, "de meting moet cellen teruggeven"
    assert over_180 > 0, "de meting moet ook over 180° vragen"
    assert not niet, niet


def _filters(extra: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """De filters van de tweede meting, met hun poort: "geo" voor wat photo_geo zelf heeft,
    "node" voor wat de join op node vraagt (ook samen met een van "geo")."""
    jaar = bench.DAG.year
    datum = {"from": f"{jaar - 5}-01-01", "to": f"{jaar - 4}-12-31"}
    return {
        "plaats": ("geo", {"place": PLAATS_PLEK + 1}),  # Amsterdam
        "land": ("geo", {"country": "NL"}),
        "datum": ("geo", datum),
        "plaats+datum": ("geo", {"place": PLAATS_PLEK + 1, **datum}),
        "soort": ("node", {"type": "image"}),
        "map": ("node", {"in": extra["map"]}),
        "camera": ("node", {"camera": "canon eos 80d"}),
        "land+soort": ("node", {"country": "nl", "type": "image"}),
    }


def test_de_kaart_met_een_filter(boom, tmp_path):
    """Keuze 4a: met een filter van Keuze 43 binnen de poort van zijn soort (review golf 3,
    contract F1)."""
    data, extra = boom
    filters = _filters(extra)
    namen = list(filters)
    poorten = {"geo": POORT, "node": POORT_NODE}
    app = App(data, tmp_path / "server.log", env={"PLATFORM_PLACES": "uit"})
    app.start()
    rondes: dict[str, list[list[float]]] = {soort: [] for soort in poorten}
    per_filter: dict[str, list[float]] = {naam: [] for naam in namen}
    fotos: dict[str, int] = dict.fromkeys(namen, 0)
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
            for i, (bbox, z) in enumerate(_vragen(10, extra["midden"], 20)):
                params = {"bbox": bbox, "z": z, **filters[namen[i % len(namen)]][1]}
                client.get("/api/v1/photos/map", params=params)
            for ronde in range(1, RONDES + 1):
                tijden: dict[str, list[float]] = {soort: [] for soort in poorten}
                for i, (bbox, z) in enumerate(_vragen(10 + ronde, extra["midden"], PER_RONDE)):
                    naam = namen[(i + ronde) % len(namen)]
                    soort, f = filters[naam]
                    begin = time.perf_counter()
                    r = client.get("/api/v1/photos/map", params={"bbox": bbox, "z": z, **f})
                    tijd = (time.perf_counter() - begin) * 1000
                    tijden[soort].append(tijd)
                    per_filter[naam].append(tijd)
                    if r.status_code != 200:
                        fout.append(f"{naam} z={z}: {r.status_code}")
                        continue
                    fotos[naam] += sum(c["n"] for c in r.json()["cells"])
                for soort, t in tijden.items():
                    rondes[soort].append(t)
    finally:
        app.stop()
    p95 = {soort: statistics.median(_p(t, 0.95) for t in r) for soort, r in rondes.items()}
    uitkomst = {
        "sqlite": sqlite3.sqlite_version,
        "filters": {naam: {"poort": soort, **f} for naam, (soort, f) in filters.items()},
        "p95": p95,
        "p95_per_ronde": {soort: [_p(t, 0.95) for t in r] for soort, r in rondes.items()},
        "poorten": poorten,
        "per_filter": {
            naam: {"p50": _p(t, 0.5), "p95": _p(t, 0.95), "max": max(t), "fotos": fotos[naam]}
            for naam, t in per_filter.items()
        },
        "fout": fout,
    }
    niet = [
        f"{soort}: p95 {p95[soort]:.1f} > {poort:.0f}"
        for soort, poort in poorten.items()
        if p95[soort] > poort
    ]
    if fout:
        niet.append(f"fouten {len(fout)}")
    uitkomst["poort"] = {"gehaald": not niet, "niet": niet}
    verslag = "\n".join([
        "=== kaart met een filter ===",
        f"SQLite {sqlite3.sqlite_version}, dezelfde boom; {len(namen)} filters om de beurt",
        *(
            f"GET /photos/map met een filter op {'photo_geo' if soort == 'geo' else 'node'}: "
            f"p95 {p95[soort]:.1f} ms (rondes "
            + ", ".join(f"{x:.1f}" for x in uitkomst["p95_per_ronde"][soort])
            + f"), poort {poorten[soort]:.0f} ms"
            for soort in poorten
        ),
        *(
            f"  {naam} ({filters[naam][0]}): p50 {v['p50']:.1f}, p95 {v['p95']:.1f}, "
            f"max {v['max']:.1f} ms, {v['fotos']} foto's in de cellen"
            for naam, v in uitkomst["per_filter"].items()
        ),
        "poorten: gehaald" if not niet else "poorten: NIET: " + "; ".join(niet),
        "=== einde kaart met een filter ===",
    ])  # fmt: skip
    print(verslag)
    if map_ := os.environ.get("ZOEK_PERF_UIT"):
        Path(map_).mkdir(parents=True, exist_ok=True)
        (Path(map_) / "fotokaart_filters.json").write_text(json.dumps(uitkomst, indent=2) + "\n")
        (Path(map_) / "fotokaart_filters.txt").write_text(verslag + "\n")
    # Elk filter vond iets (anders meet hij niets)
    assert all(fotos.values()), fotos
    assert not niet, niet
