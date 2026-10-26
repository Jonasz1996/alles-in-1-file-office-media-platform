"""Het eerste socketpakket in de tunnel (Keuze 6, StreamGuard). O0-stub; O1 maakt het af.

Guard leest de frames van de browser (gemaskeerd, eventueel in stukken) tot het CONNECT-pakket
van socket.io en het eerste auth-bericht. Elk JWT daarin: HS256 met het office-geheim, en
document.key gelijk aan de key van het pad. Hooguit 64 KB en 10 s. De vorm staat in
tests/fake_docserver/recorded/socket.json. De stub weigert alles.
"""

from typing import Literal

from app.config import Settings


class Guard:
    def __init__(self, settings: Settings, key: str) -> None:
        self.settings = settings
        self.key = key

    def feed(self, data: bytes) -> Literal["more", "ok", "deny"]:
        return "deny"
