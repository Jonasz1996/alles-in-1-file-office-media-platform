"""S3, de kant van de app (bouwontwerp fase 5, §3.7 en Keuze 11).

Draait als `platform` (uid 10001) in hetzelfde afgeleide image. Maakt een blob in `/data/blobs`
(modus 0600, zoals `mkstemp`), opent hem alleen lezen, maakt een pipe, en stuurt de blob en de
schrijfkant van de pipe als SCM_RIGHTS naar de worker, over de socket in het benoemde volume.
Daarna leest hij de pipe (de fMP4 van de remux in de worker) en de antwoorden, kijkt of de
blob gelijk bleef, en print alles als JSON op stdout.

    python3 -I s3_app.py --socket /run/media/worker.sock [--wacht-s 60]
"""

from __future__ import annotations

import argparse
import errno
import grp
import hashlib
import json
import os
import pwd
import socket
import stat
import sys
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import berichten  # noqa: E402
import boxen  # noqa: E402
from gedeeld import draai, log  # noqa: E402

STANDIN = os.environ.get("SPIKE_FFMPEG_STANDIN") == "1"
BLOBS = os.environ.get("SPIKE_BLOBS", "/data/blobs")  # een andere map: alleen een proef op de host


def fout(e: OSError) -> str:
    return f"{type(e).__name__} ({errno.errorcode.get(e.errno, e.errno)})"


def bestand(pad: str) -> dict:
    try:
        st = os.stat(pad)
    except OSError as e:
        return {"fout": fout(e)}
    try:
        naam = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        naam = None
    return {
        "modus": f"{stat.S_IMODE(st.st_mode):04o}",
        "uid": st.st_uid,
        "gid": st.st_gid,
        "groep": naam,
    }


def sha(pad: str) -> str:
    h = hashlib.sha256()
    with open(pad, "rb") as f:
        for stuk in iter(lambda: f.read(1 << 20), b""):
            h.update(stuk)
    return h.hexdigest()


def maak_blob() -> tuple[str, dict]:
    """Een blob zoals de app hem heeft: naar zijn sha genoemd, 0600, van 10001."""
    os.makedirs(BLOBS, mode=0o700, exist_ok=True)
    tijdelijk = os.path.join(BLOBS, "nieuw.mkv")
    info: dict = {}
    if STANDIN:
        with open(tijdelijk, "wb") as f:
            f.write(os.urandom(2 * 1024 * 1024))
        info["soort"] = "willekeurige bytes (stand-in zonder ffmpeg)"
    else:
        r = draai(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "6",
                "-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", tijdelijk,
            ],
            deadline_s=60,
        )  # fmt: skip
        info["soort"] = "mkv uit lavfi (H.264, AAC, 6 s)"
        info["maken"] = {"rc": r["rc"], "stderr": r["stderr"][-300:]}
    os.chmod(tijdelijk, 0o600)
    pad = os.path.join(BLOBS, sha(tijdelijk))
    os.replace(tijdelijk, pad)
    info.update(bestand(pad))
    info["grootte"] = os.path.getsize(pad)
    return pad, info


def lees_pijp(fd: int, buf: bytearray, klaar: threading.Event) -> None:
    try:
        while True:
            stuk = os.read(fd, 1 << 16)
            if not stuk or len(buf) > 64 * 1024 * 1024:
                break
            buf += stuk
    finally:
        os.close(fd)
        klaar.set()


def fmp4(data: bytes) -> dict:
    """Wat er door de pipe kwam: de volgorde van de boxen, de sporen en het eerste fragment."""
    splitser = boxen.Splitser()
    stukken = splitser.voeg(data)
    volgorde = [s for s, _, _ in stukken]
    sporen: dict = {}
    eerste = None
    moof = None
    for soort, pos, box in stukken:
        if soort == "moov":
            sporen = boxen.lees_init(box)
        elif soort == "moof":
            moof = (pos, box)
        elif soort == "mdat" and moof and eerste is None:
            eerste = boxen.ontleed(boxen.lees_moof(moof[1], moof[0], sporen), box, pos, sporen)
    return {
        "bytes": len(data),
        "volgorde": volgorde[:4],
        "moof": volgorde.count("moof"),
        "mdat": volgorde.count("mdat"),
        "sporen": [s.info() for s in sporen.values()],
        "eerste_fragment": eerste,
        "kapot": splitser.kapot,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--wacht-s", type=float, default=60.0)
    args = ap.parse_args()
    uit: dict = {
        "app": {
            "uid": os.getuid(),
            "gid": os.getgid(),
            "groepen": os.getgroups(),
            "naam": pwd.getpwuid(os.getuid()).pw_name,
        },
        "standin": STANDIN,
    }
    pad, uit["blob"] = maak_blob()
    sha_voor = sha(pad)
    grootte = os.path.getsize(pad)
    offset, lengte = grootte // 2, min(256 * 1024, grootte // 4)
    with open(pad, "rb") as f:
        f.seek(offset)
        verwacht = hashlib.sha256(f.read(lengte)).hexdigest()
    eind = time.monotonic() + args.wacht_s
    while not os.path.exists(args.socket) and time.monotonic() < eind:
        time.sleep(0.2)
    uit["socket"] = bestand(args.socket)
    uit["run_media"] = bestand(os.path.dirname(args.socket))
    try:
        open(os.path.join(os.path.dirname(args.socket), "app-schrijft"), "w").close()
        uit["app_maakt_in_run_media"] = "gelukt (FOUT)"
    except OSError as e:
        uit["app_maakt_in_run_media"] = fout(e)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(args.socket)
        uit["verbinden"] = "ok"
    except OSError as e:
        uit["verbinden"] = fout(e)
        print(json.dumps(uit))
        return 0
    fd = os.open(pad, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    r, w = os.pipe()
    vraag = {
        "v": 1,
        "op": "selftest",
        "args": {"offset": offset, "lengte": lengte, "demuxer": "matroska"},
    }
    berichten.stuur(sock, vraag, [fd, w])
    # de app houdt geen kopie van de schrijfkant: zo geeft de pipe EOF als ffmpeg klaar is (D7)
    os.close(w)
    os.close(fd)
    buf = bytearray()
    klaar = threading.Event()
    threading.Thread(target=lees_pijp, args=(r, buf, klaar), daemon=True).start()
    events = []
    resultaat = None
    t0 = time.monotonic()
    while True:
        try:
            bericht, extra = berichten.ontvang(sock)
        except EOFError:
            events.append("eof")
            break
        for x in extra:
            os.close(x)
        events.append(bericht.get("ev"))
        if bericht.get("ev") == "result":
            resultaat = bericht.get("data")
        if bericht.get("ev") in ("done", "error"):
            if bericht.get("ev") == "error":
                uit["worker_fout"] = bericht
            break
    sock.close()
    klaar.wait(60)
    uit["duur_s"] = round(time.monotonic() - t0, 2)
    uit["events"] = events
    uit["worker"] = resultaat
    uit["sha_stuk_verwacht"] = verwacht
    uit["blob_gelijk"] = sha(pad) == sha_voor
    uit["fmp4"] = fmp4(bytes(buf)) if buf else {"bytes": 0}
    log(f"app: klaar, events {events}")
    print(json.dumps(uit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
