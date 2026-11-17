"""De opdrachtregel van de omzetter (fase 6, X3a, Keuze 73 en 84).

In de dienst `analyse-omzetten` (compose.analyse.yaml, profiel `omzetten`), als uid 10004, zonder
netwerk, sleutel, socket of /models; de bron alleen-lezen, de uitvoer een lege map:

    python -I -c "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));
        runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"
        /srv --bron /bron --uit /uit [--fp32]

(app.analyse.runtime.OMZET_BOOT). numpy staat in /opt/analyse, niet in /opt/venv: die map komt
achteraan in sys.path vóór de eerste import van numpy (zoals in de kinderen van de worker).

Uitvoer op stdout: per bestand een regel voor models.lock (`sha256 bytes naam -`). Op stderr de
voortgang, de tijd en maxrss. Code 0: klaar; 2: een bron of map die niet klopt (een zin, geen
traceback; de uitvoermap blijft leeg); 1: een onverwachte fout.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ANALYSE_PATH = "/opt/analyse"  # de vijf pakketten van de extra analyse (Keuze 57)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="analyse-omzetten", description="SigLIP 2 omzetten naar int8-ONNX (Keuze 73)"
    )
    ap.add_argument("--bron", required=True, help="de map met de vijf bronbestanden")
    ap.add_argument("--uit", required=True, help="een lege map voor de uitvoer")
    ap.add_argument("--fp32", action="store_true", help="ook de fp32-modellen (BZ4)")
    args = ap.parse_args(argv)
    if os.path.isdir(ANALYSE_PATH) and ANALYSE_PATH not in sys.path:
        sys.path.append(ANALYSE_PATH)

    from app.analyse.worker.omzetten import OmzetFout

    begin = time.monotonic()

    def melden(tekst: str) -> None:
        print(f"omzetten: {tekst} ({time.monotonic() - begin:.1f} s)", file=sys.stderr, flush=True)

    try:
        from app.analyse.worker.omzetten import bouw

        uitkomsten = bouw.omzet(args.bron, args.uit, fp32=args.fp32, melden=melden)
    except OmzetFout as e:
        print(f"omzetten: {e}", file=sys.stderr, flush=True)
        return 2
    except ModuleNotFoundError as e:
        print(f"omzetten: het pakket {e.name} ontbreekt (de extra analyse)", file=sys.stderr)
        return 2
    for u in uitkomsten:
        print(u.regel(), flush=True)
    print(
        f"omzetten: klaar in {time.monotonic() - begin:.1f} s, maxrss {bouw.maxrss_mib():.0f} MiB",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
