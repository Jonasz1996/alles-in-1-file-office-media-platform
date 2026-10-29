"""De kern van de mediaworker (bouwontwerp fase 5, Keuze 12; §3.8).

Twee lagen:
- de server in dit proces (Worker uit server.py, zonder ffmpeg): de weigeringen, de plaatsen
  per soort met busy, caps die blijft werken, en stoppen als de verbinding dichtgaat (het kind
  is daarna weg). De trage opdracht is hier een kind `sleep 60` via ctx.spawn;
- de echte lokale worker (marker media, met ffmpeg): caps, de weigeringen over een echte
  socket, busy en stoppen met een nep-ffprobe die slaapt (in PATH vóór de echte), de fd's, de
  grenzen en de omgeving van een kind, en local.stop.

Een kind telt als weg als /proc/<pid> er niet meer is of een zombie is; stoppen gebeurt alleen
op pid (regel 13)."""

from __future__ import annotations

import array
import asyncio
import contextlib
import errno
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.media import client, local, wire
from app.media.client import WorkerBusy, WorkerDown, WorkerFailed
from app.media.wire import Code, Op, WorkClass
from app.media.worker import args as A
from app.media.worker import server
from app.media.worker.server import Config, OpError, Places, Worker

pytestmark = pytest.mark.anyio

CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
KEY = "de-sleutel-van-deze-test-" * 2  # hello (review S1)
PROBE = A.ProbeArgs()
SPRITES = A.SpritesArgs(demuxer="matroska", video_index=0)
FRAME = A.FrameArgs(demuxer="matroska", video_index=0)
SYNTH = A.SynthArgs(source="testsrc2", seed=0, duration_s=30, height=720)
TRANSCODE = A.StreamArgs.model_validate(
    {
        "mode": "transcode",
        "demuxer": "matroska",
        "video_index": 0,
        "video": {
            "codec": "libx264",
            "width": 640,
            "height": 360,
            "level": "3.1",
            "maxrate_k": 900,
        },
        "audio": "none",
        "grid": "2s",
    }
)
REMUX = A.StreamArgs.model_validate(
    {"mode": "remux", "demuxer": "matroska", "video_index": 0, "video": "copy", "audio": "none"}
)


# --- hulpjes ---------------------------------------------------------------------------------


def proc_stat(pid: int) -> tuple[str, int, int] | None:
    """(toestand, ppid, sessie) uit /proc/<pid>/stat, of None als het proces weg is."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    rest = text[text.rindex(")") + 2 :].split()
    return rest[0], int(rest[1]), int(rest[3])


def proc_file(pid: int, name: str) -> bytes:
    return Path(f"/proc/{pid}/{name}").read_bytes()


def alive(pid: int) -> bool:
    st = proc_stat(pid)
    return st is not None and st[0] != "Z"


def children(ppid: int) -> list[int]:
    found = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            st = proc_stat(int(name))
            if st is not None and st[1] == ppid and st[0] != "Z":
                found.append(int(name))
    return found


async def until(cond: Callable[[], bool], limit: float = 5.0) -> bool:
    """Wacht begrensd tot cond waar is."""
    for _ in range(int(limit / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


async def raw_connect(path: Path) -> socket.socket:
    """Een verbinding zonder hello."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.setblocking(False)
    with wire.socket_address(path) as address:
        await asyncio.get_running_loop().sock_connect(sock, address)
    return sock


async def connect(path: Path) -> socket.socket:
    """Een verbinding na hello, met de sleutel waar de client op gebonden is."""
    return await client.connect(path)


async def ask(path: Path, message: dict, fds: list[int] | tuple = ()) -> dict:
    """Eén vraag over een eigen verbinding (na hello); het eerste antwoord."""
    sock = await connect(path)
    try:
        await wire.send(sock, message, fds)
        reply, extra = await wire.receive(sock, max_fds=0)
        wire.close_all(extra)
        return reply
    finally:
        sock.close()


async def ask_raw(path: Path, data: bytes, fds: list[int] | tuple = ()) -> dict:
    sock = await connect(path)
    try:
        if fds:
            anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds).tobytes())]
            sock.sendmsg([data], anc)
        else:
            await asyncio.get_running_loop().sock_sendall(sock, data)
        reply, _ = await wire.receive(sock, max_fds=0)
        return reply
    finally:
        sock.close()


def request(op: Op, args: BaseModel | dict, v: int = wire.PROTOCOL) -> dict:
    body = args.model_dump(mode="json") if isinstance(args, BaseModel) else args
    return {"v": v, "op": op.value, "args": body}


@pytest.fixture
def source(tmp_path) -> Path:
    path = tmp_path / "bron.mkv"
    path.write_bytes(b"\x1aE\xdf\xa3" + b"\0" * 1020)
    return path


@contextlib.contextmanager
def opened(path: Path, flags: int = os.O_RDONLY):
    fd = os.open(path, flags | os.O_CLOEXEC)
    try:
        yield fd
    finally:
        os.close(fd)


# --- de plaatsen (zonder socket) -------------------------------------------------------------


def test_caps_zegt_alleen_lokaal_off_en_anders_unchecked(monkeypatch):
    """Review S6: zonder --local is de worker niet vanzelf afgeschermd; dat zegt pas de
    zelftest (M1). Zonder ffmpeg ook: caps.gather start dan niets."""
    from app.media import status
    from app.media.types import WorkerCaps
    from app.media.worker.ops import caps as caps_op

    monkeypatch.setattr(caps_op, "_out", lambda argv: None)
    local_caps = caps_op.gather(local=True, max_procs=3, max_transcodes=1)
    other = caps_op.gather(local=False, max_procs=3, max_transcodes=1)
    assert (local_caps["mode"], local_caps["sandbox"]) == ("local", "off")
    assert (other["mode"], other["sandbox"]) == ("container", "unchecked")
    assert WorkerCaps.model_validate(other).sandbox == "unchecked"
    assert status.SANDBOX_TEXT[other["sandbox"]] == "afscherming niet gecontroleerd"
    assert status.SANDBOX_TEXT["on"] == "afgeschermd"


def test_places_per_soort_en_totaal():
    places = Places(Config(socket=Path("/x"), max_procs=3, max_transcodes=1).limits(), 3)
    assert places.take(WorkClass.SHORT, 1)
    assert places.take(WorkClass.SHORT, 1)
    assert not places.take(WorkClass.SHORT, 1)  # soort vol
    assert places.take(WorkClass.TRANSCODE, 1)
    assert not places.take(WorkClass.REMUX, 1)  # totaal vol (3 van 3)
    assert places.take(WorkClass.CONTROL, 0)  # caps telt nooit mee
    snap = places.snapshot()
    assert snap["in_use"] == {"transcode": 1, "remux": 0, "background": 0, "short": 2}
    assert snap["places"] == {"transcode": 1, "remux": 6, "background": 1, "short": 2}
    assert snap["procs"] == 3 and snap["max_procs"] == 3
    places.give(WorkClass.SHORT, 1)
    places.give(WorkClass.CONTROL, 0)
    assert places.take(WorkClass.REMUX, 1)
    assert places.snapshot()["procs"] == 3


def test_client_vertaalt_de_fouten():
    with pytest.raises(WorkerBusy) as busy:
        client._raise({"ev": "error", "code": "busy", "class": "background"})
    assert busy.value.work_class == WorkClass.BACKGROUND
    with pytest.raises(WorkerBusy) as busy:
        client._raise({"ev": "error", "code": "busy", "class": "onzin"})
    assert busy.value.work_class == WorkClass.SHORT
    with pytest.raises(WorkerDown):
        client._raise({"ev": "error", "code": "protocol", "detail": "PROTOCOL 2"})
    with pytest.raises(WorkerFailed) as failed:
        client._raise({"ev": "error", "code": "unreadable", "detail": "x" * 5000})
    assert failed.value.code == "unreadable" and len(failed.value.detail) == wire.DETAIL_CHARS


# --- de server in dit proces -----------------------------------------------------------------


@pytest.fixture
async def worker(tmp_path):
    config = Config(socket=tmp_path / "run" / "w.sock", max_procs=6, max_transcodes=2, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        yield w
    finally:
        await w.stop()
        client.bind(None)


@pytest.fixture
def slow(monkeypatch) -> list[int]:
    """Elke opdracht behalve caps start `sleep 60` en wacht erop; geeft de pids."""
    pids: list[int] = []
    real = server.handler

    async def run(ctx: server.Context) -> dict:
        child = ctx.spawn(["sleep", "60"])
        pids.append(child.pid)
        await child.wait()
        return {}

    monkeypatch.setattr(server, "handler", lambda op: real(op) if op == Op.CAPS else run)
    return pids


async def test_socket_0660_en_caps(worker):
    mode = worker.config.socket.stat().st_mode & 0o777
    assert mode == 0o660
    caps = await client.caps()
    assert caps.protocol == wire.PROTOCOL
    assert caps.places == {"transcode": 2, "remux": 6, "background": 1, "short": 2}
    assert caps.in_use == {"transcode": 0, "remux": 0, "background": 0, "short": 0}
    assert caps.procs == 0 and caps.max_procs == 6


async def test_andere_protocol(worker):
    reply = await ask(worker.config.socket, request(Op.CAPS, {}, v=2))
    assert reply["ev"] == "error" and reply["code"] == Code.PROTOCOL


async def test_onbekende_opdracht(worker):
    reply = await ask(worker.config.socket, {"v": 1, "op": "rm", "args": {}})
    assert reply["code"] == Code.UNKNOWN_OP
    reply = await ask(worker.config.socket, {"v": 1, "args": {}})
    assert reply["code"] == Code.UNKNOWN_OP


async def test_verkeerde_argumenten_zonder_waarden(worker, source):
    with opened(source) as fd:
        reply = await ask(worker.config.socket, request(Op.PROBE, {"demuxer": "/etc/x"}), [fd])
    assert reply["code"] == Code.BAD_ARGS
    assert "/etc/x" not in reply["detail"] and "demuxer" in reply["detail"]


async def test_te_grote_boodschap(worker):
    reply = await ask_raw(worker.config.socket, struct.pack(">I", wire.MAX_MESSAGE + 1))
    assert reply["code"] == Code.TOO_LARGE


async def test_te_veel_fds(worker, source):
    with opened(source) as fd:
        data = wire.encode(request(Op.PROBE, PROBE))
        reply = await ask_raw(worker.config.socket, data, [fd] * (wire.MAX_FDS + 1))
    assert reply["code"] == Code.BAD_FDS


async def test_verkeerd_aantal_fds(worker, source):
    path = worker.config.socket
    assert (await ask(path, request(Op.PROBE, PROBE)))["code"] == Code.BAD_FDS
    with opened(source) as fd:
        assert (await ask(path, request(Op.PROBE, PROBE), [fd, fd]))["code"] == Code.BAD_FDS
        # frame vraagt een invoer en een pipe
        assert (await ask(path, request(Op.FRAME, FRAME), [fd]))["code"] == Code.BAD_FDS
        # caps neemt geen fd's
        assert (await ask(path, request(Op.CAPS, {}), [fd]))["code"] == Code.BAD_FDS


async def test_verkeerde_soort_fd_en_de_worker_sluit_hem(worker, source, tmp_path):
    path = worker.config.socket
    r, w = os.pipe2(os.O_CLOEXEC)
    try:
        # een pipe als invoer
        reply = await ask(path, request(Op.PROBE, PROBE), [w])
        assert reply["code"] == Code.BAD_FDS and "gewoon bestand" in reply["detail"]
        os.close(w)
        w = -1
        os.set_blocking(r, False)

        def eof() -> bool:
            try:
                return os.read(r, 1) == b""
            except BlockingIOError:
                return False

        assert await until(eof), "de worker hield zijn kopie van de fd open"
    finally:
        wire.close_all([fd for fd in (r, w) if fd >= 0])
    # een gewoon bestand als uitvoer van frame
    with opened(source) as fd, opened(tmp_path / "uit.jpg", os.O_WRONLY | os.O_CREAT) as out:
        reply = await ask(path, request(Op.FRAME, FRAME), [fd, out])
    assert reply["code"] == Code.BAD_FDS and "pipe" in reply["detail"]


async def test_synth_alleen_met_loadtest(worker, tmp_path):
    with opened(tmp_path / "synth.mkv", os.O_WRONLY | os.O_CREAT) as out:
        reply = await ask(worker.config.socket, request(Op.SYNTH, SYNTH), [out])
    assert reply["code"] == Code.NOT_ALLOWED


async def test_stub_geeft_not_built_en_de_plaats_komt_vrij(worker, source):
    job, (out,) = await client.call(Op.FRAME, FRAME, source=source, outputs=1)
    try:
        with pytest.raises(WorkerFailed) as exc:
            await job.result()
        assert exc.value.code == Code.NOT_BUILT
    finally:
        os.close(out)
    caps = await client.caps()
    assert caps.in_use["short"] == 0 and caps.procs == 0


async def test_symlink_als_bron_gaat_niet_naar_de_worker(worker, source, tmp_path):
    link = tmp_path / "link.mkv"
    link.symlink_to(source)
    with pytest.raises(OSError):
        await client.call(Op.PROBE, PROBE, source=link)


async def test_geen_worker_is_worker_down(tmp_path):
    client.bind(tmp_path / "geen.sock", KEY)
    try:
        with pytest.raises(WorkerDown):
            await client.caps()
    finally:
        client.bind(None)


# --- hello: de worker bewijst zich vóór er een fd naartoe gaat (review S1) -------------------


class Impostor:
    """Iets anders op de socket: neemt verbindingen aan, leest alles (met de fd's erbij) en
    antwoordt op de eerste boodschap met answer(boodschap), of nooit."""

    def __init__(self, path: Path, answer: Callable[[dict], dict] | None):
        self.path = path
        self.answer = answer
        self.messages: list[dict] = []
        self.fds = 0
        self._tasks: set[asyncio.Task] = set()

    async def __aenter__(self) -> Impostor:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        with wire.socket_address(self.path) as address:
            self._listener.bind(address)
        self._listener.listen(8)
        self._listener.setblocking(False)
        self._accept = asyncio.create_task(self._accepting())
        return self

    async def __aexit__(self, *exc) -> None:
        self._accept.cancel()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(self._accept, *self._tasks, return_exceptions=True)
        self._listener.close()
        self.path.unlink(missing_ok=True)

    async def _accepting(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            conn, _ = await loop.sock_accept(self._listener)
            conn.setblocking(False)
            task = asyncio.create_task(self._one(conn))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _one(self, conn: socket.socket) -> None:
        try:
            while True:
                message, fds = await wire.receive(conn)
                self.fds += len(fds)
                wire.close_all(fds)
                self.messages.append(message)
                if self.answer is not None and len(self.messages) == 1:
                    await wire.send(conn, self.answer(message))
        except (wire.Closed, wire.WireError, OSError):
            pass
        finally:
            conn.close()


def _wrong_key(message: dict) -> dict:
    return wire.hello_reply("z" * wire.KEY_MIN, message["hello"], os.geteuid())


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (_wrong_key, "bewijst zich niet"),
        (lambda m: {"ev": "hello", "v": 1, "uid": os.geteuid(), "proof": "0" * 64}, "bewijst"),
        (lambda m: {"ev": "started"}, "geen hello"),  # een worker zonder hello
        (lambda m: {"ev": "hello", "v": 1, "uid": True, "proof": "0" * 64}, "ziet uid"),
        (None, "binnen"),  # zegt niets
    ],
    ids=["andere-sleutel", "nep-bewijs", "geen-hello", "rare-uid", "stil"],
)
async def test_een_bedrieger_op_de_socket_krijgt_geen_fd(
    tmp_path, source, monkeypatch, answer, why
):
    """Wie de socket overneemt (een gekraakt kind van de worker, Keuze 11) zonder de sleutel,
    krijgt alleen hello: de app opent de bron niet eens."""
    monkeypatch.setattr(client, "HELLO_TIMEOUT", 0.3)
    opened_sources: list[Path] = []
    real_open = client._open_source
    monkeypatch.setattr(client, "_open_source", lambda p: opened_sources.append(p) or real_open(p))
    async with Impostor(tmp_path / "w.sock", answer) as impostor:
        client.bind(impostor.path, KEY)
        try:
            with pytest.raises(WorkerDown, match=why):
                await client.call(Op.FRAME, FRAME, source=source, outputs=1)
        finally:
            client.bind(None)
        await asyncio.sleep(0.05)
    assert impostor.fds == 0 and opened_sources == []
    assert len(impostor.messages) == 1 and set(impostor.messages[0]) == {"v", "hello"}


async def test_een_verkeerde_sleutel_of_geen_is_worker_down(worker, source, monkeypatch):
    seen: list[Op] = []
    real = server.handler
    monkeypatch.setattr(server, "handler", lambda op: seen.append(op) or real(op))
    client.bind(worker.config.socket, "y" * 40)
    with pytest.raises(WorkerDown, match="PLATFORM_MEDIA_WORKER_KEY klopt niet"):
        await client.call(Op.PROBE, PROBE, source=source)
    client.bind(worker.config.socket, "kort")
    with pytest.raises(WorkerDown, match="geen sleutel"):
        await client.caps()
    assert seen == []  # de worker kreeg geen vraag
    client.bind(worker.config.socket, KEY)
    assert (await client.caps()).protocol == wire.PROTOCOL


async def test_het_bewijs_hangt_aan_de_uid_die_de_worker_ziet(worker, source, monkeypatch):
    """Een tussenpersoon met een andere uid: de worker bewijst zich voor die uid, en dat
    aanvaardt de app niet (zonder root nagespeeld; de echte staat hieronder)."""
    monkeypatch.setattr(wire, "peer_uid", lambda sock: os.geteuid() + 1)
    with pytest.raises(WorkerDown, match=f"ziet uid {os.geteuid() + 1}"):
        await client.call(Op.PROBE, PROBE, source=source)


async def test_zonder_sleutel_start_de_worker_niet(tmp_path):
    for key in ("", "x" * (wire.KEY_MIN - 1)):
        config = Config(socket=tmp_path / "w.sock", key=key)
        with pytest.raises(ValueError, match="PLATFORM_MEDIA_WORKER_KEY"):
            await Worker(config, dict(CAPS)).start()
        assert not config.socket.exists()
    assert KEY not in repr(Config(socket=tmp_path / "w.sock", key=KEY))


# Een tussenpersoon als een andere uid (nobody): neemt één verbinding aan, verbindt met de
# worker en geeft de bytes door; telt de fd's die de app hem stuurt
RELAY = r"""
import os, select, socket, sys
here, there = sys.argv[1], sys.argv[2]
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
listener.bind(here)
os.chmod(here, 0o666)
listener.listen(1)
print("luistert", flush=True)
a, _ = listener.accept()
b = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
b.connect(there)
fds = 0
done = False
while not done:
    ready, _, _ = select.select([a, b], [], [], 10)
    if not ready:
        break
    for s in ready:
        if s is a:
            data, anc, _, _ = a.recvmsg(65536, socket.CMSG_SPACE(64 * 4))
            fds += sum(len(raw) // 4 for _, _, raw in anc)
            if not data:
                done = True
                break
            b.sendall(data)
        else:
            data = b.recv(65536)
            if not data:
                done = True
                break
            a.sendall(data)
print("fds", fds, flush=True)
"""


@pytest.fixture
def shared_dir():
    """Een map in /tmp waar ook nobody bij kan (tmp_path is 0700); daarna weg."""
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="media-relay-", dir="/tmp"))
    base.chmod(0o777)
    yield base
    shutil.rmtree(base, ignore_errors=True)


# De tussenpersoon draait als nobody: die kan de python van de venv niet starten, wel die van
# het systeem (alleen de standaardbibliotheek)
SYSTEM_PYTHON = "/usr/bin/python3"


@pytest.mark.skipif(os.geteuid() != 0, reason="een andere uid kan alleen als root")
@pytest.mark.skipif(not os.access(SYSTEM_PYTHON, os.X_OK), reason="geen python van het systeem")
async def test_een_tussenpersoon_met_een_andere_uid_krijgt_niets(source, shared_dir):
    """De echte tussenpersoon (review S1): een proces als nobody op een eigen socket dat alles
    doorgeeft. De worker ziet nobody, bewijst zich voor die uid, en de app weigert; de
    tussenpersoon kreeg geen enkele fd. Rechtstreeks werkt dezelfde sleutel wel."""
    base = shared_dir
    config = Config(socket=base / "w.sock", max_procs=3, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    os.chmod(config.socket, 0o666)
    relay = await asyncio.create_subprocess_exec(
        SYSTEM_PYTHON, "-I", "-c", RELAY, str(base / "r.sock"), str(config.socket),
        user=65534, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, env=local.worker_env(),
        cwd="/", start_new_session=True,
    )  # fmt: skip
    try:
        assert relay.stdout is not None
        assert await asyncio.wait_for(relay.stdout.readline(), 10) == b"luistert\n"
        client.bind(base / "r.sock", KEY)
        with pytest.raises(WorkerDown, match="ziet uid 65534"):
            await client.call(Op.PROBE, PROBE, source=source)
        out = await asyncio.wait_for(relay.stdout.read(), 15)
        assert out == b"fds 0\n"
        client.bind(config.socket, KEY)
        assert (await client.caps()).protocol == wire.PROTOCOL
    finally:
        client.bind(None)
        if relay.returncode is None:
            relay.kill()  # op pid
        await relay.wait()
        await w.stop()


async def test_voortgang_resultaat_en_done(worker, monkeypatch):
    async def run(ctx: server.Context) -> dict:
        await ctx.progress(1500, 2.5)
        await ctx.progress(3000, None)
        return {"klaar": True}

    real = server.handler
    monkeypatch.setattr(server, "handler", lambda op: real(op) if op == Op.CAPS else run)
    job, _ = await client.call(Op.BENCH, A.BenchArgs(), source=None, inputs=0)
    events = [e async for e in job.events()]
    assert events == [
        {"ev": "progress", "t_ms": 1500, "speed": 2.5},
        {"ev": "progress", "t_ms": 3000, "speed": None},
        {"ev": "result", "data": {"klaar": True}},
    ]
    assert job.data == {"klaar": True} and job.finished


async def test_fouten_van_een_opdracht(worker, monkeypatch, source):
    async def nette_fout(ctx):
        raise OpError(Code.UNREADABLE, "kapot")

    async def kapot(ctx):
        raise RuntimeError("geheim /pad/naar/iets")

    async def geen_programma(ctx):
        ctx.spawn(["/bestaat/niet/ffmpeg"])

    real = server.handler
    for fn, code in ((nette_fout, Code.UNREADABLE), (kapot, Code.INTERNAL),
                     (geen_programma, Code.FFMPEG_MISSING)):  # fmt: skip
        monkeypatch.setattr(server, "handler", lambda op, fn=fn: real(op) if op == Op.CAPS else fn)
        job, _ = await client.call(Op.PROBE, PROBE, source=source)
        with pytest.raises(WorkerFailed) as exc:
            await job.result()
        assert exc.value.code == code
        assert "/pad" not in exc.value.detail  # een fout in de worker noemt alleen de klasse
    assert (await client.caps()).procs == 0


async def test_tijdgrens_stopt_het_kind(worker, slow, monkeypatch, source):
    monkeypatch.setitem(server.TIMEOUT, Op.PROBE, 0.5)
    job, _ = await client.call(Op.PROBE, PROBE, source=source)
    with pytest.raises(WorkerFailed) as exc:
        await job.result()
    assert exc.value.code == Code.TIMEOUT
    assert len(slow) == 1 and not alive(slow[0])


async def test_bezet_per_soort_terwijl_caps_werkt(worker, slow, source):
    jobs: list[tuple[client.Job, list[int]]] = []

    async def start(op: Op, args: BaseModel, outputs: int = 0) -> None:
        jobs.append(await client.call(op, args, source=source, outputs=outputs))

    async def busy(op: Op, args: BaseModel, outputs: int = 0) -> WorkClass:
        with pytest.raises(WorkerBusy) as exc:
            await client.call(op, args, source=source, outputs=outputs)
        return exc.value.work_class

    try:
        await start(Op.PROBE, PROBE)
        await start(Op.PROBE, PROBE)
        assert await busy(Op.PROBE, PROBE) == WorkClass.SHORT
        await start(Op.SPRITES, SPRITES, 1)
        assert await busy(Op.SPRITES, SPRITES, 1) == WorkClass.BACKGROUND
        await start(Op.STREAM, TRANSCODE, 1)
        await start(Op.STREAM, TRANSCODE, 1)
        assert await busy(Op.STREAM, TRANSCODE, 1) == WorkClass.TRANSCODE
        await start(Op.STREAM, REMUX, 1)  # het zesde proces
        # remux heeft nog 5 plaatsen, maar het totaal (6) is vol
        assert await busy(Op.STREAM, REMUX, 1) == WorkClass.REMUX
        assert await until(lambda: len(slow) == 6 and all(alive(p) for p in slow))
        caps = await client.caps()  # controle: telt niet mee, start niets
        assert caps.in_use == {"transcode": 2, "remux": 1, "background": 1, "short": 2}
        assert caps.procs == 6
    finally:
        for job, reads in jobs:
            await job.cancel()
            wire.close_all(reads)
    assert await until(lambda: not any(alive(p) for p in slow))

    async def free() -> bool:
        caps = await client.caps()
        return caps.procs == 0 and not any(caps.in_use.values())

    for _ in range(100):
        if await free():
            break
        await asyncio.sleep(0.02)
    assert await free()


async def test_verbinding_dicht_is_stoppen(worker, slow, source):
    job, _ = await client.call(Op.PROBE, PROBE, source=source)
    assert await until(lambda: len(slow) == 1)
    pid = slow[0]
    assert alive(pid)
    st = proc_stat(pid)
    assert st is not None and st[2] == pid  # een eigen sessie en procesgroep
    await job.cancel()
    assert await until(lambda: not alive(pid))
    assert await until(lambda: worker.places.procs == 0)


async def test_kind_dat_sigterm_negeert_krijgt_sigkill(worker, monkeypatch, source):
    pids: list[int] = []

    async def stubborn(ctx: server.Context) -> dict:
        child = ctx.spawn(["sh", "-c", "trap '' TERM; exec sleep 60"])
        pids.append(child.pid)
        await child.wait()
        return {}

    real = server.handler
    monkeypatch.setattr(server, "handler", lambda op: real(op) if op == Op.CAPS else stubborn)
    job, _ = await client.call(Op.PROBE, PROBE, source=source)
    assert await until(lambda: len(pids) == 1)
    await asyncio.sleep(0.2)  # sh heeft de trap gezet en sleep gestart
    await job.cancel()
    await asyncio.sleep(0.5)
    assert alive(pids[0])  # SIGTERM doet niets
    assert worker.places.procs == 1  # telt mee tot waitpid
    assert await until(lambda: not alive(pids[0]), limit=5)
    assert await until(lambda: worker.places.procs == 0)


async def test_child_stop_nogmaals_gestopt_wacht_toch_op_het_kind():
    """Een tweede cancel tijdens de genade van SIGTERM: meteen SIGKILL, en pas als het kind weg
    is de CancelledError (anders komt de plaats vrij terwijl het kind nog draait)."""
    from app.media.worker import run as R

    child = R.spawn(["sh", "-c", "trap '' TERM; exec sleep 60"], memory_mb=64)
    await asyncio.sleep(0.2)
    stopping = asyncio.create_task(child.stop())
    await asyncio.sleep(0.3)
    assert alive(child.pid)  # SIGTERM wordt genegeerd
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert not alive(child.pid) and not child.running


async def test_stop_van_de_worker_stopt_elk_kind(tmp_path, slow, source):
    config = Config(socket=tmp_path / "w.sock", max_procs=3, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        job, _ = await client.call(Op.PROBE, PROBE, source=source)
        assert await until(lambda: len(slow) == 1)
        await w.stop()
        assert not alive(slow[0])
        assert not config.socket.exists()
        await job.cancel()
    finally:
        client.bind(None)


async def test_een_andere_fout_bij_aannemen_stopt_de_worker_met_code_1(tmp_path, monkeypatch):
    """Faalt accept met iets anders dan te weinig fd's of geheugen, dan stopt serve (code 1),
    zodat de bewaking de worker opnieuw start; de socket is weg."""
    from app.media.worker import __main__ as main

    async def kapot(self) -> None:
        raise OSError(errno.EBADF, "kapot")

    monkeypatch.setattr(Worker, "_accept", kapot)
    config = Config(socket=tmp_path / "w.sock", max_procs=3, key=KEY)
    async with asyncio.timeout(5):
        assert await main.serve(config, dict(CAPS)) == 1
    assert not config.socket.exists()


# Een worker als los proces met een kleine RLIMIT_NOFILE (S3): de grens gaat aan vóór exec
NOFILE_WRAPPER = (
    "import os,resource,sys;resource.setrlimit(resource.RLIMIT_NOFILE,(64,64));"
    "os.execv(sys.argv[1],sys.argv[1:])"
)


async def test_zonder_fds_neemt_de_worker_daarna_weer_aan(tmp_path):
    """EMFILE bij accept (S3): de worker wacht en neemt daarna weer aan, in plaats van te
    blijven leven zonder ooit nog een verbinding aan te nemen."""
    settings = media_settings(tmp_path)
    path = tmp_path / "run" / "w.sock"
    path.parent.mkdir()
    argv = local.worker_argv(settings, path)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", NOFILE_WRAPPER, *argv,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=local.worker_env(KEY), cwd="/", start_new_session=True,
    )  # fmt: skip
    socks: list[socket.socket] = []
    client.bind(path, KEY)
    try:
        assert await until(path.exists, limit=15), "de worker luistert niet"
        for _ in range(90):  # meer dan de 64 fd's van de worker
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
            sock.setblocking(False)
            try:
                with wire.socket_address(path) as address:
                    sock.connect(address)
            except (BlockingIOError, ConnectionRefusedError):
                sock.close()
                break
            socks.append(sock)
        assert len(socks) > 40
        await asyncio.sleep(0.5)  # de worker loopt tegen EMFILE aan
        for sock in socks:
            sock.close()
        socks.clear()
        caps = None
        async with asyncio.timeout(10):  # de wachtrij loopt leeg (pauze hooguit 1 s)
            while caps is None:
                try:
                    caps = await client.caps()
                except WorkerDown:
                    await asyncio.sleep(0.2)
        assert caps.protocol == wire.PROTOCOL
        assert proc.returncode is None
    finally:
        for sock in socks:
            sock.close()
        client.bind(None)
        if proc.returncode is None:
            proc.send_signal(signal.SIGTERM)  # op pid
        async with asyncio.timeout(10):
            _, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()[-2000:]
    assert b"EMFILE" in err


# Een worker die een kind start en dan zelf hard stopt (S4): het kind gaat mee
ORPHAN = """
import asyncio, sys
sys.path.insert(0, sys.argv[1])
from app.media.worker import run as R

async def main():
    child = R.spawn(["sleep", "60"], memory_mb=64)
    print(child.pid, flush=True)
    await asyncio.sleep(60)

asyncio.run(main())
"""


def test_een_kind_stopt_als_de_worker_hard_stopt():
    """SIGKILL op de worker (of de OOM-killer): zijn kind (een eigen sessie) krijgt SIGKILL via
    PR_SET_PDEATHSIG, in plaats van als wees door te lopen met de fd's van de opdracht."""
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", ORPHAN, str(local.API_ROOT)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, env=local.worker_env(), cwd="/",
        start_new_session=True,
    )  # fmt: skip
    pid = 0
    try:
        assert proc.stdout is not None
        pid = int(proc.stdout.readline())
        st = proc_stat(pid)
        assert st is not None and st[1] == proc.pid and st[2] == pid  # kind, eigen sessie
        os.kill(proc.pid, signal.SIGKILL)  # op pid
        proc.wait(timeout=10)
        for _ in range(250):  # hooguit 5 s
            if not alive(pid):
                break
            time.sleep(0.02)
        assert not alive(pid), "het kind leeft nog als wees"
    finally:
        if pid and alive(pid):
            os.kill(pid, signal.SIGKILL)  # opruimen op pid
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        if proc.stdout is not None:
            proc.stdout.close()


# --- de echte lokale worker (ffmpeg) ---------------------------------------------------------

FAKE_FFPROBE = """#!/bin/sh
# Nep-ffprobe voor de test: op een fd slapen (een trage opdracht), anders de echte
case " $* " in
  *" fd: "*) exec sleep 60 ;;
esac
exec {real} "$@"
"""


def media_settings(tmp_path: Path, **over) -> Settings:
    values = {"media_max_procs": 3, "media_max_transcodes": 1, **over}
    return Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32, **values)


@pytest.fixture
async def real_worker(tmp_path):
    w = await local.start(media_settings(tmp_path), tmp_path / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        yield w
    finally:
        await local.stop(w)
        client.bind(None)
        assert not children(w.pid)


@pytest.fixture
async def sleepy_worker(tmp_path, monkeypatch):
    """Een echte lokale worker met een nep-ffprobe die op de fd slaapt."""
    real = shutil.which("ffprobe")
    assert real, "ffprobe ontbreekt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ffprobe"
    fake.write_text(FAKE_FFPROBE.format(real=real))
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}")
    w = await local.start(media_settings(tmp_path), tmp_path / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        yield w
    finally:
        await local.stop(w)
        client.bind(None)


@pytest.mark.media
async def test_echte_worker_caps(real_worker):
    caps = await client.caps()
    assert caps.protocol == wire.PROTOCOL
    assert caps.mode == "local" and caps.sandbox == "off"
    assert caps.ffmpeg and caps.ffprobe
    assert caps.missing == []
    assert set(A.DEMUXERS) <= set(caps.demuxers)
    assert "lavfi" in caps.demuxers  # uit -devices (spike punt 7)
    assert {"fd", "pipe"} <= set(caps.protocols)
    assert {"libx264", "libvpx-vp9", "aac", "libopus"} <= set(caps.encoders)
    assert caps.places == {"transcode": 1, "remux": 6, "background": 1, "short": 2}
    assert caps.max_procs == 3 and caps.procs == 0
    mode = real_worker.socket.stat().st_mode & 0o777
    assert mode == 0o660
    # de sleutel gaat via de omgeving, nooit op de opdrachtregel (die is voor iedereen te lezen)
    assert real_worker.key and real_worker.key.encode() not in proc_file(real_worker.pid, "cmdline")


@pytest.mark.media
async def test_echte_worker_weigert(real_worker, source):
    path = real_worker.socket
    assert (await ask(path, request(Op.CAPS, {}, v=2)))["code"] == Code.PROTOCOL
    assert (await ask(path, {"v": 1, "op": "shell", "args": {}}))["code"] == Code.UNKNOWN_OP
    big = struct.pack(">I", wire.MAX_MESSAGE + 1)
    assert (await ask_raw(path, big))["code"] == Code.TOO_LARGE
    assert (await ask(path, request(Op.PROBE, PROBE)))["code"] == Code.BAD_FDS
    with opened(source) as fd:
        data = wire.encode(request(Op.PROBE, PROBE))
        many = await ask_raw(path, data, [fd] * (wire.MAX_FDS + 1))
        assert many["code"] == Code.BAD_FDS
    # de worker leeft nog, en heeft niets meer lopen
    caps = await client.caps()
    assert caps.procs == 0
    assert not children(real_worker.pid)


@pytest.mark.media
async def test_echte_worker_probe_en_geen_proces_over(real_worker):
    from tests.media.fixtures import make

    probe = await client.probe(make("h264_aac.mp4"))
    assert probe.state == "ready" and probe.container == "mp4"
    assert probe.video() is not None and probe.video().codec == "h264"
    assert not children(real_worker.pid)
    assert (await client.caps()).procs == 0


@pytest.mark.media
async def test_echte_worker_bezet_en_stoppen(sleepy_worker, source):
    jobs = []
    try:
        jobs.append(await client.call(Op.PROBE, PROBE, source=source))
        jobs.append(await client.call(Op.PROBE, PROBE, source=source))
        with pytest.raises(WorkerBusy) as exc:
            await client.call(Op.PROBE, PROBE, source=source)
        assert exc.value.work_class == WorkClass.SHORT
        assert await until(lambda: len(children(sleepy_worker.pid)) == 2)
        caps = await client.caps()  # werkt, ook nu kort vol is
        assert caps.in_use["short"] == 2 and caps.procs == 2
        first, second = children(sleepy_worker.pid)
        # stoppen = de verbinding dicht; het kind gaat weg, het andere blijft
        await jobs[0][0].cancel()
        assert await until(lambda: len(children(sleepy_worker.pid)) == 1)
        await jobs[1][0].cancel()
        assert await until(lambda: not children(sleepy_worker.pid))
        assert not alive(first) and not alive(second)
        caps = await client.caps()
        assert caps.procs == 0 and caps.in_use["short"] == 0
    finally:
        for job, _ in jobs:
            await job.cancel()


@pytest.mark.media
async def test_echte_worker_kind_fds_grenzen_en_omgeving(sleepy_worker, source):
    job, _ = await client.call(Op.PROBE, PROBE, source=source)
    try:
        assert await until(lambda: len(children(sleepy_worker.pid)) == 1)
        (pid,) = children(sleepy_worker.pid)
        await asyncio.sleep(0.1)  # sh heeft sleep gestart (exec: dezelfde pid)
        fds = sorted(int(n) for n in os.listdir(f"/proc/{pid}/fd"))
        assert fds == [0, 1, 2, 3]  # alleen de bron op fd 3; de rest is CLOEXEC
        assert os.readlink(f"/proc/{pid}/fd/3") == str(source.resolve())
        assert os.readlink(f"/proc/{pid}/cwd") == "/"
        env = proc_file(pid, "environ").split(b"\0")
        names = {e.split(b"=", 1)[0].decode() for e in env if e}
        assert names - {"PWD"} == {"HOME", "LC_ALL", "PATH"}  # PWD zet de sh van de nep zelf
        limits = {}
        for line in proc_file(pid, "limits").decode().splitlines()[1:]:
            name, soft = line[:26].strip(), line[26:47].split()[0]
            limits[name] = soft
        assert limits["Max file size"] == "0"  # RLIMIT_FSIZE 0
        assert limits["Max core file size"] == "0"
        assert limits["Max data size"] == str(A.SHORT_MEMORY_MB * 1024 * 1024)
        st = proc_stat(pid)
        assert st is not None and st[2] == pid  # eigen sessie
    finally:
        await job.cancel()
    assert await until(lambda: not children(sleepy_worker.pid))


@pytest.mark.media
async def test_local_stop_stopt_ook_het_kind(tmp_path, sleepy_worker, source):
    job, _ = await client.call(Op.PROBE, PROBE, source=source)
    assert await until(lambda: len(children(sleepy_worker.pid)) == 1)
    (pid,) = children(sleepy_worker.pid)
    await local.stop(sleepy_worker)
    assert sleepy_worker.process.returncode == 0
    assert not sleepy_worker.socket.exists()
    assert await until(lambda: not alive(pid), limit=2)
    await job.cancel()


@pytest.mark.media
def test_check_en_selftest():
    argv = [sys.executable, "-I", "-c", local.BOOT, str(local.API_ROOT)]
    env = local.worker_env()
    done = subprocess.run(
        [*argv, "--check"], capture_output=True, timeout=60, env=env, cwd="/", check=False
    )
    assert done.returncode == 0, done.stderr[-500:]
    caps = json.loads(done.stdout)
    assert caps["missing"] == [] and caps["protocol"] == wire.PROTOCOL
    assert caps["mode"] == "container" and caps["sandbox"] == "unchecked"  # geen --local
    done = subprocess.run(
        [*argv, "--selftest"], capture_output=True, timeout=60, env=env, cwd="/", check=False
    )
    assert done.returncode == 2  # M1 bouwt de zelftest
    # luisteren zonder sleutel: niet (review S1)
    done = subprocess.run(
        [*argv, "--socket", "/nergens/w.sock"], capture_output=True, timeout=60, env=env,
        cwd="/", check=False,
    )  # fmt: skip
    assert done.returncode == 2 and b"PLATFORM_MEDIA_WORKER_KEY" in done.stderr


@pytest.mark.media
async def test_worker_stopt_op_sigterm_zonder_kinderen(tmp_path):
    w = await local.start(media_settings(tmp_path), tmp_path / "w.sock")
    try:
        os.kill(w.pid, signal.SIGTERM)
        async with asyncio.timeout(10):
            await w.process.wait()
        assert w.process.returncode == 0
        assert not w.socket.exists()
    finally:
        await local.stop(w)


# --- runtime: uit is uit, aan start de lokale worker -----------------------------------------


class FakeApp:
    def __init__(self):
        self.state = type("State", (), {})()


async def test_runtime_uit_start_niets(tmp_path):
    from app.media import runtime

    app = FakeApp()
    state = await runtime.start(app, media_settings(tmp_path))
    try:
        assert state.enabled is False and not runtime.enabled(app)
        assert getattr(app.state, "media_local", None) is None
        assert getattr(app.state, "media_health", None) is None
        assert not (tmp_path / "media").exists()  # geen map, geen socket
    finally:
        await runtime.stop(app)


async def test_runtime_container_zonder_worker_is_down(tmp_path, monkeypatch):
    from app.media import health, runtime

    monkeypatch.setattr(health, "FIRST", 0.01)
    app = FakeApp()
    settings = media_settings(tmp_path, media_enabled=True, media_worker=str(tmp_path / "x.sock"))
    state = await runtime.start(app, settings)
    try:
        assert state.mode == "container" and runtime.enabled(app)
        assert getattr(app.state, "media_local", None) is None  # nooit stil terug op lokaal
        assert await until(lambda: state.worker == "down")
        assert not state.healthy
        assert "PLATFORM_MEDIA_WORKER_KEY" in (state.reason or "")  # review S1
    finally:
        await runtime.stop(app)


@pytest.mark.media
async def test_runtime_aan_start_en_stopt_de_lokale_worker(tmp_path, monkeypatch):
    from app.media import health, runtime

    monkeypatch.setattr(health, "FIRST", 0.05)
    app = FakeApp()
    state = await runtime.start(app, media_settings(tmp_path, media_enabled=True))
    worker = app.state.media_local
    try:
        assert state.mode == "local"
        assert await until(lambda: state.worker == "ok", limit=10), state.reason
        assert state.healthy and state.caps is not None and state.caps.missing == []
        assert worker.socket == tmp_path / "media" / "run" / "worker.sock"
    finally:
        await runtime.stop(app)
    assert worker.process.returncode is not None
    assert not worker.socket.exists()


async def test_runtime_start_en_stopt_de_lus_van_de_scanner(tmp_path, monkeypatch):
    """Review K1: de haak voor M2. Met media aan roept runtime.start scanner.start één keer aan
    (met de instellingen, voor media_scan_tick), en stop geeft zijn handvat aan scanner.stop;
    met media uit gebeurt geen van beide. Een echte lus (zoals M2 hem maakt) stopt dan ook."""
    from app.media import health, runtime, scanner

    monkeypatch.setattr(health, "FIRST", 60)  # geen caps in deze test
    calls: list[tuple] = []
    ticks: list[float] = []

    def start(app, settings):
        async def loop() -> None:
            while True:
                ticks.append(settings.media_scan_tick)
                await asyncio.sleep(settings.media_scan_tick)

        task = asyncio.create_task(loop())
        calls.append(("start", app, settings))
        return task

    real_stop = scanner.stop

    async def stop(handle):
        calls.append(("stop", handle))
        await real_stop(handle)

    monkeypatch.setattr(scanner, "start", start)
    monkeypatch.setattr(scanner, "stop", stop)
    off = FakeApp()
    await runtime.start(off, media_settings(tmp_path))
    await runtime.stop(off)
    assert calls == [("stop", None)]  # niets gestart; stop met None is niets
    calls.clear()

    app = FakeApp()
    settings = media_settings(
        tmp_path, media_enabled=True, media_worker=str(tmp_path / "x.sock"), media_scan_tick=0.05,
        media_worker_key="s" * 40,
    )  # fmt: skip
    await runtime.start(app, settings)
    task = app.state.media_scanner
    try:
        assert calls == [("start", app, settings)]
        assert await until(lambda: len(ticks) >= 3)
    finally:
        await runtime.stop(app)
    assert calls[1] == ("stop", task) and task.cancelled()
    assert app.state.media_scanner is None


async def test_de_stub_van_de_scanner_start_niets():
    from app.media import scanner

    assert scanner.start(FakeApp(), None) is None
    await scanner.stop(None)
