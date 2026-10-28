"""Gedeeld door de metingen van office (fase 4, §4.7 van docs/fase-4/bouwontwerp.md).

Wat hier staat:
- start_office(): een eigen server (python -m app.serve, de productie-build uit web/dist) tegen
  de echte DS uit E2E_REAL_DS, met de timers van productie (de ronde en de gezondheid elke 30 s,
  het herstel elke 10 minuten) in plaats van die van de e2e (elke 1 of 2 s): die zouden de cpu
  van de app meten in plaats van wat een gebruiker doet. Met clock=True hoort DS de app via
  CallbackClock;
- CallbackClock: een kleine TCP-doorgever tussen DS en de interne luisteraar van de app, die van
  elk verzoek de tijd van de eerste byte tot de kop van het antwoord vastlegt. De app antwoordt
  op een callback pas na de commit (saving.py), dus dat is "callback tot versie in de database";
- procfs: cpu (utime + stime) en geheugen (VmRSS) van de server, en hoe druk de machine is
  (busy(), wait_quiet()), zodat een meting nooit naast een achtergebleven test draait;
- de omstandigheden van de machine (cpu's, geheugen, de grenzen van de DS-container, Chromium);
- team(): een eigenaar met een gedeelde map en wat bestanden, en wait_background(): tot de
  PDF-voorbeelden (O3) en andere taken klaar zijn.

Geen eigen afhankelijkheden: alleen de standaardbibliotheek, httpx en wat de e2e al hebben.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_watchdog  # noqa: E402

from conftest import (  # noqa: E402 - pas na het pad naar e2e/
    Account,
    Server,
    create_user,
    free_port,
    make_admin,
)
from office_harness import (  # noqa: E402
    REAL_READY_SECONDS,
    DocStub,
    Office,
    office_env,
    wait_real_ds,
)

TICK = os.sysconf("SC_CLK_TCK")
# De timers van de e2e die we terugzetten naar de standaard van config.py
E2E_TIMERS = (
    "PLATFORM_OFFICE_SWEEP_SECONDS",
    "PLATFORM_OFFICE_RECOVERY_SECONDS",
    "PLATFORM_OFFICE_HEALTH_SECONDS",
)


def tmpdir(prefix: str) -> Path:
    """Een nieuwe map voor de data van een meting: onder E2E_PERF_TMP als die staat (niet via
    TMPDIR: Chromium wil een kort pad voor zijn profiel), anders onder de standaard."""
    import tempfile

    root = os.environ.get("E2E_PERF_TMP") or None
    if root:
        Path(root).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


# --- de echte DS ---


def real_ds() -> tuple[str, str] | None:
    """(adres, geheim) uit E2E_REAL_DS en E2E_REAL_DS_SECRET_FILE, of None. Het geheim staat
    alleen in een bestand, nooit in de omgeving of de uitvoer."""
    url = os.environ.get("E2E_REAL_DS")
    if not url:
        return None
    secret_file = os.environ.get("E2E_REAL_DS_SECRET_FILE")
    if not secret_file or not Path(secret_file).is_file():
        raise RuntimeError(
            "E2E_REAL_DS staat, maar E2E_REAL_DS_SECRET_FILE wijst niet naar een bestand"
        )
    return url.rstrip("/"), Path(secret_file).read_text().strip()


def need_real_ds() -> tuple[str, str]:
    found = real_ds()
    if found is None:
        raise SystemExit(
            "Deze meting heeft een echte DS nodig: E2E_REAL_DS en E2E_REAL_DS_SECRET_FILE"
            " (docs/fase-4/e2e.md, 'Met de echte DS')."
        )
    return found


def ds_container(url: str) -> dict[str, Any]:
    """De container van DS (op zijn gepubliceerde poort) en zijn grenzen, als docker er is."""
    port = url.rsplit(":", 1)[-1].split("/", 1)[0]
    out: dict[str, Any] = {"url": url}
    try:
        names = subprocess.run(
            ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.split()
        if not names:
            return out
        raw = subprocess.run(
            ["docker", "inspect", names[0]], capture_output=True, text=True, timeout=20, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    info = json.loads(raw)[0]
    host = info.get("HostConfig") or {}
    out.update(
        name=names[0],
        image=(info.get("Config") or {}).get("Image"),
        image_id=info.get("Image"),
        memory_bytes=host.get("Memory") or None,
        memory_swap_bytes=host.get("MemorySwap") or None,
        cpus=(host.get("NanoCpus") or 0) / 1e9 or None,
        pids_limit=host.get("PidsLimit"),
        limits_applied=bool(host.get("Memory") and host.get("NanoCpus")),
    )
    return out


# --- de machine ---


def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def machine(url: str | None = None, browser_version: str | None = None) -> dict[str, Any]:
    """De omstandigheden waaronder gemeten is."""
    mem = {
        line.split(":")[0]: int(line.split()[1]) * 1024
        for line in _read("/proc/meminfo").splitlines()
        if line.split(":")[0] in ("MemTotal", "MemAvailable")
    }
    model = next(
        (
            line.split(":", 1)[1].strip()
            for line in _read("/proc/cpuinfo").splitlines()
            if line.startswith("model name")
        ),
        None,
    )
    out: dict[str, Any] = {
        "cpus": os.cpu_count(),
        "cpu_model": model,
        "mem_total_bytes": mem.get("MemTotal"),
        "mem_available_bytes": mem.get("MemAvailable"),
        "kernel": os.uname().release,
        "python": sys.version.split()[0],
        "load_avg": [round(x, 2) for x in os.getloadavg()],
    }
    if browser_version:
        out["chromium"] = browser_version
    if url:
        out["ds"] = ds_container(url)
    return out


# --- procfs ---


def _stat(pid: int) -> list[str] | None:
    text = _read(f"/proc/{pid}/stat")
    if not text:
        return None
    return text.rsplit(")", 1)[1].split()


def cpu_seconds(pid: int) -> float:
    """utime + stime van één proces (alle draden), in seconden."""
    rest = _stat(pid)
    if rest is None:
        raise ProcessLookupError(pid)
    return (int(rest[11]) + int(rest[12])) / TICK


def descendants(pid: int) -> list[int]:
    found: list[int] = []
    stack = [pid]
    while stack:
        current = stack.pop()
        for task in Path(f"/proc/{current}/task").glob("*/children"):
            with contextlib.suppress(OSError):
                for child in task.read_text().split():
                    found.append(int(child))
                    stack.append(int(child))
    return found


def tree_cpu(pid: int) -> float:
    """cpu van het proces en zijn kinderen die nu leven (de app start soms een hulpproces)."""
    total = cpu_seconds(pid)
    for child in descendants(pid):
        with contextlib.suppress(ProcessLookupError):
            total += cpu_seconds(child)
    return total


def rss_bytes(pid: int) -> int:
    for line in _read(f"/proc/{pid}/status").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise ProcessLookupError(pid)


def _all_cpu() -> dict[int, tuple[str, float]]:
    out: dict[int, tuple[str, float]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        text = _read(f"/proc/{entry.name}/stat")
        if not text or ")" not in text:
            continue
        comm = text[text.find("(") + 1 : text.rfind(")")]
        rest = text.rsplit(")", 1)[1].split()
        out[int(entry.name)] = (comm, (int(rest[11]) + int(rest[12])) / TICK)
    return out


def _idle_total() -> tuple[float, float]:
    fields = _read("/proc/stat").splitlines()[0].split()[1:]
    values = [int(v) for v in fields[:8]]
    idle = values[3] + values[4]
    return idle / TICK, sum(values) / TICK


def busy(seconds: float = 3.0, top: int = 5) -> dict[str, Any]:
    """Hoeveel cores de machine gebruikt (gemiddeld over seconds) en de drukste processen."""
    before, (idle0, total0) = _all_cpu(), _idle_total()
    time.sleep(seconds)
    after, (idle1, total1) = _all_cpu(), _idle_total()
    cpus = os.cpu_count() or 1
    elapsed = (total1 - total0) / cpus or seconds
    used = ((total1 - total0) - (idle1 - idle0)) / elapsed
    deltas = [
        (after[pid][1] - before[pid][1], pid, after[pid][0])
        for pid in after
        if pid in before and after[pid][1] > before[pid][1]
    ]
    deltas.sort(reverse=True)
    return {
        "cores": round(used, 2),
        "seconds": seconds,
        "top": [
            {"pid": pid, "comm": comm, "cores": round(d / seconds, 2)}
            for d, pid, comm in deltas[:top]
        ],
    }


def wait_quiet(max_cores: float = 0.3, timeout: float = 180, seconds: float = 3.0) -> dict:
    """Wacht tot de hele machine (ook DS) onder max_cores zit. Geeft de laatste meting; lukt
    het niet binnen timeout, dan staat quiet op False (de meting noemt dat)."""
    end = time.monotonic() + timeout
    last = busy(seconds)
    while last["cores"] > max_cores and time.monotonic() < end:
        last = busy(seconds)
    last["quiet"] = last["cores"] <= max_cores
    return last


def stats(values: list[float], digits: int = 3) -> dict[str, Any]:
    """Mediaan, kleinste, grootste en spreiding (grootste min kleinste) van de herhalingen."""
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None, "spread": None}
    return {
        "n": len(values),
        "median": round(statistics.median(values), digits),
        "min": round(min(values), digits),
        "max": round(max(values), digits),
        "spread": round(max(values) - min(values), digits),
        "values": [round(v, digits) for v in values],
    }


# --- de klok tussen DS en de interne luisteraar ---


@dataclass
class Exchange:
    """Eén verzoek van DS aan de app en wanneer het antwoord begon (time.monotonic)."""

    method: str
    path: str
    started: float
    body_done: float | None = None
    answered: float | None = None
    status: int | None = None
    callback_status: int | None = None  # het veld status van een callback
    # Ook uit de callback (geen geheim): de key (alleen in het geheugen, om te koppelen aan een
    # bestand; de metingen schrijven hem niet weg), forcesavetype (0 commando, 1 knop, 2 klok
    # van DS) en userdata (bij de klok van de app "timer", bij het stoppen "stop")
    key: str | None = None
    forcesavetype: int | None = None
    userdata: str | None = None

    def seconds(self) -> float | None:
        return None if self.answered is None else self.answered - self.started


class _Conn:
    """Leest de HTTP-verzoeken en -antwoorden op één verbinding mee (zonder iets te
    veranderen). Geen pipelining: DS wacht op elk antwoord."""

    def __init__(self, clock: CallbackClock) -> None:
        self.clock = clock
        self.req = bytearray()
        self.req_body_left = 0
        self.current: Exchange | None = None
        self.pending: list[Exchange] = []
        self.res = bytearray()
        self.res_body_left = 0
        self.res_chunked = False
        self.broken = False

    def from_ds(self, data: bytes) -> None:
        if self.broken:
            return
        try:
            self._requests(data)
        except ValueError:
            self.broken = True

    def _requests(self, data: bytes) -> None:
        now = time.monotonic()
        view = memoryview(data)
        while view:
            if self.req_body_left:
                take = min(self.req_body_left, len(view))
                if self.current is not None and self.current.path.startswith(
                    "/internal/office/callback"
                ):
                    self.req += view[:take]
                view = view[take:]
                self.req_body_left -= take
                if not self.req_body_left:
                    self._body_done(now)
                continue
            if not self.req and self.current is None:
                self.current = Exchange("", "", now)
            self.req += view
            view = memoryview(b"")
            end = self.req.find(b"\r\n\r\n")
            if end < 0:
                return
            head, rest = bytes(self.req[:end]), bytes(self.req[end + 4 :])
            self.req = bytearray()
            line, *headers = head.decode("latin-1").split("\r\n")
            method, path, _ = line.split(" ", 2)
            assert self.current is not None
            self.current.method, self.current.path = method, path
            length = 0
            for h in headers:
                name, _, value = h.partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
                if name.strip().lower() == "transfer-encoding":
                    raise ValueError("chunked verzoek: niet meegelezen")
            self.req_body_left = length
            if not length:
                self._body_done(now)
            view = memoryview(rest)

    def _body_done(self, now: float) -> None:
        ex = self.current
        self.current = None
        if ex is None:
            return
        ex.body_done = now
        if ex.path.startswith("/internal/office/callback") and self.req:
            # Alleen status, key, forcesavetype en userdata; het token blijft hier en komt
            # nergens in de uitvoer
            with contextlib.suppress(ValueError, AttributeError, TypeError):
                body = json.loads(bytes(self.req))
                ex.callback_status = int(body.get("status"))
                ex.key = body.get("key") if isinstance(body.get("key"), str) else None
                if isinstance(body.get("forcesavetype"), int):
                    ex.forcesavetype = body["forcesavetype"]
                if isinstance(body.get("userdata"), str):
                    ex.userdata = body["userdata"][:32]
        self.req = bytearray()
        self.pending.append(ex)
        self.clock._record(ex)

    def from_app(self, data: bytes) -> None:
        if self.broken:
            return
        try:
            self._responses(data)
        except ValueError:
            self.broken = True

    def _responses(self, data: bytes) -> None:
        now = time.monotonic()
        view = memoryview(data)
        while view:
            if self.res_body_left:
                take = min(self.res_body_left, len(view))
                view = view[take:]
                self.res_body_left -= take
                continue
            if self.res_chunked:
                # Het einde van een chunked antwoord: 0\r\n\r\n (de app geeft content-length)
                self.res += view
                view = memoryview(b"")
                end = self.res.find(b"\r\n0\r\n\r\n")
                if end < 0:
                    if len(self.res) > 64:
                        del self.res[:-64]
                    return
                view = memoryview(bytes(self.res[end + 7 :]))
                self.res = bytearray()
                self.res_chunked = False
                continue
            self.res += view
            view = memoryview(b"")
            end = self.res.find(b"\r\n\r\n")
            if end < 0:
                return
            head, rest = bytes(self.res[:end]), bytes(self.res[end + 4 :])
            self.res = bytearray()
            line, *headers = head.decode("latin-1").split("\r\n")
            status = int(line.split(" ", 2)[1])
            if self.pending and status >= 200:
                ex = self.pending.pop(0)
                ex.answered, ex.status = now, status
            length = 0
            for h in headers:
                name, _, value = h.partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
                if name.strip().lower() == "transfer-encoding" and "chunked" in value.lower():
                    self.res_chunked = True
            self.res_body_left = length
            view = memoryview(rest)


class CallbackClock:
    """Een TCP-doorgever op 0.0.0.0:<vrije poort> naar de interne luisteraar van de app. DS
    krijgt hem als PLATFORM_OFFICE_APP_URL. Elke verbinding krijgt twee draden; bytes gaan
    ongewijzigd door."""

    def __init__(self, target_port: int, target_host: str = "127.0.0.1") -> None:
        self.target = (target_host, target_port)
        self.port = free_port()
        self.exchanges: list[Exchange] = []
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.port))
        sock.listen(64)
        sock.settimeout(0.5)
        self._sock = sock
        threading.Thread(target=self._accept, name="callback-clock", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()

    def _record(self, ex: Exchange) -> None:
        with self._lock:
            self.exchanges.append(ex)

    def callbacks(self, statuses: tuple[int, ...] = (2, 6)) -> list[Exchange]:
        with self._lock:
            return [
                e
                for e in self.exchanges
                if e.path.startswith("/internal/office/callback") and e.callback_status in statuses
            ]

    def _accept(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                upstream = socket.create_connection(self.target, timeout=10)
            except OSError:
                client.close()
                continue
            for s in (client, upstream):
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn = _Conn(self)
            threading.Thread(
                target=self._pump, args=(client, upstream, conn.from_ds), daemon=True
            ).start()
            threading.Thread(
                target=self._pump, args=(upstream, client, conn.from_app), daemon=True
            ).start()

    @staticmethod
    def _pump(src: socket.socket, dst: socket.socket, watch: Any) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                watch(data)
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                with contextlib.suppress(OSError):
                    s.shutdown(socket.SHUT_RDWR)
                with contextlib.suppress(OSError):
                    s.close()


# --- de server ---


@dataclass
class Bench:
    """Een server met office aan tegen de echte DS, en wat erbij hoort."""

    office: Office
    clock: CallbackClock | None
    base: Path
    timers: str = "productie"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def server(self) -> Server:
        return self.office.server

    @property
    def pid(self) -> int:
        assert self.server.proc is not None
        return self.server.proc.pid

    def stop(self) -> None:
        self.office.stop()
        if self.clock is not None:
            self.clock.stop()


def start_office(
    base: Path, url: str, secret: str, *, clock: bool = False, stub: bool = False
) -> Bench:
    """Zoals start_real_office in het harnas, maar met de timers van productie en (clock=True)
    de CallbackClock tussen DS en de interne luisteraar. Met stub=True draait ook de DocStub
    van het harnas (op 0.0.0.0, voor DS bereikbaar als host.docker.internal): de bron van de
    documenten als een meting DS rechtstreeks aanspreekt, zonder de app ertussen."""
    wait_real_ds(url)
    host = os.environ.get("E2E_REAL_DS_APP_HOST") or "host.docker.internal"
    internal_port = free_port()
    env = office_env(
        ds_url=url,
        secret=secret,
        internal_port=internal_port,
        app_host=host,
        internal_host="0.0.0.0",
    )
    for name in E2E_TIMERS:
        env.pop(name, None)
    timer = None
    if clock:
        timer = CallbackClock(internal_port)
        timer.start()
        env["PLATFORM_OFFICE_APP_URL"] = f"http://{host}:{timer.port}"
    server = Server(base / "data", env=env)
    docs = DocStub(None, bind="0.0.0.0", host=host)
    try:
        server.start()
        admin = make_admin(server)
        office = Office(
            server=server,
            admin=admin,
            ds_url=url,
            secret=secret,
            internal_port=internal_port,
            stub=docs,
            real=True,
        )
        if stub:
            docs.settings = office.settings()
            docs.start()
        office.wait_state("ok", REAL_READY_SECONDS)
    except BaseException:
        server.stop()
        docs.stop()
        if timer is not None:
            timer.stop()
        raise
    return Bench(office=office, clock=timer, base=base)


# --- gebruikers, bestanden en achtergrondwerk ---


@dataclass
class Team:
    owner: Account
    members: list[Account]
    folder: str
    nodes: dict[str, str]  # naam -> node-id

    @property
    def everyone(self) -> list[Account]:
        return [self.owner, *self.members]


def team(bench: Bench, files: dict[str, bytes], members: int = 0, tag: str = "") -> Team:
    """Een eigenaar met een map "Samen" (schrijfrecht gedeeld met members anderen) en de
    bestanden daarin."""
    server, admin = bench.server, bench.office.admin
    tag = tag or uuid.uuid4().hex[:6]
    owner = create_user(server, admin, name=f"Eigenaar {tag}")
    folder = server.call(
        owner, "POST", f"/api/v1/files/{owner.root_id}/folders", json={"name": "Samen"}, expect=201
    ).json()["id"]
    others = []
    for n in range(members):
        other = create_user(server, admin, name=f"Lid {n + 1} {tag}")
        server.call(
            owner,
            "POST",
            f"/api/v1/files/{folder}/shares",
            json={"user_id": other.id, "permission": "write"},
            expect=201,
        )
        others.append(other)
    nodes = {}
    for name, data in files.items():
        nodes[name] = server.call(
            owner,
            "PUT",
            f"/api/v1/files/{folder}/upload",
            params={"name": name},
            content=data,
            expect=201,
        ).json()["id"]
    return Team(owner, others, folder, nodes)


def _db(server: Server) -> sqlite3.Connection:
    db = server.data_dir / "platform.db"
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)


def background(server: Server) -> dict[str, int]:
    """Wat er nog loopt: PDF-voorbeelden die wachten (O3) en taken in de wachtrij."""
    with _db(server) as conn:
        pdf = conn.execute("SELECT count(*) FROM blob WHERE pdf_state = 'pending'").fetchone()[0]
        jobs = conn.execute(
            "SELECT count(*) FROM job WHERE status IN ('queued', 'running')"
        ).fetchone()[0]
    return {"pdf_pending": pdf, "jobs": jobs}


def wait_background(server: Server, timeout: float = 300) -> dict[str, int]:
    """Tot er geen PDF-voorbeeld meer wacht en geen taak meer loopt."""
    end = time.monotonic() + timeout
    left = background(server)
    while any(left.values()) and time.monotonic() < end:
        time.sleep(1)
        left = background(server)
    if any(left.values()):
        raise AssertionError(f"achtergrondwerk niet klaar binnen {timeout:.0f} s: {left}")
    return left


def node_blob(server: Server, node: str) -> tuple[str | None, int | None]:
    """(blob, grootte) van een bestand, rechtstreeks uit de database (alleen lezen)."""
    with _db(server) as conn:
        row = conn.execute(
            "SELECT blob_id, size FROM node WHERE id = ?", (uuid.UUID(node).hex,)
        ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def log_offset(server: Server) -> int:
    try:
        return server.log_path.stat().st_size
    except OSError:
        return 0


def access_lines(server: Server, start: int, end: int | None = None) -> list[str]:
    """De regels van de toegangslog van uvicorn (INFO: ... "GET /pad HTTP/1.1" 200) tussen
    twee posities in de log van de server: de verzoeken die echt bij de app aankwamen."""
    with open(server.log_path, "rb") as f:
        f.seek(start)
        data = f.read() if end is None else f.read(max(0, end - start))
    out = []
    for line in data.decode("utf-8", "replace").splitlines():
        if line.startswith("INFO:") and '"' in line and ' HTTP/1.1" ' in line:
            out.append(line.split('"', 1)[1])
    return out


def upload(server: Server, account: Account, folder: str, name: str, data: bytes) -> str:
    """Een bestand in een map; geeft het id."""
    return server.call(
        account,
        "PUT",
        f"/api/v1/files/{folder}/upload",
        params={"name": name},
        content=data,
        expect=201,
    ).json()["id"]


def pdf_state(server: Server, node: str) -> str | None:
    """blob.pdf_state van de inhoud van een bestand (alleen lezen)."""
    with _db(server) as conn:
        row = conn.execute(
            "SELECT b.pdf_state FROM node n JOIN blob b ON b.sha256 = n.blob_id WHERE n.id = ?",
            (uuid.UUID(node).hex,),
        ).fetchone()
    return row[0] if row else None


def office_jobs(server: Server, node: str) -> list[dict[str, Any]]:
    """De taken office.pdf van de inhoud van een bestand: status, pogingen en de fout."""
    blob, _size = node_blob(server, node)
    if blob is None:
        return []
    with _db(server) as conn:
        rows = conn.execute(
            "SELECT status, attempts, error FROM job WHERE type = 'office.pdf' AND key = ?",
            (f"office.pdf:{blob}",),
        ).fetchall()
    return [{"status": s, "attempts": a, "error": (e or "")[:200]} for s, a, e in rows]


def mem_available() -> int:
    """MemAvailable van de machine, in bytes."""
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return 0


# --- de container van DS: geheugen, cpu, processen, gezondheid ---


def _cgroup(cid: str) -> dict[str, Any] | None:
    """De mappen van de cgroup van een container (v1 met cgroupfs of systemd, of v2)."""
    root = Path("/sys/fs/cgroup")
    for memory, cpu in (
        (root / "memory/docker" / cid, root / "cpuacct/docker" / cid),
        (
            root / f"memory/system.slice/docker-{cid}.scope",
            root / f"cpuacct/system.slice/docker-{cid}.scope",
        ),
    ):
        if (memory / "memory.usage_in_bytes").is_file():
            return {"v2": False, "memory": memory, "cpu": cpu}
    for path in (root / f"system.slice/docker-{cid}.scope", root / "docker" / cid):
        if (path / "memory.current").is_file():
            return {"v2": True, "memory": path, "cpu": path}
    return None


def container_id(name: str) -> str | None:
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{.Id}}", name],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


class DsWatch:
    """Meet de container van DS zolang hij loopt: elke interval s het geheugen van de cgroup
    (met en zonder paginacache), de cpu (cpuacct), en van elk proces in de container de naam en
    VmRSS (zo zie je x2t, docservice en de converter apart). Daarnaast elke health_every s
    GET /healthcheck. Na stop(): summary(). Een OOM staat in memory.oom_control (oom_kill)."""

    def __init__(
        self, name: str, url: str, *, interval: float = 0.2, health_every: float | None = 0.5
    ) -> None:
        self.name, self.url = name, url.rstrip("/")
        self.interval, self.health_every = interval, health_every
        cid = container_id(name)
        self.cg = _cgroup(cid) if cid else None
        self.samples: list[dict[str, Any]] = []
        self.health: list[tuple[float, bool, float]] = []  # (t, true?, ms)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.started = self.stopped = 0.0
        self.oom0 = self.oom1 = 0
        self.pids0: dict[str, set[int]] = {}
        self.pids1: dict[str, set[int]] = {}
        self.boot0 = 0.0
        self.victims: list[dict[str, Any]] = []

    # metingen van de cgroup
    def _file(self, kind: str, name: str) -> str:
        assert self.cg is not None
        return _read(str(self.cg[kind] / name))

    def _stat(self) -> dict[str, int]:
        out = {}
        for line in self._file("memory", "memory.stat").splitlines():
            k, _, v = line.partition(" ")
            with contextlib.suppress(ValueError):
                out[k] = int(v)
        return out

    def usage(self) -> int:
        if self.cg is None:
            return 0
        name = "memory.current" if self.cg["v2"] else "memory.usage_in_bytes"
        return int(self._file("memory", name) or 0)

    def rss(self) -> int:
        """Anoniem geheugen van de cgroup (zonder paginacache)."""
        if self.cg is None:
            return 0
        st = self._stat()
        return st.get("anon", 0) if self.cg["v2"] else st.get("total_rss", st.get("rss", 0))

    def cpu_seconds(self) -> float:
        if self.cg is None:
            return 0.0
        if self.cg["v2"]:
            for line in self._file("cpu", "cpu.stat").splitlines():
                if line.startswith("usage_usec "):
                    return int(line.split()[1]) / 1e6
            return 0.0
        return int(self._file("cpu", "cpuacct.usage") or 0) / 1e9

    def oom_kills(self) -> int:
        if self.cg is None:
            return 0
        text = self._file("memory", "memory.events" if self.cg["v2"] else "memory.oom_control")
        for line in text.splitlines():
            if line.startswith("oom_kill "):
                return int(line.split()[1])
        return 0

    def reset_peak(self) -> None:
        """De piek van de kernel terug naar nu (alleen v1; v2 telt alleen de steekproeven)."""
        if self.cg is not None and not self.cg["v2"]:
            with contextlib.suppress(OSError):
                (self.cg["memory"] / "memory.max_usage_in_bytes").write_text("0")

    def kernel_peak(self) -> int | None:
        if self.cg is None:
            return None
        name = "memory.peak" if self.cg["v2"] else "memory.max_usage_in_bytes"
        text = self._file("memory", name).strip()
        return int(text) if text.isdigit() else None

    def processes(self) -> list[tuple[int, str, int]]:
        """(pid, naam, VmRSS) van elk proces in de container."""
        if self.cg is None:
            return []
        out = []
        for pid_text in self._file("memory", "cgroup.procs").split():
            pid = int(pid_text)
            comm = _read(f"/proc/{pid}/comm").strip()
            rss = 0
            for line in _read(f"/proc/{pid}/status").splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
            if comm:
                out.append((pid, comm, rss))
        return out

    def _pids(self) -> dict[str, set[int]]:
        found: dict[str, set[int]] = {}
        for pid, comm, _rss in self.processes():
            if comm in ("docservice", "converter"):
                found.setdefault(comm, set()).add(pid)
        return found

    # de lussen
    def _sample(self) -> None:
        while not self._stop.is_set():
            t = time.monotonic()
            procs = self.processes()
            x2t = [rss for _pid, comm, rss in procs if comm == "x2t"]
            by = {comm: rss for _pid, comm, rss in procs if comm in ("docservice", "converter")}
            self.samples.append(
                {
                    "t": t,
                    "usage": self.usage(),
                    "rss": self.rss(),
                    "cpu": self.cpu_seconds(),
                    "x2t": len(x2t),
                    "x2t_rss": max(x2t, default=0),
                    "x2t_pids": [pid for pid, comm, _ in procs if comm == "x2t"],
                    "docservice_rss": by.get("docservice", 0),
                    "converter_rss": by.get("converter", 0),
                }
            )
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - t)))

    def _health(self) -> None:
        import httpx

        assert self.health_every is not None
        with httpx.Client(trust_env=False, timeout=5) as client:
            while not self._stop.is_set():
                t = time.monotonic()
                ok = False
                with contextlib.suppress(httpx.HTTPError):
                    r = client.get(self.url + "/healthcheck")
                    ok = r.status_code == 200 and r.text.strip() == "true"
                self.health.append((t, ok, (time.monotonic() - t) * 1000))
                self._stop.wait(max(0.0, self.health_every - (time.monotonic() - t)))

    def start(self) -> DsWatch:
        self.reset_peak()
        self.boot0 = office_watchdog.uptime()
        self.oom0 = self.oom_kills()
        self.pids0 = self._pids()
        self.started = time.monotonic()
        loops = [self._sample] + ([self._health] if self.health_every else [])
        for loop in loops:
            th = threading.Thread(target=loop, name=f"ds-watch-{loop.__name__}", daemon=True)
            th.start()
            self._threads.append(th)
        return self

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        for th in self._threads:
            th.join(timeout=10)
        self.stopped = time.monotonic()
        self.oom1 = self.oom_kills()
        self.pids1 = self._pids()
        self.victims = office_watchdog.oom_kills(self.boot0)
        return self.summary()

    def between(self, t0: float, t1: float) -> dict[str, Any]:
        """Een samenvatting van de steekproeven tussen twee momenten (time.monotonic)."""
        rows = [s for s in self.samples if t0 <= s["t"] <= t1]
        if len(rows) < 2:
            return {"samples": len(rows)}
        mb = 1024**2
        span = rows[-1]["t"] - rows[0]["t"]
        # cpu per seconde, over vensters van ±1 s
        cores, i = [], 0
        for j in range(1, len(rows)):
            if rows[j]["t"] - rows[i]["t"] >= 1.0:
                cores.append((rows[j]["cpu"] - rows[i]["cpu"]) / (rows[j]["t"] - rows[i]["t"]))
                i = j
        return {
            "samples": len(rows),
            "seconds": round(span, 1),
            "cpu_s": round(rows[-1]["cpu"] - rows[0]["cpu"], 2),
            "cores_avg": round((rows[-1]["cpu"] - rows[0]["cpu"]) / span, 2) if span else None,
            "cores_max_1s": round(max(cores), 2) if cores else None,
            "usage_mb_max": round(max(s["usage"] for s in rows) / mb),
            "rss_mb_start": round(rows[0]["rss"] / mb),
            "rss_mb_max": round(max(s["rss"] for s in rows) / mb),
            "rss_mb_end": round(rows[-1]["rss"] / mb),
            "x2t_runs": len({p for s in rows for p in s["x2t_pids"]}),
            "x2t_mb_max": round(max(s["x2t_rss"] for s in rows) / mb),
            "docservice_mb_max": round(max(s["docservice_rss"] for s in rows) / mb),
            "converter_mb_max": round(max(s["converter_rss"] for s in rows) / mb),
        }

    def summary(self) -> dict[str, Any]:
        peak = self.kernel_peak()
        bad = [h for h in self.health if not h[1]]
        out = {
            **self.between(self.started, self.stopped or time.monotonic()),
            "kernel_peak_mb": None if peak is None else round(peak / 1024**2),
            "limit_cgroup": self.cg is not None,
            "oom_kills": self.oom1 - self.oom0,
            # Wie de OOM-killer doodde (kernellog), bijv. x2t; leeg als dmesg niet te lezen is
            "oom_victims": [
                {k: v[k] for k in ("comm", "pid", "anon_rss_mib", "cgroup")} for v in self.victims
            ],
            "docservice_restarted": bool(self.pids0.get("docservice"))
            and self.pids0.get("docservice") != self.pids1.get("docservice"),
            "converter_restarted": bool(self.pids0.get("converter"))
            and self.pids0.get("converter") != self.pids1.get("converter"),
        }
        if self.health_every:
            out["health"] = {
                "n": len(self.health),
                "not_true": len(bad),
                "ms_median": round(statistics.median(h[2] for h in self.health), 1)
                if self.health
                else None,
                "ms_max": round(max((h[2] for h in self.health), default=0), 1),
                "first_bad_s": round(bad[0][0] - self.started, 1) if bad else None,
            }
        return out


# --- DS stoppen, killen en weer starten (alleen de eigen container van de meting) ---


def docker(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def ds_running(name: str) -> bool:
    r = docker("inspect", "--format", "{{.State.Running}}", name, timeout=30)
    return r.stdout.strip() == "true"


def converter_starts(name: str) -> int:
    """Hoe vaak supervisord in de container de converter (opnieuw) startte: de font-herstart
    na een start is de tweede keer."""
    r = docker(
        "exec",
        name,
        "grep",
        "-c",
        "success: converter entered RUNNING",
        "/var/log/supervisor/supervisord.log",
        timeout=30,
    )
    return int(r.stdout.strip() or 0) if r.returncode in (0, 1) else -1


def ds_stop(name: str, grace: int = 60) -> dict[str, Any]:
    """docker stop -t grace (zoals stop_grace_period in compose.office.yaml); de duur en of
    hij netjes stopte (exitcode van de container, en of docker moest killen)."""
    t0 = time.monotonic()
    r = docker("stop", "-t", str(grace), name, timeout=grace + 60)
    took = time.monotonic() - t0
    state = docker("inspect", "--format", "{{.State.ExitCode}} {{.State.OOMKilled}}", name)
    code, _, oom = state.stdout.strip().partition(" ")
    return {
        "seconds": round(took, 2),
        "ok": r.returncode == 0,
        "exit_code": int(code) if code.lstrip("-").isdigit() else None,
        # 137 = SIGKILL: docker moest na grace seconden killen
        "killed_after_grace": code == "137",
        "oom_killed": oom == "true",
    }


def ds_kill(name: str) -> dict[str, Any]:
    """docker kill (SIGKILL), zoals een stroomstoring of de OOM-killer voor de hele container."""
    t0 = time.monotonic()
    r = docker("kill", name, timeout=60)
    return {"seconds": round(time.monotonic() - t0, 2), "ok": r.returncode == 0}


def ds_start(name: str, url: str, timeout: float = 480) -> dict[str, Any]:
    """docker start, dan wachten tot DS gezond is en zijn font-herstart achter de rug heeft
    (elke start doet die, ook een herstart van dezelfde container: spike, extra bevinding 1)."""
    import httpx

    before = converter_starts(name)
    t0 = time.monotonic()
    r = docker("start", name, timeout=120)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout).strip()[:300]}
    healthy_at = fonts_at = None
    end = t0 + timeout
    with httpx.Client(trust_env=False, timeout=5) as client:
        while time.monotonic() < end:
            ok = False
            with contextlib.suppress(httpx.HTTPError):
                h = client.get(url.rstrip("/") + "/healthcheck")
                ok = h.status_code == 200 and h.text.strip() == "true"
            if ok and healthy_at is None:
                healthy_at = time.monotonic() - t0
            if fonts_at is None and converter_starts(name) >= before + 2:
                fonts_at = time.monotonic() - t0
            if ok and fonts_at is not None:
                break
            time.sleep(2)
    return {
        "ok": healthy_at is not None and fonts_at is not None,
        "healthy_s": None if healthy_at is None else round(healthy_at, 1),
        "fonts_done_s": None if fonts_at is None else round(fonts_at, 1),
    }
