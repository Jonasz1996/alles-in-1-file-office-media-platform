"""MusicBrainz (Keuze 41): albums, standaard uit.

- Aan te zetten op /beheer (`MediaConfig.musicbrainz`): het stuurt album- en artiestnamen naar
  een derde. Uit: `provider()` is None en er gaat niets naar buiten.
- Geen sleutel. Wel 1 verzoek per seconde (de token bucket van http.py) en een User-Agent met
  contact (de URL van het repo, nooit een e-mailadres).
- Met een MBID uit de tags (Picard): `GET /ws/2/release/{mbid}?inc=release-groups+artist-credits
  +media&fmt=json`. Anders zoeken: `GET /ws/2/release/?query=release:"…" AND artist:"…"
  &fmt=json&limit=5`.
- Id's naar buiten: `release:<mbid>`.
- Verdedigend lezen zoals bij TMDB; een fuzztest met een vaste seed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.media.providers import http
from app.media.providers.base import (
    Candidate,
    Details,
    EpisodeDetails,
    ImageRef,
    MetadataProvider,
    ProviderUnavailable,
)

NAME = "musicbrainz"
LIMIT = 5
ID = re.compile(r"^release:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
YEAR = re.compile(r"^(\d{4})")
LUCENE = re.compile(r'(["\\])')


def mbid(value: Any) -> str | None:
    """Een MBID in de vaste vorm (kleine letters, 8-4-4-4-12), of None."""
    if not isinstance(value, str) or len(value.strip()) != 36:
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        return None


def parse_id(value: str) -> str:
    m = ID.match(value or "")
    if m is None:
        raise ValueError("geen id van MusicBrainz")
    return m.group(1)


def phrase(text: str) -> str:
    """Een Lucene-zin: tussen aanhalingstekens, met " en \\ ontsnapt."""
    return '"' + LUCENE.sub(r"\\\1", " ".join(text.split())[:200]) + '"'


def _text(value: Any, limit: int = 255) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(http.printable(value).split())[:limit] or None


def _int(value: Any, low: int = 0, high: int = 10_000) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _year(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    m = YEAR.match(value)
    if m is None:
        return None
    year = int(m.group(1))
    return year if 1800 <= year <= 2200 else None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass
class Release:
    """Een release (een album) zoals wij hem gebruiken."""

    id: str
    title: str
    artist: str | None
    year: int | None
    track_count: int | None
    group_id: str | None
    cover: bool
    score: int | None = None

    def candidate(self) -> Candidate:
        count = f"{self.track_count} #" if self.track_count else None
        parts = [p for p in (self.artist, count) if p]
        return Candidate(
            provider=NAME,
            id=f"release:{self.id}",
            kind="album",
            title=self.title,
            year=self.year,
            original_title=self.artist,
            overview=" · ".join(parts) or None,
            poster=None,
            popularity=float(self.score) if self.score is not None else None,
        )


def _artist(data: dict[str, Any]) -> str | None:
    names = []
    for credit in _list(data.get("artist-credit"))[:20]:
        credit = _dict(credit)
        name = _text(credit.get("name")) or _text(_dict(credit.get("artist")).get("name"))
        if name:
            names.append(name + (_text(credit.get("joinphrase"), 20) or "").rstrip())
    return _text(" ".join(names)) if names else None


def _tracks(data: dict[str, Any]) -> int | None:
    count = _int(data.get("track-count"), 1)
    if count is not None:
        return count
    total = 0
    for medium in _list(data.get("media"))[:100]:
        n = _int(_dict(medium).get("track-count"), 0)
        if n is None:
            return None
        total += n
    return total or None


def release_of(data: Any) -> Release | None:
    data = _dict(data)
    release_id = mbid(data.get("id"))
    title = _text(data.get("title"))
    if release_id is None or title is None:
        return None
    group = _dict(data.get("release-group"))
    art = _dict(data.get("cover-art-archive"))
    return Release(
        id=release_id,
        title=title,
        artist=_artist(data),
        year=_year(data.get("date")),
        track_count=_tracks(data),
        group_id=mbid(group.get("id")),
        cover=art.get("front") is True,
        score=_int(data.get("score"), 0, 100),
    )


class MusicBrainz:
    """De provider MusicBrainz (MetadataProvider), voor albums."""

    name = NAME

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = settings.media_musicbrainz_url.rstrip("/") + "/ws/2"

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return await http.get_json(NAME, self.base + path, params={**params, "fmt": "json"})

    async def release(self, release_id: str) -> Release:
        """Een release op zijn MBID. http.NotFound als hij er niet is."""
        wanted = mbid(release_id)
        if wanted is None:
            raise ValueError("geen MBID")
        data = await self._get(f"/release/{wanted}", {"inc": "release-groups+artist-credits+media"})
        release = release_of(data)
        if release is None:
            raise ProviderUnavailable("musicbrainz: onverwacht antwoord")
        return release

    async def search_album(
        self, title: str, artist: str | None, year: int | None = None
    ) -> list[Release]:
        """De 5 beste releases voor dit album (en deze artiest)."""
        if not " ".join(title.split()):
            return []
        query = f"release:{phrase(title)}"
        if artist and " ".join(artist.split()):
            query += f" AND artist:{phrase(artist)}"
        if year is not None:
            query += f" AND date:{int(year)}"
        data = await self._get("/release/", {"query": query, "limit": LIMIT})
        out = []
        for raw in _list(data.get("releases"))[:LIMIT]:
            release = release_of(raw)
            if release is not None:
                out.append(release)
        return out

    async def search(self, kind: str, title: str, year: int | None, lang: str) -> list[Candidate]:
        return [r.candidate() for r in await self.search_album(title, None, year)]

    async def details(self, kind: str, id: str, lang: str) -> Details:
        release = await self.release(parse_id(id))
        ids = {"musicbrainz": release.id}
        if release.group_id:
            ids["musicbrainz_group"] = release.group_id
        images = []
        if release.cover:
            images.append(ImageRef(provider="caa", kind="cover", path=release.id))
        return Details(
            provider=NAME,
            id=f"release:{release.id}",
            kind="album",
            title=release.title,
            original_title=release.artist,
            year=release.year,
            ids=ids,
            images=images,
        )

    async def season(self, show_id: str, n: int, lang: str) -> list[EpisodeDetails]:
        return []

    async def images(self, kind: str, id: str) -> list[ImageRef]:
        return (await self.details(kind, id, "")).images

    async def check(self) -> None:
        await self._get("/release/", {"query": 'release:"test"', "limit": 1})


def enabled() -> bool:
    """Staat MusicBrainz aan op /beheer (MediaConfig.musicbrainz; standaard uit)?"""
    from app.media import runtime
    from app.media.state import media_policy

    bound = runtime.app()
    if bound is None:
        return False
    config = media_policy(bound).config
    return bool(config is not None and config.musicbrainz)


def provider() -> MetadataProvider | None:
    """De provider als hij aan staat op /beheer, anders None (alleen lokale metadata)."""
    if not enabled():
        return None
    from app.media import runtime

    return MusicBrainz(runtime.settings())
