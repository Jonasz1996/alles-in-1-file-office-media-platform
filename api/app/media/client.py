"""De enige weg van de app naar de mediaworker (bouwontwerp fase 5, §3.3). Frozen.

    job, (out,) = await client.call(Op.FRAME, FrameArgs(...), source=path, outputs=1)
    ... lees out (asyncio), en dan:
    data = await job.result()

call opent de bron zelf (O_RDONLY | O_NOFOLLOW | O_CLOEXEC), per opdracht opnieuw (spike punt
1: een fd deelt zijn positie met elk kind), maakt `outputs` pipes, stuurt de schrijfkanten mee,
sluit de eigen kopieën en geeft de leeskanten terug (D7). Wie een leeskant krijgt, sluit hem
zelf, ook als hij stopt met lezen: een gepauzeerde asyncio-transport krijgt na een kill geen EOF
(spike punt 4).

Vóór er iets geopend of gestuurd wordt, moet de worker zich bewijzen (hello, wire.py; review
S1): een HMAC met de sleutel (PLATFORM_MEDIA_WORKER_KEY, of bij de lokale worker een sleutel
uit het geheim van de app: Settings.media_key) over een verse nonce en de uid van de app zoals
de worker die ziet. Lukt dat niet, dan WorkerDown, en heeft de andere kant niets gekregen.

call wacht op het eerste antwoord van de worker. Is dat busy, dan WorkerBusy; een andere
PROTOCOL, een worker die zich niet bewijst of geen verbinding is WorkerDown; elke andere fout
WorkerFailed. Zo ligt er na call nooit een half gestarte opdracht.

De verbinding sluiten (Job.cancel, of de taak die stopt) is stoppen: de worker doet killpg.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import socket
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from pydantic import BaseModel

from app.media import wire
from app.media.types import Probe, WorkerCaps
from app.media.wire import Code, Op, WorkClass
from app.media.worker.args import CapsArgs, ProbeArgs

CONNECT_TIMEOUT = 5.0
HELLO_TIMEOUT = 5.0
PROBE_TIMEOUT = 60.0
CAPS_TIMEOUT = 5.0

_socket: Path | None = None
_key: str | None = None


class WorkerDown(Exception):
    """Geen verbinding met de worker, of hij spreekt een andere PROTOCOL."""


class WorkerBusy(Exception):
    """Geen plaats in deze soort, of het totaal aan processen is vol (Keuze 12). De app maakt
    er 503 media_busy {reason: worker} van; op de achtergrond jobs.Defer(30)."""

    def __init__(self, work_class: WorkClass | str):
        super().__init__(f"worker druk ({work_class})")
        try:
            self.work_class = WorkClass(work_class)
        except ValueError:
            self.work_class = WorkClass.SHORT


class WorkerFailed(Exception):
    """De opdracht mislukte; code is een wire.Code, detail ≤ 2.000 tekens van stderr."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def bind(socket_path: Path | None, key: str | None = None) -> None:
    """Waar de worker luistert en met welke sleutel hij zich bewijst (runtime.start); None =
    Settings.media_socket en Settings.media_key()."""
    global _socket, _key
    _socket = socket_path
    _key = key


def socket_path() -> Path:
    if _socket is not None:
        return _socket
    from app.config import get_settings

    return get_settings().media_socket


def worker_key() -> str:
    if _key is not None:
        return _key
    from app.config import get_settings

    return get_settings().media_key()


def _raise(message: dict) -> None:
    code = str(message.get("code") or Code.INTERNAL.value)
    detail = str(message.get("detail") or "")[: wire.DETAIL_CHARS]
    if code == Code.BUSY:
        raise WorkerBusy(str(message.get("class") or WorkClass.SHORT.value))
    if code == Code.PROTOCOL:
        raise WorkerDown(f"andere PROTOCOL: {detail}")
    raise WorkerFailed(code, detail)


class Job:
    """Een lopende opdracht: één verbinding met de worker."""

    def __init__(self, sock: socket.socket, op: Op):
        self._sock = sock
        self.op = op
        self.data: dict = {}
        self.finished = False
        self._reading = False
        self._cancelled = False

    async def events(self) -> AsyncIterator[dict]:
        """De antwoorden na started: progress en result, tot done. WorkerFailed bij een
        fout; WorkerDown als de verbinding wegvalt zonder done."""
        while not self.finished:
            self._reading = True
            try:
                message, fds = await wire.receive(self._sock, max_fds=0)
            except (wire.Closed, wire.WireError, OSError) as exc:
                self.finished = True
                self._close()
                if self._cancelled:
                    return
                if isinstance(exc, wire.Closed):
                    raise WorkerDown("verbinding met de worker viel weg") from None
                raise WorkerDown(f"geen geldig antwoord: {exc}") from None
            finally:
                self._reading = False
            wire.close_all(fds)
            if self._cancelled:
                self.finished = True
                self._close()
                return
            ev = message.get("ev")
            if ev == "error":
                self.finished = True
                self._close()
                _raise(message)
            if ev == "done":
                self.finished = True
                self._close()
                return
            if ev == "result":
                data = message.get("data")
                self.data = data if isinstance(data, dict) else {}
            yield message

    async def result(self) -> dict:
        """Wacht tot done; de data van het laatste result (of {})."""
        async for _ in self.events():
            pass
        return self.data

    async def cancel(self) -> None:
        """De verbinding dicht = de worker stopt de procesgroep van het kind. Wacht er een
        andere taak in events(), dan wekt shutdown die (EOF) en sluit die de socket."""
        self._cancelled = True
        if self._sock.fileno() < 0:
            return
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        if not self._reading:
            self._close()

    def _close(self) -> None:
        if self._sock.fileno() >= 0:
            self._sock.close()

    async def __aenter__(self) -> Job:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.cancel()


async def _connect(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.setblocking(False)
    try:
        with wire.socket_address(path) as address:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await asyncio.get_running_loop().sock_connect(sock, address)
        # Een volle wachtrij (EAGAIN) ziet asyncio bij een Unix-socket als verbonden; dan is er
        # geen andere kant
        sock.getpeername()
    except (OSError, TimeoutError) as exc:
        sock.close()
        raise WorkerDown(f"geen verbinding met de worker: {exc.__class__.__name__}") from None
    return sock


async def _hello(sock: socket.socket, key: str) -> None:
    """De worker bewijst zich (wire.py): een verse nonce, en dan het bewijs voor de euid van
    de app. Geen fd's in beide richtingen."""
    nonce = secrets.token_hex(16)
    me = os.geteuid()
    try:
        async with asyncio.timeout(HELLO_TIMEOUT):
            await wire.send(sock, wire.hello(nonce))
            reply, extra = await wire.receive(sock, max_fds=0)
    except wire.Closed:
        raise WorkerDown("de worker sloot de verbinding bij hello") from None
    except TimeoutError:
        raise WorkerDown(f"geen hello van de worker binnen {HELLO_TIMEOUT:g} s") from None
    except (wire.WireError, OSError) as exc:
        raise WorkerDown(f"geen geldig hello: {exc}") from None
    wire.close_all(extra)
    if reply.get("ev") == "error":
        code = str(reply.get("code") or Code.INTERNAL.value)
        detail = str(reply.get("detail") or "")[:200]
        raise WorkerDown(f"de worker weigerde hello ({code}: {detail})")
    if reply.get("ev") != "hello" or reply.get("v") != wire.PROTOCOL:
        raise WorkerDown(f"geen hello maar {reply.get('ev')!r} (andere PROTOCOL?)")
    uid = reply.get("uid")
    if type(uid) is not int or uid != me:
        raise WorkerDown(f"de worker ziet uid {uid!r} en niet {me}: iets anders zit op de socket")
    got = str(reply.get("proof") or "").encode()
    if not hmac.compare_digest(got, wire.proof(key, nonce, me).encode()):
        raise WorkerDown("de worker bewijst zich niet (PLATFORM_MEDIA_WORKER_KEY klopt niet)")


async def connect(path: Path | None = None, key: str | None = None) -> socket.socket:
    """Een verbinding met de worker, die zich al bewezen heeft (hello). Voor call; de tests
    sturen er hun eigen vragen over."""
    key = worker_key() if key is None else key
    if len(key) < wire.KEY_MIN:
        raise WorkerDown(
            f"geen sleutel voor de worker (PLATFORM_MEDIA_WORKER_KEY, minstens {wire.KEY_MIN} "
            "tekens)"
        )
    sock = await _connect(socket_path() if path is None else path)
    try:
        await _hello(sock, key)
    except BaseException:
        sock.close()
        raise
    return sock


def _open_source(source: Path) -> int:
    return os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)


async def call(
    op: Op,
    args: BaseModel,
    *,
    source: Path | None = None,
    outputs: int = 0,
    inputs: int = 1,
    fds: Sequence[int] = (),
) -> tuple[Job, list[int]]:
    """Start een opdracht. De fd's gaan als [invoer…, fds…, uitvoer…]:

    - source wordt `inputs` keer los geopend (2 bij inbranden: twee lezers, spike punt 14);
    - fds zijn extra fd's van de aanroeper (bij subs de pipes naar de worker, bij synth het
      bestand); call sluit ze niet;
    - outputs pipes: de schrijfkanten gaan mee, de leeskanten komen terug (CLOEXEC).

    Eerst de verbinding en hello: pas als de worker zich bewezen heeft, opent call de bron en
    maakt hij de pipes. De open() van de bron mag mislukken (FileNotFoundError, of ELOOP voor
    een symlink): dan gaat er niets naar de worker."""
    sock = await connect()
    own: list[int] = []  # wat na het sturen dicht moet
    reads: list[int] = []
    try:
        if source is not None:
            for _ in range(max(1, inputs)):
                own.append(_open_source(source))
        sent = list(own) + list(fds)
        for _ in range(outputs):
            r, w = os.pipe2(os.O_CLOEXEC)
            reads.append(r)
            own.append(w)
            sent.append(w)
        message = {"v": wire.PROTOCOL, "op": op.value, "args": args.model_dump(mode="json")}
        try:
            await wire.send(sock, message, sent)
        except OSError as exc:
            raise WorkerDown(f"sturen mislukte: {exc.__class__.__name__}") from None
        wire.close_all(own)
        own = []
        job = Job(sock, op)
        try:
            first, extra = await wire.receive(sock, max_fds=0)
        except wire.Closed:
            raise WorkerDown("de worker sloot de verbinding") from None
        except (wire.WireError, OSError) as exc:
            raise WorkerDown(f"geen geldig antwoord: {exc}") from None
        wire.close_all(extra)
        if first.get("ev") == "error":
            _raise(first)
        if first.get("ev") != "started":
            raise WorkerDown(f"onverwacht antwoord {first.get('ev')!r}")
        return job, reads
    except BaseException:
        wire.close_all(own)
        wire.close_all(reads)
        sock.close()
        raise


async def caps(*, limit_s: float = CAPS_TIMEOUT) -> WorkerCaps:
    """Wat de worker kan (uit zijn cache; telt niet mee in de plaatsen)."""
    async with asyncio.timeout(limit_s):
        job, _ = await call(Op.CAPS, CapsArgs())
        async with job:
            return WorkerCaps.model_validate(await job.result())


async def probe(source: Path, *, limit_s: float = PROBE_TIMEOUT) -> Probe:
    """De probe van één bestand (Keuze 15). WorkerFailed met code unreadable,
    unknown_format als de bron niet te lezen is. Een bron boven 8192×4320 geeft een Probe met
    state failed en error too_large (Keuze 15)."""
    async with asyncio.timeout(limit_s):
        job, _ = await call(Op.PROBE, ProbeArgs(), source=source)
        async with job:
            return Probe.model_validate(await job.result())
