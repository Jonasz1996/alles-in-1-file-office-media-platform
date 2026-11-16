"""De routes van gezichten bij 60.000 gezichten (bouwontwerp fase 6, X2 "Tests" en "Harde
poorten"; X2b; marker zoek_perf, job zoek-perf in python:3.12-slim met SQLite 3.46.1).

1. De boom van §3.6 via de echte migratie (bench.maak, --n 100000 --seed 8). De eigenaar (b00)
   zet gezichten aan en krijgt FOTOS foto's erbij in acht mappen, met 1 tot 4 gezichten per foto
   (samen GEZICHTEN), zoals een scan en een groepsronde ze achterlaten: een verzegelde vec van
   540 B per gezicht (willekeurige bytes: deze routes lezen hem niet), 400 mensen met een
   scheve verdeling (Zipf), waarvan de eerste 80 een persoon zijn. Van hun gezichten heeft 40%
   een "ja"; de rest heeft een voorstel (sim, soms tag) en een groep. De andere mensen staan in
   een groep (70%) of los. 3% is "niet tonen" (zonder vec), 2% heeft een "nee", en 2% staat in
   een map in de prullenbak (own_live laat ze weg). Elke levende foto van de eigenaar heeft een
   face_scan op klaar (ook die van de boom zelf, met 0 gezichten): de sweep bij het starten
   heeft niets te doen.
2. De app zoals in productie: python -m app.serve (tests/search/server.py) met de analyse aan en
   PLATFORM_FACES_KEYS (een lege map), zonder worker (de monitor zegt down; de routes lezen
   alleen de database), rate limits uit. Eerst wachten tot de jobs van het starten klaar zijn.
3. De client in dit proces, per route 10 vragen om op te warmen, dan 3 rondes van PER_RONDE
   (vaste seed): voorstellen (de helft per persoon, de helft het overzicht), groepen (60% de
   eerste pagina, 40% een latere via de cursor), de gezichten van een foto en de stand. p95 =
   de mediaan van de p95 van de rondes. Hard: voorstellen, groepen en /files/{id}/faces p95 ≤
   100 ms, de stand p95 ≤ 50 ms, en geen fout. Rapport: p50, p99, max per route.

ZOEK_PERF_N maakt de boom kleiner en ZOEK_PERF_GEZICHTEN het aantal gezichten (alleen om dit
bestand zelf te proberen); ZOEK_PERF_UIT is een map voor de JSON en het verslag (CI: het
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.people.faces.drempels import MIN_GROUP
from app.people.faces.version import FACES_VERSION
from app.search import bench
from app.text import fold
from tests.search.server import App, zonder_2fa

pytestmark = pytest.mark.zoek_perf

N = int(os.environ.get("ZOEK_PERF_N", "100000"))
GEZICHTEN = int(os.environ.get("ZOEK_PERF_GEZICHTEN", "60000"))
POORT = {"voorstellen": 100.0, "groepen": 100.0, "foto": 100.0, "stand": 50.0}
RONDES = 3
PER_RONDE = 60
OPWARMEN = 10
MENSEN = 400
PERSONEN = 80  # de eerste zoveel mensen zijn een persoon
JA = 0.4  # van de gezichten van een persoon
IN_GROEP = 0.7  # van de gezichten van wie geen persoon is
VERBORGEN = 0.03
NEE = 0.02
PRULLENBAK = 0.02
VEC = 540
RUN = 7


def _p(waarden: list[float], q: float) -> float:
    rij = sorted(waarden)
    return rij[min(len(rij) - 1, max(0, round(q * len(rij)) - 1))]


def _tijd(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")


def _gezichten(db: Path, naam: str, doel: int) -> dict:
    """Foto's met doel gezichten erbij voor naam, met personen, oordelen, voorstellen en groepen,
    en face_scan, face_owner en photo_pref zoals na een scan. Rechtstreeks in SQLite: de
    triggers van 0010 zetten mpath, dead en ftype; een map gaat daarna naar de prullenbak."""
    rng = random.Random("gezichten")
    nu = _tijd(bench.DAG)
    with contextlib.closing(sqlite3.connect(db)) as con, con:
        owner, root = con.execute(
            "SELECT id, root_id FROM user WHERE email = ?", (f"{naam}@{bench.DOMEIN}",)
        ).fetchone()
        sha = con.execute(
            "SELECT blob_id FROM node WHERE owner_id = ? AND mime = 'image/jpeg' LIMIT 1", (owner,)
        ).fetchone()[0]
        mappen = [uuid.uuid4().hex for _ in range(8)]
        for i, map_ in enumerate(mappen):
            con.execute(
                "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, "
                "modified_at, search_name) VALUES (?, ?, ?, ?, 'dir', 0, ?, ?, ?)",
                (map_, root, owner, f"Gezichten {i}", nu, nu, f"gezichten {i}"),
            )
        weg = mappen[-1]  # deze map gaat naar de prullenbak

        personen = []
        for i in range(PERSONEN):
            naam_ = f"Persoon {i:02d}"
            personen.append((uuid.uuid4().hex, owner, naam_, fold(naam_), 0, nu, nu))
        con.executemany(
            "INSERT INTO person (id, owner_id, name, search_name, hidden, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            personen,
        )

        # Een scheve verdeling over de mensen: een paar staan op heel veel foto's
        gewicht = [1 / (i + 1) ** 0.8 for i in range(MENSEN)]
        fotos, gezichten, oordelen, scans = [], [], [], []
        tel = {"ja": 0, "nee": 0, "voorstel": 0, "groep": 0, "los": 0, "verborgen": 0}
        i = 0
        while len(gezichten) < doel:
            genomen = bench.DAG - timedelta(days=rng.uniform(0, 365 * 15), seconds=i)
            naam_ = genomen.strftime(f"IMG_%Y%m%d_%H%M%S_{i}.jpg")
            node = uuid.uuid4().hex
            map_ = weg if rng.random() < PRULLENBAK else rng.choice(mappen[:-1])
            fotos.append((
                node, map_, owner, naam_, "file", "image/jpeg", 2_000_000, sha, nu, nu,
                fold(naam_), _tijd(genomen), int(genomen.strftime("%Y%m%d")),
            ))  # fmt: skip
            aantal = min(doel - len(gezichten), rng.choices((1, 2, 3, 4), (4, 3, 2, 1))[0])
            for j in range(aantal):
                face = uuid.uuid4().hex
                mens = rng.choices(range(MENSEN), gewicht)[0]
                cluster = suggest = score = why = None
                hidden, ja = 0, False
                vec: bytes | None = rng.randbytes(VEC)
                if rng.random() < VERBORGEN:
                    hidden, vec = 1, None
                    tel["verborgen"] += 1
                elif mens < PERSONEN:
                    persoon = personen[mens][0]
                    if rng.random() < JA:
                        oordelen.append((face, persoon, owner, "ja", nu))
                        tel["ja"] += 1
                        ja = True
                    else:
                        suggest, cluster = persoon, mens
                        why = "tag" if rng.random() < 0.05 else "sim"
                        score = rng.uniform(0.45, 0.85) if why == "sim" else None
                        tel["voorstel"] += 1
                elif rng.random() < IN_GROEP:
                    cluster = mens
                    tel["groep"] += 1
                else:
                    tel["los"] += 1
                if hidden == 0 and not ja and rng.random() < NEE:
                    ander = personen[rng.randrange(PERSONEN)][0]
                    oordelen.append((face, ander, owner, "nee", nu))
                    tel["nee"] += 1
                x = 0.05 + 0.22 * j
                gezichten.append((
                    face, node, owner, x, rng.uniform(0.1, 0.6), 0.18, 0.24,
                    rng.uniform(0.7, 0.99), rng.randint(40, 300), vec, FACES_VERSION, cluster,
                    suggest, score, why, hidden,
                    _tijd(genomen + timedelta(days=rng.uniform(0, 30), seconds=j)),
                ))  # fmt: skip
            scans.append((node, owner, FACES_VERSION, "done", aantal, genomen.isoformat(), nu, nu))
            i += 1
        con.executemany(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
            "created_at, modified_at, search_name, taken_at, taken_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            fotos,
        )
        con.executemany(
            "INSERT INTO face (id, node_id, owner_id, x, y, w, h, score, px, vec, version, "
            "cluster, suggest_id, suggest_score, suggest_why, hidden, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            gezichten,
        )
        con.executemany(
            "INSERT INTO face_person (face_id, person_id, owner_id, verdict, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            oordelen,
        )
        con.executemany(
            "INSERT INTO face_scan (node_id, owner_id, version, state, faces, tries, prio, "
            "queued_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
            scans,
        )
        # De andere foto's van de eigenaar (uit de boom): gescand, zonder gezicht
        con.execute(
            "INSERT INTO face_scan (node_id, owner_id, version, state, faces, tries, prio, "
            "queued_at, updated_at) SELECT id, owner_id, ?, 'done', 0, 0, '', ?, ? FROM node "
            "WHERE owner_id = ? AND kind = 'file' AND ftype = 'image' AND blob_id IS NOT NULL "
            "AND id NOT IN (SELECT node_id FROM face_scan)",
            (FACES_VERSION, nu, nu, owner),
        )
        con.execute(
            "INSERT INTO face_owner (owner_id, run, grouped_at, suggested_at, wiping) "
            "VALUES (?, ?, ?, ?, 0)",
            (owner, RUN, nu, nu),
        )
        con.execute(
            "INSERT INTO photo_pref (user_id, location, memories, ocr_photos, faces, updated_at) "
            "VALUES (?, 'mij', 1, 0, 'aan', ?) "
            "ON CONFLICT (user_id) DO UPDATE SET faces = 'aan'",
            (owner, nu),
        )
        con.execute("UPDATE node SET deleted_at = ? WHERE id = ?", (nu, weg))
        levend = con.execute(
            "SELECT count(*) FROM face JOIN node ON node.id = face.node_id "
            "WHERE face.owner_id = ? AND node.dead = 0 AND face.hidden = 0",
            (owner,),
        ).fetchone()[0]
        groepen = con.execute(
            "SELECT count(*) FROM (SELECT cluster FROM face JOIN node ON node.id = face.node_id "
            "WHERE face.owner_id = ? AND node.dead = 0 AND face.hidden = 0 "
            "AND cluster IS NOT NULL AND face.id NOT IN "
            "(SELECT face_id FROM face_person WHERE verdict = 'ja') "
            "GROUP BY cluster HAVING count(*) >= ?)",
            (owner, MIN_GROUP),
        ).fetchone()[0]
        scans_ = con.execute(
            "SELECT count(*) FROM face_scan WHERE owner_id = ?", (owner,)
        ).fetchone()[0]
    foto_ids = [f[0] for f in fotos if f[1] != weg]
    met_voorstel = sorted({g[12] for g in gezichten if g[12] is not None})
    return {
        "gezichten": len(gezichten),
        "levend": levend,
        "fotos": len(fotos),
        "face_scan": scans_,
        "groepen": groepen,
        **tel,
        "foto_ids": foto_ids,
        "personen": [uuid.UUID(p).hex for p in met_voorstel],
    }


@pytest.fixture(scope="module")
def boom(tmp_path_factory):
    data = tmp_path_factory.mktemp("gezichten")
    begin = time.monotonic()
    uit = bench.maak(data, bench.Plan(n=N, seed=8))
    zonder_2fa(data / "platform.db")
    extra = _gezichten(data / "platform.db", bench.ROLLEN["eigenaar"], GEZICHTEN)
    extra["seconden"] = round(time.monotonic() - begin, 1)
    print(uit.kort())
    return data, extra


def _rustig(db: Path, seconden: float = 180) -> list[tuple]:
    """Wacht tot er geen job loopt of klaarstaat (het werk van het starten: de sweep van
    gezichten, de backfill); geeft wat er daarna nog in de rij staat (later)."""
    eind = time.monotonic() + seconden
    while True:
        with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as con:
            nu = _tijd(datetime.now(UTC).replace(tzinfo=None))
            bezig = con.execute(
                "SELECT type, status FROM job WHERE status = 'running' "
                "OR (status = 'queued' AND run_after <= ?)",
                (nu,),
            ).fetchall()
            if not bezig:
                return con.execute(
                    "SELECT type, status FROM job WHERE status IN ('queued', 'running')"
                ).fetchall()
        if time.monotonic() > eind:
            raise AssertionError(f"de jobs van het starten liepen nog na {seconden} s: {bezig}")
        time.sleep(0.5)


def _vragen(rng: random.Random, extra: dict, cursors: list[str]) -> dict[str, list[tuple]]:
    """PER_RONDE vragen per route: (pad, params)."""
    out: dict[str, list[tuple]] = {k: [] for k in POORT}
    for _ in range(PER_RONDE):
        if rng.random() < 0.5:
            out["voorstellen"].append(("/api/v1/people/faces/suggestions", {}))
        else:
            persoon = str(uuid.UUID(rng.choice(extra["personen"])))
            out["voorstellen"].append(("/api/v1/people/faces/suggestions", {"person": persoon}))
        if rng.random() < 0.6 or not cursors:
            out["groepen"].append(("/api/v1/people/faces/groups", {}))
        else:
            out["groepen"].append(("/api/v1/people/faces/groups", {"cursor": rng.choice(cursors)}))
        node = str(uuid.UUID(rng.choice(extra["foto_ids"])))
        out["foto"].append((f"/api/v1/files/{node}/faces", {}))
        out["stand"].append(("/api/v1/people/faces/status", {}))
    return out


def test_de_routes_van_gezichten_bij_60000(boom, tmp_path):
    data, extra = boom
    versie = sqlite3.sqlite_version
    sleutels = tmp_path / "sleutels"
    sleutels.mkdir()
    app = App(
        data,
        tmp_path / "server.log",
        env={
            "PLATFORM_ANALYSE_ENABLED": "true",
            "PLATFORM_FACES_KEYS": str(sleutels),
            "PLATFORM_ANALYSE_WORKER": str(tmp_path / "geen-worker.sock"),
        },
    )
    app.start()
    tijden: dict[str, list[list[float]]] = {k: [] for k in POORT}
    fout: list[str] = []
    gezien = {"voorstellen": 0, "groepen": 0, "foto": 0}
    try:
        na_start = _rustig(data / "platform.db")
        with httpx.Client(base_url=app.url, trust_env=False, timeout=30) as client:
            login = {
                "email": f"{bench.ROLLEN['eigenaar']}@{bench.DOMEIN}",
                "password": bench.WACHTWOORD,
            }
            r = client.post("/api/v1/auth/login", json=login)
            assert r.status_code == 200, r.text
            client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            stand = client.get("/api/v1/people/faces/status")
            assert stand.status_code == 200, stand.text
            # De cursors van de groepen (alle pagina's langs)
            cursors: list[str] = []
            pagina = client.get("/api/v1/people/faces/groups").json()
            while pagina["cursor"] is not None:
                cursors.append(pagina["cursor"])
                pagina = client.get(
                    "/api/v1/people/faces/groups", params={"cursor": pagina["cursor"]}
                ).json()
            for vragen in _vragen(random.Random("opwarmen"), extra, cursors).values():
                for pad, params in vragen[:OPWARMEN]:
                    client.get(pad, params=params)
            for ronde in range(1, RONDES + 1):
                for route, vragen in _vragen(random.Random(ronde), extra, cursors).items():
                    rij = []
                    for pad, params in vragen:
                        begin = time.perf_counter()
                        r = client.get(pad, params=params)
                        rij.append((time.perf_counter() - begin) * 1000)
                        if r.status_code != 200:
                            fout.append(f"{route} {pad}: {r.status_code}")
                            continue
                        body = r.json()
                        if route == "voorstellen":
                            gezien[route] += len(body.get("people", body.get("faces", [])))
                        elif route == "groepen":
                            gezien[route] += len(body["groups"])
                        elif route == "foto":
                            gezien[route] += len(body)
                    tijden[route].append(rij)
    finally:
        app.stop()

    uitkomst: dict = {
        "sqlite": versie,
        "boom": {"n": N, **{k: v for k, v in extra.items() if k not in ("foto_ids", "personen")}},
        "stand": stand.json(),
        "groepen_paginas": len(cursors) + 1,
        "jobs_na_start": [list(j) for j in na_start],
        "routes": {},
        "fout": fout,
        "gezien": gezien,
    }
    niet = []
    regels = []
    for route, rondes in tijden.items():
        alle = [t for rij in rondes for t in rij]
        p95 = statistics.median(_p(rij, 0.95) for rij in rondes)
        uitkomst["routes"][route] = {
            "p95": p95,
            "p95_per_ronde": [_p(rij, 0.95) for rij in rondes],
            "p50": _p(alle, 0.50),
            "p99": _p(alle, 0.99),
            "max": max(alle),
            "poort": POORT[route],
        }
        if p95 > POORT[route]:
            niet.append(f"{route} p95 {p95:.1f} > {POORT[route]:.0f}")
        regels.append(
            f"{route}: p95 {p95:.1f} ms (rondes "
            + ", ".join(f"{x:.1f}" for x in uitkomst["routes"][route]["p95_per_ronde"])
            + f"), p50 {_p(alle, 0.5):.1f}, p99 {_p(alle, 0.99):.1f}, max {max(alle):.1f}; "
            f"poort {POORT[route]:.0f}"
        )
    if fout:
        niet.append(f"fouten {len(fout)}")
    uitkomst["poort"] = {"gehaald": not niet, "niet": niet}
    b = uitkomst["boom"]
    verslag = "\n".join([
        "=== gezichten ===",
        f"SQLite {versie}, boom n={N} seed=8; de eigenaar: {b['gezichten']} gezichten op "
        f"{b['fotos']} foto's ({b['levend']} levend en te zien), {b['groepen']} groepen in "
        f"{uitkomst['groepen_paginas']} pagina's, {b['ja']} ja, {b['voorstel']} met een voorstel "
        f"({len(extra['personen'])} personen), {b['nee']} nee, {b['verborgen']} niet tonen, "
        f"{b['face_scan']} face_scan; klaar in {b['seconden']} s",
        *regels,
        "poorten (voorstellen, groepen, foto ≤ 100 ms; stand ≤ 50 ms): gehaald"
        if not niet else "poorten: NIET: " + "; ".join(niet),
        "=== einde gezichten ===",
    ])  # fmt: skip
    print(verslag)
    if map_ := os.environ.get("ZOEK_PERF_UIT"):
        Path(map_).mkdir(parents=True, exist_ok=True)
        (Path(map_) / "gezichten.json").write_text(json.dumps(uitkomst, indent=2) + "\n")
        (Path(map_) / "gezichten.txt").write_text(verslag + "\n")
    # De meting moet echt gezichten zien: geen lege antwoorden van een route die uit staat
    assert uitkomst["stand"]["faces"]["found"] == b["levend"], uitkomst["stand"]
    assert uitkomst["stand"]["faces"]["groups"] == b["groepen"], uitkomst["stand"]
    assert all(gezien.values()), gezien
    assert not niet, niet
