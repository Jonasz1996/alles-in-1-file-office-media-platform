"""De mediaworker: `python -I -m app.media.worker --socket /run/media/worker.sock` (Keuze 11, 13).

    --socket PAD        waar de worker luistert (modus 0660)
    --max-procs N       ffmpeg en ffprobe samen (PLATFORM_MEDIA_MAX_PROCS, standaard 10)
    --max-transcodes N  plaatsen voor transcode (PLATFORM_MEDIA_MAX_TRANSCODES, standaard 3)
    --memory-mb N       RLIMIT_DATA voor live en sprites (PLATFORM_MEDIA_JOB_MEMORY_MB, 1200)
    --local             de lokale worker van de app (sandbox: off)
    --loadtest          synth toestaan (PLATFORM_MEDIA_LOADTEST)
    --check             caps als JSON op stdout en stoppen (0 als er niets ontbreekt)
    --selftest          de zelftest van Keuze 11 (M1); tot dan code 2

Zonder vlag gelden de omgevingsvariabelen. De worker leest nooit de rest van de instellingen
van de app: hij heeft geen data, geen database en geen netwerk.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import json
import logging
import os
import signal
import sys
from pathlib import Path

from app.media.worker import run as R
from app.media.worker.ops import caps as caps_op
from app.media.worker.server import Config, Worker

log = logging.getLogger("platform.media.worker")
PR_SET_DUMPABLE = 4


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="app.media.worker")
    ap.add_argument("--socket", default=os.environ.get("PLATFORM_MEDIA_SOCKET", ""))
    ap.add_argument("--max-procs", type=int, default=_env_int("PLATFORM_MEDIA_MAX_PROCS", 10))
    ap.add_argument(
        "--max-transcodes", type=int, default=_env_int("PLATFORM_MEDIA_MAX_TRANSCODES", 3)
    )
    ap.add_argument("--memory-mb", type=int, default=_env_int("PLATFORM_MEDIA_JOB_MEMORY_MB", 1200))
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--loadtest", action="store_true", default=_env_bool("PLATFORM_MEDIA_LOADTEST"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if not (args.check or args.selftest) and not args.socket:
        ap.error("--socket ontbreekt")
    return args


def _not_dumpable() -> None:
    """PR_SET_DUMPABLE 0: een gekraakte ffmpeg kan niet in /proc/<worker>/fd (Keuze 11)."""
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)


async def serve(config: Config, caps: dict) -> int:
    """Luisteren tot SIGTERM of SIGINT (code 0), of tot het aannemen van verbindingen faalt
    (code 1: dan start de bewaking de worker opnieuw)."""
    worker = Worker(config, caps)
    await worker.start()
    log.info(
        "mediaworker luistert (ffmpeg %s, %s, max %d processen)",
        caps.get("ffmpeg"),
        "lokaal" if config.local else "container",
        config.max_procs,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    stopped = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({stopped, worker.accepting}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopped.cancel()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        failed = worker.accepting.done() and not worker.accepting.cancelled()
        await worker.stop()
        log.info("mediaworker gestopt%s", " (aannemen faalde)" if failed else "")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s"
    )
    caps = caps_op.gather(
        local=args.local, max_procs=args.max_procs, max_transcodes=args.max_transcodes
    )
    if args.check:
        print(json.dumps(caps, indent=2))
        return 0 if not caps.get("missing") else 1
    if args.selftest:
        print("de zelftest bouwt M1", file=sys.stderr)
        return 2
    os.umask(0o007)
    R.reserve_low_fds()
    _not_dumpable()
    config = Config(
        socket=Path(args.socket),
        max_procs=args.max_procs,
        max_transcodes=args.max_transcodes,
        memory_mb=args.memory_mb,
        local=args.local,
        loadtest=args.loadtest,
    )
    return asyncio.run(serve(config, caps))


if __name__ == "__main__":
    sys.exit(main())
