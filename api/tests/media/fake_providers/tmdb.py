"""De nep-TMDB (API v3) en zijn plaatjeshost, naar de documentatie van TMDB [doc] (§4.4).

- Elk verzoek naar de API eist `Authorization: Bearer <tmdb_key>`; anders 401 met
  `status_code: 7`. Ook een `api_key` in het adres geeft 401: bij ons staat een sleutel nooit in
  een adres (Keuze 40), en de nep vangt het als iemand dat toch doet.
- Meer dan `tmdb_rate` (20) per seconde: 429 met `Retry-After: 1` en `status_code: 25`.
- `language` (standaard `en-US`): een titel of naam valt terug op `en-US`, dan op de eerste
  die er is (zoals TMDB de originele titel geeft); een samenvatting die er in die taal niet is,
  is leeg (`""`), zodat de terugval van de app naar `en-US` getest wordt.
- `append_to_response`: `external_ids` maakt de nep zelf (uit `imdb_id`, `tvdb_id`); de rest
  komt uit `append` in de data. Onbekende namen laat TMDB weg, de nep ook.
- Onbekend id of eindpunt: 404 met `status_code: 34`.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from .app import FakeProviders

PAGE_SIZE = 20
MAX_PAGE = 500
CHANGE_KEYS = [
    "adult", "air_date", "also_known_as", "alternative_titles", "biography", "birthday",
    "budget", "cast", "certifications", "character_names", "created_by", "crew", "deathday",
    "episode", "episode_number", "episode_run_time", "freebase_id", "freebase_mid",
    "general", "genres", "guest_stars", "homepage", "images", "imdb_id", "languages",
    "name", "network", "origin_country", "original_name", "original_title", "overview",
    "parts", "place_of_birth", "plot_keywords", "production_code", "production_companies",
    "production_countries", "releases", "revenue", "runtime", "season", "season_number",
    "season_regular", "spoken_languages", "status", "tagline", "title", "translations",
    "tvdb_id", "tvrage_id", "type", "video", "videos",
]  # fmt: skip
SIZES = {
    "backdrop_sizes": ["w300", "w780", "w1280", "original"],
    "logo_sizes": ["w45", "w92", "w154", "w185", "w300", "w500", "original"],
    "poster_sizes": ["w92", "w154", "w185", "w342", "w500", "w780", "original"],
    "profile_sizes": ["w45", "w185", "h632", "original"],
    "still_sizes": ["w92", "w185", "w300", "original"],
}
# Wat de plaatjeshost geeft: elke breedte uit de configuratie, en `original`
IMAGE_SIZES = sorted({s for sizes in SIZES.values() for s in sizes if s[0] == "w"} | {"original"})
EXTERNAL_SOURCES = {
    "imdb_id", "facebook_id", "instagram_id", "tvdb_id", "tiktok_id", "twitter_id",
    "wikidata_id", "youtube_id",
}  # fmt: skip
IMAGE_PATH = re.compile(r"^/t/p/([a-z0-9]+)(/[A-Za-z0-9_-]{1,64}\.jpg)$")
ID = re.compile(r"^[1-9][0-9]{0,9}$")


def error(status: int, code: int, message: str, **headers: str) -> JSONResponse:
    body = {"success": False, "status_code": code, "status_message": message}
    return JSONResponse(body, status_code=status, headers=headers or None)


def not_found() -> JSONResponse:
    return error(404, 34, "The resource you requested could not be found.")


def unprocessable(*errors: str) -> JSONResponse:
    return JSONResponse({"errors": list(errors)}, status_code=422)


# --- teksten per taal ---


def pick(value: Any, language: str, *, fallback: bool) -> str:
    """De tekst in `language` (ook `nl` voor `nl-NL`). Ontbreekt hij: met `fallback` die in
    `en-US` of de eerste; zonder `""`."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if value.get(language):
        return value[language]
    primary = language.split("-")[0]
    for lang, text in value.items():
        if lang.split("-")[0] == primary and text:
            return text
    if not fallback:
        return ""
    if value.get("en-US"):
        return value["en-US"]
    return next((text for text in value.values() if text), "")


def norm(text: str) -> list[str]:
    """Woorden om op te zoeken: zonder accenten en hoofdletters, alles behalve letters en cijfers
    als scheiding."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", " ", text).split()


def names(item: dict[str, Any], key: str, original: str) -> list[str]:
    value = item.get(key)
    out = list(value.values()) if isinstance(value, dict) else [value]
    out += [item.get(original)] + list(item.get("alt_titles") or [])
    return [n for n in out if isinstance(n, str) and n]


def score(query: list[str], candidates: list[str]) -> int:
    """3: precies een titel; 2: een titel begint ermee; 1: elk woord staat erin; 0: niet."""
    best = 0
    for candidate in candidates:
        words = norm(candidate)
        if words == query:
            return 3
        if words[: len(query)] == query:
            best = max(best, 2)
        elif all(w in words for w in query):
            best = max(best, 1)
    return best


# --- de vormen van TMDB ---


def year(date: Any) -> int | None:
    return int(date[:4]) if isinstance(date, str) and len(date) >= 4 else None


def movie_title(movie: dict[str, Any], language: str) -> str:
    return pick(movie.get("title"), language, fallback=True)


def original_title(movie: dict[str, Any]) -> str:
    return movie.get("original_title") or pick(movie.get("title"), "en-US", fallback=True)


def show_name(show: dict[str, Any], language: str) -> str:
    return pick(show.get("name"), language, fallback=True)


def original_name(show: dict[str, Any]) -> str:
    return show.get("original_name") or pick(show.get("name"), "en-US", fallback=True)


def movie_result(movie: dict[str, Any], language: str) -> dict[str, Any]:
    """Een film zoals in de zoekresultaten en in `find`."""
    return {
        "adult": False,
        "backdrop_path": movie.get("backdrop_path"),
        "genre_ids": [g["id"] for g in movie.get("genres", []) if isinstance(g, dict)],
        "id": movie["id"],
        "original_language": movie.get("original_language", "en"),
        "original_title": original_title(movie),
        "overview": pick(movie.get("overview"), language, fallback=False),
        "popularity": movie.get("popularity", 1.0),
        "poster_path": movie.get("poster_path"),
        "release_date": movie.get("release_date", ""),
        "title": movie_title(movie, language),
        "video": False,
        "vote_average": movie.get("vote_average", 0.0),
        "vote_count": movie.get("vote_count", 0),
    }


def show_result(show: dict[str, Any], language: str) -> dict[str, Any]:
    return {
        "adult": False,
        "backdrop_path": show.get("backdrop_path"),
        "genre_ids": [g["id"] for g in show.get("genres", []) if isinstance(g, dict)],
        "id": show["id"],
        "origin_country": show.get("origin_country", []),
        "original_language": show.get("original_language", "en"),
        "original_name": original_name(show),
        "overview": pick(show.get("overview"), language, fallback=False),
        "popularity": show.get("popularity", 1.0),
        "poster_path": show.get("poster_path"),
        "first_air_date": show.get("first_air_date", ""),
        "name": show_name(show, language),
        "vote_average": show.get("vote_average", 0.0),
        "vote_count": show.get("vote_count", 0),
    }


def season_id(show: dict[str, Any], season: dict[str, Any]) -> int:
    return show["id"] * 1000 + season["season_number"]


def episode_id(show: dict[str, Any], season: dict[str, Any], episode: dict[str, Any]) -> int:
    return season_id(show, season) * 1000 + episode["episode_number"]


def episode_name(episode: dict[str, Any], language: str) -> str:
    return pick(episode.get("name"), language, fallback=True) or (
        f"Episode {episode['episode_number']}"
    )


def episode_result(
    show: dict[str, Any], season: dict[str, Any], episode: dict[str, Any], language: str
) -> dict[str, Any]:
    return {
        "air_date": episode.get("air_date", ""),
        "episode_number": episode["episode_number"],
        "episode_type": episode.get("episode_type", "standard"),
        "id": episode_id(show, season, episode),
        "name": episode_name(episode, language),
        "overview": pick(episode.get("overview"), language, fallback=False),
        "production_code": "",
        "runtime": episode.get("runtime"),
        "season_number": season["season_number"],
        "show_id": show["id"],
        "still_path": episode.get("still_path"),
        "vote_average": 0.0,
        "vote_count": 0,
    }


def season_summary(show: dict[str, Any], season: dict[str, Any], language: str) -> dict[str, Any]:
    return {
        "air_date": season.get("air_date", ""),
        "episode_count": len(season.get("episodes", [])),
        "id": season_id(show, season),
        "name": pick(season.get("name"), language, fallback=True)
        or f"Season {season['season_number']}",
        "overview": pick(season.get("overview"), language, fallback=False),
        "poster_path": season.get("poster_path"),
        "season_number": season["season_number"],
        "vote_average": 0.0,
    }


def movie_external_ids(movie: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": movie["id"],
        "imdb_id": movie.get("imdb_id"),
        "wikidata_id": None,
        "facebook_id": None,
        "instagram_id": None,
        "twitter_id": None,
    }


def show_external_ids(show: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": show["id"],
        "imdb_id": show.get("imdb_id"),
        "freebase_mid": None,
        "freebase_id": None,
        "tvdb_id": show.get("tvdb_id"),
        "tvrage_id": None,
        "wikidata_id": None,
        "facebook_id": None,
        "instagram_id": None,
        "twitter_id": None,
    }


MOVIE_KEYS = {"title", "overview", "alt_titles", "append", "original_title"}
SHOW_KEYS = {"name", "overview", "alt_titles", "append", "original_name", "seasons"}


def movie_details(movie: dict[str, Any], language: str, append: list[str]) -> dict[str, Any]:
    extra = {k: v for k, v in movie.items() if k not in MOVIE_KEYS}
    body = {
        "adult": False,
        "backdrop_path": None,
        "belongs_to_collection": None,
        "budget": 0,
        "genres": [],
        "homepage": "",
        "imdb_id": None,
        "original_language": "en",
        "popularity": 1.0,
        "poster_path": None,
        "production_companies": [],
        "production_countries": [],
        "release_date": "",
        "revenue": 0,
        "runtime": None,
        "spoken_languages": [],
        "status": "Released",
        "tagline": "",
        "video": False,
        "vote_average": 0.0,
        "vote_count": 0,
        **extra,
        "id": movie["id"],
        "original_title": original_title(movie),
        "overview": pick(movie.get("overview"), language, fallback=False),
        "title": movie_title(movie, language),
    }
    return _append(body, movie, append, movie_external_ids)


def show_details(show: dict[str, Any], language: str, append: list[str]) -> dict[str, Any]:
    extra = {k: v for k, v in show.items() if k not in SHOW_KEYS}
    seasons = sorted(show.get("seasons", []), key=lambda s: s["season_number"])
    dates = [e.get("air_date") for s in seasons for e in s.get("episodes", []) if e.get("air_date")]
    body = {
        "adult": False,
        "backdrop_path": None,
        "created_by": [],
        "episode_run_time": [],
        "first_air_date": "",
        "genres": [],
        "homepage": "",
        "in_production": False,
        "languages": [],
        "last_air_date": max(dates) if dates else None,
        "number_of_episodes": sum(len(s.get("episodes", [])) for s in seasons),
        "number_of_seasons": len([s for s in seasons if s["season_number"] > 0]),
        "origin_country": [],
        "original_language": "en",
        "popularity": 1.0,
        "poster_path": None,
        "status": "Ended",
        "type": "Scripted",
        "vote_average": 0.0,
        "vote_count": 0,
        **extra,
        "id": show["id"],
        "name": show_name(show, language),
        "original_name": original_name(show),
        "overview": pick(show.get("overview"), language, fallback=False),
        "seasons": [season_summary(show, s, language) for s in seasons],
    }
    return _append(body, show, append, show_external_ids)


def _append(body: dict[str, Any], item: dict[str, Any], append: list[str], external) -> dict:
    extra = item.get("append") or {}
    for name in append:
        if name == "external_ids":
            body[name] = external(item)
        elif name in extra:
            body[name] = extra[name]
    return body


def season_details(show: dict[str, Any], season: dict[str, Any], language: str) -> dict:
    episodes = sorted(season.get("episodes", []), key=lambda e: e["episode_number"])
    return {
        "_id": f"{season_id(show, season):024x}",
        "air_date": season.get("air_date", ""),
        "episodes": [
            {**episode_result(show, season, e, language), "crew": [], "guest_stars": []}
            for e in episodes
        ],
        "name": pick(season.get("name"), language, fallback=True)
        or f"Season {season['season_number']}",
        "overview": pick(season.get("overview"), language, fallback=False),
        "id": season_id(show, season),
        "poster_path": season.get("poster_path"),
        "season_number": season["season_number"],
        "vote_average": 0.0,
    }


# --- de eindpunten ---


def _page(request: Request) -> int | JSONResponse:
    raw = request.query_params.get("page", "1")
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_PAGE:
        return unprocessable(f"page must be less than or equal to {MAX_PAGE}")
    return int(raw)


def _paged(results: list[dict[str, Any]], page: int) -> JSONResponse:
    start = (page - 1) * PAGE_SIZE
    return JSONResponse(
        {
            "page": page,
            "results": results[start : start + PAGE_SIZE],
            "total_pages": max(1, math.ceil(len(results) / PAGE_SIZE)),
            "total_results": len(results),
        }
    )


def _year(request: Request, *keys: str) -> int | None | JSONResponse:
    for key in keys:
        raw = request.query_params.get(key)
        if raw is not None and raw != "":
            if not raw.isdigit() or len(raw) != 4:
                return unprocessable(f"{key} must be a valid year")
            return int(raw)
    return None


def search(fake: FakeProviders, request: Request, kind: str) -> Response:
    query = norm(request.query_params.get("query", ""))
    if not query:
        return unprocessable("query must be provided")
    page = _page(request)
    if isinstance(page, Response):
        return page
    language = request.query_params.get("language") or "en-US"
    if kind == "movie":
        items = fake.data.movies
        keys = ("title", "original_title", "release_date")
        wanted = _year(request, "primary_release_year", "year")
        make = movie_result
    else:
        items = fake.data.shows
        keys = ("name", "original_name", "first_air_date")
        wanted = _year(request, "first_air_date_year", "year")
        make = show_result
    if isinstance(wanted, Response):
        return wanted
    title_key, original, date_key = keys
    found = []
    for item in items.values():
        s = score(query, names(item, title_key, original))
        if s and (wanted is None or year(item.get(date_key)) == wanted):
            found.append((-s, -float(item.get("popularity", 1.0)), item["id"], item))
    found.sort(key=lambda t: t[:3])
    return _paged([make(t[3], language) for t in found], page)


def find(fake: FakeProviders, request: Request, external_id: str) -> Response:
    source = request.query_params.get("external_source")
    if source not in EXTERNAL_SOURCES:
        return error(422, 5, "Invalid parameters: Your request parameters are incorrect.")
    language = request.query_params.get("language") or "en-US"
    body: dict[str, list[Any]] = {
        "movie_results": [],
        "person_results": [],
        "tv_results": [],
        "tv_episode_results": [],
        "tv_season_results": [],
    }
    if source == "imdb_id":
        for movie in fake.data.movies.values():
            if movie.get("imdb_id") == external_id:
                body["movie_results"].append(movie_result(movie, language))
    for show in fake.data.shows.values():
        if source == "imdb_id" and show.get("imdb_id") == external_id:
            body["tv_results"].append(show_result(show, language))
        if source == "tvdb_id" and str(show.get("tvdb_id")) == external_id:
            body["tv_results"].append(show_result(show, language))
        for season in show.get("seasons", []):
            for episode in season.get("episodes", []):
                if source == "imdb_id" and episode.get("imdb_id") == external_id:
                    body["tv_episode_results"].append(
                        episode_result(show, season, episode, language)
                    )
    return JSONResponse(body)


def configuration(fake: FakeProviders) -> JSONResponse:
    base = fake.config.image_url.rstrip("/") + "/t/p/"
    return JSONResponse(
        {
            "images": {
                "base_url": re.sub(r"^https://", "http://", base),
                "secure_base_url": base,
                **SIZES,
            },
            "change_keys": CHANGE_KEYS,
        }
    )


def _auth(fake: FakeProviders, request: Request) -> bool:
    if "api_key" in request.query_params:
        return False
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ") and fake.key_ok(auth[7:])


async def api(fake: FakeProviders, request: Request, path: str) -> Response:
    """`api.themoviedb.org`: de API, versie 3."""
    count = fake.over_rate("tmdb", fake.config.tmdb_rate)
    if count is not None:
        limit = fake.config.tmdb_rate
        message = f"Your request count ({count}) is over the allowed limit of ({limit})."
        return error(429, 25, message, **{"Retry-After": "1"})
    if not _auth(fake, request):
        return error(401, 7, "Invalid API key: You must be granted a valid key.")
    if request.method not in ("GET", "HEAD"):
        return error(405, 3, "Authentication failed: You do not have permissions to access it.")
    parts = path.strip("/").split("/")
    if parts[0] != "3":
        return not_found()
    parts = parts[1:]
    language = request.query_params.get("language") or "en-US"
    append = [a for a in request.query_params.get("append_to_response", "").split(",") if a]
    match parts:
        case ["configuration"]:
            return configuration(fake)
        case ["search", ("movie" | "tv") as kind]:
            return search(fake, request, kind)
        case ["find", external_id]:
            return find(fake, request, external_id)
        case ["movie", movie_id] if ID.match(movie_id):
            movie = fake.data.movies.get(int(movie_id))
            if movie is None:
                return not_found()
            return JSONResponse(movie_details(movie, language, append))
        case ["tv", show_id] if ID.match(show_id):
            show = fake.data.shows.get(int(show_id))
            if show is None:
                return not_found()
            return JSONResponse(show_details(show, language, append))
        case ["tv", show_id, "season", number] if ID.match(show_id) and number.isdigit():
            show = fake.data.shows.get(int(show_id))
            seasons = {s["season_number"]: s for s in (show or {}).get("seasons", [])}
            season = seasons.get(int(number))
            if show is None or season is None:
                return not_found()
            return JSONResponse(season_details(show, season, language))
    return not_found()


async def image(fake: FakeProviders, request: Request, path: str) -> Response:
    """`image.tmdb.org`: `/t/p/{maat}/{pad}` geeft een echte JPEG in die breedte."""
    m = IMAGE_PATH.match(path)
    if request.method not in ("GET", "HEAD") or m is None:
        return Response("Not Found", status_code=404, media_type="text/plain")
    size, name = m.groups()
    kind = fake.data.images.get(name)
    if size not in IMAGE_SIZES or kind is None:
        return Response("Not Found", status_code=404, media_type="text/plain")
    width = None if size == "original" else int(size[1:])
    body = fake.images.jpeg(name, kind, width)
    return Response(
        body, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000"}
    )
