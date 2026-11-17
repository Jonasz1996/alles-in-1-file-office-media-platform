"""Releasenamen lezen (fase 11, stap 1)."""

from __future__ import annotations

import pytest

from app.arr.release import parse_release

CASES = [
    # naam, soort, verwacht (alleen de genoemde velden)
    (
        "Inception.2010.1080p.BluRay.x264-SPARKS",
        "movies",
        dict(
            title="Inception",
            year=2010,
            resolution="1080p",
            source="bluray",
            codec="h264",
            group="SPARKS",
        ),
    ),
    (
        "Inception 2010 2160p UHD BluRay REMUX HDR10 HEVC TrueHD Atmos 7.1-FGT",
        "movies",
        dict(
            resolution="2160p",
            source="remux",
            codec="h265",
            hdr=("hdr10",),
            audio=("truehd", "atmos"),
            group="FGT",
        ),
    ),
    (
        "Breaking.Bad.S01E02.720p.WEB-DL.DD5.1.H.264-NTb",
        "shows",
        dict(
            title="Breaking Bad",
            season=1,
            episode=2,
            full_season=False,
            resolution="720p",
            source="webdl",
            codec="h264",
            audio=("dd",),
            group="NTb",
        ),
    ),
    (
        "The.Office.US.S03.1080p.WEBRip.AAC2.0.x265-RARBG",
        "shows",
        dict(
            title="The Office US",
            season=3,
            episode=None,
            full_season=True,
            source="webrip",
            codec="h265",
            audio=("aac",),
        ),
    ),
    (
        "Fargo S01-S03 1080p BluRay x264",
        "shows",
        dict(title="Fargo", season=1, season_end=3, full_season=True, group=None),
    ),
    ("Fargo.Season.2.720p.HDTV", "shows", dict(season=2, full_season=True, source="hdtv")),
    # Een titel die op een bron lijkt, is geen bron
    (
        "Cam.2018.1080p.NF.WEB-DL.DDP5.1.x264-NTG",
        "movies",
        dict(title="Cam", year=2018, source="webdl", audio=("ddp",)),
    ),
    ("Dune.Part.Two.2024.HDCAM.x264-XYZ", "movies", dict(source="cam", bad=True)),
    (
        "Show.S01E01.PROPER.REPACK.DUTCH.720p.HDTV-GRP[rarbg]",
        "shows",
        dict(revision=3, languages=("nl",), group="GRP"),
    ),
    (
        "Movie.2020.MULTI.1080p.WEB.H264-ABC",
        "movies",
        dict(multi=True, languages=("en",), source="webdl"),
    ),
    ("Film.2019.1080p.BluRay.DTS-HD.MA.5.1.x264", "movies", dict(audio=("dtshd",))),
    (
        "Film.2021.2160p.WEB-DL.DV.HDR10+.DDP5.1.Atmos.H.265-GRP",
        "movies",
        dict(hdr=("dv", "hdr10+"), audio=("ddp", "atmos"), codec="h265"),
    ),
    ("Film.2022.720p.mkv", "movies", dict(resolution="720p", source=None, group=None)),
]


@pytest.mark.parametrize(("name", "kind", "expected"), CASES, ids=[c[0] for c in CASES])
def test_parse_release(name: str, kind: str, expected: dict) -> None:
    release = parse_release(name, kind)
    for key, value in expected.items():
        assert getattr(release, key) == value, (key, release)


def test_without_language_word_is_english() -> None:
    assert parse_release("Film.2020.1080p.WEB-DL.x264-A").languages == ("en",)
