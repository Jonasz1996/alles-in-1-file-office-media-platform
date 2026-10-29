"""De lokale worker: een kindproces van de app (Keuze 13). Van M1 (M0 had starten en stoppen).

    python -I -c <BOOT> <api-map> --socket <data_dir>/media/run/worker.sock --local …

`python -I -m app.media.worker` vindt `app` niet als het project niet geïnstalleerd is (-I zet
de werkmap niet in sys.path); BOOT zet de map van de API er zelf in en start dan de module. Het
kind krijgt een kale omgeving en een eigen procesgroep, en stopt alleen op pid. De sleutel van
hello (Settings.media_key, review S1) gaat alleen via de omgeving mee, nooit als argument.

- start: de worker starten en wachten tot hij luistert; daarna houdt een bewaker hem in de
  gaten. Stopt hij zonder dat de app het vroeg (een crash, kill -9, de OOM-killer), dan start
  de bewaker hem opnieuw na 1, 2, 4 … hooguit 60 s; na STABLE_S zonder stoppen begint dat weer
  bij 1 s. LocalWorker.process is steeds het proces van nu;
- stop: eerst de bewaker, dan SIGTERM op de pid (de worker stopt zijn kinderen zelf), na
  STOP_GRACE SIGKILL op de procesgroep; wacht tot waitpid;
- oneshot: een worker voor één renderproces, als media uit staat (render.py, de previews van
  video's zoals in 0.6.0). Synchroon, in de procesgroep van het renderproces (previews.render
  stopt die groep bij een tijdgrens), met een eigen willekeurige sleutel en een socket in de
  werkmap van de preview.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import resource
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings

log = logging.getLogger("platform.media")

API_ROOT = Path(__file__).resolve().parents[2]
BOOT = (
    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('app.media.worker',run_name='__main__',alter_sys=True)"
)
READY_TIMEOUT = 15.0
STOP_GRACE = 5.0
BACKOFF_FIRST = 1.0
BACKOFF_MAX = 60.0
STABLE_S = 60.0  # zo lang zonder stoppen: de backoff begint weer bij BACKOFF_FIRST
ONESHOT_PROCS = 2  # frame en meta na elkaar; nooit meer dan één ffmpeg per renderproces


@dataclass
class LocalWorker:
    process: asyncio.subprocess.Process
    socket: Path
    key: str = field(default="", repr=False)
    settings: Settings | None = field(default=None, repr=False)
    restarts: int = 0
    _guard: asyncio.Task | None = field(default=None, repr=False)
    _stopping: bool = False

    @property
    def pid(self) -> int:
        return self.process.pid


def worker_argv(settings: Settings, socket_path: Path) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-c",
        BOOT,
        str(API_ROOT),
        "--socket",
        str(socket_path),
        "--local",
        "--max-procs",
        str(settings.media_max_procs),
        "--max-transcodes",
        str(settings.media_max_transcodes),
        "--memory-mb",
        str(settings.media_job_memory_mb),
        *(["--loadtest"] if settings.media_loadtest else []),
    ]


def worker_env(key: str = "") -> dict[str, str]:
    """Kaal: PATH, LC_ALL=C en HOME=/tmp (Keuze 12), en de sleutel van hello."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", "HOME": "/tmp"}
    if key:
        env["PLATFORM_MEDIA_WORKER_KEY"] = key
    return env


def _forget(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _listening(path: Path) -> bool:
    return path.exists()


async def _launch(settings: Settings, path: Path, key: str) -> asyncio.subprocess.Process:
    """Eén worker starten en wachten tot hij luistert. Een oude socket (na kill -9) gaat eerst
    weg, anders lijkt hij meteen klaar."""
    _forget(path)
    process = await asyncio.create_subprocess_exec(
        *worker_argv(settings, path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=None,  # het log van de worker gaat naar dat van de app
        env=worker_env(key),
        cwd="/",
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(READY_TIMEOUT):
            while not _listening(path):
                if process.returncode is not None:
                    raise RuntimeError(f"de worker stopte meteen (code {process.returncode})")
                await asyncio.sleep(0.05)
    except BaseException:
        await _end(process)
        raise
    return process


async def start(
    settings: Settings, socket_path: Path | None = None, key: str | None = None
) -> LocalWorker:
    """Start de worker en wacht tot hij luistert (hooguit READY_TIMEOUT); daarna bewaakt (zie
    boven). key: standaard Settings.media_key(); client.bind(worker.socket, worker.key) praat
    ermee."""
    key = settings.media_key() if key is None else key
    path = socket_path or settings.media_socket
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    process = await _launch(settings, path, key)
    worker = LocalWorker(process, path, key, settings)
    worker._guard = asyncio.create_task(_supervise(worker), name="media-local")
    return worker


async def _supervise(worker: LocalWorker) -> None:
    """Wacht op het einde van de worker en start hem opnieuw, met backoff."""
    assert worker.settings is not None
    loop = asyncio.get_running_loop()
    delay = BACKOFF_FIRST
    while True:
        began = loop.time()
        code = await worker.process.wait()
        if worker._stopping:
            return
        if loop.time() - began >= STABLE_S:
            delay = BACKOFF_FIRST
        log.warning(
            "lokale mediaworker %s stopte (code %s); opnieuw over %g s", worker.pid, code, delay
        )
        await asyncio.sleep(delay)
        delay = min(delay * 2, BACKOFF_MAX)
        if worker._stopping:
            return
        try:
            worker.process = await _launch(worker.settings, worker.socket, worker.key)
        except Exception as exc:  # het volgende rondje probeert het later weer
            log.warning("lokale mediaworker start niet opnieuw: %s", exc)
            continue
        worker.restarts += 1
        log.info("lokale mediaworker opnieuw gestart (pid %s)", worker.pid)


async def _end(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signal.SIGTERM)
        try:
            async with asyncio.timeout(STOP_GRACE):
                await process.wait()
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


async def stop(worker: LocalWorker) -> None:
    """Eerst de bewaker (geen herstart meer), dan SIGTERM op de pid (de worker stopt zijn
    kinderen zelf), na STOP_GRACE SIGKILL op de procesgroep; wacht tot waitpid."""
    worker._stopping = True
    guard = worker._guard
    if guard is not None and not guard.done():
        guard.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await guard
    await _end(worker.process)
    _forget(worker.socket)


# --- één worker voor één renderproces ------------------------------------------------------


PR_SET_PDEATHSIG = 1


def _oneshot_child() -> None:
    """In het kind, vóór exec: de geheugengrens van het renderproces (RLIMIT_AS) niet erven (de
    worker zet zelf RLIMIT_DATA op elke ffmpeg; RLIMIT_AS kan daar niet, ffmpeg reserveert veel
    adresruimte voor threads, N8), en SIGKILL als het renderproces wegvalt (anders houdt de
    worker de stderr-pipe van previews.render open)."""
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


@contextlib.contextmanager
def oneshot(directory: Path | None = None) -> Iterator[tuple[Path, str]]:
    """Een lokale worker zolang het blok duurt: (socket, sleutel). Voor render.py zonder media
    (er draait dan geen worker van de app); hetzelfde protocol en dezelfde code, met
    --max-procs 2 en geen transcode. Het renderproces heeft geen threads, dus preexec_fn mag."""
    base = Path(tempfile.mkdtemp(prefix=".mediaworker-", dir=directory))
    path = base / "worker.sock"
    key = secrets.token_hex(32)
    argv = [
        sys.executable, "-I", "-c", BOOT, str(API_ROOT), "--socket", str(path), "--local",
        "--max-procs", str(ONESHOT_PROCS), "--max-transcodes", "0",
    ]  # fmt: skip
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        env=worker_env(key),
        cwd="/",
        preexec_fn=_oneshot_child,
    )
    try:
        deadline = time.monotonic() + READY_TIMEOUT
        while not path.exists():
            if process.poll() is not None:
                raise RuntimeError(f"de worker stopte meteen (code {process.returncode})")
            if time.monotonic() > deadline:
                raise TimeoutError("de worker luistert niet")
            time.sleep(0.02)
        yield path, key
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(STOP_GRACE)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        shutil.rmtree(base, ignore_errors=True)
