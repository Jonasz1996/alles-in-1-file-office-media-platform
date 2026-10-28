"""De treden van transcode en hun kosten (Keuze 24, 26). Stub van M0; M4 vult de tabel."""

from __future__ import annotations

from app.media.types import Rung

# De volgorde van Keuze 26: de eerste die past
ORDER: tuple[Rung, ...] = (
    Rung(height=1080, preset="veryfast", codec="h264"),
    Rung(height=1080, preset="superfast", codec="h264"),
    Rung(height=720, preset="veryfast", codec="h264"),
    Rung(height=720, preset="superfast", codec="h264"),
    Rung(height=480, preset="veryfast", codec="h264"),
)


def cost(rung: Rung, *, hdr: bool = False) -> float:
    """De cores van de tabel (Keuze 24). De stub kent de tabel nog niet."""
    raise NotImplementedError("M4")
