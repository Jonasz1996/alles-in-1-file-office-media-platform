"""S1: wat de ffmpeg van het image kan.

Versie, encoders, filters, protocollen `fd` en `pipe`, `-format_whitelist`, de opties van §3.3,
`nice` en `ionice`. De witte lijsten en de keten van de tone-mapping (Keuze 27) worden ook echt
gedraaid, niet alleen opgezocht.
"""

from __future__ import annotations

import os
import shutil
import stat

import commandos
from gedeeld import Stap, ffmpeg_versie, uitvoer
from gedeeld import draai as voer

ENCODERS = ("libx264", "aac", "libopus", "libvpx-vp9", "mjpeg")
ENCODERS_EXTRA = ("libx265", "flac")  # bench (f_dec) en remux_audio
FILTERS = ("zscale", "tonemap", "bwdif", "overlay", "tile")
FILTERS_EXTRA = ("scale", "format", "fps", "noise", "testsrc2", "sine", "cellauto", "setparams")
DEMUXERS = tuple(commandos.FORMATEN.split(",")) + ("lavfi", "hevc")
MUXERS = ("mp4", "matroska", "webvtt", "ass", "srt", "image2pipe", "hevc", "null")
OPTIES = ("-fpsmax", "-max_alloc", "max_pixels", "format_whitelist", "protocol_whitelist")


def _lijst(argv: list[str], kolom_vlaggen: int = 1) -> set[str]:
    """Namen uit `ffmpeg -encoders`, `-filters`, `-demuxers`, `-muxers` (na de kop `--`)."""
    rc, tekst = uitvoer(argv)
    namen: set[str] = set()
    if rc != 0:
        return namen
    regels = tekst.splitlines()
    # -encoders, -demuxers en -muxers hebben een kop tot een regel met streepjes; -filters niet,
    # daar herken je een filter aan de kolom met "->"
    kop = next((i for i, r in enumerate(regels) if r.strip().startswith("--")), None)
    for i, regel in enumerate(regels):
        delen = regel.split()
        if len(delen) <= kolom_vlaggen:
            continue
        if kop is not None and i > kop or kop is None and len(delen) > 2 and "->" in delen[2]:
            for naam in delen[kolom_vlaggen].split(","):
                namen.add(naam)
    return namen


def _protocollen() -> tuple[set[str], set[str]]:
    rc, tekst = uitvoer(["ffmpeg", "-hide_banner", "-protocols"])
    invoer, uit, deel = set(), set(), None
    for regel in tekst.splitlines() if rc == 0 else []:
        r = regel.strip()
        if r.startswith("Input"):
            deel = invoer
        elif r.startswith("Output"):
            deel = uit
        elif deel is not None and r:
            deel.add(r)
    return invoer, uit


def _modus(pad: str | None) -> str | None:
    if not pad:
        return None
    try:
        st = os.stat(pad)
    except OSError:
        return None
    return f"{stat.S_IMODE(st.st_mode):04o} uid {st.st_uid}"


def draai(ctx) -> Stap:
    stap = Stap(
        "S1",
        "ffmpeg in het image: versie, encoders, filters, protocollen, witte lijsten, nice/ionice",
        "of Keuze 27 tone-mapping heeft; de lijst van de selftest",
    )
    stap.verwacht = (
        "ffmpeg 7.1.x (Debian 13); encoders libx264, aac, libopus, libvpx-vp9, mjpeg; filters"
        " zscale, tonemap, bwdif, overlay, tile; protocollen fd en pipe; -format_whitelist en"
        " -protocol_whitelist weigeren wat er niet op staat; de tone-mapping van Keuze 27 draait;"
        " nice en ionice werken zonder rechten."
    )
    versie = ffmpeg_versie()
    rc, probe_tekst = uitvoer(["ffprobe", "-hide_banner", "-version"])
    probe_versie = probe_tekst.split()[2] if rc == 0 and len(probe_tekst.split()) > 2 else None
    rc, dpkg = uitvoer(["dpkg-query", "-W", "-f", "${Version}", "ffmpeg"])
    stap.gegevens["dpkg_ffmpeg"] = dpkg.strip() if rc == 0 else None
    stap.check(
        "versie",
        bool(versie) and versie.startswith("7.1") and probe_versie == versie,
        "ffmpeg en ffprobe 7.1.x",
        f"ffmpeg {versie}, ffprobe {probe_versie}, pakket {stap.gegevens['dpkg_ffmpeg']}",
    )
    stap.gegevens["modus"] = {
        "ffmpeg": _modus(shutil.which("ffmpeg")),
        "ffprobe": _modus(shutil.which("ffprobe")),
    }

    encoders = _lijst(["ffmpeg", "-hide_banner", "-encoders"])
    mist = [e for e in ENCODERS if e not in encoders]
    stap.check("encoders", not mist, list(ENCODERS), f"ontbreekt: {mist}" if mist else "alle")
    mist = [e for e in ENCODERS_EXTRA if e not in encoders]
    stap.check(
        "encoders voor bench en remux_audio",
        not mist,
        list(ENCODERS_EXTRA),
        f"ontbreekt: {mist}" if mist else "alle",
    )
    filters = _lijst(["ffmpeg", "-hide_banner", "-filters"])
    mist = [f for f in FILTERS if f not in filters]
    stap.check("filters", not mist, list(FILTERS), f"ontbreekt: {mist}" if mist else "alle")
    mist = [f for f in FILTERS_EXTRA if f not in filters]
    stap.check(
        "filters voor schalen, bench en synth",
        not mist,
        list(FILTERS_EXTRA),
        f"ontbreekt: {mist}" if mist else "alle",
    )
    invoer, _ = _protocollen()
    stap.check(
        "protocollen fd en pipe",
        {"fd", "pipe"} <= invoer,
        ["fd", "pipe"],
        sorted(invoer & {"fd", "pipe", "file", "http", "concat", "unix"}),
    )
    demuxers = _lijst(["ffmpeg", "-hide_banner", "-demuxers"])
    mist = [d for d in DEMUXERS if d not in demuxers]
    stap.check(
        "demuxers van de witte lijst",
        not mist,
        list(DEMUXERS),
        f"ontbreekt: {mist}" if mist else "alle",
    )
    muxers = _lijst(["ffmpeg", "-hide_banner", "-muxers"])
    mist = [m for m in MUXERS if m not in muxers]
    stap.check("muxers", not mist, list(MUXERS), f"ontbreekt: {mist}" if mist else "alle")
    rc, hulp = uitvoer(["ffmpeg", "-hide_banner", "-h", "full"])
    rc2, hulp264 = uitvoer(["ffmpeg", "-hide_banner", "-h", "encoder=libx264"])
    mist = [o for o in OPTIES if o not in hulp] + (
        [] if "forced-idr" in hulp264 else ["forced-idr"]
    )
    stap.check(
        "opties van §3.3",
        not mist,
        [*OPTIES, "forced-idr"],
        f"ontbreekt: {mist}" if mist else "alle",
    )
    rc, hw = uitvoer(["ffmpeg", "-hide_banner", "-hwaccels"])
    stap.gegevens["hwaccels"] = [r.strip() for r in hw.splitlines()[1:] if r.strip()]
    stap.gegevens["dev_dri"] = sorted(os.listdir("/dev/dri")) if os.path.isdir("/dev/dri") else []

    _witte_lijsten(stap, ctx)
    tonemap = _tonemap(stap)
    _deinterlace(stap)

    for naam, argv in (
        ("nice -n 5", ["nice", "-n", "5", "true"]),
        ("nice -n 15 ionice -c 3", ["nice", "-n", "15", "ionice", "-c", "3", "true"]),
    ):
        rc, tekst = uitvoer(argv)
        stap.check(naam, rc == 0, "rc 0 zonder rechten", f"rc {rc} {tekst.strip()[:200]}")

    zelftest = {
        "encoders": [e for e in ENCODERS + ENCODERS_EXTRA if e in encoders],
        "filters": [f for f in FILTERS if f in filters],
        "protocollen": sorted({"fd", "pipe"} & invoer),
        "tone_mapping": tonemap,
    }
    stap.gegevens["selftest"] = zelftest
    stap.gemeten = (
        f"ffmpeg {versie}; tone-mapping {'werkt' if tonemap else 'werkt niet'};"
        f" anders: {stap.anders() or 'niets'}"
    )
    besluit = [
        "Keuze 27 zoals ontworpen (zscale + tonemap)"
        if tonemap
        else "Keuze 27 zonder tone-mapping: 422 media_hdr met can_raw: true"
    ]
    besluit.append(
        "de selftest eist: encoders "
        + ", ".join(zelftest["encoders"])
        + "; filters "
        + ", ".join(zelftest["filters"])
        + "; protocollen fd en pipe; een weigering door -format_whitelist en -protocol_whitelist"
    )
    if versie and not versie.startswith("7.1"):
        besluit.append(f"let op: dit was ffmpeg {versie}, niet die van het image")
    stap.besluit = "; ".join(besluit)
    if not tonemap:
        stap.terugval = "Keuze 27: 'Ontbreekt de tone-mapping' (422 media_hdr, can_raw: true)"
    return stap


def _witte_lijsten(stap: Stap, ctx) -> None:
    klein = ctx.bron("klein.mkv")
    if "fout" in klein:
        stap.check("witte lijsten", False, "een kleine mkv", klein["fout"][-300:])
        return
    basis = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    uit = ["-frames:v", "1", "-f", "null", "-"]
    goed = voer(["ffmpeg", *commandos.invoer("matroska"), *uit], bron=klein["pad"])
    fmt = voer(
        [
            *basis, "-protocol_whitelist", "fd,pipe", "-format_whitelist", "mov",
            "-f", "matroska", "-fd", "3", "-i", "fd:", *uit,
        ],
        bron=klein["pad"],
    )  # fmt: skip
    prot = voer([*basis, "-protocol_whitelist", "fd,pipe", "-i", f"file:{klein['pad']}", *uit])
    stap.check(
        "invoer via fd: met de witte lijsten",
        goed["rc"] == 0,
        "rc 0",
        f"rc {goed['rc']} {goed['stderr'][-200:]}",
    )
    stap.check(
        "-format_whitelist weigert een demuxer die er niet op staat",
        fmt["rc"] != 0 and "whitelist" in fmt["stderr"].lower(),
        "fout 'not on whitelist'",
        f"rc {fmt['rc']} {fmt['stderr'][-200:]}",
    )
    stap.check(
        "-protocol_whitelist weigert file:",
        prot["rc"] != 0 and "whitelist" in prot["stderr"].lower(),
        "fout 'not on whitelist'",
        f"rc {prot['rc']} {prot['stderr'][-200:]}",
    )


def _tonemap(stap: Stap) -> bool:
    """De keten van Keuze 27 op een paar beelden 10-bit PQ en HLG uit lavfi."""
    goed = True
    for tin, trc in (("pq", "smpte2084"), ("hlg", "arib-std-b67")):
        bron = (
            "testsrc2=size=640x360:rate=24,format=yuv420p10le,"
            f"setparams=color_primaries=bt2020:color_trc={trc}:colorspace=bt2020nc:range=tv"
        )
        vf = commandos.TONEMAP.format(W=320, H=180, tin="arib-std-b67" if tin == "hlg" else trc)
        r = voer(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", bron, "-frames:v", "3", "-vf", vf, "-f", "null", "-",
            ]
        )  # fmt: skip
        goed &= stap.check(
            f"tone-mapping {tin} (Keuze 27)",
            r["rc"] == 0,
            "rc 0",
            f"rc {r['rc']} {r['stderr'][-200:]}",
        )
    return goed


def _deinterlace(stap: Stap) -> None:
    r = voer(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc2=size=320x240:rate=25,setfield=tff", "-frames:v", "5",
            "-vf", f"{commandos.DEINTERLACE},format=yuv420p", "-f", "null", "-",
        ]
    )  # fmt: skip
    stap.check("bwdif (interlaced)", r["rc"] == 0, "rc 0", f"rc {r['rc']} {r['stderr'][-200:]}")
