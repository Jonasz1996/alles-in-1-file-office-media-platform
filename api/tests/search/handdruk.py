"""Spike S11 (bouwontwerp fase 6, §3.7; Keuze 17): de handdruk van het tekst-kind, bewezen.

Een kind uit een leesbaar programma (python) is dumpbaar tot het zelf PR_SET_DUMPABLE 0 zet. Een
ander proces met dezelfde uid (een gekraakte ffmpeg in de worker, uid 10002) kan in die tijd via
/proc/<pid>/fd/N de bestanden van dat kind openen. Daarom krijgt het kind bij de start alleen een
socket op fd 3, zet het zichzelf op niet-dumpbaar, meldt "klaar", en krijgt dan pas de fd's van
de blobs via SCM_RIGHTS.

Dit script speelt de worker na:
- de blobs zijn bestanden zonder pad (aangemaakt en meteen weg), zoals in de worker: daar zijn ze
  van uid 10001 en alleen via een fd te lezen;
- het kind start via app.media.worker.run.spawn (de grenzen, PDEATHSIG, een eigen procesgroep en
  een kale omgeving van de worker), als `python -I -c KIND`;
- een aanvaller (een ander proces met dezelfde uid, geen kind van het kind) probeert vóór en na
  "klaar" elk /proc/<pid>/fd/N van het kind te openen en te lezen, en ook /proc/<pid>/mem; wat hij
  vóór "klaar" kon openen houdt hij vast en leest hij na afloop nog eens.

Testhaak: het kind wacht vóór PR_SET_DUMPABLE op "ga", zodat de aanvaller het venster zeker ziet.
Het echte kind (Z2, `python -I -m app.render text`) zet het als eerste.

    python -I handdruk.py [--srv PAD]            # PAD: waar het pakket app staat
    docker compose exec -T media python -I - --srv /srv < handdruk.py

Uitvoer: JSON op stdout. Exitcode 0 als geen enkele poging de inhoud van een blob gaf en na
"klaar" alles EACCES of weg was; anders 1. Nooit als root draaien: root mag alles in /proc.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

KIND = r"""
import ctypes, hashlib, os, socket, sys
s = socket.socket(fileno=3)
if s.recv(2) != b"ga":
    sys.exit(4)
if ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE 0
    sys.exit(5)
s.sendall(b"klaar")
if sys.argv[1] == "vork":
    # Het werk in een kleinkind: een nieuwe mm, en net als zijn ouder nooit dumpbaar. Wie vóór
    # "klaar" /proc/<kind>/mem opende, ziet alleen het geheugen van het kind
    kleinkind = os.fork()
    if kleinkind:
        _, status = os.waitpid(kleinkind, 0)
        sys.exit(os.waitstatus_to_exitcode(status))
_, fds, _, _ = socket.recv_fds(s, 16, 16)
uit = []
for fd in fds:
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        stuk = os.read(fd, 65536)
        if not stuk:
            break
        h.update(stuk)
    uit.append(h.hexdigest())
s.sendall((" ".join(uit) + "\n").encode())
s.recv(4)  # "stop": tot dan houdt het kind de fd's open
"""

AANVALLER = r"""
import ctypes, errno, json, os, sys
pid, merk = sys.argv[1], bytes.fromhex(sys.argv[2])
vast = {}
mem_vast, gebieden = None, []
libc = ctypes.CDLL(None, use_errno=True)

def ptrace():
    # Aanhaken lukt? Dan meteen weer los (SEIZE, INTERRUPT, wachten, DETACH): wie aangehaakt
    # blijft, stuurt het kind met zijn registers, en daar kijkt de kernel niet naar dumpbaar
    p = int(pid)
    if libc.ptrace(0x4206, p, 0, 0) != 0:  # PTRACE_SEIZE
        return errno.errorcode.get(ctypes.get_errno(), "?")
    libc.ptrace(0x4207, p, 0, 0)  # PTRACE_INTERRUPT
    os.waitpid(p, 0x40000000)  # __WALL
    libc.ptrace(17, p, 0, 0)  # PTRACE_DETACH
    return "gelukt"

def poging(pad):
    try:
        fd = os.open(pad, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        return {"uitkomst": errno.errorcode.get(e.errno, str(e.errno))}, None
    try:
        data = os.read(fd, 4096)
    except OSError as e:
        data = None
        gelezen = errno.errorcode.get(e.errno, str(e.errno))
    else:
        gelezen = len(data)
    return {"uitkomst": "geopend", "gelezen": gelezen}, (fd, data)

for regel in sys.stdin:
    ronde = regel.strip()
    if ronde == "lees":
        uit = []
        if mem_vast is not None:
            # Het geheugen van het kind via de mem-fd van vóór "klaar": elk schrijfbaar gebied
            # uit maps van toen, en verder tot het eerste gat (de heap groeit)
            data = bytearray()
            for lo, hi in gebieden:
                adres = lo
                while adres < hi + (64 << 20) and len(data) < (512 << 20):
                    try:
                        stuk = os.pread(mem_vast, 1 << 20, adres)
                    except OSError:
                        break
                    if not stuk:
                        break
                    data += stuk
                    adres += len(stuk)
            uit.append({"fd": "mem", "gelezen": len(data), "blob": merk in data})
        for n, fd in sorted(vast.items()):
            try:
                data = os.read(fd, 4096)
                uit.append({"fd": n, "gelezen": len(data), "blob": merk in data})
            except OSError as e:
                uit.append({"fd": n, "gelezen": errno.errorcode.get(e.errno, str(e.errno)),
                            "blob": False})
        print(json.dumps({"ronde": ronde, "vastgehouden": uit}), flush=True)
        continue
    try:
        namen = sorted(int(x) for x in os.listdir(f"/proc/{pid}/fd"))
        lijst = "ja"
    except OSError as e:
        namen, lijst = [], errno.errorcode.get(e.errno, str(e.errno))
    uit = []
    for n in sorted(set(namen) | set(range(0, 24))):
        pad = f"/proc/{pid}/fd/{n}"
        try:
            doel = os.readlink(pad)
            if doel.startswith(("pipe:", "socket:")):
                doel = doel.split(":")[0]
        except OSError as e:
            doel = errno.errorcode.get(e.errno, str(e.errno))
        r, open_ = poging(pad)
        if open_ is not None:
            fd, data = open_
            if ronde == "voor":
                vast[n] = fd
            else:
                os.close(fd)
            r["blob"] = merk in (data or b"")
        if doel == "ENOENT" and r["uitkomst"] == "ENOENT":
            continue  # die fd heeft het kind niet
        uit.append({"fd": n, "doel": doel, **r})
    mem, open_ = poging(f"/proc/{pid}/mem")
    if open_ is not None:
        if ronde == "voor":
            mem_vast = open_[0]
            for regel_ in open(f"/proc/{pid}/maps"):
                adres, rechten = regel_.split()[:2]
                if rechten.startswith("rw"):
                    gebieden.append(tuple(int(x, 16) for x in adres.split("-")))
        else:
            os.close(open_[0])
    andere = {}
    if ronde == "na":
        # Ook elk ander proces dat er nu is (een kleinkind van het kind), behalve ikzelf
        for naam in os.listdir("/proc"):
            if not naam.isdigit() or naam in (pid, str(os.getpid())):
                continue
            try:
                fds = os.listdir(f"/proc/{naam}/fd")
            except OSError as e:
                fout = errno.errorcode.get(e.errno, str(e.errno))
                andere[fout] = andere.get(fout, 0) + 1
                continue
            for n in fds:
                r, open_ = poging(f"/proc/{naam}/fd/{n}")
                if open_ is not None:
                    os.close(open_[0])
                    r["blob"] = merk in (open_[1] or b"")
                    uit.append({"fd": f"{naam}/{n}", "doel": "ander proces", **r})
            r, open_ = poging(f"/proc/{naam}/mem")
            if open_ is not None:
                os.close(open_[0])
            andere[f"{naam}: fd's te lezen"] = f"mem: {r['uitkomst']}"
    print(json.dumps({"ronde": ronde, "lijst": lijst, "fds": uit, "mem": mem["uitkomst"],
                      "ptrace": ptrace(), "andere processen": andere}), flush=True)
"""

MERK = os.urandom(16).hex().encode()
VARIANTEN = ("ontwerp", "vork", "0711")


def _blob(grootte: int) -> int:
    """Een bestand zonder pad met een herkenbaar begin: alleen via deze fd te lezen."""
    fd, pad = tempfile.mkstemp(prefix="handdruk-")
    os.unlink(pad)
    data = b"BLOB-" + MERK + os.urandom(grootte)
    os.write(fd, data)
    return fd


def _sha(fd: int) -> str:
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while stuk := os.read(fd, 65536):
        h.update(stuk)
    return h.hexdigest()


def _inhoud(ronde: dict) -> list:
    """De fd's (of "mem") waar de aanvaller iets van een blob las."""
    rijen = ronde.get("fds", []) + ronde.get("vastgehouden", [])
    return [r["fd"] for r in rijen if r.get("blob")]


async def _vraag(aanvaller: asyncio.subprocess.Process, ronde: str) -> dict:
    """Eén ronde van de aanvaller. Hij meldt per poging alleen of het merk van een blob in wat
    hij las stond, nooit de bytes zelf."""
    aanvaller.stdin.write(ronde.encode() + b"\n")
    await aanvaller.stdin.drain()
    return json.loads(await asyncio.wait_for(aanvaller.stdout.readline(), 30))


def _onleesbaar(kopie_map: str | None) -> tuple[str, str]:
    """Een kopie van deze python die niemand mag lezen (0111), met een pyvenv.cfg ernaast zodat
    hij de standaardbibliotheek vindt. Zo is het kind vanaf zijn exec niet dumpbaar, zoals
    ffmpeg (0711 in het image). Hier van de eigen uid, voor de proef; in het image hoort hij van
    root te zijn, anders kan een proces van dezelfde uid hem weer leesbaar maken."""
    echt = os.path.realpath(sys.executable)
    map_ = tempfile.mkdtemp(prefix="handdruk-", dir=kopie_map)
    kopie = os.path.join(map_, "python-kind")
    with open(echt, "rb") as bron, open(kopie, "wb") as doel:
        while stuk := bron.read(1 << 20):
            doel.write(stuk)
    os.chmod(kopie, 0o111)
    Path(map_, "pyvenv.cfg").write_text(f"home = {os.path.dirname(echt)}\n")
    return map_, kopie


async def handdruk(srv: Path, variant: str, kopie_map: str | None = None) -> dict:
    """variant "ontwerp": het kind doet het werk zelf (Keuze 17 zoals ontworpen); "vork": het
    kind forkt na "klaar" en het kleinkind doet het werk; "0711": als "ontwerp", maar uit een
    python die niemand mag lezen."""
    if str(srv) not in sys.path:
        sys.path.insert(0, str(srv))
    from app.media.worker import run

    loop = asyncio.get_running_loop()
    python, map_ = sys.executable, None
    if variant == "0711":
        map_, python = _onleesbaar(kopie_map)
    blobs = [_blob(256 * 1024), _blob(64 * 1024)]
    shas = [_sha(fd) for fd in blobs]
    ouder, kant = socket.socketpair()
    try:
        child = run.spawn([python, "-I", "-c", KIND, variant], extra=[kant.fileno()],
                          memory_mb=768)  # fmt: skip
    except OSError as e:
        # Bijvoorbeeld /tmp met noexec (tmpfs in docker): geef --kopie-map
        for fd in blobs:
            os.close(fd)
        ouder.close()
        kant.close()
        _weg(map_)
        return {"variant": variant, "niet te proberen": f"{type(e).__name__}: {e}"}
    kant.close()
    ouder.setblocking(False)
    aanvaller = await asyncio.create_subprocess_exec(
        *[sys.executable, "-I", "-c", AANVALLER, str(child.pid), MERK.hex()],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    uit: dict = {"variant": variant, "kind": child.pid}
    try:
        uit["voor"] = await _vraag(aanvaller, "voor")
        await loop.sock_sendall(ouder, b"ga")
        klaar = await asyncio.wait_for(loop.sock_recv(ouder, 5), 10)
        uit["klaar"] = klaar == b"klaar"
        socket.send_fds(ouder, [b"blobs"], blobs)
        antwoord = b""
        while not antwoord.endswith(b"\n"):
            stuk = await asyncio.wait_for(loop.sock_recv(ouder, 4096), 10)
            if not stuk:
                break
            antwoord += stuk
        uit["kind las de blobs"] = antwoord.decode().split() == shas
        uit["na"] = await _vraag(aanvaller, "na")
        uit["vastgehouden"] = await _vraag(aanvaller, "lees")
        await loop.sock_sendall(ouder, b"stop")
        uit["exit kind"] = (await asyncio.wait_for(child.wait(), 10)).rc
    finally:
        await child.stop()
        aanvaller.stdin.close()
        await asyncio.wait_for(aanvaller.wait(), 10)
        ouder.close()
        for fd in blobs:
            os.close(fd)
        _weg(map_)
    return uit


def _weg(map_: str | None) -> None:
    if map_ is None:
        return
    for naam in os.listdir(map_):
        os.unlink(os.path.join(map_, naam))
    os.rmdir(map_)


def oordeel(uit: dict) -> dict:
    """ok: nergens de inhoud van een blob, en na "klaar" is elke fd EACCES of weg."""
    inhoud = {r: _inhoud(uit[r]) for r in ("voor", "na", "vastgehouden")}
    na = uit["na"]
    na_open = [r["fd"] for r in na["fds"] if r["uitkomst"] not in ("EACCES", "ENOENT")]
    voor_open = [f"{r['fd']} ({r['doel']})" for r in uit["voor"]["fds"]
                 if r["uitkomst"] == "geopend"]  # fmt: skip
    return {
        "blob-inhoud gezien": {r: v for r, v in inhoud.items() if v} or "nee",
        "blob-inhoud via /proc/<pid>/fd": [f for v in inhoud.values() for f in v if f != "mem"],
        "na klaar": "EACCES of weg" if not na_open else f"open: {na_open}",
        "lijst na klaar": na["lijst"],
        "mem vóór klaar": uit["voor"]["mem"],
        "mem na klaar": na["mem"],
        "ptrace vóór klaar": uit["voor"]["ptrace"],
        "ptrace na klaar": na["ptrace"],
        "vóór klaar te openen": voor_open,
        "ok": not any(inhoud.values())
        and not na_open
        and uit["klaar"]
        and uit["kind las de blobs"],
        # Geen venster: ook vóór "klaar" geen fd, geen mem en geen ptrace
        "geen venster": not voor_open
        and uit["voor"]["mem"] != "geopend"
        and uit["voor"]["ptrace"] != "gelukt",
    }


def _ptrace_scope() -> str:
    try:
        return Path("/proc/sys/kernel/yama/ptrace_scope").read_text().strip()
    except OSError:
        return "geen Yama"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    hier = Path(__file__).resolve()
    if Path("/srv/app").is_dir():
        standaard = "/srv"
    elif hier.is_file():
        standaard = str(hier.parents[2])
    else:
        standaard = None
    ap.add_argument("--srv", default=standaard, required=standaard is None,
                    help="de map met het pakket app")  # fmt: skip
    ap.add_argument("--kopie-map", help="waar de python van 0711 komt (een map zonder noexec)")
    args = ap.parse_args(argv)
    if os.geteuid() == 0:
        print(json.dumps({"ok": False, "fout": "niet als root: root mag alles in /proc"}))
        return 1
    # Zoals de worker zelf (__main__._not_dumpable): de aanvaller kan niet in de ouder kijken
    ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)
    uit: dict = {
        "uid": os.getuid(),
        "python": sys.version.split()[0],
        "ptrace_scope": _ptrace_scope(),
    }
    for variant in VARIANTEN:
        ronde = asyncio.run(handdruk(Path(args.srv), variant, args.kopie_map))
        if "niet te proberen" not in ronde:
            ronde["oordeel"] = oordeel(ronde)
        uit[variant] = ronde
    gedaan = {v: r["oordeel"] for v, r in uit.items() if v in VARIANTEN and "oordeel" in r}
    # De eis van S11 zoals hij er staat: via /proc/<pid>/fd nooit de inhoud van een blob, en na
    # "klaar" alles EACCES of weg. Dat geldt voor elke vorm.
    uit["S11"] = all(not o["blob-inhoud via /proc/<pid>/fd"] for o in gedaan.values()) and all(
        o["na klaar"] == "EACCES of weg" for o in gedaan.values()
    )
    # Wat de spike er verder van leert: welke vormen ook zonder Yama (ptrace_scope 0 of geen
    # Yama) niets lekken, en welke geen venster hebben
    uit["lekt niet"] = sorted(v for v, o in gedaan.items() if o["ok"])
    uit["geen venster"] = sorted(v for v, o in gedaan.items() if o["geen venster"])
    uit["ok"] = uit["S11"] and "ontwerp" in gedaan and "vork" in gedaan
    print(json.dumps(uit, indent=2, ensure_ascii=False))
    return 0 if uit["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
