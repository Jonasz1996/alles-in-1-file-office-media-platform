"""Kwaliteitsprofielen en de keuze van de beste release: pure functies, geen I/O.

Een kwaliteit is bron plus resolutie (`webdl-1080p`), zoals in Sonarr en Radarr. `QUALITIES` zet
ze van slecht naar goed. Een profiel zegt welke kwaliteiten mogen, tot welke kwaliteit de app
blijft upgraden (`cutoff`), welke talen er moeten zijn, hoe groot een release mag zijn (in MB
per minuut speelduur) en wat extra punten geeft (een codec, HDR, een groep).

`choose(candidates, profile, current=None)` geeft de beste kandidaat terug, of None. Een
kandidaat valt af als hij slecht is (cam, ts, tc, screener), een kwaliteit heeft die niet mag,
de taal mist, te groot of te klein is, te weinig seeders heeft, op de zwarte lijst staat, of niet
beter is dan wat er al is. Van de rest wint, in die volgorde: de hoogste kwaliteit (maar niet
boven de cutoff als er een kandidaat op de cutoff is: die is kleiner), de hoogste revisie
(PROPER, REPACK), de meeste extra punten, de meeste seeders, de kleinste.

De ingebouwde profielen (`BUILTIN`) maken Recyclarr en Profilarr overbodig: er valt niets te
synchroniseren.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.arr.release import Release

QUALITIES: tuple[str, ...] = (
    "unknown",
    "dvd-480p",
    "hdtv-480p",
    "webrip-480p",
    "webdl-480p",
    "dvd-576p",
    "hdtv-720p",
    "webrip-720p",
    "webdl-720p",
    "bluray-720p",
    "hdtv-1080p",
    "webrip-1080p",
    "webdl-1080p",
    "bluray-1080p",
    "remux-1080p",
    "hdtv-2160p",
    "webrip-2160p",
    "webdl-2160p",
    "bluray-2160p",
    "remux-2160p",
)
_RANK = {name: index for index, name in enumerate(QUALITIES)}


def quality(release: Release) -> str:
    """De kwaliteit van een release; zonder resolutie of bron 'unknown'."""
    source = release.source
    resolution = release.resolution
    if source is None or resolution is None or release.bad:
        return "unknown"
    if source == "dvd":
        resolution = "576p" if resolution == "576p" else "480p"
    if source == "remux" and resolution not in ("1080p", "2160p"):
        source = "bluray"
    name = f"{source}-{resolution}"
    return name if name in _RANK else "unknown"


def rank(name: str) -> int:
    return _RANK.get(name, 0)


@dataclass(frozen=True)
class Profile:
    name: str
    allowed: frozenset[str]
    cutoff: str
    languages: tuple[str, ...] = ()  # leeg: elke taal; anders moet er één van in zitten
    min_mb_per_min: float = 0.0
    max_mb_per_min: float = 0.0  # 0: geen grens
    min_seeders: int = 1
    bonus: dict[str, int] = field(default_factory=dict)  # "codec:h265", "hdr:dv", "group:X"
    upgrades: bool = True

    def __post_init__(self) -> None:
        unknown = [name for name in (*self.allowed, self.cutoff) if name not in _RANK]
        if unknown:
            raise ValueError(f"onbekende kwaliteit: {', '.join(sorted(unknown))}")
        if self.cutoff not in self.allowed:
            raise ValueError("de cutoff moet zelf toegelaten zijn")


def _between(low: str, high: str) -> frozenset[str]:
    return frozenset(QUALITIES[rank(low) : rank(high) + 1])


BUILTIN: dict[str, Profile] = {
    "hd-1080p": Profile(
        name="HD 1080p",
        allowed=_between("hdtv-720p", "bluray-1080p"),
        cutoff="webdl-1080p",
        max_mb_per_min=60.0,
    ),
    "hd-720p": Profile(
        name="HD 720p",
        allowed=_between("hdtv-720p", "bluray-720p"),
        cutoff="webdl-720p",
        max_mb_per_min=25.0,
    ),
    "uhd-2160p": Profile(
        name="Ultra HD 2160p",
        allowed=_between("webdl-1080p", "remux-2160p") - {"hdtv-2160p"},
        cutoff="bluray-2160p",
        bonus={"hdr:hdr10": 10, "hdr:dv": 5},
    ),
    "klein": Profile(
        name="Klein (h265, tot 1080p)",
        allowed=_between("hdtv-720p", "bluray-1080p"),
        cutoff="webdl-1080p",
        max_mb_per_min=20.0,
        bonus={"codec:h265": 20},
    ),
}


@dataclass(frozen=True)
class Candidate:
    release: Release
    size: int  # bytes
    seeders: int = 0
    id: str = ""  # bijv. de guid van de bron


@dataclass(frozen=True)
class Verdict:
    candidate: Candidate
    accepted: bool
    reasons: tuple[str, ...]  # waarom niet, of leeg
    quality: str
    points: int


def points(release: Release, profile: Profile) -> int:
    total = 0
    for key, value in profile.bonus.items():
        kind, _, wanted = key.partition(":")
        if (
            (kind == "codec" and release.codec == wanted)
            or (kind == "hdr" and wanted in release.hdr)
            or (kind == "audio" and wanted in release.audio)
            or (kind == "group" and (release.group or "").lower() == wanted.lower())
            or (kind == "tag" and wanted in release.tags)
        ):
            total += value
    return total


def judge(
    candidate: Candidate,
    profile: Profile,
    *,
    minutes: float | None = None,
    current: Release | None = None,
    blocked: Iterable[str] = (),
) -> Verdict:
    release = candidate.release
    name = quality(release)
    reasons: list[str] = []
    if release.bad:
        reasons.append("slechte bron")
    elif name not in profile.allowed:
        reasons.append(f"kwaliteit {name} niet toegelaten")
    if profile.languages and not set(profile.languages) & set(release.languages):
        reasons.append("taal ontbreekt")
    if candidate.seeders < profile.min_seeders:
        reasons.append("te weinig seeders")
    if candidate.id and candidate.id in set(blocked):
        reasons.append("op de zwarte lijst")
    if minutes and minutes > 0:
        per_min = candidate.size / 1_000_000 / minutes
        if per_min < profile.min_mb_per_min:
            reasons.append("te klein")
        if profile.max_mb_per_min and per_min > profile.max_mb_per_min:
            reasons.append("te groot")
    if current is not None and not reasons:
        if not is_upgrade(current, release, profile):
            reasons.append("niet beter dan wat er is")
    return Verdict(candidate, not reasons, tuple(reasons), name, points(release, profile))


def is_upgrade(current: Release, new: Release, profile: Profile) -> bool:
    """Is `new` een verbetering van `current` volgens het profiel?"""
    if not profile.upgrades:
        return False
    old_rank, new_rank, cut = rank(quality(current)), rank(quality(new)), rank(profile.cutoff)
    if old_rank >= cut:
        # Het doel is bereikt: alleen nog een nieuwere revisie van dezelfde kwaliteit
        return new_rank == old_rank and new.revision > current.revision
    if new_rank != old_rank:
        return new_rank > old_rank
    if new.revision != current.revision:
        return new.revision > current.revision
    return points(new, profile) > points(current, profile)


def _key(verdict: Verdict, cut: int) -> tuple:
    candidate = verdict.candidate
    quality_rank = min(rank(verdict.quality), cut)  # boven de cutoff wint niets meer
    return (
        quality_rank,
        candidate.release.revision,
        verdict.points,
        candidate.seeders,
        -candidate.size,
        rank(verdict.quality),
    )


def choose(
    candidates: Sequence[Candidate],
    profile: Profile,
    *,
    minutes: float | None = None,
    current: Release | None = None,
    blocked: Iterable[str] = (),
) -> tuple[Candidate | None, list[Verdict]]:
    """De beste kandidaat (of None) en het oordeel over elke kandidaat (voor de geschiedenis)."""
    blocked = frozenset(blocked)
    verdicts = [
        judge(c, profile, minutes=minutes, current=current, blocked=blocked) for c in candidates
    ]
    good = [v for v in verdicts if v.accepted]
    if not good:
        return None, verdicts
    cut = rank(profile.cutoff)
    best = max(good, key=lambda v: _key(v, cut))
    return best.candidate, verdicts
