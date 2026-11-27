"""De Cover Art Archive (Keuze 41): de voorkant van een release.

`GET {CAA}/release/{mbid}/front-500` geeft een 307 naar archive.org; http.py volgt die alleen
naar de lijst van Keuze 39 (`coverartarchive.org`, `archive.org`, `*.archive.org`), met een nieuwe
controle van het adres. Samen met MusicBrainz aan of uit (standaard uit).
"""

from __future__ import annotations

from app.config import Settings
from app.media.providers import http, musicbrainz
from app.media.providers.base import (
    Candidate,
    Details,
    EpisodeDetails,
    ImageRef,
    MetadataProvider,
)

NAME = "caa"
SIZE = 500


class CoverArt:
    """Alleen plaatjes: zoeken en details horen bij MusicBrainz."""

    name = NAME

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = settings.media_caa_url.rstrip("/")

    def front_url(self, release_id: str) -> str | None:
        wanted = musicbrainz.mbid(release_id)
        if wanted is None:
            return None
        return f"{self.base}/release/{wanted}/front-{SIZE}"

    async def front(self, release_id: str) -> http.Fetched:
        """De voorkant (jpeg, png of webp, hooguit 10 MB). http.NotFound zonder voorkant."""
        url = self.front_url(release_id)
        if url is None:
            raise ValueError("geen MBID")
        return await http.get_image(NAME, url)

    async def search(self, kind: str, title: str, year: int | None, lang: str) -> list[Candidate]:
        return []

    async def details(self, kind: str, id: str, lang: str) -> Details:
        raise ValueError("de Cover Art Archive heeft geen details")

    async def season(self, show_id: str, n: int, lang: str) -> list[EpisodeDetails]:
        return []

    async def images(self, kind: str, id: str) -> list[ImageRef]:
        wanted = musicbrainz.mbid(id.removeprefix("release:"))
        if wanted is None:
            return []
        return [ImageRef(provider=NAME, kind="cover", path=wanted)]

    async def check(self) -> None:
        return None


def provider() -> MetadataProvider | None:
    """De provider als MusicBrainz aan staat, anders None (alleen lokale metadata)."""
    if not musicbrainz.enabled():
        return None
    from app.media import runtime

    return CoverArt(runtime.settings())
