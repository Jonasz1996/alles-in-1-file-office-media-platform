"""De cache onder data_dir/media en het opruimen (Keuze 31). Stub van M0; M4 bouwt hem."""

from __future__ import annotations

KINDS = ("sessions", "audio", "trick", "art")


def sizes() -> dict[str, int]:
    """Bytes per soort, voor /beheer."""
    return dict.fromkeys(KINDS, 0)
