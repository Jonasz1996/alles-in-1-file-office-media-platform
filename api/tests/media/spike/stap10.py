"""S10: de grenzen van Keuze 12 met x264 1080 veryfast en `-threads 4`.

Draait ffmpeg nog met `RLIMIT_FSIZE` 0, `RLIMIT_DATA` 1.200 MB, `RLIMIT_CORE` 0,
`RLIMIT_NOFILE` 256, `-max_alloc` en `-max_pixels` (V6 deed dat op 6.1.1 met 720)? En wat kost
`-threads 4` tegen de standaard? Twee controles laten zien dat de grenzen echt bijten: een
bestand schrijven met `RLIMIT_FSIZE` 0, en dezelfde transcode met `RLIMIT_DATA` 64 MB.
"""

from __future__ import annotations

import os

import commandos
import stroom
from gedeeld import MB, Stap
from gedeeld import draai as voer

GRENZEN = {
    "RLIMIT_CORE": 0,
    "RLIMIT_NOFILE": 256,
    "RLIMIT_FSIZE": 0,
    "RLIMIT_DATA": 1200 * MB,
}


def _argv(bron: dict, threads: bool) -> list[str]:
    return commandos.transcode(
        bron["demuxer"], 0, bron["start_s"], bron["video"], bron["audio"],
        w=1920, h=1080, maxrate_k=6000, level="4.0", threads=threads,
    )  # fmt: skip


def _kort(r: dict, media_s: float) -> dict:
    v = [f for f in r["fragmenten"] if "vide" in f]
    return {
        "rc": r["rc"],
        "signaal": r["signaal"],
        "cpu_s": r["cpu_s"],
        "cores_voor_1x": round(r["cpu_s"] / media_s, 3) if media_s else None,
        "wand_s": r["wand_s"],
        "snelheid": round(media_s / r["wand_s"], 2) if r["wand_s"] else None,
        "max_rss_mb": r["max_rss_mb"],
        "eerste_fragment_s": r["t_eerste_fragment_s"],
        "fragmenten": len(v),
        "stderr": r["stderr"][-300:] if r["rc"] else "",
    }


def draai(ctx) -> Stap:
    stap = Stap(
        "S10",
        "RLIMIT_FSIZE 0, RLIMIT_DATA 1.200 MB, -max_alloc en -max_pixels met 1080 veryfast en"
        " -threads 4; de kosten van -threads 4",
        "Keuze 12 (de grenzen vóór exec, -threads 4)",
    )
    stap.verwacht = (
        "De transcode naar 1080 veryfast met -threads 4 draait gewoon onder alle grenzen (rc 0,"
        " alle fragmenten). Een bestand schrijven onder RLIMIT_FSIZE 0 faalt; RLIMIT_DATA 64 MB"
        " is te weinig. -threads 4 kost niet meer CPU dan de standaard."
    )
    bron = ctx.bron("src1080.mkv")
    if "fout" in bron:
        stap.check("bron", False, "bron gemaakt", bron["fout"][-300:])
        stap.besluit = "niet te meten"
        return stap
    media = bron["duur_s"]
    a = ctx.run("s10_threads4", lambda: stroom.run(_argv(bron, True), bron["pad"], grenzen=GRENZEN))
    ka = _kort(a, media)
    stap.check(
        "1080 veryfast, -threads 4, alle grenzen",
        a["rc"] == 0 and ka["fragmenten"] == 4,
        "rc 0, 4 fragmenten (8 s)",
        ka,
    )
    gebruikt = dict(GRENZEN)
    if a["rc"] != 0:
        # welke grens breekt het? eerst zonder FSIZE, dan DATA naar 2.000 MB (§3.7)
        zonder_fsize = {k: v for k, v in GRENZEN.items() if k != "RLIMIT_FSIZE"}
        r = stroom.run(_argv(bron, True), bron["pad"], grenzen=zonder_fsize)
        stap.check("zonder RLIMIT_FSIZE", r["rc"] == 0, "rc 0", _kort(r, media))
        if r["rc"] == 0:
            gebruikt = zonder_fsize
        else:
            meer = dict(zonder_fsize, RLIMIT_DATA=2000 * MB)
            r = stroom.run(_argv(bron, True), bron["pad"], grenzen=meer)
            stap.check("RLIMIT_DATA 2.000 MB", r["rc"] == 0, "rc 0", _kort(r, media))
            gebruikt = meer if r["rc"] == 0 else gebruikt
    b = ctx.run(
        "s10_standaard", lambda: stroom.run(_argv(bron, False), bron["pad"], grenzen=gebruikt)
    )
    kb = _kort(b, media)
    stap.check("1080 veryfast, standaard threads, dezelfde grenzen", b["rc"] == 0, "rc 0", kb)
    if a["rc"] == 0 and b["rc"] == 0:
        verhouding = round(a["cpu_s"] / b["cpu_s"], 3) if b["cpu_s"] else None
        stap.check(
            "-threads 4 kost niet meer CPU dan de standaard",
            verhouding is not None and verhouding <= 1.10,
            "CPU met -threads 4 / CPU standaard ≤ 1,10",
            f"{verhouding} (cores voor 1,0×: {ka['cores_voor_1x']} tegen {kb['cores_voor_1x']};"
            f" snelheid {ka['snelheid']}× tegen {kb['snelheid']}×; RSS {ka['max_rss_mb']} tegen"
            f" {kb['max_rss_mb']} MB)",
        )
    # bijt RLIMIT_FSIZE echt? ffmpeg schrijft één beeld naar een bestand
    pad = os.path.join(ctx.map, "fsize.mkv")
    f = voer(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "testsrc2=size=320x240:rate=24", "-frames:v", "24", "-c:v", "libx264",
            "-preset", "ultrafast", pad,
        ],
        grenzen={"RLIMIT_FSIZE": 0},
    )  # fmt: skip
    groot = os.path.getsize(pad) if os.path.exists(pad) else None
    stap.check(
        "controle: RLIMIT_FSIZE 0 laat ffmpeg geen bestand schrijven",
        f["rc"] != 0 and not groot,
        "rc ≠ 0 (SIGXFSZ of EFBIG), bestand leeg",
        f"rc {f['rc']}, signaal {f['signaal']}, bestand {groot} bytes, {f['stderr'][-150:]}",
    )
    weinig = dict(gebruikt, RLIMIT_DATA=64 * MB)
    c = stroom.run(_argv(bron, True), bron["pad"], grenzen=weinig, deadline_s=60)
    stap.check(
        "controle: RLIMIT_DATA 64 MB is te weinig",
        c["rc"] != 0,
        "rc ≠ 0 (malloc faalt)",
        _kort(c, media),
    )
    stap.gegevens.update({"grenzen": gebruikt, "threads4": ka, "standaard": kb})
    stap.gemeten = (
        f"-threads 4: rc {ka['rc']}, {ka['cores_voor_1x']} cores voor 1,0×, RSS"
        f" {ka['max_rss_mb']} MB; standaard: rc {kb['rc']}, {kb['cores_voor_1x']} cores, RSS"
        f" {kb['max_rss_mb']} MB"
    )
    besluit = []
    if "RLIMIT_FSIZE" in gebruikt and a["rc"] == 0:
        besluit.append("RLIMIT_FSIZE 0 gaat aan")
    else:
        besluit.append("zonder RLIMIT_FSIZE")
        stap.terugval = "S10: zonder RLIMIT_FSIZE; de rest blijft"
    if gebruikt.get("RLIMIT_DATA") == 2000 * MB:
        besluit.append("RLIMIT_DATA voor live wordt 2.000 MB")
        stap.terugval = (stap.terugval or "S10") + "; RLIMIT_DATA live 2.000 MB"
    elif a["rc"] == 0:
        besluit.append("RLIMIT_DATA 1.200 MB voor live blijft")
    threads = [c for c in stap.checks if c["naam"].startswith("-threads 4 kost niet meer CPU")]
    if not threads:
        besluit.append("de kosten van -threads 4 zijn niet gemeten (een run faalde)")
    elif threads[0]["ok"]:
        besluit.append("-threads 4 blijft de bovengrens per proces")
    else:
        besluit.append("-threads 4 kost meer dan de standaard: de toelating rekent met deze kosten")
    stap.besluit = "; ".join(besluit) + " (Keuze 12)."
    return stap
