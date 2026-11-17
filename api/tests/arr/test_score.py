"""Kwaliteitsprofielen en de keuze van de beste release (fase 11, stap 1)."""

from __future__ import annotations

import pytest

from app.arr.release import parse_release
from app.arr.score import BUILTIN, QUALITIES, Candidate, Profile, choose, is_upgrade, quality

GB = 1_000_000_000
HD = BUILTIN["hd-1080p"]


def cand(name: str, size: float = 2.0, seeders: int = 10, kind: str = "movies", id: str = ""):
    return Candidate(parse_release(name, kind), int(size * GB), seeders, id or name)


def test_quality_names() -> None:
    assert quality(parse_release("A.2020.1080p.WEB-DL.x264")) == "webdl-1080p"
    assert quality(parse_release("A.2020.2160p.BluRay.REMUX.HEVC")) == "remux-2160p"
    assert quality(parse_release("A.2020.HDCAM")) == "unknown"
    assert quality(parse_release("A.2020.x264")) == "unknown"


def test_builtin_profiles_are_valid() -> None:
    for profile in BUILTIN.values():
        assert profile.cutoff in profile.allowed
        assert all(q in QUALITIES for q in profile.allowed)


def test_profile_rejects_unknown_quality() -> None:
    with pytest.raises(ValueError):
        Profile(name="x", allowed=frozenset({"webdl-1080p"}), cutoff="bestaat-niet")


def test_choose_prefers_cutoff_quality_over_higher() -> None:
    best, _ = choose(
        [
            cand("A.2020.720p.HDTV.x264", 1),
            cand("A.2020.1080p.WEB-DL.x264", 3),
            cand("A.2020.1080p.BluRay.x264", 8),  # boven de cutoff, groter: wint niet
        ],
        HD,
        minutes=120,
    )
    assert best is not None and best.release.source == "webdl"


def test_choose_rejects_bad_small_large_and_blocked() -> None:
    best, verdicts = choose(
        [
            cand("A.2020.HDCAM.x264", 1),
            cand("A.2020.1080p.WEB-DL.x264", 0.1, id="te-klein"),
            cand("A.2020.1080p.WEB-DL.x264", 20, id="te-groot"),
            cand("A.2020.1080p.WEB-DL.x264", 3, id="zwart"),
            cand("A.2020.1080p.WEB-DL.x264", 3, seeders=0, id="geen-seeders"),
        ],
        Profile(
            name="t", allowed=HD.allowed, cutoff=HD.cutoff, min_mb_per_min=5, max_mb_per_min=60
        ),
        minutes=120,
        blocked={"zwart"},
    )
    assert best is None
    reasons = {v.candidate.id: v.reasons for v in verdicts}
    assert "slechte bron" in reasons["A.2020.HDCAM.x264"]
    assert reasons["te-klein"] == ("te klein",)
    assert reasons["te-groot"] == ("te groot",)
    assert reasons["zwart"] == ("op de zwarte lijst",)
    assert reasons["geen-seeders"] == ("te weinig seeders",)


def test_language_required() -> None:
    profile = Profile(name="nl", allowed=HD.allowed, cutoff=HD.cutoff, languages=("nl",))
    best, _ = choose(
        [cand("A.2020.1080p.WEB-DL.x264-A"), cand("A.2020.DUTCH.720p.WEB-DL.x264-B")], profile
    )
    assert best is not None and best.release.group == "B"


def test_revision_and_bonus_break_ties() -> None:
    best, _ = choose(
        [cand("A.2020.1080p.WEB-DL.x264-A"), cand("A.2020.1080p.WEB-DL.PROPER.x264-B")], HD
    )
    assert best.release.group == "B"
    small = BUILTIN["klein"]
    best, _ = choose(
        [cand("A.2020.1080p.WEB-DL.x264-A"), cand("A.2020.1080p.WEB-DL.x265-B")], small
    )
    assert best.release.group == "B"


def test_upgrades() -> None:
    hdtv = parse_release("A.2020.720p.HDTV.x264")
    web = parse_release("A.2020.1080p.WEB-DL.x264")
    web_proper = parse_release("A.2020.1080p.WEB-DL.PROPER.x264")
    bluray = parse_release("A.2020.1080p.BluRay.x264")
    assert is_upgrade(hdtv, web, HD)
    assert not is_upgrade(web, hdtv, HD)
    assert not is_upgrade(web, bluray, HD)  # de cutoff is bereikt
    assert is_upgrade(web, web_proper, HD)
    best, verdicts = choose([cand("A.2020.1080p.BluRay.x264", 5)], HD, current=web)
    assert best is None and verdicts[0].reasons == ("niet beter dan wat er is",)
