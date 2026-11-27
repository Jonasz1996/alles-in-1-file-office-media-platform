"""De poort van groeperen (fase 6, X2 "Harde poorten"; X2b): 60.000 open gezichten (de grens van
een ronde, PLATFORM_FACES_GROUP_MAX) plus 6.000 bevestigde, mode full, in een nieuw proces op één
core met één draad: ≤ 60 s en maxrss ≤ 400 MB; op de synthetische set zuiverheid ≥ 0,99 en
recall ≥ 0,95.

Het proces doet wat het kind werk doet: de invoer in een verzegelde memfd, group.run met een Job,
de uitkomst in uit. De tijd is die van group.run; de maxrss is die van het hele proces (de import
van numpy erbij): VmHWM, de piek van dat proces sinds exec. Niet ru_maxrss: Linux geeft bij fork
(en vfork) en exec de piek van de ouder door, en dan telde de piek van pytest mee (419 MB na de
samenvoeging van golf 6, waar het proces zelf 256 MB was; zie
test_de_meting_telt_de_piek_van_pytest_niet_mee, en tests/search/test_geheugen_0010.py). De set:
tests_analyse/groep_maak.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from app.analyse import wire
from tests_analyse.groep_maak import invoer, maak, uitvoer, zuiverheid_en_recall

pytestmark = [pytest.mark.analyse_echt]

API = Path(__file__).resolve().parents[1]
OPEN = 60_000
BEKEND = 6_000
GRENS_S = 60.0
GRENS_MB = 400.0
MB = 1024 * 1024

# De piek van dit proces sinds exec, in MB (zie boven: niet ru_maxrss)
PIEK = r"""
def piek_mb():
    for regel in open("/proc/self/status"):
        if regel.startswith("VmHWM:"):
            return int(regel.split()[1]) / 1024
    raise SystemExit("geen VmHWM")
"""

KIND = (
    PIEK
    + r"""
import json, os, sys, time
from app.analyse import wire
from app.analyse.worker.child import Job, StopFlag
from app.analyse.worker.ops import group
data = open(sys.argv[1], "rb").read()
src, out = wire.sealed_memfd("in", data), wire.out_memfd("uit")
del data
args = wire.GroupArgs(mode="full").model_dump()
start = time.perf_counter()
answer = group.run(Job(1, args, [src, out], 120_000, StopFlag(), None))
took = time.perf_counter() - start
size = os.fstat(out).st_size
with open(sys.argv[2], "wb") as f:
    f.write(os.pread(out, size, 0))
print(json.dumps({"s": took, "maxrss_mb": piek_mb(), "n": answer["n"]}))
"""
)


def test_de_meting_telt_de_piek_van_pytest_niet_mee():
    """Houdt pytest 256 MB vast, dan meet een kind dat zelf weinig doet toch alleen zijn eigen
    piek; ru_maxrss van datzelfde kind (de oude meting) neemt de 256 MB van pytest mee."""
    blok = np.ones(256 * MB // 8)  # aangeraakt: 256 MB in de RSS van pytest
    try:
        kind = PIEK + "import resource\n"
        kind += "print(piek_mb(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)\n"
        done = subprocess.run(
            [sys.executable, "-c", kind], cwd=API, capture_output=True, text=True, timeout=60
        )
    finally:
        del blok
    assert done.returncode == 0, done.stderr[-2000:]
    piek, oud = map(float, done.stdout.split())
    assert oud >= 256, (piek, oud)
    assert piek < 64, (piek, oud)


def test_60000_gezichten_binnen_60_s_en_400_mb(tmp_path):
    vecs, wie = maak(OPEN + BEKEND, seed=6)
    # de bevestigde: van de identiteiten met de meeste gezichten, steeds de eerste
    tel = {}
    bekend, person, persoon_van = [], [], {}
    for i, ident in enumerate(wie.tolist()):
        if ident < 0 or len(bekend) >= BEKEND:
            continue
        tel[ident] = tel.get(ident, 0) + 1
        if tel[ident] <= 2:
            persoon_van.setdefault(ident, len(persoon_van))
            bekend.append(i)
            person.append(persoon_van[ident])
    rest = np.setdiff1d(np.arange(len(vecs)), bekend)
    assert len(rest) == OPEN and len(bekend) == BEKEND
    data = invoer(np.concatenate([vecs[bekend], vecs[rest]]), person, len(persoon_van))
    (tmp_path / "in").write_bytes(data)
    del data
    cpu = min(os.sched_getaffinity(0))
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("OPENBLAS", "OMP", "MKL"))},
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    done = subprocess.run(
        [
            "taskset",
            "-c",
            str(cpu),
            sys.executable,
            "-c",
            KIND,
            str(tmp_path / "in"),
            str(tmp_path / "uit"),
        ],
        cwd=API,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    meting = json.loads(done.stdout.strip().splitlines()[-1])
    got = uitvoer((tmp_path / "uit").read_bytes())
    assert len(got) == OPEN + BEKEND == meting["n"]
    zuiver, recall = zuiverheid_en_recall(got["g"][BEKEND:], wie[rest])
    voorstellen = int((got["s"][BEKEND:] >= 0).sum())
    print(
        f"\ngroeperen {OPEN} open + {BEKEND} bevestigd op cpu {cpu}: {meting['s']:.1f} s, "
        f"maxrss {meting['maxrss_mb']:.0f} MB, zuiverheid {zuiver:.4f}, recall {recall:.4f}, "
        f"{int(got['g'].max()) + 1} groepen, {voorstellen} voorstellen"
    )
    assert meting["s"] <= GRENS_S
    assert meting["maxrss_mb"] <= GRENS_MB
    assert zuiver >= 0.99 and recall >= 0.95
    # elk voorstel van een open gezicht is de persoon van zijn eigen identiteit
    terug = {p: ident for ident, p in persoon_van.items()}
    fout = sum(
        1
        for face, p in zip(rest.tolist(), got["s"][BEKEND:].tolist(), strict=True)
        if p >= 0 and terug[p] != wie[face]
    )
    assert fout <= voorstellen * 0.01
    assert wire.GROUP_OUT.size * len(got) == (tmp_path / "uit").stat().st_size
