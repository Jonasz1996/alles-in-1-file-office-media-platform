"""De proef van laag 2: een kind met extra gedrag, alleen in de tests (fase 6, X2a).

server.Config.child_code = BOOT start een kind dat eerst child.HANDLERS aanvult en dan
child.main draait, precies zoals een echt kind (dumpable 0 vóór de import van de app, dezelfde
grenzen, dezelfde fd's). Wat de proef doet, kiest de test met de eerste bytes van de preview
(faces, in werk) of met de tekst (embed_text, in zoeken):

- WACHT: blijft lopen tot pid 1 de opdracht stopt (cancel, deadline), dan Stopped;
- VROEG: als WACHT, maar het kind begint pas aan de opdracht (StopFlag.begin) nadat de draad van
  fd 4 de stop al las: de stop komt vóór begin aan (de race van de review, veilig F1);
- NEGEER: blijft lopen en luistert niet naar stop (dan SIGKILL na KILL_GRACE_S);
- VREET-SHM <MB>: gedeeld geheugen (mmap, MAP_SHARED) dat RLIMIT_DATA niet telt: de wachter moet
  het kind stoppen (K1-07);
- VREET-MALLOC <MB>: gewoon geheugen boven RLIMIT_DATA: MemoryError, too_big, en het kind leeft;
- FOUT: een uitzondering met de merkvector en GEHEIM in de tekst (K1-16);
- STDERR: dezelfde tekst rechtstreeks op stderr, en dan een crash;
- anders: de echte faces.

embed_text (zoeken): de tekst GEHEIM geeft een uitzondering met die tekst; anders de omgeving,
sys.path en of PIL en onnxruntime geladen zijn (zoeken decodeert nooit een beeld, Keuze 75).
rank en forget blijven de echte.
"""

from __future__ import annotations

import mmap
import os
import sys
import threading
import time

from app.analyse.worker import child

GEHEIM = "geheimwoord7"
# De merkvector: 128 waarden die nergens anders voorkomen (K1-16, test_faces_no_vector.py van X2b
# doet hetzelfde in de app)
MERK = [round(0.0123457 + i * 0.0007351, 7) for i in range(128)]

BOOT = (
    "import ctypes,sys\n"
    "if ctypes.CDLL(None).prctl(4,0,0,0,0):sys.exit(3)\n"
    "sys.path.insert(0,sys.argv.pop(1))\n"
    "from tests_analyse import proef\n"
    "sys.exit(proef.main())"
)


# VROEG: de nummers die de draad van fd 4 doorgaf (StopFlag.stop)
_STOPS: set[int] = set()
_STOP_KWAM = threading.Condition()
VROEG_S = 10.0


class _Vlag(child.StopFlag):
    def stop(self, n: int) -> None:
        super().stop(n)
        with _STOP_KWAM:
            _STOPS.add(n)
            _STOP_KWAM.notify_all()


def _recv(sock):
    """child._recv, maar bij een preview VROEG pas terug als de stop van die opdracht binnen is
    (dan komt StopFlag.begin zeker na StopFlag.stop)."""
    message, fds = _echte_recv(sock)
    if message.get("op") == "faces" and fds and os.pread(fds[0], 6, 0) == b"VROEG ":
        n = int(message.get("n") or 0)
        with _STOP_KWAM:
            _STOP_KWAM.wait_for(lambda: n in _STOPS, VROEG_S)
    return message, fds


_echte_recv = child._recv


def _merk_bytes() -> bytes:
    import struct

    return struct.pack(f"<{len(MERK)}f", *MERK)


def _wacht(job: child.Job, *, luister: bool = True) -> dict:
    end = time.monotonic() + job.deadline_ms / 1000 + 60
    while time.monotonic() < end:
        if luister:
            job.stop.check()
        time.sleep(0.01)
    return {"photos": []}


def _vreet_shm(mb: int) -> dict:
    # Anoniem en gedeeld: RssShmem, buiten RLIMIT_DATA (en zonder ftruncate, dus ook buiten
    # RLIMIT_FSIZE)
    area = mmap.mmap(-1, mb << 20, flags=mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)
    for i in range(0, mb << 20, 4096):
        area[i] = 1
    time.sleep(30)  # de wachter meet elke interval s
    return {"photos": []}


def _vreet_malloc(mb: int) -> dict:
    blocks = []
    for _ in range(mb):
        blocks.append(bytearray(1 << 20))
    return {"photos": [], "n": len(blocks)}


def faces(job: child.Job) -> dict:
    from app.analyse.worker.ops import faces as echte

    head = os.pread(job.fds[0], 64, 0)
    word, _, rest = head.partition(b" ")
    if word in (b"WACHT", b"VROEG"):
        return _wacht(job)
    if word == b"NEGEER":
        return _wacht(job, luister=False)
    if word == b"VREET-SHM":
        return _vreet_shm(int(rest.split()[0]))
    if word == b"VREET-MALLOC":
        return _vreet_malloc(int(rest.split()[0]))
    if word == b"FOUT":
        raise ValueError(f"{GEHEIM} {_merk_bytes().hex()} {MERK}")
    if word == b"STDERR":
        os.write(2, f"{GEHEIM} {MERK}\n".encode())
        os.write(2, _merk_bytes() + b"\n")
        os._exit(7)
    return echte.run(job)


def embed_text(job: child.Job) -> dict:
    text = str(job.args.get("text"))
    if text == GEHEIM:
        raise RuntimeError(f"de zoekvraag was {text}")
    if text == "WACHT":
        return _wacht(job)
    return {
        "env": dict(os.environ),
        "path": list(sys.path),
        "pil": "PIL" in sys.modules,
        "onnxruntime": "onnxruntime" in sys.modules,
        "dumpable": child.dumpable(),
        "pid": os.getpid(),
    }


def main() -> int:
    child.HANDLERS.update({"faces": faces, "embed_text": embed_text})
    child.StopFlag = _Vlag  # type: ignore[misc]
    child._recv = _recv
    return child.main()
