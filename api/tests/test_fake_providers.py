"""De kern van de nep-providers (§3.6, §4.4): TMDB, zijn plaatjeshost, MusicBrainz, de CAA en de
tweede host; de fouten op bestelling, het logboek, het tempo en het laden van de data. Eén test
draait hem als proces, zoals de e2e (start, verzoeken, `drop`, stoppen op PID).

M8 vult de data en test zijn clients ertegen; hier staat alleen wat de kern belooft.
"""

from __future__ import annotations

import io
import json
import os
import uuid
from typing import Any

import httpx
import pytest
from PIL import Image

from tests.media.fake_providers import (
    NEP,
    Data,
    DataError,
    FakeConfig,
    create_app,
    load,
    transport,
)
from tests.media.fake_providers.musicbrainz import image_id
from tests.media.fake_providers.process import FakeProvidersProcess

KEY = "nep-sleutel-" + "x" * 20
UA = "PlatformTest/0.7.0 ( https://example.org/repo )"
TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
MB = "https://musicbrainz.org/ws/2"
CAA = "https://coverartarchive.org"


def mbid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{name}.nep"))


R1, R2, A1, A2, G1 = (mbid(n) for n in ("r1", "r2", "a1", "a2", "g1"))

DOC: dict[str, Any] = {
    "_nep": NEP,
    "movies": [
        {
            "id": 900001,
            "title": {"en-US": "The Fake Harbour", "nl-NL": "De Nephaven"},
            "original_title": "De Nephaven",
            "original_language": "nl",
            "release_date": "2004-05-01",
            "overview": {"en-US": "A harbour that is not real.", "nl-NL": "Een nephaven."},
            "alt_titles": ["Nephaven"],
            "popularity": 12.5,
            "runtime": 101,
            "imdb_id": "tt9000001",
            "genres": [{"id": 18, "name": "Drama"}],
            "poster_path": "/p900001.jpg",
            "backdrop_path": "/b900001.jpg",
            "append": {"credits": {"cast": [{"name": "Nep Acteur"}], "crew": []}},
            "tagline": "Nergens",
        },
        {
            "id": 900002,
            "title": "Harbour Lights",
            "release_date": "1999-01-01",
            "overview": {"en-US": "Lights."},
            "popularity": 50.0,
            "imdb_id": "tt9000002",
        },
        {"id": 900003, "title": "Harbour", "release_date": "2010-03-03", "popularity": 1.0},
        {"id": 900004, "title": {"en-US": "Café Société Nep"}, "release_date": ""},
    ],
    "shows": [
        {
            "id": 900101,
            "name": {"en-US": "Fake Street", "nl-NL": "Nepstraat"},
            "original_name": "Nepstraat",
            "first_air_date": "2010-01-01",
            "overview": {"en-US": "A street."},
            "imdb_id": "tt9000101",
            "tvdb_id": 9000101,
            "poster_path": "/p900101.jpg",
            "seasons": [
                {"season_number": 0, "name": "Specials", "episodes": [{"episode_number": 1}]},
                {
                    "season_number": 1,
                    "name": {"en-US": "Season 1", "nl-NL": "Seizoen 1"},
                    "air_date": "2010-01-01",
                    "poster_path": "/s900101-1.jpg",
                    "episodes": [
                        {"episode_number": 2, "name": "Two", "air_date": "2010-01-08"},
                        {
                            "episode_number": 1,
                            "name": {"en-US": "Pilot", "nl-NL": "Proef"},
                            "overview": {"en-US": "The start."},
                            "air_date": "2010-01-01",
                            "runtime": 42,
                            "imdb_id": "tt9000102",
                            "still_path": "/e900101-1-1.jpg",
                        },
                    ],
                },
            ],
        }
    ],
    "releases": [
        {
            "id": R1,
            "title": "Nep Liedjes",
            "artist": "De Neppers",
            "artist_id": A1,
            "artist_sort": "Neppers, De",
            "date": "1999-03-01",
            "country": "NL",
            "barcode": "0000000000017",
            "release_group": {"id": G1, "title": "Nep Liedjes", "primary_type": "Album"},
            "tracks": [{"title": "Een", "length_ms": 201000}, {"title": "Twee"}],
            "cover": True,
        },
        {
            "id": R2,
            "title": 'Stilte "Live"',
            "artist": "Niemand",
            "artist_id": A2,
            "date": "2001",
            "track_count": 3,
            "cover": False,
        },
    ],
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(**options: Any):
    options.setdefault("tmdb_key", KEY)
    options.setdefault("data", Data.from_documents(DOC))
    options.setdefault("rate_limits", False)
    app = create_app(FakeConfig(**options))
    return app, httpx.AsyncClient(transport=transport(app))


AUTH = {"Authorization": f"Bearer {KEY}"}


async def tmdb(path: str, **params: Any) -> httpx.Response:
    _, c = make()
    async with c:
        return await c.get(f"{TMDB}{path}", params=params, headers=AUTH)


def jpeg_size(body: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(body)) as im:
        assert im.format == "JPEG"
        return im.size


# --- TMDB: sleutel, configuratie ---


@pytest.mark.anyio
async def test_tmdb_wil_een_bearer():
    app, c = make()
    async with c:
        r = await c.get(f"{TMDB}/configuration")
        assert r.status_code == 401
        assert r.json() == {
            "success": False,
            "status_code": 7,
            "status_message": "Invalid API key: You must be granted a valid key.",
        }
        r = await c.get(f"{TMDB}/configuration", headers={"Authorization": "Bearer fout"})
        assert r.status_code == 401
        r = await c.get(f"{TMDB}/configuration", headers={"Authorization": f"Basic {KEY}"})
        assert r.status_code == 401
        # een sleutel in het adres: altijd fout, ook naast een goede Bearer (Keuze 40)
        r = await c.get(f"{TMDB}/configuration", params={"api_key": KEY}, headers=AUTH)
        assert r.status_code == 401
        r = await c.get(f"{TMDB}/configuration", headers=AUTH)
        assert r.status_code == 200
    log = app.state.fake.log
    assert [e["auth"] for e in log] == [None, "bearer", "other", "bearer", "bearer"]
    assert [e["auth_ok"] for e in log] == [False, False, False, True, True]
    assert log[3]["query"] == "api_key=%2A%2A%2A"
    assert KEY not in json.dumps(log)


@pytest.mark.anyio
async def test_tmdb_zonder_sleutel_is_elke_bearer_fout():
    _, c = make(tmdb_key=None)
    async with c:
        assert (await c.get(f"{TMDB}/configuration", headers=AUTH)).status_code == 401


@pytest.mark.anyio
async def test_tmdb_configuratie():
    _, c = make(image_url="http://127.0.0.1:9/tmdb-img")
    async with c:
        body = (await c.get(f"{TMDB}/configuration", headers=AUTH)).json()
    images = body["images"]
    assert images["secure_base_url"] == "http://127.0.0.1:9/tmdb-img/t/p/"
    assert "w500" in images["poster_sizes"] and "w1280" in images["backdrop_sizes"]
    assert "w300" in images["still_sizes"] and "original" in images["poster_sizes"]
    assert "overview" in body["change_keys"]


# --- TMDB: zoeken ---


@pytest.mark.anyio
async def test_zoeken_naar_films():
    r = await tmdb("/search/movie", query="harbour")
    body = r.json()
    assert r.status_code == 200
    # eerst precies de titel, dan "begint ermee", dan "alle woorden"; gelijk: populairder eerst
    assert [m["id"] for m in body["results"]] == [900003, 900002, 900001]
    assert body["page"] == 1 and body["total_results"] == 3 and body["total_pages"] == 1
    first = body["results"][2]
    assert set(first) == {
        "adult", "backdrop_path", "genre_ids", "id", "original_language", "original_title",
        "overview", "popularity", "poster_path", "release_date", "title", "video",
        "vote_average", "vote_count",
    }  # fmt: skip
    assert first["genre_ids"] == [18] and first["original_title"] == "De Nephaven"
    # op een titel in een andere taal, een andere titel, en zonder accenten
    assert [m["id"] for m in (await tmdb("/search/movie", query="nephaven")).json()["results"]] == [
        900001
    ]
    hits = (await tmdb("/search/movie", query="CAFE societe")).json()["results"]
    assert [m["id"] for m in hits] == [900004]
    assert hits[0]["release_date"] == "" and hits[0]["overview"] == ""
    assert (await tmdb("/search/movie", query="onbekend")).json()["results"] == []


@pytest.mark.anyio
async def test_zoeken_met_jaar_en_fouten():
    hits = (await tmdb("/search/movie", query="harbour", year="1999")).json()["results"]
    assert [m["id"] for m in hits] == [900002]
    hits = (await tmdb("/search/movie", query="harbour", primary_release_year=2004)).json()
    assert [m["id"] for m in hits["results"]] == [900001]
    r = await tmdb("/search/movie")
    assert r.status_code == 422 and r.json() == {"errors": ["query must be provided"]}
    assert (await tmdb("/search/movie", query="  ")).status_code == 422
    assert (await tmdb("/search/movie", query="x", year="99")).status_code == 422
    assert (await tmdb("/search/movie", query="x", page=0)).status_code == 422
    assert (await tmdb("/search/movie", query="x", page=501)).status_code == 422


@pytest.mark.anyio
async def test_zoeken_bladert_per_20():
    movies = [
        {"id": 900500 + i, "title": f"Nep Reeks {i}", "popularity": float(i)} for i in range(25)
    ]
    _, c = make(data=Data.from_documents({"_nep": NEP, "movies": movies}))
    async with c:
        one = (await c.get(f"{TMDB}/search/movie?query=nep+reeks", headers=AUTH)).json()
        two = (await c.get(f"{TMDB}/search/movie?query=nep+reeks&page=2", headers=AUTH)).json()
        three = (await c.get(f"{TMDB}/search/movie?query=nep+reeks&page=3", headers=AUTH)).json()
    assert (one["total_results"], one["total_pages"]) == (25, 2)
    assert len(one["results"]) == 20 and len(two["results"]) == 5 and three["results"] == []
    assert one["results"][0]["id"] == 900524  # de populairste eerst
    assert {m["id"] for m in one["results"] + two["results"]} == {m["id"] for m in movies}


@pytest.mark.anyio
async def test_zoeken_naar_series():
    body = (await tmdb("/search/tv", query="nepstraat", language="nl-NL")).json()
    [show] = body["results"]
    assert show["id"] == 900101 and show["name"] == "Nepstraat"
    assert show["original_name"] == "Nepstraat" and show["first_air_date"] == "2010-01-01"
    assert show["overview"] == ""  # geen nl-NL: leeg, de app vraagt dan en-US
    assert (await tmdb("/search/tv", query="fake street", first_air_date_year=2011)).json()[
        "results"
    ] == []


# --- TMDB: talen ---


@pytest.mark.anyio
async def test_taal_en_terugval():
    nl = (await tmdb("/movie/900001", language="nl-NL")).json()
    assert nl["title"] == "De Nephaven" and nl["overview"] == "Een nephaven."
    assert (await tmdb("/movie/900001", language="nl")).json()["title"] == "De Nephaven"
    en = (await tmdb("/movie/900001")).json()
    assert en["title"] == "The Fake Harbour" and en["overview"] == "A harbour that is not real."
    # geen nl-NL: de titel valt terug op en-US, de samenvatting is leeg
    lights = (await tmdb("/movie/900002", language="nl-NL")).json()
    assert lights["title"] == "Harbour Lights" and lights["overview"] == ""
    assert (await tmdb("/movie/900002", language="en-US")).json()["overview"] == "Lights."
    # een string geldt voor elke taal; geen samenvatting: ""
    plain = (await tmdb("/movie/900003", language="de-DE")).json()
    assert plain["title"] == "Harbour" and plain["overview"] == ""


# --- TMDB: details ---


@pytest.mark.anyio
async def test_film_met_append():
    body = (await tmdb("/movie/900001", append_to_response="external_ids,credits,videos")).json()
    assert body["id"] == 900001 and body["imdb_id"] == "tt9000001" and body["runtime"] == 101
    assert body["external_ids"] == {
        "id": 900001,
        "imdb_id": "tt9000001",
        "wikidata_id": None,
        "facebook_id": None,
        "instagram_id": None,
        "twitter_id": None,
    }
    assert body["credits"]["cast"] == [{"name": "Nep Acteur"}]
    assert "videos" not in body  # onbekend: weg, zoals bij TMDB
    assert body["tagline"] == "Nergens"  # een extra veld uit de data gaat mee
    assert body["genres"] == [{"id": 18, "name": "Drama"}]
    for key in ("append", "alt_titles"):
        assert key not in body
    plain = (await tmdb("/movie/900003")).json()
    assert plain["imdb_id"] is None and plain["poster_path"] is None and "external_ids" not in plain


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    ["/movie/123", "/movie/900999", "/movie/abc", "/tv/900999", "/tv/900101/season/7", "/genre"],
)
async def test_onbekend_geeft_34(path):
    r = await tmdb(path)
    assert r.status_code == 404 and r.json()["status_code"] == 34


@pytest.mark.anyio
async def test_buiten_versie_3_en_andere_methode():
    _, c = make()
    async with c:
        r = await c.get("https://api.themoviedb.org/4/list/1", headers=AUTH)
        assert r.status_code == 404
        r = await c.post(f"{TMDB}/configuration", headers=AUTH)
        assert r.status_code == 405


@pytest.mark.anyio
async def test_serie_en_seizoen():
    show = (await tmdb("/tv/900101", language="nl-NL", append_to_response="external_ids")).json()
    assert show["name"] == "Nepstraat" and show["number_of_seasons"] == 1
    assert show["number_of_episodes"] == 3 and show["last_air_date"] == "2010-01-08"
    assert [(s["season_number"], s["name"], s["episode_count"]) for s in show["seasons"]] == [
        (0, "Specials", 1),
        (1, "Seizoen 1", 2),
    ]
    assert show["external_ids"]["tvdb_id"] == 9000101
    season = (await tmdb("/tv/900101/season/1", language="nl-NL")).json()
    assert season["season_number"] == 1 and season["name"] == "Seizoen 1"
    pilot, two = season["episodes"]
    assert (pilot["episode_number"], pilot["name"], pilot["overview"]) == (1, "Proef", "")
    assert pilot["runtime"] == 42 and pilot["still_path"] == "/e900101-1-1.jpg"
    assert pilot["show_id"] == 900101 and pilot["season_number"] == 1
    assert two["episode_number"] == 2 and two["name"] == "Two"
    assert pilot["id"] != two["id"]
    specials = (await tmdb("/tv/900101/season/0")).json()
    assert specials["episodes"][0]["name"] == "Episode 1"


@pytest.mark.anyio
async def test_find():
    movie = (await tmdb("/find/tt9000001", external_source="imdb_id")).json()
    assert [m["id"] for m in movie["movie_results"]] == [900001]
    assert movie["tv_results"] == movie["tv_episode_results"] == movie["person_results"] == []
    show = (await tmdb("/find/tt9000101", external_source="imdb_id")).json()
    assert [s["id"] for s in show["tv_results"]] == [900101] and show["movie_results"] == []
    episode = (await tmdb("/find/tt9000102", external_source="imdb_id")).json()
    [hit] = episode["tv_episode_results"]
    assert (hit["show_id"], hit["season_number"], hit["episode_number"]) == (900101, 1, 1)
    tvdb = (await tmdb("/find/9000101", external_source="tvdb_id")).json()
    assert [s["id"] for s in tvdb["tv_results"]] == [900101]
    none = (await tmdb("/find/tt0000000", external_source="imdb_id")).json()
    assert all(v == [] for v in none.values())
    for params in ({}, {"external_source": "nep_id"}):
        r = await tmdb("/find/tt9000001", **params)
        assert r.status_code == 422 and r.json()["status_code"] == 5


# --- TMDB: tempo ---


@pytest.mark.anyio
async def test_tmdb_meer_dan_20_per_seconde():
    clock = Clock()
    app, c = make(rate_limits=True, clock=clock)
    async with c:
        for _ in range(20):
            assert (await c.get(f"{TMDB}/configuration", headers=AUTH)).status_code == 200
        r = await c.get(f"{TMDB}/configuration", headers=AUTH)
        assert r.status_code == 429 and r.headers["retry-after"] == "1"
        assert r.json()["status_code"] == 25
        clock.now += 0.5  # nog in hetzelfde venster: ook geweigerde verzoeken tellen
        assert (await c.get(f"{TMDB}/configuration", headers=AUTH)).status_code == 429
        clock.now += 0.51
        assert (await c.get(f"{TMDB}/configuration", headers=AUTH)).status_code == 200
        # de plaatjeshost telt niet mee
        for _ in range(30):
            assert (await c.get(f"{IMG}/w92/p900001.jpg")).status_code == 200
    statuses = [e["status"] for e in app.state.fake.log]
    assert statuses.count(429) == 2


# --- de plaatjeshost ---


@pytest.mark.anyio
async def test_plaatjes_in_de_gevraagde_maat():
    _, c = make()
    async with c:
        poster = await c.get(f"{IMG}/w500/p900001.jpg")
        assert poster.status_code == 200 and poster.headers["content-type"] == "image/jpeg"
        assert jpeg_size(poster.content) == (500, 750)
        assert (await c.get(f"{IMG}/w500/p900001.jpg")).content == poster.content
        assert jpeg_size((await c.get(f"{IMG}/original/p900001.jpg")).content) == (1000, 1500)
        assert jpeg_size((await c.get(f"{IMG}/w1280/b900001.jpg")).content) == (1280, 720)
        assert jpeg_size((await c.get(f"{IMG}/w300/e900101-1-1.jpg")).content) == (300, 169)
        other = await c.get(f"{IMG}/w500/p900101.jpg")
        assert other.content != poster.content
        for path in ("/w501/p900001.jpg", "/w500/onbekend.jpg", "/w500/../p900001.jpg", "/w500"):
            assert (await c.get(f"{IMG}{path}")).status_code == 404, path


# --- MusicBrainz ---


async def mb(path: str, *, headers: dict[str, str] | None = None, **params: Any):
    _, c = make()
    async with c:
        return await c.get(
            f"{MB}{path}", params=params, headers={"User-Agent": UA, **(headers or {})}
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "agent",
    ["", "python-httpx/0.28.1", "Platform/0.7", "Platform/0.7 (contact)", "Platform ( x )"],
)
async def test_musicbrainz_wil_een_user_agent_met_contact(agent):
    r = await mb(f"/release/{R1}", headers={"User-Agent": agent}, fmt="json")
    assert r.status_code == 403 and "User-Agent" in r.json()["error"]


@pytest.mark.anyio
async def test_musicbrainz_een_per_seconde():
    clock = Clock()
    _, c = make(rate_limits=True, clock=clock)
    async with c:
        url = f"{MB}/release/{R1}?fmt=json"
        assert (await c.get(url, headers={"User-Agent": UA})).status_code == 200
        r = await c.get(url, headers={"User-Agent": UA})
        assert r.status_code == 503 and "retry-after" not in r.headers
        assert "rate limit" in r.json()["error"]
        clock.now += 1.01
        assert (await c.get(url, headers={"User-Agent": UA})).status_code == 200


@pytest.mark.anyio
async def test_release_opzoeken():
    body = (await mb(f"/release/{R1}", inc="release-groups artist-credits", fmt="json")).json()
    assert body["id"] == R1 and body["title"] == "Nep Liedjes" and body["date"] == "1999-03-01"
    [credit] = body["artist-credit"]
    assert credit["name"] == "De Neppers" and credit["artist"]["id"] == A1
    assert credit["artist"]["sort-name"] == "Neppers, De"
    assert body["release-group"]["id"] == G1 and body["release-group"]["primary-type"] == "Album"
    assert body["cover-art-archive"]["front"] is True and "media" not in body
    plain = (await mb(f"/release/{R2}", fmt="json")).json()
    assert "artist-credit" not in plain and "release-group" not in plain
    assert plain["cover-art-archive"] == {
        "artwork": False,
        "count": 0,
        "front": False,
        "back": False,
        "darkened": False,
    }
    tracks = (await mb(f"/release/{R1}", inc="recordings", fmt="json")).json()["media"]
    assert tracks[0]["track-count"] == 2
    assert [t["title"] for t in tracks[0]["tracks"]] == ["Een", "Twee"]
    assert tracks[0]["tracks"][0]["length"] == 201000
    counted = (await mb(f"/release/{R2}", inc="media", fmt="json")).json()["media"]
    assert counted[0]["track-count"] == 3 and "tracks" not in counted[0]
    # met Accept: application/json mag fmt weg
    accept = await mb(f"/release/{R1}", headers={"Accept": "application/json"})
    assert accept.json()["id"] == R1


@pytest.mark.anyio
async def test_release_fouten_en_xml():
    r = await mb("/release/geen-mbid", fmt="json")
    assert r.status_code == 400 and r.json()["error"] == "Invalid mbid."
    r = await mb(f"/release/{mbid('weg')}", fmt="json")
    assert r.status_code == 404 and r.json()["error"] == "Not Found"
    r = await mb(f"/release/{R1}", inc="artist-credits+nep", fmt="json")
    assert r.status_code == 400 and "nep is not a valid inc" in r.json()["error"]
    assert (await mb("/artist/x", fmt="json")).status_code == 404
    xml = await mb(f"/release/{R1}")
    assert xml.status_code == 200 and xml.headers["content-type"].startswith("application/xml")
    assert xml.text.startswith("<?xml") and "Nep Liedjes" in xml.text
    with pytest.raises(json.JSONDecodeError):
        xml.json()


@pytest.mark.anyio
async def test_release_zoeken():
    q = 'release:"Nep Liedjes" AND artist:"De Neppers"'
    body = (await mb("/release/", query=q, fmt="json", limit=5)).json()
    assert body["count"] == 1 and body["offset"] == 0 and body["created"].endswith("Z")
    [hit] = body["releases"]
    assert hit["id"] == R1 and hit["score"] == 100 and hit["track-count"] == 2
    assert hit["artist-credit"][0]["artist"]["id"] == A1
    assert hit["release-group"]["primary-type"] == "Album"
    # een deel van de titel: wel gevonden, lagere score
    partial = (await mb("/release/", query='release:"liedjes"', fmt="json")).json()["releases"]
    assert [h["id"] for h in partial] == [R1] and partial[0]["score"] == 80
    # geen treffer, EN die niet klopt, NOT, OR, een onbekend veld
    for query, expected in [
        ('release:"Nep Liedjes" AND artist:"Niemand"', []),
        ('release:"Nep Liedjes" NOT artist:"De Neppers"', []),
        ('release:"Nep Liedjes" OR release:"Stilte \\"Live\\""', [R1, R2]),
        ("liedjes", [R1]),
        ("date:1999", [R1]),
        ("tracks:3", [R2]),
        (f"reid:{R2}", [R2]),
        ('nepveld:"Nep Liedjes"', []),
    ]:
        hits = (await mb("/release/", query=query, fmt="json")).json()["releases"]
        assert sorted(h["id"] for h in hits) == sorted(expected), query
    paged = (await mb("/release/", query="date:1999 OR date:2001", fmt="json", limit=1)).json()
    assert paged["count"] == 2 and len(paged["releases"]) == 1
    rest = await mb("/release/", query="date:1999 OR date:2001", fmt="json", limit=1, offset=1)
    assert len(rest.json()["releases"]) == 1
    assert rest.json()["releases"][0]["id"] != paged["releases"][0]["id"]
    assert (await mb("/release/", fmt="json")).status_code == 400
    assert (await mb("/release/", query="x", limit="veel", fmt="json")).status_code == 400
    xml = await mb("/release/", query="liedjes")
    assert 'count="1"' in xml.text and R1 in xml.text


# --- de Cover Art Archive en de tweede host ---


@pytest.mark.anyio
async def test_caa_leidt_om_naar_de_tweede_host():
    _, c = make()
    async with c:
        r = await c.get(f"{CAA}/release/{R1}/front-500")
        assert r.status_code == 307
        target = f"https://archive.org/download/mbid-{R1}/mbid-{R1}-{image_id(R1)}_thumb500.jpg"
        assert r.headers["location"] == target
        image = await c.get(target)
        assert image.headers["content-type"] == "image/jpeg"
        assert jpeg_size(image.content) == (500, 500)
        followed = await c.get(f"{CAA}/release/{R1}/front", follow_redirects=True)
        assert jpeg_size(followed.content) == (1400, 1400)
        assert len(followed.history) == 1
        for size, width in (("front-250", 250), ("front-1200", 1200)):
            r = await c.get(f"{CAA}/release/{R1}/{size}", follow_redirects=True)
            assert jpeg_size(r.content) == (width, width)
        # ook op een andere host van archive.org
        other = target.replace("https://archive.org", "https://ia800100.us.archive.org")
        assert (await c.get(other)).status_code == 200
        assert (await c.get(f"{CAA}/release/{R2}/front-500")).status_code == 404
        assert (await c.get(f"{CAA}/release/{mbid('weg')}/front-500")).status_code == 404
        assert (await c.get(f"{CAA}/release/geen/front-500")).status_code == 400
        assert (await c.get(f"{CAA}/release/{R1}/back")).status_code == 404
        wrong = target.replace(str(image_id(R1)), "1")
        assert (await c.get(wrong)).status_code == 404
        assert (await c.get(f"https://archive.org/download/mbid-{R2}/x.jpg")).status_code == 404


@pytest.mark.anyio
async def test_caa_naar_een_eigen_archive_url():
    _, c = make(archive_url="http://localhost:9/archive")
    async with c:
        r = await c.get(f"{CAA}/release/{R1}/front-500")
        assert r.headers["location"].startswith(f"http://localhost:9/archive/download/mbid-{R1}/")
        # het voorvoegsel /archive op een onbekende host
        image = await c.get(r.headers["location"])
        assert jpeg_size(image.content) == (500, 500)


# --- routes op host en voorvoegsel ---


@pytest.mark.anyio
async def test_voorvoegsels_als_de_host_onbekend_is():
    app, c = make()
    async with c:
        base = "http://127.0.0.1:9"
        assert (await c.get(f"{base}/tmdb/3/configuration", headers=AUTH)).status_code == 200
        r = await c.get(f"{base}/tmdb-img/t/p/w92/p900001.jpg")
        assert jpeg_size(r.content) == (92, 138)
        r = await c.get(f"{base}/mb/ws/2/release/{R1}?fmt=json", headers={"User-Agent": UA})
        assert r.json()["id"] == R1
        assert (await c.get(f"{base}/caa/release/{R1}/front-500")).status_code == 307
        r = await c.get(f"{base}/nergens")
        assert r.status_code == 404
    services = [(e["service"], e["inner"]) for e in app.state.fake.log]
    assert services == [
        ("tmdb", "/3/configuration"),
        ("image", "/t/p/w92/p900001.jpg"),
        ("musicbrainz", f"/ws/2/release/{R1}"),
        ("caa", f"/release/{R1}/front-500"),
        (None, "/nergens"),
    ]


# --- fouten op bestelling ---


@pytest.mark.anyio
async def test_fout_500_eenmaal_en_dan_weer_goed():
    app, c = make()
    fake = app.state.fake
    f = fake.fault("500", "/3/search/*")
    async with c:
        r = await c.get(f"{TMDB}/search/movie?query=harbour", headers=AUTH)
        assert r.status_code == 500 and r.json()["status_code"] == 11
        assert (await c.get(f"{TMDB}/search/movie?query=harbour", headers=AUTH)).status_code == 200
    assert f.hits == 1 and fake.faults == []
    assert [e["fault"] for e in fake.log] == ["500", None]


@pytest.mark.anyio
async def test_fout_op_host_en_pad_en_aantal():
    app, c = make()
    fake = app.state.fake
    fake.fault("wrongtype", "/t/p/*", host="image.tmdb.org", times=2)
    fake.fault("429", "/3/movie/*", host="tmdb", times=None, retry_after=7)
    async with c:
        # de fout voor de plaatjeshost raakt TMDB niet, en andersom
        assert (await c.get(f"{TMDB}/configuration", headers=AUTH)).status_code == 200
        for _ in range(2):
            r = await c.get(f"{IMG}/w500/p900001.jpg")
            assert r.headers["content-type"].startswith("text/html")
        assert (await c.get(f"{IMG}/w500/p900001.jpg")).headers["content-type"] == "image/jpeg"
        for _ in range(3):
            r = await c.get(f"{TMDB}/movie/900001", headers=AUTH)
            assert r.status_code == 429 and r.headers["retry-after"] == "7"
        assert len(fake.faults) == 1
        fake.clear_faults()
        assert (await c.get(f"{TMDB}/movie/900001", headers=AUTH)).status_code == 200


@pytest.mark.anyio
async def test_fout_slow_wacht_en_antwoordt_dan():
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    app, c = make(sleep=sleep)
    app.state.fake.fault("slow", "/3/configuration")
    app.state.fake.fault("slow", "/t/p/*", seconds=2.5)
    async with c:
        assert (await c.get(f"{TMDB}/configuration", headers=AUTH)).status_code == 200
        assert (await c.get(f"{IMG}/w92/p900001.jpg")).status_code == 200
    assert waits == [10.0, 2.5]


@pytest.mark.anyio
async def test_fout_garbage():
    app, c = make()
    app.state.fake.fault("garbage", host="tmdb")
    app.state.fake.fault("garbage", host="musicbrainz")
    app.state.fake.fault("garbage", host="archive")
    async with c:
        r = await c.get(f"{TMDB}/configuration", headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"] == "application/json"
        with pytest.raises(json.JSONDecodeError):
            r.json()
        r = await c.get(f"{MB}/release/{R1}?fmt=json", headers={"User-Agent": UA})
        with pytest.raises(json.JSONDecodeError):
            r.json()
        r = await c.get(f"{CAA}/release/{R1}/front-500", follow_redirects=True)
        assert r.headers["content-type"] == "image/jpeg"
        with pytest.raises(OSError):
            jpeg_size(r.content)


@pytest.mark.anyio
async def test_fout_huge():
    app, c = make()
    app.state.fake.fault("huge", "/t/p/*", size_mb=3)
    async with c:
        async with c.stream("GET", f"{IMG}/w500/p900001.jpg") as r:
            assert r.headers["content-length"] == str(3 * 1024 * 1024)
            assert r.headers["content-type"] == "image/jpeg"
            first = b""
            total = 0
            async for chunk in r.aiter_bytes():
                first = first or chunk
                total += len(chunk)
    assert first[:3] == b"\xff\xd8\xff" and total == 3 * 1024 * 1024
    assert app.state.fake.fault("huge").size_mb == 50  # de standaard: 50 MB


@pytest.mark.anyio
async def test_fout_redirect_evil():
    app, c = make()
    app.state.fake.fault("redirect_evil", f"/release/{R1}/*")
    app.state.fake.fault("redirect_evil", "/3/*", location="http://169.254.169.254/latest")
    async with c:
        r = await c.get(f"{CAA}/release/{R1}/front-500")
        assert r.status_code == 302 and r.headers["location"] == "https://evil.example/steal"
        r = await c.get(f"{TMDB}/configuration", headers=AUTH)
        assert r.headers["location"] == "http://169.254.169.254/latest"


@pytest.mark.anyio
async def test_fout_drop():
    app, c = make()
    app.state.fake.fault("drop", "/3/configuration")
    async with c:
        with pytest.raises(httpx.RemoteProtocolError):
            await c.get(f"{TMDB}/configuration", headers=AUTH)
        assert (await c.get(f"{TMDB}/configuration", headers=AUTH)).status_code == 200


def test_fout_controleert_zijn_invoer():
    app, _ = make()
    fake = app.state.fake
    for kind, path, options in [
        ("404", "*", {}),
        ("500", "geen-slash", {}),
        ("500", "*", {"times": 0}),
        ("500", "*", {"times": "2"}),
        ("500", "*", {"host": "example.org"}),
        ("500", "*", {"nep": 1}),
    ]:
        with pytest.raises(ValueError):
            fake.fault(kind, path, **options)
    assert fake.faults == []


@pytest.mark.anyio
async def test_stuurpaden():
    app, c = make()
    async with c:
        base = "http://127.0.0.1:9"
        r = await c.post(f"{base}/__fault", json={"kind": "500", "path": "/3/*", "times": None})
        assert r.status_code == 201
        first = r.json()["id"]
        r = await c.post(f"{base}/__fault", json={"kind": "slow", "host": "musicbrainz.org"})
        second = r.json()["id"]
        faults = (await c.get(f"{base}/__fault")).json()["faults"]
        assert [(f["id"], f["kind"], f["host"], f["times"]) for f in faults] == [
            (first, "500", None, None),
            (second, "slow", "musicbrainz", 1),
        ]
        for body in ({"kind": "nep"}, {"kind": "500", "times": -1}, ["500"]):
            r = await c.post(f"{base}/__fault", json=body)
            assert r.status_code == 400 and r.json()["error"]
        r = await c.post(f"{base}/__fault", content=b"{geen json")
        assert r.status_code == 400
        assert (await c.delete(f"{base}/__fault/{first}")).status_code == 204
        assert (await c.delete(f"{base}/__fault/{first}")).status_code == 404
        assert (await c.delete(f"{base}/__fault")).status_code == 204
        assert app.state.fake.faults == []
        await c.get(f"{TMDB}/configuration", headers={**AUTH, "Cookie": "a=b", "X-Nep": "1"})
        [entry] = (await c.get(f"{base}/__log")).json()["entries"]
        assert entry["host"] == "api.themoviedb.org" and entry["method"] == "GET"
        assert entry["status"] == 200 and entry["auth_ok"] is True and entry["t"] > 0
        assert entry["headers"]["x-nep"] == "1"
        assert "authorization" not in entry["headers"] and "cookie" not in entry["headers"]
        assert (await c.delete(f"{base}/__log")).status_code == 204
        assert (await c.get(f"{base}/__log")).json()["entries"] == []
        health = (await c.get(f"{base}/__health")).json()
        assert health == {"ok": True, "movies": 4, "shows": 1, "releases": 2, "images": 5}
        app.state.fake.fault("500")
        await c.get(f"{TMDB}/configuration", headers=AUTH)
        assert (await c.post(f"{base}/__reset")).status_code == 204
        assert app.state.fake.faults == [] and app.state.fake.log == []
        assert (await c.get(f"{base}/__onbekend")).status_code == 404


# --- de data ---


def doc(**changes: Any) -> dict[str, Any]:
    out = {"_nep": NEP, "movies": [], "shows": [], "releases": []}
    out.update(changes)
    return out


MOVIE = {"id": 900001, "title": "Nep"}
SHOW = {"id": 900101, "name": "Nep", "seasons": []}
RELEASE = {"id": R1, "title": "Nep", "artist": "Nep", "artist_id": A1, "track_count": 1}


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"movies": []}, "eerste sleutel"),
        ({"movies": [], "_nep": NEP}, "eerste sleutel"),
        ({"_nep": "echt"}, "eerste sleutel"),
        (doc(films=[]), "onbekende sleutels"),
        (doc(movies={}), "geen lijst"),
        (doc(movies=[{**MOVIE, "id": 550}]), "verzonnen id"),
        (doc(movies=[{**MOVIE, "id": True}]), "verzonnen id"),
        (doc(movies=[MOVIE, MOVIE]), "twee keer"),
        (doc(movies=[{"id": 900001}]), "title"),
        (doc(movies=[{**MOVIE, "title": ""}]), "leeg"),
        (doc(movies=[{**MOVIE, "title": {"nl": "x"}}]), "geen taal"),
        (doc(movies=[{**MOVIE, "title": {"nl-NL": ""}}]), "geen enkele taal"),
        (doc(movies=[{**MOVIE, "release_date": "2004"}]), "JJJJ-MM-DD"),
        (doc(movies=[{**MOVIE, "imdb_id": "9000001"}]), "tt0000000"),
        (doc(movies=[{**MOVIE, "popularity": "hoog"}]), "geen getal"),
        (doc(movies=[{**MOVIE, "alt_titles": "Nep"}]), "lijst van strings"),
        (doc(movies=[{**MOVIE, "append": []}]), "verwacht dict"),
        (doc(movies=[{**MOVIE, "poster_path": "p.jpg"}]), "/naam.jpg"),
        (doc(movies=[{**MOVIE, "poster_path": "/../x.jpg"}]), "/naam.jpg"),
        (doc(movies=[{**MOVIE, "poster_path": "/x.png"}]), "/naam.jpg"),
        (doc(movies=[{**MOVIE, "poster_path": "/x.jpg", "backdrop_path": "/x.jpg"}]), "al een"),
        (doc(shows=[{**SHOW, "seasons": {}}]), "geen lijst"),
        (doc(shows=[{**SHOW, "seasons": [{"season_number": -1}]}]), "season_number"),
        (doc(shows=[{**SHOW, "seasons": [{"season_number": 1}, {"season_number": 1}]}]), "dubbel"),
        (
            doc(shows=[{**SHOW, "seasons": [{"season_number": 1, "episodes": [{}]}]}]),
            "episode_number",
        ),
        (doc(releases=[{**RELEASE, "id": "geen-uuid"}]), "MBID"),
        (doc(releases=[{**RELEASE, "id": R1.upper()}]), "kleine letters"),
        (doc(releases=[{**RELEASE, "artist": ""}]), "artist"),
        (doc(releases=[{**RELEASE, "artist_id": None}]), "MBID"),
        (doc(releases=[{**RELEASE, "date": "1999/03"}]), "JJJJ"),
        (doc(releases=[{**RELEASE, "release_group": {"id": "x"}}]), "release_group.id"),
        (doc(releases=[{**RELEASE, "track_count": 0}]), "track_count"),
        (doc(releases=[{**RELEASE, "tracks": []}]), "leeg"),
        (doc(releases=[{**RELEASE, "tracks": [{"length_ms": 1}]}]), "title"),
        (doc(releases=[{**RELEASE, "cover": "ja"}]), "true of false"),
        (doc(releases=[RELEASE, RELEASE]), "twee keer"),
    ],
)
def test_data_fouten(document, message):
    with pytest.raises(DataError, match=message):
        Data.from_documents(document, names=["films.json"])


def test_data_goed_en_laden(tmp_path):
    data = Data.from_documents(DOC)
    assert data.counts() == {"movies": 4, "shows": 1, "releases": 2, "images": 5}
    assert data.images["/b900001.jpg"] == "backdrop"
    assert data.images["/e900101-1-1.jpg"] == "still"
    # een map die er niet is: leeg (M8 vult hem); de standaardmap
    assert load(tmp_path / "weg").counts() == Data().counts()
    load()
    (tmp_path / "b.json").write_text(json.dumps(doc(movies=[MOVIE])))
    (tmp_path / "a.json").write_text(json.dumps(doc(releases=[RELEASE])))
    (tmp_path / "niet.txt").write_text("{")
    loaded = load(tmp_path)
    assert (len(loaded.movies), len(loaded.releases)) == (1, 1)
    # dubbel over twee bestanden: de naam van het tweede in de fout
    (tmp_path / "c.json").write_text(json.dumps(doc(movies=[MOVIE])))
    with pytest.raises(DataError, match=r"c\.json: movies\[0\].*twee keer"):
        load(tmp_path)
    (tmp_path / "c.json").write_text("{kapot")
    with pytest.raises(DataError, match=r"c\.json: niet te lezen"):
        load(tmp_path)


@pytest.mark.anyio
async def test_zonder_data():
    app = create_app(FakeConfig(tmdb_key=KEY, data=Data()))
    async with httpx.AsyncClient(transport=transport(app)) as c:
        r = await c.get(f"{TMDB}/search/movie?query=x", headers=AUTH)
        assert r.json()["results"] == []
        assert (await c.get(f"{IMG}/w500/p900001.jpg")).status_code == 404


# --- als proces ---


def test_als_proces(tmp_path):
    """python -m tests.media.fake_providers: luistert, spreekt de diensten op het voorvoegsel,
    leidt om naar `localhost`, breekt af bij `drop`, en stopt op PID. De sleutel staat nooit in
    zijn log."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "nep.json").write_text(json.dumps(DOC))
    fake = FakeProvidersProcess(tmp_path / "werk", data_dir=data)
    with fake:
        pid = fake.proc.pid
        assert fake.health()["movies"] == 4
        assert (tmp_path / "werk" / "tmdb.key").stat().st_mode & 0o777 == 0o600
        env = fake.env()
        assert env["PLATFORM_MEDIA_TMDB_URL"] == f"{fake.url}/tmdb"
        assert env["PLATFORM_MEDIA_PROVIDER_PRIVATE_OK"] == "true"
        headers = {"Authorization": f"Bearer {fake.tmdb_key}", "User-Agent": UA}
        with httpx.Client(trust_env=False, timeout=10, headers=headers) as c:
            config = c.get(f"{env['PLATFORM_MEDIA_TMDB_URL']}/3/configuration").json()
            base = config["images"]["secure_base_url"]
            assert base == f"{fake.url}/tmdb-img/t/p/"
            assert jpeg_size(c.get(f"{base}w500/p900001.jpg").content) == (500, 750)
            r = c.get(f"{env['PLATFORM_MEDIA_MUSICBRAINZ_URL']}/ws/2/release/{R1}?fmt=json")
            assert r.json()["id"] == R1
            r = c.get(f"{env['PLATFORM_MEDIA_CAA_URL']}/release/{R1}/front-500")
            assert r.status_code == 307
            assert r.headers["location"].startswith(f"http://localhost:{fake.port}/archive/")
            assert jpeg_size(c.get(r.headers["location"]).content) == (500, 500)
            fake.fault("drop", "/3/configuration")
            with pytest.raises(httpx.RemoteProtocolError):
                c.get(f"{fake.url}/tmdb/3/configuration")
            assert c.get(f"{fake.url}/tmdb/3/configuration").status_code == 200
        log = fake.log()
        assert [e["service"] for e in log][:4] == ["tmdb", "image", "musicbrainz", "caa"]
        assert all(e["headers"].get("user-agent") == UA for e in log)
        assert fake.tmdb_key not in json.dumps(log)
        fake.reset()
        assert fake.log() == []
    assert fake.proc is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    text = fake.log_path.read_text(errors="replace")
    assert fake.tmdb_key not in text
    assert "nep: drop" in text and "Traceback" not in text  # de bedoelde fout: één regel
