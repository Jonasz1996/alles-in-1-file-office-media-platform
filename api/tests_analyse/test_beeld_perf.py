"""GET /search?mode=image bij 55.000 en 100.000 foto's, onder `taskset -c 0` (fase 6, X3; BZ6
zonder het echte model; K2-13; X3b).

De echte worker (laag 2) met de nepmodellen van fake_beeld.py: het tekstmodel is een klein nepmodel,
dus wat hier telt is alles behalve het echte tekstmodel (dat meet beeld-model, BZ6): de route, de
grenzen, de fd, de tocht naar het kind zoeken, de cache in dat kind (f16 → f32), de scan en de top
1.000, en de SQL van stap 5 met de tweede controle. Alles op één core: deze test, zijn draden en
de worker met zijn kinderen (die erven het van de test).

- Eva heeft N foto's (de nodes, blobs en rijen van analysis met gewone INSERTs, met de triggers
  van 0010 en 0012) en een bestand per eigenaar met N willekeurige f16-vectoren (L2 = 1), zoals
  vecfile.build het neerzet: kop, num's, vectoren, het begin van elke sha; via os.replace.
- Warm (na een eerste vraag die het bestand laadt): VRAGEN vragen, elk met een andere tekst (geen
  treffer in de cache van 60 s). p95 ≤ 100 ms, bij 55.000 en bij 100.000.
- De eerste vraag na een nieuw bestand dat al is opgewarmd (bjobs.warm, rank met k = 0, zoals de
  job na de build doet): NIEUW keer een foto erbij, een nieuw bestand, opwarmen, één vraag; p95
  ≤ 100 ms (zoals BZ6: de p95 over de eerste vragen).
- Elk antwoord is echt zoeken op beeld (geen fallback) met treffers.
- De heap van het testproces staat in gc.freeze zolang de test loopt, zoals serve.freeze_heap
  dat in de echte app na de start doet (frozen_heap hieronder).
"""

from __future__ import annotations

import contextlib
import gc
import math
import os
import secrets
import sqlite3
import time
import uuid
from datetime import UTC, datetime

import numpy as np
import pytest

from app import db
from app.analyse import monitor
from app.analysis import VERSIONS, Step
from app.beeld import cache as bcache
from app.beeld import jobs as bjobs
from app.beeld import limits as blimits
from app.beeld import types as vt
from app.beeld import vecfile
from app.config import Settings
from app.migrate import upgrade
from tests.beeld.nep import run_jobs
from tests.conftest import admin, anyio_backend, certs, client  # noqa: F401
from tests.test_files import root
from tests.test_shares import person
from tests_analyse import echt, fake_beeld, fake_models

pytestmark = pytest.mark.anyio

N_KLEIN, N_GROOT = 55_000, 100_000
VRAGEN = 40
NIEUW = 20
GRENS_MS = 100.0
STUK = 10_000


def _alle_draden(cpus: set[int]) -> None:
    for tid in os.listdir("/proc/self/task"):
        with contextlib.suppress(OSError):
            os.sched_setaffinity(int(tid), cpus)


@pytest.fixture
def een_kern():
    """taskset -c 0 voor dit proces (elke draad) en alles wat het daarna start."""
    voor = os.sched_getaffinity(0)
    kern = 0 if 0 in voor else min(voor)
    _alle_draden({kern})
    try:
        yield kern
    finally:
        _alle_draden(voor)


@pytest.fixture
def frozen_heap():
    """De heap van dit testproces gaat in gc.freeze zolang de test loopt, zoals python -m
    app.serve dat na de start doet (serve.freeze_heap, review golf 1 van fase 6, H2) en zoals
    frozen_heap in test_media_loadtest. Zonder dat liep een volle ronde van de gc hier over
    ±298.000 objecten van het testproces (pytest, de testmodules, de app) en hield die de event
    loop 100–170 ms vast, ±3 keer per run, telkens midden in een gemeten vraag (gemeten met een
    spion op gc.callbacks, deze test alleen). De echte app heeft die ronde niet. Wat de vragen
    zelf aan afval maken, ruimt de gc nog steeds op (een volle ronde kost dan 2–6 ms), en de
    grens van 100 ms blijft."""
    gc.collect()
    gc.freeze()
    yield
    gc.unfreeze()


@pytest.fixture
def worker(tmp_path, een_kern):
    gezichten = fake_models.install(tmp_path / "modellen")
    fake_beeld.install(gezichten)
    with echt.EchteWorker(tmp_path / "worker", gezichten) as w:
        yield w


@pytest.fixture
async def settings(worker):
    base = worker.base.parent
    (base / "data").mkdir()
    s = Settings(
        data_dir=base / "data",
        web_dist=base / "data" / "web",
        jwt_secret="test" * 8,
        analyse_enabled=True,
        analyse_worker=worker.socket,
        analyse_worker_key=worker.key,
        rate_limits=False,
    )
    await upgrade(s)
    yield s
    await db.get_engine(s).dispose()
    db._engine.cache_clear()


def _schoon() -> None:
    bjobs.disable()
    bcache.forget()
    blimits.reset()
    monitor._set_quiet(monitor.OFF)


def _tijd() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _vul(path, eigenaar: uuid.UUID, map_: str, start: int, aantal: int) -> None:
    """aantal foto's van eigenaar in map_: blob, node en de rij van embed (done), met gewone
    INSERTs; de triggers zetten num, mpath, dead, de FTS-rij en gen."""
    nu = _tijd()
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        for begin in range(start, start + aantal, STUK):
            rijen = range(begin, min(start + aantal, begin + STUK))
            shas = [secrets.token_hex(32) for _ in rijen]
            con.execute("BEGIN")
            con.executemany(
                "INSERT INTO blob (sha256, size, ref_count, created_at, mime, preview, "
                "preview_meta, taken_at, meta_version, render_version, pdf_state, pdf_version) "
                "VALUES (?, 2000, 1, ?, 'image/jpeg', 'none', NULL, NULL, 0, 0, NULL, 0)",
                ((sha, nu) for sha in shas),
            )
            con.executemany(
                "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
                "created_at, modified_at, deleted_at, search_name, taken_at, taken_day) "
                "VALUES (?, ?, ?, ?, 'file', 'image/jpeg', 2000, ?, ?, ?, NULL, ?, NULL, NULL)",
                (
                    (uuid.uuid4().hex, map_, eigenaar.hex, f"IMG_{i:06d}.jpg", sha, nu, nu,
                     f"img_{i:06d}.jpg")
                    for i, sha in zip(rijen, shas, strict=True)
                ),
            )  # fmt: skip
            con.executemany(
                "INSERT INTO analysis (blob_sha, step, version, state, tries, code, owner_id, "
                "queued_at, updated_at) VALUES (?, ?, ?, 'done', 0, NULL, ?, ?, ?)",
                (
                    (sha, Step.EMBED.value, VERSIONS[Step.EMBED], eigenaar.hex, nu, nu)
                    for sha in shas
                ),
            )
            con.execute("COMMIT")
    finally:
        con.close()


def _bestand(settings: Settings, path, eigenaar: uuid.UUID, seed: int) -> int:
    """Het bestand van eigenaar met een willekeurige vector per foto, en beeld_owner bij; geeft
    het aantal foto's."""
    con = sqlite3.connect(path, isolation_level=None)
    try:
        rijen = con.execute(
            "SELECT num, blob_id FROM node WHERE owner_id = ? AND ftype = 'image' AND dead = 0 "
            "ORDER BY num",
            (eigenaar.hex,),
        ).fetchall()
        gen = con.execute(
            "SELECT gen FROM beeld_owner WHERE user_id = ?", (eigenaar.hex,)
        ).fetchone()[0]
        n = len(rijen)
        directory = vecfile.ensure_dir(settings.beeld_dir)
        final = vecfile.path_for(directory, eigenaar)
        tmp = directory / f".{eigenaar.hex}.{secrets.token_hex(6)}.tmp"
        rng = np.random.default_rng(seed)
        with open(tmp, "wb") as out:
            out.write(vt.pack_head(vt.Head(vt.MODEL, VERSIONS[Step.EMBED], vt.DIM, n, gen)))
            out.write(np.asarray([r[0] for r in rijen], dtype="<i8").tobytes())
            for begin in range(0, n, STUK):
                m = rng.standard_normal((min(STUK, n - begin), vt.DIM)).astype(np.float32)
                m /= np.linalg.norm(m, axis=1, keepdims=True)
                out.write(m.astype("<f2").tobytes())
            out.write(b"".join(vt.sha_prefix(str(r[1])) for r in rijen))
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
        con.execute(
            "UPDATE beeld_owner SET built_gen = gen, built_rows = ? WHERE user_id = ?",
            (n, eigenaar.hex),
        )
        return n
    finally:
        con.close()


def p95(waarden: list[float]) -> float:
    rij = sorted(waarden)
    return rij[max(0, math.ceil(0.95 * len(rij)) - 1)]


async def _vraag(client, headers, q: str) -> float:  # noqa: F811
    begin = time.perf_counter()
    r = await client.get("/api/v1/search", params={"mode": "image", "q": q}, headers=headers)
    ms = (time.perf_counter() - begin) * 1000
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["mode"], body["fallback"], body["ranked"]) == ("image", None, True), body
    assert body["results"], "geen treffers"
    return ms


async def test_p95_bij_55000_en_100000_fotos(
    client,  # noqa: F811
    admin,  # noqa: F811
    settings,
    worker,
    een_kern,
    frozen_heap,
):
    jonas, _ = admin
    eva, eva_id = await person(client, jonas, "eva@example.nl")
    eigenaar = uuid.UUID(eva_id)
    dbpad = settings.data_dir / "platform.db"
    gemeten: dict[str, list[float]] = {}
    try:
        monitor._set_quiet(await monitor.check(settings))
        assert bjobs.ready(), monitor.status()
        assert bjobs.enable(settings)
        r = await client.patch("/api/v1/me/photo-settings", json={"beeld": "aan"}, headers=eva)
        assert r.status_code == 200, r.text
        await run_jobs(settings)
        map_ = uuid.UUID((await root(client, eva))["folder"]["id"]).hex
        klaar = 0
        for n in (N_KLEIN, N_GROOT):
            _vul(dbpad, eigenaar, map_, klaar, n - klaar)
            klaar = n
            assert _bestand(settings, dbpad, eigenaar, seed=n) == n
            await _vraag(client, eva, "rood")  # laadt het bestand in het kind zoeken
            gemeten[f"warm {n}"] = [await _vraag(client, eva, f"rood {i}") for i in range(VRAGEN)]
        # de eerste vraag na een nieuw bestand dat al is opgewarmd (K2-13)
        eerste = []
        for i in range(NIEUW):
            _vul(dbpad, eigenaar, map_, klaar, 1)
            klaar += 1
            assert _bestand(settings, dbpad, eigenaar, seed=1_000 + i) == klaar
            assert await bjobs.warm(settings, eigenaar)
            eerste.append(await _vraag(client, eva, f"blauw {i}"))
        gemeten[f"eerste na opwarmen {N_GROOT}"] = eerste
        # alles op één core: de worker en zijn kinderen ook
        for pid in (worker.pid, *worker.children()):
            assert os.sched_getaffinity(pid) == {een_kern}, pid
    finally:
        _schoon()
    for naam, tijden in gemeten.items():
        print(
            f"beeld-perf {naam}: p50 {sorted(tijden)[len(tijden) // 2]:.1f} ms, "
            f"p95 {p95(tijden):.1f} ms, max {max(tijden):.1f} ms ({len(tijden)} vragen)"
        )
    for naam, tijden in gemeten.items():
        assert p95(tijden) <= GRENS_MS, (naam, sorted(tijden))
