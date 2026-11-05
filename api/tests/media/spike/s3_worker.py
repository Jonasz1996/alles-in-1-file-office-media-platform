"""S3, de kant van de worker (bouwontwerp fase 5, §3.7 en Keuze 11).

Draait als `media` (uid 10002) in het afgeleide image, met de afscherming van Keuze 11
(`s3.compose.yaml`). Maakt de socket in het benoemde volume (`/run/media`, modus 0660), wacht op
één verbinding van de app, en krijgt daarin de fd's als SCM_RIGHTS: de blob (alleen lezen) en de
schrijfkant van een pipe van de app. Dan de zelftest van Keuze 11:

- `/proc/self/fd/N` van de blob opnieuw openen (schrijven) faalt;
- de worker zet `PR_SET_DUMPABLE` 0: een kind kan `/proc/<worker>/fd` dan niet openen;
- ffmpeg en ffprobe zijn 0711: de worker kan ze niet lezen, wel starten, en een buurproces ziet
  `/proc/<ffmpeg>/fd` niet (wel `status`, voor `VmRSS`);
- `id -u` is 10002.

Met de echte ffmpeg ook: ffprobe op de fd, en een remux van de fd naar de pipe van de app.
Alles gaat als antwoord terug naar de app (`{"ev": "result"}`); de app print het.

    python3 -I s3_worker.py --socket /run/media/worker.sock [--wacht-s 90]
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import grp
import hashlib
import json
import os
import pwd
import resource
import socket
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import berichten  # noqa: E402
import commandos  # noqa: E402
from gedeeld import MB, cpu_grens, geheugen_grens, log, start, wacht  # noqa: E402

PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4
GRENZEN = {"RLIMIT_CORE": 0, "RLIMIT_NOFILE": 256, "RLIMIT_FSIZE": 0, "RLIMIT_DATA": 1200 * MB}
STANDIN = os.environ.get("SPIKE_FFMPEG_STANDIN") == "1"

# Een buurproces (zelfde uid): wat ziet het van een ander proces in /proc? `fd_open` is de
# eerste fd (de blob, of stdin van de buur); `fd_open_eigen` een bestand van de worker zelf in
# /tmp. De blob is 0600 van de app, dus die opent geen buur ooit via /proc; het eigen bestand
# wel, zolang de worker dumpbaar is. Dat is de controle.
KIJKER = r"""
import json, os, sys
pid, fds = sys.argv[1], sys.argv[2:]
uit = {}
def probeer(naam, f):
    try:
        uit[naam] = f()
    except OSError as e:
        uit[naam] = type(e).__name__
def open_fd(fd):
    os.close(os.open(f"/proc/{pid}/fd/{fd}", os.O_RDONLY | os.O_NONBLOCK))
    return "ok"
def rss():
    with open(f"/proc/{pid}/status") as f:
        return next((r.split()[1] + " kB" for r in f if r.startswith("VmRSS")), "geen VmRSS")
probeer("fd_lijst", lambda: "ok: %d" % len(os.listdir(f"/proc/{pid}/fd")))
probeer("fd_open", lambda: open_fd(fds[0]))
if len(fds) > 1:
    probeer("fd_open_eigen", lambda: open_fd(fds[1]))
probeer("status", rss)
print(json.dumps(uit))
"""


def fout(e: OSError) -> str:
    return f"{type(e).__name__} ({errno.errorcode.get(e.errno, e.errno)})"


def groep(gid: int) -> str | None:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return None


def bestand(pad: str) -> dict:
    try:
        st = os.stat(pad)
    except OSError as e:
        return {"fout": fout(e)}
    return {
        "modus": f"{stat.S_IMODE(st.st_mode):04o}",
        "uid": st.st_uid,
        "gid": st.st_gid,
        "groep": groep(st.st_gid),
    }


def status_velden() -> dict:
    uit = {}
    with open("/proc/self/status") as f:
        for regel in f:
            k, _, v = regel.partition(":")
            if k in ("CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Seccomp"):
                uit[k] = v.strip()
    return uit


def koppelingen() -> dict:
    """Wat er op /, /data, /tmp en /run/media gekoppeld is (uit mountinfo)."""
    uit = {}
    with open("/proc/self/mountinfo") as f:
        for regel in f:
            delen = regel.split()
            punt = delen[4]
            if punt in ("/", "/data", "/tmp", "/run/media", "/spike"):
                streep = delen.index("-")
                uit[punt] = {"opties": delen[5], "type": delen[streep + 1]}
    return uit


def netwerk() -> dict:
    with open("/proc/net/dev") as f:
        interfaces = [r.split(":")[0].strip() for r in f.readlines()[2:]]
    uit = {"interfaces": interfaces}
    for naam, adres in (("internet", ("1.1.1.1", 53)), ("docker_host", ("172.17.0.1", 80))):
        try:
            socket.create_connection(adres, timeout=2).close()
            uit[naam] = "verbonden (FOUT)"
        except OSError as e:
            uit[naam] = fout(e)
    return uit


def afscherming() -> dict:
    """Wat de worker van zijn eigen afscherming ziet."""
    try:
        open("/spike-schrijftest", "w").close()
        rootfs = "schrijfbaar (FOUT)"
    except OSError as e:
        rootfs = fout(e)
    pids = None
    for pad in ("/sys/fs/cgroup/pids.max", "/sys/fs/cgroup/pids/pids.max"):
        try:
            with open(pad) as f:
                pids = f.read().strip()
            break
        except OSError:
            continue
    try:
        data = os.listdir("/data")
    except OSError as e:
        data = fout(e)
    return {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "groepen": os.getgroups(),
        "naam": pwd.getpwuid(os.getuid()).pw_name,
        "status": status_velden(),
        "rlimit_nofile": resource.getrlimit(resource.RLIMIT_NOFILE),
        "rlimit_core": resource.getrlimit(resource.RLIMIT_CORE),
        "pids_max": pids,
        "cpu_grens": cpu_grens(),
        "geheugen_grens": geheugen_grens(),
        "koppelingen": koppelingen(),
        "rootfs_schrijven": rootfs,
        "data": data,
        "data_map": bestand("/data"),
        "netwerk": netwerk(),
        "run_media": bestand("/run/media"),
    }


def kijk(pid: int, *fds: int) -> dict:
    r = subprocess.run(
        [sys.executable, "-I", "-c", KIJKER, str(pid), *map(str, fds)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"fout": (r.stdout + r.stderr)[-300:]}


def zelftest(args: dict, fds: list[int], libc) -> dict:
    uit: dict = {"fds": len(fds)}
    if len(fds) != 2:
        uit["fout"] = f"2 fd's verwacht, {len(fds)} gekregen"
        return uit
    blob, pijp = fds
    st = os.fstat(blob)
    link = os.readlink(f"/proc/self/fd/{blob}")
    uit["blob"] = {
        "uid": st.st_uid,
        "modus": f"{stat.S_IMODE(st.st_mode):04o}",
        "grootte": st.st_size,
        "readlink": link,
        "pad_bestaat_hier": os.path.exists(link),
        "cloexec": not os.get_inheritable(blob),
    }
    ps = os.fstat(pijp)
    uit["pijp"] = {"uid": ps.st_uid, "fifo": stat.S_ISFIFO(ps.st_mode)}
    # lezen via de fd: een stuk midden in het bestand
    stuk = os.pread(blob, int(args["lengte"]), int(args["offset"]))
    uit["sha_stuk"] = hashlib.sha256(stuk).hexdigest()
    try:
        os.write(blob, b"x")
        uit["schrijven_op_fd"] = "geschreven (FOUT)"
    except OSError as e:
        uit["schrijven_op_fd"] = fout(e)
    heropen = {}
    for naam, vlag in (("O_WRONLY", os.O_WRONLY), ("O_RDWR", os.O_RDWR), ("O_RDONLY", os.O_RDONLY)):
        try:
            os.close(os.open(f"/proc/self/fd/{blob}", vlag))
            heropen[naam] = "geopend"
        except OSError as e:
            heropen[naam] = fout(e)
    uit["heropen_via_proc"] = heropen
    # niet dumpbaar: eerst de controle (dumpbaar 1), dan 0, en zo blijft het
    eigen, eigen_pad = tempfile.mkstemp(prefix="eigen-")
    os.unlink(eigen_pad)
    uit["dumpbaar_bij_start"] = libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)
    libc.prctl(PR_SET_DUMPABLE, 1, 0, 0, 0)
    uit["kind_bij_dumpbaar_1"] = kijk(os.getpid(), blob, eigen)
    libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    uit["kind_bij_dumpbaar_0"] = kijk(os.getpid(), blob, eigen)
    uit["dumpbaar_nu"] = libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)
    os.close(eigen)
    # ffmpeg en ffprobe: 0711 en eigenaar root; lezen mag de worker niet
    programmas = {}
    for pad in ("/usr/bin/ffmpeg", "/usr/bin/ffprobe"):
        info = bestand(pad)
        try:
            with open(pad, "rb") as f:
                f.read(4)
            info["lezen"] = "gelezen (FOUT)"
        except OSError as e:
            info["lezen"] = fout(e)
        programmas[pad] = info
    uit["programmas"] = programmas
    uit["buren"] = buren()
    if STANDIN:
        uit["ffmpeg"] = "overgeslagen: stand-in zonder ffmpeg"
    else:
        uit["ffmpeg"] = via_fd(blob, pijp, args.get("demuxer", "matroska"))
    os.close(pijp)
    os.close(blob)
    return uit


def buren() -> dict:
    """Een buurproces van dezelfde uid tegen ffmpeg (0711) en tegen een gewoon programma."""
    uit = {}
    r, w = os.pipe()
    if STANDIN:
        argv = ["/usr/bin/ffmpeg", "30"]  # de stand-in is een kopie van sleep
    else:
        argv = [
            "/usr/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-re",
            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "30", "-f", "null", "-",
        ]  # fmt: skip
    for naam, cmd in (
        ("ffmpeg_0711", argv),
        ("controle_python", [sys.executable, "-I", "-c", "import time; time.sleep(30)"]),
    ):
        try:
            p = subprocess.Popen(cmd, stdin=r, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as e:
            uit[naam] = {"start": fout(e)}
            continue
        time.sleep(0.5)
        uit[naam] = {"start": "ok" if p.poll() is None else f"klaar met {p.returncode}"}
        uit[naam]["buur"] = kijk(p.pid, 0)
        p.kill()
        p.wait()
        if p.stderr:
            uit[naam]["stderr"] = p.stderr.read().decode("utf-8", "replace")[-300:]
            p.stderr.close()
    os.close(r)
    os.close(w)
    return uit


def via_fd(blob: int, pijp: int, demuxer: str) -> dict:
    """ffprobe op de fd (Keuze 15), en een remux van de fd naar de pipe van de app."""
    uit = {}
    p, staart = start(commandos.probe(demuxer), bron=blob, grenzen=GRENZEN)
    tekst = p.stdout.read()
    p.stdout.close()
    w = wacht(p, 60)
    uit["probe"] = {"rc": w["rc"], "stderr": staart.tekst()[-300:]}
    try:
        probe = json.loads(tekst)
    except ValueError:
        return uit
    fmt = probe.get("format", {})
    streams = probe.get("streams", [])
    uit["probe"].update(
        {
            "format": fmt.get("format_name"),
            "start_time": fmt.get("start_time"),
            "streams": [(s.get("index"), s.get("codec_name")) for s in streams],
        }
    )
    video = next((s["index"] for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s["index"] for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return uit
    argv = commandos.remux(demuxer, 0, float(fmt.get("start_time") or 0), video, audio)
    # Een fd uit SCM_RIGHTS deelt zijn positie (de open file description) met de app en met elk
    # kind. ffmpeg begint met `fd:` op die positie, niet op 0. Eerst laten we dat zien (de
    # remux na de probe, zonder terugzetten, naar /dev/null), dan de echte remux na lseek 0.
    uit["positie_na_probe"] = os.lseek(blob, 0, os.SEEK_CUR)
    p, staart = start(argv, bron=blob, stdout=subprocess.DEVNULL, grenzen=GRENZEN)
    w = wacht(p, 60)
    uit["remux_zonder_lseek"] = {"rc": w["rc"], "stderr": staart.tekst()[-200:]}
    os.lseek(blob, 0, os.SEEK_SET)
    p, staart = start(argv, bron=blob, stdout=pijp, grenzen=GRENZEN)
    w = wacht(p, 120)
    uit["remux"] = {"rc": w["rc"], "cpu_s": w["cpu_s"], "stderr": staart.tekst()[-300:]}
    uit["positie_na_remux"] = os.lseek(blob, 0, os.SEEK_CUR)
    return uit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--wacht-s", type=float, default=90.0)
    args = ap.parse_args()
    libc = ctypes.CDLL(None, use_errno=True)
    omgeving = afscherming()
    os.umask(0o077)
    try:
        os.unlink(args.socket)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(args.socket)
    os.chmod(args.socket, 0o660)
    srv.listen(4)
    omgeving["socket"] = bestand(args.socket)
    log(f"worker: klaar op {args.socket} als uid {os.getuid()}")
    srv.settimeout(args.wacht_s)
    try:
        conn, _ = srv.accept()
    except TimeoutError:
        log("worker: geen verbinding binnen de tijd")
        print(json.dumps({"worker": omgeving, "fout": "geen verbinding"}))
        return 1
    conn.settimeout(None)
    with conn:
        vraag, fds = berichten.ontvang(conn)
        berichten.stuur(conn, {"ev": "started"})
        try:
            data = {
                "omgeving": omgeving,
                "vraag": vraag,
                "zelftest": zelftest(vraag["args"], fds, libc),
            }
            berichten.stuur(conn, {"ev": "result", "data": data})
            berichten.stuur(conn, {"ev": "done"})
        except Exception as e:  # de app hoort het, en de job ziet het in spike.json
            berichten.stuur(
                conn, {"ev": "error", "code": type(e).__name__, "detail": str(e)[:2000]}
            )
            raise
    srv.close()
    os.unlink(args.socket)
    log("worker: klaar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
