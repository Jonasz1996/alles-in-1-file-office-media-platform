"""bench: de meting van Keuze 26, zonder invoer (§3.3). Van M1.

1. Coderen: encode_s s testsrc2 met ruis, 1080p naar 720p veryfast (één ffmpeg):

       ffmpeg -f lavfi -i "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t" -t 10
         -vf "scale=-2:720" -c:v libx264 -preset veryfast -threads 4 -f null -

   Cores voor 1,0× = de CPU-tijd van het kind (wait4) / encode_s. f = die cores / REF_CORES.
2. Decoderen: decode_s s HEVC10 van 1080p, die de worker eerst zelf maakt met libx265 naar een
   pipe die hij zelf maakt (twee ffmpeg tegelijk; args.procs zegt 2):

       ffmpeg -f lavfi -i "testsrc2=size=1920x1080:rate=24" -t 4 -c:v libx265 -preset ultrafast
         -pix_fmt yuv420p10le -f hevc pipe:1
       ffmpeg -f hevc -i pipe:0 -f null -

   Alleen de CPU-tijd van het tweede telt. f_dec = (CPU-tijd / decode_s) / REF_DEC_CORES.

(spike, S7) De referenties zijn de cores van precies deze bench in de sandbox, niet 0,74 (de
tabelprijs van 720 veryfast van een bestand) en niet 0,31 (d_codec van HEVC). M1 mat ze onder
taskset -c 0-2 (regel 14), drie keer, met de echte opdracht via de lokale worker; de cijfers en
de spreiding staan in docs/fase-5/m1.md. f is dus 1,0 op de sandbox, en de kostentabel van
Keuze 24 (gemeten in de sandbox) geldt hier × f.

Op de achtergrond: nice -n 15 en ionice -c 3 (args.niceness); dat verandert de CPU-tijd niet.
"""

from __future__ import annotations

import os
import time

from app.media.wire import Code
from app.media.worker.args import THREADS, BenchArgs
from app.media.worker.server import Context, OpError

# Gemeten door M1 in de sandbox (docs/fase-5/m1.md, "De referentie van bench")
REF_CORES = 1.0
REF_DEC_CORES = 1.0
_BASE = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]


def encode_argv(seconds: int) -> list[str]:
    return [
        *_BASE, "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t",
        "-t", str(seconds), "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast",
        "-threads", str(THREADS), "-f", "null", "-",
    ]  # fmt: skip


def hevc_argv(seconds: int) -> list[str]:
    return [
        *_BASE, "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24", "-t", str(seconds),
        "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-f", "hevc",
        "pipe:1",
    ]  # fmt: skip


def decode_argv() -> list[str]:
    return [*_BASE, "-f", "hevc", "-i", "pipe:0", "-f", "null", "-"]


def _check(rc: int, tail: str, what: str) -> None:
    if rc != 0:
        raise OpError(Code.FAILED, f"{what}: {tail or f'ffmpeg stopte met {rc}'}")


async def run(ctx: Context) -> dict:
    assert isinstance(ctx.args, BenchArgs)
    encode_s, decode_s = ctx.args.encode_s, ctx.args.decode_s
    began = time.monotonic()

    child = ctx.spawn(encode_argv(encode_s))
    done = await child.wait()
    _check(done.rc, child.tail(), "coderen")
    encode_wall = time.monotonic() - began
    cores = done.cpu_s / encode_s

    # De pipe maakt de worker; elk kind krijgt zijn kant, en de worker sluit de zijne meteen,
    # anders ziet de decoder nooit EOF
    r, w = os.pipe2(os.O_CLOEXEC)
    try:
        maker = ctx.spawn(hevc_argv(decode_s), stdout=w)
    finally:
        os.close(w)
    try:
        reader = ctx.spawn(decode_argv(), stdin=r)
    finally:
        os.close(r)
    made = await maker.wait()
    read = await reader.wait()
    _check(made.rc, maker.tail(), "HEVC maken")
    _check(read.rc, reader.tail(), "HEVC decoderen")
    dec_cores = read.cpu_s / decode_s
    return {
        "f": round(cores / REF_CORES, 3),
        "f_dec": round(dec_cores / REF_DEC_CORES, 3),
        "cores": round(cores, 3),
        "dec_cores": round(dec_cores, 3),
        "encode_s": encode_s,
        "decode_s": decode_s,
        "encode_wall_s": round(encode_wall, 3),
        "wall_s": round(time.monotonic() - began, 3),
        "ref": {"cores": REF_CORES, "dec_cores": REF_DEC_CORES},
        "max_rss_kb": max(done.max_rss_kb, made.max_rss_kb, read.max_rss_kb),
    }
