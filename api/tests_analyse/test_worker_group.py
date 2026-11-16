"""group met numpy tegen de referentie in puur Python (fase 6, X2 "De tests", laag 2; X2b).

app/analyse/worker/ops/group.py moet dezelfde groepen en voorstellen geven als
tests/analyse/groep_ref.py (die de nep-worker in laag 1 gebruikt). Daarnaast de regels die je in
de uitkomst moet kunnen zien: alleen wederzijdse buren, geen keten A–B–C, afwijzingen, de marge,
twee keer hetzelfde, en een invoer die liegt.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from app.analyse import client, wire
from app.analyse.wire import Code, Op
from app.analyse.worker.child import Job, OpFailed, StopFlag
from app.analyse.worker.ops import group as G
from tests.analyse import groep_ref
from tests_analyse import echt, fake_models
from tests_analyse.groep_maak import invoer, maak, uitvoer

pytestmark = [pytest.mark.anyio, pytest.mark.analyse_echt]


def _draai(data: bytes, args: wire.GroupArgs, *, sealed: bool = True) -> tuple[dict, bytes]:
    src = wire.sealed_memfd("in", data) if sealed else os.memfd_create("in", os.MFD_CLOEXEC)
    if not sealed:
        os.write(src, data)
    out = wire.out_memfd("uit")
    try:
        job = Job(1, args.model_dump(), [src, out], 120_000, StopFlag(), None)
        answer = G.run(job)
        return answer, wire.read_all(out, 1 << 30)
    finally:
        wire.close_all([src, out])


def _eenheid(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    return (v / np.linalg.norm(v)).astype("<f4")


def _as(i: int) -> np.ndarray:
    v = np.zeros(wire.FACE_DIM)
    v[i] = 1.0
    return v


def test_numpy_gelijk_aan_de_referentie_op_2000_gezichten():
    vecs, wie = maak(2000, seed=21)
    # de eerste twee gezichten van 60 identiteiten zijn bevestigd (persoon 0..59)
    known, person = [], []
    for p, ident in enumerate(np.unique(wie[wie >= 0])[:60]):
        idx = np.flatnonzero(wie == ident)[:2]
        known += idx.tolist()
        person += [p] * len(idx)
    rest = np.setdiff1d(np.arange(len(vecs)), known)
    alle = np.concatenate([vecs[known], vecs[rest]])
    k = len(known)
    # afgewezen: de eerste open gezichten van de eerste vijf personen, met hun eigen persoon
    rejects = []
    for p, ident in enumerate(np.unique(wie[wie >= 0])[:5]):
        open_idx = np.flatnonzero(wie[rest] == ident)
        if len(open_idx):
            rejects.append((k + int(open_idx[0]), p))
    data = invoer(alle, person, 60, rejects)
    args = wire.GroupArgs(mode="full")
    answer, raw = _draai(data, args)
    assert answer == {"n": len(alle)}
    ref = uitvoer(groep_ref.run(data, args))
    got = uitvoer(raw)
    assert len(raw) == len(alle) * wire.GROUP_OUT.size
    assert (got["s"] == ref["s"]).all()
    assert (got["g"] == ref["g"]).all()
    assert np.abs(got["f"] - ref["f"]).max() <= 1e-5
    # niet leeg: er zijn groepen en voorstellen, en de afwijzingen zijn geen voorstel
    assert (got["g"] >= 0).sum() > 500 and (got["s"] >= 0).sum() > 30
    for face, p in rejects:
        assert got["s"][face] != p
    assert (got["s"][:k] == -1).all() and (got["g"][:k] == -1).all()


def test_alleen_wederzijdse_buren():
    # c ligt het dichtst bij b, maar de beste buur van b is a (k = 1): geen rand b–c
    a = _eenheid(_as(0))
    b = _eenheid(_as(0) + 0.3 * _as(1))
    c = _eenheid(_as(0) + 0.3 * _as(1) + 0.5 * _as(2))
    data = invoer(np.stack([a, b, c]), [], 0)
    args = wire.GroupArgs(mode="full", k=1, min_group=2)
    got = uitvoer(_draai(data, args)[1])
    assert got["g"].tolist() == [0, 0, -1]
    assert got["g"].tolist() == uitvoer(groep_ref.run(data, args))["g"].tolist()


def test_een_keten_waar_a_en_c_niet_lijken_blijft_gesplitst():
    rng = np.random.default_rng(3)
    groep_a = [_eenheid(_as(0) + 0.15 * rng.standard_normal(wire.FACE_DIM) / 11) for _ in range(5)]
    groep_c = [_eenheid(_as(1) + 0.15 * rng.standard_normal(wire.FACE_DIM) / 11) for _ in range(5)]
    brug = _eenheid(_as(0) + _as(1))  # lijkt op allebei (±0,7), a en c lijken niet (±0)
    data = invoer(np.stack([*groep_a, brug, *groep_c]), [], 0)
    args = wire.GroupArgs(mode="full")
    got = uitvoer(_draai(data, args)[1])["g"].tolist()
    assert len(set(got[:5])) == 1 and len(set(got[6:])) == 1
    assert got[0] != got[6] and -1 not in got
    assert got == uitvoer(groep_ref.run(data, args))["g"].tolist()


def test_afwijzingen_en_de_marge():
    anna, bram = _eenheid(_as(0)), _eenheid(_as(1))
    lijkt_op_anna = _eenheid(_as(0) + 0.2 * _as(1))
    tussen = _eenheid(_as(0) + 0.97 * _as(1))  # bijna even dicht bij beide: binnen de marge
    data = invoer(np.stack([anna, bram, lijkt_op_anna, tussen]), [0, 1], 2)
    args = wire.GroupArgs(mode="suggest")
    got = uitvoer(_draai(data, args)[1])
    assert got["s"].tolist() == [-1, -1, 0, -1]
    assert got["f"][2] == pytest.approx(float(lijkt_op_anna @ anna), abs=1e-6)
    assert got["f"][3] == 0.0
    # "nee, dit is niet Anna": dan Bram, als die boven de drempel ligt (hier niet: ±0,2)
    data = invoer(np.stack([anna, bram, lijkt_op_anna, tussen]), [0, 1], 2, [(2, 0), (3, 1)])
    got = uitvoer(_draai(data, args)[1])
    assert got["s"].tolist() == [-1, -1, -1, 0]
    assert uitvoer(groep_ref.run(data, args))["s"].tolist() == [-1, -1, -1, 0]


def test_twee_keer_hetzelfde():
    vecs, _wie = maak(600, seed=5)
    data = invoer(vecs, [0, 0, 1], 2)
    args = wire.GroupArgs(mode="full")
    assert _draai(data, args)[1] == _draai(data, args)[1]


@pytest.mark.parametrize(
    "fout",
    ["open", "kort", "magic", "lang", "nan", "persoon", "afwijzing_bekend", "afwijzing_persoon"],
)
def test_een_invoer_die_liegt_is_bad_request(fout):
    vecs = np.stack([_eenheid(_as(i)) for i in range(4)])
    person, persons, rejects = [0, 1], 2, [(2, 0)]
    if fout == "nan":
        vecs = vecs.copy()
        vecs[3, 5] = np.nan
    if fout == "persoon":
        person = [0, 2]
    if fout == "afwijzing_bekend":
        rejects = [(1, 0)]
    if fout == "afwijzing_persoon":
        rejects = [(2, 2)]
    data = invoer(vecs, person, persons, rejects)
    if fout == "kort":
        data = data[:-4]
    if fout == "lang":
        data += b"\0" * 4
    if fout == "magic":
        data = b"PFG2" + data[4:]
    with pytest.raises(OpFailed) as caught:
        _draai(data, wire.GroupArgs(mode="full"), sealed=fout != "open")
    assert caught.value.code is Code.BAD_REQUEST


@pytest.fixture(scope="module")
def modellen(tmp_path_factory):
    return fake_models.install(tmp_path_factory.mktemp("modellen") / "modellen")


async def test_group_door_de_echte_worker(tmp_path, modellen):
    """Via de socket en het kind werk: de vectoren in een verzegelde memfd, de uitkomst in uit;
    in het antwoord alleen n."""
    vecs, _wie = maak(300, seed=8)
    data = invoer(vecs, [0, 0, 1, 1], 2)
    src, out = wire.sealed_memfd("in", data), wire.out_memfd("uit")
    async with echt.binnen(tmp_path, modellen) as (_worker, settings):
        try:
            answer = await client.call(
                settings, Op.GROUP, wire.GroupArgs(mode="full"), fds=[src, out]
            )
            raw = wire.read_all(out, 1 << 20)
        finally:
            wire.close_all([src, out])
    assert wire.GroupData.model_validate(answer).n == 300
    assert set(answer) <= {"code", "n"}  # geen vector, geen score in het bericht
    assert len(raw) == 300 * wire.GROUP_OUT.size
    got, ref = uitvoer(raw), uitvoer(groep_ref.run(data, wire.GroupArgs(mode="full")))
    assert (got["g"] == ref["g"]).all() and (got["s"] == ref["s"]).all()
