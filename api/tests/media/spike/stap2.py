"""S2: D1–D6 en J4 op de ffmpeg van het image, met de commando's van §3.3.

- fd-invoer met zoeken (D1): mkv met Cues, mp4 met de moov achteraan, MPEG-TS;
- fMP4 naar een pipe (D2): eerst ftyp en moov, dan moof+mdat;
- `+frag_discont` (J4): `tfdt` = mediatijd + 1 s, ook na `-ss 2k`;
- het rooster: `gte(t,n_forced*2)` met `-forced-idr 1` en `-g 100000`, vanaf 0 en na `-ss 2k`,
  ook voor `fps23976.mkv`, `interlaced.ts` en `fps240.mp4`: precies één IDR per 2 s, en 60 fps;
- elk fragment begint met NAL-type 5 (H.264);
- tegendruk (D6);
- de tijden (V4): met `+negative_cts_offsets` is `tfdt` plus de offset van het eerste sample
  de brontijd + 1 s, binnen één frame: H.264 met B-frames (transcode) en HEVC (remux).

Daarbij VP9 met Opus (de tests in Chromium spelen dat) en een remux van H.264.
"""

from __future__ import annotations

import math

import commandos
import stroom
from gedeeld import Stap

# naam → (bron, sprongen, w, h, maxrate, level, codec, deinterlace)
TRANSCODES = {
    "h264_bframes": ("bframes.mkv", (0, 6), 1280, 720, 3500, "3.1", "libx264", False),
    "fps23976": ("fps23976.mkv", (0, 6), 1280, 720, 3500, "3.1", "libx264", False),
    "interlaced": ("interlaced.ts", (0, 6), 1280, 720, 3500, "3.1", "libx264", True),
    "fps240": ("fps240.mp4", (0, 2), 640, 360, 1500, "3.1", "libx264", False),
    "vp9_opus": ("bframes.mkv", (0, 6), 640, 360, 1500, "", "libvpx-vp9", False),
}
REMUXEN = {"remux_h264": ("bframes.mkv", False), "remux_hevc": ("hevc.mkv", True)}
PAUZE_S = 4.0
EPS = 0.0015  # afronding van tijdschalen, in s


def transcode_run(ctx, naam: str, start_s: float) -> dict:
    bron_naam, _, w, h, rate, level, codec, deint = TRANSCODES[naam]
    bron = ctx.bron(bron_naam)
    argv = commandos.transcode(
        bron["demuxer"], start_s, bron["start_s"], bron["video"], bron["audio"],
        w=w, h=h, maxrate_k=rate, level=level or "3.1", codec=codec, deinterlace=deint,
    )  # fmt: skip
    pauze = PAUZE_S if (naam == "h264_bframes" and start_s == 0) else 0.0
    return ctx.run(f"{naam}@{start_s}", lambda: stroom.run(argv, bron["pad"], pauze_s=pauze))


def remux_b(bron: dict) -> float:
    """De grens van segment 1 bij remux: de eerste keyframe ≥ 6 s mediatijd (Keuze 23)."""
    s = bron["start_s"]
    for k in bron["sleutels"]:
        if k - s >= 6.0 - 1e-6:
            return k - s
    return 6.0


def remux_run(ctx, naam: str, herstart: bool) -> dict:
    bron_naam, hvc1 = REMUXEN[naam]
    bron = ctx.bron(bron_naam)
    start_s = remux_b(bron) - 0.5 if herstart else 0.0
    argv = commandos.remux(
        bron["demuxer"], start_s, bron["start_s"], bron["video"], bron["audio"], hvc1=hvc1
    )
    return ctx.run(f"{naam}@{start_s}", lambda: stroom.run(argv, bron["pad"]))


def _frame(r: dict, fps_bron: float | None) -> float:
    """De duur van één beeld van de uitvoer, in s."""
    v = [f["vide"] for f in r["fragmenten"] if "vide" in f]
    sporen = r.get("_sporen", {})
    ts = next((s.tijdschaal for s in sporen.values() if s.soort == "vide"), 0)
    if v and ts and v[0]["duren"]:
        return max(v[0]["duren"]) / ts
    return 1.0 / (fps_bron or 24.0)


def _video(r: dict) -> list[dict]:
    return [f["vide"] for f in r["fragmenten"] if "vide" in f]


def _audio(r: dict) -> list[dict]:
    return [f["soun"] for f in r["fragmenten"] if "soun" in f]


def draai(ctx) -> Stap:
    stap = Stap(
        "S2",
        "D1–D6 en J4 op het image: fd-invoer, fMP4 naar een pipe, tfdt, het rooster, NAL-type 5,"
        " tegendruk en de tijden van V4",
        "de commando's van §3.3",
    )
    stap.verwacht = (
        "Zoals op 6.1.1 (D1–D6, J4, V4, V5): zoeken via fd: werkt in mkv, mp4 en TS; de pipe geeft"
        " ftyp+moov en dan moof+mdat; tfdt = mediatijd + 1 s, ook na -ss 2k; precies één IDR"
        " (NAL 5) per 2 s vanaf 0 en na -ss 2k, ook bij 23,976 fps, interlaced en 240 fps (→ 60"
        " fps); trun v1 en het eerste beeld op brontijd + 1 s binnen één frame; zonder lezen"
        " staat ffmpeg stil."
    )
    runs = {}
    aantallen: list[dict] = []
    for naam, (bron_naam, sprongen, *_rest) in TRANSCODES.items():
        bron = ctx.bron(bron_naam)
        if "fout" in bron:
            stap.check(f"{naam}: bron", False, "bron gemaakt", bron["fout"][-300:])
            continue
        for start_s in sprongen:
            r = transcode_run(ctx, naam, start_s)
            runs[(naam, start_s)] = r
            _controleer_transcode(stap, naam, start_s, r, bron, aantallen)
        if all((naam, s) in runs for s in sprongen):
            _controleer_herstart(stap, naam, runs[(naam, 0)], runs[(naam, sprongen[1])], bron)
    anders = [a for a in aantallen if a["n_gemeten"] != a["n_ontwerp"]]
    stap.check(
        "aantal fragmenten = N = ceil(D/2) (Keuze 22)",
        bool(aantallen) and not anders,
        "N = ceil((D − 2k) / 2), met D de duur van de bron (ffprobe, mediatijd)",
        f"{len(aantallen) - len(anders)} van {len(aantallen)} runs gelijk; anders: "
        + "; ".join(
            f"{a['run']}: {a['n_gemeten']} in plaats van {a['n_ontwerp']} (D {a['d_s']:g} s,"
            f" laatste fragment {a['beelden_laatste']} beelden, uit het laatste bronbeeld"
            f" {a['n_uit_beelden']})"
            for a in anders
        ),
        runs=aantallen,
    )
    for naam in REMUXEN:
        bron = ctx.bron(REMUXEN[naam][0])
        if "fout" in bron:
            stap.check(f"{naam}: bron", False, "bron gemaakt", bron["fout"][-300:])
            continue
        r0 = remux_run(ctx, naam, False)
        r1 = remux_run(ctx, naam, True)
        _controleer_remux(stap, naam, r0, r1, bron)
    _controleer_zoeken(stap, ctx, runs)
    _controleer_tegendruk(stap, runs.get(("h264_bframes", 0)))
    _samenvatten(stap, runs)
    return stap


def _controleer_transcode(
    stap: Stap, naam: str, start_s: float, r: dict, bron: dict, aantallen: list[dict]
) -> None:
    wie = f"{naam} vanaf {start_s} s"
    stap.gegevens[f"run {wie}"] = stroom.openbaar(r)
    if not stap.check(
        f"{wie}: rc 0 en de vorm van D2",
        r["rc"] == 0 and r["vorm_goed"] and not r["kapot"],
        "rc 0, ftyp moov (moof mdat)×N",
        f"rc {r['rc']}, {r['volgorde']}"
        + (f", {r['kapot']}" if r["kapot"] else "")
        + (f", stderr: {r['stderr'][-300:]}" if r["rc"] else ""),
    ):
        return
    v = _video(r)
    frame = _frame(r, bron["fps"])
    tol = frame + EPS
    # het rooster: fragment j begint op 1 + start + 2j (mediatijd + 1 s), binnen één beeld
    afwijking = [round(f["pts0_s"] - (1 + start_s + 2 * j), 4) for j, f in enumerate(v)]
    stap.check(
        f"{wie}: rooster van 2 s, tfdt = mediatijd + 1 s",
        all(abs(a) <= tol for a in afwijking),
        f"fragment j begint op {1 + start_s:g} + 2j s (± 1 beeld = {frame:.4f} s)",
        f"{len(v)} fragmenten, eerste tfdt {v[0]['tfdt_s']:.4f} s, afwijking per fragment "
        f"{afwijking[:8]}",
    )
    # het aantal: Keuze 22 zegt N = ceil(D/2), met D de duur van de bron (mediatijd)
    n_ontwerp = math.ceil(round((bron["duur_s"] - start_s) / 2.0, 6))
    na = [p for p in bron["pts"] if p >= bron["start_s"] + start_s - 1e-6]
    n_beelden = int((na[-1] - na[0]) // 2) + 1 if na else None
    aantallen.append(
        {
            "run": wie,
            "d_s": bron["duur_s"],
            "n_ontwerp": n_ontwerp,
            "n_gemeten": len(v),
            "n_uit_beelden": n_beelden,
            "beelden_laatste": v[-1]["n"],
        }
    )
    if naam == "vp9_opus":
        ok = all(f.get("vp9_sleutel0") and f.get("vp9_sleutels") == 1 for f in v)
        stap.check(
            f"{wie}: één keyframe per fragment, vooraan",
            ok and all(f.get("sync0") and f.get("sync_n") == 1 for f in v),
            "elk fragment: eerste frame een VP9-keyframe en het enige",
            [(f.get("vp9_sleutel0"), f.get("vp9_sleutels"), f.get("sync_n")) for f in v][:8],
        )
    else:
        ok = all(
            f.get("sync0") and f.get("vcl0") == 5 and f.get("idr_samples") == 1
            and f.get("sync_n") == 1
            for f in v
        )  # fmt: skip
        stap.check(
            f"{wie}: één IDR per fragment, NAL-type 5 vooraan",
            ok,
            "elk fragment: eerste sample sync met NAL 5, geen andere IDR of sync",
            [(f.get("nal0"), f.get("idr_samples"), f.get("sync_n")) for f in v][:6],
        )
    if naam == "fps240":
        sporen = r["_sporen"]
        ts = next((s.tijdschaal for s in sporen.values() if s.soort == "vide"), 0)
        duren = sorted({d for f in v for d in f["duren"]})
        fps = [round(ts / d, 3) for d in duren if d]
        n_vol = [f["n"] for f in v[:-1]]
        stap.check(
            f"{wie}: -fpsmax 60 geeft 60 fps",
            fps == [60.0] and all(n == 120 for n in n_vol),
            "alle beelden 1/60 s, 120 per volledig fragment",
            f"fps {fps}, beelden per fragment {[f['n'] for f in v]}",
        )
    # V4: het eerste beeld valt op de tijd van een bronbeeld + 1 − S, met trun v1
    s_ = bron["start_s"]
    dichtst = (
        min(bron["pts"], key=lambda p: abs(p - s_ + 1 - v[0]["pts0_s"])) if bron["pts"] else None
    )
    verwacht0 = None if dichtst is None else round(dichtst - s_ + 1, 6)
    a = _audio(r)
    av = None if not a or a[0]["tfdt_s"] is None else round((v[0]["pts0_s"] - a[0]["tfdt_s"]), 4)
    negatief = any(c < 0 for f in v for c in f["cto"])
    trun = sorted({t for f in v for t in f["trun"]})
    stap.check(
        f"{wie}: eerste beeld = brontijd + 1 s (V4), trun v1",
        verwacht0 is not None
        and abs(v[0]["pts0_s"] - verwacht0) <= EPS
        and v[0]["pts0_s"] >= 1 + start_s - EPS
        and trun == [1],
        "tfdt + offset van het eerste sample = de tijd van een bronbeeld − S + 1, niet vóór het"
        " doel; trun v1",
        f"tfdt {v[0]['tfdt_s']:.4f} + offset {v[0]['cto'][:1]} → {v[0]['pts0_s']:.4f} s (bronbeeld"
        f" → {verwacht0} s), trun {trun}, negatieve offsets {negatief}, offsets {v[0]['cto']},"
        f" beeld − geluid {av} s",
        beeld_min_geluid_s=av,
    )


def _controleer_herstart(stap: Stap, naam: str, r0: dict, r1: dict, bron: dict) -> None:
    """D4: een herstart op 2k geeft hetzelfde rooster als een run vanaf 0."""
    if r0["rc"] or r1["rc"]:
        return
    v0 = {round((f["pts0_s"] - 1) / 2): f for f in _video(r0)}
    v1 = {round((f["pts0_s"] - 1) / 2): f for f in _video(r1)}
    gedeeld_ = sorted(set(v0) & set(v1))
    delta = [round(v1[j]["pts0_s"] - v0[j]["pts0_s"], 4) for j in gedeeld_]
    a0 = {round((f["tfdt_s"] - 1) / 2): f for f in _audio(r0)}
    a1 = {round((f["tfdt_s"] - 1) / 2): f for f in _audio(r1)}
    audio = [
        round((a1[j]["tfdt_s"] - a0[j]["tfdt_s"]) * 1000, 1) for j in sorted(set(a0) & set(a1))
    ]
    frame = _frame(r0, bron["fps"])
    gelijk = sum(1 for d in delta if abs(d) < 0.0005)
    stap.check(
        f"{naam}: herstart geeft hetzelfde rooster (D4)",
        bool(gedeeld_) and all(abs(d) <= frame + EPS for d in delta) and len(v1) == len(gedeeld_),
        "dezelfde videotijden als vanaf 0 (bij 23,976 fps mag één beeld schelen)",
        f"{len(gedeeld_)} fragmenten vergeleken, {gelijk} precies gelijk, verschil {delta[:6]} s;"
        f" audio na de sprong {audio[:3]} ms",
        audio_verschil_ms=audio[:3],
    )


def _controleer_remux(stap: Stap, naam: str, r0: dict, r1: dict, bron: dict) -> None:
    for wie, r in ((f"{naam} vanaf 0", r0), (f"{naam} na de sprong", r1)):
        stap.gegevens[f"run {wie}"] = stroom.openbaar(r)
        stap.check(
            f"{wie}: rc 0 en de vorm van D2",
            r["rc"] == 0 and r["vorm_goed"] and not r["kapot"],
            "rc 0, ftyp moov (moof mdat)×N",
            f"rc {r['rc']}, {r['volgorde']}"
            + (f", stderr: {r['stderr'][-300:]}" if r["rc"] else ""),
        )
    if r0["rc"] or r1["rc"] or not _video(r0) or not _video(r1):
        return
    s = bron["start_s"]
    sleutels = [round(k - s + 1, 4) for k in bron["sleutels"]]
    v0 = _video(r0)
    begin0 = [round(f["pts0_s"], 4) for f in v0]
    stap.check(
        f"{naam}: één fragment per GOP, op de keyframes van de bron (D5)",
        len(begin0) == len(sleutels)
        and all(abs(a - b) <= EPS for a, b in zip(begin0, sleutels, strict=False)),
        f"fragmenten op {sleutels} s",
        f"{begin0}",
    )
    frame = _frame(r0, bron["fps"])
    trun = sorted({t for f in v0 for t in f["trun"]})
    stap.check(
        f"{naam}: eerste beeld = brontijd + 1 s (V4), trun v1",
        bool(sleutels) and abs(v0[0]["pts0_s"] - sleutels[0]) <= frame + EPS and trun == [1],
        f"tfdt + offset = {sleutels[0] if sleutels else None} s (± 1 beeld), trun v1",
        f"tfdt {v0[0]['tfdt_s']:.4f} + offsets {v0[0]['cto']} → {v0[0]['pts0_s']:.4f} s, trun "
        f"{trun}",
    )
    b = remux_b(bron)
    v1 = [f for f in _video(r1) if f["pts0_s"] >= b + 1 - EPS]
    weg = len(_video(r1)) - len(v1)
    ref = [f for f in v0 if f["pts0_s"] >= b + 1 - EPS]
    paren = [
        (round(x["pts0_s"], 4), x["n"], round(y["pts0_s"], 4), y["n"])
        for x, y in zip(v1, ref, strict=False)
    ]
    stap.check(
        f"{naam}: herstart op -ss b−0,5 geeft na b dezelfde fragmenten",
        len(v1) == len(ref) and all(abs(p[0] - p[2]) <= EPS and p[1] == p[3] for p in paren),
        f"na b = {b:g} s dezelfde fragmenten als vanaf 0; ervoor weggooien",
        f"{weg} fragment(en) vóór b, daarna {paren[:4]}",
    )
    nal = [f.get("nal0") for f in v0][:4]
    entry = next((sp.entry for sp in r0["_sporen"].values() if sp.soort == "vide"), None)
    hevc = naam == "remux_hevc"
    stap.check(
        f"{naam}: sample entry en het eerste sample van elk fragment",
        all(f.get("sync0") for f in v0) and entry == ("hvc1" if hevc else "avc1"),
        f"{'hvc1' if hevc else 'avc1'}, elk fragment begint met een sync sample",
        f"{entry}, NAL-types vooraan {nal}",
    )


def _controleer_zoeken(stap: Stap, ctx, runs: dict) -> None:
    """D1: zoeken via fd: in mkv met Cues, mp4 met de moov achteraan en MPEG-TS. Na -ss moet de
    uitvoer op het doel beginnen (binnen één beeld), niet op een latere keyframe."""
    for naam, bron_naam, s in (
        ("h264_bframes", "bframes.mkv", 6),
        ("fps240", "fps240.mp4", 2),
        ("interlaced", "interlaced.ts", 6),
    ):
        bron = ctx.bron(bron_naam)
        r = runs.get((naam, s))
        v = _video(r) if r else []
        if not v or r["rc"]:
            stap.check(f"D1: zoeken via fd: in {bron_naam}", False, "een run", "geen fragmenten")
            continue
        frame = _frame(r, bron["fps"])
        laat = round(v[0]["pts0_s"] - (1 + s), 4)
        sleutels = [round(k - bron["start_s"], 3) for k in bron.get("sleutels", [])][:12]
        stap.check(
            f"D1: zoeken via fd: in {bron_naam}",
            abs(laat) <= frame + EPS,
            f"na -ss {s} begint de uitvoer op mediatijd {s} (± 1 beeld)",
            f"begint {laat} s na het doel; Cues {bron.get('matroska_cues')},"
            f" mp4 {bron.get('mp4_volgorde')},"
            f" S {bron.get('start_s')}, keyframes van de bron (mediatijd) {sleutels}",
            te_laat_s=laat,
        )


def _controleer_tegendruk(stap: Stap, r: dict | None) -> None:
    if not r or not r.get("pauze"):
        stap.check("D6: tegendruk", False, "een run met pauze", "geen run")
        return
    p = r["pauze"]
    cpu = p.get("cpu_na_1s_s")
    stap.check(
        "D6: tegendruk zet ffmpeg stil",
        cpu is not None and cpu <= 0.3 and r["rc"] == 0 and len(_video(r)) == 6,
        "in de laatste 3 s van 4 s niet lezen ≤ 0,3 s CPU; daarna klaar met alle fragmenten",
        f"CPU {p.get('cpu_hele_pauze_s')} s over 4 s, {cpu} s na de eerste seconde, staat"
        f" {p.get('staat_ffmpeg')}; daarna rc {r['rc']} met {len(_video(r))} fragmenten",
    )


def _samenvatten(stap: Stap, runs: dict) -> None:
    eerste = {f"{n}@{s}": r.get("t_eerste_fragment_s") for (n, s), r in runs.items()}
    stap.gegevens["eerste_fragment_s"] = eerste
    if stap.ok():
        stap.gemeten = f"Alle {len(stap.checks)} controles zoals verwacht."
        stap.besluit = (
            "De commando's van §3.3 (transcode, VP9 en remux) liggen vast zoals ze er staan."
        )
        return
    anders = stap.anders()
    stap.gemeten = f"{len(stap.checks) - len(anders)} van {len(stap.checks)} zoals verwacht;"
    stap.gemeten += " anders: " + "; ".join(anders)
    ts_zoeken = any(a.startswith("D1") and ".ts" in a for a in anders)

    def is_ts(a: str) -> bool:
        return a.startswith("interlaced vanaf 6") or a.startswith("interlaced: herstart")

    terugval = []
    if any(("rooster" in a or "D4" in a) and not is_ts(a) for a in anders):
        terugval.append(
            "S2: de knipper zet tfdt zelf (een vaste verschuiving per run); een keyframe die na"
            " een herstart één beeld anders valt (23,976 fps) is onschuldig"
        )
    if any("V4" in a and not is_ts(a) for a in anders):
        terugval.append(
            "S2 (tijden): de knipper telt de offset van het eerste sample bij tfdt op en schrijft"
            " dat in de tfdt; dan is trun v1 niet nodig"
        )
    stap.terugval = "; ".join(terugval) or None
    besluit = []
    if any("aantal fragmenten" in a for a in anders):
        besluit.append(
            "N = ceil(D/2) met de duur van het formaat klopt niet altijd (zie n_ontwerp en"
            " n_gemeten): M4 rekent N uit de tijd van het laatste videobeeld, sluit het laatste"
            " segment bij het eind van de producent, en voegt een fragment voorbij N (-fpsmax vult"
            " het eind aan) bij het laatste segment"
        )
    if ts_zoeken:
        besluit.append(
            "MPEG-TS heeft geen index: na -ss begint de uitvoer pas op de volgende keyframe, dus"
            " een herstart mist tot één GOP van de bron; M4 moet bij TS anders zoeken"
        )
    if any(("IDR" in a or "keyframe" in a) and not is_ts(a) for a in anders):
        besluit.append("geen EXT-X-INDEPENDENT-SEGMENTS tot het rooster van IDR's klopt")
    if any("D6" in a for a in anders):
        besluit.append("tegendruk alleen is niet genoeg: -readrate erbij (S6)")
    if any(("D1" in a and not ts_zoeken) or "vorm" in a for a in anders):
        besluit.append("de commando's van §3.3 niet bevriezen voor dit is uitgezocht")
    rest = "De commando's van §3.3 zelf blijven zoals ze er staan"
    stap.besluit = "; ".join([rest, *besluit]) + "."
