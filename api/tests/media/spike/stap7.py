"""S7: `bench` drie keer, onder de grens van 3 cores (regel 14): is de spreiding < 10 %?

De meting en het decoderen van HEVC10 zijn de regels van §3.3 (Keuze 26): `f` = cores voor
1,0× / 0,74 en `f_dec` = (CPU-tijd / 4 s) / 0,31. De CPU-tijd komt uit `wait4` van het kind.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import threading
import time

import commandos
from gedeeld import Stap, cpu_grens, start, wacht
from gedeeld import draai as voer

RUNS = 3


def _f_dec(serieel: bool) -> dict:
    """HEVC10 maken en decoderen. Normaal twee processen met een pipe (§3.3); serieel eerst
    maken en dan dezelfde bytes in de pipe:0 van de decoder (nooit twee ffmpeg tegelijk)."""
    t0 = time.monotonic()
    if serieel:
        p1, s1 = start(commandos.HEVC10_MAAK, stdout=subprocess.PIPE)
        data = p1.stdout.read()
        p1.stdout.close()
        maak = wacht(p1, 120)
        p2, s2 = start(commandos.HEVC10_DECODEER, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)

        def schrijf() -> None:
            try:
                p2.stdin.write(data)
            except OSError:
                pass
            finally:
                p2.stdin.close()

        draad = threading.Thread(target=schrijf, daemon=True)
        draad.start()
        dec = wacht(p2, 120)
        draad.join(5)
    else:
        r, w = os.pipe()
        p1, s1 = start(commandos.HEVC10_MAAK, stdout=w)
        p2, s2 = start(commandos.HEVC10_DECODEER, stdin=r, stdout=subprocess.DEVNULL)
        os.close(r)
        os.close(w)
        dec = wacht(p2, 120)
        maak = wacht(p1, 120)
        data = b""
    maak["stderr"] = s1.tekst()[-300:]
    dec["stderr"] = s2.tekst()[-300:]
    return {
        "maak": maak,
        "decodeer": dec,
        "hevc_bytes": len(data) if serieel else None,
        "wand_s": round(time.monotonic() - t0, 2),
        "cores": round(dec["cpu_s"] / commandos.HEVC10_MEDIA_S, 3),
    }


def _spreiding(waarden: list[float]) -> float | None:
    if len(waarden) < 2 or not statistics.mean(waarden):
        return None
    return round((max(waarden) - min(waarden)) / statistics.mean(waarden), 3)


def draai(ctx) -> Stap:
    stap = Stap(
        "S7",
        "bench drie keer onder 3 cores: spreiding van f en f_dec",
        "of de factoren f en f_dec bruikbaar zijn (Keuze 26)",
    )
    stap.verwacht = (
        "Drie keer de meting van §3.3 (10 s testsrc2 met ruis, 1080 → 720 veryfast) en het"
        " decoderen van 4 s HEVC10: de spreiding (max − min) / gemiddelde is voor f en voor f_dec"
        " kleiner dan 10 %."
    )
    grens = cpu_grens()
    try:
        affiniteit = len(os.sched_getaffinity(0))
    except OSError:
        affiniteit = None
    stap.check(
        "onder de grens van 3 cores (regel 14)",
        grens.get("cores") == 3.0 or affiniteit == 3,
        "--cpus 3 (cgroup) of taskset -c 0-2",
        f"cgroup {grens}, affiniteit {affiniteit}",
    )
    runs = []
    for i in range(RUNS):
        b = voer(commandos.BENCH, deadline_s=180)
        d = _f_dec(ctx.serieel)
        rij = {
            "bench": {k: b[k] for k in ("rc", "cpu_s", "wand_s", "max_rss_mb")},
            "bench_cores": round(b["cpu_s"] / commandos.BENCH_MEDIA_S, 3),
            "f_dec": d,
        }
        if b["rc"]:
            rij["bench"]["stderr"] = b["stderr"][-300:]
        runs.append(rij)
        stap.check(
            f"run {i + 1}: rc 0",
            b["rc"] == 0 and d["maak"]["rc"] == 0 and d["decodeer"]["rc"] == 0,
            "rc 0 voor bench, maken en decoderen",
            f"bench rc {b['rc']} ({rij['bench_cores']} cores, {b['wand_s']} s), HEVC10 maken rc"
            f" {d['maak']['rc']}, decoderen rc {d['decodeer']['rc']} ({d['cores']} cores)",
        )
    cores = [r["bench_cores"] for r in runs if r["bench"]["rc"] == 0]
    dec = [r["f_dec"]["cores"] for r in runs if r["f_dec"]["decodeer"]["rc"] == 0]
    f = [round(c / commandos.REF_BENCH, 3) for c in cores]
    f_dec = [round(c / commandos.REF_DEC, 3) for c in dec]
    sp_f, sp_dec = _spreiding(f), _spreiding(f_dec)
    stap.check(
        "spreiding van f < 10 %",
        sp_f is not None and sp_f < 0.10,
        "< 0,10",
        f"f = {f} (cores voor 1,0×: {cores}), spreiding {sp_f}",
    )
    stap.check(
        "spreiding van f_dec < 10 %",
        sp_dec is not None and sp_dec < 0.10,
        "< 0,10",
        f"f_dec = {f_dec} (cores: {dec}), spreiding {sp_dec}",
    )
    stap.gegevens.update(
        {
            "runs": runs,
            "f": f,
            "f_dec": f_dec,
            "spreiding_f": sp_f,
            "spreiding_f_dec": sp_dec,
            "serieel": ctx.serieel,
            "referenties": {"bench": commandos.REF_BENCH, "dec": commandos.REF_DEC},
            "noot_referentie": (
                "0,74 is in de tabel van Keuze 26 de prijs van 720 veryfast van een echte bron in"
                " de sandbox, niet van deze bench (testsrc2 met ruis). Lokaal (ffmpeg 6.1.1, 3"
                " cores) kostte deze bench 1,4 tot 1,5 cores, dus f ≈ 2 waar het 1 hoort te zijn."
                " De referentie moet de bench zelf zijn, gemeten op de sandbox."
            ),
        }
    )
    gem_f = round(statistics.mean(f), 3) if f else None
    gem_dec = round(statistics.mean(f_dec), 3) if f_dec else None
    stap.gemeten = (
        f"f {f} (gemiddeld {gem_f}, spreiding {sp_f}); f_dec {f_dec} (gemiddeld {gem_dec},"
        f" spreiding {sp_dec})"
    )
    if stap.ok():
        stap.besluit = (
            f"De factoren zijn bruikbaar: f ≈ {gem_f} en f_dec ≈ {gem_dec} hier. Eén run per"
            " meting is genoeg."
        )
    else:
        stap.besluit = (
            "De spreiding is te groot voor één run: bench neemt de mediaan van drie runs, of meet"
            " langer. §3.7 heeft voor S7 geen terugval; dit gaat naar M1 en M10."
        )
    stap.besluit += (
        " Let op: f deelt door 0,74, de tabelprijs van een echte bron, niet door deze bench in de"
        " sandbox (zie noot_referentie); M1 meet de referentie van de bench zelf."
    )
    return stap
