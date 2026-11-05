"""De commando's van §3.3 (bouwontwerp fase 5), zoals de worker ze uit argumenten bouwt.

Letterlijk de regels van §3.3; `{…}` vult de spike hier zelf in. Waar §3.3 een keuze open laat,
staat het erbij:

- `-ss` staat direct vóór `-i fd:` ("vóór -i");
- VP9 vervangt het blok van x264 (`-c:v libx264` tot en met `-level:v`) door de regel van
  §3.3 en laat `-forced-idr 1` weg. `-threads 4`, `-sc_threshold 0`, `-g 100000` en
  `-force_key_frames` blijven;
- de meting (`bench`) en het maken van HEVC10 zijn de regels van §3.3 zonder iets erbij.
"""

from __future__ import annotations

from gedeeld import getal

# render.container plus mp3, flac en aac (Keuze 12)
FORMATEN = "mov,matroska,avi,wav,flv,ogg,asf,mpegts,mpeg,mp3,flac,aac"
MOVFLAGS = (
    "+frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont+negative_cts_offsets"
)
TONEMAP = (
    "zscale=w={W}:h={H}:tin={tin}:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100,"
    "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
DEINTERLACE = "bwdif=mode=send_frame:parity=auto:deint=interlaced"


def invoer(demuxer: str, start_s: float = 0.0) -> list[str]:
    """Elke invoer (§3.3), met `-ss` als er een sprong is."""
    argv = [
        "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2", "-nostats",
        "-max_alloc", "536870912",
        "-protocol_whitelist", "fd,pipe", "-format_whitelist", FORMATEN,
        "-max_pixels", "35389440", "-f", demuxer, "-fd", "3",
    ]  # fmt: skip
    if start_s > 0:
        argv += ["-ss", getal(start_s)]
    return argv + ["-i", "fd:"]


def staart(bron_start_s: float) -> list[str]:
    """Het eind van stream: tijden, fMP4 naar pipe:1."""
    return [
        "-output_ts_offset", getal(1 - bron_start_s), "-avoid_negative_ts", "disabled",
        "-f", "mp4", "-movflags", MOVFLAGS, "-fflags", "+bitexact", "pipe:1",
    ]  # fmt: skip


def transcode(
    demuxer: str,
    start_s: float,
    bron_start_s: float,
    video: int,
    audio: int | None,
    *,
    w: int,
    h: int,
    preset: str = "veryfast",
    maxrate_k: int = 3500,
    level: str = "3.1",
    codec: str = "libx264",
    deinterlace: bool = False,
    tonemap: str | None = None,
    threads: bool = True,
) -> list[str]:
    """Transcode vanaf 0 of na een sprong naar segment k (start_s = 2k). `threads=False`
    laat `-threads 4` weg (alleen voor de vergelijking van S10)."""
    argv = ["nice", "-n", "5", "ffmpeg", *invoer(demuxer, start_s), "-copyts", "-map", f"0:{video}"]
    if audio is not None:
        argv += ["-map", f"0:{audio}"]
    argv += ["-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1"]
    if tonemap:
        tin = "arib-std-b67" if tonemap == "hlg" else "smpte2084"
        vf = TONEMAP.format(W=w, H=h, tin=tin)
    else:
        vf = f"scale=w={w}:h={h}:flags=bicubic,format=yuv420p"
    if deinterlace:
        vf = f"{DEINTERLACE},{vf}"
    argv += ["-vf", vf, "-fpsmax", "60"]
    draden = ["-threads", "4"] if threads else []
    if codec == "libx264":
        argv += [
            "-c:v", "libx264", "-preset", preset, "-crf", "23",
            "-maxrate", f"{maxrate_k}k", "-bufsize", f"{2 * maxrate_k}k",
            "-profile:v", "high", "-level:v", level, *draden, "-sc_threshold", "0",
            "-g", "100000", "-force_key_frames", "expr:gte(t,n_forced*2)", "-forced-idr", "1",
        ]  # fmt: skip
    elif codec == "libvpx-vp9":
        argv += [
            "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
            "-b:v", f"{maxrate_k}k", "-maxrate", f"{maxrate_k}k", "-crf", "32",
            *draden, "-sc_threshold", "0", "-g", "100000",
            "-force_key_frames", "expr:gte(t,n_forced*2)",
        ]  # fmt: skip
    else:
        raise ValueError(codec)
    if audio is not None:
        if codec == "libvpx-vp9":
            argv += ["-c:a", "libopus", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
        else:
            argv += ["-c:a", "aac", "-ac", "2", "-b:a", "160k"]
    if tonemap:
        argv += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    return argv + staart(bron_start_s)


def remux(
    demuxer: str,
    start_s: float,
    bron_start_s: float,
    video: int,
    audio: int | None,
    *,
    hvc1: bool = False,
    audio_codec: str = "copy",
) -> list[str]:
    """Remux: video kopiëren, audio kopiëren of omzetten (start_s = b_k − 0,5 na een sprong)."""
    argv = ["ffmpeg", *invoer(demuxer, start_s), "-copyts", "-map", f"0:{video}"]
    if audio is not None:
        argv += ["-map", f"0:{audio}"]
    argv += ["-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1", "-c:v", "copy"]
    if hvc1:
        argv += ["-tag:v", "hvc1"]
    if audio is not None:
        argv += {
            "copy": ["-c:a", "copy"],
            "aac": ["-c:a", "aac", "-ac", "2", "-b:a", "160k"],
            "opus": ["-c:a", "libopus", "-b:a", "128k", "-ac", "2", "-ar", "48000"],
        }[audio_codec]
    return argv + staart(bron_start_s)


def probe(demuxer: str) -> list[str]:
    """De probe via fd 3 (Keuze 15): ffprobe met dezelfde witte lijsten."""
    return [
        "ffprobe", "-v", "error", "-protocol_whitelist", "fd,pipe", "-format_whitelist", FORMATEN,
        "-f", demuxer, "-fd", "3", "-show_format", "-show_streams", "-of", "json", "-i", "fd:",
    ]  # fmt: skip


# De meting (Keuze 26), zonder invoer: letterlijk §3.3
BENCH = [
    "ffmpeg", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t",
    "-t", "10", "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast", "-threads", "4",
    "-f", "null", "-",
]  # fmt: skip
BENCH_MEDIA_S = 10.0
HEVC10_MAAK = [
    "ffmpeg", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24", "-t", "4",
    "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-f", "hevc", "pipe:1",
]  # fmt: skip
HEVC10_DECODEER = ["ffmpeg", "-f", "hevc", "-i", "pipe:0", "-f", "null", "-"]
HEVC10_MEDIA_S = 4.0
# De referenties van Keuze 26: cores voor 1,0× in de sandbox
REF_BENCH = 0.74
REF_DEC = 0.31
