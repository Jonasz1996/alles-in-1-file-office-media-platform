"""De lokale worker: een kindproces van de app (Keuze 13). M1 bouwt de bewaking en de herstart
met backoff; M0 heeft het starten en stoppen, zodat de kern zonder Docker werkt.

    python -I -c <BOOT> <api-map> --socket <data_dir>/media/run/worker.sock --local …

`python -I -m app.media.worker` vindt `app` niet als het project niet geïnstalleerd is (-I zet
de werkmap niet in sys.path); BOOT zet de map van de API er zelf in en start dan de module. Het
kind krijgt een kale omgeving en een eigen procesgroep, en stopt alleen op pid.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

API_ROOT = Path(__file__).resolve().parents[2]
BOOT = (
    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('app.media.worker',run_name='__main__',alter_sys=True)"
)
READY_TIMEOUT = 15.0
STOP_GRACE = 5.0


@dataclass
class LocalWorker:
    process: asyncio.subprocess.Process
    socket: Path

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


def worker_env() -> dict[str, str]:
    """Kaal: PATH, LC_ALL=C en HOME=/tmp (Keuze 12)."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", "HOME": "/tmp"}


async def start(settings: Settings, socket_path: Path | None = None) -> LocalWorker:
    """Start de worker en wacht tot hij luistert (hooguit READY_TIMEOUT)."""
    path = socket_path or settings.media_socket
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    process = await asyncio.create_subprocess_exec(
        *worker_argv(settings, path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=None,  # het log van de worker gaat naar dat van de app
        env=worker_env(),
        cwd="/",
        start_new_session=True,
    )
    worker = LocalWorker(process, path)
    try:
        async with asyncio.timeout(READY_TIMEOUT):
            while not path.exists():
                if process.returncode is not None:
                    raise RuntimeError(f"de worker stopte meteen (code {process.returncode})")
                await asyncio.sleep(0.05)
    except BaseException:
        await stop(worker)
        raise
    return worker


async def stop(worker: LocalWorker) -> None:
    """SIGTERM op de pid (de worker stopt zijn kinderen zelf), na STOP_GRACE SIGKILL op de
    procesgroep; wacht tot waitpid."""
    process = worker.process
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
    with contextlib.suppress(FileNotFoundError):
        worker.socket.unlink()
