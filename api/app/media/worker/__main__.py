"""De mediaworker: `python -I -m app.media.worker --socket /run/media/worker.sock` (Keuze 11, 13).

    --socket PAD        waar de worker luistert (modus 0660)
    --max-procs N       ffmpeg en ffprobe samen (PLATFORM_MEDIA_MAX_PROCS, standaard 10)
    --max-transcodes N  plaatsen voor transcode (PLATFORM_MEDIA_MAX_TRANSCODES, standaard 3)
    --memory-mb N       RLIMIT_DATA voor live en sprites (PLATFORM_MEDIA_JOB_MEMORY_MB, 1200)
    --local             de lokale worker van de app (sandbox: off)
    --loadtest          synth toestaan (PLATFORM_MEDIA_LOADTEST)
    --check             caps als JSON op stdout en stoppen (0 als er niets ontbreekt)
    --check SOCKET      leeft de worker op die socket? hello zonder sleutel: 0 als hij een
                        hello teruggeeft, 1 bij een weigering (de zelftest faalde) of niets
                        (de healthcheck van compose.media.yaml; M1)
    --selftest          de zelftest van Keuze 11 en §2 M1 (selftest.full): JSON op stdout,
                        0 als alles goed is, anders 1
    --ffmpeg BEGIN      de versie die de zelftest eist (standaard 7.1.)

Zonder vlag gelden de omgevingsvariabelen. De worker leest nooit de rest van de instellingen
van de app: hij heeft geen data, geen database en geen netwerk.

De sleutel van hello (review S1) komt alleen uit de omgeving: PLATFORM_MEDIA_WORKER_KEY,
minstens 32 tekens, en nooit als argument (de opdrachtregel is voor iedereen te lezen in
/proc). De worker haalt hem meteen uit os.environ; zijn kinderen krijgen toch een kale omgeving
(run.environment), en /proc/<worker>/environ is na PR_SET_DUMPABLE 0 niet te lezen voor
dezelfde uid. Zonder sleutel stopt de worker met code 2 (--check en --selftest hebben hem niet
nodig).

M0-hotfix van M1 (docs/fase-5/m1.md): zonder --local draait bij de start eerst de afscherming
van de zelftest (selftest.startup). Slaagt die, dan staat caps.sandbox op "on" (review S6);
faalt die, dan luistert de worker wel, maar weigert hij elke hello met de reden
(selftest.RefusingWorker), en neemt hij geen opdracht of fd aan. De meting van het geheugen
(guard.start) loopt naast de server.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import json
import logging
import os
import secrets
import signal
import socket
import sys
from pathlib import Path

from app.media import wire
from app.media.worker import guard, selftest
from app.media.worker import run as R
from app.media.worker.ops import caps as caps_op
from app.media.worker.server import Config, Worker

log = logging.getLogger("platform.media.worker")
PR_SET_DUMPABLE = 4
KEY_ENV = "PLATFORM_MEDIA_WORKER_KEY"


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
    ap.add_argument("--check", nargs="?", const="", default=None, metavar="SOCKET")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ffmpeg", default=selftest.VERSION, metavar="BEGIN")
    args = ap.parse_args(argv)
    if not (args.check is not None or args.selftest) and not args.socket:
        ap.error("--socket ontbreekt")
    return args


def _not_dumpable() -> None:
    """PR_SET_DUMPABLE 0: een gekraakte ffmpeg kan niet in /proc/<worker>/fd (Keuze 11)."""
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)


async def alive(path: Path) -> int:
    """--check SOCKET: hello zonder sleutel. Een hello terug is een levende worker (het bewijs
    kan alleen de app controleren); een weigering of niets is 1."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.setblocking(False)
    try:
        async with asyncio.timeout(4):
            with wire.socket_address(path) as address:
                await loop.sock_connect(sock, address)
            await wire.send(sock, wire.hello(secrets.token_hex(16)))
            reply, fds = await wire.receive(sock, max_fds=0)
        wire.close_all(fds)
    except (OSError, TimeoutError, wire.WireError) as exc:
        print(f"geen worker op {path}: {exc.__class__.__name__}")
        return 1
    finally:
        sock.close()
    if reply.get("ev") == "hello" and reply.get("v") == wire.PROTOCOL:
        print("de worker leeft")
        return 0
    print(f"de worker weigert: {reply.get('code')}: {str(reply.get('detail'))[:300]}")
    return 1


async def serve(config: Config, caps: dict, refuse: str | None = None) -> int:
    """Luisteren tot SIGTERM of SIGINT (code 0), of tot het aannemen van verbindingen faalt
    (code 1: dan start de bewaking de worker opnieuw). Met refuse: elke hello krijgt die
    weigering (de afscherming faalde)."""
    if refuse is None:
        worker = Worker(config, caps)
    else:
        worker = selftest.RefusingWorker(config, caps, refuse)
    await worker.start()
    watch = guard.start(caps)
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
        watch.cancel()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        failed = worker.accepting.done() and not worker.accepting.cancelled()
        await worker.stop()
        log.info("mediaworker gestopt%s", " (aannemen faalde)" if failed else "")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    key = os.environ.pop(KEY_ENV, "")  # vóór elk kind, ook die van caps
    _not_dumpable()
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s"
    )
    if args.check:
        return asyncio.run(alive(Path(args.check)))  # zonder caps: geen ffmpeg nodig
    caps = caps_op.gather(
        local=args.local, max_procs=args.max_procs, max_transcodes=args.max_transcodes
    )
    if args.check is not None:
        print(json.dumps(caps, indent=2))
        return 0 if not caps.get("missing") else 1
    socket_path = Path(args.socket) if args.socket else None
    if args.selftest:
        R.reserve_low_fds()
        report = asyncio.run(
            selftest.full(local=args.local, version=args.ffmpeg, socket_path=socket_path)
        )
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    if len(key) < wire.KEY_MIN:
        print(f"{KEY_ENV} ontbreekt of is korter dan {wire.KEY_MIN} tekens", file=sys.stderr)
        return 2
    os.umask(0o007)
    R.reserve_low_fds()
    refuse = None
    if not args.local:
        shield = asyncio.run(selftest.startup(local=False, socket_path=socket_path))
        caps["selftest"] = {k: shield[k] for k in ("ok", "sandbox", "problems")}
        if shield["ok"]:
            caps["sandbox"] = "on"
        else:
            refuse = "zelftest: " + "; ".join(shield["problems"])
            log.error("de afscherming faalt; de worker neemt niets aan: %s", refuse)
    config = Config(
        socket=Path(args.socket),
        max_procs=args.max_procs,
        max_transcodes=args.max_transcodes,
        memory_mb=args.memory_mb,
        local=args.local,
        loadtest=args.loadtest,
        key=key,
    )
    return asyncio.run(serve(config, caps, refuse))


if __name__ == "__main__":
    sys.exit(main())
