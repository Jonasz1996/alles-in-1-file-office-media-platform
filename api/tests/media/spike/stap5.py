"""S5: welke boxen verschillen tussen de inits van twee runs (N10).

Alleen `mvhd`, `tkhd` en `mdhd` (dan is de vergelijking van Keuze 22 op `stsd` goed), of ook
`stsd` en `elst`? Vergeleken: een run vanaf 0 met een herstart (transcode, VP9 en remux), en als
controle twee runs die echt anders zijn (een andere grootte): daar moet `stsd` verschillen.
"""

from __future__ import annotations

import boxen
import commandos
import stap2
import stroom
from gedeeld import Stap


def _stsd(r: dict) -> dict[str, bytes]:
    return {f"{s.soort}": s.stsd for s in r["_sporen"].values()}


def _elst(r: dict) -> dict[str, object]:
    return {f"{s.soort}": s.elst for s in r["_sporen"].values()}


def draai(ctx) -> Stap:
    stap = Stap(
        "S5",
        "verschillen tussen de inits van twee runs: mvhd/tkhd/mdhd, of ook stsd en elst",
        "de vergelijking van Keuze 22 (generaties van de init)",
    )
    stap.verwacht = (
        "Tussen een run vanaf 0 en een herstart verschillen hooguit mvhd, tkhd en mdhd; stsd is per"
        " spoor gelijk (dezelfde generatie). Bij een echt andere uitvoer (een andere grootte)"
        " verschilt stsd wel."
    )
    paren = []

    def bron_goed(naam: str, bron_naam: str) -> bool:
        bron = ctx.bron(bron_naam)
        if "fout" in bron:
            stap.check(f"{naam}: bron", False, "bron gemaakt", bron["fout"][-300:])
        return "fout" not in bron

    for naam, (bron_naam, sprongen, *_r) in stap2.TRANSCODES.items():
        if naam in ("fps240", "fps23976", "interlaced") or not bron_goed(naam, bron_naam):
            continue
        r0 = stap2.transcode_run(ctx, naam, 0)
        paren.append((naam, r0, stap2.transcode_run(ctx, naam, sprongen[1])))
    for naam, (bron_naam, _hvc1) in stap2.REMUXEN.items():
        if bron_goed(naam, bron_naam):
            paren.append(
                (naam, stap2.remux_run(ctx, naam, False), stap2.remux_run(ctx, naam, True))
            )
    elst_gezien: dict[str, object] = {}
    elst_anders = False
    for naam, r0, r1 in paren:
        if not r0["_init"] or not r1["_init"]:
            stap.check(f"{naam}: twee inits", False, "twee inits", "een run gaf geen init")
            continue
        diff = boxen.verschil(r0["_init"], r1["_init"])
        s0, s1 = _stsd(r0), _stsd(r1)
        e0, e1 = _elst(r0), _elst(r1)
        elst_gezien[naam] = {"vanaf_0": e0, "herstart": e1}
        elst_anders |= e0 != e1
        stsd_gelijk = s0 == s1 and bool(s0)
        stap.check(
            f"{naam}: herstart heeft dezelfde stsd per spoor",
            stsd_gelijk,
            "stsd gelijk; verschil hooguit in mvhd, tkhd, mdhd",
            f"stsd {'gelijk' if stsd_gelijk else 'ANDERS'}; verschillende boxen: {diff or 'geen'};"
            f" elst vanaf 0 {e0}, herstart {e1}",
            boxen_anders=diff,
            init_bytes=[len(r0["_init"]), len(r1["_init"])],
            init_gelijk=r0["_init"] == r1["_init"],
        )
    # de controle: een andere grootte moet een andere stsd geven
    bron = ctx.bron("bframes.mkv")
    if "fout" not in bron:
        argv = commandos.transcode(
            bron["demuxer"], 0, bron["start_s"], bron["video"], bron["audio"],
            w=640, h=360, maxrate_k=1500, level="3.1",
        )  # fmt: skip
        anders = ctx.run("controle_640x360", lambda: stroom.run(argv, bron["pad"]))
        ref = stap2.transcode_run(ctx, "h264_bframes", 0)
        if anders["_init"] and ref["_init"]:
            sa, sr = _stsd(anders), _stsd(ref)
            stap.check(
                "controle: 640×360 tegen 1280×720 geeft een andere video-stsd",
                sa.get("vide") != sr.get("vide") and sa.get("soun") == sr.get("soun"),
                "video-stsd anders, audio-stsd gelijk",
                f"verschillende boxen: {boxen.verschil(anders['_init'], ref['_init'])}",
            )
    stap.gegevens["elst"] = elst_gezien
    if stap.ok():
        stap.gemeten = (
            "stsd per spoor is gelijk na elke herstart; de controle ziet een andere stsd."
        )
        if elst_anders:
            elst_besluit = "elst niet meenemen: die verschilt tussen herstarts"
        elif any(v["vanaf_0"] and any(v["vanaf_0"].values()) for v in elst_gezien.values()):
            elst_besluit = "elst is gelijk; meenemen mag, maar hoeft niet"
        else:
            elst_besluit = "er staat geen elst in de init; er valt niets mee te vergelijken"
        stap.besluit = f"De vergelijking van Keuze 22 is de stsd per spoor; {elst_besluit}."
    else:
        stap.gemeten = "anders: " + "; ".join(stap.anders())
        stap.besluit = (
            "Een herstart geeft een andere stsd: elke sprong wordt een nieuwe generatie. MSE vangt"
            " dat op met X-Init (Keuze 22); native HLS sluit dan bij elke sprong met 410. Nakijken"
            " welk veld verschilt (boxen_anders) voor M4 de vergelijking bouwt."
        )
        stap.terugval = (
            "§3.7 noemt voor S5 alleen Safari (een apparaattest in fase 9). Hier geldt de regel"
            " van Keuze 22 voor een andere stsd: generatie +1 met X-Init, en native HLS 410"
        )
    return stap
