"""subs: ondertitels naar WebVTT (§3.3, Keuze 29). M5.

Met tracks (de ingebedde tekstsporen, hooguit 8, in één run):

    nice -n 15 ionice -c 3 ffmpeg {invoer} -copyts
      -map 0:{i} -c:s copy -f ass pipe:{4+k}       (ASS en SSA, in hun eigen vorm)
      -map 0:{i} -c:s copy -f webvtt pipe:{4+k}    (WebVTT)
      -map 0:{i} -c:s srt -f srt pipe:{4+k}        (de rest: subrip, mov_text, text)

{invoer} is die van elke invoer (§3.3, zoals frame): -protocol_whitelist fd,pipe, de witte lijst
van demuxers, -max_pixels en -max_alloc, -f {demuxer} -fd 3 -i fd:. Met -copyts blijven de tijden
die van de bron; de app trekt de start eraf (subtitles.check_vtt).

De fd's komen allemaal van de app (Keuze 11): [bron, (ffmpeg_w, worker_r) × N, vtt_w × N]. ffmpeg
krijgt de bron op 3 en spoor k op 4+k. De worker
1. sluit zijn kopie van elke ffmpeg_w meteen na de start (anders komt er nooit EOF);
2. leest alle worker_r tegelijk tot EOF (ffmpeg schrijft de sporen door elkaar), hooguit
   subconv.MAX_INPUT per spoor. Daarboven wordt dat spoor too_large, en leest hij de rest leeg
   (anders blijft ffmpeg op die pipe staan);
3. zet elk spoor met subconv om, en geeft na elke 200 cues zijn lus de beurt (de worker heeft één
   lus voor alle opdrachten; 3,2 MB ASS kost ± 0,5 s);
4. schrijft de VTT naar vtt_w en sluit die. Een spoor dat niet lukt, krijgt een lege vtt_w en
   een error in het antwoord.

Met loose (een los bestand, zonder ffmpeg): het bestand lezen (hooguit 5 MB), omzetten, naar de
ene uitvoer schrijven.

Antwoord: {"tracks": [{"index", "cues", "bytes"} | {"index", "error"}]}, of bij loose
{"cues", "bytes"}.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

from app.media import subconv
from app.media.wire import Code
from app.media.worker import guard
from app.media.worker.args import THREADS, SubsArgs
from app.media.worker.ops.probe import WHITELIST
from app.media.worker.server import Context, OpError

YIELD_EVERY = 200
FIRST_TRACK_FD = 4  # de bron staat op 3
_FORMAT = {"ass": ("copy", "ass"), "webvtt": ("copy", "webvtt"), "srt": ("srt", "srt")}


def argv(args: SubsArgs) -> list[str]:
    out = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:2",
        "-nostats", "-max_alloc", "536870912", "-protocol_whitelist", "fd,pipe",
        "-format_whitelist", WHITELIST, "-max_pixels", "35389440", "-threads", str(THREADS),
        "-f", str(args.demuxer), "-fd", "3", "-i", "fd:", "-copyts",
    ]  # fmt: skip
    for k, track in enumerate(args.tracks):
        codec, muxer = _FORMAT[track.kind]
        out += ["-map", f"0:{track.index}", "-c:s", codec, "-f", muxer]
        out.append(f"pipe:{FIRST_TRACK_FD + k}")
    return out


def close_fd(fd: int) -> None:
    """Een fd van de app dicht zonder het nummer vrij te geven: server sluit ctx.outputs aan het
    eind zelf (wire.close_all), en een vrijgegeven nummer kan dan al iets anders zijn."""
    with contextlib.suppress(OSError):
        null = os.open(os.devnull, os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.dup2(null, fd, inheritable=False)
        finally:
            os.close(null)


async def _wait_fd(fd: int, *, write: bool) -> None:
    loop = asyncio.get_running_loop()
    ready = loop.create_future()

    def wake() -> None:
        if not ready.done():
            ready.set_result(None)

    if write:
        loop.add_writer(fd, wake)
    else:
        loop.add_reader(fd, wake)
    try:
        await ready
    finally:
        if write:
            loop.remove_writer(fd)
        else:
            loop.remove_reader(fd)


async def collect(fd: int, limit: int) -> bytes | None:
    """Een pipe tot EOF. None als er meer dan limit bytes kwamen (de rest is leeg gelezen)."""
    os.set_blocking(fd, False)
    chunks: list[bytes] = []
    size = 0
    while True:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            await _wait_fd(fd, write=False)
            continue
        if not data:
            return b"".join(chunks) if size <= limit else None
        size += len(data)
        if size <= limit:
            chunks.append(data)
        elif chunks:
            chunks.clear()  # te groot: niet meer bewaren, wel leeg lezen


async def send(fd: int, data: bytes) -> bool:
    """Alles naar een pipe van de app, wachtend als hij vol is. False als de app weg is."""
    os.set_blocking(fd, False)
    view = memoryview(data)
    while view:
        try:
            view = view[os.write(fd, view) :]
        except BlockingIOError:
            await _wait_fd(fd, write=True)
        except BrokenPipeError:
            return False
    return True


async def convert(data: bytes, kind: str) -> tuple[bytes, int]:
    """subconv.to_vtt, met na elke YIELD_EVERY cues een beurt voor de lus. (vtt, cues)."""
    cues: list[subconv.Cue] = []
    for n, cue in enumerate(subconv.parse(data, kind), 1):
        subconv.take(cue, cues)
        if n % YIELD_EVERY == 0:
            await asyncio.sleep(0)
    out = [subconv.HEADER]
    for n, block in enumerate(subconv.blocks(cues), 1):
        out.append(block)
        if n % YIELD_EVERY == 0:
            await asyncio.sleep(0)
    return "".join(out).encode(), len(out) - 1


def read_file(fd: int, limit: int) -> bytes | None:
    """Een gewoon bestand vanaf 0 (pread: de positie is gedeeld met de app), hooguit limit
    bytes; None als het groter is."""
    chunks: list[bytes] = []
    offset = 0
    while offset <= limit:
        chunk = os.pread(fd, min(1 << 20, limit + 1 - offset), offset)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)
    return None


async def _loose(ctx: Context, args: SubsArgs) -> dict:
    out = ctx.outputs[0]
    try:
        data = read_file(ctx.inputs[0], subconv.MAX_LOOSE)
        if data is None:
            raise OpError(Code.TOO_LARGE, f"meer dan {subconv.MAX_LOOSE} bytes")
        try:
            vtt, cues = await convert(data, str(args.loose))
        except subconv.SubtitleError as exc:
            raise OpError(Code.TOO_LARGE, exc.detail) from None
        if not await send(out, vtt):
            raise OpError(Code.FAILED, "de pipe naar de app is dicht")
        return {"cues": cues, "bytes": len(vtt)}
    finally:
        close_fd(out)


async def _tracks(ctx: Context, args: SubsArgs) -> dict:
    n = len(args.tracks)
    to_ffmpeg = [ctx.outputs[2 * k] for k in range(n)]
    from_ffmpeg = [ctx.outputs[2 * k + 1] for k in range(n)]
    vtt_out = ctx.outputs[2 * n :]
    try:
        child = ctx.spawn(argv(args), inputs=ctx.inputs[:1], extra=to_ffmpeg)
        for fd in to_ffmpeg:
            close_fd(fd)  # ffmpeg heeft ze nu; zonder onze kopie komt er EOF als hij stopt
        raws = await asyncio.gather(*(collect(fd, subconv.MAX_INPUT) for fd in from_ffmpeg))
        done = await child.wait()
        if done.rc != 0:
            reason = guard.why(child.pid)
            if reason:
                raise OpError(Code.FAILED, reason)
            raise OpError(Code.UNREADABLE, child.tail() or f"ffmpeg stopte met {done.rc}")
        results: list[dict] = []
        for track, raw, fd in zip(args.tracks, raws, vtt_out, strict=True):
            item: dict = {"index": track.index}
            if raw is None:
                item["error"] = Code.TOO_LARGE.value
            else:
                try:
                    vtt, cues = await convert(raw, track.kind)
                except subconv.SubtitleError as exc:
                    item["error"] = exc.code
                else:
                    if await send(fd, vtt):
                        item |= {"cues": cues, "bytes": len(vtt)}
                    else:
                        item["error"] = Code.FAILED.value
            close_fd(fd)
            results.append(item)
        return {"tracks": results}
    finally:
        for fd in (*to_ffmpeg, *from_ffmpeg, *vtt_out):
            close_fd(fd)


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, SubsArgs)
    if ctx.args.loose is not None:
        return await _loose(ctx, ctx.args)
    return await _tracks(ctx, ctx.args)
