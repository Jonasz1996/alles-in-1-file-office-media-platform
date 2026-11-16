"""pid 1 van de worker van de analyse (fase 6, §3.11, Keuze 56; X2a). Zonder numpy.

Per verbinding (één opdracht, wire.py):

1. hello: de nonce van de app (zonder fd's; een fd bij hello sluit de kernel, want pid 1 neemt
   er geen aan), en het bewijs met de sleutel over de uid van SO_PEERCRED;
2. één vraag met zijn fd's, nagekeken met wire.parse (de volgorde van A0: PROTOCOL, velden, op,
   gebouwd, deadline_ms, args, fd's). Een weigering is meteen done, zonder started;
3. caps en cancel doet pid 1 zelf. Een opdracht voor een kind krijgt een nummer en een plaats:
   per kind een rij van één (busy voor een tweede opdracht voor hetzelfde kind), dan started,
   en de opdracht gaat met de fd's naar het kind (child.Kid.run);
4. de deadline telt vanaf started. Is hij op, dan terminate in het kind, na KILL_GRACE_S
   SIGKILL en een nieuw kind: timeout. cancel met het nummer, of de verbinding sluiten, breekt
   de opdracht af (cancelled; na het sluiten zonder antwoord);
5. eerst is het kind weer vrij, dan pas gaat done weg: wie na done meteen een nieuwe opdracht
   stuurt, krijgt geen busy.

De wachter (app/media/worker/guard.Watch) meet elke INTERVAL s het geheugen van de kinderen; een
kind met meer RssAnon + RssShmem dan zijn RLIMIT_DATA gaat met SIGKILL weg, en de opdracht krijgt
too_big (K1-07). pid 1 blijft staan.

Wat er in het log komt: de op, het nummer, het kind, de code en tijden. Nooit een bericht, een
argument of een detail van het kind (K1-16): de zoektekst van X3 staat in args.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.analyse import lock, wire
from app.analyse.wire import CHILD, CHILDREN, Child, ChildSpec, Code, Op
from app.analyse.worker import caps as caps_mod
from app.analyse.worker import child as kids
from app.media.worker import guard

log = logging.getLogger("platform.analyse.worker")

# Wat de worker van X2a doet (caps.built); group (X2b) en beeld (X3) komen erbij
BUILT: frozenset[Op] = frozenset(
    {Op.CAPS, Op.CANCEL, Op.FACES, Op.EMBED_IMAGE, Op.EMBED_TEXT, Op.RANK, Op.FORGET}
)
SCARCE = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOBUFS, errno.ENOMEM})
ACCEPT_PAUSE = 0.1
ACCEPT_PAUSE_MAX = 1.0
API_ROOT = str(Path(__file__).resolve().parents[3])


@dataclass
class Config:
    socket: Path
    models: Path
    key: str
    sandbox: bool = False  # de zelftest slaagde: een kind start alleen uit python-tekst
    built: frozenset[Op] = BUILT
    children: Mapping[Child, ChildSpec] = field(default_factory=lambda: dict(CHILDREN))
    child_code: str = kids.BOOT  # een test kan een eigen BOOT geven (de proef van laag 2)
    analyse_path: str = kids.ANALYSE_PATH
    lock: Path = lock.LOCK_PATH
    cgroup: Path = caps_mod.CGROUP
    watch_interval: float = guard.INTERVAL
    clock: Callable[[], float] = time.monotonic
    python: str | None = None  # None: python-tekst als die er is, anders (zonder sandbox) deze


@dataclass
class Running:
    n: int
    op: Op
    child: Child
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


class Worker:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.states = caps_mod.ModelStates(config.models, config.lock)
        self.watch = guard.Watch({}, interval=config.watch_interval)
        self.kids: dict[Child, kids.Kid] = {
            child: kids.Kid(
                spec,
                argv=self._argv_for(child),
                killed=self.watch.killed.get,
                clock=config.clock,
            )
            for child, spec in config.children.items()
        }
        self.running: dict[Child, Running] = {}
        self._n = 0
        self._tasks: set[asyncio.Task] = set()
        self._listener: socket.socket | None = None
        self._accept_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None

    def _argv_for(self, child: Child) -> Callable[[], list[str] | None]:
        def argv() -> list[str] | None:
            exe = self.config.python or kids.python(self.config.sandbox)
            if exe is None:
                return None
            c = self.config
            return [exe, "-I", "-c", c.child_code, API_ROOT, child.value, str(c.models),
                    c.analyse_path, str(c.lock)]  # fmt: skip

        return argv

    # --- starten en stoppen ---

    async def start(self) -> None:
        """Luisteren op config.socket (0660): eerst onder een tijdelijke naam, dan rename."""
        if len(self.config.key) < wire.KEY_MIN:
            raise ValueError(f"de sleutel is korter dan {wire.KEY_MIN} tekens")
        path = self.config.socket
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        with wire.socket_address(tmp) as address:
            listener.bind(address)
        os.chmod(tmp, 0o660)
        listener.listen(64)
        listener.setblocking(False)
        os.replace(tmp, path)
        self._listener = listener
        self._accept_task = asyncio.create_task(self._accept(), name="analyse-accept")
        self._watch_task = asyncio.create_task(self.watch.run(), name="analyse-guard")
        # De sha256 van de modellen alvast (38 MB voor SFace), zodat de eerste caps snel is
        await asyncio.to_thread(self.states.gather)

    @property
    def accepting(self) -> asyncio.Task:
        assert self._accept_task is not None
        return self._accept_task

    async def stop(self) -> None:
        for task in (self._accept_task, self._watch_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._listener is not None:
            self._listener.close()
        with contextlib.suppress(FileNotFoundError):
            self.config.socket.unlink()
        for running in list(self.running.values()):
            running.cancel.set()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for kid in self.kids.values():
            await kid.retire(kill=True)

    async def _accept(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._listener is not None
        pause = ACCEPT_PAUSE
        while True:
            try:
                conn, _ = await loop.sock_accept(self._listener)
            except OSError as exc:
                if exc.errno not in SCARCE:
                    log.exception("verbindingen aannemen faalt; de worker stopt")
                    raise
                log.warning("geen verbinding aangenomen; over %.1f s opnieuw", pause)
                await asyncio.sleep(pause)
                pause = min(pause * 2, ACCEPT_PAUSE_MAX)
                continue
            pause = ACCEPT_PAUSE
            conn.setblocking(False)
            task = asyncio.create_task(self._connection(conn))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    # --- één verbinding ---

    async def _send(self, sock: socket.socket, message: dict) -> None:
        with contextlib.suppress(OSError, wire.WireError):
            await wire.send(sock, message)

    async def _connection(self, sock: socket.socket) -> None:
        try:
            async with asyncio.timeout(wire.ANSWER_S * 2):
                ok = await self._hello(sock)
            if ok:
                await self._request(sock)
        except (TimeoutError, wire.Closed, OSError):
            pass
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("verbinding")
        finally:
            sock.close()

    async def _hello(self, sock: socket.socket) -> bool:
        try:
            message, fds = await wire.receive(sock, max_fds=0)
            wire.close_all(fds)
        except wire.WireError:
            await self._send(sock, wire.done(Code.BAD_REQUEST, "geen geldig hello (zonder fd's)"))
            return False
        problem = wire.hello_problem(message)
        if problem is not None:
            await self._send(sock, wire.done(Code.BAD_REQUEST, problem))
            return False
        uid = wire.peer_uid(sock)
        await wire.send(sock, wire.hello_reply(self.config.key, message["hello"], uid))
        return True

    async def _request(self, sock: socket.socket) -> None:
        try:
            async with asyncio.timeout(wire.ANSWER_S * 2):
                message, fds = await wire.receive(sock)
        except wire.WireError:
            await self._send(sock, wire.done(Code.BAD_REQUEST, "geen geldige vraag"))
            return
        try:
            await self._handle(sock, message, fds)
        finally:
            wire.close_all(fds)

    async def _handle(self, sock: socket.socket, message: dict, fds: list[int]) -> None:
        try:
            req = wire.parse(message, len(fds), self.config.built)
        except wire.Refusal as exc:
            await self._send(sock, wire.done(exc.code, exc.detail))
            return
        child = CHILD[req.op]
        if child is None:
            await self._pid1(sock, req)
            return
        if req.op is Op.GROUP and not wire.is_sealed(fds[-2]):
            await self._send(sock, wire.done(Code.BAD_REQUEST, "in is niet verzegeld"))
            return
        if child in self.running or child not in self.kids:
            await self._send(sock, wire.done(Code.BUSY, child.value))
            return
        self._n += 1
        running = self.running[child] = Running(self._n, req.op, child)
        started = time.monotonic()
        code = Code.INTERNAL
        try:
            await wire.send(sock, wire.started(running.n, child))
            outcome = await self._run(sock, running, req, fds)
            if outcome is None:  # de app sloot de verbinding
                code = Code.CANCELLED
                return
            code = outcome.code
            self.running.pop(child, None)  # eerst het kind vrij, dan done
            await self._send(sock, wire.done(outcome.code, outcome.detail, outcome.data))
        finally:
            if self.running.get(child) is running:
                self.running.pop(child, None)
            log.info(
                "opdracht %d (%s, %s): %s in %.0f ms",
                running.n, req.op.value, child.value, code.value,
                (time.monotonic() - started) * 1000,
            )  # fmt: skip

    async def _run(
        self, sock: socket.socket, running: Running, req: wire.Request, fds: list[int]
    ) -> kids.Outcome | None:
        kid = self.kids[running.child]
        args = req.args.model_dump(mode="json")
        work = asyncio.create_task(
            kid.run(running.n, req.op, args, fds, req.deadline_ms, running.cancel)
        )
        peer = asyncio.create_task(wire.receive(sock, max_fds=0))
        try:
            await asyncio.wait({work, peer}, return_when=asyncio.FIRST_COMPLETED)
            closed = not work.done()
            if closed:  # de app sloot de verbinding (of stuurde iets): afbreken
                running.cancel.set()
            outcome = await work
            if closed:
                return None
            return self._checked(req, outcome, fds)
        finally:
            peer.cancel()
            with contextlib.suppress(BaseException):
                await peer
            if not work.done():
                running.cancel.set()
                with contextlib.suppress(BaseException):
                    await work

    def _checked(self, req: wire.Request, outcome: kids.Outcome, fds: list[int]) -> kids.Outcome:
        """De uitvoer van het kind nagekeken, voor hij naar de app gaat (de app kijkt opnieuw)."""
        if outcome.code is not Code.OK or req.op is not Op.FACES:
            return outcome
        try:
            data = wire.FacesData.model_validate(outcome.data)
            if len(data.photos) != req.previews:
                raise ValueError("aantal foto's")
            if os.fstat(fds[-1]).st_size != data.vector_bytes():
                raise ValueError("uit heeft niet n × 512 B")
        except (ValidationError, ValueError, OSError):
            return kids.Outcome(Code.INTERNAL, "de uitvoer van het kind klopt niet")
        return kids.Outcome(Code.OK, "", data.model_dump(mode="json"))

    async def _pid1(self, sock: socket.socket, req: wire.Request) -> None:
        if req.op is Op.CAPS:
            data = await asyncio.to_thread(
                caps_mod.gather, self.states, self.config.built, self.config.cgroup
            )
            await wire.send(sock, wire.done(Code.OK, data=data))
            return
        target = req.args.n  # type: ignore[attr-defined]
        found = False
        for running in self.running.values():
            if running.n == target and not running.cancel.is_set():
                running.cancel.set()
                found = True
        await wire.send(sock, wire.done(Code.OK, data=wire.CancelData(found=found)))


class RefusingWorker(Worker):
    """De zelftest faalde: luisteren (zodat de app de reden ziet), maar elke hello krijgt
    done/sandbox met de reden, en er komt nooit een vraag of een fd binnen. Geen kind."""

    def __init__(self, config: Config, reason: str) -> None:
        super().__init__(config)
        self.reason = reason[: wire.DETAIL_CHARS]

    async def _hello(self, sock: socket.socket) -> bool:
        with contextlib.suppress(wire.Closed, wire.WireError, OSError):
            message, fds = await wire.receive(sock, max_fds=0)
            wire.close_all(fds)
        await self._send(sock, wire.done(Code.SANDBOX, self.reason))
        return False


def python_ok() -> bool:
    """Zonder numpy in pid 1 (test_worker_sandbox kijkt het na in een echte worker)."""
    return "numpy" not in sys.modules and "onnxruntime" not in sys.modules
