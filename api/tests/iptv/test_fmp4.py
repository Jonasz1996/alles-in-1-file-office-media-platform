"""De knipper en de codecs, op echte uitvoer van ops/live.py (ffmpeg op een testbeeld)."""

import shutil
import subprocess

import pytest

from app.iptv import fmp4
from app.media.worker.args import AudioEncode, LiveArgs
from app.media.worker.ops import live

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="geen ffmpeg")


def _ts(seconds: int, codec: str) -> bytes:
    return subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x240:rate=25:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            codec,
            "-g",
            "25",
            "-c:a",
            "mp2",
            "-f",
            "mpegts",
            "pipe:1",
        ],  # fmt: skip
        check=True,
        capture_output=True,
    ).stdout


def _live(ts: bytes, video: str) -> bytes:
    args = LiveArgs(video=video, audio=AudioEncode(codec="aac"))
    argv = live.argv(args)
    # De worker geeft de pipe als fd 3; hier stdin, met dezelfde rest van de argv
    i = argv.index("-fd")
    argv = [*argv[:i], *argv[i + 2 :]]
    argv[argv.index("fd:")] = "pipe:0"
    argv[argv.index("fd,pipe")] = "pipe"
    return subprocess.run(argv, input=ts, check=True, capture_output=True).stdout


def test_knippen_en_codecs():
    out = _live(_ts(4, "libx264"), "copy")
    splitter = fmp4.Splitter()
    fragments = []
    for i in range(0, len(out), 7000):  # in stukken, zoals uit een pipe
        fragments += splitter.feed(out[i : i + 7000])
    assert splitter.init is not None
    assert fmp4.video_codec(splitter.init) == "avc1"
    codecs = fmp4.codecs(splitter.init)
    assert codecs.startswith("avc1.") and codecs.endswith(",mp4a.40.2")
    assert len(fragments) >= 3
    assert all(f[4:8] == b"moof" for f in fragments)


def test_mpeg2_wordt_omgezet():
    out = _live(_ts(2, "mpeg2video"), "copy")
    splitter = fmp4.Splitter()
    splitter.feed(out)
    assert splitter.init is not None
    assert fmp4.video_codec(splitter.init) not in ("avc1", "avc3")
    out = _live(_ts(2, "mpeg2video"), "h264")
    splitter = fmp4.Splitter()
    splitter.feed(out)
    assert fmp4.video_codec(splitter.init) == "avc1"
