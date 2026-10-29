"""De bewaking van de mediaworker (bouwontwerp fase 5, M1; Keuze 12).

De server draait in dit proces (Worker uit server.py) met de echte opdrachten frame, meta en
bench, maar met een nep-ffmpeg vooraan in PATH: een klein script dat slaapt, geheugen vraagt of
een kleinkind start. Zo zijn de grenzen, het stoppen en de plaatsen te zien zonder echte
video. De tests met marker media gebruiken de echte ffmpeg (de zelftest, een malloc boven
RLIMIT_DATA, de kale omgeving in de lokale worker).

Een kind telt als weg als /proc/<pid> er niet meer is of een zombie is; stoppen alleen op pid
(regel 13).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import signal
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app import render, render_meta
from app.config import Settings
from app.media import client, local, wire
from app.media.client import WorkerBusy, WorkerFailed
from app.media.wire import Code, Op, WorkClass
from app.media.worker import args as A
from app.media.worker import guard, selftest, server
from app.media.worker.ops import bench, frame, meta
from app.media.worker.server import Config, Worker

pytestmark = pytest.mark.anyio

CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
KEY = "de-sleutel-van-de-bewaking-" * 2
FRAME = A.FrameArgs(demuxer="matroska", video_index=0)
PY = f"#!{sys.executable} -IB\n"  # -B: geen .pyc (RLIMIT_FSIZE is 0)

SLEEPER = PY + "import time\ntime.sleep(60)\n"
STUBBORN = (
    PY + "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n"
)
# Gedeeld geheugen telt niet in RLIMIT_DATA: dat vangt de meting
HOG = PY + (
    "import mmap, sys, time\n"
    "size = {mb} * 1024 * 1024\n"
    "m = mmap.mmap(-1, size, flags=mmap.MAP_SHARED)\n"
    "chunk = b'x' * (1024 * 1024)\n"
    "for at in range(0, size, len(chunk)):\n"
    "    m[at:at + len(chunk)] = chunk\n"
    "sys.stderr.write('vol\\n')\n"
    "sys.stderr.flush()\n"
    "time.sleep(60)\n"
)
# Gewoon geheugen (malloc) boven RLIMIT_DATA: MemoryError, het kind stopt met een fout
GREEDY = PY + "data = bytearray({mb} * 1024 * 1024)\n"
# Een kind met een kleinkind (schrijven in een bestand kan een kind niet: RLIMIT_FSIZE 0)
FAMILY = """#!/bin/sh
sleep 60 &
wait
"""


# --- hulpjes ---------------------------------------------------------------------------------


def proc_stat(pid: int) -> list[str] | None:
    """De velden van /proc/<pid>/stat vanaf de toestand (veld 3), of None als hij weg is."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return text[text.rindex(")") + 2 :].split()


def alive(pid: int) -> bool:
    st = proc_stat(pid)
    return st is not None and st[0] not in ("Z", "X")


def limits(pid: int) -> dict[str, tuple[str, str]]:
    found = {}
    for line in Path(f"/proc/{pid}/limits").read_text().splitlines()[1:]:
        name, soft, hard = line[:26].strip(), *line[26:].split()[:2]
        found[name] = (soft, hard)
    return found


def environ(pid: int) -> bytes:
    return Path(f"/proc/{pid}/environ").read_bytes()


def ionice(pid: int) -> str:
    return subprocess.run(["ionice", "-p", str(pid)], capture_output=True, text=True).stdout


def which(name: str) -> str:
    found = subprocess.run(["sh", "-c", f"command -v {name}"], capture_output=True, text=True)
    return found.stdout.strip()


def ffmpegs(parent: int | None = None) -> list[int]:
    """De kinderen die ffmpeg heten (de nep), van dit proces of van parent."""
    return [p for p, comm in guard.children(parent or os.getpid()) if comm == "ffmpeg"]


async def until(cond: Callable[[], bool], limit: float = 5.0) -> bool:
    for _ in range(int(limit / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


def fake(tmp_path: Path, monkeypatch, script: str, name: str = "ffmpeg") -> Path:
    """Een nep-programma vooraan in PATH (de worker en zijn kinderen nemen PATH over)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text(script)
    path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}")
    return path


@pytest.fixture
def source(tmp_path) -> Path:
    path = tmp_path / "bron.mkv"
    path.write_bytes(b"\x1aE\xdf\xa3" + b"\0" * 1020)
    return path


@contextlib.asynccontextmanager
async def serving(tmp_path: Path, *, max_procs: int = 6, memory_mb: int = 1200, watch=None):
    config = Config(
        socket=tmp_path / "run" / "w.sock",
        max_procs=max_procs,
        max_transcodes=2,
        memory_mb=memory_mb,
        key=KEY,
    )
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    task = guard.start(w.caps, interval=watch) if watch else None
    try:
        yield w
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await w.stop()
        client.bind(None)
        assert await until(lambda: not ffmpegs(), limit=5), "een kind bleef over"


async def frame_job(source: Path) -> client.Job:
    job, reads = await client.call(Op.FRAME, FRAME, source=source, outputs=1)
    for fd in reads:
        os.close(fd)  # het beeld lezen we hier niet
    return job


# --- het geheugen -----------------------------------------------------------------------------


async def test_een_job_boven_de_geheugengrens_wordt_gestopt(tmp_path, monkeypatch, source):
    """RLIMIT_DATA telt geen gedeeld geheugen; de meting wel (RssAnon + RssShmem)."""
    monkeypatch.setattr(A, "SHORT_MEMORY_MB", 48)
    fake(tmp_path, monkeypatch, HOG.format(mb=96))
    async with serving(tmp_path, watch=0.05) as w:
        job = await frame_job(source)
        assert await until(lambda: len(ffmpegs()) == 1)
        (pid,) = ffmpegs()
        with pytest.raises(WorkerFailed) as exc:
            async with asyncio.timeout(10):
                await job.result()
        assert exc.value.code == Code.FAILED
        assert "geheugengrens" in exc.value.detail and "grens 48 MB" in exc.value.detail
        assert guard.why(pid) == exc.value.detail
        assert not alive(pid)
        caps = await client.caps()  # de worker leeft, en de plaats is terug
        assert caps.procs == 0 and caps.in_use["short"] == 0
        assert await until(lambda: all(c["pid"] != pid for c in w.caps["children"]))


async def test_een_malloc_boven_rlimit_data_stopt_het_kind_niet_de_worker(
    tmp_path, monkeypatch, source
):
    monkeypatch.setattr(A, "SHORT_MEMORY_MB", 48)
    fake(tmp_path, monkeypatch, GREEDY.format(mb=200))
    async with serving(tmp_path):
        job = await frame_job(source)
        with pytest.raises(WorkerFailed) as exc:
            async with asyncio.timeout(10):
                await job.result()
        assert exc.value.code == Code.UNREADABLE  # ffmpeg stopte met een fout
        assert "MemoryError" in exc.value.detail
        caps = await client.caps()
        assert caps.procs == 0
        # en de volgende opdracht gaat gewoon
        fake(tmp_path, monkeypatch, SLEEPER)
        job = await frame_job(source)
        assert await until(lambda: len(ffmpegs()) == 1)
        await job.cancel()


async def test_de_meting_staat_in_caps(tmp_path, monkeypatch, source):
    fake(tmp_path, monkeypatch, SLEEPER)
    async with serving(tmp_path, watch=0.05) as w:
        job = await frame_job(source)
        try:
            assert await until(lambda: len(w.caps["children"]) == 1)
            (seen,) = w.caps["children"]
            assert seen["name"] == "ffmpeg" and seen["rss_kb"] > 0
            assert w.caps["rss_kb"] == seen["rss_kb"]
        finally:
            await job.cancel()
        assert await until(lambda: w.caps["children"] == [])
        assert w.caps["rss_kb"] == 0


def test_de_meting_leest_proc():
    me = os.getpid()
    assert guard.rss_kb(me) and guard.rss_kb(me) > 0
    assert guard.rss_kb(2**22 + 12345) is None  # bestaat niet
    assert guard.data_limit_kb(2**22 + 12345) is None
    proc = subprocess.Popen(
        ["sleep", "30"],
        preexec_fn=lambda: __import__("resource").setrlimit(
            __import__("resource").RLIMIT_DATA, (64 * 1024 * 1024, 64 * 1024 * 1024)
        ),
    )
    try:
        assert guard.data_limit_kb(proc.pid) == 64 * 1024
        assert (proc.pid, "sleep") in guard.children(me)
    finally:
        proc.kill()  # op pid
        proc.wait()


# --- stoppen ----------------------------------------------------------------------------------


async def test_verbinding_dicht_stopt_ffmpeg_en_kleinkinderen_binnen_3_s(
    tmp_path, monkeypatch, source
):
    fake(tmp_path, monkeypatch, FAMILY)
    async with serving(tmp_path):
        job = await frame_job(source)
        assert await until(lambda: len(ffmpegs()) == 1)
        (pid,) = ffmpegs()
        assert await until(lambda: len(guard.children(pid)) == 1)
        ((grandchild, comm),) = guard.children(pid)
        assert comm == "sleep" and alive(grandchild)
        began = time.monotonic()
        await job.cancel()
        assert await until(lambda: not alive(pid) and not alive(grandchild), limit=3)
        assert time.monotonic() - began < 3
        assert await _caps_procs(0)


# --- de plaatsen ------------------------------------------------------------------------------


async def test_een_volle_soort_geeft_busy_met_die_soort_en_caps_werkt(
    tmp_path, monkeypatch, source
):
    fake(tmp_path, monkeypatch, SLEEPER)
    async with serving(tmp_path, watch=0.05) as w:
        jobs = [await frame_job(source), await frame_job(source)]
        try:
            with pytest.raises(WorkerBusy) as exc:
                await frame_job(source)
            assert exc.value.work_class == WorkClass.SHORT
            with pytest.raises(WorkerBusy):
                await client.call(Op.META, A.MetaArgs(demuxer="matroska"), source=source)
            caps = await client.caps()  # de controleverbinding telt niet mee
            assert caps.in_use["short"] == 2 and caps.procs == 2
            assert await until(lambda: len(w.caps["children"]) == 2)
        finally:
            for job in jobs:
                await job.cancel()
        assert await until(lambda: not ffmpegs())
        assert (await client.caps()).procs == 0


@pytest.fixture
def stubborn(tmp_path, monkeypatch):
    """Elke opdracht behalve caps start de nep-ffmpeg die SIGTERM negeert, en wacht erop."""
    fake(tmp_path, monkeypatch, STUBBORN)
    real = server.handler

    async def run(ctx: server.Context) -> dict:
        child = ctx.spawn(["ffmpeg"])
        await child.wait()
        return {}

    monkeypatch.setattr(server, "handler", lambda op: real(op) if op == Op.CAPS else run)


async def test_max_procs_3_vierde_proces_busy_ook_als_er_een_stopt(tmp_path, stubborn, source):
    remux = A.StreamArgs(
        mode="remux", demuxer="matroska", video_index=0, video="copy", audio="none"
    )
    sprites = A.SpritesArgs(demuxer="matroska", video_index=0)
    async with serving(tmp_path, max_procs=3):
        jobs = []
        try:
            jobs.append((await client.call(Op.FRAME, FRAME, source=source, outputs=1))[0])
            jobs.append((await client.call(Op.PROBE, A.ProbeArgs(), source=source))[0])
            jobs.append((await client.call(Op.SPRITES, sprites, source=source, outputs=1))[0])
            assert await until(lambda: len(ffmpegs()) == 3)
            assert await until(lambda: all(ignores_term(p) for p in ffmpegs()))
            # elke soort heeft nog plaats (remux 6), maar het totaal is vol
            with pytest.raises(WorkerBusy):
                await client.call(Op.STREAM, remux, source=source, outputs=1)
            stopping = ffmpegs()
            await jobs[1].cancel()  # de probe stopt, maar zijn kind negeert SIGTERM (2 s)
            await asyncio.sleep(0.3)
            assert len(ffmpegs()) == 3 and (await client.caps()).procs == 3
            with pytest.raises(WorkerBusy):
                await client.call(Op.STREAM, remux, source=source, outputs=1)
            # na SIGKILL en waitpid is de plaats terug
            assert await until(lambda: len(ffmpegs()) == 2, limit=5)
            assert await _caps_procs(2)
            job, reads = await client.call(Op.STREAM, remux, source=source, outputs=1)
            jobs.append(job)
            for fd in reads:
                os.close(fd)
            assert await until(lambda: len(ffmpegs()) == 3)
            assert set(ffmpegs()) != set(stopping)
        finally:
            for job in jobs:
                await job.cancel()


def ignores_term(pid: int) -> bool:
    """Heeft de nep SIGTERM al op negeren gezet (SigIgn in /proc/<pid>/status)?"""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("SigIgn:"):
                return bool(int(line.split()[1], 16) & (1 << (signal.SIGTERM - 1)))
    except FileNotFoundError:
        return False
    return False


async def _caps_procs(wanted: int) -> bool:
    for _ in range(100):
        if (await client.caps()).procs == wanted:
            return True
        await asyncio.sleep(0.02)
    return False


# --- één producent per sessie -----------------------------------------------------------------


async def test_tien_keer_zoeken_in_een_seconde_nooit_meer_dan_2_ffmpeg(
    tmp_path, monkeypatch, source
):
    fake(tmp_path, monkeypatch, SLEEPER)
    async with serving(tmp_path):
        producer: guard.OneProducer[client.Job] = guard.OneProducer()
        most = 0
        stopped: list[int] = []
        sampling = True

        async def sample() -> None:
            nonlocal most
            while sampling:
                most = max(most, len(ffmpegs()))
                await asyncio.sleep(0.005)

        async def start() -> client.Job:
            return await frame_job(source)

        async def stop(job: client.Job) -> None:
            before = ffmpegs()
            assert await guard.finish(job)
            # finish komt pas terug na waitpid van het kind in de worker
            stopped.extend(p for p in before if not alive(p))
            assert all(not alive(p) for p in before)

        sampler = asyncio.create_task(sample())
        seeks = []
        for _ in range(10):
            seeks.append(asyncio.create_task(producer.replace(start, stop)))
            await asyncio.sleep(0.1)
        results = await asyncio.gather(*seeks)
        assert await until(lambda: len(ffmpegs()) == 1)
        sampling = False
        await sampler
        assert most <= 2, most
        assert results[-1] is not None and producer.current is results[-1]
        assert 1 <= producer.started <= 10
        await producer.close()
        assert await until(lambda: not ffmpegs())
        assert (await client.caps()).procs == 0


async def test_samenvoegen_binnen_300_ms():
    started: list[int] = []
    producer: guard.OneProducer[int] = guard.OneProducer(coalesce_s=0.3)

    async def start_of(n: int):
        async def start() -> int:
            started.append(n)
            return n

        return start

    async def stop(_n: int) -> None:
        await asyncio.sleep(0.01)

    first = await producer.replace(await start_of(0), stop)  # zonder vorige: meteen
    assert first == 0 and started == [0]
    burst = [asyncio.create_task(producer.replace(await start_of(n), stop)) for n in (1, 2, 3)]
    assert await asyncio.gather(*burst) == [None, None, 3]
    assert started == [0, 3] and producer.started == 2
    await producer.close()
    assert producer.current is None
    late = await producer.replace(await start_of(4), stop)
    assert late == 4


async def test_finish_van_een_dichte_job(tmp_path, monkeypatch, source):
    fake(tmp_path, monkeypatch, SLEEPER)
    async with serving(tmp_path):
        job = await frame_job(source)
        await job.cancel()
        assert await guard.finish(job)  # al dicht: meteen klaar
        assert job._cancelled


# --- de grenzen en de omgeving van een kind ---------------------------------------------------


async def test_nice_rlimits_en_kale_omgeving_van_frame(tmp_path, monkeypatch, source):
    monkeypatch.setenv("PLATFORM_MEDIA_WORKER_KEY", "x" * 64)  # mag nooit naar een kind
    monkeypatch.setenv("PLATFORM_JWT_SECRET", "geheim")
    fake(tmp_path, monkeypatch, SLEEPER)
    async with serving(tmp_path):
        job = await frame_job(source)
        try:
            assert await until(lambda: len(ffmpegs()) == 1)
            (pid,) = ffmpegs()
            st = proc_stat(pid)
            assert st is not None
            assert int(st[16]) == 15  # nice -n 15 voor één beeld
            assert int(st[3]) == pid  # eigen procesgroep (en sessie)
            lim = limits(pid)
            assert lim["Max data size"] == (str(512 * 1024 * 1024),) * 2
            assert lim["Max file size"] == ("0", "0")
            assert lim["Max core file size"] == ("0", "0")
            assert lim["Max open files"] == ("256", "256")
            env = environ(pid).split(b"\0")
            names = {e.split(b"=", 1)[0].decode() for e in env if e}
            assert names == {"PATH", "LC_ALL", "HOME"}
            assert b"geheim" not in b"".join(env) and b"x" * 64 not in b"".join(env)
            assert os.readlink(f"/proc/{pid}/cwd") == "/"
            fds = sorted(int(n) for n in os.listdir(f"/proc/{pid}/fd"))
            assert fds == [0, 1, 2, 3]  # de bron op 3, de pipe van de app op 1
        finally:
            await job.cancel()


async def test_nice_ionice_en_geheugen_van_bench(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, SLEEPER)
    async with serving(tmp_path, memory_mb=900):
        job, _ = await client.call(Op.BENCH, A.BenchArgs())
        try:
            assert await until(lambda: len(ffmpegs()) == 1)
            (pid,) = ffmpegs()
            st = proc_stat(pid)
            assert st is not None and int(st[16]) == 15
            assert ionice(pid).strip() == "idle"
            assert limits(pid)["Max data size"][0] == str(900 * 1024 * 1024)
            caps = await client.caps()
            assert caps.in_use["background"] == 1 and caps.procs == 2  # bench reserveert er 2
        finally:
            await job.cancel()


# --- de argv per opdracht (goud) --------------------------------------------------------------


def test_argv_van_frame():
    args = A.FrameArgs(demuxer="mov", video_index=2, at_s=12.3456, width=1024)
    assert frame.argv(args) == [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2",
        "-nostats", "-max_alloc", "536870912", "-protocol_whitelist", "fd,pipe",
        "-format_whitelist", "mov,matroska,avi,wav,flv,ogg,asf,mpegts,mpeg,mp3,flac,aac",
        "-max_pixels", "35389440", "-threads", "4", "-ss", "12.346", "-f", "mov", "-fd", "3",
        "-i", "fd:", "-map", "0:2", "-frames:v", "1", "-vf", "scale=1024:-2", "-c:v", "mjpeg",
        "-q:v", "3", "-f", "image2pipe", "pipe:1",
    ]  # fmt: skip
    assert A.niceness(Op.FRAME, args) == ("nice", "-n", "15")


def test_argv_van_meta():
    argv = meta.argv("matroska")
    assert argv[0] == "ffprobe"
    assert "-show_format" in argv and "-show_streams" in argv
    i = argv.index("-format_whitelist")
    assert argv[i + 1] == "mov,matroska,avi,wav,flv,ogg,asf,mpegts,mpeg,mp3,flac,aac"
    assert argv[argv.index("-protocol_whitelist") + 1] == "fd,pipe"
    assert argv[argv.index("-f") + 1] == "matroska"
    assert argv[-2:] == ["-i", "fd:"] or "fd:" in argv
    assert "lavfi" not in " ".join(argv)


def test_argv_van_bench():
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    assert bench.encode_argv(10) == [
        *base, "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t",
        "-t", "10", "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast",
        "-threads", "4", "-f", "null", "-",
    ]  # fmt: skip
    assert bench.hevc_argv(4) == [
        *base, "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24", "-t", "4", "-c:v",
        "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-f", "hevc", "pipe:1",
    ]  # fmt: skip
    assert bench.decode_argv() == [*base, "-f", "hevc", "-i", "pipe:0", "-f", "null", "-"]
    assert A.niceness(Op.BENCH, A.BenchArgs()) == ("nice", "-n", "15", "ionice", "-c", "3")
    assert A.procs(Op.BENCH, A.BenchArgs()) == 2
    assert bench.REF_CORES > 0 and bench.REF_DEC_CORES > 0


def test_frame_width_zoals_de_box_van_0_6_0():
    assert render.frame_width(1920, 1080) == 1024
    assert render.frame_width(1080, 1920) == 576  # staand: de hoogte wordt 1024
    assert render.frame_width(160, 120) == 160  # nooit groter dan het origineel
    assert render.frame_width(161, 121) == 160  # even
    assert render.frame_width(8, 8) == 16  # de ondergrens van FrameArgs
    assert render.frame_width(99999, 10) == 1024


# --- fuzz (regel 10): wat de worker ontleedt, en wat de app ervan leest ----------------------


def _junk(rng: random.Random, depth: int = 0):
    kinds = ["int", "float", "str", "none", "bool", "list", "dict", "big", "nan"]
    kind = rng.choice(kinds if depth < 4 else kinds[:5])
    if kind == "int":
        return rng.randint(-(2**40), 2**40)
    if kind == "float":
        return rng.uniform(-1e15, 1e15)
    if kind == "big":
        return "9" * rng.randint(1, 400)
    if kind == "nan":
        return rng.choice([float("nan"), float("inf"), "NaN", "-inf", "1e999"])
    if kind == "str":
        alphabet = "abc 01:.-TZ\x00é‮/"
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
    if kind == "none":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "list":
        return [_junk(rng, depth + 1) for _ in range(rng.randint(0, 6))]
    keys = [
        "format", "streams", "tags", "duration", "codec_type", "codec_name", "width", "height",
        "index", "side_data_list", "rotation", "disposition", "attached_pic", "creation_time",
        "com.apple.quicktime.creationdate", "rotate", "format_name", "channels", "sample_rate",
        "location", "profile", "pix_fmt",
    ]  # fmt: skip
    return {rng.choice(keys): _junk(rng, depth + 1) for _ in range(rng.randint(0, 8))}


def _shaped(rng: random.Random) -> dict:
    """Iets dat op de JSON van ffprobe lijkt, met rare waarden."""
    streams = []
    for i in range(rng.randint(0, 80)):
        stream = _junk(rng, 1) if rng.random() < 0.3 else {}
        if isinstance(stream, dict):
            stream.setdefault("index", rng.choice([i, -1, "x", None, 2**40]))
            stream.setdefault("codec_type", rng.choice(["video", "audio", "evil", None]))
        streams.append(stream)
    fmt = _junk(rng, 1)
    return {"format": fmt, "streams": streams if rng.random() < 0.9 else _junk(rng)}


def test_fuzz_meta_clean_en_wat_de_app_ervan_leest():
    rng = random.Random(20251029)
    for _ in range(1500):
        raw = _shaped(rng) if rng.random() < 0.7 else _junk(rng)
        out = meta.clean(raw, "matroska", "mkv")
        assert set(out) == {"demuxer", "container", "format", "streams"}
        assert len(out["streams"]) <= meta.MAX_STREAMS
        assert len(json.dumps(out)) < wire.MAX_MESSAGE
        for stream in out["streams"]:
            assert isinstance(stream["index"], int) and 0 <= stream["index"] < 2**31
            assert stream.get("codec_type") in meta.TYPES
            for name in stream.get("tags", {}):
                assert name in meta.TAGS  # nooit locatie
        # wat render.py en render_meta.py ermee doen, faalt nooit
        render.codecs_of(out)
        render_meta.video_meta(out)
        for stream in out["streams"]:
            assert render._rotation(stream) in range(360)


def test_fuzz_de_boxen_van_de_zelftest():
    rng = random.Random(7)
    sample = _fmp4_sample()
    for n in range(1500):
        if n % 3 == 0:
            data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 300)))
        else:
            data = bytearray(sample)
            for _ in range(rng.randint(1, 12)):
                data[rng.randrange(len(data))] = rng.randrange(256)
            data = bytes(data[: rng.randint(0, len(data))])
        found = list(selftest.boxes(data))
        assert all(0 <= a <= b <= len(data) for _kind, a, b in found)
        out = selftest.fragments(data)
        assert set(out) == {"timescale", "moofs"}
    good = selftest.fragments(sample)
    assert good["timescale"] == 12288 and len(good["moofs"]) == 2
    assert [m["tfdt"] for m in good["moofs"]] == [12288, 36864]


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), kind) + body


def _fmp4_sample() -> bytes:
    """Een klein fMP4 zoals de zelftest het ziet: moov met mdhd, twee moof met tfdt en trun."""
    mdhd = _box(b"mdhd", b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">I", 12288) + b"\0" * 8)
    moov = _box(b"moov", _box(b"trak", _box(b"mdia", mdhd)))

    def moof(t: int) -> bytes:
        tfdt = _box(b"tfdt", b"\x01\x00\x00\x00" + struct.pack(">Q", t))
        trun = _box(b"trun", b"\x01\x00\x0a\x05" + struct.pack(">I", 1) + b"\0" * 12)
        return _box(b"moof", _box(b"traf", tfdt + trun))

    return _box(b"ftyp", b"iso6") + moov + moof(12288) + _box(b"mdat", b"x") + moof(36864)


# --- met de echte ffmpeg ----------------------------------------------------------------------


def _local_version() -> str:
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
    number = out.stdout.split()[2]  # "ffmpeg version 6.1.1-3ubuntu5 ..."
    return ".".join(number.split(".")[:2]) + "."


@pytest.mark.media
def test_selftest_lokaal_zegt_wat_hij_controleerde():
    done = subprocess.run(
        [
            sys.executable, "-I", "-c", local.BOOT, str(local.API_ROOT), "--selftest", "--local",
            "--ffmpeg", _local_version(),
        ],
        capture_output=True,
        timeout=180,
        env={"PATH": os.environ["PATH"], "LC_ALL": "C", "HOME": "/tmp"},
    )  # fmt: skip
    report = json.loads(done.stdout)
    assert done.returncode == 0, report["problems"]
    assert report["ok"] is True and report["sandbox"] == "off" and report["problems"] == []
    checks = {c["name"]: c for c in report["checks"]}
    # lokaal tellen alleen de controles buiten de afscherming; die van de afscherming staan er
    # wel in (sandbox: true), en zonder container falen ze hier
    assert all(c["ok"] for c in checks.values() if not c["sandbox"])
    wanted = [
        "ffmpeg versie", "ffprobe versie", "encoders", "filters", "protocollen fd en pipe",
        "demuxers en lavfi", "nice en ionice", "weigering door -format_whitelist",
        "weigering door -protocol_whitelist", "twee fragmenten", "tfdt op 1,000 en 3,000",
        "trun versie 1", "1080 veryfast onder RLIMIT_DATA", "een vraag vóór hello geweigerd",
        "fd's vóór hello geweigerd",
    ]  # fmt: skip
    sandbox = [
        "uid 10002", "geen capabilities", "no-new-privileges", "geen netwerk", "/ alleen lezen",
        "/data leeg", "ffmpeg 0711 van root", "ffprobe 0711 van root", "blob niet heropenbaar",
        "een kind leest /proc/<worker>/fd niet", "een kind leest /proc/<worker>/environ niet",
        "een buur ziet /proc/<ffmpeg>/fd niet",
    ]  # fmt: skip
    for name in wanted:
        assert any(n.startswith(name) and not c["sandbox"] for n, c in checks.items()), name
    for name in sandbox:
        assert any(n.startswith(name) and c["sandbox"] for n, c in checks.items()), name


@pytest.mark.media
async def test_frame_en_meta_met_de_echte_ffmpeg(tmp_path):
    from tests.media.fixtures import make

    film = make("h264_aac.mp4")
    async with serving(tmp_path):
        job, _ = await client.call(Op.META, A.MetaArgs(), source=film)
        async with job:
            info = await job.result()
        assert info["demuxer"] == "mov" and info["container"] == "mp4"
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        args = A.FrameArgs(demuxer="mov", video_index=video["index"], at_s=0.5, width=320)
        job, (r,) = await client.call(Op.FRAME, args, source=film, outputs=1)
        async with job:
            data = await asyncio.to_thread(_read_all, r)
            result = await job.result()
        assert data[:3] == b"\xff\xd8\xff" and result["frames"] == 1
        # voorbij het einde: geen beeld, en geen fout
        late = A.FrameArgs(demuxer="mov", video_index=video["index"], at_s=3600, width=320)
        job, (r,) = await client.call(Op.FRAME, late, source=film, outputs=1)
        async with job:
            data = await asyncio.to_thread(_read_all, r)
            result = await job.result()
        assert data == b"" and not result["frames"]


def _read_all(fd: int) -> bytes:
    try:
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


@pytest.mark.media
async def test_echte_ffmpeg_boven_rlimit_data_stopt_met_een_fout(tmp_path, monkeypatch):
    from tests.media.fixtures import make

    film = make("h264_aac.mp4")
    monkeypatch.setattr(A, "SHORT_MEMORY_MB", 16)
    async with serving(tmp_path):
        args = A.FrameArgs(demuxer="mov", video_index=0, at_s=0.5, width=320)
        job, (r,) = await client.call(Op.FRAME, args, source=film, outputs=1)
        os.close(r)
        with pytest.raises(WorkerFailed):
            async with asyncio.timeout(30):
                await job.result()
        assert (await client.caps()).procs == 0  # de worker leeft


@pytest.mark.media
async def test_de_sleutel_komt_niet_bij_ffmpeg_van_de_lokale_worker(tmp_path, monkeypatch):
    """De lokale worker heeft de sleutel in zijn omgeving; zijn kinderen niet."""
    script = (
        f'#!/bin/sh\ncase " $* " in *" fd: "*) exec sleep 60 ;; esac\nexec {which("ffmpeg")} "$@"\n'
    )
    fake(tmp_path, monkeypatch, script)
    settings = Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32, media_max_procs=3
    )
    w = await local.start(settings, tmp_path / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    film = tmp_path / "a.mkv"
    film.write_bytes(b"\x1aE\xdf\xa3" + b"\0" * 1020)
    try:
        assert w.key.encode() in environ(w.pid)
        job = await frame_job(film)
        try:
            assert await until(lambda: len(guard.children(w.pid)) == 1)
            ((pid, _comm),) = guard.children(w.pid)
            await asyncio.sleep(0.1)
            env = environ(pid)
            assert w.key.encode() not in env and b"PLATFORM_" not in env
        finally:
            await job.cancel()
        assert await until(lambda: not guard.children(w.pid))
    finally:
        await local.stop(w)
        client.bind(None)
