"""De enige weg naar buiten voor media (regel 9, Keuze 39): een eigen client met grenzen,
stroomonderbreker en geen privé-adressen. Stub van M0; M8 bouwt hem."""

from __future__ import annotations

from app.media.providers.base import ProviderUnavailable


async def get_json(provider: str, url: str, *, params: dict | None = None) -> dict:
    raise ProviderUnavailable(f"{provider}: nog niet gebouwd")
