"""text: de tekst uit documenten voor zoeken (bouwontwerp fase 6, Z2; Keuze 17). Geen ffmpeg.

Eén kind per batch van hooguit 16 blobs: `python -I -c <BOOT> <api-map> text` (BOOT zet de map
van de API in sys.path, zoals local.BOOT, en roept app.render.text_child aan). Het kind start
via run.spawn_handshake met alleen een socket op fd 3, RLIMIT_DATA 768 MB, RLIMIT_CPU 60 s,
RLIMIT_FSIZE 0, RLIMIT_NOFILE 256, nice 19, de idle IO-klasse, een eigen procesgroep en
PDEATHSIG. Na zijn "klaar" (dan is het niet-dumpbaar) krijgt het de vraag en de fd's: de blobs
(ctx.inputs) en de pipe naar de app (ctx.outputs[0]). Het schrijft per blob een uitkomst op die
pipe; de worker leest hem niet.

In het image is python voor dit kind /opt/venv/bin/python-tekst: een kopie die niemand mag lezen
(0711, root; de regel in de Dockerfile), zodat het kind vanaf zijn exec niet dumpbaar is (spike
S11). Zonder die kopie (lokaal, in tests) is het sys.executable, met alleen de handdruk.

Antwoord: {"rc": exitcode van het kind (−24: SIGXCPU, RLIMIT_CPU), "done": hoeveel uitkomsten
het kind zegt te hebben geschreven, "error": de soort fout of None, "cpu_s"}. Welke blob klaar
is, weet de app uit de pipe; bij een crash halveert de app de rest (app/extract/step.py).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.media.worker import run as R
from app.media.worker.args import TextArgs
from app.media.worker.server import Context

MEMORY_MB = 768
API_ROOT = str(Path(__file__).resolve().parents[4])
# Eerst niet-dumpbaar (prctl PR_SET_DUMPABLE 0), nog vóór de import van app.render: zo kort
# mogelijk een venster als python leesbaar is (zonder de kopie); text_child zet het nog eens
BOOT = (
    "import ctypes,sys\n"
    "if ctypes.CDLL(None).prctl(4,0,0,0,0):sys.exit(3)\n"
    "sys.path.insert(0,sys.argv.pop(1))\n"
    "from app.render import text_child\n"
    "sys.exit(text_child())"
)
COPY_NAME = "python-tekst"


def python() -> str:
    """De python voor het kind: de kopie die niemand mag lezen als die er is."""
    copy = Path(sys.executable).with_name(COPY_NAME)
    if os.access(copy, os.X_OK) and copy.is_file():
        return str(copy)
    return sys.executable


async def run(ctx: Context) -> dict:
    args = ctx.args
    assert isinstance(args, TextArgs)
    child, sock = R.spawn_handshake(
        [python(), "-I", "-c", BOOT, API_ROOT, "text"],
        memory_mb=MEMORY_MB,
        cpu_s=args.child_cpu_s,
    )
    ctx.children.append(child)
    try:
        request = {
            "items": [{"kind": item.kind} for item in args.items],
            "max_chars": args.max_chars,
            "blob_cpu_s": args.blob_cpu_s,
            "batch_cpu_s": args.batch_cpu_s,
        }
        try:
            await R.handshake(sock, [*ctx.inputs, ctx.outputs[0]], json.dumps(request).encode())
        except (R.ChildError, TimeoutError, OSError) as exc:
            await child.stop()
            done = await child.wait()
            return {"rc": done.rc, "done": 0, "error": f"handdruk: {exc}"[:200], "cpu_s": 0.0}
        # De app ziet EOF op de pipe als de server na het antwoord ook zijn kant sluit
        done = await child.wait()
        said = await R.report(sock)
    finally:
        sock.close()
    error = None
    count = 0
    try:
        answer = json.loads(said) if said else {}
        count = int(answer.get("done", 0))
        error = answer.get("error")
    except (ValueError, TypeError, AttributeError):
        error = "onleesbaar antwoord"
    return {
        "rc": done.rc,
        "done": count,
        "error": str(error)[:200] if error else None,
        "cpu_s": done.cpu_s,
    }
