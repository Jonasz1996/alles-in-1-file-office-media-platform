"""Wat een bestand in een bibliotheek is (Keuze 5, 8, 9): film, aflevering, extra, video of
nummer, en wat de bestanden ernaast zijn (ondertitels, NFO, plaatjes). Pure functies, geen I/O.

- Op extensie: video `mkv mp4 m4v mov webm avi ts m2ts mts mpg mpeg ogv wmv`, audio `mp3 flac
  m4a aac opus ogg oga wav wma`, ondertitels `srt vtt ass ssa`, `nfo`, en plaatjes `jpg jpeg png
  webp`. Losse beeldondertitels (`sup`, `sub` met `idx`) koppelen we in fase 5 niet: de speler
  toont nooit een spoor dat niet kan (names-open.tsv).
- Extra's: in een map `Extras`, `Featurettes`, `Trailers`, `Behind The Scenes`, `Deleted Scenes`,
  `Interviews`, `Scenes`, `Shorts`, `Samples` of `Other`, of met `-trailer`, `-featurette`,
  `-deleted` (en de andere achtervoegsels van Jellyfin) aan het eind van de naam. Een sample
  (`sample` als los woord in de naam) is een extra van het soort sample; de scanner toont hem
  niet zolang hij korter is dan 5 minuten (Keuze 5).
- Verborgen bestanden en mappen (de naam begint met een punt, zoals de AppleDouble `._Film.mkv`
  van een Mac) zijn altijd other: geen item, geen ondertitel, geen art.
- Plaatjes met een bekende naam (Keuze 9): `poster`, `folder`, `cover`, `fanart`, `backdrop`,
  `background`, `banner`, `logo`, `clearlogo`, `thumb`, `landscape`, `<naam>-poster`,
  `<naam>-fanart`, `<naam>-thumb`, en `season01-poster` of `season-specials-poster` bij een
  serie.
"""

from __future__ import annotations

import re
from typing import Literal

Kind = Literal["movie", "episode", "extra", "video", "track", "other"]
FileKind = Literal["video", "audio", "subtitle", "nfo", "image", "image_subtitle", "other"]
ExtraKind = Literal["trailer", "featurette", "deleted", "behind", "sample", "other"]
ArtRole = Literal["poster", "backdrop", "banner", "logo", "thumb"]

VIDEO = frozenset("mkv mp4 m4v mov webm avi ts m2ts mts mpg mpeg ogv wmv".split())
AUDIO = frozenset("mp3 flac m4a aac opus ogg oga wav wma".split())
SUBTITLES = frozenset("srt vtt ass ssa".split())
IMAGE_SUBTITLES = frozenset("sup sub idx".split())  # niet in fase 5 (names-open.tsv)
NFO = frozenset({"nfo"})
IMAGES = frozenset("jpg jpeg png webp".split())
KNOWN = VIDEO | AUDIO | SUBTITLES | IMAGE_SUBTITLES | NFO | IMAGES

SAMPLE_MAX_MS = 5 * 60 * 1000  # een sample is korter dan 5 minuten (Keuze 5)

# Mappen met extra's (zonder hoofdletters; spaties, punten en streepjes als één spatie)
EXTRA_DIRS: dict[str, ExtraKind] = {
    "extras": "other",
    "extra": "other",
    "featurettes": "featurette",
    "featurette": "featurette",
    "trailers": "trailer",
    "trailer": "trailer",
    "behind the scenes": "behind",
    "behindthescenes": "behind",
    "making of": "behind",
    "deleted scenes": "deleted",
    "deleted": "deleted",
    "deletedscenes": "deleted",
    "interviews": "other",
    "scenes": "other",
    "shorts": "other",
    "clips": "other",
    "samples": "sample",
    "sample": "sample",
    "other": "other",
}
# Achtervoegsels van Jellyfin en Plex: "Film (2010)-trailer.mkv"
_SUFFIX: dict[str, ExtraKind] = {
    "trailer": "trailer",
    "featurette": "featurette",
    "deleted": "deleted",
    "deletedscene": "deleted",
    "behindthescenes": "behind",
    "interview": "other",
    "scene": "other",
    "short": "other",
    "clip": "other",
    "other": "other",
    "extra": "other",
    "sample": "sample",
}
_EXTRA_SUFFIX = re.compile(r"\s*-\s*(" + "|".join(_SUFFIX) + r")\s*$", re.IGNORECASE)
_SAMPLE = re.compile(r"(?:^|[\s._\-\[(])sample(?:$|[\s._\-\])])", re.IGNORECASE)
_DIR_SPACES = re.compile(r"[\s._\-]+")

_ART_NAMES: dict[str, ArtRole] = {
    "poster": "poster",
    "folder": "poster",
    "cover": "poster",
    "default": "poster",
    "movie": "poster",
    "show": "poster",
    "fanart": "backdrop",
    "backdrop": "backdrop",
    "background": "backdrop",
    "art": "backdrop",
    "banner": "banner",
    "logo": "logo",
    "clearlogo": "logo",
    "thumb": "thumb",
    "landscape": "thumb",
}
_ART_SUFFIX: dict[str, ArtRole] = {
    "poster": "poster",
    "cover": "poster",
    "fanart": "backdrop",
    "backdrop": "backdrop",
    "banner": "banner",
    "logo": "logo",
    "clearlogo": "logo",
    "thumb": "thumb",
    "landscape": "thumb",
}
_SEASON_ART = re.compile(
    r"^season[\s._\-]?(\d{1,3}|specials|all)[\s._\-](poster|fanart|banner|thumb)$", re.IGNORECASE
)


def extension(name: str) -> str:
    """De extensie zonder punt, in kleine letters ("" als er geen is)."""
    base, dot, ext = name.rpartition(".")
    return ext.lower() if dot and base else ""


def stem(name: str) -> str:
    """De naam zonder een bekende extensie."""
    base, dot, ext = name.rpartition(".")
    if dot and base and ext.lower() in KNOWN:
        return base
    return name


def hidden(name: str) -> bool:
    """Een verborgen bestand of map (de naam begint met een punt): de AppleDouble `._Film.mkv`
    die een Mac op een share of in een import achterlaat, `.DS_Store`, `.AppleDouble/`. Dat
    wordt nooit een item of iets ernaast (review golf 3, S5)."""
    return name.startswith(".")


def file_kind(name: str, mime: str | None = None) -> FileKind:
    """Het soort bestand op extensie; de mime helpt alleen zonder extensie. Een verborgen
    bestand is altijd other."""
    if hidden(name):
        return "other"
    ext = extension(name)
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext in SUBTITLES:
        return "subtitle"
    if ext in IMAGE_SUBTITLES:
        return "image_subtitle"
    if ext in NFO:
        return "nfo"
    if ext in IMAGES:
        return "image"
    if mime and not ext:
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "audio"
    return "other"


def _dir_key(name: str) -> str:
    return _DIR_SPACES.sub(" ", name.strip().lower()).strip()


def extra_dir(name: str) -> ExtraKind | None:
    """Is deze map een map met extra's? Dan het soort."""
    return EXTRA_DIRS.get(_dir_key(name))


def extra_suffix(name_stem: str) -> tuple[ExtraKind | None, str]:
    """Een achtervoegsel als "-trailer" aan het eind: (soort, de naam zonder). "trailer" of
    "sample" als hele naam (Jellyfin: trailer.mp4 in de map van de film) telt ook; de naam is
    dan leeg."""
    whole = name_stem.strip().lower()
    if whole in ("trailer", "sample"):
        return _SUFFIX[whole], ""
    found = _EXTRA_SUFFIX.search(name_stem)
    if found is None or found.start() == 0:
        return None, name_stem
    return _SUFFIX[found.group(1).lower()], name_stem[: found.start()]


def extra_of(parts: list[str]) -> ExtraKind | None:
    """Het soort extra van dit pad (vanaf de map van de bibliotheek), of None. Een map met
    extra's gaat voor het achtervoegsel; een los woord sample maakt er een sample van."""
    if not parts:
        return None
    for folder in parts[:-1]:
        kind = extra_dir(folder)
        if kind is not None:
            return kind
    name = stem(parts[-1])
    low = name.lower()
    if "-" in name or low.strip() in ("trailer", "sample"):
        kind, _ = extra_suffix(name)
        if kind is not None:
            return kind
    if "sample" in low and _SAMPLE.search(name):
        return "sample"
    return None


def art_role(name: str, video_stems: list[str] | None = None) -> tuple[ArtRole, str | None] | None:
    """Wat dit plaatje is: (rol, de naam van de video waar hij bij hoort, of None voor de hele
    map). video_stems: de namen (zonder extensie) van de video's in dezelfde map, voor
    "<naam>-poster.jpg". None als het geen bekend plaatje is."""
    if hidden(name) or extension(name) not in IMAGES:
        return None
    low = stem(name).lower()
    if low in _ART_NAMES:
        return _ART_NAMES[low], None
    for video in sorted(video_stems or (), key=len, reverse=True):
        v = video.lower()
        if len(low) > len(v) + 1 and low.startswith(v) and low[len(v)] in " ._-":
            rest = low[len(v) :].lstrip(" ._-")
            if rest in _ART_SUFFIX:
                return _ART_SUFFIX[rest], video
    return None


def season_art(name: str) -> tuple[int | None, ArtRole] | None:
    """season01-poster.jpg: (seizoen, rol); season-specials-poster is seizoen 0, season-all
    None (de hele serie). None als het dat niet is."""
    if extension(name) not in IMAGES:
        return None
    found = _SEASON_ART.match(stem(name))
    if found is None:
        return None
    which, role = found.group(1).lower(), found.group(2).lower()
    season = 0 if which == "specials" else None if which == "all" else int(which)
    return season, _ART_SUFFIX[role]


def classify(library_kind: str, path_parts: list[str], mime: str | None) -> Kind:
    """Wat dit bestand in een bibliotheek van dit soort is. path_parts: het pad vanaf de map van
    de bibliotheek (de naam als laatste). In een bibliotheek met films is elke video een film
    (of een extra); in een met series een aflevering als de naam er een noemt (anders een
    video); muziek kent alleen nummers; eigen video's alleen video's. De rest is other: dat
    wordt geen item (ondertitels, NFO en plaatjes horen bij een video)."""
    if not path_parts or any(hidden(part) for part in path_parts):
        return "other"
    kind = file_kind(path_parts[-1], mime)
    if library_kind == "music":
        return "track" if kind == "audio" else "other"
    if kind != "video":
        return "other"
    if library_kind == "videos":
        return "video"
    if extra_of(path_parts) is not None:
        return "extra"
    if library_kind == "movies":
        return "movie"
    if library_kind == "shows":
        from app.media import names  # names gebruikt dit bestand; pas hier laden

        return "episode" if names.parse(path_parts, "shows").is_episode else "video"
    return "other"
