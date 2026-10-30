"""Codec-strings (RFC 6381) voor de browser (Keuze 16).

De browser test strings (`isTypeSupported`, `canPlayType`, `decodingInfo`). ffprobe geeft ze
niet, en profiel en level alleen zijn niet genoeg voor `hvc1` en `av01`. Deze lezers maken ze uit
de configuratie van de stream (de extradata uit -show_data):

- H.264: `avc1.PPCCLL` uit de `avcC`;
- HEVC: `hvc1.<ruimte><profiel>.<compat>.<tier><level>.<constraints>` uit de `hvcC` (in mkv de
  CodecPrivate, dezelfde bytes);
- Annex-B (MPEG-TS, m2ts, soms mkv): de extradata beginnen met `00 00 01` of `00 00 00 01`. Dan
  komen profiel, constraints en level uit de eerste SPS (H.264), of uit de SPS en anders de VPS
  (HEVC), na het weghalen van de emulatiebytes (`00 00 03`);
- AV1: `av01.<profiel>.<level><tier>.<bitdiepte>` uit de `av1C`;
- VP9: `vp09.<profiel>.<level>.<bitdiepte>` uit de `vpcC` (of de lijst van kenmerken in de
  CodecPrivate van Matroska), anders uit ffprobe. ffmpeg 6.1 en 7.1 geven voor VP9 geen
  extradata; het level komt dan uit de grootte en de fps (de tabel van de VP9-specificatie,
  bijlage A);
- Dolby Vision profiel 5 (geen bruikbare basislaag): `dvh1.<profiel>.<level>`. ffmpeg leest de
  `dvcC`/`dvvC` en zet profiel en level in het DOVI-record van de stream; `dovi_record` leest
  dezelfde bytes zelf. Profiel 7 en 8 krijgen de string van hun basislaag (HEVC), want die speelt
  elke HEVC-client (Keuze 15);
- audio: `mp4a.40.2`, `mp4a.40.5`, `mp4a.40.29`, `opus`, `flac`, `ac-3`, `ec-3`, `mp3`, …

Zonder bruikbare extradata valt de lezer terug op wat ffprobe weet (profiel, level, bitdiepte).

Pure functies zonder I/O: de module draait in de worker, want de extradata zijn bytes van een
gebruiker (regel 5). Elke lezer is begrensd (hooguit MAX_BYTES, hooguit MAX_NALS units) en gooit
nooit: kapotte bytes geven de terugval of None (fuzz in test_media_codecs.py, regel 10).

    strings(stream, extradata) -> "avc1.64001f" | None
"""

from __future__ import annotations

import math
from typing import Any

MAX_BYTES = 64 * 1024  # meer extradata lezen we niet
MAX_NALS = 64  # zoveel NAL-units in Annex-B, dan stoppen

_AVC_PROFILE = {
    "baseline": (0x42, 0x00),
    "constrained baseline": (0x42, 0xE0),
    "main": (0x4D, 0x40),
    "extended": (0x58, 0x00),
    "high": (0x64, 0x00),
    "high 10": (0x6E, 0x00),
    "high 10 intra": (0x6E, 0x10),
    "high 4:2:2": (0x7A, 0x00),
    "high 4:2:2 intra": (0x7A, 0x10),
    "high 4:4:4 predictive": (0xF4, 0x00),
    "high 4:4:4 intra": (0xF4, 0x10),
}
# HEVC zonder extradata: profiel → (profile_idc, de compatibiliteit zoals in de string)
_HEVC_PROFILE = {"main": (1, "6"), "main 10": (2, "4"), "main still picture": (3, "8")}
_AAC = {
    "lc": "mp4a.40.2",
    "he-aac": "mp4a.40.5",
    "he-aacv2": "mp4a.40.29",
    "main": "mp4a.40.1",
    "ltp": "mp4a.40.4",
}
_AUDIO = {
    "opus": "opus",
    "flac": "flac",
    "ac3": "ac-3",
    "eac3": "ec-3",
    "mp3": "mp3",
    "vorbis": "vorbis",
    "alac": "alac",
}
_AV1_PROFILE = {"main": 0, "high": 1, "professional": 2}
_VP9_PROFILE = {"profile 0": 0, "profile 1": 1, "profile 2": 2, "profile 3": 3}
# VP9-levels (bijlage A): (level, hoogste luma-samples per seconde, grootste beeld in samples)
_VP9_LEVELS = (
    (10, 829_440, 36_864),
    (11, 2_764_800, 73_728),
    (20, 4_608_000, 122_880),
    (21, 9_216_000, 245_760),
    (30, 20_736_000, 552_960),
    (31, 36_864_000, 983_040),
    (40, 83_558_400, 2_228_224),
    (41, 160_432_128, 2_228_224),
    (50, 311_951_360, 8_912_896),
    (51, 588_251_136, 8_912_896),
    (52, 1_176_502_272, 8_912_896),
    (60, 1_176_502_272, 35_651_584),
    (61, 2_353_004_544, 35_651_584),
    (62, 4_706_009_088, 35_651_584),
)
_VP9_LEVEL_IDS = frozenset(level for level, _, _ in _VP9_LEVELS)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _rate(value: Any) -> float | None:
    """Een rate als "24000/1001" of "25" als getal; 0/0 en onzin geven None."""
    text = str(value or "")
    if "/" in text:
        num, _, den = text.partition("/")
        a, b = _float(num), _float(den)
        return a / b if a is not None and b else None
    return _float(text)


def _depth(stream: dict) -> int:
    raw = _int(stream.get("bits_per_raw_sample"))
    if raw in (8, 10, 12):
        return raw
    pix = str(stream.get("pix_fmt") or "")
    for depth in (12, 10):
        if f"p{depth}" in pix:
            return depth
    return 8


# --- Annex-B -----------------------------------------------------------------------------------


def is_annexb(data: bytes) -> bool:
    return data[:3] == b"\x00\x00\x01" or data[:4] == b"\x00\x00\x00\x01"


def nal_units(data: bytes) -> list[bytes]:
    """De NAL-units van een Annex-B-stroom (met hun kop), hooguit MAX_NALS."""
    data = data[:MAX_BYTES]
    out: list[bytes] = []
    start = -1
    i = data.find(b"\x00\x00\x01")
    while i >= 0 and len(out) < MAX_NALS:
        if start >= 0:
            out.append(data[start:i].rstrip(b"\x00"))
        start = i + 3
        i = data.find(b"\x00\x00\x01", start)
    if start >= 0 and len(out) < MAX_NALS:
        out.append(data[start:])
    return [u for u in out if u]


def unescape(data: bytes, limit: int = 64) -> bytes:
    """De eerste `limit` bytes van een RBSP: elke 03 na 00 00 eruit (emulation prevention)."""
    out = bytearray()
    zeros = 0
    for byte in data[: limit * 2]:
        if len(out) >= limit:
            break
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


# --- H.264 -------------------------------------------------------------------------------------


def _avc(profile: int, flags: int, level: int) -> str | None:
    if not profile or not level:
        return None
    return f"avc1.{profile:02x}{flags:02x}{level:02x}"


def avc_from_avcc(data: bytes) -> str | None:
    """avcC: configurationVersion 1, dan profile_idc, de constraint-vlaggen en level_idc."""
    if len(data) < 4 or data[0] != 1:
        return None
    return _avc(data[1], data[2], data[3])


def avc_from_annexb(data: bytes) -> str | None:
    """De eerste SPS (nal_unit_type 7): na de kop van één byte profiel, vlaggen en level."""
    for unit in nal_units(data):
        if unit[0] & 0x1F == 7:
            rbsp = unescape(unit[1:], 3)
            return _avc(*rbsp) if len(rbsp) == 3 else None
    return None


# --- HEVC --------------------------------------------------------------------------------------


def _reverse32(value: int) -> int:
    return int(f"{value & 0xFFFFFFFF:032b}"[::-1], 2)


def hevc_from_ptl(ptl: bytes) -> str | None:
    """De algemene profile_tier_level (12 bytes) als string (ISO/IEC 14496-15, bijlage E):
    ruimte (leeg, A, B of C) en profiel, de compatibiliteit met de bits omgekeerd in hex, tier
    (L of H) en level, en de 6 bytes constraints zonder de nullen aan het eind."""
    if len(ptl) < 12:
        return None
    space = ("", "A", "B", "C")[ptl[0] >> 6]
    tier = "H" if ptl[0] & 0x20 else "L"
    profile = ptl[0] & 0x1F
    compat = _reverse32(int.from_bytes(ptl[1:5], "big"))
    constraints = list(ptl[5:11])
    level = ptl[11]
    if not profile or not level:
        return None
    while constraints and constraints[-1] == 0:
        constraints.pop()
    parts = [f"hvc1.{space}{profile}", f"{compat:X}", f"{tier}{level}"]
    parts += [f"{c:X}" for c in constraints]
    return ".".join(parts)


def hevc_from_hvcc(data: bytes) -> str | None:
    """hvcC: configurationVersion 1, dan de profile_tier_level in dezelfde volgorde."""
    if len(data) < 13 or data[0] != 1:
        return None
    return hevc_from_ptl(data[1:13])


def hevc_from_annexb(data: bytes) -> str | None:
    """De SPS (type 33: 1 byte, dan de PTL), en anders de VPS (type 32: 4 bytes, dan de PTL),
    elk na de kop van 2 bytes en zonder emulatiebytes."""
    sps = vps = None
    for unit in nal_units(data):
        if len(unit) < 2:
            continue
        kind = (unit[0] >> 1) & 0x3F
        if kind == 33 and sps is None:
            sps = unescape(unit[2:], 13)[1:13]
        elif kind == 32 and vps is None:
            vps = unescape(unit[2:], 16)[4:16]
    for ptl in (sps, vps):
        found = hevc_from_ptl(ptl) if ptl else None
        if found:
            return found
    return None


# --- Dolby Vision ------------------------------------------------------------------------------


def dovi_record(data: bytes) -> tuple[int, int, int] | None:
    """dvcC/dvvC: versie (2 bytes), dan profiel (7 bits), level (6 bits), rpu, el en bl (elk 1
    bit) en de compatibiliteit van de basislaag (4 bits). Geeft (profiel, level, compat)."""
    if len(data) < 5:
        return None
    bits = int.from_bytes(data[2:5], "big")
    return bits >> 17, (bits >> 11) & 0x3F, (bits >> 4) & 0x0F


def dovi_string(profile: int, level: int) -> str | None:
    if not 0 < profile < 100 or not 0 <= level < 100:
        return None
    return f"dvh1.{profile:02d}.{level:02d}"


def _dovi_side(stream: dict) -> tuple[int, int] | None:
    """Profiel en level uit het DOVI-record dat ffprobe uit de dvcC/dvvC haalt."""
    for side in stream.get("side_data_list") or []:
        if isinstance(side, dict) and "DOVI" in str(side.get("side_data_type", "")):
            profile = _int(side.get("dv_profile"))
            if profile is not None:
                return profile, _int(side.get("dv_level")) or 0
    return None


# --- AV1 en VP9 --------------------------------------------------------------------------------


def av1_from_av1c(data: bytes) -> str | None:
    """av1C: marker en versie (0x81), dan seq_profile (3 bits) en seq_level_idx_0 (5), dan
    seq_tier_0, high_bitdepth, twelve_bit, … Alleen de korte vorm van de string."""
    if len(data) < 3 or data[0] != 0x81:
        return None
    profile = data[1] >> 5
    level = data[1] & 0x1F
    if profile > 2:
        return None
    tier = "H" if data[2] & 0x80 else "M"
    high = bool(data[2] & 0x40)
    twelve = bool(data[2] & 0x20)
    depth = (12 if twelve else 10) if profile == 2 and high else (10 if high else 8)
    return f"av01.{profile}.{level:02d}{tier}.{depth:02d}"


def vp9_level(width: int | None, height: int | None, fps: float | None) -> int:
    """Het kleinste VP9-level waar dit beeld en deze fps in passen (40 als we het niet weten)."""
    if not width or not height or width <= 0 or height <= 0:
        return 40
    size = width * height
    rate = size * (fps if fps and 0 < fps <= 1000 else 30.0)
    for level, max_rate, max_size in _VP9_LEVELS:
        if size <= max_size and rate <= max_rate:
            return level
    return 62


def _vp9(profile: int, level: int, depth: int) -> str | None:
    if profile > 3 or depth not in (8, 10, 12) or level not in _VP9_LEVEL_IDS:
        return None
    return f"vp09.{profile:02d}.{level:02d}.{depth:02d}"


def vp9_from_vpcc(data: bytes) -> str | None:
    """vpcC (versie 1, 3 bytes vlaggen, dan profiel, level, bitdiepte << 4 | …), of de lijst van
    kenmerken in de CodecPrivate van Matroska (id, lengte 1, waarde; 1 profiel, 2 level,
    3 bitdiepte, 4 chroma)."""
    if len(data) >= 7 and data[0] == 1 and data[1:4] == b"\x00\x00\x00":
        return _vp9(data[4], data[5], data[6] >> 4)
    found: dict[int, int] = {}
    i = 0
    while i + 2 < len(data) and i < 64:
        ident, size = data[i], data[i + 1]
        if size != 1 or ident not in (1, 2, 3, 4):
            return None
        found[ident] = data[i + 2]
        i += 3
    if i != len(data) or 1 not in found:
        return None
    return _vp9(found[1], found.get(2, 40), found.get(3, 8))


# --- samen -------------------------------------------------------------------------------------


def _h264(stream: dict, data: bytes) -> str | None:
    if data:
        found = avc_from_annexb(data) if is_annexb(data) else avc_from_avcc(data)
        if found:
            return found
    profile = str(stream.get("profile") or "").lower()
    level = _int(stream.get("level"))
    if profile not in _AVC_PROFILE or level is None or not 0 < level < 256:
        return None
    return _avc(*_AVC_PROFILE[profile], level)


def _hevc(stream: dict, data: bytes) -> str | None:
    dv = _dovi_side(stream)
    if dv is not None and dv[0] == 5:
        return dovi_string(5, dv[1])
    if data:
        found = hevc_from_annexb(data) if is_annexb(data) else hevc_from_hvcc(data)
        if found:
            return found
    profile = str(stream.get("profile") or "").lower()
    level = _int(stream.get("level"))
    if profile not in _HEVC_PROFILE or level is None or not 0 < level < 256:
        return None
    idc, compat = _HEVC_PROFILE[profile]
    return f"hvc1.{idc}.{compat}.L{level}.B0"


def _av1(stream: dict, data: bytes) -> str | None:
    found = av1_from_av1c(data) if data else None
    if found:
        return found
    profile = _AV1_PROFILE.get(str(stream.get("profile") or "").lower(), 0)
    level = _int(stream.get("level"))
    lvl = level if level is not None and 0 <= level < 32 else 8
    return f"av01.{profile}.{lvl:02d}M.{_depth(stream):02d}"


def _vp9_stream(stream: dict, data: bytes) -> str | None:
    found = vp9_from_vpcc(data) if data else None
    if found:
        return found
    profile = _VP9_PROFILE.get(str(stream.get("profile") or "").lower(), 0)
    fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate"))
    level = vp9_level(_int(stream.get("width")), _int(stream.get("height")), fps)
    return _vp9(profile, level, _depth(stream))


def strings(stream: dict, extradata: bytes | None = None) -> str | None:
    """De codec-string van één stream (de ruwe dict van ffprobe), of None als we hem niet
    kunnen maken. Gooit nooit."""
    try:
        return _strings(stream, bytes(extradata or b"")[:MAX_BYTES])
    except Exception:  # een lezer die toch struikelt, mag de probe niet breken
        return None


def _strings(stream: dict, data: bytes) -> str | None:
    if not isinstance(stream, dict):
        return None
    codec = str(stream.get("codec_name") or "").lower()
    if codec == "h264":
        return _h264(stream, data)
    if codec == "hevc":
        return _hevc(stream, data)
    if codec == "av1":
        return _av1(stream, data)
    if codec == "vp9":
        return _vp9_stream(stream, data)
    if codec == "vp8":
        return "vp8"
    if codec == "aac":
        return _AAC.get(str(stream.get("profile") or "").lower(), "mp4a.40.2")
    return _AUDIO.get(codec)
