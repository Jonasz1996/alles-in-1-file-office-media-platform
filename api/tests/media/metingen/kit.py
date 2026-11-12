"""Wat de meetscripts van fase 5 delen (§4.5): een app met media, de waakhond, de processen
via /proc, de rust van de machine, en de mediaan en de slechtste.

- App: `python -m app.serve` met media aan in een eigen map (zoals de Server van de e2e), de
  lokale worker op CPUS (taskset -c 0-2, regel 14, ook na een herstart), MAX_PROCS ffmpeg
  (regel 13), de belastingtest aan, en een beheerder. Optioneel met office (de echte DS).
  Stoppen en herstarten op pid, nooit op naam.
- Watchdog: elke 0,5 s MemAvailable; onder MIN_FREE_GIB stopt hij alles wat de meting startte
  (de app, de worker en ffmpeg, op pid met de starttijd; een container van de meting met
  docker stop), en de meting faalt.
- Sampler: elke 0,5 s de CPU en de RSS van de app, de worker en zijn ffmpeg (/proc), en de CPU
  van de hele machine (/proc/stat), zodat te zien is wat er naast de meting liep.
- quiet(): wacht tot de cores van de worker stil zijn, voor een meting van tijd of CPU.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

API_DIR = Path(__file__).resolve().parents[3]
REPO = API_DIR.parent
WEB_DIST = REPO / "web" / "dist"
CPUS = frozenset({0, 1, 2})  # de grens van de LXC voor de worker (regel 14)
MAX_PROCS = 3  # regel 13
MIN_FREE_GIB = 1.5
PASSWORD = "Meten-fase-5-niet-echt!"
TICK = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGESIZE") // 1024
FFMPEG = ("ffmpeg", "ffprobe")


def log(*parts: Any) -> None:
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- statistiek -------------------------------------------------------------------------------


def spread(values: Iterable[float | None], *, worst: str = "max", digits: int = 3) -> dict:
    """De waarden, de mediaan en de slechtste (de grootste, of met worst="min" de kleinste)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"waarden": [], "n": 0, "mediaan": None, "slechtste": None}
    bad = max(vals) if worst == "max" else min(vals)
    return {
        "waarden": [round(v, digits) for v in vals],
        "n": len(vals),
        "mediaan": round(statistics.median(vals), digits),
        "slechtste": round(bad, digits),
    }


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def write_json(path: str | Path | None, data: Any) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    log("uitkomst:", out)


# --- processen via /proc ------------------------------------------------------------------------


@dataclass
class Proc:
    pid: int
    ppid: int
    start: int
    state: str
    comm: str
    ticks: int  # utime + stime
    child_ticks: int  # cutime + cstime (kinderen die al weg zijn)
    rss_kb: int


def proc(pid: int) -> Proc | None:
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    comm = data[data.find("(") + 1 : data.rfind(")")]
    rest = data[data.rfind(")") + 2 :].split()
    try:
        return Proc(
            pid=pid,
            ppid=int(rest[1]),
            start=int(rest[19]),
            state=rest[0],
            comm=comm,
            ticks=int(rest[11]) + int(rest[12]),
            child_ticks=int(rest[13]) + int(rest[14]),
            rss_kb=int(rest[21]) * PAGE_KB,
        )
    except (IndexError, ValueError):
        return None


def table() -> dict[int, Proc]:
    out = {}
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            found = proc(int(entry.name))
            if found is not None:
                out[found.pid] = found
    return out


def descendants(root: int, procs: dict[int, Proc]) -> list[Proc]:
    children: dict[int, list[Proc]] = {}
    for p in procs.values():
        children.setdefault(p.ppid, []).append(p)
    out, todo = [], [root]
    while todo:
        for child in children.get(todo.pop(), []):
            out.append(child)
            todo.append(child.pid)
    return out


def cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def alive(pid: int, start: int) -> bool:
    found = proc(pid)
    return found is not None and found.start == start and found.state not in ("Z", "X")


def kill_pid(pid: int, start: int, sig: int = signal.SIGKILL) -> bool:
    """Alleen dit proces, en alleen als het nog hetzelfde is (dezelfde starttijd)."""
    if not alive(pid, start):
        return False
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, sig)
        return True
    return False


def set_affinity(pid: int, cpus: Iterable[int]) -> None:
    """Zoals `taskset -a -p -c`: elke draad van het proces (kinderen erven het)."""
    wanted = set(cpus) & os.sched_getaffinity(0) or os.sched_getaffinity(0)
    for task in Path(f"/proc/{pid}/task").iterdir():
        with contextlib.suppress(ProcessLookupError, OSError):
            os.sched_setaffinity(int(task.name), wanted)


def machine_ticks(cpus: frozenset[int] | None = None) -> tuple[int, int]:
    """(bezig, totaal) in ticks (/proc/stat): van alle cores samen, of van de cores in cpus."""
    lines = Path("/proc/stat").read_text().splitlines()
    if cpus is None:
        rows = [lines[0]]
    else:
        rows = [ln for ln in lines if ln.startswith("cpu") and ln[3:4].isdigit()
                and int(ln.split()[0][3:]) in cpus]  # fmt: skip
    busy = total = 0
    for row in rows:
        values = [int(v) for v in row.split()[1:]]
        idle = values[3] + values[4]  # idle + iowait
        busy += sum(values[:8]) - idle
        total += sum(values[:8])
    return busy, total


def mem_available() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return 0


def busy_cores(seconds: float = 3.0, cpus: frozenset[int] | None = None) -> float:
    """De CPU over seconds, in cores: van de hele machine, of van de cores in cpus."""
    b0, t0 = machine_ticks(cpus)
    time.sleep(seconds)
    b1, t1 = machine_ticks(cpus)
    count = len(cpus) if cpus is not None else (os.cpu_count() or 1)
    return (b1 - b0) / max(1, t1 - t0) * count


def quiet(limit: float = 0.6, wait_s: float = 900.0) -> float:
    """Wacht tot de cores van de worker (CPUS) onder limit cores zitten (over 3 s); geeft wat
    het laatst gemeten werd. Na wait_s gaat de meting toch door (en staat de drukte in het
    rapport). Alleen CPUS: in de sandbox draait ander werk vast op core 3, en dat raakt de
    worker niet; wat er op de hele machine liep, meet de Sampler (anders_cores)."""
    end = time.monotonic() + wait_s
    while True:
        used = busy_cores(3.0, CPUS)
        if used <= limit or time.monotonic() > end:
            return round(used, 2)
        log(f"de cores {min(CPUS)}-{max(CPUS)} zijn druk ({used:.2f} cores); wachten")


# --- de waakhond --------------------------------------------------------------------------------


class Watchdog(threading.Thread):
    """Stopt de meting als het geheugen op raakt (onder MIN_FREE_GIB beschikbaar)."""

    def __init__(self, min_free_gib: float = MIN_FREE_GIB) -> None:
        super().__init__(daemon=True, name="waakhond")
        self.min_free = int(min_free_gib * 1024**3)
        self.roots: list[Callable[[], int | None]] = []
        self.containers: list[str] = []
        self.tripped = False
        self.lowest = mem_available()
        self._halt = threading.Event()

    def watch(self, root: Callable[[], int | None]) -> None:
        self.roots.append(root)

    def run(self) -> None:
        while not self._halt.wait(0.5):
            free = mem_available()
            self.lowest = min(self.lowest, free)
            if free < self.min_free and not self.tripped:
                self.tripped = True
                log(f"WAAKHOND: nog {free / 1024**3:.2f} GiB vrij; alles van de meting stopt")
                self.kill_all()

    def kill_all(self) -> None:
        procs = table()
        victims = []
        for root in self.roots:
            pid = root()
            if pid is None or pid not in procs:
                continue
            victims += [*descendants(pid, procs), procs[pid]]
        for p in victims:
            kill_pid(p.pid, p.start)
        for name in self.containers:
            subprocess.run(["docker", "stop", "-t", "5", name], capture_output=True, check=False)

    def stop(self) -> None:
        self._halt.set()

    def summary(self) -> dict[str, Any]:
        return {"ingegrepen": self.tripped, "minst_vrij_gib": round(self.lowest / 1024**3, 2)}


# --- de app -------------------------------------------------------------------------------------


@dataclass
class Office:
    """De echte DS voor de varianten met office (e2e/office_ds.sh start hem)."""

    url: str
    secret: str
    container: str
    internal_port: int = field(default_factory=free_port)
    host: str = "host.docker.internal"

    def env(self) -> dict[str, str]:
        return {
            "PLATFORM_OFFICE_URL": self.url,
            "PLATFORM_OFFICE_SECRET": self.secret,
            "PLATFORM_OFFICE_APP_URL": f"http://{self.host}:{self.internal_port}",
            "PLATFORM_OFFICE_INTERNAL_PORT": str(self.internal_port),
            "PLATFORM_OFFICE_INTERNAL_HOST": "0.0.0.0",
        }


class App:
    """De app met media aan (zoals MediaApp van de e2e, zonder pytest)."""

    def __init__(
        self,
        base: Path,
        *,
        env: dict[str, str] | None = None,
        office: Office | None = None,
        cpus: frozenset[int] | None = CPUS,
    ) -> None:
        self.base = Path(base)
        self.data_dir = self.base / "data"
        self.extra = dict(env or {})
        self.office = office
        self.cpus = cpus
        self.port = free_port()
        self.https_port = free_port()
        self.proc: subprocess.Popen | None = None
        self.email = "meten@example.org"
        self._token: tuple[str, float] | None = None
        self.log_path = self.base / "app.log"
        self.http = httpx.Client(base_url=self.url, trust_env=False, timeout=60)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def environment(self) -> dict[str, str]:
        inherited = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
        env = {
            **inherited,
            "PLATFORM_MODE": "standalone",
            "PLATFORM_DATABASE_URL": "",
            "PLATFORM_JWT_SECRET": "",
            "PLATFORM_CLUSTER_NODES": "",
            "PLATFORM_DATA_DIR": str(self.data_dir),
            "PLATFORM_WEB_DIST": str(WEB_DIST),
            "PLATFORM_HOST": "127.0.0.1",
            "PLATFORM_HTTP_PORT": str(self.port),
            "PLATFORM_HTTPS_PORT": str(self.https_port),
            "PLATFORM_RATE_LIMITS": "false",
            "PLATFORM_MEDIA_ENABLED": "true",
            "PLATFORM_MEDIA_MAX_PROCS": str(MAX_PROCS),
            "PLATFORM_MEDIA_MAX_TRANSCODES": "3",
            "PLATFORM_MEDIA_LOADTEST": "1",
        }
        if self.office is not None:
            env.update(self.office.env())
        env.update(self.extra)
        return env

    # --- starten en stoppen ---

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None and self.proc.poll() is None else None

    def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "ab") as out:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "app.serve"], cwd=API_DIR, env=self.environment(),
                stdout=out, stderr=subprocess.STDOUT,
            )  # fmt: skip
        self._wait_ready()
        self.wait_worker()
        self.pin()
        self._setup()

    def _wait_ready(self, seconds: float = 90) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"de app stopte bij het starten:\n{self.log_tail()}")
            with contextlib.suppress(httpx.HTTPError):
                if self.http.get("/health", timeout=2).status_code == 200:
                    return
            time.sleep(0.1)
        raise RuntimeError(f"de app kwam niet op binnen {seconds} s:\n{self.log_tail()}")

    def tree(self) -> list[Proc]:
        pid = self.pid
        return descendants(pid, table()) if pid is not None else []

    def worker_pid(self) -> int | None:
        for p in sorted(self.tree(), key=lambda p: p.pid):
            if "app.media.worker" in cmdline(p.pid):
                return p.pid
        return None

    def wait_worker(self, seconds: float = 30) -> int:
        sock = self.data_dir / "media" / "run" / "worker.sock"
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            pid = self.worker_pid()
            if pid is not None and sock.exists():
                return pid
            time.sleep(0.05)
        raise RuntimeError(f"geen lokale worker binnen {seconds} s:\n{self.log_tail()}")

    def pin(self) -> None:
        pid = self.worker_pid()
        if pid is not None and self.cpus is not None:
            set_affinity(pid, self.cpus)

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        before = descendants(proc.pid, table())
        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        left = [p for p in before if kill_pid(p.pid, p.start)]
        if left:
            log("na het stoppen nog over (nu weg):", [f"{p.pid} {p.comm}" for p in left])

    def restart(self, outage_s: float = 0.0) -> float:
        """Stoppen op pid, outage_s wachten, weer starten op dezelfde poort; geeft het moment
        (time.monotonic) waarop de app weer antwoordt ("ready")."""
        self.stop()
        if outage_s:
            time.sleep(outage_s)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "ab") as out:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "app.serve"], cwd=API_DIR, env=self.environment(),
                stdout=out, stderr=subprocess.STDOUT,
            )  # fmt: skip
        self._wait_ready()
        ready = time.monotonic()
        self.wait_worker()
        self.pin()
        return ready

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return "(geen log)"

    def close(self) -> None:
        self.stop()
        self.http.close()

    # --- de beheerder en de API ---

    def _setup(self) -> None:
        """De eerste beheerder zoals de e2e (make_admin): setup, 2FA aan en weer uit, en 2FA
        niet verplicht. Na een herstart is hij er al."""
        r = self.http.get("/api/v1/auth/status")
        if r.status_code == 200 and not r.json().get("setup_required", True):
            return
        body = {"email": self.email, "display_name": "Meten", "password": PASSWORD}
        r = self.http.post("/api/v1/auth/setup", json=body)
        if r.status_code == 409:
            return
        r.raise_for_status()
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        from app import totp

        secret = self.http.post("/api/v1/auth/2fa/setup", headers=headers).json()["secret"]
        code = totp.code_at(secret, totp.current_step())
        codes = self.http.post(
            "/api/v1/auth/2fa/enable", headers=headers, json={"code": code, "password": PASSWORD}
        ).json()["recovery_codes"]
        self.http.put(
            "/api/v1/admin/security", headers=headers, json={"require_2fa": False}
        ).raise_for_status()
        self.http.post(
            "/api/v1/auth/2fa/disable", headers=headers,
            json={"code": codes[0], "password": PASSWORD},
        ).raise_for_status()  # fmt: skip
        self._token = (token, time.monotonic())

    def token(self) -> str:
        if self._token is None or time.monotonic() - self._token[1] > 600:
            r = self.http.post(
                "/api/v1/auth/login", json={"email": self.email, "password": PASSWORD}
            )
            r.raise_for_status()
            self._token = (r.json()["access_token"], time.monotonic())
        return self._token[0]

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}

    def call(self, method: str, path: str, *, expect: int | None = None, **kw: Any):
        headers = {**self.headers(), **(kw.pop("headers", None) or {})}
        r = self.http.request(method, path, headers=headers, **kw)
        if expect is not None and r.status_code != expect:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r

    def root_folder(self) -> str:
        return self.call("GET", "/api/v1/files/root", expect=200).json()["folder"]["id"]

    def mkdir(self, parent: str, name: str) -> str:
        r = self.call("POST", f"/api/v1/files/{parent}/folders", json={"name": name}, expect=201)
        return r.json()["id"]

    def upload(self, parent: str, path: Path, name: str | None = None) -> str:
        size = path.stat().st_size
        with open(path, "rb") as body:
            r = self.call(
                "PUT", f"/api/v1/files/{parent}/upload", params={"name": name or path.name},
                content=body, headers={"Content-Length": str(size)},
                expect=201,
            )  # fmt: skip
        return r.json()["id"]

    def upload_bytes(self, parent: str, name: str, data: bytes) -> str:
        r = self.call(
            "PUT", f"/api/v1/files/{parent}/upload", params={"name": name}, content=data,
            expect=201,
        )  # fmt: skip
        return r.json()["id"]

    def config(self, **values: Any) -> dict:
        """PUT /api/v1/admin/media/config met deze waarden (de rest zoals ze zijn)."""
        current = self.call("GET", "/api/v1/admin/media", expect=200).json().get("config") or {}
        body = {**{k: v for k, v in current.items() if k != "bench"}, **values}
        return self.call("PUT", "/api/v1/admin/media/config", json=body, expect=200).json()

    def bench(self, seconds: float = 180) -> dict:
        """De meting (POST …/bench) en wachten tot hij in de instellingen staat."""
        before = (self.call("GET", "/api/v1/admin/media").json().get("config") or {}).get("bench")
        self.call("POST", "/api/v1/admin/media/bench", expect=202)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            time.sleep(2)
            found = (self.call("GET", "/api/v1/admin/media").json().get("config") or {}).get(
                "bench"
            )
            if found and found != before:
                return found
        raise RuntimeError("de meting kwam niet klaar")

    def cli(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "app.cli", *args], cwd=API_DIR, env=self.environment(),
            capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip


# --- de sampler ---------------------------------------------------------------------------------


@dataclass
class Sample:
    t: float
    app_ticks: int
    worker_ticks: int  # de worker, zijn levende kinderen en de kinderen die al weg zijn
    machine_busy: int
    machine_total: int
    app_rss_kb: int
    worker_rss_kb: int  # de worker zelf
    ffmpeg_rss_kb: int  # alle ffmpeg en ffprobe onder de worker samen
    ffmpeg: int


class Sampler(threading.Thread):
    """Elke interval s: de CPU en de RSS van de app, de worker en zijn ffmpeg, en de machine."""

    def __init__(self, app: App, interval: float = 0.5) -> None:
        super().__init__(daemon=True, name="sampler")
        self.app = app
        self.interval = interval
        self.samples: list[Sample] = []
        self.marks: list[tuple[str, float]] = []
        self._halt = threading.Event()
        self._dead: dict[int, int] = {}  # ticks van ffmpeg die weg zijn maar nog niet geteld
        self.max_ffmpeg = 0

    def mark(self, name: str) -> float:
        now = time.monotonic()
        self.marks.append((name, now))
        return now

    def run(self) -> None:
        while not self._halt.wait(self.interval):
            with contextlib.suppress(Exception):
                self.samples.append(self._one())

    def _one(self) -> Sample:
        procs = table()
        now = time.monotonic()
        app_pid = self.app.pid
        app = procs.get(app_pid) if app_pid else None
        worker = None
        kids: list[Proc] = []
        if app is not None:
            for p in descendants(app.pid, procs):
                if p.ppid == app.pid and "app.media.worker" in cmdline(p.pid):
                    worker = p
            if worker is not None:
                kids = descendants(worker.pid, procs)
        ff = [k for k in kids if k.comm in FFMPEG and k.state not in ("Z", "X")]
        self.max_ffmpeg = max(self.max_ffmpeg, len(ff))
        busy, total = machine_ticks()
        worker_ticks = 0
        if worker is not None:
            worker_ticks = worker.ticks + worker.child_ticks + sum(k.ticks for k in kids)
        return Sample(
            t=now,
            app_ticks=app.ticks if app is not None else 0,
            worker_ticks=worker_ticks,
            machine_busy=busy,
            machine_total=total,
            app_rss_kb=app.rss_kb if app is not None else 0,
            worker_rss_kb=worker.rss_kb if worker is not None else 0,
            ffmpeg_rss_kb=sum(k.rss_kb for k in ff),
            ffmpeg=len(ff),
        )

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=5)

    def window(self, start: float, end: float) -> dict[str, Any]:
        """De CPU (cores, gemiddeld) en de hoogste RSS tussen start en end (time.monotonic)."""
        part = [s for s in self.samples if start <= s.t <= end]
        if len(part) < 2:
            return {}
        a, b = part[0], part[-1]
        wall = b.t - a.t
        ncpu = os.cpu_count() or 1

        def cores(x: int, y: int) -> float:
            return round(max(0, y - x) / TICK / wall, 3) if wall > 0 else 0.0

        machine = (b.machine_busy - a.machine_busy) / max(1, b.machine_total - a.machine_total)
        worker_cores = cores(a.worker_ticks, b.worker_ticks)
        app_cores = cores(a.app_ticks, b.app_ticks)
        return {
            "seconden": round(wall, 1),
            "app_cores": app_cores,
            "worker_cores": worker_cores,
            "machine_cores": round(machine * ncpu, 3),
            "anders_cores": round(max(0.0, machine * ncpu - app_cores - worker_cores), 3),
            "app_rss_mb_max": round(max(s.app_rss_kb for s in part) / 1024, 1),
            "worker_rss_mb_max": round(max(s.worker_rss_kb for s in part) / 1024, 1),
            "ffmpeg_rss_mb_max": round(max(s.ffmpeg_rss_kb for s in part) / 1024, 1),
            "worker_totaal_rss_mb_max": round(
                max(s.worker_rss_kb + s.ffmpeg_rss_kb for s in part) / 1024, 1
            ),
            "ffmpeg_max": max(s.ffmpeg for s in part),
        }


# --- de omgeving van de meting ----------------------------------------------------------------


def conditions() -> dict[str, Any]:
    """Wat de uitkomst mede bepaalt: de machine, ffmpeg en de commit."""

    def run(*cmd: str) -> str:
        with contextlib.suppress(Exception):
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            return out.stdout.strip()
        return ""

    model = ""
    with contextlib.suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    return {
        "datum": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "commit": run("git", "-C", str(REPO), "rev-parse", "--short", "HEAD"),
        "cpu": model,
        "cores": os.cpu_count(),
        "geheugen_gib": round(int(Path("/proc/meminfo").read_text().split()[1]) / 1024**2, 1),
        "ffmpeg": run("ffmpeg", "-version").splitlines()[0] if run("ffmpeg", "-version") else "",
        "worker_cores": sorted(CPUS),
        "max_procs": MAX_PROCS,
    }
