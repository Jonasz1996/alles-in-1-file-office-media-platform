"""synth: een bron voor de belastingtest (§3.3 en §4.6). Van M10.

Alleen uit de vaste lijst: SynthArgs laat alleen testsrc2 of cellauto toe, een seed 0–9, een duur
van 30–600 s en een hoogte van 1080 of 720; een andere naam of een extra parameter weigert de
worker al bij het lezen van de vraag (bad_args). Zonder --loadtest (PLATFORM_MEDIA_LOADTEST)
weigert de server synth helemaal (not_allowed). Er gaat nooit een vrije string naar ffmpeg.

De argv is die van §3.3:

    nice -n 15 ionice -c 3 ffmpeg -f lavfi -i "{testsrc2=… | cellauto=…},noise=alls=20:allf=t"
      -f lavfi -i "sine=frequency=440:sample_rate=48000" -t {duur}
      -c:v libx264 -preset ultrafast -b:v 6M -maxrate 6M -bufsize 12M -threads 3
      -c:a aac -b:a 160k -f matroska -fd 3 fd:

- de uitvoer is de fd van de app (een gewoon bestand in media/loadtest/ dat de app opende; de
  server kijkt dat na). De worker heeft dus geen schrijfbare map nodig. Het kind krijgt hem op
  fd 3 (run.spawn zet extra fd's op 3, 4, …); §3.3 schreef `-fd 4`, maar de plek volgt de
  indeling van run.spawn. Het fd-protocol kan zoeken, dus de mkv krijgt Cues (V8);
- testsrc2 heeft geen seed: daar zit de seed in de ruis (alls=20+seed), zodat twee bronnen nooit
  gelijk zijn;
- RLIMIT_FSIZE is hier niet 0 (de enige opdracht die schrijft): de duur × (6 Mbit/s + 160 kbit/s)
  met marge, zodat een fout nooit de schijf vult;
- het voorvoegsel nice en ionice komt van Context.spawn (args.niceness: achtergrond).
"""

from __future__ import annotations

import asyncio
import contextlib

from app.media.wire import Code
from app.media.worker import guard
from app.media.worker.args import SynthArgs
from app.media.worker.ops.stream import PROGRESS_S
from app.media.worker.server import Context, OpError

VIDEO_KBPS = 6000
AUDIO_KBPS = 160
SIZES = {1080: "1920x1080", 720: "1280x720"}
OUT_FD = 3  # de eerste extra fd van run.spawn


def lavfi(args: SynthArgs) -> str:
    size = SIZES[args.height]
    if args.source == "testsrc2":
        return f"testsrc2=size={size}:rate=24,noise=alls={20 + args.seed}:allf=t"
    return f"cellauto=s={size}:rate=24:rule=110:random_seed={args.seed},noise=alls=20:allf=t"


def argv(args: SynthArgs) -> list[str]:
    """De argv van §3.3 voor deze argumenten, zonder het voorvoegsel (dat zet Context.spawn)."""
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-progress", "pipe:2", "-nostats",
        "-f", "lavfi", "-i", lavfi(args),
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(args.duration_s),
        "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "6M", "-maxrate", "6M",
        "-bufsize", "12M", "-threads", "3",
        "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k",
        "-f", "matroska", "-fd", str(OUT_FD), "fd:",
    ]  # fmt: skip


def size_limit(args: SynthArgs) -> int:
    """RLIMIT_FSIZE: de bitrate × de duur, × 1,5 en 16 MB voor de container en de piek."""
    payload = (VIDEO_KBPS + AUDIO_KBPS) * 1000 // 8 * args.duration_s
    return payload * 3 // 2 + 16 * 1024 * 1024


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, SynthArgs)
    latest: list[tuple[int, float | None]] = []

    def progress(t_ms: int, speed: float | None) -> None:
        latest[:] = [(t_ms, speed)]

    child = ctx.spawn(
        argv(ctx.args), extra=ctx.outputs, fsize=size_limit(ctx.args), on_progress=progress
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
    return {
        "source": ctx.args.source,
        "seed": ctx.args.seed,
        "duration_s": ctx.args.duration_s,
        "height": ctx.args.height,
        "cpu_s": ended.cpu_s,
        "max_rss_kb": ended.max_rss_kb,
    }
