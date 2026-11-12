"""De stappen van de spike die in het image draaien (bouwontwerp fase 5, §3.7): S1, S2, S5,
S7 en S10. S3 heeft twee containers nodig en draait vanuit `ci.py` op de runner.

    python3 -I spike.py [--stappen S1,S2,S5,S7,S10] [--serieel]

De uitkomst gaat als JSON naar stdout, het verloop naar stderr. Een stap die anders uitvalt
dan het ontwerp verwacht, is een uitkomst (met "verwacht", "gemeten", "besluit" en de
terugval). Alleen een fout in het script zelf geeft exitcode 1, nadat de JSON er staat.

`--serieel` draait nooit twee ffmpeg tegelijk (in de sandbox, naast een andere agent). Dan maakt
S7 de HEVC10 eerst en decodeert hij daarna uit dezelfde pipe-invoer, in plaats van twee
processen met een pipe ertussen.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bronnen  # noqa: E402
from gedeeld import log, omgeving  # noqa: E402

STAPPEN = ("S1", "S2", "S5", "S7", "S10")


class Context:
    """Wat de stappen delen: de tijdelijke map, de bronnen en de runs (S5 vergelijkt de inits
    uit S2)."""

    def __init__(self, map_: str, serieel: bool) -> None:
        self.map = map_
        self.serieel = serieel
        self._bronnen: dict[str, dict] = {}
        self._runs: dict[str, dict] = {}

    def bron(self, naam: str) -> dict:
        if naam not in self._bronnen:
            self._bronnen[naam] = bronnen.maak(self.map, naam)
        return self._bronnen[naam]

    def run(self, sleutel: str, maak) -> dict:
        if sleutel not in self._runs:
            log(f"  run {sleutel}")
            self._runs[sleutel] = maak()
        return self._runs[sleutel]

    def bronnen_openbaar(self) -> dict:
        return {n: bronnen.openbaar(b) for n, b in self._bronnen.items()}


def stap_module(code: str):
    import stap1
    import stap2
    import stap5
    import stap7
    import stap10

    return {"S1": stap1, "S2": stap2, "S5": stap5, "S7": stap7, "S10": stap10}[code]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stappen", default=",".join(STAPPEN))
    ap.add_argument("--serieel", action="store_true")
    args = ap.parse_args()
    gekozen = [s.strip().upper() for s in args.stappen.split(",") if s.strip()]
    onbekend = [s for s in gekozen if s not in STAPPEN]
    if onbekend:
        ap.error(f"onbekende stap: {onbekend}")
    map_ = tempfile.mkdtemp(prefix="spike-")
    ctx = Context(map_, args.serieel)
    uit = {
        "omgeving": omgeving(),
        "serieel": args.serieel,
        "begin": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stappen": [],
    }
    crash = False
    try:
        for code in gekozen:
            t0 = time.monotonic()
            try:
                stap = stap_module(code).draai(ctx)
                uit["stappen"].append(stap.uit())
            except Exception:
                crash = True
                fout = traceback.format_exc()
                log(f"!! {code}: fout in het script\n{fout}")
                uit["stappen"].append(
                    {
                        "stap": code,
                        "uitkomst": "fout in het script",
                        "fout": fout[-4000:],
                        "duur_s": round(time.monotonic() - t0, 2),
                    }
                )
        uit["bronnen"] = ctx.bronnen_openbaar()
    finally:
        shutil.rmtree(map_, ignore_errors=True)
    uit["crash"] = crash
    json.dump(uit, sys.stdout, ensure_ascii=False, indent=1, default=str)
    sys.stdout.write("\n")
    return 1 if crash else 0


if __name__ == "__main__":
    sys.exit(main())
