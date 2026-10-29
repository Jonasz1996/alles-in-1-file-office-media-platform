"""Wat een bestand in een bibliotheek is (Keuze 5, 8, 9, M2): app/media/classify.py.

Extensies, extra's (map en achtervoegsel), samples, losse beeldondertitels en de namen van lokale
art. Pure functies: de scanner beslist met deze antwoorden.
"""

from __future__ import annotations

import random

import pytest

from app.media import classify


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Film.mkv", "video"),
        ("FILM.MP4", "video"),
        ("a.m2ts", "video"),
        ("nummer.flac", "audio"),
        ("nummer.Opus", "audio"),
        ("film.nl.srt", "subtitle"),
        ("film.ass", "subtitle"),
        ("film.sup", "image_subtitle"),
        ("film.idx", "image_subtitle"),
        ("movie.nfo", "nfo"),
        ("poster.JPG", "image"),
        ("lees mij.txt", "other"),
        ("geen-extensie", "other"),
        (".mkv", "other"),  # een verborgen bestand zonder naam is geen video
    ],
)
def test_soort_op_extensie(name, kind):
    assert classify.file_kind(name) == kind


def test_mime_alleen_zonder_extensie():
    assert classify.file_kind("opname", "video/mp4") == "video"
    assert classify.file_kind("opname", "audio/ogg") == "audio"
    assert classify.file_kind("opname.txt", "video/mp4") == "other"


def test_stem_haalt_alleen_een_bekende_extensie_weg():
    assert classify.stem("Film (2010).mkv") == "Film (2010)"
    assert classify.stem("Mr. Robot") == "Mr. Robot"
    assert classify.stem("Film.2010") == "Film.2010"


@pytest.mark.parametrize(
    ("parts", "extra"),
    [
        (["Film (2010)", "Extras", "Interview.mkv"], "other"),
        (["Film (2010)", "Featurettes", "Maken.mkv"], "featurette"),
        (["Film (2010)", "behind the scenes", "x.mkv"], "behind"),
        (["Film (2010)", "Deleted.Scenes", "x.mkv"], "deleted"),
        (["Film (2010)", "Trailers", "x.mkv"], "trailer"),
        (["Film (2010)", "Film (2010)-trailer.mkv"], "trailer"),
        (["Film (2010)", "Film (2010) - Featurette.mkv"], "featurette"),
        (["Film (2010)", "trailer.mp4"], "trailer"),
        (["Film (2010)", "Film.2010.sample.mkv"], "sample"),
        (["Film (2010)", "sample-film.mkv"], "sample"),
        (["Film (2010)", "Samples", "x.mkv"], "sample"),
        (["Film (2010)", "Film (2010).mkv"], None),
        (["Spider-Man (2002)", "Spider-Man (2002).mkv"], None),  # een streep is geen extra
        (["Samplers (2010)", "Samplers (2010).mkv"], None),  # sample als deel van een woord
        (["The Other Guys (2010)", "The Other Guys.mkv"], None),
    ],
)
def test_extras(parts, extra):
    assert classify.extra_of(parts) == extra


def test_achtervoegsel_en_de_naam_zonder():
    assert classify.extra_suffix("Film (2010)-trailer") == ("trailer", "Film (2010)")
    assert classify.extra_suffix("Film - Deleted") == ("deleted", "Film")
    assert classify.extra_suffix("trailer") == ("trailer", "")
    assert classify.extra_suffix("-trailer") == (None, "-trailer")
    assert classify.extra_suffix("Film") == (None, "Film")


@pytest.mark.parametrize(
    ("library", "parts", "kind"),
    [
        ("movies", ["Film (2010)", "Film (2010).mkv"], "movie"),
        ("movies", ["Film (2010)", "Extras", "x.mkv"], "extra"),
        ("movies", ["Film (2010)", "Film (2010).nl.srt"], "other"),
        ("movies", ["Film (2010)", "poster.jpg"], "other"),
        ("shows", ["Serie", "Season 1", "Serie S01E02.mkv"], "episode"),
        ("shows", ["Serie", "Season 1", "02 - Pilot.mkv"], "episode"),
        ("shows", ["Serie", "Achter de schermen.mkv"], "video"),
        ("shows", ["Serie", "Extras", "Serie S01E02 interview.mkv"], "extra"),
        ("videos", ["2024", "verjaardag.mp4"], "video"),
        ("videos", ["2024", "Extras", "x.mp4"], "video"),  # eigen video's kennen geen extra's
        ("music", ["Artiest", "Album", "01 - Nummer.flac"], "track"),
        ("music", ["Artiest", "Album", "clip.mkv"], "other"),
        ("movies", [], "other"),
    ],
)
def test_classify(library, parts, kind):
    assert classify.classify(library, parts, None) == kind


def test_lokale_art():
    assert classify.art_role("poster.jpg") == ("poster", None)
    assert classify.art_role("folder.png") == ("poster", None)
    assert classify.art_role("fanart.webp") == ("backdrop", None)
    assert classify.art_role("clearlogo.png") == ("logo", None)
    assert classify.art_role("poster.gif") is None  # alleen jpg, png en webp
    videos = ["Film (2010)", "Film (2010) cd2"]
    assert classify.art_role("Film (2010)-poster.jpg", videos) == ("poster", "Film (2010)")
    assert classify.art_role("Film (2010) cd2-fanart.jpg", videos) == (
        "backdrop",
        "Film (2010) cd2",
    )
    assert classify.art_role("Ander-poster.jpg", videos) is None
    assert classify.art_role("vakantie.jpg", videos) is None


def test_art_van_een_seizoen():
    assert classify.season_art("season01-poster.jpg") == (1, "poster")
    assert classify.season_art("Season 2-banner.png") == (2, "banner")
    assert classify.season_art("season-specials-poster.jpg") == (0, "poster")
    assert classify.season_art("season-all-fanart.jpg") == (None, "backdrop")
    assert classify.season_art("season01.jpg") is None
    assert classify.season_art("poster.jpg") is None


def test_fuzz_namen_geven_altijd_een_antwoord():
    rng = random.Random(5)
    alphabet = "abcXYZ019 ._-[](){}éß千\x00/'"
    exts = ["mkv", "srt", "nfo", "jpg", "flac", "sup", "", "MKV"]
    for _ in range(3000):
        stem = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        name = f"{stem}.{rng.choice(exts)}"
        parts = [stem[: rng.randint(0, 10)] or "map", name]
        for library in ("movies", "shows", "music", "videos"):
            assert classify.classify(library, parts, None) in (
                "movie", "episode", "extra", "video", "track", "other",
            )  # fmt: skip
        classify.art_role(name, [stem])
        classify.season_art(name)
        classify.extra_suffix(stem)
