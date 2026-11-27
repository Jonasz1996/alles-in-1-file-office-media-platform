"""Een releasenaam lezen: pure functies, geen I/O.

`parse_release(name, kind) -> Release`. kind is "movies" of "shows". De titel, het jaar en het
seizoen en de aflevering komen uit de eigen parser van fase 5 (`app.media.names`); hier komt de
kwaliteit bij, die die parser als ruis weggooit:

- resolutie: 2160p (ook 4K, UHD), 1080p, 720p, 576p, 480p;
- bron: bluray, remux (een BluRay-remux), webdl, webrip, hdtv, dvd, en de slechte: cam, ts
  (telesync), tc (telecine), screener;
- codec: h264, h265, av1, xvid, vc1, mpeg2;
- HDR: hdr10, hdr10+, dv (Dolby Vision), hlg;
- audio: truehd, atmos, dtsx, dtshd, dts, ddp, dd, aac, flac, opus, mp3;
- talen: uit woorden als `NL`, `DUTCH`, `FLEMISH`, `MULTI`, `VOSTFR`; zonder taalwoord Engels;
- groep: na de laatste `-` (`…x264-GROEP`), zonder `[site]` erachter;
- proper en repack: elk telt als een revisie (`PROPER` 2, `REPACK2` 3);
- seizoenspakket: een seizoen zonder aflevering (`S01`, `Season 1`, `S01-S03`).

De woorden zoeken we alleen als heel token (`hdtv`, niet in `hdtvshow`) en pas na de titel,
zodat een film als "Cam" of "Remux" niet misgaat.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

from app.media import names

RELEASE_VERSION = 1  # omhoog bij elke verandering die een uitkomst verandert

RESOLUTIONS = ("2160p", "1080p", "720p", "576p", "480p")
SOURCES = ("remux", "bluray", "webdl", "webrip", "hdtv", "dvd", "screener", "tc", "ts", "cam")
BAD_SOURCES = frozenset({"cam", "ts", "tc", "screener"})

_TOKEN = re.compile(r"[a-z0-9+]+")

_RES = {
    "2160p": "2160p",
    "4k": "2160p",
    "uhd": "2160p",
    "1080p": "1080p",
    "1080i": "1080p",
    "720p": "720p",
    "576p": "576p",
    "480p": "480p",
    "sd": "480p",
}
_SOURCE = {
    "bluray": "bluray",
    "bdrip": "bluray",
    "brrip": "bluray",
    "bdremux": "remux",
    "remux": "remux",
    "webdl": "webdl",
    "web": "webdl",
    "webrip": "webrip",
    "hdtv": "hdtv",
    "pdtv": "hdtv",
    "dvdrip": "dvd",
    "dvd": "dvd",
    "dvd5": "dvd",
    "dvd9": "dvd",
    "cam": "cam",
    "camrip": "cam",
    "hdcam": "cam",
    "ts": "ts",
    "telesync": "ts",
    "hdts": "ts",
    "tc": "tc",
    "telecine": "tc",
    "scr": "screener",
    "screener": "screener",
    "dvdscr": "screener",
}
_CODEC = {
    "x264": "h264",
    "h264": "h264",
    "avc": "h264",
    "x265": "h265",
    "h265": "h265",
    "hevc": "h265",
    "av1": "av1",
    "xvid": "xvid",
    "divx": "xvid",
    "vc1": "vc1",
    "mpeg2": "mpeg2",
}
_HDR = {
    "hdr": "hdr10",
    "hdr10": "hdr10",
    "hdr10+": "hdr10+",
    "hdr10plus": "hdr10+",
    "dv": "dv",
    "dovi": "dv",
    "dolbyvision": "dv",
    "hlg": "hlg",
}
_AUDIO = {
    "truehd": "truehd",
    "atmos": "atmos",
    "dtsx": "dtsx",
    "dtshd": "dtshd",
    "dtsma": "dtshd",
    "dts": "dts",
    "ddp": "ddp",
    "eac3": "ddp",
    "dd+": "ddp",
    "dd": "dd",
    "ac3": "dd",
    "aac": "aac",
    "flac": "flac",
    "opus": "opus",
    "mp3": "mp3",
}
_LANG = {
    "nl": "nl",
    "dutch": "nl",
    "nederlands": "nl",
    "flemish": "nl",
    "vlaams": "nl",
    "nlsubs": None,
    "english": "en",
    "eng": "en",
    "french": "fr",
    "vostfr": "fr",
    "truefrench": "fr",
    "german": "de",
    "ger": "de",
    "spanish": "es",
    "esp": "es",
    "italian": "it",
    "ita": "it",
    "japanese": "ja",
    "jpn": "ja",
}
_MULTI = frozenset({"multi", "multi2", "dual", "dualaudio"})
_SEASON_PACK = re.compile(
    r"(?<![a-z0-9])(?:s(\d{1,2})(?:\s*-\s*s?(\d{1,2}))?|season\s*(\d{1,2})|seizoen\s*(\d{1,2}))"
    r"(?![a-z0-9]|\s*e\d)",
    re.IGNORECASE,
)
_GROUP = re.compile(r"-([A-Za-z0-9][A-Za-z0-9._]{0,31})\s*(?:\[[^\]]*\])?\s*$")
_EXT = re.compile(r"\.(mkv|mp4|avi|m4v|ts|torrent)$", re.IGNORECASE)


@dataclass(frozen=True)
class Release:
    name: str
    title: str | None = None
    year: int | None = None
    season: int | None = None
    season_end: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    full_season: bool = False
    resolution: str | None = None
    source: str | None = None
    codec: str | None = None
    hdr: tuple[str, ...] = ()
    audio: tuple[str, ...] = ()
    languages: tuple[str, ...] = ("en",)
    multi: bool = False
    group: str | None = None
    revision: int = 1
    tags: tuple[str, ...] = field(default=())

    @property
    def bad(self) -> bool:
        """Een cam, telesync, telecine of screener: nooit automatisch kiezen."""
        return self.source in BAD_SOURCES


def _fields(parsed: names.Parsed) -> dict:
    return {f.name: getattr(parsed, f.name) for f in dataclasses.fields(parsed)}


def _normalise(name: str) -> str:
    text = _EXT.sub("", name.strip())[: names.MAX_INPUT]
    # Losse varianten aan elkaar: WEB-DL, WEB DL, DTS-HD, DD+5.1, H.264, Dolby Vision
    text = re.sub(r"(?i)\bweb[\s._-]?dl\b", "WEBDL", text)
    text = re.sub(r"(?i)\bdts[\s._-]?(hd|x|ma)\b", lambda m: "DTS" + m.group(1).upper(), text)
    text = re.sub(r"(?i)\bdd[p+]\s*([257]\.[01])?", "DDP ", text)
    text = re.sub(r"(?i)\b(dd|aac|truehd|flac|opus|dts|ac3|eac3)\s*([1-9]\.[01])\b", r"\1 ", text)
    text = re.sub(r"(?i)\b([hx])\.(26[45])\b", r"\1\2", text)
    text = re.sub(r"(?i)\bdolby[\s._-]?vision\b", "DOLBYVISION", text)
    text = re.sub(r"(?i)\bdual[\s._-]audio\b", "DUALAUDIO", text)
    text = re.sub(r"(?i)\bhdr10(\+|plus)", "HDR10+", text)
    return text


def _quality_tokens(text: str, title: str | None) -> list[str]:
    """De tokens na de titel (de titel zelf kan 'Cam' of 'Remux' heten)."""
    lowered = text.lower()
    start = 0
    if title:
        squashed = re.sub(r"[^a-z0-9]+", "", title.lower())
        # Zoek het einde van de titel in de naam, teken voor teken op letters en cijfers
        seen = 0
        for index, char in enumerate(lowered):
            if seen == len(squashed):
                start = index
                break
            if char.isalnum():
                if char == squashed[seen]:
                    seen += 1
                else:
                    start = 0
                    break
    return _TOKEN.findall(lowered[start:])


def parse_release(name: str, kind: str = "movies") -> Release:
    text = _normalise(name)
    parsed = names.parse(text.replace("/", " "), "shows" if kind == "shows" else "movies")
    tokens = _quality_tokens(text, parsed.title)

    resolution = source = codec = None
    hdr: list[str] = []
    audio: list[str] = []
    languages: list[str] = []
    multi = False
    revision = 1
    tags: list[str] = []
    for token in tokens:
        if resolution is None and token in _RES:
            resolution = _RES[token]
        elif token in _SOURCE:
            found = _SOURCE[token]
            # remux wint van bluray, en een echte bron van "web" als het ook webrip zegt
            if source is None or found == "remux" or (source == "webdl" and found == "webrip"):
                source = found
        elif codec is None and token in _CODEC:
            codec = _CODEC[token]
        elif token in _HDR:
            if _HDR[token] not in hdr:
                hdr.append(_HDR[token])
        elif token in _AUDIO:
            if _AUDIO[token] not in audio:
                audio.append(_AUDIO[token])
        elif token in _MULTI:
            multi = True
        elif token in _LANG and _LANG[token]:
            if _LANG[token] not in languages:
                languages.append(_LANG[token])
        elif token in ("proper", "real"):
            revision += 1
            tags.append(token)
        elif token.startswith("repack") and (token[6:] == "" or token[6:].isdigit()):
            revision += int(token[6:] or 1)
            tags.append("repack")
        elif token in ("internal", "limited", "extended", "hybrid", "imax", "uncut", "remastered"):
            tags.append(token)
    if source == "bluray" and "remux" in tokens:
        source = "remux"
    if multi and "en" not in languages:
        languages.append("en")
    if not languages:
        languages = ["en"]

    season, season_end, episode, episode_end = parsed.season, None, parsed.episode, None
    full_season = False
    if kind == "shows":
        episode_end = parsed.episode_end
        if episode is None:
            pack = _SEASON_PACK.search(text.replace(".", " "))
            if pack:
                first = pack.group(1) or pack.group(3) or pack.group(4)
                season = int(first)
                season_end = int(pack.group(2)) if pack.group(2) else None
                full_season = True
                title = parsed.title or parsed.show
                if title:  # de parser van fase 5 laat "S03" in de titel staan
                    cut = re.split(r"(?i)\s+(?:s\d{1,2}|season\s*\d|seizoen\s*\d)", title)[0]
                    parsed = names.Parsed(**{**_fields(parsed), "title": cut, "show": cut})

    group_match = _GROUP.search(text)
    group = group_match.group(1) if group_match else None
    if group and group.lower() in _CODEC | _AUDIO | _SOURCE | _RES:
        group = None

    return Release(
        name=name,
        title=parsed.title or parsed.show,
        year=parsed.year,
        season=season,
        season_end=season_end,
        episode=episode,
        episode_end=episode_end,
        full_season=full_season,
        resolution=resolution,
        source=source,
        codec=codec,
        hdr=tuple(hdr),
        audio=tuple(audio),
        languages=tuple(languages),
        multi=multi,
        group=group,
        revision=revision,
        tags=tuple(tags),
    )
