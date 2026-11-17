"""compose.analyse.yaml en compose.analyse-limits.yaml bij de code (fase 6, §3.11; X2a).

De container zelf draait alleen in CI (de job analyse-image, test_analyse_container.sh). Deze test
houdt de bestanden hier gelijk met de code en met het ontwerp:
- x-analyse-boot is runtime.BOOT en x-omzet-boot is runtime.OMZET_BOOT; de worker en de
  healthcheck starten daarmee (via `env -i`), en __main__ leest die argumenten; pid 1 start uit
  python-tekst (0711 van root: vanaf de exec niet dumpbaar);
- de sleutel gaat alleen via de omgeving naar platform en analyse, nooit in command of
  healthcheck, en nooit naar analyse-omzetten (K1-05);
- de afscherming: user analyse, network_mode none, read_only, cap_drop ALL,
  no-new-privileges, pids_limit, core 0, geen init, /data als tmpfs, de modellen :ro;
- analyse-omzetten alleen in het profiel omzetten, zonder analyse-run, zonder /models;
- de grenzen staan in hun eigen bestand;
- `docker compose config` met en zonder profiel, zonder OMZET_BRON (x2a.md, afwijkingen).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.analyse import runtime
from app.analyse.worker import caps, server
from app.analyse.worker import child as kids

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "api"
SINGLE = ROOT / "infra" / "single"
SOCK = "/run/analyse/worker.sock"
KEY_REF = "${ANALYSE_WORKER_KEY:-}"
CLEAN_PATH = "PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
PYTHON_TEKST = "/opt/venv/bin/python-tekst"  # 0711 van root (Dockerfile)


def load(name: str) -> dict:
    return yaml.safe_load((SINGLE / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def analyse() -> dict:
    return load("compose.analyse.yaml")


def _parse(args: list[str]) -> dict:
    """__main__.parse in een eigen proces: de import zet PR_SET_DUMPABLE op 0, en dat hoort niet
    in het proces van pytest."""
    code = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from app.analyse.worker.__main__ import parse;"
        "print(json.dumps(vars(parse(sys.argv[2:]))))"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code, str(API), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout)


def test_de_boot_is_die_van_runtime(analyse):
    assert analyse["x-analyse-boot"] == runtime.BOOT
    assert analyse["x-omzet-boot"] == runtime.OMZET_BOOT


def test_de_worker_start_met_boot(analyse):
    """pid 1 uit python-tekst: 0711 van root (de regel in de Dockerfile), dus vanaf de exec niet
    dumpbaar, en de sleutel staat dan nooit open in /proc/1/environ (review golf 5, veilig F6)."""
    command = analyse["services"]["analyse"]["command"]
    assert command == [
        PYTHON_TEKST, "-I", "-c", runtime.BOOT, "/srv", "--socket", SOCK, "--models", "/models",
    ]  # fmt: skip
    assert command == runtime.serve_argv(SOCK, "/models", python=PYTHON_TEKST, root="/srv")
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert f'-m 0711 "$(readlink -f /opt/venv/bin/python)" {PYTHON_TEKST}' in dockerfile
    assert Path(PYTHON_TEKST).name == kids.COPY_NAME
    args = _parse(command[5:])
    assert (args["socket"], args["models"], args["local"]) == (SOCK, "/models", False)
    platform = analyse["services"]["platform"]["environment"]
    assert platform["PLATFORM_ANALYSE_WORKER"] == SOCK
    assert platform["PLATFORM_ANALYSE_ENABLED"] == "true"
    assert platform["PLATFORM_FACES_KEYS"] == "/keys"


def test_de_healthcheck_zonder_de_omgeving_van_de_container(analyse):
    check = analyse["services"]["analyse"]["healthcheck"]
    test = check["test"]
    assert test[:4] == ["CMD", "/usr/bin/env", "-i", CLEAN_PATH]
    assert test[4:] == ["python", "-I", "-c", runtime.BOOT, "/srv", "--check", SOCK]
    assert _parse(test[9:])["check"] == SOCK
    assert (check["interval"], check["timeout"], check["retries"]) == ("30s", "5s", 3)
    assert check["start_period"] == "30s"


def test_de_sleutel_alleen_via_de_omgeving(analyse):
    services = analyse["services"]
    for name in ("platform", "analyse"):
        assert services[name]["environment"]["PLATFORM_ANALYSE_WORKER_KEY"] == KEY_REF
    worker = services["analyse"]
    for argv in (worker["command"], worker["healthcheck"]["test"]):
        assert not any("KEY" in part or "$" in part for part in argv)
    assert set(worker["environment"]) == {
        "PLATFORM_ANALYSE_WORKER_KEY",
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
    }
    assert worker["environment"]["OPENBLAS_NUM_THREADS"] == "1"
    assert worker["environment"]["OMP_NUM_THREADS"] == "1"


def test_de_afscherming_van_analyse(analyse):
    worker = analyse["services"]["analyse"]
    assert worker["image"] == "platform:local" and worker["pull_policy"] == "never"
    assert worker["restart"] == "unless-stopped" and worker["user"] == "analyse"
    assert worker["network_mode"] == "none" and worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert worker["security_opt"] == ["no-new-privileges:true"]
    assert worker["pids_limit"] == 64
    assert worker["ulimits"] == {"core": 0, "nofile": {"soft": 256, "hard": 256}}
    assert "init" not in worker and "ports" not in worker and "privileged" not in worker
    assert worker["tmpfs"] == [
        "/tmp:size=64m,mode=1777",
        "/data:size=1m,mode=0700,uid=10003,gid=10003",
    ]
    assert worker["volumes"] == [
        "analyse-run:/run/analyse",
        "${MODELLEN_ROOT:-/opt/platform/modellen}:/models:ro",
    ]
    assert worker["stop_grace_period"] == "5s"
    assert "analyse-run" in analyse["volumes"]


def test_de_app_krijgt_de_socket_en_de_sleutels_per_eigenaar(analyse):
    platform = analyse["services"]["platform"]
    assert platform["volumes"] == [
        "analyse-run:/run/analyse",
        "${SLEUTELS_ROOT:-/opt/platform/sleutels/gezichten}:/keys",
    ]
    assert set(platform) == {"environment", "volumes"}


def test_de_omzetter_zonder_sleutel_socket_en_modellen(analyse):
    """K1-05: een gekraakte omzetter kan zich niet als worker voordoen en geen model vervangen."""
    omzet = analyse["services"]["analyse-omzetten"]
    assert omzet["profiles"] == ["omzetten"]
    assert omzet["user"] == "omzetten" and omzet["image"] == "platform:local"
    assert "restart" not in omzet
    assert omzet["command"] == [
        "python", "-I", "-c", runtime.OMZET_BOOT, "/srv", "--bron", "/bron", "--uit", "/uit",
    ]  # fmt: skip
    assert not any("KEY" in name for name in omzet["environment"])
    assert omzet["environment"] == {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    text = json.dumps(omzet)
    assert "analyse-run" not in text and "/models" not in text and "KEY" not in text
    assert omzet["network_mode"] == "none" and omzet["read_only"] is True
    assert omzet["cap_drop"] == ["ALL"]
    assert omzet["security_opt"] == ["no-new-privileges:true"]
    assert omzet["pids_limit"] == 16 and omzet["ulimits"] == {"core": 0}
    assert [(v["target"], v.get("read_only", False)) for v in omzet["volumes"]] == [
        ("/bron", True),
        ("/uit", False),
    ]
    assert [v["source"] for v in omzet["volumes"]] == [
        "${OMZET_BRON:-/nonexistent/omzet-bron}",
        "${OMZET_UIT:-/nonexistent/omzet-uit}",
    ]
    for volume in omzet["volumes"]:
        assert volume["type"] == "bind"
        assert volume["bind"] == {"create_host_path": False}


def test_de_grenzen_in_hun_eigen_bestand(analyse):
    limits = load("compose.analyse-limits.yaml")
    assert limits["services"] == {
        "analyse": {"mem_limit": "3g", "memswap_limit": "3g", "cpus": 1.0, "cpu_shares": 256},
        "analyse-omzetten": {"mem_limit": "3g", "memswap_limit": "3g", "cpus": 1.0},
    }
    for service in analyse["services"].values():
        assert not {"mem_limit", "memswap_limit", "cpus", "cpu_shares"} & set(service)


def test_dockerfile_heeft_de_map_van_de_socket():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "install -d -o analyse -g platform -m 2750 /run/analyse" in text
    assert "/srv/zelftest-10001" in text


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker ontbreekt")
@pytest.mark.parametrize("profile", [[], ["--profile", "omzetten"]])
def test_docker_compose_config_zonder_omzet_variabelen(tmp_path, profile):
    """Zonder OMZET_BRON en OMZET_UIT (een gewone run van install.sh) werkt compose: ${…:?} zou
    daar falen, ook voor een dienst in een profiel dat niet aan staat."""
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("docker compose ontbreekt")
    env = tmp_path / ".env"
    env.write_text(f"DATA_ROOT={tmp_path}/data\nANALYSE_WORKER_KEY={'a' * 64}\n")
    files = ["compose.yaml", "compose.analyse.yaml", "compose.analyse-limits.yaml"]
    argv = ["docker", "compose", "--project-directory", str(SINGLE), "--env-file", str(env)]
    for name in files:
        argv += ["-f", str(SINGLE / name)]
    clean = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(
        [*argv, *profile, "config", "--format", "json"],
        capture_output=True,
        text=True,
        env=clean,
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    expected = {"platform", "analyse"} | ({"analyse-omzetten"} if profile else set())
    assert set(data["services"]) == expected
    worker = data["services"]["analyse"]
    assert worker["environment"]["PLATFORM_ANALYSE_WORKER_KEY"] == "a" * 64
    assert (worker["mem_limit"], worker["cpus"], worker["cpu_shares"]) == ("3221225472", 1.0, 256)
    if profile:
        omzet = data["services"]["analyse-omzetten"]
        assert "a" * 64 not in json.dumps(omzet)
        assert [v["source"] for v in omzet["volumes"]] == [
            "/nonexistent/omzet-bron",
            "/nonexistent/omzet-uit",
        ]


def test_de_containertest_kent_precies_de_opdrachten_van_built():
    """test_analyse_container.sh (de job analyse-image) kijkt twee keer naar de opdrachten van de
    echte worker: de regel `opdrachten:` van analyse-status en caps.built van pid 1. Beide moeten
    precies caps.built(server.BUILT) zijn. Na X2b staat group erbij; het script vroeg nog de lijst
    zonder group, en analyse-image werd rood (review golf 6, G6-1)."""
    script = (SINGLE / "tests" / "test_analyse_container.sh").read_text(encoding="utf-8")
    want = caps.built(server.BUILT)
    status = re.findall(r'\[ "\$opdrachten" = "([a-z_ ]*)" \]', script)
    assert status == [" ".join(want) + " "]
    proef = re.findall(r"sorted\(caps\.built\) == \[([^\]]*)\]", script)
    assert len(proef) == 1
    assert [s.strip().strip('"') for s in proef[0].split(",")] == want
