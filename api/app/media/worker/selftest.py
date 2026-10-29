"""De zelftest van de worker (bouwontwerp fase 5, §2 M1, Keuze 11). Van M1.

Twee delen:

- de afscherming (isolation): wat de container moet geven. De worker draait dit bij elke start
  zonder --local (__main__). Slaagt alles, dan zet hij caps.sandbox op "on" (review S6); faalt
  er iets, dan neemt hij geen opdracht aan, en geeft hij elke hello een weigering met de reden
  (RefusingWorker): de app zegt dan media_worker_down met die reden, en stuurt nooit een fd.
  Met --local (de lokale worker, Keuze 13) is dat geen fout maar "sandbox: off";
- alles (full, `python -I -m app.media.worker --selftest`): de afscherming, plus wat ffmpeg en
  het image moeten kunnen. Exit 1 bij een fout; CI gebruikt het als harde poort (de job
  media-image, in de echte container van compose.media.yaml).

De afscherming:
- de uid is 10002 (media), niet die van de app (10001), en het bestand van 10001 (PROBE_FILE,
  uit het Dockerfile, 0644) is via /proc/self/fd/N niet opnieuw te openen om te schrijven (V1);
  schrijven op de fd zelf (alleen lezen, zoals de app hem geeft) faalt met EBADF;
- ffmpeg en ffprobe zijn 0711 van root: de worker kan ze niet lezen, dus elk kind is niet
  dumpbaar (V2);
- een kind kan /proc/<worker>/fd en /proc/<worker>/environ niet lezen (PR_SET_DUMPABLE 0; in
  environ stond de sleutel van hello, review S1);
- geen capabilities (CapEff 0), NoNewPrivs 1, geen netwerk (alleen lo, en een verbinding met
  1.1.1.1:53 faalt meteen), / alleen lezen, /data
  leeg, en (met --socket) de map van de socket 2750 van media met de groep platform.

Wat ffmpeg moet kunnen (alleen full):
- versie 7.1.x (--ffmpeg zegt welk begin; de tests in de sandbox geven 6.1 mee);
- de encoders, filters, protocollen en demuxers van §3.3, en lavfi onder -devices (S1);
- een weigering door -format_whitelist en door -protocol_whitelist;
- nice en ionice in het image;
- een transcode van 4 s testsrc2 naar fMP4 op een pipe: twee fragmenten, tfdt op 1,000 en 3,000
  (+frag_discont, J4), trun versie 1 met de eerste offset 0 (+negative_cts_offsets, V4);
- 1080 veryfast met -threads 4 onder RLIMIT_DATA 1.200 MB (V6);
- een buur ziet /proc/<ffmpeg>/fd niet, wel /proc/<ffmpeg>/status (V2; alleen in de container);
- een vraag of fd's vóór hello weigert de worker (review S1).

Elk kind start in de event loop (run.spawn), na elkaar: nooit meer dan 2 ffmpeg tegelijk.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import grp
import json
import os
import pwd
import secrets
import shutil
import socket
import stat
import struct
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.media import wire
from app.media.worker import args as A
from app.media.worker import run as R
from app.media.worker.ops import caps as caps_op
from app.media.worker.server import Config, Worker

MEDIA_UID = 10002
PLATFORM_UID = 10001
PROBE_FILE = Path("/srv/zelftest-10001")  # Dockerfile: leeg, 0644, van platform
VERSION = "7.1."
ENCODERS = ("libx264", "aac", "libopus", "libvpx-vp9", "mjpeg", "libx265", "flac")
FILTERS = ("zscale", "tonemap", "bwdif", "overlay", "scale", "fps", "tile")
PROTOCOLS = ("fd", "pipe")
STEP_S = 60.0
MKV_LIMIT = 4 * 1024 * 1024
FMP4_LIMIT = 32 * 1024 * 1024

# Het kind dat in de worker probeert te kijken (argv[1] = de pid van de worker)
_PEEK = """
import json, os, sys
pid = sys.argv[1]
out = {}
for what, path in (("fd", f"/proc/{pid}/fd"), ("environ", f"/proc/{pid}/environ"),
                   ("status", f"/proc/{pid}/status")):
    try:
        if what == "fd":
            os.listdir(path)
        else:
            with open(path, "rb") as f:
                f.read(64)
        out[what] = "open"
    except PermissionError:
        out[what] = "dicht"
    except OSError as e:
        out[what] = type(e).__name__
print(json.dumps(out))
"""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    sandbox: bool = False  # een controle van de afscherming (met --local alleen een melding)


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, name: str, ok: bool, detail: str = "", *, sandbox: bool = False) -> bool:
        self.checks.append(Check(name, bool(ok), str(detail)[:300], sandbox))
        return bool(ok)

    def result(self, *, local: bool) -> dict[str, Any]:
        """{"ok", "sandbox", "problems", "checks"} (types.Selftest plus de controles). Met
        local tellen de controles van de afscherming niet, en is sandbox "off"."""
        counted = [c for c in self.checks if not (local and c.sandbox)]
        problems = [f"{c.name}: {c.detail}" if c.detail else c.name for c in counted if not c.ok]
        shielded = [c for c in self.checks if c.sandbox]
        on = not local and bool(shielded) and all(c.ok for c in shielded)
        return {
            "ok": not problems,
            "sandbox": "on" if on else "off",
            "problems": problems,
            "checks": [asdict(c) for c in self.checks],
        }


# --- de afscherming --------------------------------------------------------------------------


def _status_field(name: str) -> str | None:
    with contextlib.suppress(OSError):
        with open("/proc/self/status") as f:
            for line in f:
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


def _reopen(report: Report) -> None:
    """V1: een fd die alleen leest, is via /proc/self/fd/N niet om te zetten in een die schrijft."""
    names = ("blob niet heropenbaar", "schrijven op een fd die leest faalt")
    try:
        info = os.stat(PROBE_FILE)
        fd = os.open(PROBE_FILE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        for name in names:
            report.add(name, False, f"{PROBE_FILE}: {exc.strerror}", sandbox=True)
        return
    try:
        if info.st_uid != PLATFORM_UID:
            for name in names:
                report.add(name, False, f"{PROBE_FILE} is van uid {info.st_uid}", sandbox=True)
            return
        opened = []
        for name, mode in (("O_WRONLY", os.O_WRONLY), ("O_RDWR", os.O_RDWR)):
            try:
                os.close(os.open(f"/proc/self/fd/{fd}", mode | os.O_CLOEXEC))
                opened.append(name)
            except PermissionError:
                pass
            except OSError as exc:
                opened.append(f"{name} ({exc.strerror})")
        report.add(
            names[0],
            not opened,
            f"/proc/self/fd/N ging open met {', '.join(opened)}" if opened else "",
            sandbox=True,
        )
        # en schrijven op de fd zelf (zoals de app hem doorgeeft: alleen lezen) faalt ook
        try:
            os.write(fd, b"x")
            wrote = "de fd schreef"
        except OSError as exc:
            wrote = "" if exc.errno == errno.EBADF else exc.strerror or str(exc)
        report.add(names[1], not wrote, wrote, sandbox=True)
    finally:
        os.close(fd)


def _binaries(report: Report) -> None:
    """V2: ffmpeg en ffprobe 0711 van root, en de worker kan ze niet lezen."""
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        if path is None:
            report.add(f"{name} 0711 van root", False, "niet gevonden", sandbox=True)
            continue
        real = os.path.realpath(path)
        info = os.stat(real)
        mode = stat.S_IMODE(info.st_mode)
        readable = os.access(real, os.R_OK)
        ok = mode == 0o711 and info.st_uid == 0 and not readable
        detail = "" if ok else f"modus {mode:04o}, uid {info.st_uid}, leesbaar: {readable}"
        report.add(f"{name} 0711 van root", ok, detail, sandbox=True)


def _no_route() -> str:
    """Met alleen lo faalt een verbinding naar buiten meteen (ENETUNREACH): "" als dat zo is."""
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            return "een verbinding met 1.1.1.1:53 lukte"
    except TimeoutError:
        return "1.1.1.1:53 gaf geen antwoord in plaats van een fout"
    except OSError:
        return ""


def _container(report: Report, socket_path: Path | None) -> None:
    uid = os.getuid()
    media, platform = _uid_of("media"), _uid_of("platform")
    report.add(
        "uid 10002",
        uid == MEDIA_UID and media == MEDIA_UID and platform == PLATFORM_UID,
        f"uid {uid} (media: {media}, platform: {platform})",
        sandbox=True,
    )
    cap = _status_field("CapEff")
    report.add("geen capabilities", cap is not None and int(cap, 16) == 0, f"CapEff {cap}",
               sandbox=True)  # fmt: skip
    nnp = _status_field("NoNewPrivs")
    report.add("no-new-privileges", nnp == "1", f"NoNewPrivs {nnp}", sandbox=True)
    try:
        names = sorted(name for _i, name in socket.if_nameindex())
    except OSError as exc:
        names = [f"? ({exc.strerror})"]
    detail = ", ".join(names)
    if names == ["lo"]:
        detail = _no_route()
    report.add("geen netwerk", names == ["lo"] and not detail, detail or "alleen lo", sandbox=True)
    readonly = bool(os.statvfs("/").f_flag & os.ST_RDONLY)
    report.add("/ alleen lezen", readonly, "" if readonly else "/ is schrijfbaar", sandbox=True)
    try:
        data = os.listdir("/data")
        report.add("/data leeg", data == [], f"{len(data)} namen in /data", sandbox=True)
    except OSError as exc:
        report.add("/data leeg", False, f"/data: {exc.strerror}", sandbox=True)
    if socket_path is not None:
        directory = socket_path.parent
        try:
            info = os.stat(directory)
        except OSError as exc:
            report.add("map van de socket", False, f"{directory}: {exc.strerror}", sandbox=True)
        else:
            mode = stat.S_IMODE(info.st_mode)
            ok = mode == 0o2750 and info.st_uid == MEDIA_UID and info.st_gid == _gid_of("platform")
            report.add(
                "map van de socket 2750 media:platform", ok,
                f"modus {mode:04o}, uid {info.st_uid}, gid {info.st_gid}", sandbox=True,
            )  # fmt: skip


async def _peek(report: Report) -> None:
    """Een kind van de worker kijkt in /proc/<worker>: fd en environ dicht, status open."""
    code = _PEEK
    try:
        done, out, tail = await R.run(
            [sys.executable, "-I", "-c", code, str(os.getpid())],
            memory_mb=A.SHORT_MEMORY_MB,
            limit_s=STEP_S,
        )
        seen = json.loads(out) if done.rc == 0 else {}
    except (OSError, ValueError, TimeoutError) as exc:
        seen, tail = {}, str(exc)
    for what in ("fd", "environ"):
        got = seen.get(what, tail or "?")
        report.add(f"een kind leest /proc/<worker>/{what} niet", got == "dicht", got,
                   sandbox=True)  # fmt: skip


async def isolation(report: Report, *, socket_path: Path | None = None) -> None:
    _container(report, socket_path)
    _binaries(report)
    _reopen(report)
    await _peek(report)


async def startup(*, local: bool, socket_path: Path | None = None) -> dict[str, Any]:
    """De afscherming bij de start (__main__): types.Selftest plus de controles."""
    report = Report()
    await isolation(report, socket_path=socket_path)
    return report.result(local=local)


# --- wat ffmpeg moet kunnen ------------------------------------------------------------------


def _list(report: Report, caps: dict[str, Any], version: str) -> None:
    for name in ("ffmpeg", "ffprobe"):
        got = caps.get(name)
        ok = bool(got) and str(got).startswith(version)
        report.add(f"{name} versie {version or '(elke)'}", ok, str(got))
    have = set(caps.get("encoders") or [])
    missing = [e for e in ENCODERS if e not in have]
    report.add("encoders", not missing, ", ".join(missing))
    have = set(caps.get("filters") or [])
    missing = [f for f in FILTERS if f not in have]
    report.add("filters", not missing, ", ".join(missing))
    have = set(caps.get("protocols") or [])
    missing = [p for p in PROTOCOLS if p not in have]
    report.add("protocollen fd en pipe", not missing, ", ".join(missing))
    have = set(caps.get("demuxers") or [])
    missing = [d for d in (*A.DEMUXERS, "lavfi") if d not in have]
    report.add("demuxers en lavfi", not missing, ", ".join(missing))
    missing = [n for n in ("nice", "ionice") if shutil.which(n) is None]
    report.add("nice en ionice", not missing, ", ".join(missing))


_BASE = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]


async def _capture(argv: list[str], *, limit: int, memory_mb: int = 1200) -> tuple[int, bytes, str]:
    """Een kind met zijn stdout op een pipe van de worker (de grens van run.spawn: geen
    bestanden schrijven); hooguit limit bytes."""
    r, w = os.pipe2(os.O_CLOEXEC)
    try:
        child = R.spawn(argv, stdout=w, memory_mb=memory_mb)
    finally:
        os.close(w)
    data = bytearray()
    loop = asyncio.get_running_loop()
    os.set_blocking(r, False)
    ready = asyncio.Event()
    loop.add_reader(r, ready.set)
    try:
        async with asyncio.timeout(STEP_S):
            while True:
                await ready.wait()
                ready.clear()
                try:
                    chunk = os.read(r, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                data += chunk
                if len(data) > limit:
                    await child.stop()
                    return -1, bytes(data[:limit]), "te veel uitvoer"
            done = await child.wait()
    except TimeoutError:
        await child.stop()
        return -1, bytes(data), "te traag"
    finally:
        loop.remove_reader(r)
        os.close(r)
        await child.stop()
    return done.rc, bytes(data), child.tail()


def _memfd(data: bytes) -> int:
    fd = os.memfd_create("zelftest", os.MFD_CLOEXEC)
    os.write(fd, data)
    return fd


async def _whitelists(report: Report) -> None:
    """Een mkv op een pipe, in een memfd; ffprobe leest hem, en weigert hem met een smallere
    -format_whitelist of -protocol_whitelist."""
    rc, mkv, tail = await _capture(
        [*_BASE, "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10", "-t", "1",
         "-c:v", "mjpeg", "-f", "matroska", "pipe:1"],
        limit=MKV_LIMIT,
    )  # fmt: skip
    if rc != 0 or not mkv:
        report.add("witte lijsten", False, f"geen mkv: {tail}")
        return
    fd = _memfd(mkv)
    try:

        def probe(formats: str, protocols: str) -> list[str]:
            return [
                "ffprobe", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", protocols,
                "-format_whitelist", formats, "-f", "matroska", "-fd", "3", "-show_format",
                "-of", "json", "-i", "fd:",
            ]  # fmt: skip

        full = ",".join(A.DEMUXERS)
        outcomes = {}
        for name, argv in (
            ("gewoon", probe(full, "fd,pipe")),
            ("format_whitelist", probe("mov", "fd,pipe")),
            ("protocol_whitelist", probe(full, "pipe")),
        ):
            done, _out, _tail = await R.run(argv, inputs=[fd], memory_mb=A.SHORT_MEMORY_MB)
            outcomes[name] = done.rc
    finally:
        os.close(fd)
    report.add("ffprobe leest de mkv", outcomes["gewoon"] == 0, f"rc {outcomes['gewoon']}")
    for name in ("format_whitelist", "protocol_whitelist"):
        report.add(f"weigering door -{name}", outcomes[name] != 0, f"rc {outcomes[name]}")


def boxes(data: bytes, start: int = 0, end: int | None = None) -> Iterator[tuple[str, int, int]]:
    """De boxen van MP4 tussen start en end: (soort, begin van de inhoud, einde). Stopt bij een
    kapotte maat; nooit een fout (fuzztest, regel 10)."""
    end = len(data) if end is None else min(end, len(data))
    at = start
    while at + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, at)
        head = 8
        if size == 1:
            if at + 16 > end:
                return
            size = struct.unpack_from(">Q", data, at + 8)[0]
            head = 16
        elif size == 0:
            size = end - at
        if size < head or at + size > end:
            return
        yield kind.decode("latin-1"), at + head, at + size
        at += size


def _child(data: bytes, start: int, end: int, kind: str) -> tuple[int, int] | None:
    for name, a, b in boxes(data, start, end):
        if name == kind:
            return a, b
    return None


def fragments(data: bytes) -> dict[str, Any]:
    """Wat de zelftest van een fMP4 wil weten: de timescale (mdhd), en per moof de tijd van
    tfdt in seconden, de versie van trun en de eerste offset (cto) van de samples."""
    out: dict[str, Any] = {"timescale": None, "moofs": []}
    moov = _child(data, 0, len(data), "moov")
    if moov:
        trak = _child(data, *moov, "trak")
        mdia = _child(data, *trak, "mdia") if trak else None
        mdhd = _child(data, *mdia, "mdhd") if mdia else None
        if mdhd and mdhd[1] - mdhd[0] >= 24:
            version = data[mdhd[0]]
            at = mdhd[0] + (20 if version == 1 else 12)
            if at + 4 <= mdhd[1]:
                out["timescale"] = struct.unpack_from(">I", data, at)[0] or None
    for name, a, b in boxes(data):
        if name != "moof":
            continue
        frag: dict[str, Any] = {"tfdt": None, "trun_version": None, "first_cto": None}
        traf = _child(data, a, b, "traf")
        if traf:
            tfdt = _child(data, *traf, "tfdt")
            if tfdt and tfdt[1] - tfdt[0] >= 8:
                version = data[tfdt[0]]
                if version == 1 and tfdt[1] - tfdt[0] >= 12:
                    frag["tfdt"] = struct.unpack_from(">Q", data, tfdt[0] + 4)[0]
                elif version == 0:
                    frag["tfdt"] = struct.unpack_from(">I", data, tfdt[0] + 4)[0]
            trun = _child(data, *traf, "trun")
            if trun and trun[1] - trun[0] >= 8:
                at, stop = trun
                version = data[at]
                flags = int.from_bytes(data[at + 1 : at + 4], "big")
                frag["trun_version"] = version
                pos = at + 8  # versie, vlaggen, aantal samples
                pos += 4 if flags & 0x1 else 0  # data_offset
                pos += 4 if flags & 0x4 else 0  # first_sample_flags
                pos += 4 if flags & 0x100 else 0  # duur
                pos += 4 if flags & 0x200 else 0  # grootte
                pos += 4 if flags & 0x400 else 0  # vlaggen
                if flags & 0x800 and pos + 4 <= stop:
                    frag["first_cto"] = struct.unpack_from(">i" if version else ">I", data, pos)[0]
        out["moofs"].append(frag)
    return out


TRANSCODE = [
    *_BASE, "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24", "-t", "4",
    "-vf", "scale=w=1280:h=720:flags=bicubic,format=yuv420p", "-fpsmax", "60",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-maxrate", "3000k",
    "-bufsize", "6000k", "-profile:v", "high", "-level:v", "3.1", "-threads", str(A.THREADS),
    "-sc_threshold", "0", "-g", "100000", "-force_key_frames", "expr:gte(t,n_forced*2)",
    "-forced-idr", "1", "-output_ts_offset", "1", "-avoid_negative_ts", "disabled",
    "-f", "mp4", "-movflags",
    "+frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont+negative_cts_offsets",
    "-fflags", "+bitexact", "pipe:1",
]  # fmt: skip

HEAVY = [
    *_BASE, "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24", "-t", "2",
    "-c:v", "libx264", "-preset", "veryfast", "-threads", str(A.THREADS), "-f", "null", "-",
]  # fmt: skip


async def _transcode(report: Report) -> None:
    rc, data, tail = await _capture(TRANSCODE, limit=FMP4_LIMIT)
    if rc != 0:
        report.add("transcode naar fMP4", False, tail or f"rc {rc}")
        return
    info = fragments(data)
    scale = info["timescale"]
    moofs = info["moofs"]
    times = [round(m["tfdt"] / scale, 3) for m in moofs if scale and m["tfdt"] is not None]
    report.add("twee fragmenten", len(moofs) == 2, f"{len(moofs)} moof")
    report.add("tfdt op 1,000 en 3,000 (+frag_discont)", times == [1.0, 3.0], f"{times}")
    versions = [m["trun_version"] for m in moofs]
    first = moofs[0]["first_cto"] if moofs else None
    report.add(
        "trun versie 1, eerste offset 0 (+negative_cts_offsets)",
        bool(versions) and all(v == 1 for v in versions) and first == 0,
        f"versies {versions}, eerste offset {first}",
    )


async def _heavy(report: Report) -> None:
    child = R.spawn(HEAVY, memory_mb=1200)
    try:
        async with asyncio.timeout(STEP_S):
            done = await child.wait()
    except TimeoutError:
        await child.stop()
        report.add("1080 veryfast onder RLIMIT_DATA 1.200 MB", False, "te traag")
        return
    report.add("1080 veryfast onder RLIMIT_DATA 1.200 MB", done.rc == 0,
               child.tail() if done.rc else "")  # fmt: skip


async def _neighbour(report: Report) -> None:
    """V2: een tweede kind ziet /proc/<ffmpeg>/fd niet (ffmpeg is niet dumpbaar), wel status."""
    ffmpeg = R.spawn(
        [*_BASE, "-re", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10", "-t", "20",
         "-f", "null", "-"],
        memory_mb=A.SHORT_MEMORY_MB,
    )  # fmt: skip
    try:
        await asyncio.sleep(0.3)
        done, out, tail = await R.run(
            [sys.executable, "-I", "-c", _PEEK, str(ffmpeg.pid)], memory_mb=A.SHORT_MEMORY_MB
        )
        seen = json.loads(out) if done.rc == 0 else {}
    except (OSError, ValueError, TimeoutError) as exc:
        seen, tail = {}, str(exc)
    finally:
        await ffmpeg.stop()
    ok = seen.get("fd") == "dicht" and seen.get("status") == "open"
    report.add("een buur ziet /proc/<ffmpeg>/fd niet, wel status", ok, json.dumps(seen) or tail,
               sandbox=True)  # fmt: skip


async def _hello(report: Report) -> None:
    """Een vraag zonder hello, en fd's op hello: de worker weigert en houdt de fd niet."""
    base = Path(tempfile.mkdtemp(prefix="zelftest-"))
    path = base / "w.sock"
    worker = Worker(Config(socket=path, key=secrets.token_hex(32)), {"protocol": wire.PROTOCOL})
    await worker.start()
    loop = asyncio.get_running_loop()
    before = len(os.listdir("/proc/self/fd"))
    try:
        for name, message, with_fd in (
            ("een vraag vóór hello", {"v": wire.PROTOCOL, "op": "caps", "args": {}}, False),
            ("fd's vóór hello", wire.hello(secrets.token_hex(16)), True),
        ):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
            sock.setblocking(False)
            fd = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
            try:
                with wire.socket_address(path) as address:
                    await loop.sock_connect(sock, address)
                await wire.send(sock, message, [fd] if with_fd else [])
                async with asyncio.timeout(5):
                    reply, extra = await wire.receive(sock, max_fds=0)
                wire.close_all(extra)
                refused = reply.get("ev") == "error" and reply.get("code") in (
                    wire.Code.PROTOCOL,
                    wire.Code.BAD_FDS,
                )
                report.add(f"{name} geweigerd", refused, json.dumps(reply)[:200])
            except (OSError, TimeoutError, wire.WireError) as exc:
                report.add(f"{name} geweigerd", isinstance(exc, wire.Closed), repr(exc)[:200])
            finally:
                os.close(fd)
                sock.close()
        await asyncio.sleep(0.1)  # de worker sluit zijn kant net na het antwoord
        after = len(os.listdir("/proc/self/fd"))
        report.add("de worker houdt geen fd van vóór hello", after <= before, f"{before} → {after}")
    finally:
        await worker.stop()
        shutil.rmtree(base, ignore_errors=True)


async def full(*, local: bool, version: str = VERSION, socket_path: Path | None = None) -> dict:
    """Alles (--selftest)."""
    report = Report()
    caps = caps_op.gather(local=local, max_procs=3, max_transcodes=1)
    _list(report, caps, version)
    if caps.get("ffmpeg") is not None:
        await _whitelists(report)
        await _transcode(report)
        await _heavy(report)
        await _neighbour(report)
    await _hello(report)
    await isolation(report, socket_path=socket_path)
    return report.result(local=local)


# --- een worker die niets aanneemt -----------------------------------------------------------


class RefusingWorker(Worker):
    """De afscherming faalde: luisteren (zodat de app de reden ziet), maar elke hello krijgt een
    weigering met de reden, en er komt nooit een vraag of fd binnen (Keuze 11)."""

    def __init__(self, config: Config, caps: dict[str, Any], reason: str):
        super().__init__(config, caps)
        self.reason = reason[: wire.DETAIL_CHARS]

    async def _hello(self, sock: socket.socket) -> bool:
        with contextlib.suppress(wire.Closed, wire.WireError, OSError):
            message, fds = await wire.receive(sock, max_fds=0)
            wire.close_all(fds)
        with contextlib.suppress(OSError):
            await wire.send(sock, wire.error(wire.Code.NOT_ALLOWED, self.reason))
        return False
