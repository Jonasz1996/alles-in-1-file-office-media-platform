"""Het blok van de analyse in de Dockerfile en de controle in de job images (fase 6, A0; Keuze 57,
K2-09, K2-15).

Het image zelf bouwt alleen in CI. Hier: het blok staat direct na `RUN uv sync --frozen --no-dev`
(die regel blijft zoals hij is), het zet de vijf pakketten met --no-cache in /opt/analyse en nooit
in /opt/venv, /run/analyse staat in de RUN van media, en de stap in ci.yml kijkt het image na met
dezelfde vijf versies als uv.lock. Een stand-in met dit blok is in de sandbox gebouwd (a0.md).

En de job analyse-image (I2, x3a.md Open 9): na X2b zet hij de echte bron van SigLIP 2 om in de
dienst analyse-omzetten, met beeld_model van install.sh (test_omzetter_echt.sh), maar alleen achter
een poort zoals die van beeld-model (git diff of "beeld-model: ja" in de PR; de bron is ±1,55 GB,
K2-03). Nooit met OMZETTER_SRC: in CI altijd de echte models.lock en huggingface.co.
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


# --- de job analyse-image: de echte bron in analyse-omzetten (I2, x3a.md Open 9) ---------------

CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
OMZETTER = ROOT / "infra" / "single" / "tests" / "test_omzetter_echt.sh"
# Wat de omzetting van install.sh in analyse-omzetten raakt (vanaf de wortel van de repository)
OMZET_PADEN = [
    "Dockerfile",
    "api/uv.lock",
    "infra/single/install.sh",
    "infra/single/compose.yaml",
    "infra/single/compose.analyse.yaml",
    "infra/single/compose.analyse-limits.yaml",
    "api/app/analyse/worker/omzetten",
    "api/app/analyse/tokenizer.py",
    "api/app/analyse/onnxw.py",
    "api/app/analyse/lock.py",
    "api/app/analyse/models.lock",
    "api/app/analyse/licenties",
    "infra/single/tests/test_omzetter_echt.sh",
]


def _job(naam: str) -> dict:
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"][naam]


def _stap(stappen: list[dict], deel: str) -> tuple[int, dict]:
    gevonden = [(i, s) for i, s in enumerate(stappen) if deel in str(s.get("run", ""))]
    assert len(gevonden) == 1, deel
    return gevonden[0]


def _poort_kern(script: str) -> list[str]:
    """De regels die beslissen (van, tot en reeks; de regel van de PR), zonder paden en reden."""
    return [
        regel.strip()
        for regel in script.splitlines()
        if regel.strip().startswith(("if [", "elif [", "van=", "if printf", "elif ! git"))
    ]


def test_analyse_image_vergelijkt_met_git_diff_zoals_beeld_model():
    job = _job("analyse-image")
    stappen = job["steps"]
    assert stappen[0]["uses"] == "actions/checkout@v4" and stappen[0]["with"]["fetch-depth"] == 0
    i, poort = next((i, s) for i, s in enumerate(stappen) if s.get("id") == "omzet-poort")
    assert poort["if"] == "${{ !cancelled() }}"
    beeld = next(s for s in _job("beeld-model")["steps"] if s.get("id") == "poort")
    assert poort["env"] == beeld["env"]  # dezelfde gegevens, de tekst van de PR via env
    script = poort["run"]
    assert "${{" not in script  # geen expressie in het script
    assert _poort_kern(script) == _poort_kern(beeld["run"])
    m = re.findall(r"^paden\+?=\((.*)\)$", script, re.M)
    assert re.search(r"^paden=\(", script, re.M)
    assert [p for g in m for p in g.split()] == OMZET_PADEN
    for pad in OMZET_PADEN:  # een tikfout zou de stap stil overslaan
        assert (ROOT / pad).exists(), pad
    assert 'git diff --name-only "$reeks" -- "${paden[@]}"' in script
    assert 'echo "draaien=$draaien" >> "$GITHUB_OUTPUT"' in script
    assert '| tee "$RUNNER_TEMP/analyse-image/omzet-poort.txt"' in script
    # na de e2e van X2b: die stap houdt zijn plek
    e2e, _ = _stap(stappen, "pytest ../e2e/test_zoek_gezichten_echt.py")
    assert e2e < i


def test_analyse_image_zet_de_echte_bron_om_in_analyse_omzetten():
    job = _job("analyse-image")
    stappen = job["steps"]
    i, stap = _stap(stappen, "bash infra/single/tests/test_omzetter_echt.sh")
    assert stappen[i - 1].get("id") == "omzet-poort"
    assert stappen[i + 1]["uses"] == "actions/upload-artifact@v4"
    assert stap["if"] == (
        "${{ !cancelled() && steps.bouw.outcome == 'success' && "
        "steps.omzet-poort.outputs.draaien == 'ja' }}"
    )
    # het image van deze run en de echte models.lock: nooit OMZETTER_SRC in CI
    assert stap["env"] == {
        "OMZETTER_IMAGE": "platform:ci",
        "OMZETTER_OUT": "${{ runner.temp }}/analyse-image",
    }
    assert "OMZETTER_SRC" not in stap["run"]
    assert "continue-on-error" not in stap  # rood is rood: dit is het bewijs van Open 9
    assert stap["run"].splitlines()[0] == 'mkdir -p "$OMZETTER_OUT"'  # als de runner, niet root
    # de bron (±1,55 GB; 26 s op de runner van beeld-model, x3a.md) en de omzetting (±8 s) passen
    # ruim in 15 min. curl mag per bestand langer (--max-time 600 s plus 1 s per MB): zo traag is
    # de stap rood op zijn timeout. De job houdt de 45 min van vóór deze stap
    assert stap["timeout-minutes"] == 15
    assert job["timeout-minutes"] >= 45 + stap["timeout-minutes"]


def test_de_samenvatting_van_analyse_image_noemt_de_omzetter():
    stappen = _job("analyse-image")["steps"]
    laatste = stappen[-1]
    assert laatste["name"] == "Samenvatting" and laatste["if"] == "always()"
    run = laatste["run"]
    assert run.splitlines()[0] == 'echo "=== kort ==="'
    regel = 'echo "X3 de echte bron in de dienst analyse-omzetten (test_omzetter_echt.sh):"'
    assert regel in run
    na = run.split(regel, 1)[1]
    assert '"$RUNNER_TEMP/analyse-image/omzet-poort.txt"' in na
    assert '"$RUNNER_TEMP/analyse-image/omzetter.txt"' in na
    assert 'echo "  liep niet"' in na


def test_test_omzetter_echt_draait_beeld_model_van_install_sh():
    script = OMZETTER.read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in script
    # het opruimen staat meteen na mktemp, vóór de eerste stap die kan falen, en docker_root (dat
    # cleanup onder set -u leest) staat er al (review I2, V1)
    werk = script.index('\nWERK=$(mktemp -d "${TMPDIR:-/tmp}/omzettertest.XXXXXX")\n')
    trap = script.index("\ntrap cleanup EXIT\n")
    assert script.index("\ndocker_root=$(") < script.index("\ncleanup() {") < werk < trap
    assert "\n" not in script[werk + 1 : trap], "trap op de regel na mktemp"
    assert trap < script.index('mkdir -p "$OUT"')
    # install.sh zelf (als bibliotheek), met de analyse aan en de echte compose-bestanden
    assert "export PLATFORM_ROOT=$WERK/platform INSTALL_LIB=1\n" in script
    assert '. "$install_sh"\n' in script
    assert "\ncompose_files 0 0 0 1\n" in script
    assert re.search(r"^  beeld_model$", script, re.M)
    # wat compose.analyse.yaml de dienst geeft, verwacht het script letterlijk
    dienst = yaml.safe_load((ROOT / "infra" / "single" / "compose.analyse.yaml").read_text())
    omzet = dienst["services"]["analyse-omzetten"]
    tmp = next(t for t in omzet["tmpfs"] if t.startswith("/tmp:"))
    verwacht = (
        f"user {omzet['user']} read_only network {omzet['network_mode']} tmpfs {tmp} "
        f"cap_drop {','.join(omzet['cap_drop'])} {','.join(omzet['security_opt'])} "
    )
    assert omzet["read_only"] is True and omzet["network_mode"] == "none"
    # en de grenzen precies, niet met * erachter (review I2, U5): die van
    # compose.analyse-limits.yaml als docker ze kan geven, anders geen
    limits = ROOT / "infra" / "single" / "compose.analyse-limits.yaml"
    omzet_grenzen = yaml.safe_load(limits.read_text())["services"]["analyse-omzetten"]
    assert (omzet_grenzen["mem_limit"], omzet_grenzen["cpus"]) == ("3g", 1.0)
    assert f'"{verwacht}"*)' not in script
    assert f'"{verwacht}$grenzen")' in script
    docker_kan = "if [ \"$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}' 2>/dev/null)\""
    kan = script.index(docker_kan + ' = "true true" ]; then\n')
    assert (
        script.index('    grenzen="mem_limit 3072MiB cpus 1" waar=""\n', kan)
        < script.index('    grenzen="mem_limit geen cpus geen" waar=', kan)
        < script.index(f'"{verwacht}$grenzen")', kan)
    )
    # de uitkomst tegen models.lock: de regels van siglip2-b32-256 met url -, zonder bron/
    assert "lock=$SRC/api/app/analyse/models.lock" in script
    assert '"$BEELD_MAP"/bron/*) continue ;;' in script
    assert '[ "$url" = - ] || continue' in script
