"""synth: een bron voor de belastingtest (M10, §3.3 en §4.6; worker/ops/synth.py).

Alleen de vaste lijst (testsrc2 of cellauto, seed 0–9, 30–600 s, 1080 of 720); een andere naam
of een extra parameter weigert de worker al bij het lezen (bad_args). Zonder --loadtest weigert
hij synth (not_allowed). De uitvoer is de fd van de app (een gewoon bestand; een pipe is
bad_fds) en het kind schrijft op fd 3. RLIMIT_FSIZE is niet 0 maar begrensd.

De server draait in dit proces (Worker uit server.py); ffmpeg is een nep in PATH die zijn argv
opschrijft. Met de marker media: een echte synth van 30 s op 720p (één ffmpeg, onder taskset
-c 0-2 in de checks), en de probe ziet een mkv met H.264 en AAC.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import shutil
import sys
from pathlib import Path

import pytest

from app.media import client
from app.media.client import WorkerFailed
from app.media.wire import Code, Op, WorkClass
from app.media.worker import args as A
from app.media.worker.ops import synth
from app.media.worker.server import Config, Worker

pytestmark = pytest.mark.anyio

KEY = "de-sleutel-van-de-synth-test-" * 2
CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
PY = f"#!{sys.executable} -IB\n"
GOOD = {"source": "cellauto", "seed": 1, "duration_s": 30, "height": 720}


@contextlib.asynccontextmanager
async def serving(tmp_path: Path, *, loadtest: bool = True):
    config = Config(
        socket=tmp_path / "run" / "w.sock", max_procs=3, max_transcodes=2, memory_mb=1200,
        key=KEY, loadtest=loadtest,
    )  # fmt: skip
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        yield w
    finally:
        await w.stop()
        client.bind(None)


def fake_ffmpeg(tmp_path: Path, monkeypatch, data: bytes = b"synth" * 1000, rc: int = 0) -> Path:
    """Een nep-ffmpeg: schrijft zijn argv naar argv.json en `data` naar fd 3."""
    out = tmp_path / "argv.json"
    script = PY + (
        "import json, os, sys\n"
        f"open({str(out)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"os.write(3, {data!r})\n"
        "sys.stderr.write('out_time_us=1000000\\nspeed=2.5x\\nprogress=end\\n')\n"
        f"sys.exit({rc})\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / "ffmpeg"
    path.write_text(script)
    path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}")
    return out


def target(tmp_path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC
    return os.open(tmp_path / "bron.mkv.part", flags, 0o600)


async def run_synth(args: dict | A.SynthArgs, fd: int) -> dict:
    job, _ = await client.call(Op.SYNTH, args, inputs=0, fds=[fd])  # type: ignore[arg-type]
    async with job:
        return await job.result()


# --- de argumenten --------------------------------------------------------------------------------


def test_alleen_de_vaste_lijst():
    assert isinstance(A.parse(Op.SYNTH, GOOD), A.SynthArgs)
    for wrong in (
        {**GOOD, "source": "smptebars"},
        {**GOOD, "source": "testsrc2,drawtext=text=x"},
        {**GOOD, "seed": 10},
        {**GOOD, "seed": -1},
        {**GOOD, "duration_s": 29},
        {**GOOD, "duration_s": 601},
        {**GOOD, "height": 480},
        {**GOOD, "filter": "noise"},  # een extra parameter
        {k: v for k, v in GOOD.items() if k != "seed"},
    ):
        with pytest.raises(ValueError):
            A.parse(Op.SYNTH, wrong)


def test_de_soort_en_de_fds():
    args = A.SynthArgs(**GOOD)
    assert A.work_class(Op.SYNTH, args) == WorkClass.BACKGROUND
    assert A.niceness(Op.SYNTH, args) == ("nice", "-n", "15", "ionice", "-c", "3")
    assert A.fd_layout(Op.SYNTH, args) == A.Layout(0, 1)
    assert A.procs(Op.SYNTH, args) == 1


def test_de_argv():
    args = A.SynthArgs(source="testsrc2", seed=3, duration_s=300, height=1080)
    argv = synth.argv(args)
    assert argv[0] == "ffmpeg" and argv[-3:] == ["-fd", "3", "fd:"]
    assert argv[argv.index("-i") + 1] == "testsrc2=size=1920x1080:rate=24,noise=alls=23:allf=t"
    assert ["-t", "300"] == argv[argv.index("-t") : argv.index("-t") + 2]
    assert "libx264" in argv and "6M" in argv and "-nostdin" in argv and "aac" in argv
    cell = synth.lavfi(A.SynthArgs(source="cellauto", seed=2, duration_s=60, height=720))
    assert cell == "cellauto=s=1280x720:rate=24:rule=110:random_seed=2,noise=alls=20:allf=t"
    # nooit een vrije string: alles komt uit de vaste lijst
    for source in ("testsrc2", "cellauto"):
        for seed in range(10):
            for height in (720, 1080):
                found = synth.argv(A.SynthArgs(source=source, seed=seed, duration_s=30,
                                               height=height))  # fmt: skip
                assert all("'" not in a and ";" not in a and " " not in a for a in found)


def test_de_grens_op_de_grootte():
    small = synth.size_limit(A.SynthArgs(**GOOD))
    big = synth.size_limit(A.SynthArgs(**{**GOOD, "duration_s": 600}))
    # 6,16 Mbit/s × de duur, × 1,5, plus 16 MB
    assert small == (6160 * 1000 // 8 * 30) * 3 // 2 + 16 * 1024 * 1024
    assert 0 < small < big < 1024**3


def test_fuzz_willekeurige_argumenten():
    """Regel 10: vaste seed; wat er ook binnenkomt, parse geeft SynthArgs uit de lijst of
    ValueError, en de argv bevat alleen wat uit de lijst komt."""
    rng = random.Random(4610)
    values = [None, 0, 1, 9, 10, -1, 29, 30, 600, 601, 720, 1080, 480, "", "testsrc2", "cellauto",
              "x" * 50, "testsrc2;rm", [], {}, 1.5, True]  # fmt: skip
    keys = ["source", "seed", "duration_s", "height", "extra"]
    for _ in range(2000):
        args = {k: rng.choice(values) for k in keys if rng.random() < 0.85}
        try:
            parsed = A.parse(Op.SYNTH, args)
        except ValueError:
            continue
        assert isinstance(parsed, A.SynthArgs)
        argv = synth.argv(parsed)
        assert parsed.source in ("testsrc2", "cellauto") and parsed.height in (720, 1080)
        assert argv[-3:] == ["-fd", "3", "fd:"]


# --- de server ----------------------------------------------------------------------------------


async def test_synth_schrijft_naar_de_fd_van_de_app(tmp_path, monkeypatch):
    seen = fake_ffmpeg(tmp_path, monkeypatch, data=b"mkv-bytes" * 500)
    fd = target(tmp_path)
    try:
        async with serving(tmp_path):
            found = await run_synth(A.SynthArgs(**GOOD), fd)
    finally:
        os.close(fd)
    assert (tmp_path / "bron.mkv.part").read_bytes() == b"mkv-bytes" * 500
    assert found["source"] == "cellauto" and found["duration_s"] == 30 and found["height"] == 720
    argv = json.loads(seen.read_text())
    assert argv == synth.argv(A.SynthArgs(**GOOD))[1:]


async def test_zonder_loadtest_geweigerd(tmp_path, monkeypatch):
    seen = fake_ffmpeg(tmp_path, monkeypatch)
    fd = target(tmp_path)
    try:
        async with serving(tmp_path, loadtest=False):
            with pytest.raises(WorkerFailed) as caught:
                await run_synth(A.SynthArgs(**GOOD), fd)
    finally:
        os.close(fd)
    assert caught.value.code == Code.NOT_ALLOWED
    assert not seen.exists() and (tmp_path / "bron.mkv.part").stat().st_size == 0


async def test_andere_argumenten_of_een_pipe_geweigerd(tmp_path, monkeypatch):
    seen = fake_ffmpeg(tmp_path, monkeypatch)
    fd = target(tmp_path)
    r, w = os.pipe()
    try:
        async with serving(tmp_path):
            for wrong in ({**GOOD, "source": "smptebars"}, {**GOOD, "extra": 1}):
                with pytest.raises(WorkerFailed) as caught:
                    await run_synth(_Raw(**wrong), fd)
                assert caught.value.code == Code.BAD_ARGS
            with pytest.raises(WorkerFailed) as caught:
                await run_synth(A.SynthArgs(**GOOD), w)
            assert caught.value.code == Code.BAD_FDS
    finally:
        for f in (fd, r, w):
            os.close(f)
    assert not seen.exists()


async def test_een_fout_van_ffmpeg(tmp_path, monkeypatch):
    fake_ffmpeg(tmp_path, monkeypatch, rc=1)
    fd = target(tmp_path)
    try:
        async with serving(tmp_path):
            with pytest.raises(WorkerFailed) as caught:
                await run_synth(A.SynthArgs(**GOOD), fd)
    finally:
        os.close(fd)
    assert caught.value.code == Code.FAILED


class _Raw(A._Args):
    """Argumenten zoals een vreemde app ze zou sturen (de worker leest ze zelf na)."""

    model_config = {"extra": "allow", "frozen": True}


# --- echt (marker media) --------------------------------------------------------------------------


@pytest.mark.media
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ontbreekt")
async def test_een_echte_synth(tmp_path):
    """Eén ffmpeg (regel 13), 30 s op 720p: een mkv met H.264 en AAC, binnen de grens."""
    fd = target(tmp_path)
    try:
        async with serving(tmp_path):
            found = await run_synth(A.SynthArgs(**GOOD), fd)
            probe = await client.probe(tmp_path / "bron.mkv.part")
    finally:
        os.close(fd)
    size = (tmp_path / "bron.mkv.part").stat().st_size
    assert 0 < size <= synth.size_limit(A.SynthArgs(**GOOD))
    assert found["cpu_s"] is not None and found["cpu_s"] > 0
    assert probe.container == "matroska"
    codecs = sorted(s.codec for s in probe.streams)
    assert codecs == ["aac", "h264"]
    video = next(s for s in probe.streams if s.codec == "h264")
    assert (video.width, video.height) == (1280, 720)
    assert probe.duration_ms is not None and abs(probe.duration_ms - 30_000) < 1000
