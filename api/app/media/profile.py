"""Het clientprofiel (Keuze 20): het basisprofiel uit profiles/*.json en wat de client zelf
meet.

- `load(data)`: het profiel van de client, gevalideerd door ClientProfile (onbekende velden weg,
  alleen strings op een bekend patroon, hooguit 64, het geheel hooguit 8 KB).
- `base(name)`: een basisprofiel. Dat zijn data (profiles/<naam>.json, beschreven in
  docs/fase-5/clientprofiel.md): wat een client aanneemt als hij niet alles kan testen, plus
  bekende eigenaardigheden (`deny`, `deny_by_name`) en vlaggen voor de playlists (`playlist`).
- `merge(profile)`: het basisprofiel dat de client noemt, met wat hij zelf mat erover. Wat de
  client meet, gaat voor: elk veld dat hij meestuurt (pydantic onthoudt welke), per codec-string
  ook. Alleen `deny` gaat boven een meting: een string die een basisprofiel uitsluit (H.264
  Hi10P op webOS 5 en Safari, en in `web` voor Firefox op client.name), speelt nooit direct of
  via remux, wat de browser ook zegt (Keuze 19, MED-10).
- `support(profile, string)`: wat het profiel van één string weet. Eerst de string zelf; anders
  een string van dezelfde familie (zelfde codec, profiel en bitdiepte, een tier en level die
  minstens zo hoog zijn). Zo dekt `avc1.640033` uit een basisprofiel ook `avc1.64001f`. Hi10P
  (`avc1.6e…`) is een eigen profiel en telt dus alleen als hij er zelf in staat.
- `flags(name)`: de vlaggen voor de playlists van een basisprofiel (voor M4).

Het profiel is geen beveiliging: wie liegt, krijgt iets wat hij niet kan spelen.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from app.media.types import PROFILE_BASES, ClientProfile, CodecSupport

PROFILES = Path(__file__).parent / "profiles"
_SECTIONS = ("client", "engines", "hdr", "max", "audio")

CANDIDATES = (
    "avc1.42E01E", "avc1.64001f", "avc1.640028", "avc1.640033", "hvc1.1.6.L120.90",
    "hvc1.2.4.L150.90", "vp09.00.40.08", "vp09.02.40.10", "av01.0.08M.08", "av01.0.08M.10",
    "mp4a.40.2", "mp4a.40.5", "ac-3", "ec-3", "opus", "flac", "mp3",
)  # fmt: skip


def load(data: Any) -> ClientProfile:
    """Het profiel van de client, gevalideerd (onbekend weg, ≤ 64 strings, ≤ 8 KB)."""
    if isinstance(data, (bytes, str)):
        return ClientProfile.model_validate_json(data)
    return ClientProfile.model_validate(data)


@lru_cache
def _raw(name: str) -> dict[str, Any]:
    if name not in PROFILE_BASES:
        raise ValueError(f"onbekend basisprofiel {name}")
    return json.loads((PROFILES / f"{name}.json").read_text(encoding="utf-8"))


def base(name: str) -> ClientProfile:
    """Een basisprofiel (elke keer een nieuwe kopie)."""
    return ClientProfile.model_validate(_raw(name))


def flags(name: str) -> dict[str, bool]:
    """De vlaggen voor de playlists van een basisprofiel, zoals independent_segments (webOS 5:
    nee). M4 leest ze bij het maken van de master."""
    raw = _raw(name).get("playlist") or {}
    return {str(k): bool(v) for k, v in raw.items()}


def denied(profile: ClientProfile) -> tuple[str, ...]:
    """De begin-stukken van strings die het basisprofiel uitsluit, ook voor deze client."""
    raw = _raw(profile.base)
    out = [str(p).lower() for p in raw.get("deny") or []]
    name = profile.client.name.lower()
    for key, prefixes in (raw.get("deny_by_name") or {}).items():
        if str(key).lower() in name:
            out += [str(p).lower() for p in prefixes]
    return tuple(out)


def merge(profile: ClientProfile) -> ClientProfile:
    """Het basisprofiel met wat de client zelf mat erover (wat hij meet, gaat voor), daarna de
    uitsluitingen van het basisprofiel."""
    out = base(profile.base)
    given = profile.model_fields_set
    for section in _SECTIONS:
        if section in given:
            mine, target = getattr(profile, section), getattr(out, section)
            for name in mine.model_fields_set:
                setattr(target, name, getattr(mine, name))
    if "file" in given:
        out.file = list(profile.file)
    for string, measured in profile.codecs.items():
        known = out.codecs.get(string)
        if known is None:
            out.codecs[string] = measured.model_copy()
            continue
        for name in measured.model_fields_set:
            setattr(known, name, getattr(measured, name))
    deny = denied(out)
    if deny:
        for string, found in out.codecs.items():
            if string.lower().startswith(deny):
                found.mse = found.file = False
    return out


class Family(NamedTuple):
    key: tuple  # codec, profiel en bitdiepte: alleen gelijke families dekken elkaar
    tier: int  # 0 (L, M) of 1 (H)
    level: int


_AVC = re.compile(r"^avc[13]\.([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$")
_HEVC = re.compile(r"^(?:hvc1|hev1)\.([abc]?)(\d{1,2})\.[0-9a-f]{1,8}\.([lh])(\d{1,3})(?:\..*)?$")
_VP9 = re.compile(r"^vp09\.(\d{2})\.(\d{2})\.(\d{2})(?:\..*)?$")
_AV1 = re.compile(r"^av01\.(\d)\.(\d{2})([mh])\.(\d{2})(?:\..*)?$")
_DV = re.compile(r"^(?:dvh1|dvhe)\.(\d{2})\.(\d{2})$")


def family(string: str) -> Family | None:
    """De familie van een video-string en zijn tier en level. Audio en onbekende strings hebben
    geen familie: die tellen alleen precies."""
    s = string.lower()
    if m := _AVC.match(s):
        return Family(("avc1", m.group(1)), 0, int(m.group(3), 16))
    if m := _HEVC.match(s):
        tier = 1 if m.group(3) == "h" else 0
        return Family(("hvc1", m.group(1), int(m.group(2))), tier, int(m.group(4)))
    if m := _VP9.match(s):
        return Family(("vp09", int(m.group(1)), int(m.group(3))), 0, int(m.group(2)))
    if m := _AV1.match(s):
        tier = 1 if m.group(3) == "h" else 0
        return Family(("av01", int(m.group(1)), int(m.group(4))), tier, int(m.group(2)))
    if m := _DV.match(s):
        return Family(("dvh1", int(m.group(1))), 0, int(m.group(2)))
    return None


def _covers(have: Family, want: Family) -> bool:
    return have.key == want.key and want.tier <= have.tier and want.level <= have.level


def support(profile: ClientProfile, string: str | None) -> CodecSupport | None:
    """Wat het profiel van deze string weet (None: niets). Eerst precies (zonder op hoofdletters
    te letten), anders de familie (zie boven): mse en file als één lid het kan, smooth en power
    True als één lid het weet, False als alleen False bekend is."""
    if not string:
        return None
    lower = string.lower()
    for key, found in profile.codecs.items():
        if key.lower() == lower:
            return found
    want = family(string)
    if want is None:
        return None
    members = [
        found
        for key, found in profile.codecs.items()
        if (have := family(key)) is not None and _covers(have, want)
    ]
    if not members:
        return None
    return CodecSupport(
        mse=any(m.mse for m in members),
        file=any(m.file for m in members),
        smooth=_known(m.smooth for m in members),
        power=_known(m.power for m in members),
    )


def _known(values: Iterable[bool | None]) -> bool | None:
    seen = [v for v in values if v is not None]
    return any(seen) if seen else None


def candidates() -> list[str]:
    """De vaste kandidaten voor GET /media/{node_id}/codecs (Keuze 20)."""
    return list(CANDIDATES)
