"""remux_audio: één nummer naar fMP4 op de pipe van de app, voor gapless (§3.3, Keuze 38). Van M9.

De argv is die van §3.3, uit gevalideerde argumenten (er gaat nooit een vrije string naar
ffmpeg):

    ffmpeg {invoer} -map 0:a:N -vn -sn -dn -map_metadata -1 -map_chapters -1 -c:a {copy | flac}
      -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof -frag_duration 10000000
      -use_editlist 0 pipe:1

- `copy` voor FLAC en AAC, `flac` voor Opus, ALAC, WAV en de rest (spike S8: Opus in fMP4 is in
  Chromium 141 niet gapless, via FLAC wel exact, met delay 0). De app kiest;
- `-use_editlist 0`: de browser krijgt alle samples, ook de voorloop; de speler knipt zelf met
  appendWindow (gapless.ts);
- `-map_chapters -1` staat niet in §3.3: hoofdstukken (de CUESHEET van een FLAC) zouden een
  tekstspoor in de init kunnen geven, en een SourceBuffer met alleen geluid weigert dat. Het
  geluid zelf verandert er niet door;
- de invoer is die van stream (`_input`): de witte lijsten, `-max_alloc`, de fd van de app.

stdout is de schrijfkant van de app (pipe:1); de tegendruk is die van de pipe. Hooguit elke
PROGRESS_S een `progress` met de tijd van ffmpeg.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.media.wire import Code
from app.media.worker import guard
from app.media.worker.args import RemuxAudioArgs
from app.media.worker.ops.stream import PROGRESS_S, _input
from app.media.worker.server import Context, OpError

MOVFLAGS = "+frag_keyframe+empty_moov+default_base_moof"
FRAG_DURATION_US = 10_000_000


def argv(args: RemuxAudioArgs) -> list[str]:
    """De argv van §3.3 voor deze argumenten, zonder het voorvoegsel nice (dat zet
    Context.spawn met args.background)."""
    return [
        "ffmpeg", *_input(args.demuxer, 3, 0),
        "-map", f"0:a:{args.audio}", "-vn", "-sn", "-dn",
        "-map_metadata", "-1", "-map_chapters", "-1", "-c:a", args.codec,
        "-f", "mp4", "-movflags", MOVFLAGS, "-frag_duration", str(FRAG_DURATION_US),
        "-use_editlist", "0", "pipe:1",
    ]  # fmt: skip


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, RemuxAudioArgs)
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
    return {"cpu_s": ended.cpu_s, "max_rss_kb": ended.max_rss_kb}
