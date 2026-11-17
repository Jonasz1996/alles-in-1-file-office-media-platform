"""Het harnas voor de e2e van media (fase 5, §3.6, §4.2 en §4.3 van docs/fase-5/bouwontwerp.md).

Uitleg en gebruik: docs/fase-5/e2e.md. Kort:

- MediaApp: een eigen Server met media aan (PLATFORM_MEDIA_*), de lokale worker als kind van
  de app, en de nep-providers als proces (tests/media/fake_providers/process.py). Met
  E2E_MEDIA_WORKER verbindt de app met die socket (de worker-container; M1) in plaats van
  zelf een worker te starten, met E2E_MEDIA_WORKER_KEY als sleutel van hello. Gemaakt door
  de fixtures media_app en media in conftest.py;
- restart(outage_s=…): de app stoppen op pid, zoveel seconden wachten en weer starten, op
  dezelfde poort (R06); kill(): alles op pid weg (de deadline);
- pin(cpus): de worker en zijn kinderen op die cores (taskset -c 0-2 voor media_perf, regel
  14), ook na een herstart;
- upload(account, naam): een fixture van tests/media/fixtures.py (gemaakt met echte ffmpeg,
  één tegelijk) via de API in een map; analyse(sha) en wait_probe(sha): de job media.analyse
  van de app laten proben (zoals M2 dat straks zelf doet), en de rij in media_probe lezen;
- cli(…): `python -m app.cli` met de omgeving van de app (media-status ziet de worker);
- FfmpegWatch: de fixture ffmpeg_count. Een draad kijkt elke 0,5 s naar ffmpeg en ffprobe in
  de procesboom van de app (de kinderen van de worker, en de previews die tot M1 nog in de
  app draaien), via /proc. Wie hij één keer zag, volgt hij ook als die van ouder wisselt (een
  gedode worker). Een vierde tegelijk laat de test falen; na de test moet het 0 zijn;
- Deadline: elke mediatest een eigen tijdsgrens (90 s, 300 s voor media_perf). Daarna een dump
  van alle draden, en de app, de worker en ffmpeg weg op pid; de test faalt;
- de browser: Chromium met --autoplay-policy=no-user-gesture-required; MediaBrowsers zet het
  init-script FRAME_JS in elke context, en voor media_perf 40 Mbit/s en 20 ms via CDP
  (Network.emulateNetworkConditions) op elke pagina;
- hulpjes: open_app (ingelogd, het token ververst), first_frame_ms (requestVideoFrameCallback),
  mark_start, wait_playing, codec_support, codec_supported en require_codec (overslaan met een
  reden, of falen met MEDIA_REQUIRE_H264=1).

Alles stopt op pid, nooit op naam. conftest importeert deze module pas in een fixture (deze
module importeert conftest).
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import hashlib
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from conftest import (
    API_DIR,
    Account,
    Browsers,
    Server,
    cdp_profile,
    chromium_path,
    create_user,
    make_admin,
)

# §3.6: de lus van de scanner op 0,5 s, en nooit meer dan 3 ffmpeg (regel 13)
MAX_PROCS = 3
MEDIA_ENV = {
    "PLATFORM_MEDIA_ENABLED": "true",
    "PLATFORM_MEDIA_SCAN_TICK": "0.5",
    "PLATFORM_MEDIA_MAX_TRANSCODES": "3",
    "PLATFORM_MEDIA_MAX_PROCS": str(MAX_PROCS),
}
# De grens van de LXC voor de worker (regel 14): taskset -c 0-2
PERF_CPUS = frozenset({0, 1, 2})
# §4.2: de deadline per test
DEADLINE_S = 90.0
DEADLINE_PERF_S = 300.0
# §3.6: het netwerk van media_perf
PERF_NETWORK = {"latency_ms": 20.0, "down_mbit": 40.0}
WATCH_INTERVAL = 0.5
FFMPEG_NAMES = ("ffmpeg", "ffprobe")
# Een test met H.264 of AAC faalt in plaats van over te slaan (§4.3; S13 beslist voor CI)
REQUIRE_H264 = "MEDIA_REQUIRE_H264"
STRICT_CODECS = ("avc1", "avc3", "mp4a", "h264", "aac")
BROWSER_ARGS = ["--no-proxy-server", "--autoplay-policy=no-user-gesture-required"]


def run_async[T](fn: Callable[[], Awaitable[T]]) -> T:
    """Async code van de app vanuit een synchrone test: in een eigen draad met een eigen lus,
    los van die van Playwright."""
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(lambda: asyncio.run(fn())).result()


# --- processen, via /proc ---


def _stat(pid: int) -> tuple[int, int, str] | None:
    """(ppid, starttime, state) uit /proc/<pid>/stat, of None als hij weg is. starttime
    onderscheidt een hergebruikte pid; state Z is een zombie (al gestopt)."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    rest = data[data.rindex(")") + 2 :].split()
    return int(rest[1]), int(rest[19]), rest[0]


def comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


Table = dict[int, tuple[int, int, str]]


def process_table() -> Table:
    """pid → (ppid, starttime, state) van elk proces dat er nu is."""
    table: Table = {}
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            found = _stat(int(entry.name))
            if found is not None:
                table[int(entry.name)] = found
    return table


def descendants(roots: Iterable[int], table: Table | None = None) -> set[int]:
    """Alle nakomelingen van roots (zonder roots zelf)."""
    table = process_table() if table is None else table
    children: dict[int, list[int]] = {}
    for pid, (ppid, _start, _state) in table.items():
        children.setdefault(ppid, []).append(pid)
    out: set[int] = set()
    todo = [r for r in roots if r]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in out:
                out.add(child)
                todo.append(child)
    return out


def alive(pid: int, start: int) -> bool:
    """Leeft dit proces nog (dezelfde starttime, geen zombie)?"""
    found = _stat(pid)
    return found is not None and found[1] == start and found[2] not in ("Z", "X")


def kill_pid(pid: int, start: int, sig: int = signal.SIGKILL) -> bool:
    """Alleen dit proces, en alleen als het nog hetzelfde is (dezelfde starttime)."""
    if not alive(pid, start):
        return False
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, sig)
        return True
    return False


def wait_gone(pids: Iterable[tuple[int, int]], seconds: float = 5) -> list[int]:
    """Tot deze processen (pid, starttime) echt weg zijn; geeft wie er dan nog is."""
    todo = list(pids)
    end = time.monotonic() + seconds
    while True:
        todo = [(pid, start) for pid, start in todo if alive(pid, start)]
        if not todo or time.monotonic() > end:
            return [pid for pid, _start in todo]
        time.sleep(0.05)


def set_affinity(pid: int, cpus: Iterable[int]) -> None:
    """Zoals `taskset -a -p -c …`: elke draad van het proces (kinderen erven het)."""
    wanted = set(cpus)
    for task in Path(f"/proc/{pid}/task").iterdir():
        with contextlib.suppress(ProcessLookupError, OSError):
            os.sched_setaffinity(int(task.name), wanted)


# --- de app met media ---


def media_env(providers: Any | None) -> dict[str, str]:
    """PLATFORM_MEDIA_* voor een Server (§3.6), met de URL's van de nep-providers."""
    env = dict(MEDIA_ENV)
    if providers is not None:
        env.update(providers.env())
    worker = os.environ.get("E2E_MEDIA_WORKER")
    if worker:
        env["PLATFORM_MEDIA_WORKER"] = worker
        # de sleutel van hello (review S1): dezelfde als die van de worker-container
        env["PLATFORM_MEDIA_WORKER_KEY"] = os.environ.get("E2E_MEDIA_WORKER_KEY", "")
    return env


@dataclass
class Uploaded:
    id: str
    name: str
    sha: str
    path: Path
    size: int


# Elke MediaApp die draait, voor de deadline (die stopt ze allemaal op pid)
RUNNING: set[MediaApp] = set()


@dataclass(eq=False)
class MediaApp:
    """Een Server met media aan en zijn lokale worker; de nep-providers ernaast."""

    server: Server
    admin: Account
    providers: Any | None = None
    cpus: frozenset[int] | None = None
    _seen: dict[int, tuple[int, str]] = field(default_factory=dict, repr=False)

    # --- starten en stoppen ---

    def start(self) -> None:
        self.server.start()
        RUNNING.add(self)
        self.wait_worker()
        if self.cpus is not None:
            self.pin(self.cpus)

    def stop(self) -> list[str]:
        """De app netjes stoppen (SIGTERM op zijn pid; de lifespan stopt de worker). Geeft
        wat daarna nog leefde en op pid is weggehaald (een lek)."""
        before = self.tree()
        self.server.stop()
        RUNNING.discard(self)
        return self._reap(before)

    def kill(self) -> list[str]:
        """Alles meteen weg op pid (de deadline): de app, de worker en elke ffmpeg."""
        before = self.tree()
        proc = self.server.proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(proc.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
        RUNNING.discard(self)
        return self._reap(before)

    def _reap(self, before: dict[int, tuple[int, str]]) -> list[str]:
        left = []
        killed = []
        for pid, (start, name) in {**self._seen, **before}.items():
            if kill_pid(pid, start):
                left.append(f"{pid} {name}")
                killed.append((pid, start))
        self._seen.clear()
        wait_gone(killed)
        return left

    def restart(self, outage_s: float = 0, env: dict[str, str] | None = None) -> list[str]:
        """Stoppen op pid, outage_s wachten (zoals een update, R06), en weer starten op dezelfde
        poort. env: andere instellingen voor de nieuwe start (bijvoorbeeld PATH zonder
        ffmpeg)."""
        left = self.stop()
        if outage_s:
            time.sleep(outage_s)
        if env:
            self.server.extra_env.update(env)
        self.start()
        return left

    def ensure_running(self) -> None:
        """Na een deadline (of een test die de app stopte): weer starten."""
        proc = self.server.proc
        if proc is None or proc.poll() is not None:
            self.server.proc = None
            self.start()

    # --- processen ---

    @property
    def pid(self) -> int | None:
        proc = self.server.proc
        return proc.pid if proc is not None and proc.poll() is None else None

    def tree(self) -> dict[int, tuple[int, str]]:
        """De nakomelingen van de app: pid → (starttime, comm). Wie hier staat, onthoudt de
        app (ook als hij later van ouder wisselt)."""
        root = self.pid
        if root is None:
            return {}
        table = process_table()
        found = {pid: (table[pid][1], comm(pid)) for pid in descendants([root], table)}
        self._seen.update(found)
        return found

    def worker_pid(self) -> int | None:
        """De lokale worker: het kind van de app met app.media.worker op zijn opdrachtregel."""
        for pid in sorted(self.tree()):
            if "app.media.worker" in cmdline(pid):
                return pid
        return None

    def wait_worker(self, seconds: float = 20) -> int | None:
        """Tot de lokale worker luistert (de socket bestaat); None met E2E_MEDIA_WORKER."""
        if "PLATFORM_MEDIA_WORKER" in self.server.extra_env:
            return None
        socket_path = self.server.data_dir / "media" / "run" / "worker.sock"
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            pid = self.worker_pid()
            if pid is not None and socket_path.exists():
                return pid
            time.sleep(0.05)
        raise AssertionError(f"geen lokale worker binnen {seconds} s:\n{self.server.log_tail()}")

    def pin(self, cpus: Iterable[int] | None) -> None:
        """De worker (en wat hij start) op deze cores; None: alle cores van deze machine."""
        wanted = frozenset(cpus) if cpus is not None else None
        self.cpus = wanted
        pid = self.worker_pid()
        if pid is None:
            return
        if wanted is None:
            set_affinity(pid, os.sched_getaffinity(0))
        else:
            available = os.sched_getaffinity(0)
            set_affinity(pid, (wanted & available) or available)

    # --- de API en de cli ---

    def user(self, **kw: Any) -> Account:
        return create_user(self.server, self.admin, **kw)

    def folder(self, account: Account, name: str, parent: str | None = None) -> str:
        r = self.server.call(
            account,
            "POST",
            f"/api/v1/files/{parent or account.root_id}/folders",
            json={"name": name},
            expect=201,
        )
        return r.json()["id"]

    def upload(
        self, account: Account, fixture: str, parent: str | None = None, name: str | None = None
    ) -> Uploaded:
        """Een fixture (gemaakt als hij er nog niet is) via PUT …/upload."""
        from tests.media import fixtures

        path = fixtures.make(fixture)
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        size = path.stat().st_size
        with open(path, "rb") as body:
            r = self.server.call(
                account,
                "PUT",
                f"/api/v1/files/{parent or account.root_id}/upload",
                params={"name": name or Path(fixture).name},
                content=body,
                headers={"Content-Length": str(size)},
                expect=201,
            )
        node = r.json()
        return Uploaded(node["id"], node["name"], digest.hexdigest(), path, size)

    def settings(self) -> Any:
        from app.config import Settings

        return Settings(
            _env_file=None,
            data_dir=self.server.data_dir,
            web_dist=self.server.web_dist,
            jwt_secret="e2e-alleen-voor-de-database-" + "x" * 8,
        )

    def analyse(self, sha: str) -> None:
        """Zet de job media.analyse voor deze inhoud klaar in de database van de app (WAL);
        de rij media van de app pakt hem op (hooguit jobs.POLL s later) en laat de worker
        proben. M2 doet dat straks zelf bij het scannen."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app import db, jobs
        from app.media import analysis

        settings = self.settings()

        async def go() -> None:
            engine = db.get_engine(settings)
            try:
                async with async_sessionmaker(engine)() as session:
                    payload = {"sha": sha, "library_id": None}
                    await jobs.enqueue(session, analysis.JOB, payload, key=f"{analysis.JOB}:{sha}")
                    await session.commit()
            finally:
                await engine.dispose()
                db._engine.cache_clear()

        run_async(go)

    def probe_row(self, sha: str) -> dict[str, Any] | None:
        """De rij in media_probe (alleen lezen), of None."""
        import json

        uri = f"file:{self.server.data_dir / 'platform.db'}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT state, container, duration_ms, streams, error, version"
                " FROM media_probe WHERE blob_sha = ?",
                (sha,),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["streams"] = json.loads(out["streams"] or "[]")
        return out

    def wait_probe(self, sha: str, seconds: float = 30) -> dict[str, Any]:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            row = self.probe_row(sha)
            if row is not None:
                return row
            time.sleep(0.25)
        raise AssertionError(f"geen probe van {sha[:12]} binnen {seconds} s")

    def cli(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        """`python -m app.cli …` met de omgeving van de app (dezelfde datamap en socket)."""
        return subprocess.run(
            [sys.executable, "-m", "app.cli", *args],
            cwd=API_DIR,
            env=self.server.environment(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


def start_media(base: Path, providers: Any | None = None) -> MediaApp:
    """Een eigen Server met media aan (fixture media_app), met de nep-providers."""
    server = Server(base / "data", env=media_env(providers))
    server.start()
    try:
        admin = make_admin(server)
        app = MediaApp(server=server, admin=admin, providers=providers)
        RUNNING.add(app)
        app.wait_worker()
    except BaseException:
        server.stop()
        raise
    return app


def start_fake_providers(workdir: Path) -> Any:
    """De nep-providers als proces (§3.6, §4.4): op 127.0.0.1, op pid te stoppen."""
    from tests.media.fake_providers.process import FakeProvidersProcess

    fake = FakeProvidersProcess(workdir)
    fake.start()
    return fake


# --- ffmpeg_count ---


class FfmpegWatch:
    """Elke 0,5 s: hoeveel ffmpeg en ffprobe er in de boom van de app draaien (regel 13)."""

    def __init__(
        self,
        roots: Callable[[], Iterable[int]],
        *,
        limit: int = MAX_PROCS,
        interval: float = WATCH_INTERVAL,
        cpus: Callable[[], frozenset[int] | None] = lambda: None,
        pinned: Callable[[], int | None] = lambda: None,
    ) -> None:
        self.roots = roots
        self.limit = limit
        self.interval = interval
        self.cpus = cpus  # de cores van de worker (MediaApp.pin), of None
        self.pinned = pinned  # de pid van de worker: alleen zijn kinderen moeten daarop
        self.seen: dict[int, tuple[int, str]] = {}  # pid → (starttime, opdrachtregel)
        self.max = 0
        self.scans = 0
        self.over: list[str] = []
        self.affinity: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def scan(self) -> list[int]:
        """Eén keer kijken; geeft de pids die nu leven."""
        table = process_table()
        tree = descendants(list(self.roots()), table)
        with self._lock:
            for pid in tree:
                if comm(pid) in FFMPEG_NAMES and pid not in self.seen:
                    self.seen[pid] = (table[pid][1], cmdline(pid)[:300])
                    self._check_cpus(pid, table)
            now = [pid for pid, (start, _c) in self.seen.items() if alive(pid, start)]
            self.scans += 1
            self.max = max(self.max, len(now))
            if len(now) > self.limit:
                lines = [f"{pid}: {self.seen[pid][1]}" for pid in now]
                self.over.append(f"{len(now)} tegelijk:\n  " + "\n  ".join(lines))
        return now

    def _check_cpus(self, pid: int, table: Table) -> None:
        """Een kind van de vastgezette worker draait op diens cores (regel 14). De previews
        van de app (tot M1) horen daar niet bij."""
        wanted = self.cpus()
        worker = self.pinned() if wanted is not None else None
        if wanted is None or worker is None or pid not in descendants([worker], table):
            return
        with contextlib.suppress(OSError):
            got = os.sched_getaffinity(pid)
            if not got <= wanted:
                self.affinity.append(f"{pid} op {sorted(got)}")

    @property
    def count(self) -> int:
        return len(self.scan())

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.scan()
            except Exception:  # de draad mag nooit stoppen
                traceback.print_exc()

    def start(self) -> FfmpegWatch:
        self.scan()
        self._thread = threading.Thread(target=self._run, name="ffmpeg_count", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def wait_zero(self, seconds: float = 15) -> list[int]:
        """Tot er geen ffmpeg meer is (een preview of probe mag nog even afmaken)."""
        end = time.monotonic() + seconds
        left = self.scan()
        while left and time.monotonic() < end:
            time.sleep(0.2)
            left = self.scan()
        return left

    def kill_left(self) -> list[str]:
        """Wat er nog is, weg op pid (en wachten tot het weg is)."""
        out = []
        killed = []
        with self._lock:
            for pid, (start, line) in self.seen.items():
                if kill_pid(pid, start):
                    out.append(f"{pid}: {line}")
                    killed.append((pid, start))
        wait_gone(killed)
        return out


# --- de deadline ---


def thread_dump() -> str:
    """De stapel van elke draad (zoals faulthandler, maar als tekst voor het verslag)."""
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        parts.append(f"--- draad {names.get(ident, ident)} ---")
        parts.append("".join(traceback.format_stack(frame)))
    return "\n".join(parts)


class Deadline:
    """Een tijdsgrens per test (§4.2). Verloopt hij: een dump van alle draden, en elke
    MediaApp in RUNNING weg op pid (de app, de worker en ffmpeg). De fixture laat de test dan
    falen."""

    def __init__(self, seconds: float, on_expire: Callable[[], Any] | None = None) -> None:
        self.seconds = seconds
        self.on_expire = on_expire or self._stop_all
        self.expired = False
        self.dump = ""
        self.stopped: list[str] = []
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def _stop_all(self) -> list[str]:
        out = []
        for app in list(RUNNING):
            out += app.kill()
        return out

    def _fire(self) -> None:
        self.expired = True
        self.dump = thread_dump()
        with contextlib.suppress(Exception):
            faulthandler.dump_traceback(file=sys.__stderr__, all_threads=True)
        result = self.on_expire()
        self.stopped = list(result) if isinstance(result, list) else []

    def start(self) -> Deadline:
        self._timer.start()
        return self

    def cancel(self) -> None:
        self._timer.cancel()


# --- de browser ---

# In elke context van media: het eerste beeld van elke <video> via requestVideoFrameCallback
# (presentationTime, in de tijd van performance.now()), vanaf het eerste loadstart
FRAME_JS = r"""
(() => {
  const hook = (v) => {
    if (v.__e2eHooked || typeof v.requestVideoFrameCallback !== "function") return;
    v.__e2eHooked = true;
    v.requestVideoFrameCallback((now, meta) => {
      v.__e2eFirstFrame = {
        at: meta.presentationTime || now,
        mediaTime: meta.mediaTime,
        width: meta.width,
        height: meta.height,
      };
    });
  };
  document.addEventListener("loadstart", (e) => {
    if (e.target instanceof HTMLVideoElement) hook(e.target);
  }, true);
  window.__e2eMediaMark = () => {
    window.__e2eMediaT0 = performance.now();
    for (const v of document.querySelectorAll("video")) {
      delete v.__e2eFirstFrame;
      v.__e2eHooked = false;
      hook(v);
    }
  };
})();
"""


def launch_media_browser(pw: Any) -> Any:
    """Chromium zoals de e2e hem gebruiken, plus autoplay zonder gebaar (§3.6)."""
    from playwright.sync_api import expect

    expect.set_options(timeout=15_000)
    return pw.chromium.launch(
        executable_path=chromium_path(),
        args=BROWSER_ARGS,
        headless=os.environ.get("E2E_HEADED") != "1",
    )


class MediaBrowsers(Browsers):
    """Browsers op de app van media: met FRAME_JS, en voor media_perf het netwerk van §3.6 op
    elke pagina (CDP)."""

    def __init__(self, browser: Any, server: Server, *, perf: bool = False) -> None:
        super().__init__(browser, server)
        self.perf = perf
        self.network: list[dict[str, Any]] = []

    def new_context(self, **options: Any) -> Any:
        context = super().new_context(**options)
        context.add_init_script(FRAME_JS)
        if self.perf:
            context.on("page", lambda page: self._throttle(context, page))
        return context

    def _throttle(self, context: Any, page: Any) -> None:
        applied = cdp_profile(context, page, **PERF_NETWORK)
        applied.pop("session", None)
        self.network.append(applied)


def open_app(page: Any) -> None:
    """De app openen en wachten tot hij ingelogd is (het hoofdmenu). Daarna is het verversen
    van het token klaar: een navigatie midden in dat verversen breekt het antwoord af, en dan
    is de sessie weg (het oude refresh-cookie is al gebruikt)."""
    from playwright.sync_api import expect

    page.goto("/")
    expect(page.get_by_role("navigation", name="Hoofdmenu")).to_be_visible()


def mark_start(page: Any) -> None:
    """Vanaf nu meet first_frame_ms (en het volgende beeld telt als eerste)."""
    page.evaluate("() => window.__e2eMediaMark && window.__e2eMediaMark()")


def first_frame_ms(page: Any, selector: str = "video", timeout_ms: float = 15_000) -> float:
    """Ms van de start (mark_start, of het begin van de navigatie) tot het eerste beeld dat de
    <video> toonde (requestVideoFrameCallback)."""
    handle = page.wait_for_function(
        """(sel) => { const v = document.querySelector(sel);
          return v && v.__e2eFirstFrame
            ? v.__e2eFirstFrame.at - (window.__e2eMediaT0 || 0) : null; }""",
        arg=selector,
        timeout=timeout_ms,
    )
    return float(handle.json_value())


def wait_playing(
    page: Any, t: float = 0.2, selector: str = "video", timeout_ms: float = 15_000
) -> float:
    """Tot de media speelt en voorbij t seconden is (geen fout, niet gepauzeerd); geeft de
    tijd."""
    handle = page.wait_for_function(
        """([sel, t]) => { const m = document.querySelector(sel);
          return m && !m.error && !m.paused && m.currentTime >= t ? m.currentTime : null; }""",
        arg=[selector, t],
        timeout=timeout_ms,
    )
    return float(handle.json_value())


def _mime(codec: str) -> str:
    if "/" in codec:
        return codec
    audio = codec.split(".")[0] in ("mp4a", "opus", "flac", "ac-3", "ec-3", "mp3")
    return f'{"audio" if audio else "video"}/mp4; codecs="{codec}"'


def codec_support(page: Any, codecs: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per codec (of mime): {mse: MediaSource.isTypeSupported, native: canPlayType}."""
    types = {c: _mime(c) for c in codecs}
    found = page.evaluate(
        """(types) => { const v = document.createElement("video"); const out = {};
          for (const [k, t] of Object.entries(types)) out[k] = {
            type: t,
            mse: typeof MediaSource !== "undefined" && MediaSource.isTypeSupported(t),
            native: v.canPlayType(t),
          };
          return out; }""",
        types,
    )
    return dict(found)


def codec_supported(page: Any, codec: str, how: str = "any") -> bool:
    """Kan deze browser het? how: mse, native of any."""
    found = codec_support(page, [codec])[codec]
    if how == "mse":
        return bool(found["mse"])
    if how == "native":
        return bool(found["native"])
    return bool(found["mse"] or found["native"])


def require_codec(page: Any, codec: str, how: str = "any") -> None:
    """Een test die deze codec nodig heeft: overslaan met de reden, of (H.264 en AAC met
    MEDIA_REQUIRE_H264=1) falen."""
    if codec_supported(page, codec, how):
        return
    version = page.context.browser.version if page.context.browser else "?"
    reason = f"de browser ({version}) kan {codec} niet ({how})"
    strict = os.environ.get(REQUIRE_H264) == "1" and any(c in codec.lower() for c in STRICT_CODECS)
    if strict:
        pytest.fail(f"{reason}, en {REQUIRE_H264}=1", pytrace=False)
    pytest.skip(reason)
