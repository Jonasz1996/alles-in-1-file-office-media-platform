"""caps: wat de worker kan (Keuze 12). Eén keer gemeten bij de start (gather), daarna uit de
cache: de opdracht start geen proces en telt niet mee in de plaatsen.

Spike punt 7: ffmpeg 7.1 noemt lavfi alleen onder -devices, niet onder -demuxers. Dus lavfi uit
-devices, de rest uit -demuxers. lavfi staat nooit in de witte lijst voor een invoer-fd; alleen
synth en bench gebruiken het.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from app.media import wire
from app.media.worker.args import DEMUXERS

# Wat de commando's van §3.3 nodig hebben
ENCODERS = ("libx264", "libvpx-vp9", "libx265", "aac", "libopus", "flac", "mjpeg", "srt")
FILTERS = (
    "scale", "format", "fps", "tile", "overlay", "bwdif", "zscale", "tonemap", "noise",
    "testsrc2", "cellauto", "sine",
)  # fmt: skip
PROTOCOLS = ("fd", "pipe")
DEVICES = ("lavfi",)
EXTRA_DEMUXERS = ("hevc",)  # bench: HEVC10 decoderen uit een pipe
HW = re.compile(r"(vaapi|qsv|nvenc|v4l2m2m|videotoolbox)")
LIST_TIMEOUT = 10


def _out(argv: list[str]) -> str | None:
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            timeout=LIST_TIMEOUT,
            check=False,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace")


def _version(text: str | None) -> str | None:
    if not text:
        return None
    m = re.match(r"\S+ version (\S+)", text)
    return m.group(1) if m else "onbekend"


def _names(text: str | None) -> set[str]:
    """De namen uit een lijst van ffmpeg (-encoders, -filters, -demuxers, -devices): elke
    ingesprongen regel "<vlaggen> <naam> …", zonder de uitleg van de vlaggen ("V..... = Video")
    en de scheidingslijn; komma's splitsen (matroska,webm)."""
    names: set[str] = set()
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] == "=" or not line[:1].isspace():
            continue
        names.update(n for n in parts[1].split(",") if n)
    return names


def _protocols(text: str | None) -> set[str]:
    """De invoerprotocollen (het blok tussen "Input:" en "Output:")."""
    names: set[str] = set()
    if not text:
        return names
    inside = False
    for line in text.splitlines():
        if line.strip() == "Input:":
            inside = True
        elif line.strip() == "Output:":
            break
        elif inside and line.strip():
            names.add(line.strip())
    return names


def _hwaccels(text: str | None) -> list[str]:
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines[1:] if line and " " not in line]


def gather(*, local: bool, max_procs: int, max_transcodes: int) -> dict[str, Any]:
    """Alles één keer, bij de start, na elkaar (nooit meer dan één ffmpeg tegelijk)."""
    ffmpeg = _out(["ffmpeg", "-hide_banner", "-version"])
    ffprobe = _out(["ffprobe", "-hide_banner", "-version"])
    caps: dict[str, Any] = {
        "protocol": wire.PROTOCOL,
        "ffmpeg": _version(ffmpeg),
        "ffprobe": _version(ffprobe),
        "mode": "local" if local else "container",
        # Zonder --local is de worker bedoeld voor de container, maar dat bewijst niets: "on"
        # zegt pas de zelftest van Keuze 11 (M1: andere uid dan de app, geen schrijfrecht op de
        # blobs, geen netwerk, NoNewPrivs en seccomp). Tot dan "unchecked" (review S6)
        "sandbox": "off" if local else "unchecked",
        "max_procs": max_procs,
        "max_transcodes": max_transcodes,
        "dri": os.path.isdir("/dev/dri"),
    }
    if ffmpeg is None:
        caps.update(encoders=[], filters=[], demuxers=[], protocols=[], hwaccels=[])
        caps["missing"] = ["ffmpeg"] + ([] if ffprobe else ["ffprobe"])
        return caps
    encoders = _names(_out(["ffmpeg", "-hide_banner", "-encoders"]))
    filters = _names(_out(["ffmpeg", "-hide_banner", "-filters"]))
    demuxers = _names(_out(["ffmpeg", "-hide_banner", "-demuxers"]))
    devices = _names(_out(["ffmpeg", "-hide_banner", "-devices"]))
    protocols = _protocols(_out(["ffmpeg", "-hide_banner", "-protocols"]))
    hwaccels = _hwaccels(_out(["ffmpeg", "-hide_banner", "-hwaccels"]))
    have_demuxers = [d for d in (*DEMUXERS, *EXTRA_DEMUXERS) if d in demuxers]
    have_devices = [d for d in DEVICES if d in devices or d in demuxers]
    missing = [
        *(e for e in ENCODERS if e not in encoders),
        *(f for f in FILTERS if f not in filters),
        *(p for p in PROTOCOLS if p not in protocols),
        *(d for d in (*DEMUXERS, *EXTRA_DEMUXERS) if d not in demuxers),
        *(d for d in DEVICES if d not in have_devices),
    ]
    if ffprobe is None:
        missing.insert(0, "ffprobe")
    caps.update(
        encoders=sorted({e for e in encoders if e in ENCODERS or HW.search(e)}),
        filters=[f for f in FILTERS if f in filters],
        demuxers=have_demuxers + have_devices,
        protocols=[p for p in PROTOCOLS if p in protocols],
        hwaccels=hwaccels,
        missing=missing,
    )
    return caps


async def run(ctx) -> dict[str, Any]:
    """Uit de cache, met de plaatsen van nu."""
    return {**ctx.worker.caps, **ctx.worker.places.snapshot()}
