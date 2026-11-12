"""De handdruk van de opdracht text, aangevallen (bouwontwerp fase 6, Z2; Keuze 17, spike S11).

Een tweede proces van dezelfde uid als de worker (uid 10002 in het image: een gekraakte ffmpeg)
kan /proc/<pid>/fd en /proc/<pid>/mem van het tekst-kind niet openen (EACCES), ook niet in de
eerste milliseconden. handdruk_tekst.py draait de echte opdracht (een Worker, ops/text.py,
run.spawn_handshake) met een aanvaller ernaast, in drie vormen; zie daar.

Root mag alles in /proc, dus de hulp draait nooit als root: als root via setpriv als uid 10002
met de python van /opt/ci-venv (die uid kan de python van uv onder /root niet lezen), anders
(CI) gewoon als de gebruiker zelf.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.media

API = Path(__file__).resolve().parents[2]
HELPER = Path(__file__).with_name("handdruk_tekst.py")
CI_PYTHON = Path("/opt/ci-venv/bin/python")


def _command() -> list[str]:
    if os.geteuid() != 0:
        return [sys.executable, "-I", str(HELPER), str(API)]
    if not CI_PYTHON.exists() or shutil.which("setpriv") is None:
        pytest.skip("als root: setpriv en een python voor uid 10002 (/opt/ci-venv) nodig")
    return [
        "setpriv", "--reuid=10002", "--regid=10002", "--clear-groups",
        str(CI_PYTHON), "-I", str(HELPER), str(API),
    ]  # fmt: skip


@pytest.fixture(scope="module")
def aanval() -> dict:
    done = subprocess.run(
        _command(),
        capture_output=True,
        text=True,
        timeout=180,
        cwd="/",
        env={"PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 0, (done.stdout[-2000:], done.stderr[-4000:])
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_niet_als_root(aanval):
    assert aanval["uid"] != 0


def test_de_aanvaller_ziet_het_venster_van_een_leesbare_python(aanval):
    """De controle: uit een leesbare python, met 0,4 s vóór PR_SET_DUMPABLE, opent de
    aanvaller wel iets (stdin, /dev/null). Anders bewijzen de andere twee niets."""
    uit = aanval["leesbaar, traag"]
    assert uit["kinderen"] >= 1 and uit["aantal geopend"] > 0, uit
    # Maar nooit een blob via een fd: die komen pas na "klaar", en dan is het kind niet-dumpbaar
    assert not any(g["blob"] for g in uit["geopend"]), uit
    assert uit["tekst klopt"] and uit["rc"] == 0, uit


def test_een_leesbare_python_geeft_ook_mem(aanval):
    """Review golf 1, F2: in dat venster opent de aanvaller ook /proc/<pid>/mem, en die blijft
    open na de prctl (de handdruk alleen is dus niet genoeg; daarom start het kind in het image
    alleen uit python-tekst). Met Yama (ptrace_scope ≥ 1) mag dat niet: dan geen mem."""
    uit = aanval["leesbaar, traag"]
    if aanval["ptrace_scope"] in (None, 0):
        assert uit["mem geopend"] >= 1, uit
    else:
        assert uit["mem geopend"] == 0, uit


@pytest.mark.parametrize("vorm", ["kopie, traag", "kopie"])
def test_uit_de_kopie_vanaf_de_eerste_milliseconde_eacces(aanval, vorm):
    uit = aanval[vorm]
    assert uit["kinderen"] >= 1 and uit["pogingen"] >= 1, uit
    assert uit["aantal geopend"] == 0 and uit["mem geopend"] == 0, uit
    assert set(uit["fouten"]) <= {"EACCES"} and uit["fouten"].get("EACCES", 0) >= 1, uit
    assert uit["tekst klopt"] and uit["rc"] == 0, uit
    if vorm == "kopie, traag":
        # 0,4 s lang onafgebroken geprobeerd, vóór het kind zelf PR_SET_DUMPABLE zette
        assert uit["pogingen"] >= 20, uit
