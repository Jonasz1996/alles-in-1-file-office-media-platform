"""Intro overslaan (fase 11, stap 7): vergelijken (puur) en de opdracht fingerprint van de
worker met echte ffmpeg (twee nep-afleveringen met dezelfde intro op een andere plek)."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from app.media import client, intro
from app.media.wire import Op
from app.media.worker.args import FingerprintArgs
from app.media.worker.server import Config, Worker
from app.render import _drain

pytestmark = pytest.mark.anyio

CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
KEY = "de-sleutel-van-de-intro-test-" * 2


def test_vergelijken() -> None:
    rng = random.Random(4)

    def noise(n: int) -> list[int]:
        return [rng.getrandbits(32) for _ in range(n)]

    shared = noise(240)  # ~30 s
    a = noise(160) + shared + noise(300)
    blur = [p ^ (1 << rng.randrange(32)) if i % 3 == 0 else p for i, p in enumerate(shared)]
    b = noise(320) + blur + noise(100)  # een derde van de punten met 1 bit verschil
    m = intro.compare(a, b)
    assert m is not None
    assert abs(m.a_start - 160 * intro.POINT_S) < 1 and abs(m.b_start - 320 * intro.POINT_S) < 1
    assert 28 < m.length < 31
    assert intro.compare(noise(500), noise(500)) is None
    found = intro.season({"a": a, "b": b, "c": noise(400)})
    assert set(found) == {"a", "b"}
    # Te kort (10 s) telt niet
    short = noise(80)
    assert intro.compare(noise(50) + short + noise(50), noise(90) + short + noise(50)) is None
    assert intro.decode(b"\x01\x00\x00\x00\x02\x00\x00\x00\xff") == [1, 2]


@pytest.fixture
async def worker(tmp_path):
    config = Config(socket=tmp_path / "run" / "w.sock", max_procs=4, max_transcodes=1, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        yield w
    finally:
        await w.stop()


def episode(work: Path, n: int, intro_wav: Path) -> Path:
    def run(*args: str) -> None:
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)

    pre, post, out = work / f"pre{n}.wav", work / f"post{n}.wav", work / f"ep{n}.mkv"
    tone = f"0.3*sin(2*PI*(330+{n * 91}*mod(floor(t*1.7),7))*t)"
    run("-f", "lavfi", "-i", f"aevalsrc='{tone}':d={10 * n}:s=22050", str(pre))
    melody = f"0.3*sin(2*PI*(200+{n * 137}+100*floor(t*{1.3 + n * 0.7}))*t)"
    run("-f", "lavfi", "-i", f"aevalsrc='{melody}':d=20:s=22050", str(post))
    run("-i", str(pre), "-i", str(intro_wav), "-i", str(post), "-filter_complex",
        "[0][1][2]concat=n=3:v=0:a=1", "-c:a", "aac", str(out))  # fmt: skip
    return out


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg niet geïnstalleerd")
async def test_fingerprint_via_de_worker(worker, tmp_path):
    song = tmp_path / "intro.wav"
    subprocess.run(  # noqa: ASYNC221 - een testbestand maken, vóór de worker iets doet
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "anoisesrc=d=25:c=pink:seed=7:a=0.3", "-f", "lavfi", "-i", "sine=f=440:d=25",
         "-filter_complex", "[0][1]amix=inputs=2,volume=2", "-ar", "22050", str(song)],
        check=True,
    )  # fmt: skip
    prints = {}
    for n in (1, 2):
        source = episode(tmp_path, n, song)
        args = FingerprintArgs(demuxer="matroska", audio_index=0, seconds=120)
        job, (out,) = await client.call(Op.FINGERPRINT, args, source=source, outputs=1)
        async with job:
            try:
                raw = await _drain(out, 1 << 20)
            finally:
                os.close(out)
            assert (await job.result())["seconds"] == 120
        prints[str(n)] = intro.decode(raw)
        assert len(prints[str(n)]) > 300
    found = intro.season(prints)
    start1, end1 = found["1"]
    start2, end2 = found["2"]
    assert abs(start1 - 10) < 2 and abs(start2 - 20) < 2
    assert 20 < end1 - start1 < 27
