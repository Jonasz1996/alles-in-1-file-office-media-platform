"""Koppelen (Keuze 42): pure functies die een kandidaat kiezen. Geen I/O.

- **Eerst id's.** Een id uit de naam of de NFO (`{tmdb-278}`, `[imdbid-tt0111161]`, `uniqueid`;
  de scanner zet ze in `ids`) geeft een directe lookup (`direct`): `/movie/{id}` of `/find`.
- **Anders zoeken op titel en jaar.** De score per kandidaat:
  - de gelijkenis van de genormaliseerde titel met `title` en `original_title` (kleine letters,
    geen accenten, geen lidwoord vooraan, geen leestekens: `names.sort_title`;
    `difflib.SequenceMatcher`), de beste van de twee;
  - jaar gelijk +0,2, één jaar verschil +0,1, anders −0,3; geen jaar (aan een van beide
    kanten) 0;
  - de populariteit alleen als scheidsrechter: hooguit +0,02.
- **Automatisch** bij een score ≥ 0,85 en ≥ 0,1 boven de tweede. Anders `review`: de lijst
  "Nakijken", met de 5 beste kandidaten.
- **Muziek:** album + albumartiest + aantal nummers; elk nummer verschil kost 0,1.

`tests/test_media_match.py` heeft de tabel; ook de 20 films met scene-namen van de nep-data.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.media import names
from app.media.providers.base import Candidate

AUTO = 0.85
MARGIN = 0.10
TOP = 5
YEAR_SAME = 0.2
YEAR_NEAR = 0.1
YEAR_OFF = -0.3
POPULARITY_MAX = 0.02
TRACK_COST = 0.1


def normalize(text: str | None) -> str:
    """Zonder hoofdletters, accenten, leestekens en lidwoord vooraan (The Matrix → matrix); een
    & telt als "and", een apostrof valt weg (Winter's → winters)."""
    if not text:
        return ""
    text = text.replace("&", " and ").replace("'", "").replace("\u2019", "")
    return names.sort_title(text)


def similarity(a: str | None, b: str | None) -> float:
    """0–1: hoe gelijk twee titels zijn, na normaliseren."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb, autojunk=False).ratio()


def year_bonus(year: int | None, other: int | None) -> float:
    if year is None or other is None:
        return 0.0
    diff = abs(year - other)
    if diff == 0:
        return YEAR_SAME
    if diff == 1:
        return YEAR_NEAR
    return YEAR_OFF


def tiebreak(popularity: float | None) -> float:
    """Populariteit als scheidsrechter: 0 tot POPULARITY_MAX (log, verzadigt bij 1000)."""
    if popularity is None or popularity != popularity or popularity <= 0:
        return 0.0
    return min(POPULARITY_MAX, POPULARITY_MAX * math.log10(1 + popularity) / 3)


def score(title: str, year: int | None, candidate: Candidate) -> float:
    """De score van één kandidaat (kan boven 1 of onder 0 komen)."""
    text = max(similarity(title, candidate.title), similarity(title, candidate.original_title))
    return text + year_bonus(year, candidate.year) + tiebreak(candidate.popularity)


@dataclass
class Scored:
    candidate: Candidate
    score: float


@dataclass
class Decision:
    """Wat er met een item gebeurt: `matched` (automatisch, met `choice`) of `review` (de
    lijst Nakijken, met de beste kandidaten in `top`)."""

    state: str
    choice: Candidate | None
    confidence: float
    top: list[Scored] = field(default_factory=list)


def rank(title: str, year: int | None, candidates: Iterable[Candidate]) -> list[Scored]:
    """De kandidaten op score, de beste eerst; bij gelijke score de volgorde van de provider.
    Dezelfde kandidaat (provider en id) telt één keer, met zijn beste score: zo telt een film
    die in nl-NL en in en-US terugkwam met de titel die het best past, en blijft de eerste
    vorm (de taal van de bibliotheek) de kandidaat die je ziet."""
    best: dict[tuple[str, str], Scored] = {}
    for c in candidates:
        key = (c.provider, c.id)
        value = score(title, year, c)
        if key not in best:
            best[key] = Scored(c, value)
        elif value > best[key].score:
            best[key].score = value
    out = list(best.values())
    out.sort(key=lambda s: -s.score)
    return out


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 3)


def choose(ranked: list[Scored]) -> Decision:
    """De regel van Keuze 42 op een gerangschikte lijst."""
    if not ranked:
        return Decision("review", None, 0.0, [])
    first = ranked[0]
    second = ranked[1].score if len(ranked) > 1 else float("-inf")
    if first.score >= AUTO and first.score - second >= MARGIN:
        return Decision("matched", first.candidate, _clamp(first.score), ranked[:TOP])
    return Decision("review", None, _clamp(first.score), ranked[:TOP])


def decide(title: str, year: int | None, candidates: Iterable[Candidate]) -> Decision:
    return choose(rank(title, year, candidates))


def best(
    title: str, year: int | None, candidates: list[Candidate]
) -> tuple[Candidate | None, float]:
    """De beste kandidaat en hoe zeker (0–1). Alleen een kandidaat als hij automatisch
    gekozen mag worden (≥ 0,85 en ≥ 0,1 boven de tweede); anders (None, de hoogste score)."""
    decision = decide(title, year, candidates)
    return decision.choice, decision.confidence


# --- id's ---------------------------------------------------------------------------------------


def _digits(value: Any) -> str | None:
    text = str(value or "").strip()
    if ":" in text:
        text = text.rsplit(":", 1)[1]
    return text if text.isdigit() and 0 < len(text) <= 10 and int(text) > 0 else None


def direct(ids: Mapping[str, Any] | None, kind: str) -> tuple[str, str] | None:
    """Een id uit de naam of de NFO: ("tmdb", "movie:278") of ("imdb", "tt0111161"); anders
    None. kind: movie of show."""
    ids = ids or {}
    number = _digits(ids.get("tmdb"))
    if number is not None:
        return "tmdb", f"{'movie' if kind == 'movie' else 'tv'}:{number}"
    imdb = str(ids.get("imdb") or "").strip()
    if imdb.startswith("tt") and imdb[2:].isdigit() and 7 <= len(imdb) - 2 <= 10:
        return "imdb", imdb
    return None


# --- muziek -------------------------------------------------------------------------------------


@dataclass
class Album:
    """Een album zoals de provider het kent (los van de JSON van de provider)."""

    id: str
    title: str
    artist: str | None
    tracks: int | None
    year: int | None = None


def album_score(
    title: str, artist: str | None, tracks: int | None, album: Album, year: int | None = None
) -> float:
    """Album en albumartiest (het gemiddelde; zonder artiest alleen het album), min 0,1 per
    nummer verschil, plus het jaar zoals bij films."""
    text = similarity(title, album.title)
    if artist and album.artist:
        text = (text + similarity(artist, album.artist)) / 2
    if tracks is not None and album.tracks is not None:
        text -= TRACK_COST * abs(tracks - album.tracks)
    return text + year_bonus(year, album.year)


def decide_album(
    title: str,
    artist: str | None,
    tracks: int | None,
    albums: Iterable[Album],
    year: int | None = None,
) -> tuple[Album | None, float]:
    """Het album als het automatisch mag (dezelfde regel als bij films), anders None; en de
    hoogste score (0–1)."""
    scored = sorted(
        ((album_score(title, artist, tracks, a, year), i, a) for i, a in enumerate(albums)),
        key=lambda t: (-t[0], t[1]),
    )
    if not scored:
        return None, 0.0
    first = scored[0][0]
    second = scored[1][0] if len(scored) > 1 else float("-inf")
    if first >= AUTO and first - second >= MARGIN:
        return scored[0][2], _clamp(first)
    return None, _clamp(first)
