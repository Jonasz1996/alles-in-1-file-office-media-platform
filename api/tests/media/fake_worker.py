"""De nep-mediaworker (§3.6): spreekt het protocol van §3.3 over een echte Unix-socket.

    async with FakeWorker(tmp_path / "w.sock") as fake:
        client.bind(fake.socket, fake.key)    # of Settings(media_worker=…, media_worker_key=…)
        fake.speed = 4.0                      # stream: 4 s mediatijd per seconde
        fake.fault("crash_after", op=Op.STREAM, n=3)
        ...

Wat hij doet, zoals de echte worker (`app/media/worker/server.py`, Keuze 12):
- eerst hello (review S1): het bewijs met `key` (standaard een verse sleutel per nep; `running`
  bindt de client erop) voor de uid van de andere kant. Een vraag of fd's vóór hello: dezelfde
  weigering als de echte;
- één verbinding per opdracht; de vraag gaat door `wire.receive` (grens op de grootte en het
  aantal fd's) en `args.parse`; een andere PROTOCOL, een onbekende opdracht, verkeerde argumenten
  of een verkeerd aantal of soort fd's geeft dezelfde foutcode;
- de plaatsen per soort (transcode = max_transcodes, remux 6, achtergrond 1, kort 2) en het
  totaal aan processen (max_procs); vol is `busy` met de soort. caps telt nooit mee;
- `started`, dan `progress`, `result` en `done`, of `error`; de verbinding dicht is stoppen;
- dezelfde tijdgrenzen per opdracht (`timeouts`, standaard die van de worker), ook die van
  keyframes met de terugval (`fallback_timeout`, standaard KEYFRAMES_FALLBACK_TIMEOUT).

Wat hij anders doet:
- **caps** komt uit `recorded/caps.json` (de echte lokale worker), met de plaatsen van nu;
- **probe** zoekt de bron op zijn inhoud (sha256, via pread: de positie van de fd blijft staan,
  spike punt 1) tussen de fixtures waar een opname van is (`recorded/probe/*.json`), of wat de
  test zelf aanmeldt (`register`). Een onbekende bron is `unknown_format`;
- **stream** schrijft vooraf gemaakte fMP4 (fixtures `stream_h264.mp4` en `stream_vp9.mp4`: 20 s
  360p, gemaakt met de transcode van §3.3, op het rooster van 2 s) naar de echte pipe, ongeacht
  de bron. VP9 + Opus als de vraag `libvpx-vp9` zegt, anders H.264 + AAC (`remux_variant` bij
  remux). Een herstart begint bij transcode op het eerste fragment vanaf `start_s` (origin + 2k),
  bij remux op het keyframe ervoor (zoals ffmpeg met kopiëren, N11). Het tempo kiest de test
  (`speed`, mediaseconden per seconde; None: zo snel als de pipe toelaat). Er wordt alleen
  geschreven als de lezer leest: de tegendruk is echt. Na elk fragment een `progress` met t_ms
  (het eind van het fragment in mediatijd);
- elke andere opdracht geeft `not_built` na `started` (zoals een stub van de worker), tenzij de
  test er een eigen antwoord voor geeft (`on`).

Fouten op bestelling (`fault(kind, op=…, times=1, **params)`), voor de randgevallen van M3, M4
en M10 zonder CPU:
- `crash_after` (n fragmenten; `how="ffmpeg"`: de pipe dicht en `error failed`, `how="worker"`:
  de verbinding valt weg zonder antwoord);
- `hang` (geen uitvoer en geen antwoord meer tot de verbinding dichtgaat of de tijdgrens; bij
  stream met `after=n`: eerst de init en n fragmenten);
- `busy` (`busy` met de soort, ook als er plaats is; `work_class` om een andere soort te noemen);
- `slow_start` (`seconds`: na `started` zo lang niets, dan pas het werk);
- `garbage` (stream: na de init een box van 4 GB met willekeur, dan dicht; een andere opdracht:
  een antwoord dat geen JSON is);
- `new_stsd` (een herstart, `start_s` > 0, krijgt de init van een andere maat: een andere `stsd`).

`processes` telt de opdrachten die nu een proces zouden hebben (zoals de kinderen van de echte
worker): na stoppen moet hij 0 zijn.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import os
import secrets
import socket
import stat
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.media import wire
from app.media.wire import Code, Op, WorkClass
from app.media.worker import args as A
from app.media.worker import server as real_server
from app.media.worker.server import TIMEOUT
from tests.media import boxes, record
from tests.media import fixtures as F

FaultKind = Literal["crash_after", "hang", "busy", "slow_start", "garbage", "new_stsd"]
FAULTS: tuple[str, ...] = FaultKind.__args__  # type: ignore[attr-defined]
VARIANTS = {
    "h264": ("stream_h264.mp4", "stream_h264_alt.mp4"),
    "vp9": ("stream_vp9.mp4", "stream_vp9_alt.mp4"),
}
SOURCE = "stream_src.mkv"  # de bron waar de vooraf gemaakte uitvoer van komt
HASH_LIMIT = 1024 * 1024 * 1024  # een grotere bron is voor de nep onbekend
GARBAGE = b"\xff\xff\xff\xf0moof" + bytes(range(256)) * 256


class OpError(Exception):
    """Een nette fout van een opdracht (zoals server.OpError)."""

    def __init__(self, code: Code, detail: str = ""):
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail


class _Crash(Exception):
    """De nep-worker 'valt om': de verbinding gaat dicht zonder antwoord."""


@dataclass
class Fault:
    kind: FaultKind
    op: Op | None = None
    times: int = 1
    params: dict[str, Any] = field(default_factory=dict)


@functools.cache
def premade(variant: str = "h264", alt: bool = False) -> boxes.Parsed:
    """De vooraf gemaakte uitvoer van stream, geknipt in init en fragmenten (één keer per
    proces; de bestanden zelf staan in de cache van de fixtures)."""
    name = VARIANTS[variant][1 if alt else 0]
    return boxes.parse(F.make(name).read_bytes())


def _limits(max_transcodes: int) -> dict[WorkClass, int]:
    """De plaatsen van Keuze 12."""
    return {
        WorkClass.TRANSCODE: max(0, max_transcodes),
        WorkClass.REMUX: 6,
        WorkClass.BACKGROUND: 1,
        WorkClass.SHORT: 2,
    }


@dataclass
class Job:
    """Wat een handler krijgt (`on`)."""

    op: Op
    args: A._Args
    inputs: list[int]
    outputs: list[int]
    worker: FakeWorker
    send: Callable[[dict], Awaitable[None]]

    async def progress(self, t_ms: int, speed: float | None) -> None:
        await self.send({"ev": "progress", "t_ms": t_ms, "speed": speed})


Handler = Callable[[Job], Awaitable[dict | None]]


@dataclass
class Request:
    """Eén vraag zoals de nep hem kreeg (voor de test)."""

    op: str
    args: dict
    fds: int


class FakeWorker:
    def __init__(
        self,
        socket_path: Path,
        *,
        max_procs: int = 10,
        max_transcodes: int = 3,
        mode: Literal["local", "container"] = "local",
        loadtest: bool = False,
        key: str | None = None,
    ):
        self.socket = socket_path
        self.key = key if key is not None else secrets.token_hex(32)
        self.max_procs = max_procs
        self.max_transcodes = max_transcodes
        self.mode = mode
        self.loadtest = loadtest
        self.speed: float | None = None
        self.remux_variant: Literal["h264", "vp9"] = "h264"
        self.timeouts: dict[Op, float | None] = dict(TIMEOUT)
        self.fallback_timeout: float | None = real_server.KEYFRAMES_FALLBACK_TIMEOUT
        self.requests: list[Request] = []
        self.limits = _limits(max_transcodes)
        self.used: dict[WorkClass, int] = dict.fromkeys(self.limits, 0)
        self.procs = 0
        self._faults: list[Fault] = []
        self._handlers: dict[Op, Handler] = {}
        self._registered: dict[str, str] = {}  # sha256 → naam van de opname
        self._index: dict[str, str] | None = None
        self._tasks: set[asyncio.Task] = set()
        self._listener: socket.socket | None = None
        self._accept: asyncio.Task | None = None

    # --- voor de test ---

    def fault(self, kind: FaultKind, *, op: Op | None = None, times: int = 1, **params) -> None:
        """Een fout voor de volgende `times` opdrachten (van `op`, of elke opdracht)."""
        if kind not in FAULTS:
            raise ValueError(f"onbekende fout {kind}")
        self._faults.append(Fault(kind, op, times, params))

    def clear_faults(self) -> None:
        self._faults.clear()

    def on(self, op: Op, handler: Handler) -> None:
        """Een eigen antwoord voor een opdracht (geeft de data van result, of raist OpError)."""
        self._handlers[op] = handler

    def register(self, source: Path | bytes, name: str) -> None:
        """Deze bron krijgt bij probe de opname van fixture `name`."""
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        self._registered[hashlib.sha256(data).hexdigest()] = name

    @property
    def processes(self) -> int:
        """Hoeveel processen de echte worker nu zou hebben."""
        return self.procs

    def snapshot(self) -> dict[str, Any]:
        return {
            "places": {wc.value: n for wc, n in self.limits.items()},
            "in_use": {wc.value: n for wc, n in self.used.items()},
            "procs": self.procs,
            "max_procs": self.max_procs,
        }

    # --- starten en stoppen ---

    async def start(self) -> FakeWorker:
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

    async def __aenter__(self) -> FakeWorker:
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

    def _take(self, kind: FaultKind, op: Op) -> Fault | None:
        for fault in self._faults:
            if fault.kind == kind and fault.op in (None, op):
                fault.times -= 1
                if fault.times <= 0:
                    self._faults.remove(fault)
                return fault
        return None

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
        layout = A.fd_layout(op, parsed)
        if len(fds) != layout.total:
            raise OpError(Code.BAD_FDS, f"{layout.total} fd's verwacht, {len(fds)} gekregen")
        for i, fd in enumerate(fds):
            mode = os.fstat(fd).st_mode
            if i < layout.inputs or op == Op.SYNTH:
                if not stat.S_ISREG(mode):
                    raise OpError(Code.BAD_FDS, f"fd {i} is geen gewoon bestand")
            elif not stat.S_ISFIFO(mode):
                raise OpError(Code.BAD_FDS, f"uitvoer {i - layout.inputs} is geen pipe")
        if op == Op.SYNTH and not self.loadtest:
            raise OpError(Code.NOT_ALLOWED, "synth alleen met PLATFORM_MEDIA_LOADTEST")
        return op, parsed

    async def _connection(self, sock: socket.socket) -> None:
        fds: list[int] = []
        try:
            await self._serve(sock, fds)
        except (asyncio.CancelledError, _Crash):
            pass
        finally:
            wire.close_all(fds)
            sock.close()

    async def _hello(self, sock: socket.socket) -> bool:
        """Zoals Worker._hello: de nonce zonder fd's, dan het bewijs voor de peer-uid."""
        try:
            message, _ = await wire.receive(sock, max_fds=0)
        except wire.Closed:
            return False
        except wire.WireError as exc:
            with contextlib.suppress(OSError):
                await wire.send(sock, wire.error(exc.code, exc.detail))
            return False
        problem = wire.hello_problem(message)
        if problem is not None:
            with contextlib.suppress(OSError):
                await wire.send(sock, wire.error(*problem))
            return False
        with contextlib.suppress(OSError):
            await wire.send(sock, wire.hello_reply(self.key, message["hello"], wire.peer_uid(sock)))
            return True
        return False

    async def _serve(self, sock: socket.socket, fds: list[int]) -> None:
        closed = False

        async def send(message: dict) -> None:
            if closed:
                raise ConnectionResetError("de app sloot de verbinding")
            await wire.send(sock, message)

        if not await self._hello(sock):
            return
        try:
            message, got = await wire.receive(sock)
        except wire.Closed:
            return
        except wire.WireError as exc:
            with contextlib.suppress(OSError):
                await wire.send(sock, wire.error(exc.code, exc.detail))
            return
        fds.extend(got)
        self.requests.append(
            Request(str(message.get("op")), dict(message.get("args") or {}), len(fds))
        )
        try:
            op, parsed = self._parse(message, fds)
        except OpError as exc:
            with contextlib.suppress(OSError):
                await send(wire.error(exc.code, exc.detail))
            return
        wc = A.work_class(op, parsed)
        procs = A.procs(op, parsed)
        busy = self._take("busy", op)
        if busy is not None or not self._take_place(wc, procs):
            named = WorkClass(busy.params.get("work_class", wc)) if busy else wc
            with contextlib.suppress(OSError):
                busy_reply = wire.error(Code.BUSY, f"geen plaats in {named.value}")
                busy_reply["class"] = named.value
                await send(busy_reply)
            return
        try:
            layout = A.fd_layout(op, parsed)
            job = Job(op, parsed, fds[: layout.inputs], fds[layout.inputs :], self, send)
            try:
                await send({"ev": "started"})
            except OSError:
                return
            task = asyncio.create_task(self._execute(job))
            loop = asyncio.get_running_loop()

            def gone() -> None:
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
                    raise
                return
            finally:
                with contextlib.suppress(ValueError, OSError):
                    loop.remove_reader(sock.fileno())
            if reply is None:  # garbage: een antwoord dat geen JSON is
                with contextlib.suppress(OSError):
                    await asyncio.get_running_loop().sock_sendall(sock, b"\x00\x00\x00\x05{nee}")
                return
            with contextlib.suppress(OSError):
                await send(reply)
                if reply.get("ev") != "error":
                    await send({"ev": "done"})
        finally:
            self._give_place(wc, procs)

    def _take_place(self, wc: WorkClass, procs: int) -> bool:
        if wc == WorkClass.CONTROL:
            return True
        if self.used[wc] >= self.limits[wc] or self.procs + procs > self.max_procs:
            return False
        self.used[wc] += 1
        self.procs += procs
        return True

    def _give_place(self, wc: WorkClass, procs: int) -> None:
        if wc == WorkClass.CONTROL:
            return
        self.used[wc] = max(0, self.used[wc] - 1)
        self.procs = max(0, self.procs - procs)

    async def _execute(self, job: Job) -> dict | None:
        limit = self.timeouts.get(job.op)
        if job.op == Op.KEYFRAMES and getattr(job.args, "fallback", False):
            limit = self.fallback_timeout  # zoals server._execute (review C3)
        try:
            async with asyncio.timeout(limit):
                slow = self._take("slow_start", job.op)
                if slow is not None:
                    await asyncio.sleep(float(slow.params.get("seconds", 5)))
                hang = None if job.op == Op.STREAM else self._take("hang", job.op)
                if hang is not None:
                    await asyncio.Event().wait()
                if job.op != Op.STREAM and self._take("garbage", job.op) is not None:
                    return None
                data = await self._run(job)
            return {"ev": "result", "data": data or {}}
        except TimeoutError:
            return wire.error(Code.TIMEOUT, f"{job.op.value} duurde langer dan {limit:g} s")
        except OpError as exc:
            return wire.error(exc.code, exc.detail)
        except (ConnectionError, BrokenPipeError):
            raise asyncio.CancelledError from None

    async def _run(self, job: Job) -> dict | None:
        handler = self._handlers.get(job.op)
        if handler is not None:
            return await handler(job)
        if job.op == Op.CAPS:
            return self.caps()
        if job.op == Op.PROBE:
            return await self._probe(job)
        if job.op == Op.STREAM:
            return await self._stream(job)
        raise OpError(Code.NOT_BUILT, f"{job.op.value} doet de nep-worker niet (on)")

    # --- caps en probe ---

    def caps(self) -> dict[str, Any]:
        out = record.load_caps()
        out.update(
            protocol=wire.PROTOCOL,
            mode=self.mode,
            sandbox="off" if self.mode == "local" else "unchecked",  # zoals caps.gather
            max_procs=self.max_procs,
            max_transcodes=self.max_transcodes,
        )
        out.update(self.snapshot())
        return out

    def _known(self, *, refresh: bool = False) -> dict[str, str]:
        """sha256 → naam, van elke opgenomen fixture die hier gemaakt is. refresh: opnieuw
        kijken (een fixture kan na de eerste probe gemaakt zijn)."""
        if self._index is None or refresh:
            index: dict[str, str] = {}
            for file in sorted(record.PROBE_DIR.glob("*.json")):
                if file.name.startswith("_"):
                    continue
                name = record.name_of(file)
                first = name.partition("/")[0]
                if first not in F.RECIPES or not F.path_of(first).exists():
                    continue
                path = record.path(name)
                if path.is_file():
                    index[_sha_file(path)] = name
            self._index = index
        return {**self._index, **self._registered}

    async def _probe(self, job: Job) -> dict:
        digest = await asyncio.to_thread(_sha_fd, job.inputs[0]) or ""
        name = self._known().get(digest)
        if name is None and digest:
            name = (await asyncio.to_thread(self._known, refresh=True)).get(digest)
        found = record.load(name) if name else None
        if found is None:
            raise OpError(Code.UNKNOWN_FORMAT, "geen bekend formaat (nep-worker)")
        if "error" in found:
            raise OpError(Code(found["error"]["code"]), found["error"].get("detail", ""))
        return found["data"]

    # --- stream ---

    async def _stream(self, job: Job) -> dict:
        args = job.args
        assert isinstance(args, A.StreamArgs)
        if args.mode == "transcode":
            assert isinstance(args.video, A.VideoEncode)
            variant = "vp9" if args.video.codec == "libvpx-vp9" else "h264"
        else:
            variant = self.remux_variant
        alt = args.start_s > 0 and self._take("new_stsd", Op.STREAM) is not None
        parsed = premade(variant, alt)
        start_ms = round(args.start_s * 1000)
        frags = parsed.fragments
        if start_ms > 0:
            if args.mode == "transcode":
                frags = [f for f in frags if f.video_start_ms >= start_ms - 1]
            else:  # kopiëren begint op het keyframe vóór start_s (N11)
                before = [i for i, f in enumerate(frags) if f.video_start_ms <= start_ms]
                frags = frags[before[-1] if before else 0 :]
        crash = self._take("crash_after", Op.STREAM)
        hang = self._take("hang", Op.STREAM)
        garbage = self._take("garbage", Op.STREAM)
        out = job.outputs[0]
        os.set_blocking(out, False)
        try:
            if hang is not None and hang.params.get("after") is None:
                await asyncio.Event().wait()
            await _write(out, parsed.init)
            if garbage is not None:
                await _write(out, GARBAGE)
                return {"fragments": 0}
            last = frags[0].video_start_ms if frags else 0
            for i, frag in enumerate(frags):
                if crash is not None and i >= int(crash.params.get("n", 1)):
                    if crash.params.get("how", "ffmpeg") == "worker":
                        raise _Crash
                    raise OpError(Code.FAILED, "ffmpeg stopte met code 1 (nep-worker)")
                if hang is not None and i >= int(hang.params["after"]):
                    await asyncio.Event().wait()
                if self.speed:
                    await asyncio.sleep(max(0, frag.end_ms - last) / 1000 / self.speed)
                last = frag.end_ms
                await _write(out, frag.data)
                await job.progress(frag.end_ms, self.speed)
            return {"fragments": len(frags)}
        finally:
            # De pipe dicht zoals een gestopte ffmpeg (de lezer krijgt EOF); de fd zelf sluit
            # _connection, dus hier een lege plek in de lijst
            _close_output(job, out)


def _close_output(job: Job, fd: int) -> None:
    with contextlib.suppress(OSError):
        # dup2 van /dev/null op de fd: de schrijfkant is dicht, het nummer blijft van ons
        null = os.open(os.devnull, os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.dup2(null, fd, inheritable=False)
        finally:
            os.close(null)


async def _write(fd: int, data: bytes) -> None:
    """Alles schrijven, wachtend als de pipe vol is (de tegendruk). BrokenPipeError als de
    lezer weg is: zoals ffmpeg dan stopt, is de opdracht mislukt."""
    loop = asyncio.get_running_loop()
    view = memoryview(data)
    while view:
        try:
            view = view[os.write(fd, view) :]
        except BlockingIOError:
            ready = loop.create_future()

            def wake(fut: asyncio.Future = ready) -> None:
                if not fut.done():
                    fut.set_result(None)

            loop.add_writer(fd, wake)
            try:
                await ready
            finally:
                loop.remove_writer(fd)
        except BrokenPipeError:
            raise OpError(Code.FAILED, "de pipe naar de app is dicht") from None


def _sha_fd(fd: int) -> str | None:
    digest = hashlib.sha256()
    offset = 0
    while offset < HASH_LIMIT:
        chunk = os.pread(fd, 1 << 20, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        offset += len(chunk)
    return None


def _sha_file(path: Path) -> str:
    st = path.stat()
    return _sha_cached(str(path), st.st_size, st.st_mtime_ns)


@functools.cache
def _sha_cached(path: str, size: int, mtime_ns: int) -> str:
    """Eén keer per bestand en versie (de fixtures veranderen niet binnen een run)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextlib.asynccontextmanager
async def running(socket_path: Path, **kwargs) -> AsyncIterator[FakeWorker]:
    """Een nep-worker voor de duur van het blok, met de client erop gebonden."""
    from app.media import client

    fake = FakeWorker(socket_path, **kwargs)
    await fake.start()
    client.bind(fake.socket, fake.key)
    try:
        yield fake
    finally:
        client.bind(None)
        await fake.stop()
