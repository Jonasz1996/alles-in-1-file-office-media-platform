"""Een nep-worker voor de container analyse (fase 6, A0; §3.11 en Keuze 56). Zonder numpy.

Hij spreekt het echte protocol van app/analyse/wire.py over een echte Unix-socket: de handdruk
met LABEL van de analyse en de uid van SO_PEERCRED, het framing en de fd's van app.media.wire,
en wire.parse voor elke vraag. Het werkmodel is dat van pid 1:

- per kind (werk, zoeken) een rij van één: een tweede opdracht voor hetzelfde kind krijgt
  busy, een opdracht voor het andere kind niet; caps en cancel doet pid 1 zelf;
- elke opdracht heeft zijn deadline (deadline_ms): daarna timeout, en een nieuw kind;
- cancel met het nummer uit started breekt een opdracht af (cancelled); de verbinding sluiten
  ook (dan zonder antwoord);
- werk leeft ANALYSE_CHILD_BATCHES opdrachten of CHILD_MAX_AGE_S (de klok is in te stellen),
  zoeken leeft lang; `generation` telt de kinderen.

Wat een opdracht doet, zegt de test (of later X2 en X3, zonder dit bestand te wijzigen):

    fake.on(Op.FACES, handler)      # async handler(FakeRequest) -> dict | BaseModel
    gate = fake.hold(Op.GROUP)      # wacht tot gate.release() (of de deadline, of cancel)
    fake.fail(Op.RANK, Code.TOO_BIG)
    fake.models["face_detection_yunet_2026may.onnx"] = "ontbreekt"
    fake.busy.add(Child.WERK)       # alles voor werk krijgt busy
    fake.refuse = Code.SANDBOX      # een RefusingWorker: elke hello krijgt done/sandbox

Een opdracht zonder handler is not_built, zoals in de echte worker tot X2 en X3 er zijn. Faults
voor de handdruk: fake.hello_fault = "silent" | "close" | "wrong_uid" | "wrong_proof" |
"media_proof". fake.settings(tmp_path) geeft Settings waarmee app.analyse.client hem bereikt.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.analyse import lock, wire
from app.analyse.wire import CHILD, CHILDREN, Child, Code, Op
from app.config import Settings
from app.media import wire as media_wire

HelloFault = Literal["silent", "close", "wrong_uid", "wrong_proof", "media_proof"]


class FakeFail(Exception):
    """Een handler die een andere code dan ok geeft."""

    def __init__(self, code: Code, detail: str = "") -> None:
        super().__init__(code.value)
        self.code = code
        self.detail = detail


@dataclass
class FakeRequest:
    op: Op
    args: BaseModel
    n: int
    child: Child
    deadline_ms: int
    previews: list[int]  # de fd's van de previews (alleen lezen)
    extra: dict[str, int]  # de andere fd's, op hun naam uit wire.FDS ("uit", "in", "vectoren")


Handler = Callable[[FakeRequest], Awaitable[dict | BaseModel | None]]


@dataclass
class Seen:
    """Wat de nep kreeg (voor de test). Nooit de inhoud van een fd."""

    op: Op | str
    code: Code
    n: int | None = None
    child: Child | None = None
    deadline_ms: int | None = None
    previews: int = 0


@dataclass
class Running:
    n: int
    op: Op
    task: asyncio.Task
    cancelled: bool = False


@dataclass
class Gate:
    """Een opdracht die wacht tot release(); started telt hoe vaak hij begon."""

    data: dict = field(default_factory=dict)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    started: asyncio.Event = field(default_factory=asyncio.Event)

    def release(self) -> None:
        self.event.set()

    async def __call__(self, req: FakeRequest) -> dict:
        self.started.set()
        await self.event.wait()
        return self.data


class FakeAnalyseWorker:
    def __init__(
        self,
        socket_path: Path,
        *,
        key: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.socket = socket_path
        self.key = key if key is not None else secrets.token_hex(32)
        self.clock = clock
        self.models: dict[str, wire.ModelState] = {m.name: "ok" for m in lock.load()}
        self.beeld: wire.BeeldCaps | None = None
        self.limits: wire.LimitsCaps | None = None
        self.refuse: Code | None = None
        self.hello_fault: HelloFault | None = None
        self.busy: set[Child] = set()
        self.requests: list[Seen] = []
        self.hellos = 0
        self.running: dict[Child, Running] = {}
        self.generation: dict[Child, int] = dict.fromkeys(Child, 0)
        self._ops: dict[Child, int] = dict.fromkeys(Child, 0)
        self._born: dict[Child, float] = {c: clock() for c in Child}
        self._handlers: dict[Op, Handler] = {}
        self._n = 0
        self._tasks: set[asyncio.Task] = set()
        self._listener: socket.socket | None = None
        self._accept: asyncio.Task | None = None

    # --- voor de test ---

    def on(self, op: Op, handler: Handler) -> None:
        if CHILD[op] is None:
            raise ValueError(f"{op.value} doet pid 1 zelf")
        self._handlers[op] = handler

    def hold(self, op: Op, data: dict | None = None) -> Gate:
        gate = Gate(data or {})
        self.on(op, gate)
        return gate

    def fail(self, op: Op, code: Code, detail: str = "") -> None:
        async def handler(req: FakeRequest) -> None:
            raise FakeFail(code, detail)

        self.on(op, handler)

    def settings(self, data_dir: Path, **extra) -> Settings:
        """Settings waarmee app.analyse.client deze nep bereikt (PLATFORM_ANALYSE_ENABLED aan,
        de socket en de sleutel van de nep); extra vult aan of vervangt."""
        values: dict = {
            "data_dir": data_dir,
            "web_dist": data_dir / "web",
            "jwt_secret": "test" * 8,
            "analyse_enabled": True,
            "analyse_worker": self.socket,
            "analyse_worker_key": self.key,
        }
        values.update(extra)
        return Settings(**values)

    @property
    def built(self) -> frozenset[Op]:
        return frozenset({Op.CAPS, Op.CANCEL, *self._handlers})

    def caps(self) -> wire.CapsData:
        return wire.CapsData(
            models=[wire.ModelCaps(name=k, state=v) for k, v in self.models.items()],
            built=sorted(op.value for op in self.built),
            beeld=self.beeld,
            limits=self.limits,
        )

    # --- starten en stoppen ---

    async def start(self) -> FakeAnalyseWorker:
        path = self.socket
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        with wire.socket_address(path) as address:
            listener.bind(address)
        os.chmod(path, 0o660)
        listener.listen(64)
        listener.setblocking(False)
        self._listener = listener
        self._accept = asyncio.create_task(self._accept_loop())
        return self

    async def stop(self) -> None:
        if self._accept is not None:
            self._accept.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._accept
        if self._listener is not None:
            self._listener.close()
        with contextlib.suppress(FileNotFoundError):
            self.socket.unlink()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def __aenter__(self) -> FakeAnalyseWorker:
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def _accept_loop(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._listener is not None
        while True:
            conn, _ = await loop.sock_accept(self._listener)
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
            if await self._hello(sock):
                await self._request(sock)
        except (wire.Closed, OSError):
            pass
        finally:
            sock.close()

    async def _hello(self, sock: socket.socket) -> bool:
        self.hellos += 1
        try:
            message, _ = await wire.receive(sock, max_fds=0)
        except wire.WireError:
            await self._send(sock, wire.done(Code.BAD_REQUEST, "geen geldig hello"))
            return False
        problem = wire.hello_problem(message)
        if problem is not None:
            await self._send(sock, wire.done(Code.BAD_REQUEST, problem))
            return False
        if self.refuse is not None:
            await self._send(sock, wire.done(self.refuse, "de zelftest faalde"))
            return False
        fault = self.hello_fault
        if fault == "silent":
            await asyncio.Event().wait()  # nooit een antwoord
        if fault == "close":
            return False
        nonce = message["hello"]
        uid = wire.peer_uid(sock)
        reply = wire.hello_reply(self.key, nonce, uid)
        if fault == "wrong_uid":
            # Een goed bewijs over de echte uid, maar een andere uid in het antwoord: zo faalt
            # alleen de vergelijking van de uid in de client (review A0, contract F3)
            reply["uid"] = uid + 1
        elif fault == "wrong_proof":
            reply["proof"] = wire.proof(secrets.token_hex(32), nonce, uid)
        elif fault == "media_proof":
            reply["proof"] = media_wire.proof(self.key, nonce, uid)
        await wire.send(sock, reply)
        return True

    async def _request(self, sock: socket.socket) -> None:
        try:
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
            req = wire.parse(message, len(fds), self.built)
        except wire.Refusal as exc:
            op = message.get("op")
            known = isinstance(op, str) and op in {o.value for o in Op}
            self.requests.append(Seen(Op(op) if known else str(op), exc.code))
            await self._send(sock, wire.done(exc.code, exc.detail))
            return
        child = CHILD[req.op]
        if child is None:
            await self._pid1(sock, req)
            return
        previews = fds[: req.previews]
        extra = dict(zip(wire.FDS[req.op].extra, fds[req.previews :], strict=True))
        if req.op is Op.GROUP and not wire.is_sealed(extra["in"]):
            self.requests.append(Seen(req.op, Code.BAD_REQUEST))
            await self._send(sock, wire.done(Code.BAD_REQUEST, "in is niet verzegeld"))
            return
        if child in self.busy or child in self.running:
            self.requests.append(Seen(req.op, Code.BUSY, child=child))
            await self._send(sock, wire.done(Code.BUSY, child.value))
            return
        self._n += 1
        n = self._n
        seen = Seen(req.op, Code.OK, n, child, req.deadline_ms, req.previews)
        self.requests.append(seen)
        work = FakeRequest(req.op, req.args, n, child, req.deadline_ms, previews, extra)
        task = asyncio.create_task(self._run(work))
        running = self.running[child] = Running(n, req.op, task)
        await wire.send(sock, wire.started(n, child))
        peer = asyncio.create_task(wire.receive(sock, max_fds=0))
        freed = False
        try:
            await asyncio.wait({task, peer}, return_when=asyncio.FIRST_COMPLETED)
            if not task.done():  # de app sloot de verbinding (of stuurde iets): afbreken
                running.cancelled = True
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                seen.code = Code.CANCELLED
                return
            code, detail, data = task.result()
            seen.code = code
            # Eerst het kind vrij, dan done: wie na done meteen een nieuwe opdracht stuurt,
            # krijgt geen busy (zo doet pid 1 het ook)
            self._free(child, code)
            freed = True
            await self._send(sock, wire.done(code, detail, data))
        finally:
            if not task.done():  # de nep stopt
                task.cancel()
            peer.cancel()
            with contextlib.suppress(BaseException):
                await peer
            if not freed:
                self._free(child, seen.code)

    def _free(self, child: Child, code: Code) -> None:
        self.running.pop(child, None)
        self._after(child, code)

    async def _run(self, req: FakeRequest) -> tuple[Code, str, dict | BaseModel | None]:
        handler = self._handlers[req.op]
        try:
            async with asyncio.timeout(req.deadline_ms / 1000):
                data = await handler(req)
        except TimeoutError:
            return Code.TIMEOUT, "de deadline was op", None
        except FakeFail as exc:
            return exc.code, exc.detail, None
        except Exception as exc:  # een fout in de handler van de test: internal, zoals de worker
            return Code.INTERNAL, type(exc).__name__, None
        except asyncio.CancelledError:
            running = self.running.get(req.child)
            if running is not None and running.n == req.n and running.cancelled:
                return Code.CANCELLED, "afgebroken", None
            raise
        return Code.OK, "", data

    def _after(self, child: Child, code: Code) -> None:
        """Het kind na een opdracht: na een timeout altijd een nieuw (SIGKILL), en werk na
        max_ops opdrachten of max_age_s."""
        spec = CHILDREN[child]
        self._ops[child] += 1
        old = spec.max_age_s is not None and self.clock() - self._born[child] >= spec.max_age_s
        many = spec.max_ops is not None and self._ops[child] >= spec.max_ops
        if code is Code.TIMEOUT or old or many:
            self.generation[child] += 1
            self._ops[child] = 0
            self._born[child] = self.clock()

    async def _pid1(self, sock: socket.socket, req: wire.Request) -> None:
        if req.op is Op.CAPS:
            self.requests.append(Seen(req.op, Code.OK, deadline_ms=req.deadline_ms))
            await wire.send(sock, wire.done(Code.OK, data=self.caps()))
            return
        target = req.args.n  # type: ignore[attr-defined]
        found = False
        for running in self.running.values():
            if running.n == target and not running.task.done():
                running.cancelled = True
                running.task.cancel()
                found = True
        self.requests.append(Seen(req.op, Code.OK, deadline_ms=req.deadline_ms))
        await wire.send(sock, wire.done(Code.OK, data=wire.CancelData(found=found)))
