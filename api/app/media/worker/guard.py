"""De bewaking van de mediaworker (bouwontwerp fase 5, Keuze 12). Van M1.

In de worker (Watch, gestart door __main__.serve):
- elke INTERVAL s VmRSS van elk kind (ffmpeg, ffprobe) uit /proc/<pid>/status, in de cache van
  caps (`children`, `rss_kb`), voor /beheer. Dat werkt ook voor een niet-dumpbaar kind: status
  blijft leesbaar voor dezelfde uid (V2);
- RLIMIT_DATA (run.limits_for) is de harde grens per kind: een malloc boven de grens faalt, en
  het kind stopt met een fout, niet de worker. Wat RLIMIT_DATA niet telt (gedeeld geheugen,
  MAP_SHARED), vangt de meting: een kind met meer RssAnon + RssShmem dan zijn eigen
  RLIMIT_DATA gaat met SIGKILL op zijn procesgroep weg. why(pid) zegt dan waarom, voor de fout
  van de opdracht;
- de rest van Keuze 12 zit in run.py en server.py (M0): nice en ionice als voorvoegsel, de
  andere grenzen, de procesgroep, de kale omgeving, lseek(0) en dup2 in het kind, de plaatsen
  per soort en het totaal (een stoppend kind telt mee tot waitpid), en PR_SET_DUMPABLE 0 in
  __main__.

Voor de app (M4, producer.py): één producent per sessie (OneProducer). De nieuwe start pas na
waitpid van de oude (finish wacht tot de worker de verbinding sluit; dat doet hij pas als elk
kind van de opdracht gewacht is), en zoekverzoeken binnen COALESCE_S worden samengevoegd: alleen
het laatste start een producent. Zo staan er per sessie hooguit 2 ffmpeg tegelijk (één die stopt
en één die start), en in de praktijk één.

Dit bestand importeert niets van de app (de worker draait zonder de rest).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("platform.media.worker")

INTERVAL = 2.0
# De programma's van de worker; buiten app/media/worker/ staat hun naam nergens
# (test_media_no_ffmpeg_outside.py)
BINARIES = ("ffmpeg", "ffprobe")
COALESCE_S = 0.3
FINISH_LIMIT_S = 10.0  # SIGTERM, 2 s, SIGKILL, waitpid: ruim binnen deze tijd
_KILLED_MAX = 64


# --- /proc lezen ---------------------------------------------------------------------------


def _status(pid: int) -> dict[str, int]:
    """De velden in kB uit /proc/<pid>/status (VmRSS, RssAnon, RssShmem …), of {}."""
    out: dict[str, int] = {}
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if len(parts) == 2 and parts[1] == "kB" and parts[0].isdigit():
                    out[name] = int(parts[0])
    except (OSError, ValueError):
        return {}
    return out


def rss_kb(pid: int) -> int | None:
    """VmRSS uit /proc/<pid>/status, of None."""
    return _status(pid).get("VmRSS")


def data_limit_kb(pid: int) -> int | None:
    """De zachte RLIMIT_DATA van pid in kB uit /proc/<pid>/limits, of None (onbegrensd of niet
    te lezen)."""
    try:
        with open(f"/proc/{pid}/limits") as f:
            for line in f:
                if line.startswith("Max data size"):
                    soft = line[len("Max data size") :].split()[0]
                    return int(soft) // 1024 if soft.isdigit() else None
    except (OSError, ValueError, IndexError):
        return None
    return None


def _stat(pid: int) -> tuple[str, int] | None:
    """(comm, ppid) uit /proc/<pid>/stat."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            text = f.read()
    except OSError:
        return None
    head, sep, rest = text.rpartition(")")
    if not sep:
        return None
    fields = rest.split()
    try:
        return head.partition("(")[2], int(fields[1])
    except (IndexError, ValueError):
        return None


def children(parent: int) -> list[tuple[int, str]]:
    """De directe kinderen van parent: (pid, comm). nice en ionice doen exec, dus dat zijn
    ffmpeg en ffprobe zelf."""
    found: list[tuple[int, str]] = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        info = _stat(int(name))
        if info is not None and info[1] == parent:
            found.append((int(name), info[0]))
    return sorted(found)


# --- de meting in de worker ----------------------------------------------------------------


class Watch:
    """Elke interval s: het geheugen van elk kind van de worker, in caps; te veel is SIGKILL."""

    def __init__(self, caps: dict[str, Any], *, pid: int | None = None, interval: float = INTERVAL):
        self.caps = caps
        self.pid = os.getpid() if pid is None else pid
        self.interval = interval
        self.killed: dict[int, str] = {}
        caps.setdefault("children", [])
        caps.setdefault("rss_kb", 0)

    def scan(self) -> list[dict[str, Any]]:
        seen = []
        for pid, comm in children(self.pid):
            status = _status(pid)
            if not status:
                continue  # net weg
            rss = status.get("VmRSS", 0)
            seen.append({"pid": pid, "name": comm[:16], "rss_kb": rss})
            limit = data_limit_kb(pid)
            own = status.get("RssAnon", 0) + status.get("RssShmem", 0)
            if limit is not None and own > limit and pid not in self.killed:
                self._kill(pid, own, limit)
        self.caps["children"] = seen
        self.caps["rss_kb"] = sum(c["rss_kb"] for c in seen)
        return seen

    def _kill(self, pid: int, own_kb: int, limit_kb: int) -> None:
        reason = f"boven de geheugengrens ({own_kb // 1024} MB, grens {limit_kb // 1024} MB)"
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)  # een kind is leider van zijn eigen groep
        log.warning("kind %s gestopt: %s", pid, reason)
        if len(self.killed) >= _KILLED_MAX:
            self.killed.pop(next(iter(self.killed)))
        self.killed[pid] = reason

    async def run(self) -> None:
        while True:
            try:
                self.scan()
            except Exception:  # de meting mag nooit stoppen
                log.exception("de meting van het geheugen")
            await asyncio.sleep(self.interval)


_watch: Watch | None = None


def start(caps: dict[str, Any], *, interval: float = INTERVAL) -> asyncio.Task:
    """De meting als taak in de event loop van de worker (__main__.serve)."""
    global _watch
    _watch = Watch(caps, interval=interval)
    return asyncio.create_task(_watch.run(), name="media-guard")


def why(pid: int) -> str | None:
    """Waarom de meting dit kind stopte, of None."""
    return _watch.killed.get(pid) if _watch is not None else None


# --- voor de app: één producent per sessie ---------------------------------------------------


async def finish(job: Any, *, limit_s: float = FINISH_LIMIT_S) -> bool:
    """Stopt de opdracht van een client.Job en wacht tot de worker de verbinding sluit. Dat doet
    de worker pas na waitpid van elk kind van de opdracht (server._serve: eerst Child.stop, dan
    de plaats terug, dan dicht). True als dat binnen limit_s gebeurde; de socket is daarna dicht.

    Alleen de schrijfkant gaat dicht (SHUT_WR): de worker ziet EOF en stopt, en de app leest
    door tot de worker zijn kant sluit. Job.cancel sluit beide kanten, en dan is dat einde niet
    meer te zien. Niemand anders mag dan nog van de job lezen (de producent stopt zijn lezer
    eerst)."""
    sock: socket.socket = job._sock  # client.Job (frozen) wacht zelf niet op het einde
    job._cancelled = True
    if sock.fileno() < 0:
        return True
    loop = asyncio.get_running_loop()
    try:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_WR)
        async with asyncio.timeout(limit_s):
            while await loop.sock_recv(sock, 65536):
                pass
        return True
    except (TimeoutError, OSError):
        return False
    finally:
        job.finished = True
        sock.close()


class OneProducer[T]:
    """Eén producent tegelijk voor één sessie (Keuze 12). replace() stopt de vorige (en wacht
    tot die weg is) en start dan een nieuwe; komt er binnen coalesce_s een nieuwer verzoek, dan
    start alleen dat nieuwere en geeft het oudere None. Het eerste verzoek zonder vorige
    producent start meteen."""

    def __init__(self, coalesce_s: float = COALESCE_S):
        self.coalesce_s = coalesce_s
        self.current: T | None = None
        self._stop: Callable[[T], Awaitable[Any]] | None = None
        self._turn = 0
        self._lock = asyncio.Lock()
        self.started = 0  # hoeveel producenten er zo gestart zijn (tests, /beheer)

    async def replace(
        self, start: Callable[[], Awaitable[T]], stop: Callable[[T], Awaitable[Any]]
    ) -> T | None:
        self._turn += 1
        mine = self._turn
        if self.current is not None or self._lock.locked():
            await asyncio.sleep(self.coalesce_s)
            if mine != self._turn:
                return None  # een nieuwer verzoek neemt het over
        async with self._lock:
            if mine != self._turn:
                return None
            await self._stop_current()
            if mine != self._turn:
                return None  # tijdens het stoppen kwam er een nieuwer verzoek
            self.current = await start()
            self._stop = stop
            self.started += 1
            return self.current

    async def _stop_current(self) -> None:
        current, stop = self.current, self._stop
        self.current, self._stop = None, None
        if current is not None and stop is not None:
            await stop(current)

    async def close(self) -> None:
        """De producent stoppen (de sessie gaat dicht); een wachtend verzoek start niet meer."""
        self._turn += 1
        async with self._lock:
            await self._stop_current()
