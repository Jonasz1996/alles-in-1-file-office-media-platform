"""De enige weg van de app naar de container analyse (fase 6, §3.11). Een contract van A0; X2a
neemt het over (bouwontwerp §2.0) en bouwt erop.

    data = await client.call(settings, Op.FACES, FacesArgs(), fds=[preview, uit],
                             deadline_ms=10_000)

    job = await client.start(settings, Op.GROUP, args, fds=[invoer, uit])
    job.n, job.child          # het nummer en het kind uit started
    data = await job.result() # of: await client.cancel(settings, job.n), of job.close()

Vóór er een fd naar de worker gaat, bewijst hij zich (wire.py): een verse nonce, en dan het
bewijs voor de euid van de app, met LABEL van de analyse. Dat is wat media.client._hello doet,
met een eigen label: een bewijs van de mediaworker telt hier niet.

Wat er mis kan gaan:

- AnalyseDown(reason): de reden is een van REASONS (Keuze 59). Geen verbinding, een verkeerde
  handdruk, geen antwoord binnen wire.ANSWER_S, de sleutel (key_missing, key_reused) of een
  worker die weigert (sandbox). Er is dan niets naar de worker gegaan.
- AnalyseBusy(child): het kind van deze opdracht heeft al een opdracht.
- AnalyseFailed(code, detail): elke andere code (not_built, timeout, cancelled, unreadable,
  too_big, image_bad, model_missing, bad_request, internal).

De fd's blijven van de aanroeper: call en start sluiten ze niet. De sleutel staat nooit in een
fout of een log; een bericht ook niet (zoektekst).
"""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import socket
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.analyse import wire
from app.analyse.wire import Child, Code, Op
from app.config import Settings

CONNECT_TIMEOUT = wire.ANSWER_S
HELLO_TIMEOUT = wire.ANSWER_S
ANSWER_TIMEOUT = wire.ANSWER_S

Reason = Literal[
    "key_missing", "key_reused", "no_socket", "refused", "handshake", "no_answer", "sandbox"
]
REASONS: tuple[Reason, ...] = (
    "key_missing",
    "key_reused",
    "no_socket",
    "refused",
    "handshake",
    "no_answer",
    "sandbox",
)


class AnalyseDown(Exception):
    """De analyse is niet bereikbaar of niet te vertrouwen; reason is een van REASONS."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


class AnalyseBusy(Exception):
    """Het kind van de opdracht heeft al een opdracht (Keuze 56). Op de achtergrond: later."""

    def __init__(self, child: str) -> None:
        super().__init__(f"{child} is bezig")
        self.child = child


class AnalyseFailed(Exception):
    """De opdracht kreeg een andere code dan ok; detail is kort en zonder waarden."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _key(settings: Settings) -> str:
    problem = settings.analyse_key_problem()
    if problem is not None:
        raise AnalyseDown(problem)
    return settings.analyse_worker_key


async def _connect(settings: Settings) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.setblocking(False)
    try:
        with wire.socket_address(settings.analyse_worker) as address:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await asyncio.get_running_loop().sock_connect(sock, address)
        # Een volle wachtrij (EAGAIN) ziet asyncio bij een Unix-socket als verbonden
        sock.getpeername()
    except (FileNotFoundError, NotADirectoryError):
        sock.close()
        raise AnalyseDown("no_socket") from None
    except TimeoutError:
        sock.close()
        raise AnalyseDown("no_answer") from None
    except OSError:
        sock.close()
        raise AnalyseDown("refused") from None
    return sock


def _refused(message: dict) -> AnalyseDown | None:
    if message.get("ev") == "done" and message.get("code") == Code.SANDBOX:
        return AnalyseDown("sandbox")
    return None


async def _hello(sock: socket.socket, key: str) -> None:
    nonce = secrets.token_hex(16)
    me = os.geteuid()
    try:
        async with asyncio.timeout(HELLO_TIMEOUT):
            await wire.send(sock, wire.hello(nonce))
            reply, extra = await wire.receive(sock, max_fds=0)
    except TimeoutError:
        raise AnalyseDown("no_answer") from None
    except (wire.Closed, wire.WireError, OSError):
        raise AnalyseDown("handshake") from None
    wire.close_all(extra)
    refused = _refused(reply)
    if refused is not None:
        raise refused
    if reply.get("ev") != "hello" or reply.get("v") != wire.PROTOCOL:
        raise AnalyseDown("handshake")
    uid = reply.get("uid")
    if type(uid) is not int or uid != me:
        raise AnalyseDown("handshake")
    got = str(reply.get("proof") or "").encode()
    if not hmac.compare_digest(got, wire.proof(key, nonce, me).encode()):
        raise AnalyseDown("handshake")


async def connect(settings: Settings) -> socket.socket:
    """Een verbinding met een worker die zich bewezen heeft. Voor start; de tests sturen er hun
    eigen vragen over."""
    key = _key(settings)
    sock = await _connect(settings)
    try:
        await _hello(sock, key)
    except BaseException:
        sock.close()
        raise
    return sock


_CODES = frozenset(code.value for code in Code)


def _plain(text: str) -> str:
    """Eén regel zonder stuurtekens: de detail van de worker kan in een fout en in het log
    (review A0, veilig F3)."""
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())


def _raise(message: dict, op: Op) -> None:
    code = str(message.get("code") or Code.INTERNAL.value)
    if code not in _CODES:
        code = Code.INTERNAL.value  # een code die de app niet kent, komt niet in een fout
    detail = _plain(str(message.get("detail") or "")[: wire.DETAIL_CHARS])
    if code == Code.BUSY:
        child = wire.CHILD[op]
        raise AnalyseBusy(child.value if child else "pid1")
    if code == Code.SANDBOX:
        raise AnalyseDown("sandbox")
    raise AnalyseFailed(code, detail)


class Job:
    """Een opdracht die een plaats kreeg (started): n en child komen van de worker. Een
    opdracht van pid 1 (caps, cancel) heeft geen nummer en is meteen klaar."""

    def __init__(self, sock: socket.socket | None, op: Op, deadline_ms: int) -> None:
        self._sock = sock
        self.op = op
        self.deadline_ms = deadline_ms
        self.n: int | None = None
        self.child: Child | None = None
        self.data: dict | None = None

    async def result(self) -> dict:
        """Wacht op done: de data bij ok, anders AnalyseBusy, AnalyseDown of AnalyseFailed.
        Komt done niet binnen de deadline plus wire.KILL_GRACE_S (en een seconde), dan gaat de
        verbinding dicht (de worker breekt af) en is het timeout."""
        if self.data is not None:
            return self.data
        if self._sock is None:
            raise AnalyseFailed(Code.INTERNAL.value, "geen verbinding")
        limit = self.deadline_ms / 1000 + wire.KILL_GRACE_S + 1
        try:
            async with asyncio.timeout(limit):
                message, fds = await wire.receive(self._sock, max_fds=0)
        except TimeoutError:
            self.close()
            raise AnalyseFailed(Code.TIMEOUT.value, "geen done binnen de deadline") from None
        except (wire.Closed, wire.WireError, OSError):
            self.close()
            raise AnalyseFailed(Code.INTERNAL.value, "de verbinding viel weg") from None
        wire.close_all(fds)
        self.close()
        if message.get("ev") != "done":
            raise AnalyseFailed(Code.INTERNAL.value, "geen done")
        if message.get("code") != Code.OK:
            _raise(message, self.op)
        data = message.get("data")
        self.data = data if isinstance(data, dict) else {}
        return self.data

    def close(self) -> None:
        """De verbinding dicht. Loopt de opdracht nog, dan breekt de worker hem af."""
        if self._sock is not None and self._sock.fileno() >= 0:
            self._sock.close()

    async def __aenter__(self) -> Job:
        return self

    async def __aexit__(self, *exc) -> None:
        self.close()


async def start(
    settings: Settings,
    op: Op,
    args: BaseModel | dict,
    *,
    fds: Sequence[int] = (),
    deadline_ms: int | None = None,
) -> Job:
    """Verbinden, hello, de vraag met de fd's, en wachten op started of done (hooguit
    ANSWER_TIMEOUT). Na start ligt er nooit een half gestarte opdracht: busy, not_built en
    bad_request komen hier al als fout. Een vraag boven MAX_MESSAGE of met te veel fd's gaat
    niet weg (bad_request, zonder verbinding)."""
    message = wire.request(op, args, deadline_ms)
    try:
        wire.encode(message)
    except wire.WireError:
        raise AnalyseFailed(Code.BAD_REQUEST.value, "de vraag is groter dan MAX_MESSAGE") from None
    if len(fds) > wire.MAX_FDS:
        raise AnalyseFailed(Code.BAD_REQUEST.value, f"hooguit {wire.MAX_FDS} fd's")
    sock = await connect(settings)
    try:
        await wire.send(sock, message, fds)
        try:
            async with asyncio.timeout(ANSWER_TIMEOUT):
                reply, extra = await wire.receive(sock, max_fds=0)
        except TimeoutError:
            raise AnalyseDown("no_answer") from None
        except (wire.Closed, wire.WireError, OSError):
            raise AnalyseFailed(Code.INTERNAL.value, "de verbinding viel weg") from None
        wire.close_all(extra)
        job = Job(sock, op, message["deadline_ms"])
        if reply.get("ev") == "done":
            sock.close()
            if reply.get("code") != Code.OK:
                _raise(reply, op)
            data = reply.get("data")
            job.data = data if isinstance(data, dict) else {}
            return job
        n = reply.get("n")
        if reply.get("ev") != "started" or type(n) is not int or n < 1:
            raise AnalyseFailed(Code.INTERNAL.value, "geen started")
        try:
            job.child = Child(reply.get("child"))
        except ValueError:
            raise AnalyseFailed(Code.INTERNAL.value, "onbekend kind") from None
        job.n = n
        return job
    except BaseException:
        sock.close()
        raise


async def call(
    settings: Settings,
    op: Op,
    args: BaseModel | dict,
    *,
    fds: Sequence[int] = (),
    deadline_ms: int | None = None,
) -> dict:
    """start en result in één. Stopt de taak (CancelledError), dan gaat de verbinding dicht en
    breekt de worker de opdracht af."""
    job = await start(settings, op, args, fds=fds, deadline_ms=deadline_ms)
    try:
        return await job.result()
    finally:
        job.close()


async def caps(settings: Settings) -> wire.CapsData:
    """caps van pid 1 (zonder plaats). Een antwoord dat niet klopt, is AnalyseFailed."""
    data = await call(settings, Op.CAPS, wire.CapsArgs())
    try:
        return wire.CapsData.model_validate(data)
    except ValidationError:
        raise AnalyseFailed(Code.INTERNAL.value, "caps klopt niet") from None


async def cancel(settings: Settings, n: int) -> bool:
    """Breek opdracht n af; False als er geen opdracht met dat nummer (meer) liep."""
    data = await call(settings, Op.CANCEL, wire.CancelArgs(n=n))
    return bool(data.get("found"))
