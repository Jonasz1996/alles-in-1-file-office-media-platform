"""TMDB (Keuze 40): elke installatie brengt haar eigen sleutel. Stub van M0; M8 bouwt hem."""

from __future__ import annotations

from app.media.providers.base import MetadataProvider


def provider() -> MetadataProvider | None:
    """De provider als hij ingesteld is, anders None (alleen lokale metadata)."""
    return None
