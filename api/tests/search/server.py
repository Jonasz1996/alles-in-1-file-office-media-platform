"""De echte server (python -m app.serve) als subproces, voor wat alleen daar te zien is: de
toegangslog van uvicorn (test_search_logs.py) en de meting van §4.4 via HTTP
(test_perf_100k.py). Zoals e2e/conftest.py: een eigen omgeving zonder PLATFORM_* van de
ontwikkelaar, rate limits uit (alleen voor een testserver), en stoppen via de PID (SIGTERM, dan
SIGKILL; nooit pkill).
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

API_DIR = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def zonder_2fa(db: Path) -> None:
    """2FA niet verplicht (zoals e2e/search_harness._zonder_2fa), rechtstreeks in SQLite."""
    from app.security import SECURITY_KEY

    with contextlib.closing(sqlite3.connect(db)) as con, con:
        con.execute(
            "INSERT INTO setting (key, value, updated_at) SELECT ?, ?, datetime('now') "
            "WHERE NOT EXISTS (SELECT 1 FROM setting WHERE key = ?)",
            (SECURITY_KEY, '{"require_2fa": false}', SECURITY_KEY),
        )


class App:
    def __init__(self, data_dir: Path, log: Path, env: dict[str, str] | None = None):
        self.data_dir = Path(data_dir)
        self.log = Path(log)
        self.extra = dict(env or {})
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def environment(self) -> dict[str, str]:
        inherited = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
        return {
            **inherited,
            "PLATFORM_MODE": "standalone",
            "PLATFORM_DATABASE_URL": "",
            "PLATFORM_JWT_SECRET": "",
            "PLATFORM_CLUSTER_NODES": "",
            "PLATFORM_DATA_DIR": str(self.data_dir),
            "PLATFORM_WEB_DIST": str(self.data_dir / "geen-web"),
            "PLATFORM_HOST": "127.0.0.1",
            "PLATFORM_HTTP_PORT": str(self.port),
            "PLATFORM_HTTPS_PORT": str(free_port()),
            "PLATFORM_RATE_LIMITS": "false",
            "PLATFORM_ENVIRONMENT": "e2e",
            **self.extra,
        }

    def start(self, seconds: float = 120) -> float:
        """Start en wacht op /health; geeft de seconden tot dan."""
        begin = time.monotonic()
        with open(self.log, "ab") as out:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "app.serve"],
                cwd=API_DIR,
                env=self.environment(),
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        with httpx.Client(trust_env=False, timeout=2) as probe:
            while time.monotonic() - begin < seconds:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"De server stopte bij het starten:\n{self.tail()}")
                with contextlib.suppress(httpx.HTTPError):
                    if probe.get(f"{self.url}/health").status_code == 200:
                        return time.monotonic() - begin
                time.sleep(0.05)
        self.stop()
        raise RuntimeError(f"De server kwam niet op binnen {seconds} s:\n{self.tail()}")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)

    def tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""
