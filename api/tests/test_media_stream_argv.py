"""De argv van stream (M4, §3.3): golden files, gelijk aan de commando's van de spike waar §3.3
niets toevoegt, en de aanvullingen van M4 (MPEG-TS, geluid kopiëren). Zonder ffmpeg."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from app.media.worker import args as A
from app.media.worker.ops import stream as S

SPIKE = Path(__file__).parent / "media" / "spike"
WL = "mov,matroska,avi,wav,flv,ogg,asf,mpegts,mpeg,mp3,flac,aac"
IN = ["-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2", "-nostats",
      "-max_alloc", "536870912", "-protocol_whitelist", "fd,pipe", "-format_whitelist", WL,
      "-max_pixels", "35389440"]  # fmt: skip
TAIL = ["-avoid_negative_ts", "disabled", "-f", "mp4", "-movflags", S.MOVFLAGS,
        "-fflags", "+bitexact", "pipe:1"]  # fmt: skip


@pytest.fixture
def spike(monkeypatch):
    monkeypatch.syspath_prepend(str(SPIKE))
    sys.modules.pop("commandos", None)
    return importlib.import_module("commandos")


def x264(**kw) -> A.VideoEncode:
    base = {"codec": "libx264", "width": 1280, "height": 720, "level": "3.1", "maxrate_k": 3500}
    return A.VideoEncode(**{**base, **kw})


AAC = A.AudioEncode(codec="aac", channels=2, bitrate_k=160)
OPUS = A.AudioEncode(codec="libopus", channels=2, bitrate_k=128)


def transcode(**kw) -> A.StreamArgs:
    base = {"mode": "transcode", "demuxer": "matroska", "source_start_s": -0.021,
            "video_index": 0, "audio_index": 1, "video": x264(), "audio": AAC,
            "grid": "2s"}  # fmt: skip
    return A.StreamArgs(**{**base, **kw})


def remux(**kw) -> A.StreamArgs:
    base = {"mode": "remux", "demuxer": "matroska", "source_start_s": -0.021,
            "video_index": 0, "audio_index": 1, "video": "copy", "audio": "copy"}  # fmt: skip
    return A.StreamArgs(**{**base, **kw})


def test_golden_transcode_vanaf_0():
    assert S.argv(transcode()) == [
        "ffmpeg", *IN, "-f", "matroska", "-fd", "3", "-i", "fd:", "-copyts",
        "-map", "0:0", "-map", "0:1", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
        "-vf", "scale=w=1280:h=720:flags=bicubic,format=yuv420p", "-fpsmax", "60",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-maxrate", "3500k",
        "-bufsize", "7000k", "-profile:v", "high", "-level:v", "3.1", "-threads", "4",
        "-sc_threshold", "0", "-g", "100000", "-force_key_frames", "expr:gte(t,n_forced*2)",
        "-forced-idr", "1", "-c:a", "aac", "-ac", "2", "-b:a", "160k",
        "-output_ts_offset", "1.021", *TAIL,
    ]  # fmt: skip


@pytest.mark.parametrize("start", [0.0, 10.021, 3599.979])
def test_transcode_gelijk_aan_de_spike(spike, start):
    """§3.3 is de argv van de spike (S1/S2 op 7.1.5), zonder het voorvoegsel nice: dat zet
    Context.spawn (args.niceness)."""
    got = S.argv(transcode(start_s=start))
    want = spike.transcode("matroska", start, -0.021, 0, 1, w=1280, h=720)
    assert want[:3] == ["nice", "-n", "5"]
    assert got == want[3:]


def test_vp9_tonemap_en_deinterlace_gelijk_aan_de_spike(spike):
    video = A.VideoEncode(codec="libvpx-vp9", width=1280, height=720, maxrate_k=3500,
                          tonemap="hlg", deinterlace=True)  # fmt: skip
    got = S.argv(transcode(video=video, audio=OPUS, start_s=4.021))
    want = spike.transcode("matroska", 4.021, -0.021, 0, 1, w=1280, h=720, codec="libvpx-vp9",
                           tonemap="hlg", deinterlace=True)  # fmt: skip
    assert got == want[3:]


def test_remux_met_omzetten_gelijk_aan_de_spike(spike):
    got = S.argv(remux(audio=AAC, hvc1=True, start_s=5.521))
    want = spike.remux("matroska", 5.521, -0.021, 0, 1, hvc1=True, audio_codec="aac")
    assert got == want


def test_remux_kopieert_het_geluid_met_delay_moov_en_zonder_elst(spike):
    got = S.argv(remux())
    want = spike.remux("matroska", 0, -0.021, 0, 1)
    tail = got[got.index("-output_ts_offset") :]
    assert tail == ["-output_ts_offset", "1.021", "-avoid_negative_ts", "disabled",
                    "-use_editlist", "0", "-f", "mp4", "-movflags", S.MOVFLAGS_COPY,
                    "-fflags", "+bitexact", "pipe:1"]  # fmt: skip
    assert "+delay_moov" in S.MOVFLAGS_COPY and "+empty_moov" in S.MOVFLAGS_COPY
    # De rest is die van de spike
    assert got[: got.index("-output_ts_offset")] == want[: want.index("-output_ts_offset")]


def test_zonder_audio_geen_map_en_geen_codec():
    got = S.argv(transcode(audio_index=None, audio="none"))
    assert got.count("-map") == 1 and "-c:a" not in got and "-af" not in got


def test_inbranden_met_een_tweede_fd():
    got = S.argv(transcode(video=x264(burn_index=3, burn_start_s=12.5), start_s=42.021))
    first = got.index("-i")
    assert got[first - 4 : first + 2] == ["-fd", "3", "-ss", "42.021", "-i", "fd:"]
    second = got.index("-i", first + 1)
    assert got[second - 4 : second + 2] == ["-fd", "4", "-ss", "12.5", "-i", "fd:"]
    graph = got[got.index("-filter_complex") + 1]
    assert graph == (
        "[0:0]scale=w=1280:h=720:flags=bicubic,format=yuv420p[v];[1:3]scale=1280:720[s];"
        "[v][s]overlay=eof_action=pass,format=yuv420p[out]"
    )
    assert got[got.index("-filter_complex") + 2 : got.index("-filter_complex") + 6] == [
        "-map", "[out]", "-map", "0:1",
    ]  # fmt: skip
    assert "-vf" not in got and got.count("-fpsmax") == 1


def test_mpegts_begint_eerder_en_knipt_op_de_grens():
    """Keuze 22: TS zoekt niet precies. -ss T − 5, trim en atrim op T + S (brontijd)."""
    args = transcode(demuxer="mpegts", source_start_s=1.458667, start_s=4.021,
                     video=x264(deinterlace=True))  # fmt: skip
    got = S.argv(args)
    assert "-ss" not in got  # 4,021 − 5 < 0: vanaf het begin lezen
    vf = got[got.index("-vf") + 1]
    assert vf.startswith("trim=start=5.479667,bwdif=")
    assert got[got.index("-af") + 1] == "atrim=start=5.479667"
    later = S.argv(args.model_copy(update={"start_s": 64.021}))
    assert later[later.index("-ss") + 1] == "59.021"
    assert later[later.index("-vf") + 1].startswith("trim=start=65.479667,")
    # Vanaf 0: niets extra
    first = S.argv(args.model_copy(update={"start_s": 0}))
    assert "-ss" not in first and "trim" not in " ".join(first)


def test_mpegts_remux_begint_alleen_eerder():
    got = S.argv(remux(demuxer="mpegts", audio=AAC, start_s=33.5))
    assert got[got.index("-ss") + 1] == "28.5"
    assert "trim" not in " ".join(got)


def test_een_andere_bron_gaat_precies():
    for demuxer in ("matroska", "mov", "avi"):
        got = S.argv(transcode(demuxer=demuxer, start_s=8.021))
        assert got[got.index("-ss") + 1] == "8.021" and "trim" not in " ".join(got)


def test_number_zoals_de_spike(spike):
    for x in (0, 1.021, -0.021, 1 - 1.458667, 3599.9999999, 10.5):
        assert S.number(x) == spike.getal(x)
