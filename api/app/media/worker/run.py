"""Een kind van de worker starten, bewaken en stoppen (bouwontwerp fase 5, Keuze 12). Frozen.

Elk kind (ffmpeg of ffprobe):
- krijgt zijn fd's op 3, 4, … via dup2 in het kind zelf, nooit in de ouder: daar kan fd 3 de
  epoll van asyncio zijn (spike punt 3). In het kind eerst een kopie boven alle doelen
  (F_DUPFD_CLOEXEC), dan dup2 naar het doel (dat haalt CLOEXEC eraf). Alle andere fd's van de
  worker zijn CLOEXEC (PEP 446, en recvmsg met MSG_CMSG_CLOEXEC), maar daar rekent het kind niet
  op: alles boven de doelen krijgt in het kind CLOEXEC (close_range met CLOSE_RANGE_CLOEXEC),
  zodat ook een fd die per ongeluk erfbaar is (een bibliotheek, een dup2) niet bij ffmpeg komt;
- leest een bron vanaf positie 0: de positie van een fd uit SCM_RIGHTS is gedeeld met de app en
  met elk vorig kind (spike punt 1). Daarom lseek(0) vóór elke start, en nooit twee kinderen
  tegelijk op één fd (de opdrachten draaien hun kinderen na elkaar);
- draait in een eigen procesgroep met een kale omgeving (PATH, LC_ALL=C, HOME=/tmp) en de
  grenzen RLIMIT_CORE 0, RLIMIT_NOFILE 256, RLIMIT_FSIZE 0 (behalve synth, dat een bestand
  schrijft) en RLIMIT_DATA (spike punt 6);
- stopt met killpg: SIGTERM, na 2 s SIGKILL, en telt mee tot waitpid klaar is;
- gaat mee als de worker weggaat (SIGKILL, de OOM-killer, een crash): PR_SET_PDEATHSIG met
  SIGKILL, en was de worker al weg vóór die stond (de ppid is niet meer de worker), dan stopt
  het kind meteen. Zo houdt geen wees de fd's van een opdracht vast, en blijft het bij de
  grens van processen (regel 13).

Wachten gaat via een pidfd in de event loop en os.wait4, zodat de CPU-tijd en het geheugen van
het kind er zijn (bench, /beheer). stderr wordt gelezen terwijl het kind loopt: de regels van
-progress worden voortgang, de rest gaat in een staart van hooguit DETAIL_CHARS.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import fcntl
import os
import re
import resource
import signal
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.media.wire import DETAIL_CHARS

MB = 1024 * 1024
STOP_GRACE = 2.0
FIRST_FD = 3
NOFILE = 256
_PROGRESS = re.compile(rb"^([a-z][a-z0-9_]*)=(.*)$")
_TAIL_BYTES = 16 * 1024
Progress = Callable[[int, float | None], None]
PR_SET_PDEATHSIG = 1
CLOSE_RANGE_CLOEXEC = 4
# Vooraf geladen: in het kind (na fork, vóór exec) alleen nog aanroepen
_LIBC = ctypes.CDLL(None, use_errno=True)
_CLOSE_RANGE = getattr(_LIBC, "close_range", None)  # glibc 2.34 en Linux 5.11
if _CLOSE_RANGE is not None:
    _CLOSE_RANGE.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.c_int)


def _cloexec_from(first: int) -> None:
    """In het kind: elke fd vanaf first krijgt CLOEXEC en is na exec weg. Sluiten kan hier nog
    niet: de foutpipe van subprocess (CLOEXEC) moet tot exec open blijven. Zonder close_range
    (ENOSYS, of EPERM van seccomp) per fd uit /proc/self/fd."""
    if _CLOSE_RANGE is not None and _CLOSE_RANGE(first, 0xFFFFFFFF, CLOSE_RANGE_CLOEXEC) == 0:
        return
    for name in os.listdir("/proc/self/fd"):
        if int(name) >= first:
            with contextlib.suppress(OSError):  # de fd van listdir zelf is dan al dicht
                os.set_inheritable(int(name), False)


def environment() -> dict[str, str]:
    """De kale omgeving van een kind (Keuze 12)."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", "HOME": "/tmp"}


def reserve_low_fds(count: int = 32) -> list[int]:
    """Houdt fd 3 … 3+count−1 bezet met /dev/null (CLOEXEC), vóór de event loop start. Dan
    krijgt geen pipe of socket van de worker zo'n nummer, en kan het kind ze veilig
    vervangen (dezelfde truc als de spike, gedeeld.py)."""
    taken = []
    for target in range(FIRST_FD, FIRST_FD + count):
        try:
            os.fstat(target)
            continue  # al in gebruik (door de aanroeper); niet aanraken
        except OSError:
            pass
        fd = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        if fd != target:
            os.dup2(fd, target, inheritable=False)
            os.close(fd)
        taken.append(target)
    return taken


def _preexec(fds: Sequence[int], limits: dict[int, int], parent: int) -> Callable[[], None]:
    def run() -> None:
        # Weg met de worker: SIGKILL als de ouder sterft. Stierf hij al tussen fork en hier,
        # dan is de ppid een ander proces (init of een subreaper), en stopt het kind zelf
        _LIBC.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        if os.getppid() != parent:
            os._exit(1)
        # Eerst kopieën boven alle bronnen en doelen, zodat dup2 niets overschrijft dat nog
        # nodig is; die kopieën zijn CLOEXEC en verdwijnen bij exec
        floor = max([FIRST_FD + len(fds), *fds], default=FIRST_FD) + 1
        high = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, floor) for fd in fds]
        for i, fd in enumerate(high):
            os.dup2(fd, FIRST_FD + i)  # zonder CLOEXEC: het kind erft hem
        _cloexec_from(FIRST_FD + len(fds))  # de rest niet, ook niet als hij erfbaar was
        for kind, value in limits.items():
            resource.setrlimit(kind, (value, value))

    return run


@dataclass
class Exit:
    rc: int
    cpu_s: float
    max_rss_kb: int


class ChildError(Exception):
    pass


class Child:
    """Een draaiend kind. Altijd afsluiten met wait() of stop() (async with doet stop)."""

    def __init__(
        self,
        popen: subprocess.Popen,
        on_progress: Progress | None,
        stdout_limit: int,
    ) -> None:
        self.popen = popen
        self.pid = popen.pid
        self._loop = asyncio.get_running_loop()
        self._on_progress = on_progress
        self._stdout_limit = stdout_limit
        self._tail = bytearray()
        self._partial = b""
        self._progress: dict[bytes, bytes] = {}
        self.stdout = bytearray()
        self.stdout_overflow = False
        self._exit: asyncio.Future[Exit] = self._loop.create_future()
        self._eof: dict[int, asyncio.Future[None]] = {}
        self._pidfd = os.pidfd_open(self.pid)
        os.set_blocking(self._pidfd, False)
        self._loop.add_reader(self._pidfd, self._reap)
        for stream, handler in ((popen.stderr, self._on_stderr), (popen.stdout, self._on_stdout)):
            if stream is None:
                continue
            fd = stream.fileno()
            os.set_blocking(fd, False)
            self._eof[fd] = self._loop.create_future()
            self._loop.add_reader(fd, handler, fd)

    # --- lezen ---

    def _read(self, fd: int) -> bytes | None:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return None
        except OSError:
            data = b""
        if not data:
            self._loop.remove_reader(fd)
            if not self._eof[fd].done():
                self._eof[fd].set_result(None)
        return data

    def _on_stderr(self, fd: int) -> None:
        data = self._read(fd)
        if not data:
            return
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()[-4096:]
        for line in lines:
            m = _PROGRESS.match(line.strip())
            if m:
                self._progress[m.group(1)] = m.group(2).strip()
                if m.group(1) == b"progress":
                    self._report()
                continue
            self._tail += line + b"\n"
        if len(self._tail) > 2 * _TAIL_BYTES:
            del self._tail[:-_TAIL_BYTES]

    def _report(self) -> None:
        if self._on_progress is None:
            return
        us = self._progress.get(b"out_time_us") or self._progress.get(b"out_time_ms")
        try:
            t_ms = max(0, int(us or b"0") // 1000)
        except ValueError:
            return
        speed: float | None = None
        raw = (self._progress.get(b"speed") or b"").rstrip(b"x")
        with contextlib.suppress(ValueError):
            speed = round(float(raw), 3)
        self._on_progress(t_ms, speed)

    def _on_stdout(self, fd: int) -> None:
        data = self._read(fd)
        if not data:
            return
        if len(self.stdout) + len(data) > self._stdout_limit:
            self.stdout_overflow = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.pid, signal.SIGKILL)
            return
        self.stdout += data

    def _reap(self) -> None:
        try:
            pid, status, usage = os.wait4(self.pid, os.WNOHANG)
        except ChildProcessError:
            pid, status, usage = self.pid, 255 << 8, None
        if pid == 0:
            return
        self._loop.remove_reader(self._pidfd)
        os.close(self._pidfd)
        self._pidfd = -1
        rc = os.waitstatus_to_exitcode(status)
        self.popen.returncode = rc
        cpu = usage.ru_utime + usage.ru_stime if usage else 0.0
        rss = usage.ru_maxrss if usage else 0
        if not self._exit.done():
            self._exit.set_result(Exit(rc, round(cpu, 3), rss))

    # --- de buitenkant ---

    @property
    def running(self) -> bool:
        return not self._exit.done()

    def tail(self) -> str:
        """Het eind van stderr zonder de regels van -progress (hooguit DETAIL_CHARS)."""
        text = bytes(self._tail + self._partial).decode("utf-8", "replace").strip()
        return text[-DETAIL_CHARS:]

    async def wait(self) -> Exit:
        result = await asyncio.shield(self._exit)
        await self._drain()
        return result

    async def _drain(self) -> None:
        # Na het einde de pipes leeglezen (hooguit 1 s), dan dicht
        pending = [f for f in self._eof.values() if not f.done()]
        if pending:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(1.0):
                    await asyncio.gather(*pending)
        for stream in (self.popen.stderr, self.popen.stdout):
            if stream is not None and not stream.closed:
                with contextlib.suppress(ValueError, OSError):
                    self._loop.remove_reader(stream.fileno())
                stream.close()

    def signal_group(self, sig: int) -> None:
        if self.running:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.pid, sig)

    async def stop(self, grace: float = STOP_GRACE) -> Exit:
        """SIGTERM op de procesgroep, na `grace` s SIGKILL; wacht tot waitpid. Ook als de taak
        zelf (nog eens) gestopt wordt: dan meteen SIGKILL, en toch eerst wachten tot het kind
        weg is; de CancelledError komt daarna. Zo telt het kind mee tot waitpid."""
        cancelled = False
        if self.running:
            self.signal_group(signal.SIGTERM)
            try:
                async with asyncio.timeout(grace):
                    await asyncio.shield(self._exit)
            except TimeoutError:
                self.signal_group(signal.SIGKILL)
            except asyncio.CancelledError:
                cancelled = True
                self.signal_group(signal.SIGKILL)
        while True:
            try:
                result = await self.wait()
                break
            except asyncio.CancelledError:
                cancelled = True
                self.signal_group(signal.SIGKILL)
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def __aenter__(self) -> Child:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()


def limits_for(memory_mb: int, *, fsize: int = 0) -> dict[int, int]:
    """De grenzen van Keuze 12 en spike punt 6."""
    return {
        resource.RLIMIT_CORE: 0,
        resource.RLIMIT_NOFILE: NOFILE,
        resource.RLIMIT_FSIZE: fsize,
        resource.RLIMIT_DATA: memory_mb * MB,
    }


def spawn(
    argv: Sequence[str],
    *,
    inputs: Sequence[int] = (),
    extra: Sequence[int] = (),
    stdout: int | None = None,
    capture_stdout: int = 0,
    stdin: int | None = None,
    memory_mb: int,
    fsize: int = 0,
    on_progress: Progress | None = None,
) -> Child:
    """Start een kind. inputs (bronnen, positie eerst naar 0) en extra (pipes) komen op fd 3,
    4, … in die volgorde. stdout: een fd voor pipe:1 (de schrijfkant van de app), of
    capture_stdout > 0 om hooguit zoveel bytes zelf te lezen (ffprobe), anders /dev/null."""
    for fd in inputs:
        os.lseek(fd, 0, os.SEEK_SET)
    fds = [*inputs, *extra]
    if capture_stdout:
        out: int | None = subprocess.PIPE
    elif stdout is not None:
        out = stdout
    else:
        out = subprocess.DEVNULL
    popen = subprocess.Popen(
        list(argv),
        stdin=stdin if stdin is not None else subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.PIPE,
        close_fds=False,
        preexec_fn=_preexec(fds, limits_for(memory_mb, fsize=fsize), os.getpid()),
        start_new_session=True,
        env=environment(),
        cwd="/",
    )
    return Child(popen, on_progress, capture_stdout)


async def run(
    argv: Sequence[str],
    *,
    inputs: Sequence[int] = (),
    memory_mb: int,
    stdout_limit: int = 4 * MB,
    limit_s: float = 30.0,
) -> tuple[Exit, bytes, str]:
    """Een kort kind (ffprobe): starten, stdout lezen (begrensd), wachten. Na limit_s:
    stoppen en TimeoutError."""
    child = spawn(argv, inputs=inputs, capture_stdout=stdout_limit, memory_mb=memory_mb)
    async with child:
        try:
            async with asyncio.timeout(limit_s):
                done = await child.wait()
        except TimeoutError:
            await child.stop()
            raise
    if child.stdout_overflow:
        raise ChildError("te veel uitvoer")
    return done, bytes(child.stdout), child.tail()
