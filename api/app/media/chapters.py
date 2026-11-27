"""Hoofdstukken en de aftiteling (bouwontwerp fase 5, Keuze 30). M5.

De hoofdstukken komen uit de probe (al in mediatijd, titel hooguit 200 tekens). items geeft ze
op volgorde en zonder onzin (geen lengte, buiten de film); credits_ms zegt waar de aftiteling
begint: het eerste hoofdstuk in de tweede helft met een titel als "Credits", "End Credits",
"Aftiteling", "Eindgeneriek" of "Outro". Dat telt voor "gezien" (Keuze 36, M7) en voor de kaart
"Volgende aflevering" (Keuze 32). "Opening Credits", "Begingeneriek" en "Intro" tellen niet.
"""

from __future__ import annotations

import re
import unicodedata

from app.media.types import Chapter, Probe

TITLE_MAX = 200

# Woorden en woordgroepen (na normaliseren: kleine letters, zonder accenten, alleen letters en
# cijfers met één spatie ertussen) die het begin van de aftiteling noemen, Nederlands en Engels
CREDITS = (
    "credits",
    "end titles",
    "closing titles",
    "outro",
    "aftiteling",
    "eindgeneriek",
    "slotgeneriek",
    "generiek",
    "generique",
)
# Alleen als de hele titel: "Ending" (anime), maar niet "A Happy Ending"
WHOLE = ("ending", "ending theme", "end")
# Wat ook "credits" noemt, maar aan het begin hoort
NOT_CREDITS = ("opening", "intro", "begingeneriek", "openingstitels")


def normalize(title: str) -> str:
    """Kleine letters, zonder accenten, alleen letters en cijfers met één spatie ertussen."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def is_credits(title: str | None) -> bool:
    """Noemt deze titel het begin van de aftiteling?"""
    if not title:
        return False
    plain = normalize(title)
    words = f" {plain} "
    if any(f" {word} " in words for word in NOT_CREDITS):
        return False
    return plain in WHOLE or any(f" {phrase} " in words for phrase in CREDITS)


def _length(probe: Probe, chapters: list[Chapter]) -> int | None:
    if probe.duration_ms:
        return probe.duration_ms
    return max((c.end_ms for c in chapters), default=None)


def items(probe: Probe) -> list[Chapter]:
    """De hoofdstukken op begintijd: zonder lengte of helemaal na het einde weg, het eind
    hooguit de duur, de titel hooguit TITLE_MAX tekens."""
    length = probe.duration_ms
    out: list[Chapter] = []
    for chapter in sorted(probe.chapters, key=lambda c: (c.start_ms, c.end_ms)):
        start = max(0, chapter.start_ms)
        end = chapter.end_ms if length is None else min(chapter.end_ms, length)
        if end <= start:
            continue
        title = (chapter.title or "").strip()[:TITLE_MAX] or None
        out.append(Chapter(start_ms=start, end_ms=end, title=title))
    return out


def credits_ms(probe: Probe) -> int | None:
    """Waar de aftiteling begint (uit de hoofdstukken), of None. Alleen een hoofdstuk dat in de
    tweede helft begint: een "Credits" vooraan is de begintitel."""
    chapters = items(probe)
    length = _length(probe, chapters)
    for chapter in chapters:
        if length and chapter.start_ms < length / 2:
            continue
        if is_credits(chapter.title):
            return chapter.start_ms
    return None
