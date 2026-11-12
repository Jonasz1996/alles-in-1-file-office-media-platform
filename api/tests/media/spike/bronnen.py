"""De bronnen van de spike: klein, gemaakt uit `lavfi` (`testsrc2`, `sine`), in een tijdelijke map.

Er staan geen mediabestanden in het repo. Elke bron wordt één keer gemaakt (één ffmpeg tegelijk)
en daarna met ffprobe beschreven: `start_time` (S), de streams en de pts van elk videopakket,
met de keyframes. Dat is de brontijd waar S2 tegen meet.
"""

from __future__ import annotations

import json
import os

from boxen import boxen
from gedeeld import draai, log, uitvoer

_SINE = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
_AAC = ["-c:a", "aac", "-b:a", "128k"]

# naam → (demuxer voor -f, argv tussen "ffmpeg -nostdin -y" en het pad)
BRONNEN: dict[str, tuple[str, list[str]]] = {
    # H.264 High met B-frames, een GOP van 2 s, 12 s, AAC
    "bframes.mkv": ("matroska", [
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24", *_SINE, "-t", "12",
        "-c:v", "libx264", "-preset", "veryfast", "-g", "48", "-bf", "3", "-pix_fmt", "yuv420p",
        *_AAC,
    ]),
    "fps23976.mkv": ("matroska", [
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24000/1001", *_SINE, "-t", "12",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", *_AAC,
    ]),
    # interlaced (tff) in MPEG-TS: start_time is dan ±1,4 s, dus 1 − S is negatief. Een GOP van
    # 1,4 s, niet op het rooster van 2 s (zoals een opname)
    "interlaced.ts": ("mpegts", [
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", *_SINE, "-t", "12",
        "-c:v", "libx264", "-preset", "veryfast", "-g", "35", "-x264-params", "interlaced=1:tff=1",
        "-pix_fmt", "yuv420p", *_AAC, "-f", "mpegts",
    ]),
    # 240 fps zonder audio, mp4 met de moov achteraan (zo schrijft de muxer een gewoon bestand)
    "fps240.mp4": ("mov", [
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=240", "-t", "6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-f", "mp4",
    ]),
    # HEVC met B-frames voor de remux (V4), een GOP van 2 s
    "hevc.mkv": ("matroska", [
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24", *_SINE, "-t", "10",
        "-c:v", "libx265", "-preset", "ultrafast",
        "-x265-params", "log-level=error:keyint=48:min-keyint=48:scenecut=0",
        "-pix_fmt", "yuv420p", *_AAC,
    ]),
    # 1080p24 H.264 voor S10
    "src1080.mkv": ("matroska", [
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24", *_SINE, "-t", "8",
        "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "6M", "-pix_fmt", "yuv420p", *_AAC,
    ]),
    # klein, voor de witte lijsten van S1
    "klein.mkv": ("matroska", [
        "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-t", "1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    ]),
}  # fmt: skip


def maak(map_: str, naam: str) -> dict:
    """Maak de bron (als dat nog niet gebeurde) en beschrijf hem."""
    demuxer, argv = BRONNEN[naam]
    pad = os.path.join(map_, naam)
    uit = draai(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *argv, pad],
        deadline_s=180,
    )
    log(f"  bron {naam}: rc {uit['rc']}, {uit['wand_s']} s")
    info = {"naam": naam, "pad": pad, "demuxer": demuxer, "maken": uit}
    if uit["rc"] != 0 or not os.path.exists(pad):
        info["fout"] = uit["stderr"][-500:]
        return info
    info.update(beschrijf(pad))
    return info


def beschrijf(pad: str) -> dict:
    """ffprobe op het pad (de spike mag dat; de worker nooit): S, streams, pts per videopakket."""
    rc, tekst = uitvoer(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", pad]
    )
    if rc != 0:
        return {"fout": tekst[-500:]}
    data = json.loads(tekst)
    fmt = data.get("format", {})
    streams = []
    video = audio = None
    for s in data.get("streams", []):
        streams.append(
            {
                "index": s.get("index"),
                "type": s.get("codec_type"),
                "codec": s.get("codec_name"),
                "fps": s.get("r_frame_rate"),
                "field_order": s.get("field_order"),
                "start_time": s.get("start_time"),
            }
        )
        if s.get("codec_type") == "video" and video is None:
            video = s.get("index")
        if s.get("codec_type") == "audio" and audio is None:
            audio = s.get("index")
    rc, tekst = uitvoer(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", pad,
        ]
    )  # fmt: skip
    pts, sleutels = [], []
    for regel in tekst.splitlines() if rc == 0 else []:
        delen = regel.strip().split(",")
        if len(delen) >= 2 and delen[0] not in ("", "N/A"):
            t = float(delen[0])
            pts.append(t)
            if "K" in delen[1]:
                sleutels.append(t)
    pts.sort()
    sleutels.sort()
    fps = None
    for s in streams:
        if s["index"] == video and s["fps"] and "/" in s["fps"]:
            teller, noemer = s["fps"].split("/")
            fps = int(teller) / int(noemer) if int(noemer) else None
    with open(pad, "rb") as f:
        inhoud = f.read()
    return {
        "start_s": float(fmt.get("start_time") or 0.0),
        "duur_s": float(fmt.get("duration") or 0.0),
        "format": fmt.get("format_name"),
        "streams": streams,
        "video": video,
        "audio": audio,
        "fps": fps,
        "pts": pts,
        "sleutels": sleutels,
        "grootte": len(inhoud),
        "matroska_cues": inhoud.find(b"\x1c\x53\xbb\x6b") >= 0,
        "mp4_volgorde": _mp4_volgorde(inhoud) if inhoud[4:8] == b"ftyp" else None,
    }


def _mp4_volgorde(inhoud: bytes) -> list[str]:
    return [soort for soort, *_ in boxen(inhoud)][:8]


def openbaar(info: dict) -> dict:
    """Wat er van een bron in spike.json komt (zonder de lijst van alle pts)."""
    weg = {"pts", "sleutels", "pad", "maken"}
    uit = {k: v for k, v in info.items() if k not in weg}
    uit["keyframes_s"] = info.get("sleutels", [])[:20]
    uit["pakketten"] = len(info.get("pts", []))
    uit["maken_s"] = info.get("maken", {}).get("wand_s")
    return uit
