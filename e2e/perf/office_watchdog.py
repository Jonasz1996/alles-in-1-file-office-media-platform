"""Een waakhond voor de zware metingen van office (fase 4, §4.7 deel 2).

Waarom: de sandbox heeft 15,7 GiB, geen swap, en de bash-processen zitten in een cgroup met een
eigen grens. Twintig echte editors in Chromium, grote conversies en x2t op zijn grens kunnen dat
opmaken; in een eerdere poging startte de hele sandbox opnieuw. De waakhond zorgt dat een meting
stopt voordat het geheugen op is, en dat er daarna te zien is wat er gebeurde.

Wat hij doet:
- start de opdracht na `--` als kind (of bewaakt met --pid een proces dat al loopt);
- elke --interval s (standaard 1): MemAvailable van de machine, het geheugen van de eigen
  cgroup (anoniem en tmpfs, zonder paginacache) tegen de grens van die cgroup, het geheugen van
  elke docker-container (cgroup), en de vijf grootste processen (VmRSS). Elke regel gaat meteen
  naar --log, met fsync: na een herstart staat de laatste seconde erin;
- zakt MemAvailable onder --min-free-gib (standaard 2), of komt de eigen cgroup binnen
  --min-free-gib van zijn grens, dan stuurt hij SIGKILL naar de hele boom van de opdracht, op
  PID (nooit op naam): eerst de lijst van alle nakomelingen, dan iedereen in die lijst. Hij
  schrijft wat er gebeurde (regel TRIP) en eindigt met exitcode 99;
- elke 5 s de kernellog (dmesg): elk proces dat de OOM-killer sinds de start doodde (ook in de
  cgroup van een container, zoals x2t in DS), met naam en geheugen, komt als regel OOM in de log;
- aan het eind een samenvatting (--summary, JSON): de piek van het geheugen (kleinste
  MemAvailable, grootste cgroup, grootste container), de OOM-kills, of hij ingreep, en de
  exitcode.

Geen afhankelijkheden: alleen de standaardbibliotheek. Geen geheimen in de log: alleen namen van
processen (comm), geen opdrachtregels.

    python e2e/perf/office_watchdog.py --log wd.log --summary wd.json -- \\
      uv run --with playwright python ../e2e/perf/office_editors.py --editors 2
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

GIB = 1024**3
MIB = 1024**2
TRIPPED = 99


def _read(path: str | Path) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def meminfo() -> dict[str, int]:
    out = {}
    for line in _read("/proc/meminfo").splitlines():
        name, _, rest = line.partition(":")
        with contextlib.suppress(ValueError, IndexError):
            out[name] = int(rest.split()[0]) * 1024
    return out


def own_cgroup() -> Path | None:
    """De map van de memory-cgroup van dit proces (v1), of None."""
    for line in _read("/proc/self/cgroup").splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1] == "memory":
            path = Path("/sys/fs/cgroup/memory") / parts[2].lstrip("/")
            return path if (path / "memory.limit_in_bytes").is_file() else None
    for line in _read("/proc/self/cgroup").splitlines():
        if line.startswith("0::"):
            path = Path("/sys/fs/cgroup") / line[3:].lstrip("/")
            return path if (path / "memory.max").is_file() else None
    return None


def cgroup_numbers(path: Path) -> dict[str, int | None]:
    """Grens en gebruik van een cgroup: anoniem plus tmpfs (wat zonder swap niet terug kan)."""
    stat: dict[str, int] = {}
    for line in _read(path / "memory.stat").splitlines():
        name, _, value = line.partition(" ")
        with contextlib.suppress(ValueError):
            stat[name] = int(value)
    if (path / "memory.limit_in_bytes").is_file():
        limit = int(_read(path / "memory.limit_in_bytes") or 0)
        usage = int(_read(path / "memory.usage_in_bytes") or 0)
        hard = stat.get("total_rss", stat.get("rss", 0)) + stat.get(
            "total_shmem", stat.get("shmem", 0)
        )
        oom = 0
        for line in _read(path / "memory.oom_control").splitlines():
            if line.startswith("oom_kill "):
                oom = int(line.split()[1])
    else:
        text = _read(path / "memory.max").strip()
        limit = int(text) if text.isdigit() else 0
        usage = int(_read(path / "memory.current") or 0)
        hard = stat.get("anon", 0) + stat.get("shmem", 0)
        oom = 0
        for line in _read(path / "memory.events").splitlines():
            if line.startswith("oom_kill "):
                oom = int(line.split()[1])
    return {
        "limit": limit if 0 < limit < 2**62 else None,
        "usage": usage,
        "hard": hard,
        "oom_kill": oom,
    }


def containers() -> dict[str, int]:
    """Het geheugen (anoniem, zonder cache) van elke docker-container, op de eerste 12 tekens
    van zijn id."""
    out = {}
    for root in (Path("/sys/fs/cgroup/memory/docker"), Path("/sys/fs/cgroup/system.slice")):
        if not root.is_dir():
            continue
        for d in root.iterdir():
            name = d.name.removeprefix("docker-").removesuffix(".scope")
            if not d.is_dir() or len(name) < 12 or not all(c in "0123456789abcdef" for c in name):
                continue
            out[name[:12]] = cgroup_numbers(d)["hard"] or 0
    return out


def top_processes(n: int = 5) -> list[tuple[int, str, int]]:
    rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        rss = 0
        for line in _read(entry / "status").splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
                break
        if rss:
            rows.append((rss, int(entry.name), _read(entry / "comm").strip()))
    rows.sort(reverse=True)
    return [(pid, comm, rss) for rss, pid, comm in rows[:n]]


def descendants(pid: int) -> list[int]:
    found: list[int] = []
    stack = [pid]
    while stack:
        current = stack.pop()
        for task in Path(f"/proc/{current}/task").glob("*/children"):
            for child in _read(task).split():
                if int(child) not in found:
                    found.append(int(child))
                    stack.append(int(child))
    return found


def uptime() -> float:
    try:
        return float(_read("/proc/uptime").split()[0])
    except (IndexError, ValueError):
        return 0.0


OOM_LINE = re.compile(
    r"^\[\s*(?P<t>[0-9.]+)\]\s+(?P<scope>Memory cgroup out of memory|Out of memory)[^:]*:"
    r" Killed process (?P<pid>\d+) \((?P<comm>[^)]*)\).*?anon-rss:(?P<rss>\d+)kB"
)


def oom_kills(since: float) -> list[dict[str, Any]]:
    """De OOM-kills uit de kernellog na since (seconden sinds de start van de machine). Leeg
    als dmesg niet te lezen is."""
    try:
        text = subprocess.run(
            ["dmesg"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in text.splitlines():
        m = OOM_LINE.match(line)
        if m and float(m["t"]) >= since:
            out.append(
                {
                    "uptime_s": float(m["t"]),
                    "cgroup": m["scope"].startswith("Memory cgroup"),
                    "pid": int(m["pid"]),
                    "comm": m["comm"],
                    "anon_rss_mib": round(int(m["rss"]) / 1024),
                }
            )
    return out


def kill_tree(pid: int) -> list[int]:
    """SIGKILL voor pid en al zijn nakomelingen (eerst de hele lijst, dan iedereen)."""
    tree = [pid, *descendants(pid)]
    for p in tree:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(p, signal.SIGKILL)
    return tree


class Log:
    def __init__(self, path: Path | None) -> None:
        self.f = open(path, "a", buffering=1) if path else None  # noqa: SIM115

    def write(self, line: str) -> None:
        stamp = time.strftime("%H:%M:%S", time.gmtime())
        text = f"{stamp} {line}"
        if self.f is None:
            print(text, file=sys.stderr, flush=True)
            return
        self.f.write(text + "\n")
        self.f.flush()
        with contextlib.suppress(OSError):
            os.fsync(self.f.fileno())

    def close(self) -> None:
        if self.f is not None:
            self.f.close()


def watch(
    pid: int,
    child: subprocess.Popen | None,
    *,
    log: Log,
    min_free: int,
    interval: float,
) -> dict[str, Any]:
    cg = own_cgroup()
    peak: dict[str, Any] = {
        "mem_available_min_mib": None,
        "cgroup_hard_max_mib": None,
        "containers_max_mib": {},
        "top_at_min": [],
    }
    tripped: dict[str, Any] | None = None
    started = time.monotonic()
    boot0 = uptime()
    oom0 = cgroup_numbers(cg)["oom_kill"] if cg else 0
    kills: list[dict[str, Any]] = []
    next_dmesg = 0.0
    while True:
        if time.monotonic() >= next_dmesg:
            next_dmesg = time.monotonic() + 5
            for k in oom_kills(boot0)[len(kills) :]:
                kills.append(k)
                where = "cgroup" if k["cgroup"] else "machine"
                log.write(f"OOM ({where}): {k['comm']} pid {k['pid']}, {k['anon_rss_mib']} MiB")
        if child is not None and child.poll() is not None:
            break
        if child is None and not Path(f"/proc/{pid}").exists():
            break
        info = meminfo()
        avail = info.get("MemAvailable", 0)
        own = cgroup_numbers(cg) if cg else {"limit": None, "hard": 0}
        cont = containers()
        top = top_processes()
        if peak["mem_available_min_mib"] is None or avail / MIB < peak["mem_available_min_mib"]:
            peak["mem_available_min_mib"] = round(avail / MIB)
            peak["top_at_min"] = [[p, c, round(r / MIB)] for p, c, r in top]
        hard = own.get("hard") or 0
        if peak["cgroup_hard_max_mib"] is None or hard / MIB > peak["cgroup_hard_max_mib"]:
            peak["cgroup_hard_max_mib"] = round(hard / MIB)
        for cid, mem in cont.items():
            if mem / MIB > peak["containers_max_mib"].get(cid, 0):
                peak["containers_max_mib"][cid] = round(mem / MIB)
        log.write(
            f"vrij {avail / MIB:.0f} MiB, cgroup {hard / MIB:.0f}"
            f"/{(own.get('limit') or 0) / MIB:.0f} MiB, containers "
            + ",".join(f"{c[:6]}={m / MIB:.0f}" for c, m in cont.items())
            + " | "
            + " ".join(f"{c}:{p}={r / MIB:.0f}" for p, c, r in top)
        )
        limit = own.get("limit")
        why = None
        if avail < min_free:
            why = f"MemAvailable {avail / MIB:.0f} MiB < {min_free / MIB:.0f} MiB"
        elif limit and limit - hard < min_free:
            why = (
                f"cgroup {hard / MIB:.0f} MiB, binnen {min_free / MIB:.0f} MiB van de grens"
                f" {limit / MIB:.0f} MiB"
            )
        if why:
            tree = kill_tree(pid)
            tripped = {
                "after_s": round(time.monotonic() - started, 1),
                "why": why,
                "killed": len(tree),
                "top": [[p, c, round(r / MIB)] for p, c, r in top],
            }
            log.write(f"TRIP {why}; SIGKILL voor {len(tree)} processen (pid {pid} en nakomelingen)")
            break
        time.sleep(interval)
    code = None
    if child is not None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            code = child.wait(timeout=30)
    for k in oom_kills(boot0)[len(kills) :]:
        kills.append(k)
        where = "cgroup" if k["cgroup"] else "machine"
        log.write(f"OOM ({where}): {k['comm']} pid {k['pid']}, {k['anon_rss_mib']} MiB")
    return {
        "seconds": round(time.monotonic() - started, 1),
        "min_free_mib": round(min_free / MIB),
        "tripped": tripped,
        "exit_code": code,
        "cgroup_limit_mib": round((cgroup_numbers(cg)["limit"] or 0) / MIB) if cg else None,
        "cgroup_oom_kills": (cgroup_numbers(cg)["oom_kill"] - oom0) if cg else None,
        "oom_kills": kills,
        **peak,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    command: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, command = argv[:at], argv[at + 1 :]
    parser = argparse.ArgumentParser(description="Waakhond voor het geheugen (§4.7 deel 2)")
    parser.add_argument("--log", type=Path, help="elke seconde een regel (fsync)")
    parser.add_argument("--summary", type=Path, help="samenvatting als JSON")
    parser.add_argument("--min-free-gib", type=float, default=2.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--pid", type=int, help="een proces dat al loopt bewaken")
    args = parser.parse_args(argv)
    if bool(command) == bool(args.pid):
        parser.error("geef een opdracht na -- of --pid, niet allebei")
    log = Log(args.log)
    child = None
    if command:
        child = subprocess.Popen(command)
        pid = child.pid
    else:
        pid = args.pid
    log.write(f"start: pid {pid}, ingrijpen onder {args.min_free_gib} GiB vrij")
    try:
        summary = watch(
            pid, child, log=log, min_free=int(args.min_free_gib * GIB), interval=args.interval
        )
    except KeyboardInterrupt:
        kill_tree(pid)
        raise
    log.write(
        f"klaar: exitcode {summary['exit_code']}, kleinste vrij"
        f" {summary['mem_available_min_mib']} MiB, ingegrepen: {bool(summary['tripped'])}"
    )
    log.close()
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    if summary["tripped"]:
        return TRIPPED
    return summary["exit_code"] or 0


if __name__ == "__main__":
    sys.exit(main())
