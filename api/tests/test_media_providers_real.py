"""De clients van TMDB, MusicBrainz en de Cover Art Archive tegen de echte diensten (M8, §4.4;
review golf 5, K1). Alleen met de hand, nooit in CI (marker `provider_real`, standaard weg).

Dezelfde scenario's als test_media_providers_contract.py tegen de nep, maar met films en albums
die bij de echte bestaan (The Matrix, Breaking Bad, OK Computer): zoeken met jaar en taal,
details met `external_ids`, een seizoen, `find` op een IMDb-id, een onbekend id is NotFound, een
foute sleutel is ProviderAuth; MusicBrainz zoeken en opzoeken op MBID; de voorkant uit de CAA.
Zo blijkt of de echte dienst nog de vorm geeft die onze clients lezen.

    cd api
    MEDIA_TEST_TMDB_TOKEN=eyJ… MEDIA_TEST_MUSICBRAINZ=1 uv run pytest -m provider_real \\
        tests/test_media_providers_real.py

- Zonder `MEDIA_TEST_TMDB_TOKEN` (het API Read Access Token) slaan de tests van TMDB over.
- Zonder `MEDIA_TEST_MUSICBRAINZ=1` slaan die van MusicBrainz en de CAA over (die hebben geen
  sleutel, maar vragen om weinig verkeer: alleen als je het zelf aanzet).

De sleutel komt uit de omgeving en gaat alleen als `Authorization: Bearer` naar TMDB; hij komt
niet in een melding van een test (de clients noemen hem nooit).
"""

from __future__ import annotations

import os

import pytest

from app.media.providers import art, caa, http, musicbrainz, secrets, tmdb
from app.media.providers.base import ProviderAuth

pytestmark = [pytest.mark.anyio, pytest.mark.provider_real]

TOKEN = os.environ.get("MEDIA_TEST_TMDB_TOKEN", "").strip()
MUSICBRAINZ = os.environ.get("MEDIA_TEST_MUSICBRAINZ", "") == "1"
needs_token = pytest.mark.skipif(not TOKEN, reason="MEDIA_TEST_TMDB_TOKEN ontbreekt")
needs_musicbrainz = pytest.mark.skipif(not MUSICBRAINZ, reason="MEDIA_TEST_MUSICBRAINZ=1 ontbreekt")

MATRIX = "movie:603"
MATRIX_IMDB = "tt0133093"
BREAKING_BAD = "tv:1396"


@pytest.fixture(autouse=True)
async def _echt(settings):
    """De echte uitgaande client (DNS vast, de grenzen van http.py), en daarna weer de oude."""
    secrets.reset()
    out = http.Outbound(settings)
    previous = http.use(out)
    yield
    http.use(previous)
    await out.aclose()
    secrets.reset()


@pytest.fixture
def db(settings):
    return tmdb.Tmdb(settings, TOKEN)


@needs_token
async def test_tmdb_de_sleutel_werkt(db):
    await db.check()


@needs_token
async def test_tmdb_zoeken_met_jaar_en_taal(db):
    found = await db.search("movie", "The Matrix", 1999, "nl-NL")
    matrix = next(c for c in found if c.id == MATRIX)
    assert (matrix.provider, matrix.kind, matrix.year) == ("tmdb", "movie", 1999)
    assert matrix.title and matrix.poster and matrix.poster.startswith("/")
    shows = await db.search("show", "Breaking Bad", None, "en-US")
    assert BREAKING_BAD in [c.id for c in shows]


@needs_token
async def test_tmdb_details_met_external_ids(db):
    details = await db.details("movie", MATRIX, "nl-NL")
    assert details.year == 1999 and details.title and details.overview
    assert details.ids == {"tmdb": "603", "imdb": MATRIX_IMDB}
    kinds = {image.kind for image in details.images}
    assert {"poster", "backdrop"} <= kinds
    assert all(image.path.startswith("/") for image in details.images)


@needs_token
async def test_tmdb_een_serie_en_een_seizoen(db):
    show = await db.details("show", BREAKING_BAD, "nl-NL")
    assert show.kind == "show" and (show.seasons or 0) >= 5
    episodes = await db.season(BREAKING_BAD, 1, "nl-NL")
    assert [e.episode for e in episodes] == list(range(1, len(episodes) + 1))
    assert len(episodes) >= 7
    assert all(e.show_id == BREAKING_BAD and e.season == 1 and e.title for e in episodes)


@needs_token
async def test_tmdb_find_op_imdb(db):
    found = await db.find(MATRIX_IMDB, "movie", "en-US")
    assert [c.id for c in found] == [MATRIX]


@needs_token
async def test_tmdb_een_onbekend_id_is_niet_gevonden(db):
    with pytest.raises(http.NotFound):
        await db.details("movie", "movie:999999999", "en-US")


async def test_tmdb_een_foute_sleutel_is_provider_auth(settings):
    """Draait ook zonder sleutel (de sleutel is vast fout), maar alleen met -m provider_real."""
    if not (TOKEN or MUSICBRAINZ):
        pytest.skip("geen MEDIA_TEST_TMDB_TOKEN en geen MEDIA_TEST_MUSICBRAINZ=1")
    with pytest.raises(ProviderAuth):
        await tmdb.Tmdb(settings, "een-foute-sleutel-" + "q" * 20).search("movie", "x", None, "")


@needs_musicbrainz
async def test_musicbrainz_zoeken_opzoeken_en_de_voorkant(settings):
    mb = musicbrainz.MusicBrainz(settings)
    found = await mb.search_album("OK Computer", "Radiohead")
    assert found and any(r.title == "OK Computer" and r.artist == "Radiohead" for r in found)
    first = next(r for r in found if r.title == "OK Computer")
    release = await mb.release(first.id)
    assert release.group_id and (release.year or 0) >= 1997 and (release.track_count or 0) >= 12
    details = await mb.details("album", f"release:{first.id}", "")
    assert details.ids["musicbrainz"] == first.id
    with pytest.raises(http.NotFound):
        await mb.release("00000000-0000-4000-8000-000000000000")
    # De voorkant van een release met een voorkant (zoek er een die er een heeft)
    for candidate in found[:5]:
        if not (await mb.release(candidate.id)).cover:
            continue
        fetched = await caa.CoverArt(settings).front(candidate.id)
        assert art.sniff(fetched.body) in ("image/jpeg", "image/png")
        break
    else:
        pytest.fail("geen release met een voorkant bij de eerste vijf")
