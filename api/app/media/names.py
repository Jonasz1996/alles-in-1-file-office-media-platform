"""Een eigen parser van bestandsnamen (Keuze 7 en 8): pure functies, geen I/O.

`parse(parts, kind) -> Parsed`. parts is het pad vanaf de map van de bibliotheek (een lijst, of
een string met `/`), kind het soort bibliotheek: movies, shows, music of videos. Parsed heeft
title, year, season, episode, episode_end, absolute, date, part, edition, extra, ids (imdb, tmdb,
tvdb), de map van de serie (show, show_index) en een zekerheid tussen 0 en 1. Parsed is ook een
Mapping met alleen de gevulde velden (zo toont `app.cli media-parse` hem).

Werkwijze:
1. id's en edities tussen haken eruit (`[imdbid-tt…]`, `{tmdb-603}`, `{edition-…}`), een groep
   vooraan (`[Groep]`) en een site (`www.….com - `);
2. wat op een aflevering lijkt maar het niet is, wordt één ruistoken: resolutie
   (`1920x1080`), codec (`x264`, `H.264`), audio (`5.1`, `DDP5.1`) en een datum wordt één
   token (`2024.03.01`);
3. tokens maken: spaties, underscores en punten scheiden, behalve een punt voor een spatie of
   aan het eind (`Mr. Robot`) en een punt tussen losse letters (`S.W.A.T`);
4. ruis knippen vanaf het eerste ruistoken (resolutie, bron, codec, audio, groep, `REPACK`).
   Een woord dat ook in een titel kan staan (`Web`, `Proper`, `Extended`) is alleen ruis in
   hoofdletters of na een jaar of ander ruis;
5. het jaar: het laatste jaar tussen 1900 en volgend jaar met een titel ervoor (`2001 A Space
   Odyssey (1968)`, `1917 (2019)`, `Blade Runner 2049 (2017)`);
6. alleen in een bibliotheek met series: de aflevering (`S01E02`, `1x02`, `S01E02-E03`, `Season
   1 Episode 2`, `Seizoen 1 Aflevering 2`, `102` en `01` alleen in een seizoensmap, een datum,
   en absolute nummering `[Groep] Titel - 012` met season leeg; nooit een seizoen 1 erbij);
7. zegt de naam weinig (`video.mkv`, `1.mp4`, `DSC_0001.MOV`), dan de map erboven.
"""

from __future__ import annotations

import bisect
import datetime as dt
import functools
import re
import time
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, NamedTuple

from app.media import classify

NAMES_VERSION = 1  # omhoog bij elke verandering die een uitkomst verandert (opnieuw ontleden)
YEAR_MIN = 1900
MAX_INPUT = 1024  # langer is geen naam meer: de rest telt niet mee

# --- de bouwstenen --------------------------------------------------------------------------------

_ID_TAG = re.compile(
    r"[\[{(]\s*(imdb|tmdb|tvdb)(?:id)?\s*[-=: ]\s*(tt\d{5,10}|\d{1,10})\s*[\]})]", re.IGNORECASE
)
_IMDB = re.compile(r"(?<![a-z0-9])(tt\d{7,8})(?![0-9])", re.IGNORECASE)
_EDITION_TAG = re.compile(r"[{\[]\s*edition\s*[-=:]\s*([^{}\[\]]{1,64}?)\s*[}\]]", re.IGNORECASE)
_LEAD_GROUP = re.compile(r"^\s*(?:\[[^\[\]]{0,64}\]\s*)+")
_SITE = re.compile(r"^\s*\[?\s*www\.[a-z0-9.\-]{1,64}\s*\]?\s*(?:-\s*)?", re.IGNORECASE)

_NOISE_MARK = " \x00n "
_DATE = re.compile(
    r"(?<!\d)((?:19|20)\d\d)[.\-_ ](0[1-9]|1[0-2])[.\-_ ](0[1-9]|[12]\d|3[01])(?!\d)"
)
# Audio als 5.1, 7.1, 2.0 (ook in DDP5.1 en AAC2.0: het woord ervoor is zelf ruis). Niet na
# een cijfer of "cijfer punt": Apollo.13.1995 en Ocean's.11.2001 blijven heel
_AUDIO = re.compile(r"(?<!\d\.)(?<!\d)[2-7]\.[01](?![0-9])")
_VCODEC = re.compile(r"(?<![0-9a-zA-Z])[hHxX]\.?26[45](?![0-9])")
# 1920x1080 of 1920 x 1080; niet "1990 x264" (een jaar en een codec)
_RESOLUTION = re.compile(r"(?<!\d)\d{3,4}(?:[xX×]| [xX×] )\d{3,4}(?!\d)")
_DIGIT = re.compile(r"\d")
_DOT_BEFORE_MARK = re.compile(r"\.(?=\s+\x00)")
_SEP_DOT = re.compile(r"\.(?!\s|$)")
# Een punt die misschien blijft: tussen twee losse letters (S.W.A.T) of tussen cijfers (10.000)
_KEEP_DOT = re.compile(
    r"(?<![^\s._\-(\[{])[^\W\d_]\.[^\W\d_](?![^\s._\-)\]}])|\d\.\d"
)  # een letter tussen scheidingen: niet de s van Ocean's.X-Men
_PATH_SPLIT = re.compile(r"[\\/]+")

# Een woord: alles behalve scheidingstekens; een punt voor een spatie, een sluithaak of het eind
# hoort bij het woord ("Mr.")
_WORD = re.compile(r"[^\s_.\[\](){}]+(?:\.(?=[\s\])}]|$))?")
_BRACKETS = re.compile(r"[\[\](){}]")

_STRONG = frozenset(
    """
    480p 576p 720p 1080p 1080i 2160p 4320p 540p 360p 240p 4k 8k uhd fhd qhd fullhd hd720 hd1080
    bluray blu-ray bdrip brrip bdremux bd25 bd50 bdmv webdl web-dl webrip web-rip hdtv hdtvrip
    pdtv sdtv dsr dvdrip dvdscr dvd5 dvd9 dvdr hdrip hddvd tvrip vhsrip hdcam hdts telesync
    telecine screener amzn dsnp hmax atvp pcok nflx x264 x265 h264 h265 hevc avc xvid divx av1
    vp9 10bit 8bit 12bit hdr10 hdr10plus hdr10+ dovi hi10p hi10 sdr aac ac3 eac3 dts dts-hd dtshd
    dts-x truehd atmos ddp dd+ lpcm 2ch 6ch 8ch repack rerip nfofix readnfo dirfix
    """.split()
)
# Woorden die ook in een titel kunnen staan: ruis in hoofdletters, of na een jaar of ander ruis
_AMBIGUOUS = frozenset(
    """
    proper real internal limited complete web cam ts hc dubbed subbed multi multisubs dual
    dualaudio remux hdr dv nl nlsub nlsubs subs ws fs retail uncensored festival hd mp3 flac opus
    dd dc
    """.split()
)
_EDITION_FIRST = frozenset(
    """
    extended unrated uncut theatrical remastered imax criterion special ultimate final directors
    director's directors' collectors collector's anniversary definitive international alternate
    redux dc
    """.split()
)
_EDITION_TAIL = frozenset({"cut", "edition", "version", "editie", "versie"})
_PART = re.compile(r"^(cd|dvd|disc|disk|part|pt)(\d{1,2})$")
_PART_WORD = frozenset({"cd", "dvd", "disc", "disk", "part", "pt"})
_PART_START = frozenset(w[:2] for w in _PART_WORD)
_NUMBER = re.compile(r"^\d{1,4}$")
_ABS_NUMBER = re.compile(r"^(\d{1,4})(?:v\d)?$")

_SEASON_DIR = re.compile(
    r"^(?:season|seizoen|staffel|saison|series|serie|stagione|temporada|s)[\s._\-]*(\d{1,4})"
    r"(?:\s*[\[(].*)?$",
    re.IGNORECASE,
)
_SPECIALS_DIR = re.compile(r"^(?:specials?|season[\s._\-]*specials?)$", re.IGNORECASE)

_SXE = re.compile(r"(?<![a-z0-9])s(\d{1,4}) ?e(\d{1,4})(?:(?:-e?|[ ]?e)(\d{1,4}))*(?![0-9])")
_NXN = re.compile(
    r"(?<![a-z0-9.\x00])(\d{1,2})x(\d{2,3})(?:(?:-(?:\d{1,2}x)?|x)(\d{2,3}))?(?![0-9a-z])"
)
_WORDS = re.compile(
    r"(?<![a-z])(?:season|seizoen|staffel|saison|series|serie)\s?(\d{1,4})\s?(?:-\s?)?"
    r"(?:episode|ep|aflevering|afl|folge|episodio)\s?(\d{1,4})(?:\s?-\s?(\d{1,4}))?(?![0-9])"
)
_EONLY = re.compile(
    r"(?<![a-z0-9])(?:(?:e|ep)(\d{1,4})|(?:episode|aflevering|afl|folge|episodio)\s?(\d{1,4}))"
    r"(?:\s?-\s?(?:e|ep)?(\d{1,4}))?(?![0-9a-z])"
)

_JUNK = re.compile(
    r"^(?:(?:video|movie|film|clip|vid|mov|title|track|untitled|new|unknown|sample|main|feature"
    r"|dvd|disc|cd|part|episode|aflevering|bonus)[\s_\-]*)?\d*$",
    re.IGNORECASE,
)
_CAMERA = re.compile(
    r"^(?:dsc|dscf|dscn|img|imgp|mvi|mvc|gopr|gp|gh|pxl|vid|mov|clip|dji|wp|sam|mah|hdv|m2u|vts"
    r"|screen[\s_\-]?recording|video)[\s_\-]*\d{2,}",
    re.IGNORECASE,
)
_ARTICLES = re.compile(r"^(?:the|a|an|de|het|een|le|la|les|l'|der|die|das|el|los|il)\s+", re.I)
_PUNCT = re.compile(r"[^\w\s]+")
_SPACES = re.compile(r"\s+")

# Muziek
_DISC_DIR = re.compile(r"^(?:disc|disk|cd)[\s._\-]*(\d{1,2})(?:\s.*)?$", re.IGNORECASE)
_TRACK = re.compile(r"^\s*(?:(\d{1,2})[-.](\d{1,3})|(\d{1,3}))(?:\s*[-.]\s+|\s*-\s*|\s+|_+)(?=\S)")
_ALBUM_YEAR = re.compile(r"^(.*?)[\s._\-]*[\[(]((?:19|20)\d\d)[\])]\s*$")
_YEAR_ALBUM = re.compile(r"^((?:19|20)\d\d)\s*-\s*(.+)$")
_COMPACT_DATE = re.compile(r"(?<!\d)((?:19|20)\d\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")


# --- de uitkomst ----------------------------------------------------------------------------------


@dataclass(slots=True)
class Parsed(Mapping[str, Any]):
    """Wat de parser in een pad ziet. Als Mapping: alleen de gevulde velden, met een datum als
    ISO-tekst (zo kan het zo in JSON). Niet frozen: dat maakt elk object twee keer zo duur."""

    title: str
    kind: str = "movies"
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    absolute: int | None = None
    date: dt.date | None = None
    part: int | None = None
    edition: str | None = None
    extra: str | None = None
    ids: dict[str, str] = field(default_factory=dict)
    # De serie: de titel, de map (index in parts, None als de aflevering los in de bibliotheek
    # staat) en wat de map nog zegt
    show: str | None = None
    show_index: int | None = None
    show_year: int | None = None
    show_ids: dict[str, str] = field(default_factory=dict)
    episode_title: str | None = None
    # Muziek (de terugval als de tags niets zeggen)
    artist: str | None = None
    album: str | None = None
    disc: int | None = None
    track: int | None = None
    confidence: float = 0.0

    @property
    def is_episode(self) -> bool:
        """Een aflevering: een nummer, een absoluut nummer of een datum (alleen bij series)."""
        if self.kind != "shows":
            return False
        return self.episode is not None or self.absolute is not None or self.date is not None

    def _value(self, name: str) -> Any:
        value = getattr(self, name)
        return value.isoformat() if isinstance(value, dt.date) else value

    def _filled(self) -> list[str]:
        return [
            f.name
            for f in fields(self)
            if f.name != "kind" and getattr(self, f.name) not in (None, {}, "")
        ]

    def __getitem__(self, key: str) -> Any:
        if key not in self._filled():
            raise KeyError(key)
        return self._value(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._filled())

    def __len__(self) -> int:
        return len(self._filled())


# --- tokens ---------------------------------------------------------------------------------------


class _Tok(NamedTuple):
    """Eén woord van de naam (een tuple: tienduizenden per seconde maken moet goedkoop)."""

    text: str
    low: str
    core: str  # zonder leestekens eromheen, om met de lijsten te vergelijken
    start: int
    end: int
    depth: int  # binnen hoeveel haken
    strong: bool  # zeker ruis
    year: int | None  # een getal van vier cijfers


@dataclass(slots=True)
class _Pre:
    text: str  # de naam na het voorwerk
    tokens: list[_Tok]
    ids: dict[str, str]
    edition: str | None
    group: bool  # er stond een groep vooraan ([Groep])


def _date_mark(match: re.Match[str]) -> str:
    """Een geldige datum wordt één datumtoken (2021-02-29 blijft gewoon tekst)."""
    y, m, d = match.group(1), match.group(2), match.group(3)
    try:
        dt.date(int(y), int(m), int(d))
    except ValueError:
        return match.group()
    return f" \x00d{y}{m}{d} "


def _prepare(stem: str) -> _Pre:
    """Stap 1 tot en met 3: id's, editie en groep eruit, ruis die op een aflevering lijkt als
    één token, en de tokens. Elke stap alleen als er iets voor te vinden is (snel genoeg voor
    10.000 namen in minder dan 0,5 s)."""
    text = stem[:MAX_INPUT]
    low = text.lower()
    ids: dict[str, str] = {}
    if "db" in low:
        for found in _ID_TAG.finditer(text):
            ids.setdefault(found.group(1).lower(), found.group(2).lower())
        text = _ID_TAG.sub(" ", text)
    if "tt" in low:
        for found in _IMDB.finditer(text):
            ids.setdefault("imdb", found.group(1).lower())
        text = _IMDB.sub(" ", text)
    edition = None
    if "edition" in low:
        found = _EDITION_TAG.search(text)
        if found is not None:
            edition = _SPACES.sub(" ", found.group(1)).strip() or None
            text = _EDITION_TAG.sub(" ", text)
    if "www." in low:
        text = _SITE.sub("", text)
    group = False
    if "[" in text:
        lead = _LEAD_GROUP.match(text)
        group = lead is not None and lead.end() < len(text)
        if group and lead is not None:
            text = text[lead.end() :]
    if _DIGIT.search(text):
        if "19" in text or "20" in text:
            text = _DATE.sub(_date_mark, text)
        if "x" in text or "X" in text or "×" in text:
            text = _RESOLUTION.sub(_NOISE_MARK, text)
        if "26" in text:
            text = _VCODEC.sub(_NOISE_MARK, text)
        if "." in text:
            text = _AUDIO.sub(_NOISE_MARK, text)
        if "\x00" in text:
            text = _DOT_BEFORE_MARK.sub(" ", text)  # "Film.1920x1080": de punt was een scheiding
    return _Pre(text, _tokens(text), ids, edition, group)


_STRIP = ".,;:!?'\""
_OPEN = frozenset("[({")
_NEW_TOK = tuple.__new__


def _tokens(text: str) -> list[_Tok]:
    out: list[_Tok] = []
    append = out.append
    brackets: list[tuple[int, str]] = []
    if "(" in text or "[" in text or "{" in text or ")" in text or "]" in text or "}" in text:
        brackets = [(m.start(), m.group()) for m in _BRACKETS.finditer(text)]
    k, depth, n = 0, 0, len(brackets)
    pos = 0
    find = text.index
    strong_words = _STRONG
    # findall en index: sneller dan finditer. Tussen twee woorden staan alleen scheidingstekens,
    # en een woord begint nooit met een scheidingsteken: index vindt dus precies het begin.
    for word in _WORD.findall(text):
        start = find(word, pos)
        pos = start + len(word)
        while k < n and brackets[k][0] < start:
            depth = max(depth + (1 if brackets[k][1] in _OPEN else -1), 0)
            k += 1
        low = word.lower()
        core = low.strip(_STRIP)
        year = int(core) if len(core) == 4 and core.isdigit() else None
        strong = (
            core in strong_words
            or core.startswith("\x00n")
            or (("-" in core or depth > 0) and _strong(core, depth))
        )
        append(_NEW_TOK(_Tok, (word, low, core, start, pos, depth, strong, year)))
    return out


def _strong(core: str, depth: int) -> bool:
    """Zeker ruis, naast de lijst: x264-GROEP, WEB-DL, of een controlegetal tussen haken."""
    if "-" in core:
        head = core.split("-", 1)[0]
        if head in _STRONG or head.startswith("\x00"):
            return True  # x264-GROEP, WEB-DL
    return depth > 0 and _crc(core)


def _crc(low: str) -> bool:
    """Een controlegetal tussen haken bij anime: [ABCD1234]."""
    return len(low) == 8 and all(c in "0123456789abcdef" for c in low) and not low.isdigit()


def _letters(text: str) -> bool:
    return any(c.isalpha() for c in text)


def _noise_cut(toks: list[_Tok], years: Mapping[int, int], begin: int = 1) -> int:
    """De index van het eerste ruistoken vanaf begin (len als er geen is)."""
    for i in range(begin, len(toks)):
        tok = toks[i]
        if tok.strong:
            return i
        low, text = tok.core, tok.text
        if "-" in low and not low.startswith("-"):
            low, text = low.split("-", 1)[0], text.split("-", 1)[0]  # REMUX-GROEP
        if low in _AMBIGUOUS or low in _EDITION_FIRST:
            upper = text.isupper() and len(low) > 1
            if upper or (i - 1) in years:
                return i
    return len(toks)


def _clean(text: str) -> str:
    """Een stuk van de naam als titel: scheidingspunten en underscores worden spaties, een punt
    tussen losse letters blijft (S.W.A.T), haken die niet sluiten gaan weg."""

    spaced = " " in text

    def dot(m: re.Match[str]) -> str:
        i = m.start()
        if spaced and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit():
            return "."  # 10.000 BC: met spaties in de naam hoort de punt bij het getal
        before = i >= 1 and text[i - 1].isalpha() and (i < 2 or text[i - 2] in " ._-([{")
        after = (
            i + 1 < len(text)
            and text[i + 1].isalpha()
            and (i + 2 >= len(text) or text[i + 2] in " ._-)]}")
        )
        return "." if before and after else " "

    if "." not in text:
        out = text
    elif _KEEP_DOT.search(text):
        out = _SEP_DOT.sub(dot, text)
    else:
        out = _SEP_DOT.sub(" ", text)
    out = out.replace("_", " ")
    out = _SPACES.sub(" ", out).strip()
    if out.count("(") != out.count(")") or out.count("[") != out.count("]"):
        out = _SPACES.sub(" ", _BRACKETS.sub(" ", out)).strip()
    out = out.strip(" -–,;:")
    return out


def _span(pre: _Pre, a: int, b: int) -> str:
    """De titel van token a tot (niet met) b, met de leestekens en haken van de naam."""
    toks = pre.tokens
    while a < b and toks[a].core in ("-", ""):
        a += 1
    while b > a and toks[b - 1].core in ("-", ""):
        b -= 1
    if a >= b:
        return ""
    text = pre.text
    start, end = toks[a].start, toks[b - 1].end
    while start > 0 and text[start - 1] in "([{" and toks[a].depth > 0:
        start -= 1
    opened = text.count("(", start, end) - text.count(")", start, end)
    while opened > 0 and end < len(text) and text[end] == ")":
        end += 1
        opened -= 1
    return _clean(text[start:end])


def _years(toks: list[_Tok], top: int) -> dict[int, int]:
    """De tokens die een jaar kunnen zijn (index: jaar), van 1900 tot en met volgend jaar."""
    return {
        i: t.year for i, t in enumerate(toks) if t.year is not None and YEAR_MIN <= t.year <= top
    }


def _pick_year(years: dict[int, int], limit: int, toks: list[_Tok]) -> int | None:
    """Het laatste jaar voor limit met een titel ervoor (een token met letters of cijfers)."""
    if not years:
        return None
    first = next((j for j, t in enumerate(toks) if t.core not in ("-", "")), len(toks))
    best = None
    for i in years:  # oplopend: een dict houdt de volgorde van de tokens
        if first < i < limit:
            best = i
    return best


def _part(toks: list[_Tok], year_i: int | None, cut: int) -> tuple[int | None, int, int] | None:
    """Een deel (cd1, part 2, pt.2, disc 1): (nummer, eerste token, na het laatste). part en pt
    alleen na het jaar of als laatste voor de ruis (Harry Potter … Part 1 (2010) is een titel)."""
    for i in range(1, min(cut, len(toks))):
        low = toks[i].core
        if low[:2] not in _PART_START:
            continue
        found = _PART.match(low)
        if found is not None:
            word, number, end = found.group(1), int(found.group(2)), i + 1
        elif low in _PART_WORD and i + 1 < len(toks) and _NUMBER.match(toks[i + 1].core):
            word, number, end = low, int(toks[i + 1].core), i + 2
            if number > 99:
                continue
        else:
            continue
        if word == "part":
            after_year = year_i is not None and i > year_i
            last = all(t.core in ("-", "") for t in toks[end:cut])
            if not (after_year or (last and (year_i is None or i > year_i))):
                continue
        return number, i, end
    return None


def _edition(pre: _Pre, start: int) -> tuple[str | None, int | None]:
    """Een editie na de titel: "- label" (Jellyfin) of woorden als Extended, Directors Cut.
    Geeft (editie, het token waar hij begint)."""
    toks = pre.tokens
    i = start
    if i < len(toks) and toks[i].core == "-":
        j = i + 1
        end = j
        while end < len(toks) and not toks[end].strong and toks[end].core != "-":
            if _PART.match(toks[end].core) or toks[end].core in _PART_WORD:
                break
            end += 1
        label = _span(pre, j, end)
        if label and _letters(label):
            return label[:64], i
    for i in range(start, len(toks)):
        tok = toks[i]
        if tok.strong:
            break
        if tok.core in _EDITION_FIRST:
            end = i + 1
            while end < len(toks) and toks[end].core in _EDITION_FIRST | _EDITION_TAIL:
                end += 1
            return _span(pre, i, end)[:64] or None, i
    return None, None


def _split(parts: str | Sequence[str]) -> list[str]:
    if isinstance(parts, str):
        raw = _PATH_SPLIT.split(parts)
    else:
        raw = list(parts)
    return [p for p in (str(x).strip() for x in raw) if p]


def _junk(title: str, year: int | None = None) -> bool:
    """Zegt deze titel weinig (video, 1, DSC_0001)? Met een jaar erbij is ook "1917" of "Film"
    een titel."""
    if not title:
        return True
    if year is not None:
        return False
    return not _letters(title) or bool(_JUNK.match(title) or _CAMERA.match(title))


def normalize(title: str) -> str:
    """Voor het groeperen en vergelijken: zonder hoofdletters, accenten en leestekens."""
    text = unicodedata.normalize("NFKD", title.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _SPACES.sub(" ", _PUNCT.sub(" ", text)).strip()


def sort_title(title: str) -> str:
    """De sorteersleutel: genormaliseerd en zonder lidwoord vooraan (The Matrix → matrix)."""
    text = normalize(title)
    stripped = _ARTICLES.sub("", text, count=1)
    return (stripped or text)[:255]


# --- films en video's -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Movie:
    title: str
    year: int | None
    ids: dict[str, str]
    edition: str | None
    part: int | None


_TOP: list[float] = [0.0, 0.0]  # tot wanneer (monotonic), en het jaar


def _top() -> int:
    """Het laatste jaar dat een jaar kan zijn: volgend jaar. Een minuut onthouden: de datum
    opvragen kost meer dan een woord ontleden."""
    now = time.monotonic()
    if now >= _TOP[0]:
        _TOP[0], _TOP[1] = now + 60.0, float(dt.date.today().year + 1)
    return int(_TOP[1])


@functools.lru_cache(maxsize=4096)
def _movie_core(stem: str, top: int) -> _Movie:
    """Titel, jaar, id's, editie en deel van één naam (een bestand of een map). Onthouden: de
    scanner vraagt dezelfde map voor elk bestand erin."""
    pre = _prepare(stem)
    toks = pre.tokens
    years = _years(toks, top)
    cut = _noise_cut(toks, years)
    year_i = _pick_year(years, cut, toks)
    end = cut if year_i is None else year_i
    part = _part(toks, year_i, cut)
    part_n = None
    if part is not None:
        part_n = part[0]
        end = min(end, part[1])
    dates = []
    if "\x00d" in pre.text:
        dates = [i for i, t in enumerate(toks[:end]) if t.low.startswith("\x00d") and i > 0]
    year = years.get(year_i) if year_i is not None else None
    if dates:
        end = min(end, dates[0])
        if year is None:
            year = int(toks[dates[0]].low[2:6])
    edition = pre.edition
    if edition is None:
        edition, at = _edition(pre, end if year_i is None else year_i + 1)
        if at is not None and year_i is None and part is None:
            end = min(end, at)
    title = _span(pre, 0, end)
    return _Movie(title, year, pre.ids, edition, part_n)


def _movie_dir(dirs: list[str]) -> int | None:
    """De map van de film: de map erboven, of die daarboven bij een map met extra's."""
    i = len(dirs) - 1
    while i >= 0 and (classify.extra_dir(dirs[i]) is not None):
        i -= 1
    return i if i >= 0 else None


def _movie(stem: str, dirs: list[str], kind: str, extra: str | None) -> Parsed:
    own = _movie_core(stem, _top())
    title, year, ids = own.title, own.year, dict(own.ids)
    confidence = 0.5
    folder_i = _movie_dir(dirs)
    folder = _movie_core(dirs[folder_i], _top()) if folder_i is not None else None
    if folder is not None and _junk(folder.title, folder.year):
        folder = None
    if _junk(title, year):
        if folder is not None:
            title = folder.title
            year = year or folder.year
            ids = {**folder.ids, **ids}
            confidence = 0.6
        else:
            title = title or _clean(stem) or stem
            confidence = 0.1
    elif folder is not None and normalize(folder.title) in (normalize(title), ""):
        year = year or folder.year
        ids = {**folder.ids, **ids}
    if year is not None and confidence >= 0.5:
        confidence = max(confidence, 0.8)
    if ids:
        confidence = 0.95
    return Parsed(
        title=title[:255],
        kind=kind,
        year=year,
        part=own.part,
        edition=own.edition,
        extra=extra,
        ids=ids,
        confidence=confidence,
    )


def _video(stem: str, dirs: list[str], extra: str | None) -> Parsed:
    """Eigen video's (Keuze 4): de naam zoals hij is, en een datum als die erin staat."""
    title = stem.strip()[:255] or stem[:255]
    date = None
    found = _DATE.search(stem) or _COMPACT_DATE.search(stem)
    if found is not None:
        try:
            date = dt.date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
        except ValueError:
            date = None
    year = date.year if date else None
    if year is None:
        pre = _prepare(stem)
        top = _top()
        years = _years(pre.tokens, top)
        year_i = _pick_year(years, len(pre.tokens), pre.tokens)
        year = years.get(year_i) if year_i is not None else None
    return Parsed(
        title=title[:255], kind="videos", year=year, date=date, extra=extra, confidence=0.5
    )


# --- series ---------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Episode:
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    absolute: int | None = None
    date: dt.date | None = None
    start: int = 0  # het eerste token van de aanduiding
    end: int = 0  # na het laatste
    confidence: float = 0.0


def _season_dir(name: str) -> int | None:
    if _SPECIALS_DIR.match(name.strip()):
        return 0
    found = _SEASON_DIR.match(name.strip())
    return int(found.group(1)) if found is not None else None


def _joined(toks: list[_Tok]) -> tuple[str, list[int]]:
    offsets: list[int] = []
    pos = 0
    for tok in toks:
        offsets.append(pos)
        pos += len(tok.low) + 1
    return " ".join(t.low for t in toks), offsets


def _range(offsets: list[int], start: int, end: int) -> tuple[int, int]:
    first = bisect.bisect_right(offsets, start) - 1
    last = bisect.bisect_right(offsets, max(start, end - 1)) - 1
    return first, last + 1


def _end_of(first: int, last: int | None) -> int | None:
    return last if last is not None and last > first else None


def _find_episode(pre: _Pre, folder_season: int | None, cut: int) -> _Episode | None:
    """Stap 6: de aanduiding van de aflevering, de sterkste eerst."""
    toks = pre.tokens
    if not toks:
        return None
    joined, offsets = _joined(toks)

    found = _SXE.search(joined)
    if found is not None:
        a, b = _range(offsets, found.start(), found.end())
        first = int(found.group(2))
        last = int(found.group(3)) if found.group(3) else None
        return _Episode(int(found.group(1)), first, _end_of(first, last), start=a, end=b,
                        confidence=0.9)  # fmt: skip
    found = _NXN.search(joined)
    if found is not None:
        a, b = _range(offsets, found.start(), found.end())
        first = int(found.group(2))
        last = int(found.group(3)) if found.group(3) else None
        return _Episode(int(found.group(1)), first, _end_of(first, last), start=a, end=b,
                        confidence=0.85)  # fmt: skip
    found = _WORDS.search(joined)
    if found is not None:
        a, b = _range(offsets, found.start(), found.end())
        first = int(found.group(2))
        last = int(found.group(3)) if found.group(3) else None
        return _Episode(int(found.group(1)), first, _end_of(first, last), start=a, end=b,
                        confidence=0.85)  # fmt: skip
    for i, tok in enumerate(toks):
        if tok.low.startswith("\x00d") and i > 0:
            raw = tok.low[2:]
            try:
                day = dt.date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            except ValueError:
                continue
            return _Episode(date=day, start=i, end=i + 1, confidence=0.8)
    found = _EONLY.search(joined)
    if found is not None:
        a, b = _range(offsets, found.start(), found.end())
        first = int(found.group(1) or found.group(2))
        last = int(found.group(3)) if found.group(3) else None
        if folder_season is not None:
            return _Episode(folder_season, first, _end_of(first, last), start=a, end=b,
                            confidence=0.6)  # fmt: skip
        return _Episode(absolute=first, start=a, end=b, confidence=0.6)
    if folder_season is not None:
        return _in_season_dir(toks, folder_season, cut)
    return _absolute(pre, cut)


def _in_season_dir(toks: list[_Tok], season: int, cut: int) -> _Episode | None:
    """In een seizoensmap: 102 (seizoen 1, aflevering 2), of een los nummer (01 - Pilot)."""
    top = _top()
    prefix = str(season)
    for i, tok in enumerate(toks[:cut]):
        low = tok.core
        if not (3 <= len(low) <= 4 and low.isdigit()):
            continue
        if len(low) == 4 and YEAR_MIN <= int(low) <= top and season not in (19, 20):
            continue
        if low.startswith(prefix) and len(low) - len(prefix) == 2:
            return _Episode(season, int(low[len(prefix) :]), start=i, end=i + 1, confidence=0.6)
    for i, tok in enumerate(toks[:cut]):
        low = tok.core
        if not (low.isdigit() and len(low) <= 3):
            continue
        after_dash = i > 0 and toks[i - 1].core == "-"
        last = i == min(cut, len(toks)) - 1
        if i == 0 or after_dash or last:
            return _Episode(season, int(low), start=i, end=i + 1, confidence=0.55)
    return None


def _absolute(pre: _Pre, cut: int) -> _Episode | None:
    """Absolute nummering (Keuze 8): "Titel - 012" of "[Groep] Titel 012 [1080p]". season blijft
    leeg; een jaartal is geen nummer."""
    toks = pre.tokens
    top = _top()
    for i in range(1, min(cut, len(toks))):
        found = _ABS_NUMBER.match(toks[i].core)
        if found is None:
            continue
        number = int(found.group(1))
        if len(found.group(1)) == 4 and YEAR_MIN <= number <= top:
            continue
        after_dash = toks[i - 1].core == "-"
        last = all(t.core in ("-", "") or t.depth > 0 for t in toks[i + 1 : cut])
        if after_dash or (pre.group and last and toks[i].depth == 0):
            return _Episode(absolute=number, start=i, end=i + 1, confidence=0.7)
    return None


def _folder_title(name: str) -> tuple[str, int | None, dict[str, str]]:
    core = _movie_core(name, _top())
    return core.title, core.year, dict(core.ids)


def _show(stem: str, dirs: list[str], extra: str | None) -> Parsed:
    season_i = None
    folder_season = None
    for i in range(len(dirs) - 1, -1, -1):
        season = _season_dir(dirs[i])
        if season is not None:
            season_i, folder_season = i, season
            break
    show_i: int | None
    if season_i is not None:
        show_i = season_i - 1 if season_i >= 1 else None
    else:
        show_i = _movie_dir(dirs)
    if show_i is not None and (_season_dir(dirs[show_i]) is not None):
        show_i = None
    pre = _prepare(stem)
    toks = pre.tokens
    top = _top()
    years = _years(toks, top)
    cut = _noise_cut(toks, years)
    ep = _find_episode(pre, folder_season, cut)
    show_title, show_year, show_ids = None, None, {}
    if show_i is not None:
        show_title, show_year, show_ids = _folder_title(dirs[show_i])
        if _junk(show_title, show_year):
            show_title = None
    if ep is None:
        movie = _movie(stem, dirs, "shows", extra)
        return Parsed(
            title=movie.title,
            kind="shows",
            year=movie.year,
            part=movie.part,
            edition=movie.edition,
            extra=extra,
            ids=movie.ids,
            show=show_title,
            show_index=show_i if show_title else None,
            show_year=show_year,
            show_ids=show_ids,
            confidence=min(movie.confidence, 0.4),
        )
    limit = min(ep.start, cut)
    year_i = _pick_year(years, limit, toks)
    title_end = limit if year_i is None else min(limit, year_i)
    title = _span(pre, 0, title_end)
    year = years.get(year_i) if year_i is not None else None
    after = ep.end
    episode_cut = _noise_cut(toks, years, begin=after) if after < len(toks) else after
    episode_title = _span(pre, after, episode_cut) if after < episode_cut else ""
    if episode_title and not _letters(episode_title):
        episode_title = ""
    if not title or _junk(title, year):
        title = show_title or title
    if show_title is None and title and not _junk(title, year):
        show_title, show_year = title, year
    confidence = ep.confidence if title else ep.confidence - 0.3
    return Parsed(
        title=(title or show_title or "")[:255],
        kind="shows",
        year=year,
        season=ep.season,
        episode=ep.episode,
        episode_end=ep.episode_end,
        absolute=ep.absolute,
        date=ep.date,
        extra=extra,
        ids=dict(pre.ids),
        show=show_title[:255] if show_title else None,
        show_index=show_i if show_i is not None and show_title else None,
        show_year=show_year,
        show_ids=show_ids,
        episode_title=episode_title[:255] or None,
        confidence=round(max(0.0, confidence), 2),
    )


# --- muziek ---------------------------------------------------------------------------------------


def _album(name: str) -> tuple[str, int | None]:
    found = _ALBUM_YEAR.match(name)
    if found is not None and found.group(1).strip():
        return found.group(1).strip(" -_"), int(found.group(2))
    found = _YEAR_ALBUM.match(name)
    if found is not None:
        return found.group(2).strip(), int(found.group(1))
    return name.strip(), None


def _music(stem: str, dirs: list[str]) -> Parsed:
    """De terugval als de tags niets zeggen: Artiest/Album (Jaar)/[Disc 2/]01 - Titel.flac."""
    dirs = list(dirs)
    disc = None
    if dirs:
        found = _DISC_DIR.match(dirs[-1].strip())
        if found is not None:
            disc = int(found.group(1))
            dirs = dirs[:-1]
    album, year = _album(dirs[-1]) if dirs else (None, None)
    artist = dirs[-2].strip() if len(dirs) >= 2 else None
    text = _SPACES.sub(" ", stem[:MAX_INPUT].replace("_", " ")).strip()
    track = None
    found = _TRACK.match(text)
    if found is not None:
        if found.group(1) is not None:
            disc = disc or int(found.group(1))
            track = int(found.group(2))
        else:
            track = int(found.group(3))
        text = text[found.end() :]
    if " - " in text:
        head, rest = text.split(" - ", 1)
        if artist is None or normalize(head) == normalize(artist):
            artist = artist or head.strip()
            text = rest
    title = text.strip(" -") or stem[:255]
    return Parsed(
        title=title[:255],
        kind="music",
        year=year,
        artist=artist[:255] if artist else None,
        album=album[:255] if album else None,
        disc=disc,
        track=track,
        confidence=0.5 if track is not None else 0.3,
    )


# --- de ingang ------------------------------------------------------------------------------------


def parse(parts: str | Sequence[str], kind: str = "movies") -> Parsed:
    """Wat de parser ziet in dit pad (vanaf de map van de bibliotheek). Faalt nooit."""
    items = _split(parts)
    if not items:
        return Parsed(title="", kind=kind)
    name, dirs = items[-1], items[:-1]
    stem = classify.stem(name)
    extra = classify.extra_of(items)
    if extra is not None:
        _, stem = classify.extra_suffix(stem)
    if kind == "music":
        return _music(stem, dirs)
    if kind == "videos":
        return _video(stem, dirs, extra)
    if kind == "shows":
        return _show(stem, dirs, extra)
    return _movie(stem, dirs, "movies", extra)
