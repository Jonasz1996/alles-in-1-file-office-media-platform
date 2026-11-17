"""Gedeeld door de stappen van de spike (bouwontwerp fase 5, §3.7).

Alleen de standaardbibliotheek: de scripts draaien met `python3 -I` in het image, waar geen
pytest of ander pakket bij hoort. Hier staan:

- `Stap`: de uitkomst van één stap, met per controle wat we verwachtten en wat we maten, en
  aan het eind "verwacht", "gemeten", "besluit" en de terugval van §3.7;
- `start` en `wacht`: een kindproces (ffmpeg) met de bron als fd 3 en de grenzen van Keuze 12
  vóór `exec`, en de CPU-tijd en het geheugen uit `wait4`;
- `omgeving`: waar de stap draaide (versies, CPU's, de cpu-grens van de cgroup).
"""

from __future__ import annotations

import os
import platform
import re
import resource
import shutil
import subprocess
import sys
import threading
import time

# fd 3 blijft voor de bron van ffmpeg (`-fd 3 -i fd:`). Houden we hem zelf bezet, dan krijgt
# geen pipe van subprocess dat nummer, en kan `voor_exec` hem in het kind veilig vervangen.
try:
    os.fstat(3)
except OSError:
    _leeg = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
    if _leeg != 3:
        os.dup2(_leeg, 3, inheritable=False)
        os.close(_leeg)

MB = 1024 * 1024
VOORTGANG = re.compile(r"^[a-z][a-z0-9_]*=")  # de regels van -progress


def log(tekst: str) -> None:
    """Naar stderr: stdout is voor de JSON van de uitkomst."""
    print(tekst, file=sys.stderr, flush=True)


class Stap:
    """De uitkomst van één stap van §3.7."""

    def __init__(self, code: str, titel: str, beslist: str) -> None:
        self.code = code
        self.titel = titel
        self.beslist = beslist
        self.checks: list[dict] = []
        self.gegevens: dict = {}
        self.verwacht = ""
        self.gemeten = ""
        self.besluit = ""
        self.terugval: str | None = None
        self.t0 = time.monotonic()
        log(f"== {code}: {titel}")

    def check(self, naam: str, ok: bool, verwacht, gemeten, **extra) -> bool:
        self.checks.append(
            {"naam": naam, "ok": bool(ok), "verwacht": verwacht, "gemeten": gemeten, **extra}
        )
        log(f"  [{'goed' if ok else 'ANDERS'}] {naam}: {gemeten}")
        return bool(ok)

    def ok(self, *namen: str) -> bool:
        """Of alle controles (of alleen de genoemde) slaagden."""
        gekozen = [c for c in self.checks if not namen or c["naam"] in namen]
        return all(c["ok"] for c in gekozen)

    def anders(self) -> list[str]:
        return [c["naam"] for c in self.checks if not c["ok"]]

    def uit(self) -> dict:
        return {
            "stap": self.code,
            "titel": self.titel,
            "beslist": self.beslist,
            "uitkomst": "zoals verwacht" if self.ok() else "anders dan verwacht",
            "verwacht": self.verwacht,
            "gemeten": self.gemeten,
            "besluit": self.besluit,
            "terugval": self.terugval,
            "anders": self.anders(),
            "checks": self.checks,
            "gegevens": self.gegevens,
            "duur_s": round(time.monotonic() - self.t0, 2),
        }


def getal(x: float) -> str:
    """Een tijd voor de argv: hooguit 6 decimalen, zonder nullen aan het eind."""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


# --- processen ---------------------------------------------------------------------------


def voor_exec(invoer_fd: int | None, grenzen: dict[str, int] | None):
    """Wat het kind vóór `exec` doet: de bron op fd 3 en de grenzen van Keuze 12."""

    def doe() -> None:
        if invoer_fd is not None:
            if invoer_fd == 3:
                os.set_inheritable(3, True)
            else:
                os.dup2(invoer_fd, 3)
        for naam, waarde in (grenzen or {}).items():
            soort = getattr(resource, naam)
            resource.setrlimit(soort, (waarde, waarde))

    return doe


class Staart:
    """Leest stderr in een draad (anders loopt de pipe vol) en houdt het eind vast."""

    def __init__(self, stroom, grootte: int = 8192) -> None:
        self.stroom = stroom
        self.grootte = grootte
        self.data = bytearray()
        self.voortgang: dict[str, str] = {}
        self.draad = threading.Thread(target=self._lees, daemon=True)
        self.draad.start()

    def _lees(self) -> None:
        rest = b""
        while True:
            stuk = self.stroom.read1(65536) if hasattr(self.stroom, "read1") else b""
            if not stuk:
                break
            self.data += stuk
            if len(self.data) > 4 * self.grootte:
                del self.data[: -self.grootte]
            rest += stuk
            *regels, rest = rest.split(b"\n")
            for regel in regels:
                tekst = regel.decode("latin-1").strip()
                if VOORTGANG.match(tekst):
                    k, _, v = tekst.partition("=")
                    self.voortgang[k] = v.strip()

    def tekst(self) -> str:
        self.draad.join(timeout=5)
        if not self.draad.is_alive():
            self.stroom.close()
        regels = [
            r
            for r in bytes(self.data[-self.grootte :]).decode("utf-8", "replace").splitlines()
            if not VOORTGANG.match(r.strip())
        ]
        return "\n".join(regels)[-2000:]


def start(
    argv: list[str],
    *,
    bron: str | int | None = None,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    grenzen: dict[str, int] | None = None,
) -> tuple[subprocess.Popen, Staart]:
    """Start een kind. `bron` gaat als fd 3 mee (alleen lezen), zoals de worker het doet: een
    pad (dan opent dit hem zelf) of een fd die er al is (de worker, S3)."""
    invoer_fd = None
    eigen = isinstance(bron, str)
    if eigen:
        invoer_fd = os.open(bron, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    elif isinstance(bron, int):
        invoer_fd = bron
    try:
        p = subprocess.Popen(
            argv,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            close_fds=False,
            preexec_fn=voor_exec(invoer_fd, grenzen),
            start_new_session=True,
        )
    finally:
        if eigen:
            os.close(invoer_fd)
    return p, Staart(p.stderr)


def wacht(p: subprocess.Popen, deadline_s: float = 300.0) -> dict:
    """Wacht op het kind met `wait4`: exitcode, CPU-tijd (user + sys) en hoogste RSS."""
    wachter = threading.Timer(deadline_s, _stop, args=(p,))
    wachter.daemon = True
    wachter.start()
    try:
        _pid, status, ru = os.wait4(p.pid, 0)
    finally:
        wachter.cancel()
    p.returncode = os.waitstatus_to_exitcode(status)
    return {
        "rc": p.returncode,
        "signaal": -p.returncode if p.returncode < 0 else None,
        "cpu_s": round(ru.ru_utime + ru.ru_stime, 3),
        "max_rss_mb": round(ru.ru_maxrss / 1024, 1),
    }


def _stop(p: subprocess.Popen) -> None:
    """Een hangend kind stoppen, alleen op zijn eigen pid (en procesgroep)."""
    log(f"  deadline: pid {p.pid} gestopt")
    try:
        os.killpg(p.pid, 9)
    except OSError:
        pass


def draai(argv: list[str], *, deadline_s: float = 120.0, **kw) -> dict:
    """Een kind dat niets op stdout geeft dat we nodig hebben: draaien tot het klaar is."""
    t0 = time.monotonic()
    kw.setdefault("stdout", subprocess.DEVNULL)
    p, staart = start(argv, **kw)
    uit = wacht(p, deadline_s)
    uit["wand_s"] = round(time.monotonic() - t0, 3)
    uit["stderr"] = staart.tekst()
    return uit


def uitvoer(argv: list[str], deadline_s: float = 60.0) -> tuple[int, str]:
    """Een kort commando met zijn uitvoer (versies, lijsten)."""
    try:
        r = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=deadline_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fout:
        return -1, f"{type(fout).__name__}: {fout}"
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def cpu_van(pid: int) -> float | None:
    """CPU-tijd (user + sys) van een lopend proces, uit /proc/<pid>/stat."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            velden = f.read().rsplit(")", 1)[1].split()
    except OSError:
        return None
    return (int(velden[11]) + int(velden[12])) / os.sysconf("SC_CLK_TCK")


# --- de omgeving -------------------------------------------------------------------------


def _lees(pad: str) -> str | None:
    try:
        with open(pad) as f:
            return f.read().strip()
    except OSError:
        return None


def cpu_grens() -> dict:
    """De cpu-grens van de cgroup: v2 (`cpu.max`) of v1 (`cfs_quota_us`/`cfs_period_us`)."""
    v2 = _lees("/sys/fs/cgroup/cpu.max")
    if v2:
        quota, _, periode = v2.partition(" ")
        cores = None if quota == "max" else round(int(quota) / int(periode), 2)
        return {"cgroup": "v2", "cpu_max": v2, "cores": cores}
    quota = _lees("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    periode = _lees("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota and periode:
        cores = None if int(quota) < 0 else round(int(quota) / int(periode), 2)
        return {"cgroup": "v1", "cfs_quota_us": quota, "cfs_period_us": periode, "cores": cores}
    return {"cgroup": None, "cores": None}


def geheugen_grens() -> str | None:
    return _lees("/sys/fs/cgroup/memory.max") or _lees(
        "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    )


def ffmpeg_versie() -> str | None:
    rc, tekst = uitvoer(["ffmpeg", "-hide_banner", "-version"])
    if rc != 0:
        return None
    eerste = tekst.splitlines()[0] if tekst else ""
    delen = eerste.split()
    return delen[2] if len(delen) > 2 and delen[:2] == ["ffmpeg", "version"] else eerste


def omgeving() -> dict:
    model = None
    cpuinfo = _lees("/proc/cpuinfo") or ""
    for regel in cpuinfo.splitlines():
        if regel.startswith("model name"):
            model = regel.split(":", 1)[1].strip()
            break
    try:
        affiniteit = len(os.sched_getaffinity(0))
    except OSError:
        affiniteit = None
    return {
        "python": sys.version.split()[0],
        "kernel": platform.release(),
        "uid": os.getuid(),
        "gid": os.getgid(),
        "cpu_model": model,
        "cpus_zichtbaar": os.cpu_count(),
        "cpus_affiniteit": affiniteit,
        "cpu_grens": cpu_grens(),
        "geheugen_grens": geheugen_grens(),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffmpeg_versie": ffmpeg_versie(),
        "image": os.environ.get("SPIKE_IMAGE"),
        "commit": os.environ.get("SPIKE_COMMIT"),
    }
