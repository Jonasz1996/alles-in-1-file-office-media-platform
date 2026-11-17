"""Het contract van een metadata-provider (bouwontwerp fase 5, §3.3 en Keuze 39). Frozen.

TMDB, MusicBrainz en de Cover Art Archive (M8) volgen dit Protocol. Elk verkeer naar buiten gaat
via providers/http.py (regel 9). Zonder sleutel of met online uit: geen provider, alleen lokale
metadata.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

Kind = Literal["movie", "show", "episode", "album", "artist", "track"]


class ProviderAuth(Exception):
    """De sleutel werkt niet (422 media_provider_auth)."""


class ProviderUnavailable(Exception):
    """Onbereikbaar, of de stroomonderbreker is dicht (503 media_provider_unavailable)."""


class ProviderRate(Exception):
    """De provider remt af (503 media_provider_rate); op de achtergrond jobs.Defer."""

    def __init__(self, retry_after: float):
        super().__init__(f"opnieuw over {retry_after:g} s")
        self.retry_after = retry_after


class Candidate(BaseModel):
    """Eén zoekresultaat (Keuze 42)."""

    provider: str
    id: str
    kind: str
    title: str
    year: int | None = None
    original_title: str | None = None
    overview: str | None = None
    poster: str | None = None  # het pad bij de provider, nooit een volledige URL van buiten
    popularity: float | None = None


class ImageRef(BaseModel):
    provider: str
    kind: Literal["poster", "backdrop", "still", "cover", "logo"]
    path: str
    width: int | None = None
    height: int | None = None
    lang: str | None = None


class Details(BaseModel):
    provider: str
    id: str
    kind: str
    title: str
    original_title: str | None = None
    year: int | None = None
    overview: str | None = None
    genres: list[str] = Field(default_factory=list)
    runtime_min: int | None = None
    rating: float | None = None
    ids: dict[str, str] = Field(default_factory=dict)  # imdb, tvdb, musicbrainz, …
    images: list[ImageRef] = Field(default_factory=list)
    seasons: int | None = None


class EpisodeDetails(BaseModel):
    provider: str
    show_id: str
    season: int
    episode: int
    title: str | None = None
    overview: str | None = None
    aired: str | None = None  # YYYY-MM-DD
    still: str | None = None
    runtime_min: int | None = None


@runtime_checkable
class MetadataProvider(Protocol):
    name: str

    async def search(
        self, kind: str, title: str, year: int | None, lang: str
    ) -> list[Candidate]: ...

    async def details(self, kind: str, id: str, lang: str) -> Details: ...

    async def season(self, show_id: str, n: int, lang: str) -> list[EpisodeDetails]: ...

    async def images(self, kind: str, id: str) -> list[ImageRef]: ...

    async def check(self) -> None:
        """ProviderAuth of ProviderUnavailable als hij niet werkt."""
        ...
