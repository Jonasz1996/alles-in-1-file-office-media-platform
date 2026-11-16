"""BZ7 meten: tokenizer.bin laden in een vers proces, met het geheugen van dat proces zelf.

Niet ru_maxrss: Linux geeft bij (v)fork en exec de piek van de ouder door (exec_mmap zet de
maxrss van het proces op de piek van de oude mm). Een kind van pytest begint dus met de piek van
pytest, en "maxrss na het laden min ervoor" was 0 zodra pytest meer had vastgehad dan het kind
(dezelfde fout als in test_group_perf.py en bouw.maxrss_mib(), gerepareerd in golf 6). Hier:
VmHWM na het laden (de piek van dit proces sinds exec) min VmRSS ervoor, beide uit
/proc/self/status van het kind. test_tokenizer.py houdt dat vast, met pytest die 256 MB vasthoudt.

Gebruikt door BZ7 in tests/beeld/test_tokenizer.py (een tokenizer.bin op ware grootte) en in
tests_analyse/test_beeld_tokenizer_echt.py (het tokenizer.bin van de omzetter, job beeld-model).
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2]


def status_kib(veld: str) -> int:
    """Een veld uit /proc/self/status in KiB (VmHWM: de piek sinds exec; VmRSS: nu)."""
    with open("/proc/self/status") as f:
        for regel in f:
            if regel.startswith(veld + ":"):
                return int(regel.split()[1])
    raise RuntimeError(f"geen {veld} in /proc/self/status")


# Het kind: status_kib zelf (dezelfde code), dan laden en meten. Alleen de tijd, bytes en het
# aantal ids gaan terug; nooit de ids of de tekst
LAAD = (
    inspect.getsource(status_kib)
    + """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from app.analyse.tokenizer import Tokenizer
voor = status_kib("VmRSS")
t = time.perf_counter()
tok = Tokenizer.load(sys.argv[2])
s = time.perf_counter() - t
na = status_kib("VmHWM")
n = len(tok.encode("hond op het strand"))
print(json.dumps({"s": s, "erbij": (na - voor) * 1024, "vmhwm": na * 1024, "ids": n}))
"""
)


def laad(bin_pad: Path) -> dict:
    """Laadt bin_pad in een vers proces (python -I). s: seconden voor Tokenizer.load; erbij:
    VmHWM na het laden min VmRSS ervoor, in bytes; vmhwm: de piek van het kind; ids: de lengte
    van één encode."""
    uit = subprocess.run(
        [sys.executable, "-I", "-c", LAAD, str(API), str(bin_pad)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(uit.stdout)
