"""De keyframe-index aan de kant van de app (Keuze 17): lezen bij play, binnen de 5 s van de
probe. Stub van M0; M4 bouwt hem (de lezers zelf staan in worker/containers.py).
"""

from __future__ import annotations

from typing import Literal

State = Literal["none", "ready", "sparse", "failed"]


async def read_now(sha: str) -> State:
    """De index nu maken met de eigen lezers. De stub zegt none: dan wordt het transcode
    (reden keyframes_missing)."""
    return "none"


def decode(blob: bytes | None) -> list[int]:
    """De tijden in ms uit media_probe.keyframes (zlib, varint-delta\'s)."""
    return []
