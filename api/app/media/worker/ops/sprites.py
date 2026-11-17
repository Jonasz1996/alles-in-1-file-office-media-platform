"""sprites: scrub-sprites voor de zoekbalk (§3.3, Keuze 30). M5.

    nice -n 15 ionice -c 3 ffmpeg -skip_frame nokey {invoer}
      -filter_complex "[0:{V}]fps=1/10,scale=160:-2,split=2[n][s];[s]tile=10x10[t]"
      -map "[n]" -f null -
      -map "[t]" -an -sn -dn -c:v mjpeg -q:v 5 -f image2pipe pipe:1

{invoer} is die van elke invoer (§3.3, zoals frame). Alleen keyframes worden gedecodeerd
(-skip_frame nokey), dus de beelden vallen op keyframes, hooguit een paar seconden naast de tik.
De tegels (JPEG's van 10×10 beelden van 160 breed) gaan rechtstreeks naar de schrijfkant van de
app (stdout = pipe:1); de worker leest ze niet. De app knipt ze (trick.split_jpegs).

Afwijking van het commando van Keuze 30 (m5.md): de beelden gaan met split ook naar een lege
uitvoer (null) die als eerste staat. Zo telt `frame=` van -progress de beelden en niet de
tegels, en weet de app precies hoeveel er in de laatste (halve) tegel staan; het filter fps
maakt er aan het eind soms één minder dan de duur doet verwachten.

Antwoord: {"count": beelden of None, "interval": 10, "width": 160, "cols": 10, "rows": 10}.
"""

from __future__ import annotations

from app.media.wire import Code
from app.media.worker import guard
from app.media.worker.args import THREADS, SpritesArgs
from app.media.worker.ops.probe import WHITELIST
from app.media.worker.ops.subs import close_fd
from app.media.worker.server import Context, OpError

INTERVAL = 10
WIDTH = 160
COLS = 10
ROWS = 10


def argv(args: SpritesArgs) -> list[str]:
    graph = (
        f"[0:{args.video_index}]fps=1/{INTERVAL},scale={WIDTH}:-2,split=2[n][s];"
        f"[s]tile={COLS}x{ROWS}[t]"
    )
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2",
        "-nostats", "-skip_frame", "nokey", "-max_alloc", "536870912",
        "-protocol_whitelist", "fd,pipe", "-format_whitelist", WHITELIST,
        "-max_pixels", "35389440", "-threads", str(THREADS),
        "-f", args.demuxer, "-fd", "3", "-i", "fd:",
        "-filter_complex", graph,
        "-map", "[n]", "-f", "null", "-",
        "-map", "[t]", "-an", "-sn", "-dn", "-c:v", "mjpeg", "-q:v", "5",
        "-f", "image2pipe", "pipe:1",
    ]  # fmt: skip


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, SpritesArgs)
    try:
        child = ctx.spawn(argv(ctx.args), inputs=ctx.inputs[:1], stdout=ctx.outputs[0])
        done = await child.wait()
    finally:
        close_fd(ctx.outputs[0])  # de app krijgt EOF zodra ffmpeg klaar is
    if done.rc != 0:
        reason = guard.why(child.pid)
        if reason:
            raise OpError(Code.FAILED, reason)
        raise OpError(Code.UNREADABLE, child.tail() or f"ffmpeg stopte met {done.rc}")
    frames = child._progress.get(b"frame", b"")  # de laatste regel van -progress
    return {
        "count": int(frames) if frames.isdigit() else None,
        "interval": INTERVAL,
        "width": WIDTH,
        "cols": COLS,
        "rows": ROWS,
    }
