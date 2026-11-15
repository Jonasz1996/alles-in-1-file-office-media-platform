"""De echte worker van de analyse in de tests van laag 2 (fase 6, X2a).

- EchteWorker: de worker als eigen proces, zoals compose hem start (runtime.serve_argv, met
  `python -I -c BOOT`), maar met --local: geen zelftest, sandbox "off", en de kinderen starten uit
  de python van deze venv (er is geen python-tekst). De sleutel staat alleen in de omgeving; wat
  de worker op stderr zet, komt in een bestand (K1-16 kijkt daarin).
- EchteDriver: de Driver van tests/analyse/analyse_contract.py voor zo'n worker (has: faces).
- binnen(): pid 1 in dit proces, met een eigen BOOT voor de kinderen (proef.py) of andere
  grenzen; voor wat alleen met een naad te testen is.
"""

from __future__ import annotations

import contextlib
import io
import os
import secrets
import signal
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from PIL import Image

from app.analyse import client, lock, runtime, wire
from app.analyse.wire import Op
from app.analyse.worker import server
from app.config import Settings
from app.media.worker import guard
from tests.analyse import analyse_contract as C
from tests_analyse import fake_models

START_S = 30.0


def settings_for(base: Path, socket_path: Path, key: str) -> Settings:
    """Settings waarmee app.analyse.client deze worker bereikt (zoals FakeAnalyseWorker)."""
    return Settings(
        data_dir=base / "data",
        web_dist=base / "data" / "web",
        jwt_secret="test" * 8,
        analyse_enabled=True,
        analyse_worker=socket_path,
        analyse_worker_key=key,
    )


def children_of(pid: int) -> list[int]:
    return [child for child, _ in guard.children(pid)]


class EchteWorker:
    def __init__(
        self,
        base: Path,
        models: fake_models.Installed,
        *,
        extra: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
        local: bool = True,
    ) -> None:
        self.base = base
        self.models = models
        self.socket = base / "run" / "worker.sock"
        self.key = secrets.token_hex(32)
        self.stderr_path = base / "worker.stderr"
        self.extra = extra
        self.env = env
        self.local = local  # False: de zelftest draait (en faalt buiten de container)
        self.proc: subprocess.Popen | None = None

    def argv(self) -> list[str]:
        extra = [*(["--local"] if self.local else []), "--lock", str(self.models.lock)]
        extra += self.extra
        return runtime.serve_argv(self.socket, self.models.root, extra=extra)

    def environment(self) -> dict[str, str]:
        if self.env is not None:
            return dict(self.env)
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), runtime.KEY_ENV: self.key}

    def start(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        with open(self.stderr_path, "wb") as err:
            self.proc = subprocess.Popen(
                self.argv(),
                env=self.environment(),
                stdin=subprocess.DEVNULL,
                stdout=err,
                stderr=err,
                cwd=self.base,
            )
        end = time.monotonic() + START_S
        while not self.socket.exists():
            if self.proc.poll() is not None:
                raise AssertionError(f"de worker stopte met {self.proc.returncode}")
            if time.monotonic() > end:
                raise AssertionError("de worker luistert niet")
            time.sleep(0.02)

    def stop(self) -> int | None:
        proc = self.proc
        if proc is None:
            return None
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            end = time.monotonic() + 15
            while proc.poll() is None and time.monotonic() < end:
                time.sleep(0.02)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        return proc.returncode

    def __enter__(self) -> EchteWorker:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def pid(self) -> int:
        assert self.proc is not None
        return self.proc.pid

    def children(self) -> list[int]:
        return children_of(self.pid)

    def stderr(self) -> bytes:
        try:
            return self.stderr_path.read_bytes()
        except FileNotFoundError:
            return b""

    def settings(self) -> Settings:
        return settings_for(self.base, self.socket, self.key)


class EchteDriver:
    """De Driver van analyse_contract.py voor de echte worker van X2a (faces, nog geen group)."""

    name = "echt"
    has = frozenset({Op.FACES})

    def __init__(self, worker: EchteWorker) -> None:
        self.worker = worker
        self.socket = worker.socket
        self.key = worker.key
        self.models = tuple(m.name for m in lock.load(worker.models.lock))

    def settings(self) -> Settings:
        return self.worker.settings()

    async def lang(self, op: Op) -> C.Opdracht:
        # Elk scenario met lang() vraagt group of rank (needs); die staan niet in de lijst van X2a
        raise AssertionError(f"geen lange opdracht voor {op.value} in X2a")

    async def los(self) -> None:
        return None


# --- pid 1 in dit proces --------------------------------------------------------------------


@contextlib.asynccontextmanager
async def binnen(
    base: Path,
    models: fake_models.Installed,
    *,
    child_code: str = server.kids.BOOT,
    built: frozenset[Op] = server.BUILT,
    children: dict | None = None,
    watch_interval: float = 0.2,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[tuple[server.Worker, Settings]]:
    """Een Worker in deze event loop (zonder zelftest, zoals --local)."""
    sock = base / "run" / "worker.sock"
    key = secrets.token_hex(32)
    config = server.Config(
        socket=sock,
        models=models.root,
        key=key,
        built=built,
        children=children if children is not None else dict(wire.CHILDREN),
        child_code=child_code,
        lock=models.lock,
        watch_interval=watch_interval,
        clock=clock,
    )
    worker = server.Worker(config)
    await worker.start()
    try:
        yield worker, settings_for(base, sock, key)
    finally:
        await worker.stop()


# --- previews en de memfd uit ---------------------------------------------------------------


def webp(image: Image.Image, *, lossless: bool = True) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "WEBP", lossless=lossless, quality=100)
    return buf.getvalue()


def preview(data: bytes) -> int:
    return wire.sealed_memfd("preview", data)


async def faces(
    settings: Settings,
    previews: list[bytes],
    args: wire.FacesArgs | None = None,
    *,
    deadline_ms: int | None = None,
) -> tuple[wire.FacesData, bytes]:
    """faces via de client: het antwoord, en de bytes in de memfd uit."""
    fds = [preview(p) for p in previews]
    out = wire.out_memfd("uit")
    try:
        data = await client.call(
            settings,
            Op.FACES,
            args or wire.FacesArgs(),
            fds=[*fds, out],
            deadline_ms=deadline_ms,
        )
        size = os.fstat(out).st_size
        raw = os.pread(out, size, 0) if size else b""
    finally:
        wire.close_all([*fds, out])
    return wire.FacesData.model_validate(data), raw
