"""Hulp voor test_ocr_handdruk.py: de handdruk van de opdracht ocr, aangevallen (bouwontwerp fase
6, X1; Keuze 17, zoals tests/extract/handdruk_tekst.py voor text).

Draait als een gewone gebruiker (in de test als uid 10002 via setpriv, of als de gebruiker van
CI), nooit als root: root mag alles in /proc. Dit proces speelt de worker: het zet zichzelf op
niet-dumpbaar, start een Worker in dit proces en vraagt ocr van een PNG met een merk erin (een
tEXt-blok vóór de pixels). Een aanvaller van dezelfde uid (de AANVALLER van handdruk_tekst.py)
zoekt vanaf zijn start onafgebroken naar elk kind van dit proces, kind 1 én kind 2, en probeert
/proc/<pid>/fd, elke /proc/<pid>/fd/N en /proc/<pid>/mem te openen; wat hij opent, leest hij,
en hij kijkt of het merk erin staat.

Kind 2 (nice, ionice, env en tesseract) loopt hier via kopieën die niemand mag lezen (0111),
zoals in het image (chmod 0711 in het Dockerfile): elke exec is dan niet-dumpbaar, dus ook de
herkenning (met de PGM op stdin) is dicht. De tesseract is een kopie van /bin/true: geen woorden
(none), maar wel de hele keten, met de maat van de PGM in de samenvatting. In het image
(infra/single/tests/test_ocr_container.sh, als media) mag deze uid de vier niet lezen: dan de
echte keten, met de echte tesseract (ook none: in het verloop staan geen woorden). "keten" in de
uitvoer zegt welke.

Vormen, alle via de echte opdracht (ops/ocr.py, run.spawn_handshake):
- "leesbaar, traag": kind 1 uit deze python (leesbaar) en een BOOT die eerst 0,4 s slaapt, vóór
  PR_SET_DUMPABLE. Hier moet de aanvaller iets openen (anders bewijst de test niets), maar nooit
  de blob: die komt pas na "klaar";
- "kopie, traag": hetzelfde uit een kopie van python die niemand mag lezen (0111);
- "kopie": de echte BOOT uit de kopie;
- "python-tekst, traag" en "python-tekst": alleen als naast deze python een python-tekst staat
  die deze uid niet mag lezen (in het image).

    python -I handdruk_ocr.py API-MAP [--kopie-map MAP]

Uitvoer: JSON op stdout, per vorm wat de aanvaller zag en wat de opdracht zei.
"""

from __future__ import annotations

import asyncio
import ctypes
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SIZE = (300, 200)
KETEN = ("nice", "ionice", "env", "tesseract")


def echte_keten() -> bool:
    """In het image: nice, ionice, env en tesseract zijn er, en deze uid mag ze niet lezen."""
    paden = [shutil.which(naam, path="/usr/bin:/bin") for naam in KETEN]
    return all(p and os.access(p, os.X_OK) and not os.access(p, os.R_OK) for p in paden)


def keten(map_: Path) -> Path:
    """nice, ionice, env en een tesseract (/bin/true) als kopieën die niemand mag lezen."""
    bin_ = map_ / "keten"
    bin_.mkdir()
    for naam, bron in (("nice", "nice"), ("ionice", "ionice"), ("env", "env"),
                       ("tesseract", "true")):  # fmt: skip
        echt = shutil.which(bron, path="/usr/bin:/bin")
        assert echt, bron
        doel = bin_ / naam
        shutil.copyfile(echt, doel)
        doel.chmod(0o111)
    return bin_


def png(merk: str) -> bytes:
    from PIL import Image, PngImagePlugin

    w, h = SIZE
    img = Image.frombytes("L", SIZE, bytes(x * 255 // (w - 1) for x in range(w)) * h)
    info = PngImagePlugin.PngInfo()
    info.add_text("merk", f"BLOB {merk}")
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


async def vorm(map_: Path, naam: str, python: str, traag: bool) -> dict:
    from app.media import client
    from app.media.wire import Op
    from app.media.worker.args import OcrArgs
    from app.media.worker.ops import ocr as ops_ocr
    from app.media.worker.ops import text as ops_text
    from app.media.worker.server import Config, Worker
    from tests.extract.handdruk_tekst import AANVALLER

    merk = os.urandom(8).hex()
    stop = map_ / f"stop-{naam.replace(' ', '-').replace(',', '')}"
    boot = ops_ocr.BOOT
    ops_text.python = lambda _sandbox: python
    ops_ocr.BOOT = ("import time\ntime.sleep(0.4)\n" + boot) if traag else boot
    key = "de-sleutel-van-de-ocr-handdruk-" * 2
    sock = map_ / f"w{len(naam)}{int(traag)}.sock"
    worker = Worker(Config(socket=sock, max_procs=4, max_transcodes=1, key=key), {
        "protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off",
    })  # fmt: skip
    await worker.start()
    client.bind(sock, key)
    pad = map_ / f"blob-{naam[0]}{int(traag)}.png"
    pad.write_bytes(png(merk))
    pad.chmod(0o600)
    aanvaller = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", AANVALLER, str(os.getpid()), str(stop), merk,
        stdout=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        assert (await asyncio.wait_for(aanvaller.stdout.readline(), 10)).strip() == b"klaar"
        job, _ = await client.call(Op.OCR, OcrArgs(kind="image"), source=pad)
        async with job:
            summary = await job.result()
    finally:
        stop.touch()
        uit = json.loads(await asyncio.wait_for(aanvaller.stdout.readline(), 30))
        await aanvaller.wait()
        await worker.stop()
        client.bind(None)
        ops_ocr.BOOT = boot
    uit["samenvatting"] = {k: summary.get(k) for k in ("state", "code", "error", "width",
                                                       "height", "osd")}  # fmt: skip
    return uit


def main() -> int:
    api = Path(sys.argv[1])
    basis = sys.argv[sys.argv.index("--kopie-map") + 1] if "--kopie-map" in sys.argv else None
    sys.path.insert(0, str(api))
    if os.geteuid() == 0:
        print(json.dumps({"fout": "niet als root: root mag alles in /proc"}))
        return 1
    from app.media.worker.ops import text as ops_text
    from tests.extract.handdruk_tekst import kopie, ptrace_scope

    ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)  # zoals de worker zelf
    map_ = Path(tempfile.mkdtemp(prefix="handdruk-ocr-", dir=basis))
    try:
        echt_ = echte_keten()
        if not echt_:
            os.environ["PATH"] = str(keten(map_))  # de kinderen zien alleen de onleesbare keten
        kopie_ = kopie(map_)
        uit: dict = {"uid": os.getuid(), "ptrace_scope": ptrace_scope()}
        uit["keten"] = "echt" if echt_ else "kopie"
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
            uit[naam] = asyncio.run(vorm(map_, naam, python, traag))
    finally:
        shutil.rmtree(map_, ignore_errors=True)
    print(json.dumps(uit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
