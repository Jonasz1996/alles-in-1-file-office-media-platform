"""Hulp voor test_media_text_handshake.py: de handdruk van de opdracht text, aangevallen.

Draait als een gewone gebruiker (in de test als uid 10002 via setpriv, of als de gebruiker van
CI), nooit als root: root mag alles in /proc. Dit proces speelt de worker: het zet zichzelf op
niet-dumpbaar (zoals __main__._not_dumpable), start een Worker in dit proces en vraagt een
batch text met twee blobs. Een aanvaller van dezelfde uid (een eigen proces, zoals een gekraakte
ffmpeg) zoekt vanaf zijn start onafgebroken naar elk kind van dit proces en probeert
/proc/<pid>/fd te lezen en elke /proc/<pid>/fd/N te openen; wat hij opent, leest hij en hij kijkt
of het merk van een blob erin staat.

Drie vormen, alle drie via de echte opdracht (ops/text.py, run.spawn_handshake):
- "leesbaar, traag": het kind uit deze python (leesbaar) en een BOOT die eerst 0,4 s slaapt,
  vóór PR_SET_DUMPABLE: het venster dat er zonder kopie is. Hier moet de aanvaller iets openen
  (anders bewijst de test niets), maar nooit een blob: die komen pas na "klaar";
- "kopie, traag": hetzelfde, maar uit een kopie van python die niemand mag lezen (0111, zoals
  python-tekst 0711 in het image): vanaf de exec niet dumpbaar, dus ook in die 0,4 s EACCES;
- "kopie": de echte BOOT uit de kopie.

    python -I handdruk_tekst.py API-MAP

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
        try:
            fds = os.listdir(f"/proc/{naam}/fd")
        except OSError as e:
            fds = []
            fout = errno.errorcode.get(e.errno, str(e.errno))
            fouten[fout] = fouten.get(fout, 0) + 1
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
    ops_text.python = lambda: python
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


def main() -> int:
    api = Path(sys.argv[1])
    sys.path.insert(0, str(api))
    if os.geteuid() == 0:
        print(json.dumps({"fout": "niet als root: root mag alles in /proc"}))
        return 1
    ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)  # zoals de worker zelf
    map_ = Path(tempfile.mkdtemp(prefix="handdruk-tekst-"))
    try:
        kopie_ = kopie(map_)
        uit: dict = {"uid": os.getuid()}
        for naam, python, traag in (
            ("leesbaar, traag", sys.executable, True),
            ("kopie, traag", kopie_, True),
            ("kopie", kopie_, False),
        ):
            uit[naam] = asyncio.run(vorm(api, map_, naam, python, traag))
    finally:
        shutil.rmtree(map_, ignore_errors=True)
    print(json.dumps(uit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
