"""fingerprint: de vingerafdruk (Chromaprint) van het begin van een audiospoor (fase 11, stap 7).

Voor intro overslaan (app/media/intro.py):

    nice -n 15 ionice -c 3 ffmpeg {invoer} -map 0:a:{N} -t {S} -ac 1 -f chromaprint
      -fp_format raw pipe:1

{invoer} is die van elke invoer (§3.3, zoals frame). De punten (uint32, little-endian) gaan
rechtstreeks naar de schrijfkant van de app; de worker leest ze niet. Zonder dat audiospoor stopt
ffmpeg met een fout: unreadable.
"""

from __future__ import annotations

import asyncio

from app.media.wire import Code
from app.media.worker import guard
from app.media.worker.args import THREADS, FingerprintArgs
from app.media.worker.ops.probe import WHITELIST
from app.media.worker.server import Context, OpError

LIMIT_S = 280.0


def argv(args: FingerprintArgs) -> list[str]:
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2",
        "-nostats", "-max_alloc", "536870912", "-protocol_whitelist", "fd,pipe",
        "-format_whitelist", WHITELIST, "-threads", str(THREADS),
        "-f", args.demuxer, "-fd", "3", "-i", "fd:",
        "-map", f"0:a:{args.audio_index}", "-t", str(args.seconds), "-vn", "-sn", "-dn",
        "-ac", "1", "-f", "chromaprint", "-fp_format", "raw", "pipe:1",
    ]  # fmt: skip


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, FingerprintArgs)
    child = ctx.spawn(argv(ctx.args), inputs=ctx.inputs[:1], stdout=ctx.outputs[0])
    try:
        async with asyncio.timeout(LIMIT_S):
            done = await child.wait()
    except TimeoutError:
        raise OpError(Code.TIMEOUT, f"fingerprint duurde langer dan {LIMIT_S:g} s") from None
    if done.rc != 0:
        reason = guard.why(child.pid)
        if reason:
            raise OpError(Code.FAILED, reason)
        raise OpError(Code.UNREADABLE, child.tail() or f"ffmpeg stopte met {done.rc}")
    return {"seconds": ctx.args.seconds}
