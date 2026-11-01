"""Koppelen (providers/match.py, Keuze 42) en het zoeken van lookup.identify tegen de nep-TMDB.

- De tabel: gelijkenis van titels (lidwoord, accenten, leestekens, &), het jaar (+0,2, +0,1,
  −0,3), de populariteit als scheidsrechter (hooguit +0,02), de regel ≥ 0,85 en ≥ 0,1 boven de
  tweede, en de muziek (min 0,1 per nummer verschil).
- Id's uit de naam of de NFO gaan voor (`{tmdb-…}`, `[imdbid-…]`).
- Klaar als (§2 M8): 20 films met scene-namen (via names.parse, zoals de scanner ze leest);
  minstens 18 worden automatisch goed gekoppeld, de rest staat onder "Nakijken" met kandidaten,
  en geen enkele wordt verkeerd gekoppeld.
"""

from __future__ import annotations

import pytest

from app.media import lookup, names
from app.media.providers import http, match, musicbrainz, secrets, tmdb
from app.media.providers.base import Candidate
from tests.media.fake_providers import FakeConfig, create_app, kit, load


def c(title, year=None, *, id="1", original=None, popularity=None, provider="tmdb"):
    return Candidate(
        provider=provider,
        id=f"movie:{id}",
        kind="movie",
        title=title,
        year=year,
        original_title=original,
        popularity=popularity,
    )


# --- de tabel -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("The Matrix", "Matrix", 1.0),
        ("Amélie", "Amelie", 1.0),
        ("Ashes and Embers", "Ashes & Embers", 1.0),
        ("Winters Lantern", "Winter's Lantern", 1.0),
        ("De Nephaven", "Nephaven", 1.0),
        ("Star.Wars", "Star Wars", 1.0),
        ("", "Iets", 0.0),
        (None, "Iets", 0.0),
    ],
)
def test_gelijkenis_na_normaliseren(a, b, expected):
    assert match.similarity(a, b) == pytest.approx(expected)


def test_gelijkenis_van_verschillende_titels():
    assert match.similarity("Glass Town", "Glass Town Chronicles of the North") < 0.7
    assert match.similarity("Paper Lanterns", "Paper Lanterns of the East") < 0.8
    assert match.similarity("Copper Sky", "Clockwork Garden") < 0.4


@pytest.mark.parametrize(
    "year, other, bonus",
    [(2004, 2004, 0.2), (2004, 2003, 0.1), (2004, 2005, 0.1), (2004, 2001, -0.3),
     (None, 2004, 0.0), (2004, None, 0.0), (None, None, 0.0)],
)  # fmt: skip
def test_het_jaar(year, other, bonus):
    assert match.year_bonus(year, other) == bonus


@pytest.mark.parametrize(
    "popularity, low, high",
    [(None, 0, 0), (0, 0, 0), (-5, 0, 0), (float("nan"), 0, 0), (1, 0, 0.005),
     (10, 0.005, 0.01), (1000, 0.0199, 0.02), (10**9, 0.02, 0.02)],
)  # fmt: skip
def test_populariteit_alleen_als_scheidsrechter(popularity, low, high):
    assert low <= match.tiebreak(popularity) <= high


def test_de_score_is_titel_plus_jaar_plus_scheidsrechter():
    assert match.score("De Nephaven", 2004, c("De Nephaven", 2004)) == pytest.approx(1.2)
    # De originele titel telt als hij beter past
    port = c("De Valse Haven", 2001, original="Le Faux Port")
    assert match.score("Le Faux Port", 2001, port) == pytest.approx(1.2)
    assert match.score("Copper Sky", 2005, c("Copper Sky", 1990)) == pytest.approx(0.7)


@pytest.mark.parametrize(
    "title, year, candidates, state",
    [
        ("Copper Sky", 2005, [c("Copper Sky", 2005)], "matched"),
        ("Copper Sky", None, [c("Copper Sky", None)], "matched"),  # 1,0 zonder jaar is genoeg
        ("Copper Sky", 2005, [c("Copper Skies", 2010)], "review"),  # onder 0,85
        # twee even goede: geen marge
        ("The Fake Storm", None,
         [c("The Fake Storm", 1998, id="1"), c("The Fake Storm", 2019, id="2")], "review"),
        # het jaar maakt het verschil
        ("Glass Mountain", 2015,
         [c("Glass Mountain", 2015, id="1"), c("Glass Mountain", 1962, id="2")], "matched"),
        ("Iets", None, [], "review"),
    ],
)  # fmt: skip
def test_de_regel_van_keuze_42(title, year, candidates, state):
    decision = match.decide(title, year, candidates)
    assert decision.state == state
    assert (decision.choice is not None) == (state == "matched")
    assert 0.0 <= decision.confidence <= 1.0
    assert len(decision.top) <= match.TOP


def test_review_bewaart_de_vijf_besten_op_volgorde():
    many = [c(f"Copper Sky {i}", 2005, id=str(i)) for i in range(8)] + [c("Copper", 2005, id="9")]
    decision = match.decide("Copper Sky", 2005, many)
    assert decision.state == "review" and len(decision.top) == 5
    scores = [s.score for s in decision.top]
    assert scores == sorted(scores, reverse=True)


def test_dezelfde_kandidaat_telt_een_keer_met_zijn_beste_titel():
    nl = c("Zeven Kleine Rivieren", 2008, id="7")
    en = c("Seven Small Rivers", 2008, id="7")
    ranked = match.rank("Seven Small Rivers", 2008, [nl, en])
    assert len(ranked) == 1
    assert ranked[0].candidate.title == "Zeven Kleine Rivieren"  # de eerste vorm blijft
    assert ranked[0].score == pytest.approx(1.2)
    assert match.decide("Seven Small Rivers", 2008, [nl, en]).state == "matched"


def test_bij_gelijke_score_de_volgorde_van_de_provider():
    first, second = c("Same", 2000, id="1"), c("Same", 2000, id="2")
    assert [s.candidate.id for s in match.rank("Same", 2000, [first, second])] == [
        "movie:1",
        "movie:2",
    ]


def test_best_geeft_alleen_een_automatische_keuze():
    assert match.best("Copper Sky", 2005, [c("Copper Sky", 2005)])[0] is not None
    choice, sure = match.best("Glass Town", 2009, [c("Glass Town Chronicles of the North", 2009)])
    assert choice is None and 0 < sure < match.AUTO


# --- id's -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ids, kind, expected",
    [
        ({"tmdb": "278"}, "movie", ("tmdb", "movie:278")),
        ({"tmdb": "278"}, "show", ("tmdb", "tv:278")),
        ({"tmdb": "tmdb:603"}, "movie", ("tmdb", "movie:603")),
        ({"tmdb": 603}, "movie", ("tmdb", "movie:603")),
        ({"imdb": "tt0111161"}, "movie", ("imdb", "tt0111161")),
        ({"tmdb": "278", "imdb": "tt0111161"}, "movie", ("tmdb", "movie:278")),
        ({"tmdb": "0"}, "movie", None),
        ({"tmdb": "12345678901"}, "movie", None),
        ({"tmdb": "../1"}, "movie", None),
        ({"imdb": "tt12"}, "movie", None),
        ({"imdb": "nm0000001"}, "movie", None),
        ({}, "movie", None),
        (None, "movie", None),
    ],
)
def test_direct_uit_de_naam_of_de_nfo(ids, kind, expected):
    assert match.direct(ids, kind) == expected


# --- muziek ---------------------------------------------------------------------------------------


def test_album_score_en_nummers():
    album = match.Album("a", "Zeegezichten", "Nep Orkest", 10, 2001)
    assert match.album_score("Zeegezichten", "Nep Orkest", 10, album) == pytest.approx(1.0)
    assert match.album_score("Zeegezichten", "Nep Orkest", 8, album) == pytest.approx(0.8)
    assert match.album_score("Zeegezichten", None, None, album, 2001) == pytest.approx(1.2)
    assert match.album_score("Zeegezichten", "Iemand Anders", 10, album) < match.AUTO


def test_decide_album_kiest_de_editie_op_het_aantal_nummers():
    normal = match.Album("n", "Tweede Plaat", "De Valse Noten", 8, 2010)
    deluxe = match.Album("d", "Tweede Plaat", "De Valse Noten", 12, 2011)
    chosen, sure = match.decide_album("Tweede Plaat", "De Valse Noten", 12, [normal, deluxe])
    assert chosen is deluxe and sure == 1.0
    chosen, _ = match.decide_album("Tweede Plaat", "De Valse Noten", 8, [normal, deluxe])
    assert chosen is normal
    # Zonder aantal en zonder jaar: twee even goede, dus nakijken
    chosen, sure = match.decide_album("Tweede Plaat", "De Valse Noten", None, [normal, deluxe])
    assert chosen is None and sure == 1.0
    assert match.decide_album("x", None, None, []) == (None, 0.0)


# --- Klaar als: 20 films met scene-namen tegen de nep-TMDB ----------------------------------------

FILMS = [
    # (bestandsnaam, het goede id of None als hij onder Nakijken hoort)
    ("De.Nephaven.2004.1080p.BluRay.x264-NEP.mkv", 900001),
    ("Paper.Lanterns.1999.720p.WEB-DL.AAC2.0.H.264-NEP.mkv", 900002),
    ("The.Quiet.Engine.2011.2160p.UHD.BluRay.x265-NEP.mkv", 900003),
    ("Midnight.in.the.Orchard.1987.DVDRip.XviD-NEP.avi", 900004),
    ("Glass.Mountain.2015.1080p.WEBRip.x264-NEP.mkv", 900005),
    ("Le.Faux.Port.2001.FRENCH.1080p.BluRay.x264-NEP.mkv", 900006),
    ("Seven.Small.Rivers.2008.1080p.BluRay.x264-NEP.mkv", 900007),
    ("Ashes.and.Embers.2013.720p.BluRay.x264-NEP.mkv", 900008),
    ("Night.of.the.Fake.Dead.1968.REMASTERED.1080p.BluRay.x264-NEP.mkv", 900009),
    ("Twelve Angry Fakes {tmdb-900010}.mkv", 900010),
    ("The Long Way Home (2019) [imdbid-tt9000011].mkv", 900011),
    ("Copper.Sky.2005.PROPER.1080p.BluRay.x264-NEP.mkv", 900012),
    ("Winters.Lantern.2016.1080p.WEB.H264-NEP.mkv", 900013),
    ("The.Ninth.Bridge.2010.LIMITED.720p.BluRay.x264-NEP.mkv", 900014),
    # het jaar van de uitgave, niet van de film: eerst niets, dan zonder jaar
    ("Salt.and.Stone.2004.1080p.WEB-DL.DD5.1.H264-NEP.mkv", 900015),
    ("Dakzomer.2021.NL.1080p.WEB-DL.x264-NEP.mkv", 900016),
    ("Hidden.Frequencies.2012.1080p.BluRay.x264-NEP.mkv", 900017),
    ("Clockwork.Garden.1994.1080p.BluRay.x264-NEP.mkv", 900018),
    # geen jaar en twee films met die titel: nakijken
    ("The.Fake.Storm.720p.HDTV.x264-NEP.mkv", None),
    # een deel van een langere titel: nakijken
    ("Glass.Town.2009.1080p.BluRay.x264-NEP.mkv", None),
]


@pytest.fixture
async def tmdb_fake(settings):
    secrets.reset()
    clock = kit.Clock()
    app = create_app(FakeConfig(tmdb_key=kit.KEY, data=load(), clock=clock))
    out = kit.outbound(settings, app, clock=clock)
    previous = http.use(out)
    yield tmdb.Tmdb(settings, kit.KEY), app.state.fake
    http.use(previous)
    await out.aclose()


@pytest.mark.anyio
async def test_20_films_met_scene_namen(tmdb_fake):
    provider, fake = tmdb_fake
    auto, review, wrong = [], [], []
    for name, expected in FILMS:
        parsed = names.parse(f"Films/{name}")
        assert parsed.kind == "movies"
        decision = await lookup.identify(
            provider, "movie", parsed.title, parsed.year, parsed.ids, "nl-NL"
        )
        if decision.state == "matched":
            assert decision.choice is not None
            if decision.choice.id != f"movie:{expected}":
                wrong.append((name, decision.choice.id))
            auto.append(name)
        else:
            assert decision.top, name  # Nakijken met kandidaten
            review.append(name)
    assert wrong == []
    assert len(auto) >= 18
    assert len(auto) + len(review) == 20
    assert sorted(review) == sorted(n for n, e in FILMS if e is None)
    # Alle verzoeken met de sleutel in de kop, geen enkele geweigerd of afgeremd
    assert {e["status"] for e in fake.log} == {200}
    assert all(e["auth"] == "bearer" and "api_key" not in e["query"] for e in fake.log)


@pytest.mark.anyio
async def test_een_id_uit_de_naam_zoekt_niet(tmdb_fake):
    provider, fake = tmdb_fake
    decision = await lookup.identify(provider, "movie", "Wat Dan Ook", None, {"tmdb": "900010"}, "")
    assert decision.state == "matched" and decision.choice.id == "movie:900010"
    assert decision.confidence == 1.0
    assert [e["inner"] for e in fake.log] == ["/3/movie/900010"]
    fake.log.clear()
    decision = await lookup.identify(provider, "movie", "x", None, {"imdb": "tt9000011"}, "")
    assert decision.choice.id == "movie:900011"
    assert [e["inner"] for e in fake.log] == ["/3/find/tt9000011"]


@pytest.mark.anyio
async def test_een_onbekend_id_valt_terug_op_zoeken(tmdb_fake):
    provider, fake = tmdb_fake
    decision = await lookup.identify(
        provider, "movie", "Copper Sky", 2005, {"tmdb": "123"}, "nl-NL"
    )
    assert decision.choice.id == "movie:900012"
    assert [e["inner"] for e in fake.log] == ["/3/movie/123", "/3/search/movie"]


@pytest.mark.anyio
async def test_series_zoeken(tmdb_fake):
    provider, _ = tmdb_fake
    decision = await lookup.identify(provider, "show", "Havenlichten", None, {}, "nl-NL")
    assert decision.state == "matched" and decision.choice.id == "tv:900102"
    decision = await lookup.identify(provider, "show", "The Fake Coast", None, {}, "en-US")
    assert decision.choice.id == "tv:900101"


@pytest.mark.anyio
async def test_albums_tegen_de_nep(settings):
    clock = kit.Clock()
    app = create_app(FakeConfig(data=load(), clock=clock))
    out = kit.outbound(settings, app, clock=clock)
    previous = http.use(out)
    try:
        mb = musicbrainz.MusicBrainz(settings)
        found = await mb.search_album("Tweede Plaat", "De Valse Noten")
        albums = [match.Album(r.id, r.title, r.artist, r.track_count, r.year) for r in found]
        chosen, _ = match.decide_album("Tweede Plaat", "De Valse Noten", 12, albums)
        assert chosen is not None and chosen.tracks == 12 and chosen.year == 2011
        found = await mb.search_album("Lanterns", "The Paper Birds")
        albums = [match.Album(r.id, r.title, r.artist, r.track_count, r.year) for r in found]
        chosen, _ = match.decide_album("Lanterns", "The Paper Birds", 12, albums)
        assert chosen is not None and chosen.title == "Lanterns"
    finally:
        http.use(previous)
        await out.aclose()
