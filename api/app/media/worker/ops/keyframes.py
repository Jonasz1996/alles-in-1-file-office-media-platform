"""keyframes: de keyframe-index van één videospoor (§3.3, Keuze 17). Van M4.

- Zonder fallback: alleen de eigen lezers (containers.py: Matroska-Cues, MP4 stss), begrensd op
  4 MB lezen. Snel genoeg voor play (binnen de 5 s van de probe). Ze draaien in de event loop
  zelf: de worker heeft geen threads (preexec_fn in run.py), en een paar MB pread kost
  milliseconden;
- met fallback: ffprobe op alle pakketten van het spoor (`-show_entries
  packet=pts_time,size,flags`). Dat leest het hele bestand: alleen op de achtergrond (nice 15,
  ionice -c 3, de tijdgrens KEYFRAMES_FALLBACK_TIMEOUT van server.py).

Het antwoord (containers.for_wire): state ready, sparse (Cues met gaten boven 20 s) of none
(niets gevonden), de index in de opgeslagen vorm als base64 (hooguit 48 KB, zo nodig uitgedund
tot één keyframe per seconde of meer), en max_gop_ms uit de volle index. Alles in ms in de tijd
van de container.
"""

from __future__ import annotations

from app.media.wire import Code
from app.media.worker import containers, guard
from app.media.worker.args import KeyframesArgs
from app.media.worker.ops.probe import WHITELIST
from app.media.worker.server import Context, OpError

PACKETS_STDOUT = 64 * 1024 * 1024  # een film van 3 uur op 60 fps is ±13 MB


def fallback_argv(args: KeyframesArgs) -> list[str]:
    return [
        "ffprobe", "-hide_banner", "-loglevel", "error", "-max_alloc", "536870912",
        "-protocol_whitelist", "fd,pipe", "-format_whitelist", WHITELIST,
        "-f", args.demuxer, "-fd", "3", "-select_streams", str(args.video_index),
        "-show_entries", "packet=pts_time,size,flags", "-of", "csv=p=0", "-i", "fd:",
    ]  # fmt: skip


def parse_packets(text: bytes) -> containers.Index | None:
    """De keyframes uit de regels van ffprobe (pts_time,size,flags); None zonder keyframes."""
    times: list[int] = []
    sizes: list[int] = []
    last: int | None = None
    current = -1
    for line in text.splitlines():
        parts = line.split(b",")
        if len(parts) < 3:
            continue
        try:
            pts = round(float(parts[0]) * 1000)
            size = int(parts[1])
        except ValueError:
            continue  # pts_time N/A
        last = pts if last is None else max(last, pts)
        if b"K" in parts[2]:
            times.append(pts)
            sizes.append(size)
            current = len(sizes) - 1
        elif current >= 0:
            sizes[current] += size
    if not times:
        return None
    times, sizes = containers.finish(times, sizes)
    return containers.Index(times, sizes, "packets", sparse=False, end_ms=last)


async def _packets(ctx: Context, args: KeyframesArgs) -> containers.Index | None:
    child = ctx.spawn(fallback_argv(args), inputs=ctx.inputs, capture_stdout=PACKETS_STDOUT)
    done = await child.wait()
    if child.stdout_overflow:
        raise OpError(Code.FAILED, "te veel pakketten voor de terugval")
    if done.rc != 0:
        reason = guard.why(child.pid)
        raise OpError(Code.UNREADABLE, reason or child.tail() or f"ffprobe stopte met {done.rc}")
    return parse_packets(bytes(child.stdout))


NONE = {"state": "none", "keyframes": None, "count": 0, "total": 0, "max_gop_ms": None}


async def run(ctx: Context) -> dict:
    args = ctx.args
    assert isinstance(args, KeyframesArgs)
    if args.fallback:
        index = await _packets(ctx, args)
    else:
        index = containers.read(ctx.inputs[0], args.demuxer, args.video_index)
    if index is None or not index.times_ms:
        return {**NONE, "source": "packets" if args.fallback else None}
    try:
        return containers.for_wire(index)
    except ValueError as exc:
        raise OpError(Code.FAILED, str(exc)) from None
