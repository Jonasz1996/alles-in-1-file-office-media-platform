"""Hoe de container analyse de worker start (fase 6, §3.11; X2a).

compose.analyse.yaml start de worker niet met `-m`: `python -I -c BOOT /srv …`. BOOT zet de map
van de API (argv[1]) vooraan in sys.path en draait dan `app.analyse.worker` als __main__, zoals
`-m` dat zou doen. `-I` laat PYTHONPATH en de map van het script weg, dus alleen die ene map komt
erbij. test_compose_analyse.py eist dat x-analyse-boot in de compose gelijk is aan BOOT, en
x-omzet-boot aan OMZET_BOOT (de omzetter van X3a, dienst analyse-omzetten).

worker_argv geeft dezelfde regel voor een test of een lokale run, met een eigen python en map.
De sleutel staat nooit in argv (/proc/<pid>/cmdline is voor iedereen te lezen): die komt uit de
omgeving, PLATFORM_ANALYSE_WORKER_KEY (KEY_ENV).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

API_ROOT = str(Path(__file__).resolve().parents[2])  # de map met app/ (in het image /srv)
KEY_ENV = "PLATFORM_ANALYSE_WORKER_KEY"

BOOT = (
    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('app.analyse.worker',run_name='__main__',alter_sys=True)"
)
OMZET_BOOT = (
    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"
)


def worker_argv(
    *args: str,
    python: str | None = None,
    root: str = API_ROOT,
) -> list[str]:
    """De opdrachtregel van de worker: `python -I -c BOOT <root> <args…>`, zoals in compose
    (`--socket …`, `--models …`, `--check …`, `--selftest`, `--local`)."""
    return [python or sys.executable, "-I", "-c", BOOT, root, *args]


def serve_argv(
    socket: Path | str,
    models: Path | str,
    *,
    extra: Sequence[str] = (),
    python: str | None = None,
    root: str = API_ROOT,
) -> list[str]:
    """De regel van de dienst analyse: luisteren op socket, met de modellen in models."""
    return worker_argv(
        "--socket", str(socket), "--models", str(models), *extra, python=python, root=root
    )
