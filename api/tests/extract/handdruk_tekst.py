"""Hulp voor test_media_text_handshake.py: de handdruk van de opdracht text, aangevallen.

Draait als een gewone gebruiker (in de test als uid 10002 via setpriv, of als de gebruiker van
CI), nooit als root: root mag alles in /proc. Dit proces speelt de worker: het zet zichzelf op
niet-dumpbaar (zoals __main__._not_dumpable), start een Worker in dit proces en vraagt een
batch text met twee blobs. Een aanvaller van dezelfde uid (een eigen proces, zoals een gekraakte
ffmpeg) zoekt vanaf zijn start onafgebroken naar elk kind van dit proces en probeert
/proc/<pid>/fd te lezen en elke /proc/<pid>/fd/N te openen; wat hij opent, leest hij en hij kijkt
of het merk van een blob erin staat.

Naast de fd's opent de aanvaller ook /proc/<pid>/mem (review golf 1, F2): wie dat opent vóór
het kind zichzelf niet-dumpbaar maakt, houdt die toegang daarna, als de blobs er zijn. Hij telt
een mem alleen als hij er ook echt iets uit kan lezen (op het eerste adres uit maps), en houdt
hem open; ziet hij het kind daarna dicht (EACCES op /proc/<pid>/fd), dan probeert hij de oude
mem opnieuw ("mem na dicht"). Met Yama (ptrace_scope ≥ 1, zoals op Ubuntu) mag een proces dat
geen ouder is mem nooit openen; ptrace_scope staat in de uitvoer.

Vormen, alle via de echte opdracht (ops/text.py, run.spawn_handshake):
- "leesbaar, traag": het kind uit deze python (leesbaar) en een BOOT die eerst 0,4 s slaapt,
  vóór PR_SET_DUMPABLE: het venster dat er zonder kopie is. Hier moet de aanvaller iets openen
  (anders bewijst de test niets), maar nooit een blob via een fd: die komen pas na "klaar";
- "kopie, traag": hetzelfde, maar uit een kopie van python die niemand mag lezen (0111, zoals
  python-tekst 0711 in het image): vanaf de exec niet dumpbaar, dus ook in die 0,4 s EACCES;
- "kopie": de echte BOOT uit de kopie;
- "python-tekst, traag" en "python-tekst": alleen als naast deze python een python-tekst staat
  die deze uid niet mag lezen (in het image: /opt/venv/bin/python-tekst, 0711 van root). Dan
  zijn het de vorige twee met de echte kopie van het image (test_media_container.sh).

    python -I handdruk_tekst.py API-MAP [--kopie-map MAP]

--kopie-map: waar de werkmap komt (in de container /run/media: /tmp is daar noexec).

Uitvoer: JSON op stdout, per vorm wat de aanvaller zag en of de tekst klopte.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shutil
import sys
import sysconfig
import tempfile
from pathlib import Path

AANVALLER = r"""
import errno, json, os, sys, time
ouder, stop, merk = sys.argv[1], sys.argv[2], sys.argv[3].encode()
mij = str(os.getpid())
gezien, pogingen, geopend, fouten = {}, 0, [], {}
mem, mem_geopend, mem_na = {}, [], set()


def fout_tel(e, ook=("ENOENT",)):
    fout = errno.errorcode.get(e.errno, str(e.errno))
    if fout not in ook:
        fouten[fout] = fouten.get(fout, 0) + 1


def mem_proberen(naam, ms):
    try:
        fd = os.open(f"/proc/{naam}/mem", os.O_RDONLY)
    except OSError as e:
        fout_tel(e, ("ENOENT", "ESRCH"))
        return
    try:
        with open(f"/proc/{naam}/maps") as f:
            begin = int(f.readline().split("-", 1)[0], 16)
        data = os.pread(fd, 16, begin)
    except (OSError, ValueError, IndexError):
        data = b""
    if data:
        mem[naam] = (fd, begin)
        mem_geopend.append({"pid": int(naam), "ms": ms})
    else:
        os.close(fd)


print("klaar", flush=True)
while not os.path.exists(stop):
    for naam in os.listdir("/proc"):
        if not naam.isdigit() or naam == mij:
            continue
        try:
            with open(f"/proc/{naam}/stat") as f:
                ppid = f.read().rsplit(")", 1)[1].split()[1]
        except (OSError, IndexError):
            continue
        if ppid != ouder:
            continue
        t = time.monotonic()
        eerst = gezien.setdefault(naam, t)
        ms = round((t - eerst) * 1000, 2)
        pogingen += 1
        if naam not in mem:
            mem_proberen(naam, ms)
        try:
            fds = os.listdir(f"/proc/{naam}/fd")
        except OSError as e:
            fds = []
            fout = errno.errorcode.get(e.errno, str(e.errno))
            if fout == "ENOENT" and not os.path.exists(f"/proc/{naam}"):
                # Het kind was al klaar en weg tussen stat en fd: geen uitkomst (zoals ENOENT bij
                # mem en bij de fd's). Bestaat het nog, dan telt ENOENT wel als fout
                continue
            fouten[fout] = fouten.get(fout, 0) + 1
            if fout == "EACCES" and naam in mem and naam not in mem_na:
                fd, begin = mem[naam]
                try:
                    if os.pread(fd, 16, begin):
                        mem_na.add(naam)
                except OSError:
                    pass
        for n in sorted({*range(0, 8), *(int(x) for x in fds if x.isdigit())}):
            try:
                fd = os.open(f"/proc/{naam}/fd/{n}", os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                fout = errno.errorcode.get(e.errno, str(e.errno))
                if fout != "ENOENT":
                    fouten[fout] = fouten.get(fout, 0) + 1
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            finally:
                os.close(fd)
            geopend.append({"pid": int(naam), "fd": n, "ms": ms, "blob": merk in data})
print(json.dumps({
    "kinderen": len(gezien), "pogingen": pogingen, "geopend": geopend[:50],
    "aantal geopend": len(geopend), "fouten": fouten,
    "mem geopend": len(mem_geopend), "mem": mem_geopend[:10], "mem na dicht": len(mem_na),
}), flush=True)
"""


def kopie(map_: Path) -> str:
    """Een mini-venv met een kopie van deze python die niemand mag lezen (0111): de
    standaardbibliotheek via home in pyvenv.cfg, de pakketten via site-packages van nu."""
    echt = os.path.realpath(sys.executable)
    bin_ = map_ / "venv" / "bin"
    bin_.mkdir(parents=True)
    doel = bin_ / "python-tekst"
    shutil.copyfile(echt, doel)
    doel.chmod(0o111)
    (map_ / "venv" / "pyvenv.cfg").write_text(f"home = {os.path.dirname(echt)}\n")
    lib = map_ / "venv" / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    lib.mkdir(parents=True)
    (lib / "site-packages").symlink_to(sysconfig.get_paths()["purelib"])
    return str(doel)


async def vorm(api: Path, map_: Path, naam: str, python: str, traag: bool) -> dict:
    from app.extract import batch
    from app.media import client
    from app.media.worker.ops import text as ops_text
    from app.media.worker.server import Config, Worker

    merk = os.urandom(8).hex()
    stop = map_ / f"stop-{naam.replace(' ', '-').replace(',', '')}"
    boot = ops_text.BOOT
    ops_text.python = lambda _sandbox: python
    ops_text.BOOT = ("import time\ntime.sleep(0.4)\n" + boot) if traag else boot
    key = "de-sleutel-van-de-handdruk-" * 2
    sock = map_ / f"w{len(naam)}{int(traag)}.sock"
    worker = Worker(Config(socket=sock, max_procs=4, max_transcodes=1, key=key), {
        "protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off",
    })  # fmt: skip
    await worker.start()
    client.bind(sock, key)
    paden = []
    for i in range(2):
        pad = map_ / f"blob-{naam[0]}{int(traag)}-{i}.txt"
        pad.write_text(f"BLOB {merk} nummer {i}")
        pad.chmod(0o600)
        paden.append(pad)
    aanvaller = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", AANVALLER, str(os.getpid()), str(stop), merk,
        stdout=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        assert (await asyncio.wait_for(aanvaller.stdout.readline(), 10)).strip() == b"klaar"
        fds = [batch.open_blob(p) for p in paden]
        try:
            ran = await batch.via_worker(fds, ["plain", "plain"], max_chars=1000)
        finally:
            for fd in fds:
                os.close(fd)
    finally:
        stop.touch()
        uit = json.loads(await asyncio.wait_for(aanvaller.stdout.readline(), 30))
        await aanvaller.wait()
        await worker.stop()
        client.bind(None)
        ops_text.BOOT = boot
    uit["tekst klopt"] = [f.text for f in ran.found] == [
        f"BLOB {merk} nummer {i}" for i in range(2)
    ]
    uit["rc"] = ran.rc
    return uit


def ptrace_scope() -> int | None:
    try:
        return int(Path("/proc/sys/kernel/yama/ptrace_scope").read_text())
    except (OSError, ValueError):
        return None


def main() -> int:
    api = Path(sys.argv[1])
    basis = sys.argv[sys.argv.index("--kopie-map") + 1] if "--kopie-map" in sys.argv else None
    sys.path.insert(0, str(api))
    if os.geteuid() == 0:
        print(json.dumps({"fout": "niet als root: root mag alles in /proc"}))
        return 1
    from app.media.worker.ops import text as ops_text

    ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)  # zoals de worker zelf
    map_ = Path(tempfile.mkdtemp(prefix="handdruk-tekst-", dir=basis))
    try:
        kopie_ = kopie(map_)
        uit: dict = {"uid": os.getuid(), "ptrace_scope": ptrace_scope()}
        vormen = [
            ("leesbaar, traag", sys.executable, True),
            ("kopie, traag", kopie_, True),
            ("kopie", kopie_, False),
        ]
        echt = ops_text.copy_path()
        uit["python-tekst"] = str(echt) if ops_text.unreadable(echt) else None
        if uit["python-tekst"]:
            vormen += [("python-tekst, traag", str(echt), True), ("python-tekst", str(echt), False)]
        for naam, python, traag in vormen:
            uit[naam] = asyncio.run(vorm(api, map_, naam, python, traag))
    finally:
        shutil.rmtree(map_, ignore_errors=True)
    print(json.dumps(uit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
