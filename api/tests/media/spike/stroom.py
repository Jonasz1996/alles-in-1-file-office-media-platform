"""Eén run van stream (§3.3): ffmpeg met de bron als fd 3, fMP4 naar `pipe:1`, en wij lezen.

Wat de app later doet (de knipper), doet dit in het klein: de boxen uit de pipe halen, de init
bewaren, en per fragment de tijden en het eerste sample lezen. Met `pauze_s` leest hij na het
eerste fragment even niets (tegendruk, D6) en meet hij de CPU van ffmpeg in die tijd.
"""

from __future__ import annotations

import os
import time

import boxen
from gedeeld import cpu_van, start, wacht


def _staat(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return None


def run(
    argv: list[str],
    bron: str,
    *,
    pauze_s: float = 0.0,
    grenzen: dict[str, int] | None = None,
    deadline_s: float = 240.0,
) -> dict:
    t0 = time.monotonic()
    p, staart = start(argv, bron=bron, grenzen=grenzen)
    fd = p.stdout.fileno()
    splitser = boxen.Splitser()
    volgorde: list[str] = []
    init = bytearray()
    sporen: dict[int, boxen.Spoor] = {}
    fragmenten: list[dict] = []
    moof: tuple[int, bytes] | None = None
    pauze: dict | None = None
    totaal = 0
    fout = None
    try:
        while True:
            data = os.read(fd, 1 << 16)
            if not data:
                break
            totaal += len(data)
            for soort, pos, box in splitser.voeg(data):
                volgorde.append(soort)
                if soort in ("ftyp", "moov") and not fragmenten and moof is None:
                    init += box
                    if soort == "moov":
                        sporen = boxen.lees_init(box)
                elif soort == "moof":
                    moof = (pos, box)
                elif soort == "mdat" and moof is not None:
                    trafs = boxen.lees_moof(moof[1], moof[0], sporen)
                    frag = boxen.ontleed(trafs, box, pos, sporen)
                    frag["t_s"] = round(time.monotonic() - t0, 3)
                    frag["bytes"] = len(moof[1]) + len(box)
                    fragmenten.append(frag)
                    moof = None
                    if pauze_s and pauze is None:
                        pauze = _pauzeer(p.pid, pauze_s)
    except Exception as e:  # een kapotte stroom is een uitkomst, geen crash van de spike
        fout = f"{type(e).__name__}: {e}"
    finally:
        p.stdout.close()
    uit = wacht(p, deadline_s)
    uit.update(
        {
            "argv": argv,
            "wand_s": round(time.monotonic() - t0, 3),
            "stderr": staart.tekst(),
            "voortgang": {k: staart.voortgang.get(k) for k in ("speed", "out_time", "progress")},
            "bytes": totaal,
            "volgorde": _kort(volgorde),
            "vorm_goed": _vorm(volgorde),
            "kapot": splitser.kapot or fout,
            "rest_bytes": len(splitser.buf),
            "sporen": {str(k): s.info() for k, s in sporen.items()},
            "fragmenten": fragmenten,
            "t_eerste_fragment_s": fragmenten[0]["t_s"] if fragmenten else None,
            "pauze": pauze,
        }
    )
    uit["_init"] = bytes(init)
    uit["_sporen"] = sporen
    return uit


def _pauzeer(pid: int, pauze_s: float) -> dict:
    """Tegendruk (D6): niets lezen, en de CPU van ffmpeg meten."""
    c0 = cpu_van(pid)
    time.sleep(1.0)
    c1 = cpu_van(pid)
    time.sleep(max(0.0, pauze_s - 1.0))
    c2 = cpu_van(pid)
    staat = _staat(pid)

    def verschil(a, b):
        return None if a is None or b is None else round(b - a, 3)

    return {
        "pauze_s": pauze_s,
        "cpu_hele_pauze_s": verschil(c0, c2),
        "cpu_na_1s_s": verschil(c1, c2),
        "staat_ffmpeg": staat,  # S = slaapt (blokkeert op de pipe), R = rekent, Z = klaar
    }


def _vorm(volgorde: list[str]) -> bool:
    """D2: eerst ftyp en moov, dan alleen moof+mdat-paren."""
    if volgorde[:2] != ["ftyp", "moov"]:
        return False
    rest = volgorde[2:]
    return (
        len(rest) >= 2
        and len(rest) % 2 == 0
        and all(rest[i] == "moof" and rest[i + 1] == "mdat" for i in range(0, len(rest), 2))
    )


def _kort(volgorde: list[str]) -> str:
    """ftyp moov (moof mdat)×N, of de eerste 12 als het anders is."""
    if _vorm(volgorde):
        return f"ftyp moov (moof mdat)×{(len(volgorde) - 2) // 2}"
    return " ".join(volgorde[:12]) + (" …" if len(volgorde) > 12 else "")


def openbaar(r: dict) -> dict:
    """Een run voor spike.json: zonder de bytes, en de fragmenten ingekort."""
    uit = {k: v for k, v in r.items() if not k.startswith("_") and k != "fragmenten"}
    uit["argv"] = " ".join(r["argv"])
    frags = r.get("fragmenten", [])
    uit["fragmenten_n"] = len(frags)
    uit["fragmenten"] = frags[:8]
    return uit
