"""De worker van de analyse (fase 6, §3.11; X2a). compose.analyse.yaml start hem zo:

    python -I -c runtime.BOOT /srv --socket /run/analyse/worker.sock --models /models

    --socket PAD     waar de worker luistert (0660)
    --models MAP     de modellen (alleen lezen, van root; MODELLEN_ROOT op de host)
    --lock PAD       models.lock (standaard die naast app/analyse/lock.py)
    --local          buiten de container (de tests van laag 2): geen zelftest, sandbox "off",
                     en een kind mag uit de gewone python starten
    --check SOCKET   leeft de worker op die socket? (health.alive; de healthcheck)
    --selftest       de zelftest als JSON op stdout: 0 als alles goed is, anders 1

De sleutel van de handdruk komt alleen uit de omgeving (PLATFORM_ANALYSE_WORKER_KEY, minstens
32 tekens) en nooit uit argv. Het eerste wat de worker doet, is PR_SET_DUMPABLE op 0 zetten:
dan kan geen ander proces van uid 10003 /proc/1/environ of /proc/1/mem lezen. Daarna haalt hij
de sleutel uit os.environ; de kinderen krijgen hem nooit (child.env). Zonder sleutel stopt de
worker met code 2: de app zegt dan down (key_missing, of handshake).

Zonder --local draait eerst de zelftest (selftest.startup). Faalt die, dan luistert de worker
wel, maar weigert hij elke hello met sandbox en de reden (server.RefusingWorker).
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
import signal
import sys
from pathlib import Path

PR_SET_DUMPABLE = 4
_LIBC = ctypes.CDLL(None, use_errno=True)


def _not_dumpable() -> None:
    _LIBC.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)


# Zo vroeg als het kan: vóór de import van de app en vóór de sleutel uit de omgeving gaat
_not_dumpable()

from app.analyse import health, lock, runtime, wire  # noqa: E402
from app.analyse.worker import selftest  # noqa: E402
from app.analyse.worker.server import Config, RefusingWorker, Worker  # noqa: E402

log = logging.getLogger("platform.analyse.worker")
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="app.analyse.worker")
    ap.add_argument("--socket", default="")
    ap.add_argument("--models", default="/models")
    ap.add_argument("--lock", default=str(lock.LOCK_PATH))
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--check", default=None, metavar="SOCKET")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.check is None and not args.selftest and not args.socket:
        ap.error("--socket ontbreekt")
    return args


async def serve(worker: Worker) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in STOP_SIGNALS:
        loop.add_signal_handler(sig, stop.set)
    try:
        await worker.start()
    except BaseException:
        for sig in STOP_SIGNALS:
            loop.remove_signal_handler(sig)
        raise
    if isinstance(worker, RefusingWorker):
        where = "weigert elke hello: de afscherming faalt"
    else:
        where = "container" if worker.config.sandbox else "lokaal"
    log.info("analyse-worker luistert (%s)", where)
    stopped = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({stopped, worker.accepting}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopped.cancel()
        for sig in STOP_SIGNALS:
            loop.remove_signal_handler(sig)
        failed = worker.accepting.done() and not worker.accepting.cancelled()
        await worker.stop()
        log.info("analyse-worker gestopt%s", " (aannemen faalde)" if failed else "")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    key = os.environ.pop(runtime.KEY_ENV, "")
    args = parse(argv)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s"
    )
    if args.check is not None:
        return asyncio.run(health.alive(Path(args.check)))
    models = Path(args.models)
    socket_path = Path(args.socket) if args.socket else None
    if args.selftest:
        report = selftest.startup(models=models, socket_path=socket_path)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["ok"] else 1
    if len(key) < wire.KEY_MIN:
        print(f"{runtime.KEY_ENV} ontbreekt of is te kort (< {wire.KEY_MIN})", file=sys.stderr)
        return 2
    os.umask(0o007)
    assert socket_path is not None
    refuse = None
    if not args.local:
        shield = selftest.startup(models=models, socket_path=socket_path)
        if not shield["ok"]:
            refuse = "zelftest: " + "; ".join(shield["problems"])
            log.error("de afscherming faalt; de worker neemt niets aan: %s", refuse)
    config = Config(
        socket=socket_path,
        models=models,
        key=key,
        sandbox=not args.local and refuse is None,
        lock=Path(args.lock),
    )
    worker = Worker(config) if refuse is None else RefusingWorker(config, refuse)
    return asyncio.run(serve(worker))


if __name__ == "__main__":
    sys.exit(main())
