"""Metadata van buiten (Keuze 39–44). base.py is het contract (M0); de rest bouwt M8."""


def status() -> dict[str, dict]:
    """Per provider: ingesteld, laatste goed, laatste fout, de stroomonderbreker (voor /beheer).
    Stub van M0; M8 vult het."""
    return {name: {"configured": False} for name in ("tmdb", "musicbrainz", "caa")}
