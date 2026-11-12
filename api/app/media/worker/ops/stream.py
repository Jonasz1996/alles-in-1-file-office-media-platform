"""stream: remux of transcode naar fMP4 op de pipe van de app (§3.3, Keuze 22–27). Van M4.

De argv is die van §3.3 (en van de spike, tests/media/spike/commandos.py), uit gevalideerde
argumenten; er gaat nooit een vrije string naar ffmpeg. Drie aanvullingen, elk gemeten
(docs/fase-5/m4.md):

- **MPEG-TS en MPEG-PS zoeken niet precies** (spike punt 11, Keuze 22): na `-ss T` begint de
  uitvoer pas op het volgende keyframe van de bron. Daarom begint de worker bij die demuxers
  `TS_LEAD_S` eerder (`-ss max(0, T − 5)`), en knipt een transcode het begin weg met
  `trim=start=(T + S)` als eerste filter voor het beeld en `atrim=start=(T + S)` voor het geluid
  (met `-copyts` tellen de filters in brontijd). Zo begint het beeld op de grens, en blijft het
  rooster gelijk (`t` van `-force_key_frames` telt vanaf het eerste beeld dat uit de filters
  komt). Bij remux gooit de app weg wat vóór `b_k` ligt (Keuze 23); de worker begint alleen
  eerder. Een GOP boven 5 s kan zo nog te laat beginnen: de producent neemt dan het eerste
  fragment dat er is;
- **geluid kopiëren** zet `+delay_moov` en `-use_editlist 0`: AC-3 en E-AC-3 halen hun
  `dac3`/`dec3` uit het eerste pakket, en met alleen `empty_moov` weigert de muxer ("Cannot
  write moov atom before AC3 packets"). `-use_editlist 0` houdt de `elst` uit de init die
  `delay_moov` anders schrijft; met beide is de uitvoer voor AAC byte voor byte gelijk aan die
  zonder. AAC uit MPEG-TS (ADTS) kopieert de app niet: de muxer weigert ook dan, en een
  transcode van TS knipt het geluid met `atrim` (streams.py zet het om);
- **voortgang**: hooguit elke PROGRESS_S een `progress` met de tijd van ffmpeg.

stdout van ffmpeg is de schrijfkant van de app (pipe:1); de worker leest de video niet. De
tegendruk is die van de pipe: leest de app niet, dan wacht ffmpeg.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.media.wire import Code
from app.media.worker import guard
from app.media.worker.args import THREADS, AudioEncode, StreamArgs, VideoEncode
from app.media.worker.ops.probe import WHITELIST
from app.media.worker.server import Context, OpError

MOVFLAGS = (
    "+frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont+negative_cts_offsets"
)
MOVFLAGS_COPY = MOVFLAGS.replace("+empty_moov", "+empty_moov+delay_moov")
TONEMAP = (
    "zscale=w={W}:h={H}:tin={tin}:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100,"
    "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
DEINTERLACE = "bwdif=mode=send_frame:parity=auto:deint=interlaced"
# De demuxers zonder index: zoeken landt op het volgende keyframe (spike punt 11)
IMPRECISE = frozenset({"mpegts", "mpeg"})
TS_LEAD_S = 5.0
PROGRESS_S = 2.0
TRANSFER = {"pq": "smpte2084", "hlg": "arib-std-b67"}


def number(x: float) -> str:
    """Een tijd voor de argv: hooguit 6 decimalen, zonder nullen aan het eind (zoals de spike)."""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _input(demuxer: str, fd: int, start_s: float) -> list[str]:
    argv = [
        "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2", "-nostats",
        "-max_alloc", "536870912", "-protocol_whitelist", "fd,pipe",
        "-format_whitelist", WHITELIST, "-max_pixels", "35389440",
        "-f", demuxer, "-fd", str(fd),
    ]  # fmt: skip
    if start_s > 0:
        argv += ["-ss", number(start_s)]
    return argv + ["-i", "fd:"]


def seek_s(args: StreamArgs) -> float:
    """Waar de invoer begint: start_s, of bij MPEG-TS/PS TS_LEAD_S eerder."""
    if args.demuxer in IMPRECISE and args.start_s > 0:
        return max(0.0, args.start_s - TS_LEAD_S)
    return args.start_s


def trim_at(args: StreamArgs) -> float | None:
    """De brontijd waar een transcode van MPEG-TS/PS het begin wegknipt, of None."""
    if args.mode != "transcode" or args.demuxer not in IMPRECISE or args.start_s <= 0:
        return None
    return args.start_s + args.source_start_s


def video_filters(video: VideoEncode, trim: float | None) -> str:
    """De keten voor het beeld: [trim,] [bwdif,] schalen of de tonemap van Keuze 27."""
    if video.tonemap is not None:
        chain = TONEMAP.format(W=video.width, H=video.height, tin=TRANSFER[video.tonemap])
    else:
        chain = f"scale=w={video.width}:h={video.height}:flags=bicubic,format=yuv420p"
    if video.deinterlace:
        chain = f"{DEINTERLACE},{chain}"
    if trim is not None:
        chain = f"trim=start={number(trim)},{chain}"
    return chain


def _encoder(video: VideoEncode) -> list[str]:
    rate = video.maxrate_k
    if video.codec == "libx264":
        assert video.level is not None
        return [
            "-c:v", "libx264", "-preset", video.preset, "-crf", "23",
            "-maxrate", f"{rate}k", "-bufsize", f"{2 * rate}k",
            "-profile:v", "high", "-level:v", video.level, "-threads", str(THREADS),
            "-sc_threshold", "0", "-g", "100000",
            "-force_key_frames", "expr:gte(t,n_forced*2)", "-forced-idr", "1",
        ]  # fmt: skip
    return [
        "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
        "-b:v", f"{rate}k", "-maxrate", f"{rate}k", "-crf", "32", "-threads", str(THREADS),
        "-sc_threshold", "0", "-g", "100000", "-force_key_frames", "expr:gte(t,n_forced*2)",
    ]  # fmt: skip


def _audio(audio: str | AudioEncode, trim: float | None) -> list[str]:
    if audio == "none":
        return []
    if audio == "copy":
        return ["-c:a", "copy"]
    assert isinstance(audio, AudioEncode)
    out = ["-af", f"atrim=start={number(trim)}"] if trim is not None else []
    if audio.codec == "aac":
        return [*out, "-c:a", "aac", "-ac", str(audio.channels), "-b:a", f"{audio.bitrate_k}k"]
    return [
        *out, "-c:a", "libopus", "-b:a", f"{audio.bitrate_k}k", "-ac", str(audio.channels),
        "-ar", "48000",
    ]  # fmt: skip


def _tail(args: StreamArgs) -> list[str]:
    copy = args.audio == "copy"
    return [
        "-output_ts_offset", number(1 - args.source_start_s), "-avoid_negative_ts", "disabled",
        *(["-use_editlist", "0"] if copy else []),
        "-f", "mp4", "-movflags", MOVFLAGS_COPY if copy else MOVFLAGS,
        "-fflags", "+bitexact", "pipe:1",
    ]  # fmt: skip


def argv(args: StreamArgs) -> list[str]:
    """De argv van §3.3 voor deze argumenten, zonder het voorvoegsel nice (dat zet
    Context.spawn met args.niceness)."""
    out = ["ffmpeg", *_input(args.demuxer, 3, seek_s(args))]
    video = args.video
    trim = trim_at(args)
    audio_map = ["-map", f"0:{args.audio_index}"] if args.audio_index is not None else []
    drop = ["-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1"]
    if isinstance(video, VideoEncode) and video.burn_index is not None:
        # Twee invoeren van dezelfde blob (spike punt 14): de ondertitel vanaf burn_start_s
        out += _input(args.demuxer, 4, video.burn_start_s)
        graph = (
            f"[0:{args.video_index}]{video_filters(video, trim)}[v];"
            f"[1:{video.burn_index}]scale={video.width}:{video.height}[s];"
            "[v][s]overlay=eof_action=pass,format=yuv420p[out]"
        )
        out += ["-copyts", "-filter_complex", graph, "-map", "[out]", *audio_map, *drop]
        out += ["-fpsmax", str(video.fps_max)]
    elif isinstance(video, VideoEncode):
        out += ["-copyts", "-map", f"0:{args.video_index}", *audio_map, *drop]
        out += ["-vf", video_filters(video, trim), "-fpsmax", str(video.fps_max)]
    else:
        out += ["-copyts", "-map", f"0:{args.video_index}", *audio_map, *drop, "-c:v", "copy"]
        if args.hvc1:
            out += ["-tag:v", "hvc1"]
    if isinstance(video, VideoEncode):
        out += _encoder(video)
    out += _audio(args.audio, trim)
    if isinstance(video, VideoEncode) and video.tonemap is not None:
        out += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    return out + _tail(args)


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, StreamArgs)
    latest: list[tuple[int, float | None]] = []

    def progress(t_ms: int, speed: float | None) -> None:
        latest[:] = [(t_ms, speed)]

    child = ctx.spawn(
        argv(ctx.args), inputs=ctx.inputs, stdout=ctx.outputs[0], on_progress=progress
    )
    waiter = asyncio.ensure_future(child.wait())
    try:
        sent: tuple[int, float | None] | None = None
        while True:
            done, _ = await asyncio.wait({waiter}, timeout=PROGRESS_S)
            if done:
                break
            if latest and latest[0] != sent:
                sent = latest[0]
                await ctx.progress(*sent)
        ended = waiter.result()
    finally:
        if not waiter.done():
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await waiter
    if ended.rc != 0:
        reason = guard.why(child.pid)
        raise OpError(Code.FAILED, reason or child.tail() or f"ffmpeg stopte met {ended.rc}")
    frames = child._progress.get(b"frame", b"")
    return {
        "frames": int(frames) if frames.isdigit() else None,
        "cpu_s": ended.cpu_s,
        "max_rss_kb": ended.max_rss_kb,
    }
