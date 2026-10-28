"""Het clientprofiel (Keuze 20): het basisprofiel uit profiles/*.json en wat de client zelf
meet. Stub van M0; M3 bouwt het.
"""

from __future__ import annotations

from typing import Any

from app.media.types import PROFILE_BASES, ClientProfile


def load(data: Any) -> ClientProfile:
    """Het profiel van de client, gevalideerd (onbekend weg, ≤ 64 strings, ≤ 8 KB)."""
    return ClientProfile.model_validate(data)


def base(name: str) -> ClientProfile:
    """Een basisprofiel; de stub kent alleen de namen."""
    if name not in PROFILE_BASES:
        raise ValueError(f"onbekend basisprofiel {name}")
    return ClientProfile(base=name)


def merge(profile: ClientProfile) -> ClientProfile:
    """Het basisprofiel met wat de client zelf mat erover (wat hij meet, gaat voor)."""
    return profile


def candidates() -> list[str]:
    """De vaste kandidaten voor GET /media/{node_id}/codecs (Keuze 20)."""
    return [
        "avc1.42E01E", "avc1.64001f", "avc1.640028", "avc1.640033", "hvc1.1.6.L120.90",
        "hvc1.2.4.L150.90", "vp09.00.40.08", "vp09.02.40.10", "av01.0.08M.08", "av01.0.08M.10",
        "mp4a.40.2", "mp4a.40.5", "ac-3", "ec-3", "opus", "flac", "mp3",
    ]  # fmt: skip
