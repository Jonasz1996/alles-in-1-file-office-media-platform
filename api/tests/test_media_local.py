"""De lokale worker als kindproces van de app (bouwontwerp fase 5, Keuze 13; M1).

- na kill -9 (op pid) start de bewaker hem opnieuw, en dan werkt caps weer;
- de backoff: 1, 2, 4 … hooguit 60 s; na STABLE_S zonder stoppen weer vanaf 1 s;
- de app stopt hem bij het afsluiten (runtime.stop, zoals de lifespan), en daarna start hij
  niet meer opnieuw;
- local.oneshot (media uit, het renderproces) laat niets achter.

Stoppen alleen op pid (regel 13).
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from app.config import Settings
from app.media import client, local, runtime
from app.media.state import media_state
from app.media.worker import guard

pytestmark = pytest.mark.anyio


def media_settings(tmp_path: Path, **over) -> Settings:
    values = {"media_max_procs": 3, "media_max_transcodes": 1, **over}
    return Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32, **values)


def alive(pid: int) -> bool:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return text[text.rindex(")") + 2] not in "ZX"


async def until(cond, limit: float = 10.0) -> bool:
    for _ in range(int(limit / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return cond()


def workers_of_me() -> list[int]:
    """De kinderen van dit proces die een worker zijn (python met BOOT)."""
    found = []
    for pid, _comm in guard.children(os.getpid()):
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except FileNotFoundError:
            continue
        if b"app.media.worker" in cmdline:
            found.append(pid)
    return found


@pytest.mark.media
async def test_herstart_na_kill_9(tmp_path, caplog):
    w = await local.start(media_settings(tmp_path), tmp_path / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        first = w.pid
        assert (await client.caps()).mode == "local"
        os.kill(first, signal.SIGKILL)  # op pid
        assert await until(lambda: w.pid != first and alive(w.pid), limit=10)
        assert await until(w.socket.exists)
        assert w.restarts == 1
        caps = await client.caps()  # dezelfde socket, dezelfde sleutel
        assert caps.mode == "local" and caps.procs == 0
        assert not alive(first)
        assert "opnieuw" in caplog.text
    finally:
        await local.stop(w)
        client.bind(None)
    pid = w.pid
    assert not alive(pid) and not w.socket.exists()
    await asyncio.sleep(1.5)  # langer dan de eerste backoff: geen nieuwe worker
    assert w.pid == pid and not workers_of_me()


class FakeProcess:
    """Een worker die meteen stopt (een crash bij de start)."""

    count = 0

    def __init__(self):
        FakeProcess.count += 1
        self.pid = 4_000_000 + FakeProcess.count
        self.returncode = 1

    async def wait(self) -> int:
        await asyncio.sleep(0)
        return 1


async def test_backoff_1_2_4_tot_60(tmp_path, monkeypatch):
    pauses: list[float] = []
    w = local.LocalWorker(FakeProcess(), tmp_path / "w.sock", "k" * 64, media_settings(tmp_path))

    async def pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) >= 9:
            w._stopping = True

    async def launch(*_args) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(local, "_pause", pause)
    monkeypatch.setattr(local, "_launch", launch)
    await asyncio.wait_for(local._supervise(w), 5)
    assert pauses == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    assert w.restarts == 8


async def test_backoff_begint_opnieuw_na_een_stabiele_run(tmp_path, monkeypatch):
    pauses: list[float] = []
    w = local.LocalWorker(FakeProcess(), tmp_path / "w.sock", "k" * 64, media_settings(tmp_path))

    async def pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) >= 3:
            w._stopping = True

    async def launch(*_args) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(local, "_pause", pause)
    monkeypatch.setattr(local, "_launch", launch)
    monkeypatch.setattr(local, "STABLE_S", 0.0)  # elke run telt als stabiel
    await asyncio.wait_for(local._supervise(w), 5)
    assert pauses == [1, 1, 1]


async def test_een_mislukte_herstart_probeert_het_later_weer(tmp_path, monkeypatch):
    pauses: list[float] = []
    w = local.LocalWorker(FakeProcess(), tmp_path / "w.sock", "k" * 64, media_settings(tmp_path))
    attempts = 0

    async def pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) >= 4:
            w._stopping = True

    async def launch(*_args) -> FakeProcess:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("de worker stopte meteen (code 2)")
        return FakeProcess()

    monkeypatch.setattr(local, "_pause", pause)
    monkeypatch.setattr(local, "_launch", launch)
    await asyncio.wait_for(local._supervise(w), 5)
    assert pauses[:3] == [1, 2, 4] and w.restarts >= 1


@pytest.mark.media
async def test_de_app_stopt_hem_bij_het_afsluiten(tmp_path):
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    settings = media_settings(tmp_path, media_enabled=True)
    runtime.bind(app, settings)
    try:
        await runtime.start(app, settings)
        w = app.state.media_local
        assert isinstance(w, local.LocalWorker) and alive(w.pid)
        assert await until(lambda: media_state(app).worker == "ok", limit=10)
        pid = w.pid
        await runtime.stop(app)  # wat de lifespan doet bij het afsluiten
        assert app.state.media_local is None
        assert w.process.returncode is not None and not alive(pid)
        await asyncio.sleep(1.5)
        assert not workers_of_me()
    finally:
        if getattr(app.state, "media_local", None) is not None:
            await local.stop(app.state.media_local)
        runtime.unbind()


@pytest.mark.media
def test_oneshot_laat_niets_achter(tmp_path):
    before = set(workers_of_me())
    with local.oneshot(tmp_path) as (path, key):
        assert path.exists() and path.parent.parent == tmp_path
        assert len(key) == 64
        started = set(workers_of_me()) - before
        assert len(started) == 1
        (pid,) = started
        # de sleutel in de omgeving, niet op de opdrachtregel; dezelfde procesgroep als hier
        assert key.encode() in Path(f"/proc/{pid}/environ").read_bytes()
        assert key.encode() not in Path(f"/proc/{pid}/cmdline").read_bytes()
        assert os.getpgid(pid) == os.getpgrp()
        assert "--max-transcodes" in Path(f"/proc/{pid}/cmdline").read_text()
    assert not alive(pid)
    assert not path.parent.exists()
    assert set(workers_of_me()) == before


@pytest.mark.media
def test_oneshot_stopt_als_het_renderproces_wegvalt(tmp_path):
    """PDEATHSIG: valt het renderproces weg (een crash, SIGKILL), dan ook zijn worker."""
    code = (
        "import sys, time; sys.path.insert(0, sys.argv[1]); from app.media import local\n"
        "with local.oneshot(local.Path(sys.argv[2])) as (path, key):\n"
        "    print(path, flush=True); time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", code, str(local.API_ROOT), str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip().endswith("worker.sock")
        (worker,) = [pid for pid, _ in guard.children(proc.pid)]
        proc.kill()  # op pid: het renderproces weg zonder op te ruimen
        proc.wait()
        for _ in range(100):
            if not alive(worker):
                break
            time.sleep(0.05)
        assert not alive(worker)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
