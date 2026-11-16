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

**Met modellen van de echte vorm** (BEELD_PERF_MODELLEN en BEELD_PERF_LOCK, samen; de een zonder
de ander faalt): dezelfde test met die map en die lock in plaats van de nep, bijvoorbeeld SigLIP 2
op ware grootte met willekeurige gewichten (siglip_vol.py, omgezet zoals de job beeld-model). Dan
zit het tekstmodel van ware grootte op één core in elke vraag, en meet de test beide getallen van
het ontwerp (de tabel bij "Tijden" in X3): alles behalve het tekstmodel p95 ≤ 100 ms (GRENS_MS),
en het geheel p95 ≤ 250 ms (BZ6_MS). Wat het tekstmodel kost, meet hij per vraag: na de reeks een
embed_text met dezelfde tekst in hetzelfde kind zoeken (warm; de tokenizer en het model, zoals
text_vector in rank, dat geen cache heeft), min een rondgang zonder werk naar dat kind (forget van
een eigenaar die er niet is). Na de reeks en niet tussen de vragen door: dat verstoorde de reeks
(de eerste vragen na opwarmen p95 175–190 ms in plaats van ±115 ms). Vóór de review van I2 (C1)
gold hier de grens van 100 ms voor het geheel met het tekstmodel erin: strenger dan het ontwerp,
en rood voor een goed systeem (op de sandbox p95 105,7 ms warm en 113,0 ms na opwarmen bij
100.000 foto's; het tekstmodel kost daar ±40 ms per vraag op één core). Met de nep kost het
tekstmodel bijna niets: daar is de grens van 100 ms op de hele tijd precies de regel van het
ontwerp, en dat blijft zo.

Vóór de eerste vraag laadt een embed_text in zoeken de tokenizer en het tekstmodel (de regel
"beeld-perf tekstmodel laden" noemt de tijd; 1,65 s op de sandbox). Dat is langer dan
PLATFORM_BEELD_DEADLINE_MS (800 ms); daarom laadt opwarmen (rank met k = 0) het sinds de review van
I2 (U1) ook. Wat gemeten wordt, is daarna. Zonder die variabelen verandert er niets.
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
from app.analyse import client as analyse
from app.analyse import monitor, wire
from app.analyse.wire import Op
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
from tests_analyse import echt, fake_beeld, fake_models, siglip_vol

pytestmark = pytest.mark.anyio

N_KLEIN, N_GROOT = 55_000, 100_000
VRAGEN = 40
NIEUW = 20
GRENS_MS = 100.0  # alles behalve het tekstmodel, via HTTP (api-analyse)
BZ6_MS = 250.0  # het geheel met het tekstmodel van ware grootte (BZ6), alleen in de echte vorm
LEEG = "0" * 32  # een eigenaar zonder bestand: forget is dan een rondgang zonder werk
STUK = 10_000
MODELLEN_ENV, LOCK_ENV = "BEELD_PERF_MODELLEN", "BEELD_PERF_LOCK"


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
    modellen = siglip_vol.uit_omgeving(MODELLEN_ENV, LOCK_ENV)
    if modellen is None:
        modellen = fake_models.install(tmp_path / "modellen")
        fake_beeld.install(modellen)
    with echt.EchteWorker(tmp_path / "worker", modellen) as w:
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


async def _tekstmodel_laden(settings: Settings) -> float:
    """embed_text één keer (in zoeken, zoals rank): de tokenizer en het tekstmodel in het
    geheugen van zoeken. Geeft de seconden."""
    uit = wire.out_memfd("uit")
    begin = time.perf_counter()
    try:
        await analyse.call(settings, Op.EMBED_TEXT, wire.EmbedTextArgs(text="opwarmen"), fds=[uit])
    finally:
        wire.close_all([uit])
    return time.perf_counter() - begin


async def _tekstmodel_ms(settings: Settings, q: str) -> float:
    """Wat het tekstmodel van de vraag q kost in het kind zoeken (ms): embed_text met dezelfde
    tekst (de tokenizer en het model, warm, zoals text_vector in rank) min een rondgang zonder werk
    naar hetzelfde kind (forget van LEEG)."""
    uit = wire.out_memfd("uit")
    try:
        begin = time.perf_counter()
        await analyse.call(settings, Op.EMBED_TEXT, wire.EmbedTextArgs(text=q), fds=[uit])
        embed = time.perf_counter() - begin
    finally:
        wire.close_all([uit])
    begin = time.perf_counter()
    await analyse.call(settings, Op.FORGET, wire.ForgetArgs(owner=LEEG))
    rond = time.perf_counter() - begin
    return max(0.0, embed - rond) * 1000


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
    echte = siglip_vol.uit_omgeving(MODELLEN_ENV, LOCK_ENV) is not None
    gemeten: dict[str, list[float]] = {}
    tekstmodel: dict[str, list[float]] = {}  # alleen in de echte vorm: per vraag, in ms

    async def tekst_van(naam: str, vragen: list[str]) -> None:
        """In de echte vorm: wat het tekstmodel van elke vraag van naam kost, ná die reeks (een
        embed_text tussen de vragen door verstoort de gemeten reeks)."""
        if echte:
            tekstmodel[naam] = [await _tekstmodel_ms(settings, q) for q in vragen]

    try:
        monitor._set_quiet(await monitor.check(settings))
        assert bjobs.ready(), monitor.status()
        assert bjobs.enable(settings)
        r = await client.patch("/api/v1/me/photo-settings", json={"beeld": "aan"}, headers=eva)
        assert r.status_code == 200, r.text
        await run_jobs(settings)
        map_ = uuid.UUID((await root(client, eva))["folder"]["id"]).hex
        if echte:
            laden = await _tekstmodel_laden(settings)
            print(f"\nbeeld-perf tekstmodel laden (echte vorm, één core): {laden:.2f} s")
        klaar = 0
        for n in (N_KLEIN, N_GROOT):
            _vul(dbpad, eigenaar, map_, klaar, n - klaar)
            klaar = n
            assert _bestand(settings, dbpad, eigenaar, seed=n) == n
            await _vraag(client, eva, "rood")  # laadt het bestand in het kind zoeken
            naam, vragen = f"warm {n}", [f"rood {i}" for i in range(VRAGEN)]
            gemeten[naam] = [await _vraag(client, eva, q) for q in vragen]
            await tekst_van(naam, vragen)
        # de eerste vraag na een nieuw bestand dat al is opgewarmd (K2-13)
        naam, vragen = f"eerste na opwarmen {N_GROOT}", [f"blauw {i}" for i in range(NIEUW)]
        eerste = []
        for q in vragen:
            _vul(dbpad, eigenaar, map_, klaar, 1)
            klaar += 1
            assert _bestand(settings, dbpad, eigenaar, seed=1_000 + len(eerste)) == klaar
            assert await bjobs.warm(settings, eigenaar)
            eerste.append(await _vraag(client, eva, q))
        gemeten[naam] = eerste
        await tekst_van(naam, vragen)
        # alles op één core: de worker en zijn kinderen ook
        for pid in (worker.pid, *worker.children()):
            assert os.sched_getaffinity(pid) == {een_kern}, pid
    finally:
        _schoon()
    # Zonder het tekstmodel: in de echte vorm de tijd van de vraag min wat het tekstmodel van
    # dezelfde vraag kost; met de nep de tijd zelf (het nepmodel kost bijna niets)
    rest = {
        naam: [h - t for h, t in zip(tijden, tekstmodel[naam], strict=True)] if echte else tijden
        for naam, tijden in gemeten.items()
    }
    for naam, tijden in gemeten.items():
        print(
            f"beeld-perf {naam}: p50 {sorted(tijden)[len(tijden) // 2]:.1f} ms, "
            f"p95 {p95(tijden):.1f} ms, max {max(tijden):.1f} ms ({len(tijden)} vragen)"
        )
        if echte:
            print(
                f"beeld-perf {naam} zonder het tekstmodel: p50"
                f" {sorted(rest[naam])[len(rest[naam]) // 2]:.1f} ms, p95 {p95(rest[naam]):.1f}"
                f" ms (grens {GRENS_MS:.0f}); het tekstmodel p95 {p95(tekstmodel[naam]):.1f} ms;"
                f" het geheel p95 {p95(tijden):.1f} ms (BZ6, grens {BZ6_MS:.0f})"
            )
    for naam, tijden in rest.items():
        assert p95(tijden) <= GRENS_MS, (naam, sorted(tijden))
    if echte:
        for naam, tijden in gemeten.items():
            assert p95(tijden) <= BZ6_MS, (naam, sorted(tijden))
