"""De data van de nep-providers: `fake_providers/data/*.json`, met de hand geschreven (M8).

Elk bestand is één JSON-object. De eerste sleutel is `"_nep": "nepdata, niet echt"`, zodat
niemand het voor echte gegevens aanziet (§4.4). Verder alleen `movies`, `shows` en `releases`.
Verzonnen titels en id's: elk TMDB-id is ≥ 900000. Een paar werken uit het publieke domein mogen,
maar ook dan met een verzonnen id.

    {
      "_nep": "nepdata, niet echt",
      "movies": [{
        "id": 900001,
        "title": {"en-US": "The Fake Harbour", "nl-NL": "De Nephaven"},   # of één string
        "original_title": "De Nephaven",              # standaard: de titel in en-US
        "original_language": "nl",
        "release_date": "2004-05-01",                 # of ""
        "overview": {"en-US": "A harbour.", "nl-NL": ""},  # ontbreekt een taal: ""
        "alt_titles": ["Nephaven"],                   # ook gevonden bij zoeken
        "popularity": 12.5, "runtime": 101, "imdb_id": "tt9000001",
        "genres": [{"id": 18, "name": "Drama"}],
        "poster_path": "/p900001.jpg", "backdrop_path": "/b900001.jpg",
        "append": {"credits": {"cast": []}}          # voor append_to_response, naast external_ids
      }],
      "shows": [{
        "id": 900101, "name": {"en-US": "…"}, "original_name": "…", "first_air_date": "2010-01-01",
        "overview": "…", "imdb_id": "tt9000101", "tvdb_id": 9000101,
        "poster_path": "/p900101.jpg", "backdrop_path": "/b900101.jpg",
        "seasons": [{"season_number": 1, "name": "Seizoen 1", "air_date": "2010-01-01",
                     "poster_path": "/s900101-1.jpg",
                     "episodes": [{"episode_number": 1, "name": "…", "overview": "…",
                                   "air_date": "2010-01-01", "runtime": 42,
                                   "still_path": "/e900101-1-1.jpg"}]}]
      }],
      "releases": [{
        "id": "<uuid>", "title": "…", "artist": "…", "artist_id": "<uuid>",
        "artist_sort": "…", "date": "1999-03-01", "country": "NL", "barcode": "",
        "release_group": {"id": "<uuid>", "title": "…", "primary_type": "Album"},
        "tracks": [{"title": "…", "length_ms": 201000}],   # of alleen "track_count"
        "cover": true                                       # een voorkant in de CAA
      }]
    }

Onbekende velden in een film of serie gaan ongewijzigd mee in de details (M8 kan er zo iets bij
zetten zonder de kern te veranderen). Fouten in de data geven `DataError` met het bestand en de
plek: liever meteen falen dan een test die om een tikfout iets anders test.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NEP = "nepdata, niet echt"
MIN_ID = 900_000
DATA_DIR = Path(__file__).parent / "data"

IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_-]{1,64}\.jpg$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RELEASE_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
IMDB = re.compile(r"^tt\d{7,8}$")
LANGUAGE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
TOP_KEYS = {"_nep", "movies", "shows", "releases"}


class DataError(ValueError):
    pass


@dataclass
class Data:
    """De ingelezen data. `images`: elk pad van een plaatje met zijn soort (`poster`,
    `backdrop`, `still`), voor de plaatjeshost en de juiste verhouding."""

    movies: dict[int, dict[str, Any]] = field(default_factory=dict)
    shows: dict[int, dict[str, Any]] = field(default_factory=dict)
    releases: dict[str, dict[str, Any]] = field(default_factory=dict)
    images: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_documents(cls, *documents: Mapping[str, Any], names: list[str] | None = None) -> Data:
        data = cls()
        for i, document in enumerate(documents):
            where = names[i] if names else f"document {i + 1}"
            data._add(document, where)
        return data

    def counts(self) -> dict[str, int]:
        return {
            "movies": len(self.movies),
            "shows": len(self.shows),
            "releases": len(self.releases),
            "images": len(self.images),
        }

    # --- inlezen en controleren ---

    def _add(self, document: Mapping[str, Any], where: str) -> None:
        if not isinstance(document, Mapping):
            raise DataError(f"{where}: geen JSON-object")
        keys = list(document)
        if not keys or keys[0] != "_nep" or document["_nep"] != NEP:
            raise DataError(f'{where}: de eerste sleutel moet "_nep": "{NEP}" zijn')
        unknown = set(keys) - TOP_KEYS
        if unknown:
            raise DataError(f"{where}: onbekende sleutels {sorted(unknown)}")
        for i, movie in enumerate(_list(document, "movies", where)):
            self._movie(movie, f"{where}: movies[{i}]")
        for i, show in enumerate(_list(document, "shows", where)):
            self._show(show, f"{where}: shows[{i}]")
        for i, release in enumerate(_list(document, "releases", where)):
            self._release(release, f"{where}: releases[{i}]")

    def _image(self, value: Any, kind: str, where: str) -> None:
        if value is None:
            return
        if not isinstance(value, str) or not IMAGE_PATH.match(value):
            raise DataError(f"{where}: {value!r} is geen pad als /naam.jpg")
        known = self.images.get(value)
        if known is not None and known != kind:
            raise DataError(f"{where}: {value} is al een {known}, niet ook een {kind}")
        self.images[value] = kind

    def _movie(self, movie: Any, where: str) -> None:
        movie = _object(movie, where)
        movie_id = _tmdb_id(movie.get("id"), where)
        if movie_id in self.movies:
            raise DataError(f"{where}: film {movie_id} staat er twee keer")
        _text(movie.get("title"), f"{where}.title", required=True)
        _text(movie.get("overview"), f"{where}.overview")
        _optional(movie, "original_title", str, where)
        _optional(movie, "original_language", str, where)
        _date(movie.get("release_date"), f"{where}.release_date")
        _imdb(movie.get("imdb_id"), where)
        _number(movie.get("popularity"), f"{where}.popularity")
        _strings(movie.get("alt_titles"), f"{where}.alt_titles")
        _optional(movie, "append", dict, where)
        self._image(movie.get("poster_path"), "poster", f"{where}.poster_path")
        self._image(movie.get("backdrop_path"), "backdrop", f"{where}.backdrop_path")
        self.movies[movie_id] = dict(movie)

    def _show(self, show: Any, where: str) -> None:
        show = _object(show, where)
        show_id = _tmdb_id(show.get("id"), where)
        if show_id in self.shows:
            raise DataError(f"{where}: serie {show_id} staat er twee keer")
        _text(show.get("name"), f"{where}.name", required=True)
        _text(show.get("overview"), f"{where}.overview")
        _optional(show, "original_name", str, where)
        _date(show.get("first_air_date"), f"{where}.first_air_date")
        _imdb(show.get("imdb_id"), where)
        _number(show.get("popularity"), f"{where}.popularity")
        _strings(show.get("alt_titles"), f"{where}.alt_titles")
        _optional(show, "append", dict, where)
        self._image(show.get("poster_path"), "poster", f"{where}.poster_path")
        self._image(show.get("backdrop_path"), "backdrop", f"{where}.backdrop_path")
        seasons = show.get("seasons", [])
        if not isinstance(seasons, list):
            raise DataError(f"{where}.seasons: geen lijst")
        numbers: set[int] = set()
        for i, season in enumerate(seasons):
            here = f"{where}.seasons[{i}]"
            season = _object(season, here)
            n = season.get("season_number")
            if not isinstance(n, int) or isinstance(n, bool) or n < 0 or n in numbers:
                raise DataError(f"{here}: season_number ontbreekt, is negatief of dubbel")
            numbers.add(n)
            _text(season.get("name"), f"{here}.name")
            _date(season.get("air_date"), f"{here}.air_date")
            self._image(season.get("poster_path"), "poster", f"{here}.poster_path")
            episodes = season.get("episodes", [])
            if not isinstance(episodes, list):
                raise DataError(f"{here}.episodes: geen lijst")
            seen: set[int] = set()
            for j, episode in enumerate(episodes):
                spot = f"{here}.episodes[{j}]"
                episode = _object(episode, spot)
                e = episode.get("episode_number")
                if not isinstance(e, int) or isinstance(e, bool) or e < 1 or e in seen:
                    raise DataError(f"{spot}: episode_number ontbreekt, is < 1 of dubbel")
                seen.add(e)
                _text(episode.get("name"), f"{spot}.name")
                _text(episode.get("overview"), f"{spot}.overview")
                _date(episode.get("air_date"), f"{spot}.air_date")
                _imdb(episode.get("imdb_id"), spot)
                self._image(episode.get("still_path"), "still", f"{spot}.still_path")
        self.shows[show_id] = dict(show)

    def _release(self, release: Any, where: str) -> None:
        release = _object(release, where)
        release_id = _mbid(release.get("id"), f"{where}.id")
        if release_id in self.releases:
            raise DataError(f"{where}: release {release_id} staat er twee keer")
        for key in ("title", "artist"):
            if not isinstance(release.get(key), str) or not release[key]:
                raise DataError(f"{where}.{key}: ontbreekt of is leeg")
        _mbid(release.get("artist_id"), f"{where}.artist_id")
        date = release.get("date", "")
        if not isinstance(date, str) or (date and not RELEASE_DATE.match(date)):
            raise DataError(f"{where}.date: {date!r} is geen JJJJ, JJJJ-MM of JJJJ-MM-DD")
        group = release.get("release_group")
        if group is not None:
            group = _object(group, f"{where}.release_group")
            _mbid(group.get("id"), f"{where}.release_group.id")
        tracks = release.get("tracks")
        if tracks is not None:
            if not isinstance(tracks, list) or not tracks:
                raise DataError(f"{where}.tracks: geen lijst, of leeg")
            for i, track in enumerate(tracks):
                track = _object(track, f"{where}.tracks[{i}]")
                if not isinstance(track.get("title"), str):
                    raise DataError(f"{where}.tracks[{i}].title: ontbreekt")
        count = release.get("track_count")
        if tracks is None and (not isinstance(count, int) or isinstance(count, bool) or count < 1):
            raise DataError(f"{where}: tracks of track_count (≥ 1) is nodig")
        if "cover" in release and not isinstance(release["cover"], bool):
            raise DataError(f"{where}.cover: true of false")
        self.releases[release_id] = dict(release)


def load(directory: Path | str | None = None) -> Data:
    """Alle `*.json` in de map (standaard `fake_providers/data/`), op naam. Een map die er niet
    is, geeft lege data: M8 vult hem."""
    directory = Path(directory) if directory is not None else DATA_DIR
    if not directory.is_dir():
        return Data()
    files = sorted(directory.glob("*.json"))
    documents = []
    for path in files:
        try:
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise DataError(f"{path.name}: niet te lezen als JSON ({e})") from None
    return Data.from_documents(*documents, names=[p.name for p in files])


# --- kleine controles ---


def _list(document: Mapping[str, Any], key: str, where: str) -> list[Any]:
    value = document.get(key, [])
    if not isinstance(value, list):
        raise DataError(f"{where}: {key} is geen lijst")
    return value


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DataError(f"{where}: geen object")
    return value


def _tmdb_id(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < MIN_ID:
        raise DataError(f"{where}.id: {value!r} is geen verzonnen id (≥ {MIN_ID})")
    return value


def _mbid(value: Any, where: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except ValueError:
        raise DataError(f"{where}: {value!r} is geen MBID (uuid)") from None
    if str(parsed) != value:
        raise DataError(f"{where}: {value!r} niet in de vorm 8-4-4-4-12 met kleine letters")
    return value


def _text(value: Any, where: str, *, required: bool = False) -> None:
    """Een string, of per taal ({"en-US": "…", "nl-NL": "…"})."""
    if value is None and not required:
        return
    if isinstance(value, str):
        if required and not value:
            raise DataError(f"{where}: leeg")
        return
    if isinstance(value, dict) and value:
        for lang, text in value.items():
            if not LANGUAGE.match(lang) or not isinstance(text, str):
                raise DataError(f"{where}: {lang!r} is geen taal als nl-NL, of geen tekst")
        if required and not any(value.values()):
            raise DataError(f"{where}: in geen enkele taal ingevuld")
        return
    raise DataError(f"{where}: geen tekst en geen object per taal")


def _date(value: Any, where: str) -> None:
    if value is None or value == "":
        return
    if not isinstance(value, str) or not DATE.match(value):
        raise DataError(f"{where}: {value!r} is geen JJJJ-MM-DD")


def _imdb(value: Any, where: str) -> None:
    if value is not None and (not isinstance(value, str) or not IMDB.match(value)):
        raise DataError(f"{where}.imdb_id: {value!r} is geen tt0000000")


def _number(value: Any, where: str) -> None:
    if value is not None and (not isinstance(value, int | float) or isinstance(value, bool)):
        raise DataError(f"{where}: geen getal")


def _strings(value: Any, where: str) -> None:
    if value is not None and (
        not isinstance(value, list) or not all(isinstance(x, str) for x in value)
    ):
        raise DataError(f"{where}: geen lijst van strings")


def _optional(obj: Mapping[str, Any], key: str, kind: type, where: str) -> None:
    if key in obj and obj[key] is not None and not isinstance(obj[key], kind):
        raise DataError(f"{where}.{key}: verwacht {kind.__name__}")
