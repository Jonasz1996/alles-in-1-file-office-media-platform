"""live: live tv (IPTV) van een pipe naar fMP4 op de pipe van de app.

De app haalt de stroom van de aanbieder (het adres met de logins blijft in de app) en schrijft
de bytes in een pipe; de worker krijgt alleen de leeskant (fd 3, als `extra` aan run.spawn:
een pipe kan niet terug naar positie 0). ffmpeg leest MPEG-TS, kopieert het beeld (H.264) of
zet het om, en maakt van het geluid altijd AAC. De uitvoer is fMP4 met een fragment per
keyframe: de app deelt hem met elke kijker van het kanaal, en een nieuwe kijker begint bij
de init en het laatste fragment (een fragment begint altijd met een keyframe). Bij omzetten
komt er elke 2 s een keyframe.

Een kapotte of onderbroken bron (pakketverlies, een nieuwe tijdlijn) mag ffmpeg niet laten
stoppen: `+discardcorrupt`, `+genpts` en `-err_detect ignore_err`. Stopt de bron, dan krijgt
ffmpeg EOF en stopt hij ook; de app beslist of hij opnieuw begint.
"""

from __future__ import annotations

from app.media.wire import Code
from app.media.worker import guard
from app.media.worker.args import THREADS, LiveArgs
from app.media.worker.ops.probe import WHITELIST
from app.media.worker.server import Context, OpError

MOVFLAGS = "+frag_keyframe+empty_moov+default_base_moof+skip_trailer"
DEINTERLACE = "bwdif=mode=send_frame:parity=auto:deint=interlaced"


def argv(args: LiveArgs) -> list[str]:
    out = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats",
        "-max_alloc", "536870912", "-protocol_whitelist", "fd,pipe",
        "-format_whitelist", WHITELIST, "-max_pixels", "35389440",
        "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
        "-probesize", "5000000", "-analyzeduration", "3000000",
        "-f", "mpegts", "-fd", "3", "-i", "fd:",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
    ]  # fmt: skip
    if args.video == "h264":
        rate = args.maxrate_k
        chain = f"{DEINTERLACE},scale=w=-2:h='min({args.height},ih)':flags=bicubic,format=yuv420p"
        out += [
            "-vf", chain, "-fpsmax", "50",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "23",
            "-maxrate", f"{rate}k", "-bufsize", f"{2 * rate}k",
            "-profile:v", "high", "-level:v", "4.2", "-threads", str(THREADS),
            "-sc_threshold", "0", "-g", "100000",
            "-force_key_frames", "expr:gte(t,n_forced*2)", "-forced-idr", "1",
        ]  # fmt: skip
    else:
        out += ["-c:v", "copy"]
    audio = args.audio
    if audio.codec == "aac":
        out += ["-c:a", "aac", "-ac", str(audio.channels), "-b:a", f"{audio.bitrate_k}k"]
    else:
        out += ["-c:a", "libopus", "-ac", str(audio.channels), "-b:a", f"{audio.bitrate_k}k"]
        out += ["-ar", "48000"]
    return out + ["-f", "mp4", "-movflags", MOVFLAGS, "-fflags", "+bitexact", "pipe:1"]


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, LiveArgs)
    child = ctx.spawn(argv(ctx.args), extra=ctx.inputs, stdout=ctx.outputs[0])
    ended = await child.wait()
    if ended.rc != 0:
        reason = guard.why(child.pid)
        raise OpError(Code.FAILED, reason or child.tail() or f"ffmpeg stopte met {ended.rc}")
    return {"cpu_s": ended.cpu_s, "max_rss_kb": ended.max_rss_kb}
