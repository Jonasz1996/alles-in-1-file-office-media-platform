"""Het blok van de analyse in de Dockerfile en de controle in de job images (fase 6, A0; Keuze 57,
K2-09, K2-15).

Het image zelf bouwt alleen in CI. Hier: het blok staat direct na `RUN uv sync --frozen --no-dev`
(die regel blijft zoals hij is), het zet de vijf pakketten met --no-cache in /opt/analyse en nooit
in /opt/venv, /run/analyse staat in de RUN van media, en de stap in ci.yml kijkt het image na met
dezelfde vijf versies als uv.lock. Een stand-in met dit blok is in de sandbox gebouwd (a0.md).
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import yaml

from tests.analyse.test_no_numpy_in_app import EXTRA

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
SYNC = "RUN uv sync --frozen --no-dev"


def _runs() -> list[str]:
    """Elke RUN als één regel (de voortzettingen met \\ samengevoegd)."""
    text = DOCKERFILE.replace("\\\n", " ")
    return [" ".join(line.split()) for line in text.splitlines() if line.startswith("RUN ")]


def _analyse_run() -> str:
    found = [run for run in _runs() if "/opt/analyse" in run]
    assert len(found) == 1
    return found[0]


def test_het_blok_staat_direct_na_uv_sync():
    lines = DOCKERFILE.splitlines()
    assert [line for line in lines if "uv sync" in line] == [SYNC]  # zonder --extra: /opt/venv
    at = lines.index(SYNC)
    assert lines[at + 1] == ""
    assert lines[at + 2].startswith("# Analyse (fase 6, Keuze 57)")
    run = next(i for i in range(at + 1, len(lines)) if lines[i].startswith("RUN "))
    assert lines[run].startswith("RUN groupadd --system --gid 10003 analyse")
    end = lines.index("COPY api/app ./app")
    assert lines[end - 1] == "" and lines[end - 2].endswith("chmod 0750 /opt/analyse")
    assert "" not in lines[run : end - 1]  # één blok: de RUN loopt door tot vlak voor COPY


# Uit de export met hashes de blokken van precies de vijf namen van analyse.txt (veilig F1)
AWK = "awk 'NR == FNR { vijf[$1]; next } /^[a-z]/ { mee = $1 in vijf } mee'"


def test_het_blok_zet_precies_vijf_pakketten_in_opt_analyse():
    run = _analyse_run()
    stappen = [
        "groupadd --system --gid 10003 analyse",
        "useradd --system --uid 10003 --gid 10003 --no-create-home analyse",
        "useradd --system --uid 10004 --gid 10003 --no-create-home omzetten",
        "uv export --frozen --no-dev --no-emit-project --no-hashes --extra analyse"
        " | grep -E '^[a-z]' | sort > /tmp/alles.txt",
        "uv export --frozen --no-dev --no-emit-project --no-hashes"
        " | grep -E '^[a-z]' | sort > /tmp/basis.txt",
        "comm -23 /tmp/alles.txt /tmp/basis.txt > /tmp/analyse.txt",
        'test "$(wc -l < /tmp/analyse.txt)" -eq 5',
        "uv export --frozen --no-dev --no-emit-project --no-header --no-annotate --extra analyse"
        f" | {AWK} /tmp/analyse.txt - > /tmp/hashes.txt",
        "test \"$(grep -c '^[a-z]' /tmp/hashes.txt)\" -eq 5",
        "uv pip install --no-cache --python /opt/venv/bin/python --no-deps --compile-bytecode"
        " --require-hashes --target /opt/analyse -r /tmp/hashes.txt",
        "rm /tmp/alles.txt /tmp/basis.txt /tmp/analyse.txt /tmp/hashes.txt",
        "chown -R root:analyse /opt/analyse",
        "chmod -R g-w,o-rwx /opt/analyse",
        "chmod 0750 /opt/analyse",
    ]
    assert run.removeprefix("RUN ").split(" && ") == stappen


EXPORT = """\
anyio==4.9.0 \\
    --hash=sha256:aa \\
    --hash=sha256:bb
numpy==2.5.4 \\
    --hash=sha256:cc
numpyx==2.5.4 \\
    --hash=sha256:dd
pywin32==311 ; sys_platform == 'win32' \\
    --hash=sha256:ee
protobuf==7.36.2 ; python_full_version >= '3.12' \\
    --hash=sha256:ff \\
    --hash=sha256:00
"""


def test_awk_neemt_precies_de_blokken_van_de_vijf(tmp_path):
    """Review A0, veilig F1: uv pip install kreeg de vijf als naam==versie, zonder hash, terwijl
    uv sync de rest met de sha256 uit uv.lock haalt. Nu gaat --require-hashes mee, met de
    blokken uit de export met hashes. De stap met awk uit de Dockerfile, hier gedraaid: een
    naam uit analyse.txt komt mee met al zijn --hash-regels (ook met een marker), een andere
    naam niet, ook niet een die ermee begint."""
    run = _analyse_run()
    (stap,) = [s for s in run.removeprefix("RUN ").split(" && ") if f"| {AWK} " in s]
    pijp = stap.split(" | ", 1)[1].replace("/tmp/", f"{tmp_path}/")
    (tmp_path / "analyse.txt").write_text(
        "numpy==2.5.4\nprotobuf==7.36.2 ; python_full_version >= '3.12'\n", encoding="utf-8"
    )
    subprocess.run(["sh", "-c", pijp], input=EXPORT, text=True, check=True, cwd=tmp_path)
    assert (tmp_path / "hashes.txt").read_text(encoding="utf-8").splitlines() == [
        "numpy==2.5.4 \\",
        "    --hash=sha256:cc",
        "protobuf==7.36.2 ; python_full_version >= '3.12' \\",
        "    --hash=sha256:ff \\",
        "    --hash=sha256:00",
    ]


def test_de_groep_analyse_bestaat_voor_run_analyse():
    runs = _runs()
    analyse = runs.index(_analyse_run())
    platform = next(i for i, r in enumerate(runs) if "useradd --system --uid 10001 platform" in r)
    media = next(i for i, r in enumerate(runs) if "useradd --system --uid 10002" in r)
    assert analyse < platform < media
    stappen = runs[media].removeprefix("RUN ").split(" && ")
    at = stappen.index("install -d -o media -g platform -m 2750 /run/media")
    assert stappen[at + 1] == "install -d -o analyse -g platform -m 2750 /run/analyse"
    assert sum("/run/analyse" in r for r in runs) == 1


def _step() -> dict:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = [s for s in ci["jobs"]["images"]["steps"] if "/opt/analyse" in str(s.get("run", ""))]
    assert len(steps) == 1
    return steps[0]


def test_images_kijkt_het_image_na():
    step = _step()
    assert step["if"] == "${{ !cancelled() && matrix.name == 'platform' }}"
    run = step["run"]
    assert "for wie in root platform media analyse omzetten; do" in run
    assert (
        'docker run --rm -i --user "$wie" --network none platform-platform:ci python3 -I - "$wie"'
        in run
    )
    script = run.split("<< 'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    compile(script, "analyse.py", "exec")
    vijf = re.search(r"^VIJF = (\{.*?\})$", script, re.M | re.S)
    assert vijf is not None
    versies = ast.literal_eval(vijf.group(1))
    assert {k.removeprefix("google."): v for k, v in versies.items()} == EXTRA
    assert "POORT = 160 * 10**6" in script
    assert '"/root/.cache/uv"' in script
