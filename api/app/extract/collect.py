"""Wat een lezer onderweg verzamelt, met de grenzen per blob (bouwontwerp fase 6, Keuze 17).

Collector houdt de ruwe tekst bij tot twee keer de grens van PLATFORM_SEARCH_MAX_TEXT (het
opschonen haalt er witruimte af; daarna kapt read.extract af op de echte grens). Is hij vol, dan
geeft add() Full: de lezer stopt en wat er is, telt. De klok is CPU-tijd van dit proces sinds de
start van de blob: tussen pagina's en onderdelen (check) en om de zoveel stukken tekst (add) kijkt
de lezer ernaar; na de grens geeft hij TooSlow, en ook dan telt wat er is (code too_slow).

Alleen de standaardbibliotheek.
"""

from __future__ import annotations

import time

RAW_FACTOR = 2
CHECK_EVERY = 256  # zo vaak kijkt add() naar de klok


class Full(Exception):
    """Genoeg tekst: de rest van de blob wordt niet meer gelezen."""


class TooSlow(Exception):
    """De klok van deze blob is op."""


class Clock:
    """CPU-tijd (alle threads van dit proces) vanaf nu."""

    def __init__(self, seconds: float):
        self.end = time.process_time() + seconds

    def over(self) -> bool:
        return time.process_time() > self.end


class Collector:
    def __init__(self, max_chars: int, clock: Clock):
        self.limit = max(0, max_chars) * RAW_FACTOR
        self.clock = clock
        self.parts: list[str] = []
        self.size = 0
        self.full = False
        self.meta = ""
        self.scan_pages: list[int] = []
        self.code: str | None = None  # pages_limit en dergelijke; too_slow zet read.extract
        self._adds = 0

    def add(self, text: str) -> None:
        if not text:
            return
        room = self.limit - self.size
        if len(text) >= room:
            text = text[: max(0, room)]
            self.full = True
        if text:
            self.parts.append(text)
            self.size += len(text)
        if self.full:
            raise Full
        self._adds += 1
        if self._adds % CHECK_EVERY == 0:
            self.check()

    def check(self) -> None:
        """Tussen pagina's en onderdelen: is de klok op?"""
        if self.clock.over():
            raise TooSlow

    def truncate(self) -> None:
        """Er was meer dan de lezer las (een grens op bytes of pagina's)."""
        self.full = True

    def text(self) -> str:
        return "".join(self.parts)
