"""Metadata van buiten (Keuze 39–44). base.py is het contract (M0); de rest bouwt M8."""

from __future__ import annotations

PROVIDERS = ("tmdb", "musicbrainz", "caa")


def status() -> dict[str, dict]:
    """Per provider: ingesteld, laatste goed, laatste fout, de stroomonderbreker (voor /beheer).
    Stub van M0; M8 vult het."""
    return {name: {"configured": False} for name in PROVIDERS}


async def check(settings, *, record: bool = False) -> dict[str, str]:
    """Voor app.cli media-provider-check: per provider ok, off (geen sleutel of online uit),
    auth of unavailable. record (--opnemen, alleen met een echte sleutel): de vorm van elk
    antwoord naar docs/fase-5/provider-shapes.json (§4.4). De stub: alles off (geen sleutel,
    alleen lokale metadata)."""
    return dict.fromkeys(PROVIDERS, "off")
