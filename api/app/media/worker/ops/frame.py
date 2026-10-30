"""frame: één beeld als JPEG op de pipe van de app (§3.3). Van M1.

Voor de previews van video's (render.py) en een ingebedde hoes:

    nice -n 15 ffmpeg {invoer, met -ss T vóór -i} -map 0:{V} -frames:v 1 -vf "scale={W}:-2"
      -c:v mjpeg -q:v 3 -f image2pipe pipe:1

{invoer} is die van elke invoer (§3.3): -protocol_whitelist fd,pipe, -format_whitelist met de
witte lijst, -max_pixels en -max_alloc, -f {demuxer} -fd 3 -i fd:. De JPEG gaat rechtstreeks
naar de schrijfkant van de app (stdout = pipe:1); de worker leest hem niet. Ligt T voorbij het
einde, dan schrijft ffmpeg niets en stopt hij met 0; het antwoord zegt dan frames 0, en de app
probeert een eerder punt.
"""

from __future__ import annotations

import asyncio

from app.media.wire import Code
from app.media.worker import guard
from app.media.worker.args import THREADS, FrameArgs
from app.media.worker.ops.probe import WHITELIST
from app.media.worker.server import Context, OpError

LIMIT_S = 30.0


def argv(args: FrameArgs) -> list[str]:
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2",
        "-nostats", "-max_alloc", "536870912", "-protocol_whitelist", "fd,pipe",
        "-format_whitelist", WHITELIST, "-max_pixels", "35389440", "-threads", str(THREADS),
        "-ss", f"{args.at_s:.3f}", "-f", args.demuxer, "-fd", "3", "-i", "fd:",
        "-map", f"0:{args.video_index}", "-frames:v", "1", "-vf", f"scale={args.width}:-2",
        "-c:v", "mjpeg", "-q:v", "3", "-f", "image2pipe", "pipe:1",
    ]  # fmt: skip


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, FrameArgs)
    child = ctx.spawn(argv(ctx.args), inputs=ctx.inputs[:1], stdout=ctx.outputs[0])
    try:
        async with asyncio.timeout(LIMIT_S):
            done = await child.wait()
    except TimeoutError:
        raise OpError(Code.TIMEOUT, f"frame duurde langer dan {LIMIT_S:g} s") from None
    if done.rc != 0:
        reason = guard.why(child.pid)
        if reason:
            raise OpError(Code.FAILED, reason)
        raise OpError(Code.UNREADABLE, child.tail() or f"ffmpeg stopte met {done.rc}")
    frames = child._progress.get(b"frame", b"")  # de laatste regel van -progress
    return {
        "at_s": ctx.args.at_s,
        "width": ctx.args.width,
        "frames": int(frames) if frames.isdigit() else None,
    }
