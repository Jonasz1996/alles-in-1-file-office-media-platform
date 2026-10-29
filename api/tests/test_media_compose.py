"""compose.media.yaml, het Dockerfile en de container-test bij elkaar (bouwontwerp fase 5, §2 M1).

De container zelf draait alleen in CI (de job media-image, test_media_container.sh). Deze test
houdt de bestanden hier gelijk met de code:
- de worker start in compose zoals local.py hem start (BOOT met /srv), de healthcheck ook, via
  `env -i`, en __main__ leest die argumenten;
- de sleutel van hello gaat via de omgeving naar allebei, nooit in command of healthcheck;
- de afscherming van Keuze 11 staat er letterlijk, zonder `init` (docker-init laat de omgeving
  van de container lezen, m1.md);
- de grenzen, en stop_grace_period 30 s voor platform (F3);
- het Dockerfile heeft de regels van Keuze 47 en PROBE_FILE van de zelftest;
- het afgeleide image van de spike bouwt ook op een image dat media al heeft (M0-hotfix van M1);
- ci.yml draait test_media_container.sh in media-image, en S3 nog (tot M4).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.media import local
from app.media.worker import selftest
from app.media.worker.__main__ import parse

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
SINGLE = ROOT / "infra" / "single"
SOCK = "/run/media/worker.sock"
KEY_REF = "${MEDIA_WORKER_KEY:?MEDIA_WORKER_KEY ontbreekt in .env}"
CLEAN_PATH = "PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"


def load(name: str) -> dict:
    return yaml.safe_load((SINGLE / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def media() -> dict:
    return load("compose.media.yaml")


def test_de_worker_start_zoals_local(media):
    command = media["services"]["media"]["command"]
    assert command == ["python", "-I", "-c", local.BOOT, "/srv", "--socket", SOCK]
    # BOOT haalt /srv eruit; de rest is voor __main__
    assert parse(command[5:]).socket == SOCK
    assert media["services"]["platform"]["environment"]["PLATFORM_MEDIA_WORKER"] == SOCK


def test_de_healthcheck_zonder_de_omgeving_van_de_container(media):
    check = media["services"]["media"]["healthcheck"]
    test = check["test"]
    assert test[:4] == ["CMD", "/usr/bin/env", "-i", CLEAN_PATH]
    assert test[4:] == ["python", "-I", "-c", local.BOOT, "/srv", "--check", SOCK]
    assert parse(test[9:]).check == SOCK
    assert (check["interval"], check["timeout"], check["retries"]) == ("30s", "5s", 3)


def test_de_sleutel_alleen_via_de_omgeving(media):
    services = media["services"]
    for name in ("platform", "media"):
        assert services[name]["environment"]["PLATFORM_MEDIA_WORKER_KEY"] == KEY_REF
    worker = services["media"]
    for argv in (worker["command"], worker["healthcheck"]["test"]):
        assert not any("KEY" in part or "$" in part for part in argv)
    assert set(worker["environment"]) == {"PLATFORM_MEDIA_WORKER_KEY"}


def test_de_afscherming_van_keuze_11(media):
    worker = media["services"]["media"]
    assert worker["image"] == "platform:local" and worker["pull_policy"] == "never"
    assert worker["restart"] == "unless-stopped" and worker["user"] == "media"
    assert worker["network_mode"] == "none" and worker["read_only"] is True
    assert worker["tmpfs"] == [
        "/tmp:size=64m,mode=1777",
        "/data:size=1m,mode=0700,uid=10002,gid=10002",
    ]
    assert worker["volumes"] == ["media-run:/run/media"]
    assert worker["cap_drop"] == ["ALL"] and "cap_add" not in worker
    assert worker["security_opt"] == ["no-new-privileges:true"]
    assert worker["pids_limit"] == 256
    assert worker["ulimits"] == {"core": 0, "nofile": {"soft": 1024, "hard": 1024}}
    assert worker["stop_grace_period"] == "5s"
    # docker-init (pid 1, uid media, dumpbaar) zou /proc/1/environ met de sleutel laten lezen
    assert not worker.get("init")
    for absent in ("ports", "expose", "networks", "privileged", "devices"):
        assert absent not in worker
    assert media["volumes"] == {"media-run": None}


def test_platform_krijgt_media_en_de_tijd_om_te_stoppen(media):
    platform = media["services"]["platform"]
    assert platform["environment"]["PLATFORM_MEDIA_ENABLED"] == "true"
    assert platform["volumes"] == ["media-run:/run/media"]
    assert platform["stop_grace_period"] == "30s"


def test_de_grenzen():
    limits = load("compose.media-limits.yaml")
    assert limits == {
        "services": {"media": {"mem_limit": "3g", "memswap_limit": "3g", "cpus": 3.0}}
    }


def test_het_dockerfile_heeft_de_regels_van_keuze_47():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.replace("\\\n", " ").splitlines()]
    joined = "\n".join(lines)
    platform = joined.index("useradd --system --uid 10001 platform")
    media = joined.index("useradd --system --uid 10002 --no-create-home media")
    user = joined.index("USER platform")
    assert platform < media < user  # de groep platform bestaat al; alles als root
    block = joined[media:user]
    assert re.search(r"install -d -o media -g platform -m 2750 /run/media", block)
    assert re.search(r"chmod 0711 /usr/bin/ffmpeg /usr/bin/ffprobe /usr/bin/env", block)
    probe = f"install -o platform -g platform -m 0644 /dev/null {selftest.PROBE_FILE}"
    assert probe in block
    assert selftest.PROBE_FILE == Path("/srv/zelftest-10001")
    assert selftest.MEDIA_UID == 10002 and selftest.PLATFORM_UID == 10001


def test_het_image_van_de_spike_bouwt_op_een_image_met_media():
    """M0-hotfix van M1: platform:ci heeft media nu zelf; useradd zou dan falen."""
    text = (ROOT / "api" / "tests" / "media" / "spike" / "Dockerfile").read_text(encoding="utf-8")
    run = [line for line in text.splitlines() if "useradd" in line]
    assert run == [
        "RUN id -u media >/dev/null 2>&1 || useradd --system --uid 10002 --no-create-home media"
    ]


def test_ci_draait_de_container_test_en_s3():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = ci["jobs"]["media-image"]["steps"]
    runs = [step for step in steps if "test_media_container.sh" in str(step.get("run", ""))]
    assert len(runs) == 1
    assert runs[0]["env"]["MEDIA_TEST_IMAGE"] == "platform:ci"
    assert runs[0]["env"]["MEDIA_TEST_FFMPEG"] == "7.1."
    assert "steps.bouw.outcome == 'success'" in runs[0]["if"]
    assert any("--stappen S3" in str(step.get("run", "")) for step in steps)  # tot M4


@pytest.mark.skipif(shutil.which("bash") is None, reason="geen bash")
def test_het_script_is_geldige_bash():
    script = SINGLE / "tests" / "test_media_container.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    text = script.read_text(encoding="utf-8")
    # de sleutel nooit in de uitvoer: alleen via de omgeving naar inspect.py, en gemaskeerd
    assert "set -x" not in text and 'echo "$KEY"' not in text
    assert 'MEDIA_TEST_KEY="$KEY"' in text
