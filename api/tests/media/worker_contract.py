"""De contracttests van de mediaworker (§3.6): scenario's die tegen de nep-worker én tegen de
echte (lokale) worker draaien. Zo blijft de nep eerlijk.

Dit bestand wordt niet zelf verzameld (het heet niet test_*.py). test_fake_worker.py draait elk
scenario uit SCENARIOS met een Driver per worker:

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
    async def test_contract_nep(scenario, fake_driver):
        await scenario(fake_driver)

- de nep (`fake_worker.FakeWorker`): altijd;
- de echte lokale worker (marker `media`): voor wat er gebouwd is. Een scenario dat een opdracht
  nodig heeft die de worker nog niet doet (`needs`, zoals stream: M4), slaat voor hem over. Als
  M4 stream bouwt, zet hij `Op.STREAM` in `has` van de echte Driver, en dan moeten dezelfde
  scenario's slagen.

Afspraken voor elke Driver:
- `socket`: waar de worker luistert; `client.bind` staat erop;
- `has`: de opdrachten die deze worker echt doet;
- `max_procs` en `max_transcodes`: zoals de worker gestart is;
- `processes()`: de ffmpeg- en ffprobe-processen van de worker nu (bij de nep: de opdrachten die
  er een zouden hebben). Na stoppen 0: er blijft geen proces over;
- `slow()`: elke probe vanaf nu blijft hangen tot de verbinding dichtgaat (bij de echte: een
  nep-ffprobe die op `fd:` slaapt); `fast()` zet dat terug;
- `source(name)`: het pad van een fixture (`tests/media/fixtures.py`).

Het rooster (Keuze 22, spike punten 10 en 12): de stroom komt uit `stream_src.mkv` (H.264 +
AAC, 20 s, S = −0,021). Fragment k begint op mediatijd `origin + 2k` (fragment 0 op `origin`),
binnen één beeld, met een keyframe; `tfdt` = mediatijd + 1 s; het aantal komt uit het laatste
videobeeld (`SegmentGrid.count_for`), en een fragment voorbij N hoort bij het laatste segment.
Een herstart met `-ss (origin + 2k)` geeft dezelfde tijden en dezelfde `stsd` (spike S5).
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import os
import socket
import struct
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.media import client, wire
from app.media.client import WorkerBusy, WorkerFailed
from app.media.types import SegmentGrid
from app.media.wire import Code, Op, WorkClass
from app.media.worker import args as A
from tests.media import boxes

FRAME_MS = 42  # één beeld bij 24 fps, naar boven
SOURCE = "stream_src.mkv"


class WorkerDriver(Protocol):
    name: str
    socket: Path
    has: frozenset[Op]
    max_procs: int
    max_transcodes: int

    def processes(self) -> int: ...

    async def slow(self) -> None: ...

    async def fast(self) -> None: ...

    def source(self, name: str) -> Path: ...


Scenario = Callable[[WorkerDriver], Awaitable[None]]


def needs(*ops: Op) -> Callable[[Scenario], Scenario]:
    def mark(fn: Scenario) -> Scenario:
        fn.needs = frozenset(ops)  # type: ignore[attr-defined]
        return fn

    return mark


def required(scenario: Scenario) -> frozenset[Op]:
    return getattr(scenario, "needs", frozenset())


# --- hulpjes ---------------------------------------------------------------------------------


async def until(cond: Callable[[], bool], limit: float = 5.0) -> bool:
    for _ in range(int(limit / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


async def _connect(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.setblocking(False)
    with wire.socket_address(path) as address:
        await asyncio.get_running_loop().sock_connect(sock, address)
    return sock


async def ask(path: Path, message: dict, fds: list[int] | tuple = ()) -> dict:
    """Eén vraag over een eigen verbinding; het eerste antwoord."""
    sock = await _connect(path)
    try:
        await wire.send(sock, message, fds)
        reply, extra = await wire.receive(sock, max_fds=0)
        wire.close_all(extra)
        return reply
    finally:
        sock.close()


async def ask_raw(path: Path, data: bytes, fds: list[int] | tuple = ()) -> dict:
    sock = await _connect(path)
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


@contextlib.contextmanager
def opened(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        yield fd
    finally:
        os.close(fd)


async def read_pipe(fd: int) -> bytes:
    """Alles van een leeskant (asyncio), tot EOF; sluit hem."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=1 << 26)
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(fd, "rb", 0)
    )
    try:
        return await reader.read()
    finally:
        transport.close()


async def free(d: WorkerDriver, limit: float = 5.0) -> None:
    """Geen proces over, en de worker telt niets meer (caps)."""
    assert await until(lambda: d.processes() == 0, limit), f"nog {d.processes()} processen"
    for _ in range(int(limit / 0.05)):
        caps = await client.caps()
        if caps.procs == 0 and not any(caps.in_use.values()):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"de worker telt nog {caps.procs} processen, {caps.in_use}")


def transcode(codec: str, start_s: float, source_start_s: float) -> A.StreamArgs:
    video: dict = {"codec": codec, "width": 640, "height": 360, "maxrate_k": 1000}
    audio: dict = {"codec": "aac"}
    if codec == "libx264":
        video["level"] = "3.0"
    else:
        audio = {"codec": "libopus", "bitrate_k": 128}
    return A.StreamArgs.model_validate(
        {
            "mode": "transcode",
            "demuxer": "matroska",
            "start_s": start_s,
            "source_start_s": source_start_s,
            "video_index": 0,
            "audio_index": 1,
            "video": video,
            "audio": audio,
            "grid": "2s",
        }
    )


async def grid_of(d: WorkerDriver) -> tuple[SegmentGrid, float]:
    """Het rooster van de bron uit de probe, en S in seconden."""
    probe = await client.probe(d.source(SOURCE))
    video = probe.video()
    assert video is not None and video.start_ms is not None and video.last_frame_ms is not None
    origin = video.start_ms
    grid = SegmentGrid(
        origin_ms=origin,
        count=SegmentGrid.count_for(origin, video.last_frame_ms),
        last_frame_ms=video.last_frame_ms,
    )
    return grid, probe.start_ms / 1000


async def run_stream(d: WorkerDriver, args: A.StreamArgs) -> tuple[boxes.Parsed, list[dict]]:
    """Eén stream van begin tot eind: de uitvoer en de antwoorden na started."""
    job, (out,) = await client.call(Op.STREAM, args, source=d.source(SOURCE), outputs=1)
    async with job:
        reading = asyncio.create_task(read_pipe(out))
        events = [e async for e in job.events()]
        data = await reading
    return boxes.parse(data), events


def on_grid(parsed: boxes.Parsed, grid: SegmentGrid, first: int) -> None:
    """Elk fragment begint met een keyframe op het rooster, vanaf segment `first`."""
    starts = [f.video_start_ms for f in parsed.fragments]
    assert starts == sorted(starts), starts
    assert all(f.sync for f in parsed.fragments), "een fragment zonder keyframe vooraan"
    for i, frag in enumerate(parsed.fragments):
        k = first + i
        want = grid.origin_ms if k == 0 else grid.start_of(k)
        if k < grid.count:
            assert abs(frag.video_start_ms - want) <= FRAME_MS, (k, frag.video_start_ms, want)
            assert grid.segment_of(frag.video_start_ms) == k
        else:  # voorbij N (spike punt 10): hoort bij het laatste segment
            assert grid.segment_of(frag.video_start_ms) == grid.count - 1


# --- de scenario's ---------------------------------------------------------------------------


async def caps_vorm(d: WorkerDriver) -> None:
    caps = await client.caps()
    assert caps.protocol == wire.PROTOCOL
    assert caps.ffmpeg and caps.ffprobe and caps.missing == []
    assert set(A.DEMUXERS) <= set(caps.demuxers) and "lavfi" in caps.demuxers
    assert {"fd", "pipe"} <= set(caps.protocols)
    assert {"libx264", "libvpx-vp9", "aac", "libopus"} <= set(caps.encoders)
    assert caps.places == {
        "transcode": d.max_transcodes,
        "remux": 6,
        "background": 1,
        "short": 2,
    }
    assert caps.in_use == dict.fromkeys(caps.places, 0)
    assert caps.procs == 0 and caps.max_procs == d.max_procs
    assert d.socket.stat().st_mode & 0o777 == 0o660


async def probe_vorm(d: WorkerDriver) -> None:
    probe = await client.probe(d.source("h264_aac.mp4"))
    assert probe.state == "ready" and probe.error is None and probe.container == "mp4"
    video = probe.video()
    assert (
        video is not None
        and video.codec == "h264"
        and (video.width, video.height)
        == (
            1280,
            720,
        )
    )
    assert video.codec_string and video.codec_string.startswith("avc1.64")
    assert video.fps == 24
    assert [s.codec for s in probe.audio_streams()] == ["aac"]
    assert probe.duration_ms is not None and abs(probe.duration_ms - 10000) <= 60
    assert video.last_frame_ms is not None and 0 < video.last_frame_ms <= probe.duration_ms
    assert probe.truncated == {}
    # Mediatijd en het rooster (spike punt 12): de video begint laat, dus origin ±521 ms
    late = (await client.probe(d.source("late_video.mkv"))).video()
    assert late is not None and late.start_ms is not None and abs(late.start_ms - 521) <= 30
    # Nette fouten, geen probe
    for name, codes in (("random.mkv", {Code.UNKNOWN_FORMAT, Code.UNREADABLE}),
                        ("empty.mkv", {Code.UNREADABLE})):  # fmt: skip
        try:
            await client.probe(d.source(name))
        except WorkerFailed as exc:
            assert exc.code in codes, (name, exc.code)
        else:
            raise AssertionError(f"{name} gaf een probe")
    await free(d)


async def begin_tot_eind(d: WorkerDriver) -> None:
    """Op één verbinding: started, result, done, en dan sluit de worker."""
    sock = await _connect(d.socket)
    try:
        with opened(d.source("noaudio.mp4")) as fd:
            await wire.send(sock, request(Op.PROBE, A.ProbeArgs()), [fd])
        events = []
        while True:
            try:
                message, extra = await asyncio.wait_for(wire.receive(sock, max_fds=0), 30)
            except wire.Closed:
                break
            wire.close_all(extra)
            events.append(message["ev"])
    finally:
        sock.close()
    assert events == ["started", "result", "done"], events
    await free(d)


async def dicht_is_stoppen(d: WorkerDriver) -> None:
    await d.slow()
    try:
        job, _ = await client.call(Op.PROBE, A.ProbeArgs(), source=d.source("h264_aac.mp4"))
        assert await until(lambda: d.processes() == 1), d.processes()
        await asyncio.sleep(0.2)
        assert d.processes() == 1  # het hangt echt
        await job.cancel()
        await free(d)
    finally:
        await d.fast()


async def bezet_per_soort(d: WorkerDriver) -> None:
    """Kort heeft 2 plaatsen: de derde is busy met de soort, en caps werkt dan nog."""
    await d.slow()
    jobs: list[client.Job] = []
    try:
        for _ in range(2):
            job, _ = await client.call(Op.PROBE, A.ProbeArgs(), source=d.source("h264_aac.mp4"))
            jobs.append(job)
        try:
            await client.call(Op.PROBE, A.ProbeArgs(), source=d.source("h264_aac.mp4"))
        except WorkerBusy as exc:
            assert exc.work_class == WorkClass.SHORT
        else:
            raise AssertionError("de derde korte opdracht was niet busy")
        assert await until(lambda: d.processes() == 2)
        caps = await client.caps()
        assert caps.in_use["short"] == 2 and caps.procs == 2
    finally:
        for job in jobs:
            await job.cancel()
        await d.fast()
    await free(d)


async def andere_protocol(d: WorkerDriver) -> None:
    reply = await ask(d.socket, request(Op.CAPS, {}, v=wire.PROTOCOL + 1))
    assert reply["ev"] == "error" and reply["code"] == Code.PROTOCOL
    reply = await ask(d.socket, {"op": "caps", "args": {}})
    assert reply["code"] == Code.PROTOCOL


async def te_grote_boodschap(d: WorkerDriver) -> None:
    reply = await ask_raw(d.socket, struct.pack(">I", wire.MAX_MESSAGE + 1))
    assert reply["ev"] == "error" and reply["code"] == Code.TOO_LARGE
    assert (await client.caps()).protocol == wire.PROTOCOL  # de worker leeft nog


async def onbekende_opdracht(d: WorkerDriver) -> None:
    for message in ({"v": 1, "op": "shell", "args": {}}, {"v": 1, "args": {}}):
        reply = await ask(d.socket, message)
        assert reply["ev"] == "error" and reply["code"] == Code.UNKNOWN_OP


async def verkeerde_argumenten(d: WorkerDriver) -> None:
    with opened(d.source("h264_aac.mp4")) as fd:
        reply = await ask(d.socket, request(Op.PROBE, {"demuxer": "/etc/passwd"}), [fd])
    assert reply["code"] == Code.BAD_ARGS
    assert "/etc/passwd" not in reply["detail"]  # nooit de waarde terug


async def verkeerd_aantal_fds(d: WorkerDriver) -> None:
    path = d.socket
    probe = request(Op.PROBE, A.ProbeArgs())
    assert (await ask(path, probe))["code"] == Code.BAD_FDS
    with opened(d.source("h264_aac.mp4")) as fd:
        assert (await ask(path, probe, [fd, fd]))["code"] == Code.BAD_FDS
        assert (await ask(path, request(Op.CAPS, {}), [fd]))["code"] == Code.BAD_FDS
        many = await ask_raw(path, wire.encode(probe), [fd] * (wire.MAX_FDS + 1))
        assert many["code"] == Code.BAD_FDS
    r, w = os.pipe2(os.O_CLOEXEC)
    try:  # een pipe als invoer
        assert (await ask(path, probe, [r]))["code"] == Code.BAD_FDS
    finally:
        wire.close_all([r, w])
    await free(d)


@needs(Op.STREAM)
async def stroom_op_rooster(d: WorkerDriver) -> None:
    """Transcode vanaf 0 en na een herstart op segment 3: dezelfde tijden en dezelfde stsd."""
    grid, start = await grid_of(d)
    assert grid.count == 10  # 20 s op 24 fps, origin 21: het laatste beeld op 19,979 s
    for codec, mime in (("libx264", "avc1.64"), ("libvpx-vp9", "vp09.")):
        whole, events = await run_stream(d, transcode(codec, 0, start))
        assert mime in whole.mime()
        on_grid(whole, grid, 0)
        assert len(whole.fragments) >= grid.count
        assert whole.fragments[0].video_start_ms == grid.origin_ms
        assert whole.fragments[-1].video_last_ms == grid.last_frame_ms
        times = [e["t_ms"] for e in events if e["ev"] == "progress"]
        assert times == sorted(times) and events[-1]["ev"] == "result"
        k = 3
        again, _ = await run_stream(d, transcode(codec, grid.start_of(k) / 1000, start))
        on_grid(again, grid, k)
        assert len(again.fragments) == len(whole.fragments) - k
        assert again.stsd() == whole.stsd()  # dezelfde generatie (Keuze 22, S5)
    await free(d)


@needs(Op.STREAM)
async def tegendruk(d: WorkerDriver) -> None:
    """Wie niet leest, houdt de stroom op: de worker schrijft niet verder dan de pipe houdt."""
    grid, start = await grid_of(d)
    job, (out,) = await client.call(
        Op.STREAM, transcode("libx264", 0, start), source=d.source(SOURCE), outputs=1
    )
    async with job:
        seen: list[dict] = []

        async def collect() -> None:
            async for event in job.events():
                seen.append(event)

        events = asyncio.create_task(collect())
        await asyncio.sleep(1.5)
        assert not events.done(), "de stroom was klaar terwijl niemand las"
        progress = [e["t_ms"] for e in seen if e["ev"] == "progress"]
        assert not progress or max(progress) < grid.start_of(grid.count - 2), progress
        started = time.monotonic()
        data = await read_pipe(out)
        await asyncio.wait_for(events, 60)
    assert len(boxes.parse(data).fragments) >= grid.count
    assert time.monotonic() - started < 60
    await free(d)


@needs(Op.STREAM)
async def stroom_dicht_is_stoppen(d: WorkerDriver) -> None:
    """De verbinding dicht stopt de stroom; de app sluit zijn leeskant zelf (spike punt 4)."""
    _grid, start = await grid_of(d)
    job, (out,) = await client.call(
        Op.STREAM, transcode("libx264", 0, start), source=d.source(SOURCE), outputs=1
    )
    try:
        assert await until(lambda: d.processes() == 1)
        await job.cancel()
    finally:
        os.close(out)
    await free(d)


SCENARIOS: list[Scenario] = [
    caps_vorm,
    probe_vorm,
    begin_tot_eind,
    dicht_is_stoppen,
    bezet_per_soort,
    andere_protocol,
    te_grote_boodschap,
    onbekende_opdracht,
    verkeerde_argumenten,
    verkeerd_aantal_fds,
    stroom_op_rooster,
    tegendruk,
    stroom_dicht_is_stoppen,
]
