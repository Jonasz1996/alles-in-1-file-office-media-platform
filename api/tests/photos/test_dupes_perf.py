"""§4.4: GET /duplicates p95 ≤ 300 ms bij 55.000 foto's van één eigenaar (bouwontwerp fase 6,
Keuzes 34–36 en 4a; Z3; marker zoek_perf, job zoek-perf in python:3.12-slim met SQLite 3.46.1).

1. De boom van §3.6 via de echte migratie (bench.maak, --n 100000 --seed 7). De eigenaar (b00)
   krijgt er foto's bij tot hij er 55.000 levende heeft, elk met een eigen inhoud (blob,
   blob_meta en blob_hash zoals photometa ze achterlaat; dan heeft de backfill bij het starten
   niets te doen). Daarin:
   - precies gelijk: 3% van de foto's nog 1 tot 3 keer, in een andere map;
   - bijna gelijk: reeksen van 2 tot 6 foto's (samen ±5%), met een paar in dup_pair voor bijna
     elk tweetal (niet altijd: dan worden het twee klieken), "zeker" en "lijkt"; in een deel van
     de reeksen tekst (OCR), soms bij één foto anders (dan wordt "zeker" "lijkt");
   - wat er niet bij hoort: 2% in een map in de prullenbak (ook kopieën en leden van een reeks,
     dead via de triggers van 0010), paren met een foto van een ander, en groepen die hij "niet
     gelijk" noemde (40, via de route).
2. De app zoals in productie: python -m app.serve (tests/search/server.py), rate limits uit,
   zonder plaatsnamen (PLACES=uit: de duplicaten hebben ze niet nodig).
3. De client in dit proces: eerst 20 groepen van elke soort "niet gelijk" (rapport), dan alle
   pagina's van beide soorten langs voor de cursors. 20 vragen om op te warmen, dan 3 rondes van
   100 (vaste seed): 40% de eerste pagina van exact, 40% die van near, 20% een latere pagina via
   zijn cursor. p95 = de mediaan van de p95 van de rondes. Hard: p95 ≤ 300 ms en geen fout.
   Rapport: p50, p99, max, de p95 per soort, en de groepen en pagina's.

ZOEK_PERF_N maakt de boom kleiner en ZOEK_PERF_DUBBEL het aantal levende foto's (alleen om dit
bestand zelf te proberen); ZOEK_PERF_UIT is een map voor de JSON en het verslag (CI: het
artefact).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import sqlite3
import statistics
import time
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.search import bench
from app.text import fold
from tests.search.server import App, zonder_2fa

pytestmark = pytest.mark.zoek_perf

N = int(os.environ.get("ZOEK_PERF_N", "100000"))
FOTOS = int(os.environ.get("ZOEK_PERF_DUBBEL", "55000"))
POORT = 300.0
RONDES = 3
PER_RONDE = 100
PRULLENBAK = 0.02
KOPIEEN = 0.03  # deel van de foto's dat nog 1 tot 3 keer bestaat
REEKSEN = 0.05  # deel van de foto's in een reeks van bijna gelijke
ZONDER_PAAR = 0.1  # een tweetal in een reeks zonder paar: geen kliek meer
MET_TEKST = 0.2  # reeksen met tekst (OCR); daarvan heeft 30% één foto met andere tekst
VAN_EEN_ANDER = 500  # paren met een foto van een ander
NIET_GELIJK = 20  # groepen per soort die hij "niet gelijk" noemt


def _p(waarden: list[float], q: float) -> float:
    rij = sorted(waarden)
    return rij[min(len(rij) - 1, max(0, round(q * len(rij)) - 1))]


def _stukken(h: int) -> tuple[int, int, int, int, int]:
    """De pHash met teken en zijn vier stukken van 16 bits (zoals photo_hash ze schrijft)."""
    signed = h - (1 << 64) if h >= 1 << 63 else h
    return signed, h & 0xFFFF, (h >> 16) & 0xFFFF, (h >> 32) & 0xFFFF, (h >> 48) & 0xFFFF


def _meer_fotos(db: Path, naam: str, doel: int) -> dict[str, int]:
    """Foto's erbij voor naam tot hij er doel levende heeft, met kopieën en reeksen. Rechtstreeks
    in SQLite: de triggers van 0010 zetten mpath, dead en de index; een map gaat daarna naar de
    prullenbak."""
    rng = random.Random("duplicaten")
    nu = bench.DAG.strftime("%Y-%m-%d %H:%M:%S.%f")
    standaard = bench.app_blob()
    with contextlib.closing(sqlite3.connect(db)) as con:
        with con:
            owner, root = con.execute(
                "SELECT id, root_id FROM user WHERE email = ?", (f"{naam}@{bench.DOMEIN}",)
            ).fetchone()
            al = con.execute(
                "SELECT count(*) FROM node WHERE owner_id = ? AND kind = 'file' AND dead = 0 "
                "AND mime LIKE 'image/%'",
                (owner,),
            ).fetchone()[0]
            vreemd = [
                sha
                for (sha,) in con.execute(
                    "SELECT DISTINCT blob_id FROM node WHERE owner_id != ? AND mime = 'image/jpeg' "
                    "AND blob_id NOT IN (SELECT blob_id FROM node WHERE owner_id = ? "
                    "AND blob_id IS NOT NULL) LIMIT 5000",
                    (owner, owner),
                )
            ]
            mappen = [uuid.uuid4().hex for _ in range(8)]
            for i, map_ in enumerate(mappen):
                con.execute(
                    "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, "
                    "modified_at, search_name) VALUES (?, ?, ?, ?, 'dir', 0, ?, ?, ?)",
                    (map_, root, owner, f"Camera {i}", nu, nu, f"camera {i}"),
                )
            weg = mappen[-1]  # deze map gaat naar de prullenbak
            extra = max(0, round((doel - al) / (1 - PRULLENBAK)))

            # De foto's zelf: (sha, grootte, genomen, phash, map)
            fotos = []
            for i in range(extra):
                genomen = bench.DAG - timedelta(days=rng.uniform(0, 365 * 12), seconds=i)
                map_ = weg if rng.random() < PRULLENBAK else rng.choice(mappen[:-1])
                sha = hashlib.sha256(f"duplicaten:{i}".encode()).hexdigest()
                fotos.append([sha, rng.randint(1_500_000, 6_000_000), genomen,
                              rng.getrandbits(64), map_])  # fmt: skip

            # Reeksen van bijna gelijke: opeenvolgende foto's, de pHash dichtbij de eerste
            reeksen: list[list[int]] = []
            i = 0
            while i < extra:
                if rng.random() < REEKSEN / 3.5:
                    lengte = min(extra - i, rng.choice((2, 2, 3, 3, 4, 5, 6)))
                    if lengte >= 2:
                        reeksen.append(list(range(i, i + lengte)))
                        for j in range(i + 1, i + lengte):
                            phash = fotos[i][3]
                            for bit in rng.sample(range(64), rng.randint(1, 5)):
                                phash ^= 1 << bit
                            fotos[j][3] = phash
                    i += lengte
                else:
                    i += 1
            paren = []
            for reeks in reeksen:
                for x, a in enumerate(reeks):
                    for b in reeks[x + 1 :]:
                        if rng.random() < ZONDER_PAAR:
                            continue
                        afstand = rng.randint(1, 10)
                        zeker = afstand <= 6 and rng.random() < 0.85
                        sa, sb = sorted((fotos[a][0], fotos[b][0]))
                        paren.append((sa, sb, afstand, 1.2, "zeker" if zeker else "lijkt"))
            for _ in range(VAN_EEN_ANDER if vreemd else 0):
                sa, sb = sorted((rng.choice(fotos)[0], rng.choice(vreemd)))
                paren.append((sa, sb, rng.randint(1, 6), 1.2, "zeker"))
            teksten = []
            for reeks in reeksen:
                if rng.random() < MET_TEKST:
                    tekst = bench.tekst(rng, 80)
                    anders = rng.choice(reeks) if rng.random() < 0.3 else None
                    for j in reeks:
                        ocr = bench.tekst(rng, 80) if j == anders else tekst
                        teksten.append((fotos[j][0], "", ocr, "", 0, nu))

            # De nodes: elke foto één keer, en een deel nog 1 tot 3 keer in een andere map
            nodes = []
            refs: dict[str, int] = {}
            for i, (sha, grootte, genomen, _phash, map_) in enumerate(fotos):
                plekken = [map_]
                if rng.random() < KOPIEEN:
                    plekken += [rng.choice(mappen) for _ in range(rng.choice((1, 1, 1, 2, 3)))]
                refs[sha] = len(plekken)
                for k, plek in enumerate(plekken):
                    naam_ = genomen.strftime(f"DSC_%Y%m%d_%H%M%S_{i}") + (f" ({k})" if k else "")
                    nodes.append((
                        uuid.uuid4().hex, plek, owner, naam_ + ".jpg", "file", "image/jpeg",
                        grootte, sha, nu, nu, fold(naam_ + ".jpg"),
                        genomen.strftime("%Y-%m-%d %H:%M:%S.%f"), int(genomen.strftime("%Y%m%d")),
                    ))  # fmt: skip
            con.executemany(
                "INSERT INTO blob (sha256, size, ref_count, created_at, mime, preview, "
                "preview_meta, taken_at, meta_version, render_version, pdf_state, pdf_version) "
                "VALUES (?, ?, ?, ?, 'image/jpeg', ?, NULL, ?, ?, ?, NULL, 0)",
                (
                    (sha, grootte, refs[sha], nu, standaard["preview"],
                     genomen.strftime("%Y-%m-%d %H:%M:%S.%f"), standaard["meta_version"],
                     standaard["render_version"])
                    for sha, grootte, genomen, _phash, _map in fotos
                ),
            )  # fmt: skip
            con.executemany(
                "INSERT INTO blob_meta (blob_sha, camera, lens, gps) VALUES (?, ?, NULL, 0)",
                ((f[0], rng.choice(bench.CAMERAS)) for f in fotos),
            )
            con.executemany(
                "INSERT INTO blob_hash (blob_sha, state, phash, h0, h1, h2, h3, version) "
                "VALUES (?, 'ok', ?, ?, ?, ?, ?, 1)",
                ((f[0], *_stukken(f[3])) for f in fotos),
            )
            con.executemany(
                "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                teksten,
            )
            con.executemany(
                "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
                "created_at, modified_at, search_name, taken_at, taken_day) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                nodes,
            )
            con.executemany(
                "INSERT OR IGNORE INTO dup_pair (a, b, dist, mad, level) VALUES (?, ?, ?, ?, ?)",
                paren,
            )
            con.execute("UPDATE node SET deleted_at = ? WHERE id = ?", (nu, weg))
            levend, dood = con.execute(
                "SELECT sum(dead = 0), sum(dead = 1) FROM node WHERE owner_id = ? "
                "AND kind = 'file' AND mime LIKE 'image/%'",
                (owner,),
            ).fetchone()
            paren_eigen = con.execute(
                "SELECT count(*) FROM dup_pair AS p WHERE EXISTS (SELECT 1 FROM node AS n "
                "WHERE n.owner_id = :me AND n.blob_id = p.a AND n.dead = 0 AND n.kind = 'file') "
                "AND EXISTS (SELECT 1 FROM node AS n WHERE n.owner_id = :me AND n.blob_id = p.b "
                "AND n.dead = 0 AND n.kind = 'file')",
                {"me": owner},
            ).fetchone()[0]
            paren_alle = con.execute("SELECT count(*) FROM dup_pair").fetchone()[0]
        # De stap text van analysis zoals de backfill van Z2 hem achterlaat (bench.maak doet dat
        # ook): dan leest die bij het starten niets opnieuw
        con.isolation_level = None
        bench.analyse_klaar(con)
    return {
        "al": al, "extra": extra, "nodes": len(nodes), "levend": levend, "dood": dood,
        "reeksen": len(reeksen), "paren": len(paren), "paren_eigen": paren_eigen,
        "paren_alle": paren_alle, "met_tekst": len(teksten),
    }  # fmt: skip


@pytest.fixture(scope="module")
def boom(tmp_path_factory):
    data = tmp_path_factory.mktemp("duplicaten")
    begin = time.monotonic()
    uit = bench.maak(data, bench.Plan(n=N, seed=7))
    zonder_2fa(data / "platform.db")
    extra = _meer_fotos(data / "platform.db", bench.ROLLEN["eigenaar"], FOTOS)
    extra["seconden"] = round(time.monotonic() - begin, 1)
    print(uit.kort())
    return data, extra


def _alle_paginas(client: httpx.Client, kind: str) -> tuple[list[str | None], list[dict]]:
    """Alle pagina's van deze soort: de cursors (None voor de eerste) en alle groepen."""
    cursors: list[str | None] = [None]
    groepen: list[dict] = []
    while True:
        params = {"kind": kind} | ({"cursor": cursors[-1]} if cursors[-1] else {})
        r = client.get("/api/v1/duplicates", params=params)
        assert r.status_code == 200, r.text
        groepen += r.json()["groups"]
        volgende = r.json()["cursor"]
        if volgende is None:
            return cursors, groepen
        cursors.append(volgende)


Vraag = tuple[str, str | None]  # (soort, cursor)


def _vragen(seed: int, cursors: dict[str, list[str | None]], n: int) -> list[Vraag]:
    """n vragen: 40% de eerste pagina van exact, 40% die van near, 20% een latere pagina."""
    rng = random.Random(f"duplicaten:{seed}")
    uit = []
    for _ in range(n):
        keuze = rng.random()
        if keuze < 0.4:
            uit.append(("exact", None))
        elif keuze < 0.8:
            uit.append(("near", None))
        else:
            kind = rng.choice(("exact", "near"))
            later = cursors[kind][1:] or [None]
            uit.append((kind, rng.choice(later)))
    return uit


def test_de_duplicaten_binnen_300_ms(boom, tmp_path):
    data, extra = boom
    versie = sqlite3.sqlite_version
    app = App(data, tmp_path / "server.log", env={"PLATFORM_PLACES": "uit"})
    app.start()
    rondes: list[list[float]] = []
    per_soort: dict[str, list[float]] = {"exact": [], "near": []}
    fout: list[str] = []
    negeer: list[float] = []
    try:
        with httpx.Client(base_url=app.url, trust_env=False, timeout=30) as client:
            login = {
                "email": f"{bench.ROLLEN['eigenaar']}@{bench.DOMEIN}",
                "password": bench.WACHTWOORD,
            }
            r = client.post("/api/v1/auth/login", json=login)
            assert r.status_code == 200, r.text
            client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            # "Niet gelijk" voor een deel van de groepen (rapport); daarna komen ze niet terug
            genegeerd = set()
            for kind in ("exact", "near"):
                r = client.get("/api/v1/duplicates", params={"kind": kind})
                assert r.status_code == 200, r.text
                for groep in r.json()["groups"][:NIET_GELIJK]:
                    begin = time.perf_counter()
                    r = client.post("/api/v1/duplicates/ignore", json={"key": groep["key"]})
                    negeer.append((time.perf_counter() - begin) * 1000)
                    assert r.status_code == 204, r.text
                    genegeerd.add(groep["key"])
            cursors, groepen = {}, {}
            for kind in ("exact", "near"):
                cursors[kind], groepen[kind] = _alle_paginas(client, kind)
            for kind, cursor in _vragen(0, cursors, 20):
                client.get("/api/v1/duplicates", params={"kind": kind, "cursor": cursor})
            for ronde in range(1, RONDES + 1):
                tijden = []
                for kind, cursor in _vragen(ronde, cursors, PER_RONDE):
                    params = {"kind": kind} | ({"cursor": cursor} if cursor else {})
                    begin = time.perf_counter()
                    r = client.get("/api/v1/duplicates", params=params)
                    ms = (time.perf_counter() - begin) * 1000
                    tijden.append(ms)
                    per_soort[kind].append(ms)
                    if r.status_code != 200 or not r.json()["groups"]:
                        fout.append(f"{kind}: {r.status_code}")
                rondes.append(tijden)
    finally:
        app.stop()
    p95 = statistics.median(_p(t, 0.95) for t in rondes)
    alle = [t for ronde in rondes for t in ronde]
    niveaus = {
        lv: sum(g["level"] == lv for g in groepen["near"]) for lv in ("zeker", "lijkt")
    }  # fmt: skip
    uitkomst = {
        "sqlite": versie,
        "boom": {"n": N, **extra},
        "p95": p95,
        "p95_per_ronde": [_p(t, 0.95) for t in rondes],
        "p50": _p(alle, 0.50),
        "p99": _p(alle, 0.99),
        "max": max(alle),
        "p95_per_soort": {k: _p(v, 0.95) for k, v in per_soort.items() if v},
        "groepen": {k: len(v) for k, v in groepen.items()},
        "paginas": {k: len(v) for k, v in cursors.items()},
        "bestanden": {k: sum(len(g["items"]) for g in v) for k, v in groepen.items()},
        "near_niveaus": niveaus,
        "niet_gelijk_p95": _p(negeer, 0.95),
        "fout": fout,
    }
    niet = []
    if p95 > POORT:
        niet.append(f"p95 {p95:.1f} > {POORT:.0f}")
    if fout:
        niet.append(f"fouten {len(fout)}")
    uitkomst["poort"] = {"gehaald": not niet, "niet": niet}
    verslag = "\n".join([
        "=== duplicaten ===",
        f"SQLite {versie}, boom n={N} seed=7, eigenaar met {extra['levend']} levende foto's "
        f"({extra['extra']} erbij, {extra['dood']} in de prullenbak); dup_pair "
        f"{extra['paren_alle']}, waarvan {extra['paren_eigen']} tussen zijn eigen, levende foto's",
        f"GET /duplicates: p95 {p95:.1f} ms (rondes "
        + ", ".join(f"{x:.1f}" for x in uitkomst["p95_per_ronde"])
        + f"), p50 {uitkomst['p50']:.1f}, p99 {uitkomst['p99']:.1f}, max {uitkomst['max']:.1f}; "
        + "per soort "
        + ", ".join(f"{k} {v:.1f}" for k, v in uitkomst["p95_per_soort"].items()),
        f"groepen: exact {len(groepen['exact'])} ({len(cursors['exact'])} pagina's), near "
        f"{len(groepen['near'])} ({len(cursors['near'])} pagina's; zeker {niveaus['zeker']}, "
        f"lijkt {niveaus['lijkt']}); {len(genegeerd)} niet gelijk",
        f"POST /duplicates/ignore (rapport): p95 {uitkomst['niet_gelijk_p95']:.1f} ms",
        "poort (p95 ≤ 300 ms): gehaald" if not niet else "poort: NIET: " + "; ".join(niet),
        "=== einde duplicaten ===",
    ])  # fmt: skip
    print(verslag)
    if map_ := os.environ.get("ZOEK_PERF_UIT"):
        Path(map_).mkdir(parents=True, exist_ok=True)
        (Path(map_) / "duplicaten.json").write_text(json.dumps(uitkomst, indent=2) + "\n")
        (Path(map_) / "duplicaten.txt").write_text(verslag + "\n")
    # De meting moet echt iets meten: veel groepen, meer dan één pagina, beide niveaus
    assert extra["levend"] >= FOTOS, extra["levend"]
    for kind in ("exact", "near"):
        assert len(cursors[kind]) > 1, f"{kind}: maar één pagina"
        assert all(len(g["items"]) >= 2 for g in groepen[kind]), kind
        assert not genegeerd & {g["key"] for g in groepen[kind]}, "niet gelijk kwam terug"
    assert niveaus["zeker"] and niveaus["lijkt"], niveaus
    assert not niet, niet
