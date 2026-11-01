"""TMDB (Keuze 40): elke installatie brengt haar eigen sleutel.

- De sleutel (het "API Read Access Token") gaat alleen als `Authorization: Bearer` naar de API
  van TMDB; nooit in een adres, nooit naar de plaatjeshost (`secrets.py`).
- Eindpunten: `/3/configuration` (de test), `/3/search/movie`, `/3/search/tv`,
  `/3/movie/{id}?append_to_response=external_ids`, `/3/tv/{id}?append_to_response=external_ids`,
  `/3/tv/{id}/season/{n}`, `/3/find/{imdb_id}?external_source=imdb_id`. De taal komt van de
  bibliotheek (`nl-NL`); een lege samenvatting haalt de client nog één keer in `en-US`.
- Plaatjes: poster `w500`, achtergrond `w1280`, still `w300`, van de plaatjeshost uit de
  instellingen. Nooit `original`.
- Id's naar buiten: `movie:<n>` en `tv:<n>`.
- Elk antwoord wordt verdedigend gelezen (`_text`, `_int`, …): een veld van het verkeerde soort
  telt als leeg, nooit als fout. Niets van het JSON van TMDB komt buiten deze module (alleen de
  dataclasses van base.py). Een fuzztest met een vaste seed (test_media_providers_contract.py).
"""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.media.providers import http, secrets
from app.media.providers.base import (
    Candidate,
    Details,
    EpisodeDetails,
    ImageRef,
    MetadataProvider,
    ProviderUnavailable,
)

NAME = "tmdb"
FALLBACK_LANG = "en-US"
SIZES = {"poster": "w500", "backdrop": "w1280", "still": "w300", "logo": "w500", "cover": "w500"}
IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_-]{1,100}\.(jpg|jpeg|png|webp)$")
ID = re.compile(r"^(movie|tv):([1-9][0-9]{0,9})$")
IMDB = re.compile(r"^tt[0-9]{7,10}$")
DATE = re.compile(r"^(\d{4})-\d{2}-\d{2}")
LANG = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
MAX_RESULTS = 20
OVERVIEW_CHARS = 10_000


# --- verdedigend lezen ------------------------------------------------------------------------


def _text(value: Any, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:limit]
    return text or None


def _overview(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()[:OVERVIEW_CHARS]
    return text or None


def _int(value: Any, low: int = 0, high: int = 10**9) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    return value if value == value and abs(value) < 1e12 else None


def _year(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    m = DATE.match(value)
    if m is None:
        return None
    year = int(m.group(1))
    return year if 1800 <= year <= 2200 else None


def _date(value: Any) -> str | None:
    if isinstance(value, str) and DATE.match(value) and len(value) == 10:
        return value
    return None


def _image(value: Any) -> str | None:
    return value if isinstance(value, str) and IMAGE_PATH.match(value) else None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_id(value: str) -> tuple[str, int]:
    """`movie:603` → ("movie", 603); anders ValueError."""
    m = ID.match(value or "")
    if m is None:
        raise ValueError("geen id van TMDB")
    return m.group(1), int(m.group(2))


def kind_of(kind: str) -> str:
    """De soort van ons (movie, show, episode) naar die van TMDB (movie, tv)."""
    return "movie" if kind == "movie" else "tv"


def candidate(result: Any, kind: str) -> Candidate | None:
    """Een zoekresultaat (of een resultaat van find) als Candidate; None als het niet klopt."""
    result = _dict(result)
    tmdb_id = _int(result.get("id"), 1)
    if tmdb_id is None:
        return None
    if kind == "movie":
        title = _text(result.get("title")) or _text(result.get("original_title"))
        original = _text(result.get("original_title"))
        year = _year(result.get("release_date"))
    else:
        title = _text(result.get("name")) or _text(result.get("original_name"))
        original = _text(result.get("original_name"))
        year = _year(result.get("first_air_date"))
    if title is None:
        return None
    overview = _overview(result.get("overview"))
    return Candidate(
        provider=NAME,
        id=f"{kind}:{tmdb_id}",
        kind="movie" if kind == "movie" else "show",
        title=title,
        year=year,
        original_title=original,
        overview=overview[:300] if overview else None,
        poster=_image(result.get("poster_path")),
        popularity=_float(result.get("popularity")),
    )


def details_of(data: dict[str, Any], kind: str) -> Details:
    """De details van een film of serie (met external_ids). ProviderUnavailable als het geen
    film of serie is."""
    tmdb_id = _int(data.get("id"), 1)
    if kind == "movie":
        title = _text(data.get("title")) or _text(data.get("original_title"))
        original = _text(data.get("original_title"))
        year = _year(data.get("release_date"))
        runtime = _int(data.get("runtime"), 1, 100_000)
        seasons = None
    else:
        title = _text(data.get("name")) or _text(data.get("original_name"))
        original = _text(data.get("original_name"))
        year = _year(data.get("first_air_date"))
        runtimes = [_int(v, 1, 100_000) for v in _list(data.get("episode_run_time"))[:10]]
        runtime = next((r for r in runtimes if r), None)
        seasons = _int(data.get("number_of_seasons"), 0, 10_000)
    if tmdb_id is None or title is None:
        raise ProviderUnavailable("tmdb: onverwacht antwoord")
    external = _dict(data.get("external_ids"))
    ids = {"tmdb": str(tmdb_id)}  # zoals de naam en de NFO het zeggen: alleen het getal
    imdb = external.get("imdb_id") or data.get("imdb_id")
    if isinstance(imdb, str) and IMDB.match(imdb):
        ids["imdb"] = imdb
    tvdb = _int(external.get("tvdb_id"), 1)
    if tvdb is not None:
        ids["tvdb"] = str(tvdb)
    images = []
    for role, key in (("poster", "poster_path"), ("backdrop", "backdrop_path")):
        path = _image(data.get(key))
        if path:
            images.append(ImageRef(provider=NAME, kind=role, path=path))  # type: ignore[arg-type]
    genres = [
        name
        for g in _list(data.get("genres"))[:20]
        if (name := _text(_dict(g).get("name"), 64)) is not None
    ]
    return Details(
        provider=NAME,
        id=f"{kind}:{tmdb_id}",
        kind="movie" if kind == "movie" else "show",
        title=title,
        original_title=original,
        year=year,
        overview=_overview(data.get("overview")),
        genres=genres,
        runtime_min=runtime,
        rating=_float(data.get("vote_average")),
        ids=ids,
        images=images,
        seasons=seasons,
    )


def episodes_of(data: dict[str, Any], show_id: str, n: int) -> list[EpisodeDetails]:
    out = []
    seen: set[int] = set()
    for raw in _list(data.get("episodes"))[:1000]:
        raw = _dict(raw)
        number = _int(raw.get("episode_number"), 0, 100_000)
        if number is None or number in seen:
            continue
        seen.add(number)
        out.append(
            EpisodeDetails(
                provider=NAME,
                show_id=show_id,
                season=n,
                episode=number,
                title=_text(raw.get("name")),
                overview=_overview(raw.get("overview")),
                aired=_date(raw.get("air_date")),
                still=_image(raw.get("still_path")),
                runtime_min=_int(raw.get("runtime"), 1, 100_000),
            )
        )
    return out


# --- de provider ------------------------------------------------------------------------------


class Tmdb:
    """De provider TMDB met één sleutel (MetadataProvider)."""

    name = NAME

    def __init__(self, settings: Settings, key: str) -> None:
        self.settings = settings
        self._key = key
        self.base = settings.media_tmdb_url.rstrip("/") + "/3"
        self.image_base = settings.media_tmdb_image_url.rstrip("/") + "/t/p"

    def __repr__(self) -> str:  # nooit de sleutel
        return "Tmdb()"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}"}
        return await http.get_json(NAME, self.base + path, params=params, headers=headers)

    @staticmethod
    def _lang(lang: str) -> str:
        return lang if LANG.match(lang or "") else FALLBACK_LANG

    async def check(self) -> None:
        data = await self._get("/configuration")
        if not isinstance(data.get("images"), dict):
            raise ProviderUnavailable("tmdb: onverwacht antwoord")

    async def search(self, kind: str, title: str, year: int | None, lang: str) -> list[Candidate]:
        tmdb_kind = kind_of(kind)
        query = " ".join(title.split())[:200]
        if not query:
            return []
        params: dict[str, Any] = {
            "query": query,
            "language": self._lang(lang),
            "include_adult": "false",
            "page": 1,
        }
        if year is not None:
            params["primary_release_year" if tmdb_kind == "movie" else "first_air_date_year"] = year
        data = await self._get(f"/search/{tmdb_kind}", params)
        out = []
        for result in _list(data.get("results"))[:MAX_RESULTS]:
            c = candidate(result, tmdb_kind)
            if c is not None:
                out.append(c)
        return out

    async def find(self, imdb_id: str, kind: str, lang: str) -> list[Candidate]:
        """Een film of serie op zijn IMDb-id (`/3/find`)."""
        if not IMDB.match(imdb_id or ""):
            return []
        params = {"external_source": "imdb_id", "language": self._lang(lang)}
        data = await self._get(f"/find/{imdb_id}", params)
        tmdb_kind = kind_of(kind)
        key = "movie_results" if tmdb_kind == "movie" else "tv_results"
        out = []
        for result in _list(data.get(key))[:MAX_RESULTS]:
            c = candidate(result, tmdb_kind)
            if c is not None:
                out.append(c)
        return out

    async def details(self, kind: str, id: str, lang: str) -> Details:
        tmdb_kind, number = parse_id(id)
        lang = self._lang(lang)
        params = {"language": lang, "append_to_response": "external_ids"}
        data = await self._get(f"/{tmdb_kind}/{number}", params)
        result = details_of(data, tmdb_kind)
        if not result.overview and lang != FALLBACK_LANG:
            fallback = await self._get(f"/{tmdb_kind}/{number}", {"language": FALLBACK_LANG})
            result.overview = _overview(fallback.get("overview"))
        return result

    async def season(self, show_id: str, n: int, lang: str) -> list[EpisodeDetails]:
        tmdb_kind, number = parse_id(show_id)
        if tmdb_kind != "tv":
            raise ValueError("geen serie")
        lang = self._lang(lang)
        data = await self._get(f"/tv/{number}/season/{int(n)}", {"language": lang})
        episodes = episodes_of(data, show_id, int(n))
        if lang != FALLBACK_LANG and any(not e.overview for e in episodes):
            fallback = await self._get(f"/tv/{number}/season/{int(n)}", {"language": FALLBACK_LANG})
            other = {e.episode: e for e in episodes_of(fallback, show_id, int(n))}
            for e in episodes:
                if not e.overview and e.episode in other:
                    e.overview = other[e.episode].overview
                if not e.title and e.episode in other:
                    e.title = other[e.episode].title
        return episodes

    async def images(self, kind: str, id: str) -> list[ImageRef]:
        return (await self.details(kind, id, FALLBACK_LANG)).images

    def image_url(self, kind: str, path: str) -> str | None:
        return image_url(self.settings, kind, path)


def image_url(settings: Settings, kind: str, path: str) -> str | None:
    """Het adres van een plaatje op de plaatjeshost (de maat van Keuze 40; nooit original). Er
    gaat geen sleutel naar de plaatjeshost."""
    if not IMAGE_PATH.match(path or ""):
        return None
    base = settings.media_tmdb_image_url.rstrip("/") + "/t/p"
    return f"{base}/{SIZES.get(kind, 'w500')}{path}"


def provider() -> MetadataProvider | None:
    """De provider als er een werkende sleutel is (zoals de laatste `secrets.load` las), anders
    None (alleen lokale metadata)."""
    if not secrets.usable():
        return None
    from app.media import runtime

    return Tmdb(runtime.settings(), secrets.tmdb_key() or "")


async def check_key(key: str, settings: Settings | None = None) -> None:
    """Werkt deze sleutel? (`GET /3/configuration`; voor secrets.save_tmdb)."""
    from app.media import runtime

    await Tmdb(settings or runtime.settings(), key).check()
