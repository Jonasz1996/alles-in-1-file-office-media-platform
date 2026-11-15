"""De cache van vectoren in het kind zoeken (fase 6, X3b; Keuze 75, K2-23, K2-13).

In dit proces, met ops/beeld.py en een eigen Cache per test: de sleutel is (eigenaar, gen,
sha256 van het bestand), forget haalt alleen die eigenaar weg, boven de grens gaat de eigenaar
eruit die het langst niet zocht, en na het opwarmen (k = 0) leest een vraag het bestand niet
opnieuw. Ook: zonder model geen bestand lezen (model_missing).
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from app.analyse import wire
from app.analyse.wire import Code
from app.analyse.worker.child import Job, OpFailed, StopFlag, Stopped
from app.analyse.worker.models import Models
from app.analyse.worker.ops import beeld as B
from tests_analyse import fake_beeld, fake_models

EVA = "e" * 32
BRAM = "b" * 32


@pytest.fixture(scope="module")
def modellen(tmp_path_factory):
    gezichten = fake_models.install(tmp_path_factory.mktemp("modellen") / "modellen")
    fake_beeld.install(gezichten)
    return Models(gezichten.root, gezichten.lock)


def rows(n: int, seed: int) -> np.ndarray:
    r = np.random.default_rng(seed)
    m = r.standard_normal((n, fake_beeld.DIM)).astype(np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def bestand(path, nums, mat, gen) -> None:
    """Zoals de app: naar een tijdelijk bestand en dan os.replace (een nieuwe inode)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(fake_beeld.vec_bytes(nums, mat, gen))
    os.replace(tmp, path)


def job(models, args: dict, fds=(), stop: StopFlag | None = None) -> Job:
    return Job(1, args, list(fds), 30_000, stop or StopFlag(), models)


def ask(models, cache, path, **args) -> list[list[int]]:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        return B.rank(job(models, args, [fd]), cache)["hits"]
    finally:
        os.close(fd)


def test_de_grens_is_400_mb():
    assert B.CACHE.limit == wire.ANALYSE_BEELD_CACHE_MB * 1024 * 1024 == 400 * 1024 * 1024
    # f32 in het geheugen plus de num's: ±136.000 foto's van 768
    assert B.need_bytes(136_000, 768) <= B.CACHE.limit < B.need_bytes(137_000, 768)


def test_twee_eigenaren_met_dezelfde_gen_elk_hun_eigen_treffers(modellen, tmp_path):
    """K2-23: met alleen gen als sleutel kreeg Bram de vectoren van Eva."""
    cache = B.Cache()
    eva, bram = tmp_path / "eva.vec", tmp_path / "bram.vec"
    rood = np.asarray(fake_beeld.KLEUR["rood"], np.float32)
    m_eva, m_bram = rows(50, 1), rows(50, 2)
    m_eva[7], m_bram[30] = rood, rood
    bestand(eva, list(range(100, 150)), m_eva, 5)
    bestand(bram, list(range(500, 550)), m_bram, 5)
    assert ask(modellen, cache, eva, owner=EVA, gen=5, text="rood", k=1)[0][0] == 107
    assert ask(modellen, cache, bram, owner=BRAM, gen=5, text="rood", k=1)[0][0] == 530
    assert ask(modellen, cache, eva, owner=EVA, gen=5, text="rood", k=1)[0][0] == 107
    assert cache.loads == 2 and set(cache.entries) == {EVA, BRAM}


def test_dezelfde_gen_met_een_andere_inhoud_laadt_opnieuw(modellen, tmp_path):
    """Na wissen begint gen opnieuw: dezelfde gen met een ander bestand is een andere sha256."""
    cache = B.Cache()
    path = tmp_path / "eva.vec"
    m = rows(20, 3)
    bestand(path, list(range(20)), m, 1)
    ask(modellen, cache, path, owner=EVA, gen=1, k=0)
    m2 = rows(20, 4)
    m2[3] = np.asarray(fake_beeld.KLEUR["blauw"], np.float32)
    bestand(path, list(range(1000, 1020)), m2, 1)
    assert ask(modellen, cache, path, owner=EVA, gen=1, text="blauw", k=1)[0][0] == 1003
    assert cache.loads == 2
    # hetzelfde bestand nog eens: uit de cache, en de sha256 niet opnieuw (dezelfde inode)
    assert ask(modellen, cache, path, owner=EVA, gen=1, text="blauw", k=1)[0][0] == 1003
    assert cache.loads == 2


def test_opwarmen_na_een_nieuw_bestand(modellen, tmp_path):
    """K2-13: de job beeld.vec stuurt na een nieuw bestand rank met k = 0; de eerste vraag
    daarna leest het bestand niet meer."""
    cache = B.Cache()
    path = tmp_path / "eva.vec"
    bestand(path, list(range(300)), rows(300, 5), 2)
    assert ask(modellen, cache, path, owner=EVA, gen=2, k=0) == []
    assert cache.loads == 1
    assert len(ask(modellen, cache, path, owner=EVA, gen=2, text="strand", k=10)) == 10
    assert cache.loads == 1
    bestand(path, list(range(301)), rows(301, 6), 3)
    ask(modellen, cache, path, owner=EVA, gen=3, k=0)
    assert cache.loads == 2 and cache.entries[EVA].gen == 3


def test_forget_haalt_alleen_die_eigenaar_weg(modellen, tmp_path):
    cache = B.Cache()
    for owner, seed in ((EVA, 7), (BRAM, 8)):
        path = tmp_path / f"{owner}.vec"
        bestand(path, list(range(10)), rows(10, seed), 1)
        ask(modellen, cache, path, owner=owner, gen=1, k=0)
    assert B.forget(job(modellen, {"owner": EVA}), cache) == {"found": True}
    assert set(cache.entries) == {BRAM}
    assert B.forget(job(modellen, {"owner": EVA}), cache) == {"found": False}
    with pytest.raises(OpFailed) as exc:
        B.forget(job(modellen, {"owner": EVA}, [0]), cache)
    assert exc.value.code == Code.BAD_REQUEST


def test_boven_de_grens_gaat_wie_het_langst_niet_zocht(modellen, tmp_path):
    """De grens hier is 3 × één bestand: de vierde eigenaar duwt de eigenaar eruit die het
    langst niet zocht, niet de eerste die laadde."""
    tijd = iter(range(1, 1000))
    one = B.need_bytes(100, fake_beeld.DIM)
    cache = B.Cache(limit=3 * one, clock=lambda: next(tijd))
    owners = ["1" * 32, "2" * 32, "3" * 32, "4" * 32]
    for i in range(len(owners)):
        bestand(tmp_path / f"{i}.vec", list(range(100)), rows(100, 10 + i), 1)
    for i in (0, 1, 2):
        ask(modellen, cache, tmp_path / f"{i}.vec", owner=owners[i], gen=1, k=0)
    ask(modellen, cache, tmp_path / "0.vec", owner=owners[0], gen=1, text="x", k=1)  # 0 zocht
    ask(modellen, cache, tmp_path / "3.vec", owner=owners[3], gen=1, k=0)
    assert set(cache.entries) == {owners[0], owners[2], owners[3]}
    assert cache.total() <= cache.limit
    # één eigenaar groter dan de grens: too_big, en niemand anders gaat eruit
    bestand(tmp_path / "groot.vec", list(range(400)), rows(400, 20), 1)
    with pytest.raises(OpFailed) as exc:
        ask(modellen, cache, tmp_path / "groot.vec", owner=owners[1], gen=1, k=0)
    assert exc.value.code == Code.TOO_BIG
    assert set(cache.entries) == {owners[0], owners[2], owners[3]}


def test_zonder_model_geen_bestand_lezen(tmp_path):
    """Ontbreekt het tekstmodel, dan model_missing voordat het bestand geladen wordt; opwarmen
    (k = 0) heeft alleen meta.json nodig. Zonder beeldmodel geeft embed_image model_missing."""
    gezichten = fake_models.install(tmp_path / "modellen")
    fake_beeld.install(gezichten, skip=(fake_beeld.NAMEN["tekst"], fake_beeld.NAMEN["beeld"]))
    models = Models(gezichten.root, gezichten.lock)
    B._loaded.clear()
    cache = B.Cache()
    path = tmp_path / "eva.vec"
    bestand(path, list(range(10)), rows(10, 30), 1)
    with pytest.raises(OpFailed) as exc:
        ask(models, cache, path, owner=EVA, gen=1, text="rood", k=3)
    assert exc.value.code == Code.MODEL_MISSING and cache.loads == 0
    assert ask(models, cache, path, owner=EVA, gen=1, k=0) == [] and cache.loads == 1
    out = os.memfd_create("uit", os.MFD_CLOEXEC)
    pv = os.memfd_create("preview", os.MFD_CLOEXEC)
    try:
        with pytest.raises(OpFailed) as exc:
            B.embed_image(job(models, {}, [pv, out]))
        assert exc.value.code == Code.MODEL_MISSING
    finally:
        os.close(out)
        os.close(pv)


def test_een_meta_json_die_niet_klopt_is_model_missing(tmp_path):
    gezichten = fake_models.install(tmp_path / "modellen")
    fake_beeld.install(gezichten)
    (gezichten.root / fake_beeld.NAMEN["meta"]).write_bytes(b"{}")
    models = Models(gezichten.root, gezichten.lock)
    B._loaded.clear()
    path = tmp_path / "eva.vec"
    bestand(path, list(range(4)), rows(4, 31), 1)
    with pytest.raises(OpFailed) as exc:
        ask(models, B.Cache(), path, owner=EVA, gen=1, k=0)
    assert exc.value.code == Code.MODEL_MISSING
    assert "{}" not in exc.value.detail


def test_stoppen_tussen_de_stukken(modellen, tmp_path):
    """Laden gaat in stukken van CHUNK_ROWS; tussen twee stukken kijkt het naar de stop."""
    cache = B.Cache()
    path = tmp_path / "eva.vec"
    bestand(path, list(range(B.CHUNK_ROWS + 5)), rows(B.CHUNK_ROWS + 5, 32), 1)
    stop = StopFlag()
    stop.begin(1)
    stop.stop(1)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(Stopped):
            B.rank(job(modellen, {"owner": EVA, "gen": 1, "k": 0}, [fd], stop), cache)
    finally:
        os.close(fd)
    assert cache.entries == {} and cache.loads == 0
