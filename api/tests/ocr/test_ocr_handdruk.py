"""De handdruk van de opdracht ocr, aangevallen (bouwontwerp fase 6, X1; Keuze 17, net als
tests/extract/test_media_text_handshake.py voor text).

Een tweede proces van dezelfde uid als de worker (uid 10002 in het image: een gekraakte ffmpeg)
kan /proc/<pid>/fd en /proc/<pid>/mem van kind 1 (de PGM uit de blob) en kind 2 (tesseract met
de PGM op stdin) niet openen (EACCES), ook niet in de eerste milliseconden. handdruk_ocr.py
draait de echte opdracht (een Worker, ops/ocr.py, run.spawn_handshake) met een aanvaller ernaast,
in drie vormen; zie daar. Kind 2 loopt er via kopieën die niemand mag lezen, zoals tesseract,
nice, ionice en env 0711 in het image.

Root mag alles in /proc, dus de hulp draait nooit als root: als root via setpriv als uid 10002
met de python van /opt/ci-venv, anders (CI) gewoon als de gebruiker zelf.
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
HELPER = Path(__file__).with_name("handdruk_ocr.py")
CI_PYTHON = Path("/opt/ci-venv/bin/python")
SIZE = (300, 200)


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


def fouten(uit: dict) -> dict:
    """De fouten op processen die er nog waren: "weg" (ENOENT of ESRCH terwijl /proc/<pid> niet
    meer bestond; zie handdruk_ocr.aanval_code) is geen weigering en geen lek."""
    return {k: v for k, v in uit["fouten"].items() if k != "weg"}


def keten_klopt(uit: dict) -> None:
    """De hele keten liep: kind 1 gaf de PGM (de maat staat in de samenvatting), en kind 2 (de
    onleesbare /bin/true als tesseract) gaf geen woorden, dus ook de OSD-ronde."""
    assert uit["samenvatting"] == {
        "state": "none", "code": None, "error": None, "width": SIZE[0], "height": SIZE[1],
        "osd": True,
    }, uit  # fmt: skip


def test_niet_als_root(aanval):
    assert aanval["uid"] != 0
    # Buiten het image zijn nice, ionice en env te lezen: dan de keten als onleesbare kopieën
    # (de echte keten van het image toetst test_ocr_container.sh)
    assert aanval["keten"] == "kopie"


def test_de_aanvaller_ziet_het_venster_van_een_leesbare_python(aanval):
    """De controle: uit een leesbare python, met 0,4 s vóór PR_SET_DUMPABLE, opent de
    aanvaller wel iets (stdin, /dev/null). Anders bewijzen de andere twee niets."""
    uit = aanval["leesbaar, traag"]
    assert uit["kinderen"] >= 1 and uit["aantal geopend"] > 0, uit
    # Maar nooit de blob via een fd: die komt pas na "klaar", en dan is kind 1 niet-dumpbaar.
    # En kind 2 (de onleesbare keten) opent hij ook niet
    assert not any(g["blob"] for g in uit["geopend"]), uit
    keten_klopt(uit)


def test_een_leesbare_python_geeft_ook_mem(aanval):
    """Zoals bij text (review golf 1, F2): in dat venster opent de aanvaller ook
    /proc/<pid>/mem, tenzij Yama (ptrace_scope ≥ 1) het verbiedt. Daarom start kind 1 in het
    image alleen uit python-tekst."""
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
    assert set(fouten(uit)) == {"EACCES"}, uit
    keten_klopt(uit)
    if vorm == "kopie, traag":
        # 0,4 s lang onafgebroken geprobeerd, vóór kind 1 zelf PR_SET_DUMPABLE zette
        assert uit["pogingen"] >= 20, uit


def test_python_tekst_als_die_er_is(aanval):
    """In het image staat python-tekst (0711) naast de python: dan dezelfde eis met de echte
    kopie. Hier (zonder) is het pad None, en is er geen vorm "python-tekst, traag"."""
    if aanval["python-tekst"] is None:
        assert "python-tekst, traag" not in aanval
        return
    for vorm in ("python-tekst, traag", "python-tekst"):
        uit = aanval[vorm]
        assert uit["aantal geopend"] == 0 and uit["mem geopend"] == 0, uit
        keten_klopt(uit)
