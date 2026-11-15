"""De twee kinderen van de worker: werk en zoeken (fase 6, §3.11, Keuze 56; X2a).

Twee kanten in één bestand. Kid draait in pid 1 (zonder numpy); main() is het kind zelf, en
importeert numpy en onnxruntime pas nadat /opt/analyse in sys.path staat.

**Starten** (Kid._spawn). pid 1 start het kind met exec en een schone omgeving (env(): PATH,
LC_ALL, HOME en de draden van OpenBLAS; nooit de sleutel), in een eigen procesgroep, met:

- fd 3: een socketpair met pid 1 (de vragen met hun fd's, en de antwoorden);
- fd 4: het leeseinde van een pijp; pid 1 schrijft daar het nummer van een opdracht die moet
  stoppen (de deadline is op, of cancel);
- fd 2: een pijp naar pid 1. pid 1 logt per regel alleen dat er een regel was en hoe lang hij
  was, en bij een crash de exitcode (K1-16). De inhoud gaat nergens heen;
- vóór de exec (_preexec): PR_SET_PDEATHSIG (SIGKILL als pid 1 weggaat), RLIMIT_DATA van het kind
  (werk 1 GB, zoeken 1,5 GB), RLIMIT_CORE 0, RLIMIT_NOFILE 256, RLIMIT_FSIZE (de memfd's van de
  uitvoer) en nice (werk 19). Elke andere fd van pid 1 is na de exec dicht.

python is in het image /opt/venv/bin/python-tekst (0711, van root; zie de Dockerfile): een
programma dat het kind niet mag lezen, maakt het proces vanaf de exec niet-dumpbaar (K1-04).
Is de worker afgeschermd (de zelftest slaagde), dan start een kind alleen uit die kopie.

**Het kind** (main). Het eerste wat het doet, nog vóór het een fd van een opdracht krijgt:
prctl(PR_SET_DUMPABLE, 0) (BOOT doet dat zelfs vóór de import van de app). Dan /opt/analyse
achteraan in sys.path, numpy en onnxruntime, onnxruntime.disable_telemetry_events(), en "klaar"
over fd 3. Daarna één opdracht tegelijk: {"n", "op", "args", "deadline_ms"} met de fd's, en
{"code", "detail", "data"} terug. Een uitzondering wordt internal met alleen de naam van de
soort fout; op stderr komt ook alleen die naam (K1-16: nooit een vector of een zoektekst).

**Stoppen.** pid 1 schrijft het nummer van de opdracht op fd 4. Een draad in het kind zet dan
RunOptions.terminate van de sessie die draait, en de opdracht kijkt tussen de stappen of hij
moet stoppen (Stopped). Komt er binnen wire.KILL_GRACE_S geen antwoord, dan SIGKILL bij de PID
(killpg: het kind is leider van zijn groep).

**Leven.** werk leeft ANALYSE_CHILD_BATCHES opdrachten of CHILD_MAX_AGE_S; na een timeout of een
crash altijd een nieuw. pid 1 start een kind pas bij de eerste opdracht ervoor (lui).
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import ctypes
import fcntl
import json
import logging
import os
import resource
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.analyse import wire
from app.analyse.wire import Child, ChildSpec, Code

log = logging.getLogger("platform.analyse.worker")

ANALYSE_PATH = "/opt/analyse"  # de vijf pakketten van de extra analyse (Keuze 57)
COPY_NAME = "python-tekst"  # de kopie van python die niemand mag lezen (Dockerfile)
NO_COPY = "geen python-tekst"
READY_S = 60.0  # numpy en onnxruntime laden; ruim, voor een trage runner
FSIZE = 64 * 1024 * 1024  # RLIMIT_FSIZE: de memfd van de uitvoer (faces: hooguit 320 KB)
NOFILE = 256
STDERR_LINES = 50  # zoveel regels van stderr per kind in het log, daarna niets meer
PR_SET_DUMPABLE = 4
PR_GET_DUMPABLE = 3
PR_SET_PDEATHSIG = 1
CLOSE_RANGE_CLOEXEC = 4
CONTROL_FD = 3
STOP_FD = 4
_STOP = struct.Struct("<q")
_INT = array.array("i").itemsize

# Het kind: eerst niet-dumpbaar (vóór de import van de app), dan de map van de API in sys.path
BOOT = (
    "import ctypes,sys\n"
    "if ctypes.CDLL(None).prctl(4,0,0,0,0):sys.exit(3)\n"
    "sys.path.insert(0,sys.argv.pop(1))\n"
    "from app.analyse.worker.child import main\n"
    "sys.exit(main())"
)

_LIBC = ctypes.CDLL(None, use_errno=True)
_CLOSE_RANGE = getattr(_LIBC, "close_range", None)
if _CLOSE_RANGE is not None:
    _CLOSE_RANGE.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.c_int)


class ChildError(Exception):
    """Het kind start niet, of zegt iets anders dan het protocol."""


# ============================================================================================
# pid 1
# ============================================================================================


def env() -> dict[str, str]:
    """De omgeving van een kind: geen sleutel, geen variabele van de app (K1-05)."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "HOME": "/tmp",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
    }


def copy_path() -> Path:
    return Path(sys.executable).with_name(COPY_NAME)


def unreadable(path: Path) -> bool:
    """Een gewoon bestand dat dit proces mag starten maar niet lezen."""
    try:
        regular = path.is_file()
    except OSError:
        return False
    return regular and os.access(path, os.X_OK) and not os.access(path, os.R_OK)


def python(sandbox: bool) -> str | None:
    """De python voor een kind: de kopie die pid 1 niet mag lezen als die er is. Afgeschermd
    zonder zo'n kopie: None (dan geen kind)."""
    copy = copy_path()
    if unreadable(copy):
        return str(copy)
    return None if sandbox else sys.executable


def _cloexec_from(first: int) -> None:
    if _CLOSE_RANGE is not None and _CLOSE_RANGE(first, 0xFFFFFFFF, CLOSE_RANGE_CLOEXEC) == 0:
        return
    for name in os.listdir("/proc/self/fd"):
        if int(name) >= first:
            with contextlib.suppress(OSError):
                os.set_inheritable(int(name), False)


def _preexec(fds: Sequence[int], spec: ChildSpec, parent: int) -> Callable[[], None]:
    """In het kind na fork, vóór exec: PDEATHSIG, de fd's op 3 en 4, de grenzen en nice."""
    limits = {
        resource.RLIMIT_CORE: 0,
        resource.RLIMIT_NOFILE: NOFILE,
        resource.RLIMIT_FSIZE: FSIZE,
        resource.RLIMIT_DATA: spec.rlimit_data,
    }

    def run() -> None:
        _LIBC.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        if os.getppid() != parent:
            os._exit(1)
        floor = max([CONTROL_FD + len(fds), *fds]) + 1
        high = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, floor) for fd in fds]
        for i, fd in enumerate(high):
            os.dup2(fd, CONTROL_FD + i)
        _cloexec_from(CONTROL_FD + len(fds))
        for kind, value in limits.items():
            resource.setrlimit(kind, (value, value))
        os.setpriority(os.PRIO_PROCESS, 0, spec.nice)

    return run


def _popen(argv: list[str], fds: list[int], err: int, spec: ChildSpec) -> subprocess.Popen:
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=err,
        close_fds=False,
        preexec_fn=_preexec(fds, spec, os.getpid()),
        start_new_session=True,
        env=env(),
        cwd="/",
    )


async def _exited(proc: subprocess.Popen, limit_s: float) -> bool:
    """Wacht hooguit limit_s tot het kind weg is (en gewacht); True als dat lukte."""
    loop = asyncio.get_running_loop()
    end = loop.time() + limit_s
    while proc.poll() is None:
        if loop.time() >= end:
            return False
        await asyncio.sleep(0.02)
    return True


@dataclass
class Outcome:
    """Wat een opdracht van een kind werd."""

    code: Code
    detail: str = ""
    data: dict | None = None


@dataclass
class Kid:
    """Eén kind, gezien vanuit pid 1. Hooguit één opdracht tegelijk (de rij van één houdt
    server.Worker bij)."""

    spec: ChildSpec
    argv: Callable[[], list[str] | None]  # None: geen python om mee te starten (NO_COPY)
    killed: Callable[[int], str | None] = lambda pid: None  # de wachter: waarom gestopt
    clock: Callable[[], float] = time.monotonic
    proc: subprocess.Popen | None = None
    sock: socket.socket | None = None
    stop_w: int = -1
    ops: int = 0
    born: float = 0.0
    generation: int = 0
    ready: dict = field(default_factory=dict)  # wat het kind bij klaar zei (pid, dumpable)
    _stderr: asyncio.Task | None = None
    _lines: int = 0
    pids: list[int] = field(default_factory=list)  # elk kind dat ooit startte (voor de tests)

    @property
    def name(self) -> str:
        return self.spec.child.value

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None and self.proc.poll() is None else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and self.sock is not None

    async def ensure(self) -> None:
        if not self.alive():
            await self.retire()
            await self._spawn()

    async def _spawn(self) -> None:
        argv = self.argv()
        if argv is None:
            raise ChildError(NO_COPY)
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        stop_r, stop_w = os.pipe2(os.O_CLOEXEC)
        err_r, err_w = os.pipe2(os.O_CLOEXEC)
        try:
            proc = _popen(argv, [theirs.fileno(), stop_r], err_w, self.spec)
        except BaseException:
            ours.close()
            os.close(stop_w)
            os.close(err_r)
            raise
        finally:
            theirs.close()
            os.close(stop_r)
            os.close(err_w)
        ours.setblocking(False)
        self.proc, self.sock, self.stop_w = proc, ours, stop_w
        self.ops, self.born, self._lines = 0, self.clock(), 0
        self.generation += 1
        self.pids.append(proc.pid)
        self._stderr = asyncio.create_task(self._read_stderr(err_r, proc.pid))
        log.info("kind %s gestart (pid %d)", self.name, proc.pid)
        try:
            async with asyncio.timeout(READY_S):
                message, fds = await wire.receive(ours, max_fds=0)
            wire.close_all(fds)
            if message.get("ev") != "klaar":
                raise ChildError("het kind zei geen klaar")
            self.ready = message
        except (TimeoutError, wire.Closed, wire.WireError, OSError) as exc:
            await self.retire(kill=True)
            raise ChildError(f"het kind {self.name} start niet ({type(exc).__name__})") from None
        except ChildError:
            await self.retire(kill=True)
            raise

    async def _read_stderr(self, fd: int, pid: int) -> None:
        """K1-16: per regel alleen de lengte in het log, nooit de inhoud."""
        loop = asyncio.get_running_loop()
        os.set_blocking(fd, False)
        ready = asyncio.Event()
        loop.add_reader(fd, ready.set)
        pending = 0
        try:
            while True:
                await ready.wait()
                ready.clear()
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                for part in chunk.split(b"\n")[:-1]:
                    self._line(pid, pending + len(part))
                    pending = 0
                pending += len(chunk) - (chunk.rfind(b"\n") + 1)
            if pending:
                self._line(pid, pending)
        finally:
            loop.remove_reader(fd)
            os.close(fd)

    def _line(self, pid: int, size: int) -> None:
        self._lines += 1
        if self._lines <= STDERR_LINES:
            log.warning("kind %s (pid %d): een regel op stderr (%d bytes)", self.name, pid, size)
        elif self._lines == STDERR_LINES + 1:
            log.warning("kind %s (pid %d): meer regels op stderr, niet meer gelogd", self.name, pid)

    def stop_op(self, n: int) -> None:
        """Vraag het kind opdracht n te stoppen (RunOptions.terminate)."""
        if self.stop_w >= 0:
            with contextlib.suppress(OSError):
                os.write(self.stop_w, _STOP.pack(n))

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.proc.pid, signal.SIGKILL)

    async def retire(self, *, kill: bool = False) -> None:
        """Het kind weg: de socket dicht (het kind ziet EOF en stopt), na wire.KILL_GRACE_S
        (of meteen met kill) SIGKILL. Daarna is er geen kind; ensure() start een nieuw."""
        proc, sock = self.proc, self.sock
        self.proc, self.sock = None, None
        if sock is not None:
            sock.close()
        if self.stop_w >= 0:
            os.close(self.stop_w)
            self.stop_w = -1
        if proc is None:
            return
        if kill:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        if not await _exited(proc, wire.KILL_GRACE_S):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            await _exited(proc, wire.KILL_GRACE_S)
        self._report(proc)
        if self._stderr is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                async with asyncio.timeout(1.0):
                    await self._stderr
            self._stderr = None

    def _report(self, proc: subprocess.Popen) -> None:
        rc = proc.returncode
        why = self.killed(proc.pid)
        if why is not None:
            log.warning("kind %s (pid %d) gestopt: %s", self.name, proc.pid, why)
        elif rc not in (0, None, -signal.SIGKILL):
            log.warning("kind %s (pid %d) stopte met code %s", self.name, proc.pid, rc)

    def worn(self) -> bool:
        """Na zoveel opdrachten of zo lang: tijd voor een nieuw kind (werk)."""
        spec = self.spec
        many = spec.max_ops is not None and self.ops >= spec.max_ops
        old = spec.max_age_s is not None and self.clock() - self.born >= spec.max_age_s
        return many or old

    async def run(
        self,
        n: int,
        op: wire.Op,
        args: dict,
        fds: Sequence[int],
        deadline_ms: int,
        cancel: asyncio.Event,
    ) -> Outcome:
        """Eén opdracht: de vraag met de fd's naar het kind, en wachten op het antwoord, de
        deadline of cancel. Na een timeout of een crash is het kind weg."""
        loop = asyncio.get_running_loop()
        end = loop.time() + deadline_ms / 1000
        try:
            await self.ensure()
        except ChildError as exc:
            return Outcome(Code.INTERNAL, str(exc))
        except OSError as exc:
            return Outcome(Code.INTERNAL, f"het kind start niet ({type(exc).__name__})")
        assert self.sock is not None and self.proc is not None
        pid = self.proc.pid
        message = {"n": n, "op": op.value, "args": args, "deadline_ms": deadline_ms}
        try:
            await wire.send(self.sock, message, fds)
        except (OSError, wire.WireError):
            await self.retire(kill=True)
            return self._dead(pid)
        answer = asyncio.create_task(wire.receive(self.sock, max_fds=0))
        stopped = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {answer, stopped},
                timeout=max(end - loop.time(), 0),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if answer not in done:
                why = Code.CANCELLED if stopped in done else Code.TIMEOUT
                return await self._stop(n, answer, why)
            return await self._answer(answer, pid)
        finally:
            stopped.cancel()
            if not answer.done():
                answer.cancel()
                with contextlib.suppress(BaseException):
                    await answer
            self.ops += 1
            if self.alive() and self.worn():
                await self.retire()

    async def _stop(self, n: int, answer: asyncio.Task, why: Code) -> Outcome:
        """De deadline is op of de opdracht is afgebroken: eerst terminate, na KILL_GRACE_S
        SIGKILL. Na een timeout altijd een nieuw kind."""
        self.stop_op(n)
        try:
            async with asyncio.timeout(wire.KILL_GRACE_S):
                message, fds = await asyncio.shield(answer)
            wire.close_all(fds)
        except (TimeoutError, wire.Closed, wire.WireError, OSError):
            await self.retire(kill=True)
            return Outcome(why, "het kind is gestopt")
        if why is Code.TIMEOUT:
            await self.retire()
            return Outcome(why, "de deadline was op")
        if message.get("code") not in (Code.CANCELLED.value, Code.OK.value):
            await self.retire()
        return Outcome(why, "afgebroken")

    async def _answer(self, answer: asyncio.Task, pid: int) -> Outcome:
        try:
            message, fds = answer.result()
        except (wire.Closed, wire.WireError, OSError):
            await self.retire(kill=True)
            return self._dead(pid)
        wire.close_all(fds)
        try:
            code = Code(message.get("code"))
        except ValueError:
            await self.retire(kill=True)
            return Outcome(Code.INTERNAL, "het kind gaf een onbekende code")
        detail = str(message.get("detail") or "")[: wire.DETAIL_CHARS]
        data = message.get("data")
        if code is Code.OK and not isinstance(data, dict):
            return Outcome(Code.INTERNAL, "het kind gaf geen data")
        if code is Code.INTERNAL:
            await self.retire()  # na een onverwachte fout een schoon kind
        return Outcome(code, detail, data if code is Code.OK else None)

    def _dead(self, pid: int) -> Outcome:
        why = self.killed(pid)
        if why is not None:
            return Outcome(Code.TOO_BIG, "het kind ging boven zijn geheugengrens")
        return Outcome(Code.INTERNAL, "het kind stopte onverwacht")


# ============================================================================================
# het kind
# ============================================================================================


class Stopped(Exception):
    """pid 1 vroeg de opdracht te stoppen (deadline of cancel)."""


class OpFailed(Exception):
    """Een opdracht met een andere code dan ok; detail zonder waarden."""

    def __init__(self, code: Code, detail: str = "") -> None:
        super().__init__(code.value)
        self.code = code
        self.detail = detail


class StopFlag:
    """Gezet door de draad van fd 4 als pid 1 het nummer van de lopende opdracht stuurt. De
    lopende sessie van onnxruntime krijgt terminate (tussen twee nodes).

    De stop (fd 4) en de vraag (fd 3) komen over twee kanalen: de stop kan er zijn voordat het
    kind de vraag gelezen heeft en begin(n) deed (cancel vlak na started). asked onthoudt het
    hoogste nummer dat pid 1 liet stoppen, en begin(n) kijkt daarnaar. De nummers van pid 1 lopen
    op (server.Worker._n), dus een late stop voor een eerdere opdracht raakt nooit een latere
    (veilig F1, contract G5-C1). De lock: stop() draait in de draad van fd 4."""

    def __init__(self) -> None:
        self.n = 0
        self.asked = 0
        self.set = False
        self.run_options: Any = None  # onnxruntime.RunOptions van de sessie die nu draait
        self._lock = threading.Lock()

    def begin(self, n: int) -> None:
        with self._lock:
            self.n, self.run_options = n, None
            self.set = bool(n) and n <= self.asked

    def stop(self, n: int) -> None:
        with self._lock:
            self.asked = max(self.asked, n)
            if n == self.n and n:
                self.set = True
                options = self.run_options
                if options is not None:
                    options.terminate = True

    def check(self) -> None:
        if self.set:
            raise Stopped()


@dataclass
class Job:
    """Wat een handler krijgt."""

    n: int
    args: dict
    fds: list[int]
    deadline_ms: int
    stop: StopFlag
    models: Any  # worker.models.Models (in het kind)


Handler = Callable[[Job], dict]


def _recv(sock: socket.socket) -> tuple[dict, list[int]]:
    """Eén boodschap van pid 1 met de fd's (blokkerend, het framing van app.media.wire)."""
    fds: list[int] = []
    ancsize = socket.CMSG_SPACE(wire.MAX_FDS * _INT)
    head = b""
    try:
        while len(head) < 4:
            data, anc, flags, _ = sock.recvmsg(4 - len(head), ancsize, socket.MSG_CMSG_CLOEXEC)
            for level, kind, raw in anc:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    got = array.array("i")
                    got.frombytes(raw[: len(raw) - (len(raw) % _INT)])
                    fds.extend(got)
            if flags & socket.MSG_CTRUNC:
                raise ChildError("te veel fd's")
            if not data:
                raise EOFError
            head += data
        (length,) = struct.unpack(">I", head)
        if length > wire.MAX_MESSAGE:
            raise ChildError("te groot")
        body = bytearray()
        while len(body) < length:
            data = sock.recv(min(length - len(body), 65536))
            if not data:
                raise EOFError
            body += data
        message = json.loads(body)
        if not isinstance(message, dict):
            raise ChildError("geen object")
        return message, fds
    except BaseException:
        wire.close_all(fds)
        raise


def _send(sock: socket.socket, message: dict) -> None:
    sock.sendall(wire.encode(message))


def _stop_reader(flag: StopFlag) -> None:
    """De draad van fd 4: elk nummer van pid 1 is een vraag om te stoppen."""
    buf = b""
    while True:
        try:
            chunk = os.read(STOP_FD, _STOP.size - len(buf))
        except InterruptedError:
            continue
        except OSError:
            return
        if not chunk:
            os._exit(0)  # pid 1 is weg (PDEATHSIG doet het ook)
        buf += chunk
        if len(buf) == _STOP.size:
            (n,) = _STOP.unpack(buf)
            buf = b""
            flag.stop(n)


def handlers(child: Child) -> dict[str, Handler]:
    """Wat dit kind doet. werk decodeert previews; zoeken nooit (Keuze 75)."""
    if child is Child.WERK:
        from app.analyse.worker.ops import faces, group

        return {wire.Op.FACES.value: faces.run, wire.Op.GROUP.value: group.run}
    return {}


HANDLERS: dict[str, Handler] = {}  # een test (de proef van test_analyse_kinderen) vult aan


def dumpable() -> int:
    return _LIBC.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)


def main(argv: Sequence[str] | None = None) -> int:
    """`python -I -c BOOT <api> <kind> <modellen> <analyse-pad> <models.lock>`."""
    if _LIBC.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:  # BOOT deed het al; voor de zekerheid
        return 3
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        print("gebruik: kind modellen analyse-pad models.lock", file=sys.stderr)
        return 2
    child = Child(args[0])
    models_dir, extra, lock_path = Path(args[1]), args[2], Path(args[3])
    if extra and os.path.isdir(extra) and extra not in sys.path:
        sys.path.append(extra)  # pas na prctl, de grenzen en nice (Keuze 57, K1-04)
    import onnxruntime

    onnxruntime.disable_telemetry_events()  # vóór de eerste sessie (a0.md, Open 2)
    onnxruntime.set_default_logger_severity(3)

    from app.analyse.worker.models import Models

    table = {**handlers(child), **HANDLERS}
    flag = StopFlag()
    threading.Thread(target=_stop_reader, args=(flag,), daemon=True, name="stop").start()
    sock = socket.socket(fileno=CONTROL_FD)
    sock.setblocking(True)
    models = Models(models_dir, lock_path)
    _send(sock, {"ev": "klaar", "pid": os.getpid(), "dumpable": dumpable()})
    while True:
        try:
            message, fds = _recv(sock)
        except (EOFError, ConnectionError):
            return 0
        n = int(message.get("n") or 0)
        op = str(message.get("op"))
        flag.begin(n)
        try:
            handler = table.get(op)
            if handler is None:
                raise OpFailed(Code.NOT_BUILT, f"{op} is niet gebouwd in {child.value}")
            job = Job(n, dict(message.get("args") or {}), fds, int(message["deadline_ms"]),
                      flag, models)  # fmt: skip
            reply: dict = {"code": Code.OK.value, "data": handler(job)}
        except Stopped:
            reply = {"code": Code.CANCELLED.value, "detail": "gestopt"}
        except OpFailed as exc:
            reply = {"code": exc.code.value, "detail": exc.detail[: wire.DETAIL_CHARS]}
        except MemoryError:
            reply = {"code": Code.TOO_BIG.value, "detail": "het kind kreeg geen geheugen meer"}
        except Exception as exc:  # nooit de tekst van de fout: die kan een waarde bevatten
            name = type(exc).__name__
            print(f"fout in {op}: {name}", file=sys.stderr, flush=True)
            reply = {"code": Code.INTERNAL.value, "detail": name}
        finally:
            flag.begin(0)
            wire.close_all(fds)
        _send(sock, reply)
