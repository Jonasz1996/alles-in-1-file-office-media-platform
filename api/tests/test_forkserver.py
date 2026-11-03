"""De forkserver van previews (render.serve, previews.Forkserver): een kind ervan mag nooit minder
afgeschermd zijn dan het eigen renderproces van vóór de forkserver. Stoppen raakt het kind
altijd, ook vlak na de fork of als de forkserver wegvalt; een forkserver die stopt of niet meer
antwoordt, komt bij het volgende gebruik terug; en de sleutel van de mediaworker komt nooit in
de forkserver (en dus nooit in het geheugen van een kind dat een foto decodeert)."""

import asyncio
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest

from app import jobs, previews

NONE = 'print(\'{"state": "none", "meta": {}, "sizes": []}\')'
# Schrijft zijn eigen pid en die van zijn ouder, en geeft dan "none"
WHO = "import os; open('wie', 'w').write(f'{os.getpid()} {os.getppid()}'); " + NONE
# Blijft een minuut hangen (een decoder die niet ophoudt)
SLOW = "import os, time; open('pid', 'w').write(str(os.getpid())); time.sleep(60); " + NONE


@pytest.fixture
async def server():
    server = await previews.start_forkserver()
    assert server is not None
    yield server
    await previews.stop_forkserver()
    if server.proc.returncode is None:  # een test die faalde met een gestopte forkserver
        server.kill()
        await server.proc.wait()


def gone(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return True
    return state in ("Z", "X")


def in_dir(cwd: Path) -> list[int]:
    """De processen die nog in deze werkmap draaien."""
    found = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                if os.readlink(f"/proc/{name}/cwd") == str(cwd) and not gone(int(name)):
                    found.append(int(name))
            except OSError:
                continue
    return found


async def until(check, seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


def who(cwd: Path) -> tuple[int, int]:
    pid, parent = (cwd / "wie").read_text().split()
    return int(pid), int(parent)


@pytest.mark.anyio
async def test_afbreken_voordat_de_pid_er_is_stopt_het_kind_ook(
    settings, monkeypatch, tmp_path, server
):
    """De tijdgrens (of het stoppen van de app) kan komen vlak nadat het verzoek bij de
    forkserver ligt, vóór zijn pid terug is. Het kind komt er dan toch: ook dat gaat dood, en
    afbreken wacht niet REAP_SECONDS. Eerder bleef het doorlopen, zonder tijdgrens."""
    monkeypatch.setattr(previews, "CHILD", SLOW)
    task = asyncio.create_task(previews.render(settings, {}, tmp_path))
    while not server.calls:  # noqa: ASYNC110 - bewust zonder te wachten: vóór de pid
        await asyncio.sleep(0)
    [(started, _done)] = server.calls.values()
    assert not started.done()
    task.cancel()  # in dezelfde ronde: de pid is er nog niet
    started = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < previews.REAP_SECONDS / 2
    await asyncio.sleep(0.3)
    assert in_dir(tmp_path) == []


def test_de_pid_komt_pas_als_het_kind_een_eigen_sessie_heeft(tmp_path):
    """previews stopt een kind met killpg op zijn pid. Meldt de forkserver die pid vóór de
    setsid van het kind, dan mist killpg het kind (en alles wat het nog start). Hier is setsid
    traag gemaakt: de pid mag pas komen als het kind zijn eigen sessie heeft."""
    code = (
        "import os, sys, time; sys.path.insert(0, sys.argv[1]); real = os.setsid; "
        "os.setsid = lambda: (time.sleep(0.3), real())[1]; "
        "from app.render import serve; serve()"
    )
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", code, previews.CODE_ROOT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        line = {"id": 1, "code": "import time; time.sleep(30)", "cwd": str(tmp_path), "request": {}}
        proc.stdin.write((json.dumps(line) + "\n").encode())
        proc.stdin.flush()
        assert select.select([proc.stdout], [], [], 10)[0], "geen antwoord van de forkserver"
        pid = json.loads(proc.stdout.readline())["pid"]
        assert os.getsid(pid) == pid  # meteen bij de melding, zonder wachten
        os.killpg(pid, signal.SIGKILL)
        assert select.select([proc.stdout], [], [], 10)[0], "geen antwoord van de forkserver"
        assert json.loads(proc.stdout.readline()) == {"id": 1, "exit": -signal.SIGKILL}
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.anyio
async def test_forkserver_weg_tijdens_een_render_neemt_het_kind_mee(
    settings, monkeypatch, tmp_path, server
):
    """Valt de forkserver weg (doodgemaakt, de OOM-killer), dan faalt de render meteen en gaat
    het kind mee (PDEATHSIG). Eerder liep het door zonder tijdgrens: de taak was al mislukt,
    niemand stopte het nog."""
    monkeypatch.setattr(previews, "CHILD", SLOW)
    task = asyncio.create_task(previews.render(settings, {}, tmp_path))
    assert await until(lambda: (tmp_path / "pid").exists() and (tmp_path / "pid").read_text())
    pid = int((tmp_path / "pid").read_text())
    os.kill(server.proc.pid, signal.SIGKILL)
    with pytest.raises(jobs.JobError, match="forkserver weg"):
        await asyncio.wait_for(task, 10)
    assert await until(lambda: gone(pid), 3), "het kind draait nog"


@pytest.mark.anyio
async def test_forkserver_start_opnieuw_bij_het_volgende_gebruik(
    settings, monkeypatch, tmp_path, server, caplog
):
    """Gestopt (gecrasht, doodgemaakt): de volgende render start hem opnieuw, en die render
    loopt al via de nieuwe. Stopt ook die meteen weer, dan tot RESTART_SECONDS een eigen proces
    per render (geen herstartlus), daarna weer opnieuw proberen."""
    monkeypatch.setattr(previews, "CHILD", WHO)
    first = server.proc.pid
    await previews.render(settings, {}, tmp_path)
    assert who(tmp_path)[1] == first

    os.kill(first, signal.SIGKILL)
    assert await until(lambda: not server.alive)
    await previews.render(settings, {}, tmp_path)
    second = previews._server.proc.pid
    assert second != first and who(tmp_path)[1] == second
    assert "opnieuw starten" in caplog.text

    os.kill(second, signal.SIGKILL)
    assert await until(lambda: not previews._server.alive)
    await previews.render(settings, {}, tmp_path)
    assert who(tmp_path)[1] == os.getpid()  # binnen RESTART_SECONDS: een eigen proces
    assert previews._server.proc.pid == second

    previews._server.restart_after = 0  # alsof RESTART_SECONDS voorbij is
    await previews.render(settings, {}, tmp_path)
    third = previews._server.proc.pid
    assert third not in (first, second) and who(tmp_path)[1] == third
    await previews.stop_forkserver()
    assert gone(third)


@pytest.mark.anyio
async def test_forkserver_die_niet_antwoordt_gaat_weg(settings, monkeypatch, tmp_path, server):
    """Een forkserver die niet antwoordt (hier gestopt met SIGSTOP), liet eerder elke render tot
    de tijdgrens hangen, steeds opnieuw. Nu gaat hij na de eerste tijdgrens weg (REAP_SECONDS
    zonder pid), en de volgende render start een nieuwe."""
    monkeypatch.setattr(previews, "CHILD", WHO)
    monkeypatch.setattr(previews, "REAP_SECONDS", 0.5)
    os.kill(server.proc.pid, signal.SIGSTOP)
    with pytest.raises(TimeoutError):
        with anyio.fail_after(0.5):
            await previews.render(settings, {}, tmp_path)
    assert await until(lambda: server.proc.returncode is not None)
    assert server.proc.returncode == -signal.SIGKILL
    await previews.render(settings, {}, tmp_path)
    assert previews._server is not server and who(tmp_path)[1] == previews._server.proc.pid


@pytest.mark.anyio
async def test_een_kind_verandert_niets_voor_het_volgende(settings, monkeypatch, tmp_path, server):
    """Elk kind is een kopie van de forkserver: wat een kwaadaardige foto daarin verandert (hier
    de toegestane decoders, de pixelgrens en de omgeving), ziet het volgende kind niet."""
    monkeypatch.setattr(
        previews,
        "CHILD",
        "import os, sys; sys.path.insert(0, sys.argv[1]); from PIL import Image; "
        "import app.render as r; r.IMAGE_FORMATS.append('EPS'); Image.MAX_IMAGE_PIXELS = None; "
        "Image.OPEN.pop('JPEG'); os.environ['PLATFORM_JWT_SECRET'] = 'x'; " + NONE,
    )
    await previews.render(settings, {}, tmp_path)
    monkeypatch.setattr(
        previews,
        "CHILD",
        "import json, os, sys; sys.path.insert(0, sys.argv[1]); from PIL import Image; "
        "import app.render as r; open('staat', 'w').write(json.dumps([r.IMAGE_FORMATS, "
        "Image.MAX_IMAGE_PIXELS, 'JPEG' in Image.OPEN, sorted(os.environ)])); " + NONE,
    )
    await previews.render(settings, {}, tmp_path)
    formats, pixels, jpeg, env = json.loads((tmp_path / "staat").read_text())
    assert "EPS" not in formats and pixels is not None and jpeg
    assert env == ["HOME", "LANG", "PATH"]


# Zoekt in het eigen geheugen naar de sleutel (in twee helften doorgegeven, omgekeerd: zo staat
# de hele sleutel alleen in het geheugen als hij er al was)
SEEK = (
    """
import json, sys
request = json.loads(sys.stdin.read())
a, b = request["a"][::-1].encode(), request["b"][::-1].encode()
found = False
mem = open("/proc/self/mem", "rb", buffering=0)
for line in open("/proc/self/maps"):
    parts = line.split()
    if "r" not in parts[1] or (len(parts) > 5 and parts[5].startswith("[v")):
        continue
    start, end = (int(x, 16) for x in parts[0].split("-"))
    try:
        mem.seek(start)
        data = mem.read(end - start)
    except (OSError, OverflowError, ValueError):
        continue
    at = data.find(a)
    while at >= 0 and not found:
        found = data[at + len(a) : at + len(a) + len(b)] == b
        at = data.find(a, at + 1)
open("gevonden", "w").write(json.dumps(found))
"""
    + NONE
)


@pytest.mark.anyio
async def test_de_sleutel_van_de_mediaworker_komt_nooit_in_de_forkserver(
    settings, monkeypatch, tmp_path, server
):
    """Een video krijgt de sleutel van de mediaworker mee in zijn verzoek. Ging dat via de
    forkserver, dan bleef de sleutel in diens geheugen, en erfde elk volgend kind hem, ook een
    dat een (kwaadaardige) foto decodeert; vóór de forkserver kende alleen het proces van die
    video hem. Nu krijgt een verzoek met de worker altijd een eigen proces."""
    settings.media_enabled = True
    key = settings.media_key()
    assert key and previews._with_worker(settings, {"mime": "video/mp4"})["worker"]["key"] == key
    monkeypatch.setattr(previews, "CHILD", WHO)
    video, photo = tmp_path / "video", tmp_path / "foto"
    video.mkdir()
    photo.mkdir()
    await previews.render(settings, {"mime": "video/mp4", "name": "film.mp4"}, video)
    assert who(video)[1] == os.getpid()  # een eigen proces, niet een kind van de forkserver
    await previews.render(settings, {"mime": "image/jpeg", "name": "a.jpg"}, photo)
    assert who(photo)[1] == server.proc.pid

    # En zo ziet een decoder in het volgende kind van de forkserver de sleutel dus niet
    monkeypatch.setattr(previews, "CHILD", SEEK)
    half = len(key) // 2
    request = {"mime": "image/jpeg", "a": key[:half][::-1], "b": key[half:][::-1]}
    await previews.render(settings, request, photo)
    assert json.loads((photo / "gevonden").read_text()) is False
