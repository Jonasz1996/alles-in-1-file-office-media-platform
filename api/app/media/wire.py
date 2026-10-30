"""Het protocol tussen de app en de mediaworker (bouwontwerp fase 5, Keuze 12). Frozen.

Eén verbinding per opdracht, over een Unix-socket. Een boodschap is 4 bytes lengte (big endian)
en dan JSON, hooguit MAX_MESSAGE bytes.

Eerst bewijst de worker wie hij is (review S1), en pas daarna gaan er fd's naar hem toe:

    app:        {"v": 1, "hello": "<nonce: 32 hex>"}                         (zonder fd's)
    worker:     {"ev": "hello", "v": 1, "uid": <uid>, "proof": "<64 hex>"}

proof = HMAC-SHA256(sleutel, "platform-media-worker|<PROTOCOL>|<nonce>|<uid>") (proof()), met
uid de uid die de worker van de andere kant ziet (SO_PEERCRED, peer_uid()). De app eist zijn
eigen euid en een kloppend bewijs; anders WorkerDown en dicht, zonder dat er een bron geopend
of gestuurd is. Zo krijgt alleen wie de sleutel heeft (PLATFORM_MEDIA_WORKER_KEY, Keuze 11)
een fd van de app. Een kind van de worker heeft hem niet: het krijgt een kale omgeving
(run.environment) en kan niet in de worker kijken (PR_SET_DUMPABLE 0). Een tussenpersoon die de
socket overneemt en doorgeeft, ziet de worker met zijn eigen uid: dan klopt het bewijs voor de
uid van de app niet. Een vraag zonder hello, of fd's op hello, weigert de worker.

Daarna draagt alleen de vraag (de eerste boodschap na hello) file descriptors, als SCM_RIGHTS:
[invoer…, uitvoer…].

    vraag:      {"v": 1, "op": "<naam>", "args": {…}}
    antwoorden: {"ev": "started"}
                {"ev": "progress", "t_ms": …, "speed": …}
                {"ev": "result", "data": {…}}
                {"ev": "done"}
                {"ev": "error", "code": "<Code>", "detail": "…"}   (+ "class" bij busy)

De verbinding sluiten is stoppen: de worker stopt de procesgroep van het kind (SIGTERM, na 2 s
SIGKILL). detail is hooguit 2.000 tekens van stderr; er staan geen paden in, want ffmpeg kent
alleen `fd:`.

Ontvangen fd's krijgen meteen FD_CLOEXEC (recvmsg met MSG_CMSG_CLOEXEC): `socket.recv_fds`
doet dat niet, en dan zou elk kind ze erven (spike, punt 2). Te veel fd's, een te grote
boodschap of fd's op een latere boodschap: weigeren, en elke ontvangen fd gaat dicht.

send en receive werken op een niet-blokkerende socket in de event loop (asyncio heeft geen
sock_sendmsg en sock_recvmsg); ze wachten met add_reader en add_writer.
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import re
import socket
import struct
from collections.abc import Iterator, Sequence
from enum import StrEnum
from pathlib import Path

PROTOCOL = 1
MAX_MESSAGE = 64 * 1024
# Hooguit zoveel fd's op één vraag: invoer (1, of 2 bij inbranden) plus uitvoer (subs: 3 per
# spoor, hooguit 8 sporen per run)
MAX_FDS = 32
DETAIL_CHARS = 2000
# De sleutel van hello (PLATFORM_MEDIA_WORKER_KEY): minstens zoveel tekens
KEY_MIN = 32
NONCE = re.compile(r"[0-9a-f]{32}")
_HEADER = struct.Struct(">I")
_INT = array.array("i").itemsize
_UCRED = struct.Struct("3i")


class Op(StrEnum):
    CAPS = "caps"
    BENCH = "bench"
    PROBE = "probe"
    KEYFRAMES = "keyframes"
    AUDIO_INFO = "audio_info"
    NFO = "nfo"
    FRAME = "frame"
    META = "meta"
    SUBS = "subs"
    SPRITES = "sprites"
    STREAM = "stream"
    REMUX_AUDIO = "remux_audio"
    SYNTH = "synth"


class WorkClass(StrEnum):
    """De plaatsen van Keuze 12. CONTROL (caps) telt nooit mee en start geen proces."""

    TRANSCODE = "transcode"
    REMUX = "remux"
    BACKGROUND = "background"
    SHORT = "short"
    CONTROL = "control"


class Code(StrEnum):
    """De codes in {"ev": "error"}. De app maakt er een API-fout van (media.errors)."""

    PROTOCOL = "protocol"  # een andere PROTOCOL, of geen geldige vraag
    TOO_LARGE = "too_large"  # boodschap boven MAX_MESSAGE
    BAD_FDS = "bad_fds"  # te veel, te weinig of verkeerde fd's
    UNKNOWN_OP = "unknown_op"
    BAD_ARGS = "bad_args"  # de argumenten kloppen niet (pydantic, in de worker)
    NOT_ALLOWED = "not_allowed"  # synth zonder PLATFORM_MEDIA_LOADTEST
    NOT_BUILT = "not_built"  # de opdracht hoort bij een pakket dat nog niet klaar is
    BUSY = "busy"  # geen plaats in deze soort, of het totaal aan processen is vol
    FFMPEG_MISSING = "ffmpeg_missing"
    UNKNOWN_FORMAT = "unknown_format"  # geen demuxer uit de witte lijst past
    UNREADABLE = "unreadable"  # ffprobe of ffmpeg kon de bron niet lezen
    FAILED = "failed"  # ffmpeg stopte met een fout
    TIMEOUT = "timeout"
    INTERNAL = "internal"


class WireError(Exception):
    """Een boodschap die niet klopt; code is een Code."""

    def __init__(self, code: Code, detail: str = "") -> None:
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail


class Closed(EOFError):
    """De andere kant sloot de verbinding vóór er een boodschap begon."""


def encode(message: dict) -> bytes:
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()
    if len(data) > MAX_MESSAGE:
        raise WireError(Code.TOO_LARGE, f"boodschap van {len(data)} bytes")
    return _HEADER.pack(len(data)) + data


def error(code: Code, detail: str = "", **extra) -> dict:
    """Een foutantwoord; detail ingekort tot DETAIL_CHARS."""
    out = {"ev": "error", "code": code.value, "detail": detail[-DETAIL_CHARS:]}
    out.update(extra)
    return out


def hello(nonce: str) -> dict:
    """De eerste boodschap van de app: de nonce (32 hex) waarover de worker zijn bewijs geeft."""
    return {"v": PROTOCOL, "hello": nonce}


def proof(key: str, nonce: str, uid: int) -> str:
    """Het bewijs van de worker: HMAC-SHA256 met de sleutel over PROTOCOL, de nonce van de app
    en de uid van de andere kant."""
    text = f"platform-media-worker|{PROTOCOL}|{nonce}|{uid}".encode()
    return hmac.new(key.encode(), text, hashlib.sha256).hexdigest()


def hello_problem(message: dict) -> tuple[Code, str] | None:
    """Wat er niet klopt aan een hello van de app, of None (voor de worker en de nep)."""
    if message.get("v") != PROTOCOL:
        return Code.PROTOCOL, f"de worker spreekt PROTOCOL {PROTOCOL}"
    nonce = message.get("hello")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce) or "op" in message:
        return Code.PROTOCOL, "eerst hello met een nonce van 32 hex, zonder fd's"
    return None


def hello_reply(key: str, nonce: str, uid: int) -> dict:
    return {"ev": "hello", "v": PROTOCOL, "uid": uid, "proof": proof(key, nonce, uid)}


def peer_uid(sock: socket.socket) -> int:
    """De uid van de andere kant van een Unix-socket (SO_PEERCRED: bij connect vastgelegd)."""
    data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
    return _UCRED.unpack(data)[1]


async def _wait(sock: socket.socket, *, write: bool) -> None:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    fd = sock.fileno()

    def ready() -> None:
        if not fut.done():
            fut.set_result(None)

    if write:
        loop.add_writer(fd, ready)
    else:
        loop.add_reader(fd, ready)
    try:
        await fut
    finally:
        if write:
            loop.remove_writer(fd)
        else:
            loop.remove_reader(fd)


async def send(sock: socket.socket, message: dict, fds: Sequence[int] = ()) -> None:
    """Lengte + JSON; fd's als SCM_RIGHTS bij de eerste byte. De fd's blijven van de
    aanroeper (die sluit zijn eigen kopie zelf)."""
    if len(fds) > MAX_FDS:
        raise WireError(Code.BAD_FDS, f"{len(fds)} fd's, hooguit {MAX_FDS}")
    buf = memoryview(encode(message))
    anc = []
    if fds:
        anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds).tobytes())]
    while buf:
        try:
            sent = sock.sendmsg([buf], anc) if anc else sock.send(buf)
        except (BlockingIOError, InterruptedError):
            await _wait(sock, write=True)
            continue
        anc = []  # de fd's gaan mee met het eerste stuk, nooit twee keer
        buf = buf[sent:]


def _take_fds(ancdata, into: list[int]) -> None:
    for level, kind, data in ancdata:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            a = array.array("i")
            a.frombytes(data[: len(data) - (len(data) % _INT)])
            into.extend(a)


async def _recv(sock: socket.socket, n: int, ancsize: int) -> tuple[bytes, list, int]:
    while True:
        try:
            data, anc, flags, _ = sock.recvmsg(n, ancsize, socket.MSG_CMSG_CLOEXEC)
        except (BlockingIOError, InterruptedError):
            await _wait(sock, write=False)
            continue
        return data, anc, flags


async def receive(sock: socket.socket, *, max_fds: int = MAX_FDS) -> tuple[dict, list[int]]:
    """Eén boodschap en de fd's die erbij zaten (al CLOEXEC). Closed als de verbinding dicht
    ging vóór de eerste byte; WireError bij een te grote boodschap, te veel fd's, fd's midden
    in een boodschap, afgekapte data of geen JSON-object. Bij een fout zijn de fd's dicht."""
    fds: list[int] = []
    ancsize = socket.CMSG_SPACE(max_fds * _INT)
    try:
        head = b""
        while len(head) < _HEADER.size:
            data, anc, flags = await _recv(sock, _HEADER.size - len(head), ancsize)
            if anc and head:
                _take_fds(anc, fds)
                raise WireError(Code.BAD_FDS, "fd's midden in een boodschap")
            _take_fds(anc, fds)
            if flags & socket.MSG_CTRUNC:
                raise WireError(Code.BAD_FDS, f"meer dan {max_fds} fd's")
            if not data:
                if head or fds:
                    raise WireError(Code.PROTOCOL, "verbinding dicht midden in een boodschap")
                raise Closed("verbinding dicht")
            head += data
        (length,) = _HEADER.unpack(head)
        if length > MAX_MESSAGE:
            raise WireError(Code.TOO_LARGE, f"boodschap van {length} bytes")
        body = bytearray()
        while len(body) < length:
            data, anc, flags = await _recv(sock, min(length - len(body), 65536), ancsize)
            if anc:
                _take_fds(anc, fds)
                raise WireError(Code.BAD_FDS, "fd's midden in een boodschap")
            if not data:
                raise WireError(Code.PROTOCOL, "verbinding dicht midden in een boodschap")
            body += data
        try:
            message = json.loads(body)
        except ValueError as exc:
            raise WireError(Code.PROTOCOL, "geen JSON") from exc
        if not isinstance(message, dict):
            raise WireError(Code.PROTOCOL, "geen JSON-object")
        return message, fds
    except BaseException:
        close_all(fds)
        raise


def close_all(fds: Sequence[int]) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


@contextlib.contextmanager
def socket_address(path: Path) -> Iterator[str]:
    """Het adres voor bind of connect. Een Unix-socket mag hooguit 107 bytes lang zijn; is het
    pad langer (een diepe tijdelijke map in de tests), dan via /proc/self/fd/<map>/<naam>."""
    text = str(path)
    if len(text.encode()) < 100:
        yield text
        return
    fd = os.open(path.parent, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield f"/proc/self/fd/{fd}/{path.name}"
    finally:
        os.close(fd)
