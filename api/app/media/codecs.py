"""Codec-strings (RFC 6381) voor de browser (Keuze 16). M3 bouwt de echte lezers van avcC, hvcC,
av1C, vpcC, dvcC en Annex-B; dit is de stub van M0: alleen wat ffprobe al weet (profiel, level,
bitdiepte). Pure functies zonder I/O: de module draait in de worker, want de extradata zijn bytes
van een gebruiker (regel 5).

    strings(stream, extradata) -> "avc1.64001f" | None

stream is de ruwe stream van ffprobe (een dict), extradata de bytes uit -show_data (of None).
"""

from __future__ import annotations

from typing import Any

_AVC_PROFILE = {
    "baseline": (0x42, 0x00),
    "constrained baseline": (0x42, 0xE0),
    "main": (0x4D, 0x40),
    "extended": (0x58, 0x00),
    "high": (0x64, 0x00),
    "high 10": (0x6E, 0x00),
    "high 10 intra": (0x6E, 0x10),
    "high 4:2:2": (0x7A, 0x00),
    "high 4:4:4 predictive": (0xF4, 0x00),
}
_AAC = {"lc": "mp4a.40.2", "he-aac": "mp4a.40.5", "he-aacv2": "mp4a.40.29", "main": "mp4a.40.1"}
_AUDIO = {
    "opus": "opus",
    "flac": "flac",
    "ac3": "ac-3",
    "eac3": "ec-3",
    "mp3": "mp3",
    "vorbis": "vorbis",
    "alac": "alac",
}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _depth(stream: dict) -> int:
    raw = _int(stream.get("bits_per_raw_sample"))
    if raw:
        return raw
    pix = str(stream.get("pix_fmt") or "")
    for depth in (12, 10):
        if f"p{depth}" in pix:
            return depth
    return 8


def _dovi(stream: dict) -> dict | None:
    for side in stream.get("side_data_list") or []:
        if isinstance(side, dict) and "DOVI" in str(side.get("side_data_type", "")):
            return side
    return None


def strings(stream: dict, extradata: bytes | None = None) -> str | None:
    """De codec-string van één stream, of None als de stub hem niet kan maken."""
    codec = str(stream.get("codec_name") or "").lower()
    profile = str(stream.get("profile") or "").lower()
    level = _int(stream.get("level"))
    if codec == "h264":
        if extradata and len(extradata) >= 4 and extradata[0] == 1:  # avcC
            return f"avc1.{extradata[1]:02x}{extradata[2]:02x}{extradata[3]:02x}"
        if profile not in _AVC_PROFILE or level is None or level <= 0:
            return None
        idc, flags = _AVC_PROFILE[profile]
        return f"avc1.{idc:02x}{flags:02x}{level:02x}"
    if codec == "hevc":
        dv = _dovi(stream)
        if dv and _int(dv.get("dv_profile")) in (5, 8):
            return f"dvh1.{_int(dv.get('dv_profile')):02d}.{_int(dv.get('dv_level')) or 0:02d}"
        if level is None or level <= 0:
            return None
        if profile == "main":
            return f"hvc1.1.6.L{level}.B0"
        if profile == "main 10":
            return f"hvc1.2.4.L{level}.B0"
        return None
    if codec == "vp9":
        number = {"profile 0": 0, "profile 1": 1, "profile 2": 2, "profile 3": 3}.get(profile, 0)
        return f"vp09.{number:02d}.40.{_depth(stream):02d}"
    if codec == "av1":
        number = {"main": 0, "high": 1, "professional": 2}.get(profile, 0)
        lvl = level if level is not None and 0 <= level < 32 else 8
        return f"av01.{number}.{lvl:02d}M.{_depth(stream):02d}"
    if codec == "aac":
        return _AAC.get(profile, "mp4a.40.2")
    return _AUDIO.get(codec)
