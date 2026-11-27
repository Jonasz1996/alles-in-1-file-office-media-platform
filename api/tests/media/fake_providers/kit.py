"""Hulpjes voor de API-tests van M8: de enige uitgaande client (providers/http.py) tegen de
nep-providers in het proces.

    app = create_app(FakeConfig(tmdb_key=KEY, data=load()))
    out = kit.outbound(settings, app)      # http.use(out) zet hem als de client van het proces

De client denkt dat hij met `https://api.themoviedb.org` praat: de nep-resolver geeft een publiek
adres, de transport is de app (die op de kop `Host` routeert). De klok is nep: wachten op de token
bucket of een Retry-After kost geen echte tijd.
"""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.media.providers import http

from .app import transport

PUBLIC = "93.184.216.34"
KEY = "nep-sleutel-" + "k" * 24


class Clock:
    """Een nepklok: sleep zet de tijd vooruit (en geeft de lus even de beurt)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class Resolver:
    """Elke naam heeft hetzelfde publieke adres. Telt de vragen."""

    def __init__(self, address: str = PUBLIC) -> None:
        self.address = address
        self.calls: list[str] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        return [self.address]


def outbound(settings: Settings, app, *, clock: Clock | None = None) -> http.Outbound:
    clock = clock or Clock()
    return http.Outbound(
        settings,
        resolver=Resolver(),
        transport=transport(app),
        clock=clock,
        sleep=clock.sleep,
    )
