"""Koppelen (Keuze 42): pure functies die een kandidaat kiezen. Stub van M0; M8 bouwt ze."""

from __future__ import annotations

from app.media.providers.base import Candidate


def best(
    title: str, year: int | None, candidates: list[Candidate]
) -> tuple[Candidate | None, float]:
    """De beste kandidaat en hoe zeker (0–1)."""
    return None, 0.0
