"""De nep-providers als subproces, voor `e2e/media_harness.py` en de test van de proces-stand.

    fake = FakeProvidersProcess(tmp_path)
    fake.start()
    env = {**env, **fake.env()}       # de URL's voor de app, en PROVIDER_PRIVATE_OK
    fake.tmdb_key                     # om op /beheer in te vullen (nooit naar de log)
    fake.fault("500", "/3/search/movie", host="tmdb", times=2)
    fake.log()                        # de verzoeken, zonder sleutel
    fake.stop()                       # op PID

Het proces krijgt een omgeving zonder `PLATFORM_*`, en de sleutel via een bestand met 600-rechten.
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API_DIR = Path(__file__).resolve().parents[3]


def write_secret(path: Path, value: str) -> None:
    """Een geheim naar een bestand met 600-rechten (nooit naar de log)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)


class FakeProvidersProcess:
    def __init__(
        self,
        workdir: Path | str,
        *,
        data_dir: Path | str | None = None,
        rate_limits: bool = True,
        tmdb_key: str | None = None,
    ) -> None:
        self.workdir = Path(workdir)
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.rate_limits = rate_limits
        self.tmdb_key = tmdb_key or secrets.token_urlsafe(32)
        self.key_file = self.workdir / "tmdb.key"
        self.port_file = self.workdir / "fake-providers.port"
        self.log_path = self.workdir / "fake-providers.log"
        self.port: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None

    # --- adressen ---

    @property
    def url(self) -> str:
        assert self.port is not None, "de nep-providers zijn nog niet gestart"
        return f"http://127.0.0.1:{self.port}"

    @property
    def urls(self) -> dict[str, str]:
        return {
            "tmdb": f"{self.url}/tmdb",
            "image": f"{self.url}/tmdb-img",
            "musicbrainz": f"{self.url}/mb",
            "caa": f"{self.url}/caa",
            "archive": f"http://localhost:{self.port}/archive",
        }

    def env(self) -> dict[str, str]:
        """De omgeving voor de app (de namen van §3.3)."""
        urls = self.urls
        return {
            "PLATFORM_MEDIA_TMDB_URL": urls["tmdb"],
            "PLATFORM_MEDIA_TMDB_IMAGE_URL": urls["image"],
            "PLATFORM_MEDIA_MUSICBRAINZ_URL": urls["musicbrainz"],
            "PLATFORM_MEDIA_CAA_URL": urls["caa"],
            "PLATFORM_MEDIA_PROVIDER_PRIVATE_OK": "true",
        }

    # --- starten en stoppen ---

    def start(self, seconds: float = 30) -> None:
        write_secret(self.key_file, self.tmdb_key)
        self.port_file.unlink(missing_ok=True)
        command = [
            sys.executable,
            "-m",
            "tests.media.fake_providers",
            "--port",
            str(self.port or 0),
            "--port-file",
            str(self.port_file),
            "--key-file",
            str(self.key_file),
        ]
        if self.data_dir is not None:
            command += ["--data", str(self.data_dir)]
        if not self.rate_limits:
            command.append("--no-rate-limits")
        env = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        with open(self.log_path, "ab") as log:
            self.proc = subprocess.Popen(
                command, cwd=API_DIR, env=env, stdout=log, stderr=subprocess.STDOUT
            )
        try:
            self._wait_ready(seconds)
        except BaseException:
            self.stop()
            raise

    def _wait_ready(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"De nep-providers stopten bij het starten:\n{self.log_tail()}")
            if self.port is None and self.port_file.exists():
                text = self.port_file.read_text().strip()
                self.port = int(text) if text.isdigit() else None
            if self.port is not None and self.healthy():
                return
            time.sleep(0.05)
        message = f"De nep-providers kwamen niet op binnen {seconds} s:\n{self.log_tail()}"
        raise RuntimeError(message)

    def stop(self) -> None:
        """Alleen dit proces, op zijn PID: SIGTERM, dan SIGKILL. De poort blijft voor een
        herstart."""
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)

    def restart(self) -> None:
        self.stop()
        self.start()

    def __enter__(self) -> FakeProvidersProcess:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def log_tail(self, lines: int = 40) -> str:
        try:
            text = self.log_path.read_text(errors="replace")
        except OSError:
            return "(geen log)"
        return "\n".join(text.replace(self.tmdb_key, "***").splitlines()[-lines:])

    # --- sturen ---

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, trust_env=False, timeout=10)

    def healthy(self) -> bool:
        try:
            with self._client() as c:
                r = c.get("/__health", timeout=2)
        except httpx.HTTPError:
            return False
        return r.status_code == 200 and r.json().get("ok") is True

    def health(self) -> dict[str, Any]:
        with self._client() as c:
            r = c.get("/__health")
        r.raise_for_status()
        return r.json()

    def fault(self, kind: str, path: str = "*", **options: Any) -> int:
        """Een fout op bestelling (zie `app.Fault`); geeft zijn id."""
        with self._client() as c:
            r = c.post("/__fault", json={"kind": kind, "path": path, **options})
        r.raise_for_status()
        return r.json()["id"]

    def clear_faults(self) -> None:
        with self._client() as c:
            c.delete("/__fault").raise_for_status()

    def log(self) -> list[dict[str, Any]]:
        with self._client() as c:
            r = c.get("/__log")
        r.raise_for_status()
        return r.json()["entries"]

    def clear_log(self) -> None:
        with self._client() as c:
            c.delete("/__log").raise_for_status()

    def reset(self) -> None:
        """Fouten, logboek en de tempo-vensters leeg."""
        with self._client() as c:
            c.post("/__reset").raise_for_status()
