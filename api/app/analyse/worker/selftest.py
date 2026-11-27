"""De zelftest van de worker van de analyse (fase 6, §3.11, K1-04; X2a). Zoals die van media
(app/media/worker/selftest.py), met de controles van de analyse:

- de uid is niet 10001 (de app), en is die van analyse (10003);
- CapEff is 0 en NoNewPrivs is 1 in /proc/self/status;
- er is alleen lo (socket.if_nameindex), en een verbinding naar buiten faalt meteen;
- / is alleen lezen; /data is leeg;
- /srv/zelftest-10001 (van de app, 0644) is niet te openen om te schrijven, ook niet via
  /proc/self/fd/N;
- /models is niet schrijfbaar (geen nieuw bestand, en geen model om in te schrijven);
- /keys bestaat niet (de sleutels per eigenaar zijn alleen van de app, Keuze 68);
- python-tekst naast de python van de worker is 0711 van root en niet te lezen: daaruit start
  elk kind, en dan is het vanaf de exec niet-dumpbaar (K1-04);
- met --socket: de map van de socket is 2750, van analyse, groep platform.

Faalt één controle, dan luistert de worker wel, maar antwoordt een RefusingWorker op elke hello
met done/sandbox en de reden (server.RefusingWorker); de app zegt dan down: sandbox. Met --local
(de tests van laag 2, buiten de container) draait de zelftest niet: sandbox "off".
"""

from __future__ import annotations

import contextlib
import errno
import grp
import os
import pwd
import socket
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.analyse.worker import child as kids

ANALYSE_UID = 10003
PLATFORM_UID = 10001
PROBE_FILE = Path("/srv/zelftest-10001")  # Dockerfile: leeg, 0644, van platform
DATA = Path("/data")
KEYS = Path("/keys")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(ok), str(detail)[:300]))
        return bool(ok)

    def result(self) -> dict[str, Any]:
        failed = [c for c in self.checks if not c.ok]
        problems = [f"{c.name}: {c.detail}" if c.detail else c.name for c in failed]
        return {
            "ok": not problems,
            "sandbox": "on" if not problems else "off",
            "problems": problems,
            "checks": [asdict(c) for c in self.checks],
        }


def status_field(name: str, text: str | None = None) -> str | None:
    """Een veld uit /proc/self/status (of uit text, voor een test)."""
    if text is None:
        try:
            with open("/proc/self/status") as f:
                text = f.read()
        except OSError:
            return None
    for line in text.splitlines():
        if line.startswith(name + ":"):
            return line.split(":", 1)[1].strip()
    return None


def _uid_of(name: str) -> int | None:
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def _gid_of(name: str) -> int | None:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


def privileges(report: Report, status: Callable[[str], str | None] = status_field) -> None:
    cap = status("CapEff")
    try:
        none = cap is not None and int(cap, 16) == 0
    except ValueError:
        none = False
    report.add("geen capabilities", none, f"CapEff {cap}")
    nnp = status("NoNewPrivs")
    report.add("no-new-privileges", nnp == "1", f"NoNewPrivs {nnp}")


def identity(report: Report) -> None:
    uid = os.getuid()
    analyse = _uid_of("analyse")
    ok = uid != PLATFORM_UID and uid == ANALYSE_UID and analyse == ANALYSE_UID
    report.add("uid 10003", ok, f"uid {uid} (analyse: {analyse})")


def _no_route() -> str:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            return "een verbinding met 1.1.1.1:53 lukte"
    except TimeoutError:
        return "1.1.1.1:53 gaf geen antwoord in plaats van een fout"
    except OSError:
        return ""


def network(report: Report) -> None:
    try:
        names = sorted(name for _i, name in socket.if_nameindex())
    except OSError as exc:
        names = [f"? ({exc.strerror})"]
    detail = ", ".join(names)
    if names == ["lo"]:
        detail = _no_route()
    report.add("geen netwerk", names == ["lo"] and not detail, detail or "alleen lo")


def filesystem(report: Report, models: Path, data: Path = DATA, keys: Path = KEYS) -> None:
    readonly = bool(os.statvfs("/").f_flag & os.ST_RDONLY)
    report.add("/ alleen lezen", readonly, "" if readonly else "/ is schrijfbaar")
    try:
        names = os.listdir(data)
        report.add(f"{data} leeg", names == [], f"{len(names)} namen in {data}")
    except OSError as exc:
        report.add(f"{data} leeg", False, f"{data}: {exc.strerror}")
    report.add(f"{keys} bestaat niet", not os.path.lexists(keys), f"{keys} bestaat")
    report.add(f"{models} niet schrijfbaar", *_not_writable(models))


def _not_writable(models: Path) -> tuple[bool, str]:
    if not models.is_dir():
        return False, f"{models} is geen map"
    probe = models / f".zelftest-{os.getpid()}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EROFS, errno.EPERM):
            return False, f"{models}: {exc.strerror}"
    else:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(probe)
        return False, f"een nieuw bestand in {models} lukte"
    for entry in sorted(models.rglob("*")):
        if entry.is_symlink() or not entry.is_file():
            continue
        try:
            os.close(os.open(entry, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW))
        except OSError:
            continue
        return False, f"{entry.name} is te schrijven"
    return True, ""


def probe_file(report: Report, path: Path = PROBE_FILE) -> None:
    """V1 van media: een fd die alleen leest, is niet om te zetten in een die schrijft."""
    name = f"{path} niet te schrijven"
    try:
        info = os.stat(path)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        report.add(name, False, f"{path}: {exc.strerror}")
        return
    try:
        if info.st_uid != PLATFORM_UID:
            report.add(name, False, f"{path} is van uid {info.st_uid}")
            return
        opened = []
        for label, mode in (("O_WRONLY", os.O_WRONLY), ("O_RDWR", os.O_RDWR)):
            for target in (str(path), f"/proc/self/fd/{fd}"):
                try:
                    os.close(os.open(target, mode | os.O_CLOEXEC))
                    opened.append(label)
                except OSError:
                    pass
        report.add(name, not opened, f"ging open met {', '.join(sorted(set(opened)))}")
    finally:
        os.close(fd)


def text_python(report: Report) -> None:
    name = f"{kids.COPY_NAME} 0711 van root"
    copy = kids.copy_path()
    try:
        info = os.stat(copy)
    except OSError:
        report.add(name, False, f"{copy} niet gevonden")
        return
    mode = stat.S_IMODE(info.st_mode)
    readable = os.access(copy, os.R_OK)
    ok = stat.S_ISREG(info.st_mode) and mode == 0o711 and info.st_uid == 0 and not readable
    report.add(name, ok, "" if ok else f"modus {mode:04o}, uid {info.st_uid}, leesbaar: {readable}")


def socket_dir(report: Report, socket_path: Path) -> None:
    directory = socket_path.parent
    try:
        info = os.stat(directory)
    except OSError as exc:
        report.add("map van de socket", False, f"{directory}: {exc.strerror}")
        return
    mode = stat.S_IMODE(info.st_mode)
    ok = mode == 0o2750 and info.st_uid == ANALYSE_UID and info.st_gid == _gid_of("platform")
    report.add(
        "map van de socket 2750 analyse:platform",
        ok,
        f"modus {mode:04o}, uid {info.st_uid}, gid {info.st_gid}",
    )


def startup(*, models: Path, socket_path: Path | None = None) -> dict[str, Any]:
    """De afscherming bij de start (__main__): {"ok", "sandbox", "problems", "checks"}."""
    report = Report()
    identity(report)
    privileges(report)
    network(report)
    filesystem(report, models)
    probe_file(report)
    text_python(report)
    if socket_path is not None:
        socket_dir(report, socket_path)
    return report.result()
