"""De server van de mediaworker: één verbinding per opdracht (bouwontwerp fase 5, Keuze 12).
Frozen.

Per verbinding:
1. de vraag lezen (wire.receive: grens op de grootte en op het aantal fd's, CLOEXEC);
2. controleren: PROTOCOL, de opdracht, de argumenten (args.parse), het aantal en de soort fd's
   (fd_layout), synth alleen met --loadtest. Elke weigering is een {"ev": "error"} en dan dicht;
3. een plaats nemen in de soort (Places): vol is busy met de soort. caps (CONTROL) neemt geen
   plaats en start geen proces, dus de gezondheid werkt ook als alles vol is;
4. started sturen en de opdracht draaien (ops/<op>.py, functie run). Een opdracht van een
   pakket dat nog niet klaar is, geeft not_built;
5. result en done sturen. Gaat de verbinding eerder dicht, dan stopt de opdracht: de taak wordt
   gestopt, en elk kind gaat met killpg weg (run.Child.stop), voor de plaats vrijkomt.

De worker draait één event loop zonder threads (preexec_fn in run.py is dan veilig).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import os
import socket
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.media import wire
from app.media.wire import Code, Op, WorkClass
from app.media.worker import args as A
from app.media.worker import run as R

log = logging.getLogger("platform.media.worker")

# Hoe lang een opdracht hooguit duurt (None: tot de app de verbinding sluit)
TIMEOUT: dict[Op, float | None] = {
    Op.CAPS: 5,
    Op.BENCH: 300,
    Op.PROBE: 60,
    Op.KEYFRAMES: 120,
    Op.AUDIO_INFO: 60,
    Op.NFO: 10,
    Op.FRAME: 60,
    Op.META: 30,
    Op.SUBS: 1800,
    Op.SPRITES: 3600,
    Op.STREAM: None,
    Op.REMUX_AUDIO: None,
    Op.SYNTH: 900,
}
KEYFRAMES_FALLBACK_TIMEOUT = 3600.0


class OpError(Exception):
    """Een nette fout van een opdracht: code uit wire.Code, detail ≤ 2.000 tekens."""

    def __init__(self, code: Code, detail: str = ""):
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail


@dataclass
class Config:
    socket: Path
    max_procs: int = 10
    max_transcodes: int = 3
    memory_mb: int = 1200
    local: bool = False
    loadtest: bool = False

    def limits(self) -> dict[WorkClass, int]:
        return {
            WorkClass.TRANSCODE: max(0, self.max_transcodes),
            WorkClass.REMUX: 6,
            WorkClass.BACKGROUND: 1,
            WorkClass.SHORT: 2,
        }


class Places:
    """De plaatsen per soort en het totaal aan processen (Keuze 12). Een opdracht reserveert
    vooraf zoveel processen als hij hooguit tegelijk draait (args.procs); ze komen pas vrij als
    de opdracht klaar is, en die wacht op waitpid van elk kind."""

    def __init__(self, limits: dict[WorkClass, int], max_procs: int):
        self.limits = dict(limits)
        self.max_procs = max(0, max_procs)
        self.used: dict[WorkClass, int] = dict.fromkeys(self.limits, 0)
        self.procs = 0

    def take(self, wc: WorkClass, procs: int) -> bool:
        if wc == WorkClass.CONTROL:
            return True
        if self.used[wc] >= self.limits[wc]:
            return False
        if self.procs + procs > self.max_procs:
            return False
        self.used[wc] += 1
        self.procs += procs
        return True

    def give(self, wc: WorkClass, procs: int) -> None:
        if wc == WorkClass.CONTROL:
            return
        self.used[wc] = max(0, self.used[wc] - 1)
        self.procs = max(0, self.procs - procs)

    def snapshot(self) -> dict[str, Any]:
        return {
            "places": {wc.value: n for wc, n in self.limits.items()},
            "in_use": {wc.value: n for wc, n in self.used.items()},
            "procs": self.procs,
            "max_procs": self.max_procs,
        }


@dataclass
class Context:
    """Wat een opdracht krijgt (ops/<op>.py: `async def run(ctx) -> dict | None`)."""

    op: Op
    args: A._Args
    inputs: list[int]
    outputs: list[int]
    worker: Worker
    send: Callable[[dict], Awaitable[None]]
    children: list[R.Child] = field(default_factory=list)

    @property
    def memory_mb(self) -> int:
        return A.memory_mb(self.op, self.worker.config.memory_mb)

    def spawn(self, argv: list[str], **kwargs) -> R.Child:
        """Een kind met de grenzen van deze opdracht; telt in ctx.children (gestopt bij het
        einde of het sluiten van de verbinding)."""
        kwargs.setdefault("memory_mb", self.memory_mb)
        child = R.spawn([*A.niceness(self.op, self.args), *argv], **kwargs)
        self.children.append(child)
        return child

    async def run(self, argv: list[str], **kwargs) -> tuple[R.Exit, bytes, str]:
        """Een kort kind zonder voorvoegsel (ffprobe), begrensd in tijd en uitvoer."""
        kwargs.setdefault("memory_mb", self.memory_mb)
        return await R.run(argv, **kwargs)

    async def progress(self, t_ms: int, speed: float | None) -> None:
        await self.send({"ev": "progress", "t_ms": t_ms, "speed": speed})


Handler = Callable[[Context], Awaitable[dict | None]]


def handler(op: Op) -> Handler | None:
    """ops/<op>.py met een functie run, of None (nog niet gebouwd)."""
    try:
        module = importlib.import_module(f"app.media.worker.ops.{op.value}")
    except ModuleNotFoundError as exc:
        if exc.name == f"app.media.worker.ops.{op.value}":
            return None
        raise
    return getattr(module, "run", None)


def _check_fds(op: Op, parsed: A._Args, fds: list[int]) -> None:
    layout = A.fd_layout(op, parsed)
    if len(fds) != layout.total:
        raise OpError(Code.BAD_FDS, f"{layout.total} fd's verwacht, {len(fds)} gekregen")
    for i, fd in enumerate(fds):
        mode = os.fstat(fd).st_mode
        if i < layout.inputs:
            if not stat.S_ISREG(mode):
                raise OpError(Code.BAD_FDS, f"invoer {i} is geen gewoon bestand")
        elif op == Op.SYNTH:
            if not stat.S_ISREG(mode):
                raise OpError(Code.BAD_FDS, "de uitvoer van synth is geen gewoon bestand")
        elif not stat.S_ISFIFO(mode):
            raise OpError(Code.BAD_FDS, f"uitvoer {i - layout.inputs} is geen pipe")


class Worker:
    def __init__(self, config: Config, caps: dict[str, Any]):
        self.config = config
        self.caps = caps
        self.places = Places(config.limits(), config.max_procs)
        self._tasks: set[asyncio.Task] = set()
        self._server: asyncio.AbstractServer | None = None

    # --- de opdracht ---

    def _parse(self, message: dict, fds: list[int]) -> tuple[Op, A._Args]:
        if message.get("v") != wire.PROTOCOL:
            raise OpError(Code.PROTOCOL, f"de worker spreekt PROTOCOL {wire.PROTOCOL}")
        try:
            op = Op(message.get("op"))
        except ValueError:
            raise OpError(Code.UNKNOWN_OP, "onbekende opdracht") from None
        try:
            parsed = A.parse(op, message.get("args", {}))
        except ValueError as exc:
            raise OpError(Code.BAD_ARGS, str(exc)) from None
        _check_fds(op, parsed, fds)
        if op == Op.SYNTH and not self.config.loadtest:
            raise OpError(Code.NOT_ALLOWED, "synth alleen met PLATFORM_MEDIA_LOADTEST")
        return op, parsed

    async def _serve(self, sock: socket.socket) -> None:
        closed = False

        async def send(message: dict) -> None:
            if closed:
                raise ConnectionResetError("de app sloot de verbinding")
            await wire.send(sock, message)

        try:
            message, fds = await wire.receive(sock)
        except wire.Closed:
            return
        except wire.WireError as exc:
            with contextlib.suppress(OSError):
                await wire.send(sock, wire.error(exc.code, exc.detail))
            return
        try:
            try:
                op, parsed = self._parse(message, fds)
                run = handler(op)
                if run is None:
                    raise OpError(Code.NOT_BUILT, f"{op.value} is nog niet gebouwd")
            except OpError as exc:
                with contextlib.suppress(OSError):
                    await send(wire.error(exc.code, exc.detail))
                return
            wc = A.work_class(op, parsed)
            procs = A.procs(op, parsed)
            if not self.places.take(wc, procs):
                with contextlib.suppress(OSError):
                    await send(
                        wire.error(Code.BUSY, f"geen plaats in {wc.value}", **{"class": wc.value})
                    )
                return
            try:
                layout = A.fd_layout(op, parsed)
                ctx = Context(op, parsed, fds[: layout.inputs], fds[layout.inputs :], self, send)
                try:
                    await send({"ev": "started"})
                except OSError:
                    return  # de app is al weg
                task = asyncio.create_task(self._execute(ctx, run))

                loop = asyncio.get_running_loop()

                def gone() -> None:
                    # Eén keer: EOF blijft leesbaar, en elke extra cancel zou het wachten op
                    # het kind afbreken (run.Child.stop)
                    nonlocal closed
                    loop.remove_reader(sock.fileno())
                    if not closed:
                        closed = True
                        task.cancel()

                loop.add_reader(sock.fileno(), gone)
                try:
                    reply = await task
                except asyncio.CancelledError:
                    if not closed:
                        raise  # de worker zelf stopt
                    return
                finally:
                    loop.remove_reader(sock.fileno())
                with contextlib.suppress(OSError):
                    await send(reply)
                    if reply.get("ev") != "error":
                        await send({"ev": "done"})
            finally:
                self.places.give(wc, procs)
        finally:
            wire.close_all(fds)

    async def _execute(self, ctx: Context, run: Handler) -> dict:
        """De opdracht met zijn tijdgrens; geeft het laatste antwoord (result of error). Elk
        kind is gestopt en gewacht voor dit terugkomt."""
        limit = TIMEOUT.get(ctx.op)
        if ctx.op == Op.KEYFRAMES and getattr(ctx.args, "fallback", False):
            limit = KEYFRAMES_FALLBACK_TIMEOUT
        try:
            async with asyncio.timeout(limit):
                data = await run(ctx)
            return {"ev": "result", "data": data or {}}
        except TimeoutError:
            return wire.error(Code.TIMEOUT, f"{ctx.op.value} duurde langer dan {limit:g} s")
        except OpError as exc:
            return wire.error(exc.code, exc.detail)
        except FileNotFoundError:
            return wire.error(Code.FFMPEG_MISSING, "ffmpeg of ffprobe ontbreekt")
        except (ConnectionError, BrokenPipeError):
            raise asyncio.CancelledError from None
        except Exception as exc:
            log.exception("opdracht %s", ctx.op.value)
            return wire.error(Code.INTERNAL, exc.__class__.__name__)
        finally:
            for child in ctx.children:
                await child.stop()

    # --- de socket ---

    async def _connection(self, sock: socket.socket) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            await self._serve(sock)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("verbinding")
        finally:
            self._tasks.discard(task)
            sock.close()

    async def start(self) -> None:
        """Luisteren op config.socket (modus 0660). Eerst op een tijdelijke naam, dan rename:
        wie de socket ziet, kan meteen verbinden."""
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
        self._accept_task = asyncio.create_task(self._accept())

    async def _accept(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            conn, _ = await loop.sock_accept(self._listener)
            conn.setblocking(False)
            asyncio.create_task(self._connection(conn))

    async def stop(self) -> None:
        """Niet meer aannemen, elke opdracht stoppen (de kinderen gaan met killpg weg), de
        socket weg."""
        self._accept_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._accept_task
        self._listener.close()
        with contextlib.suppress(FileNotFoundError):
            self.config.socket.unlink()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
