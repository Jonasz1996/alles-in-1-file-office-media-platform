"""nfo: een NFO in de stijl van Kodi lezen (Keuze 9, §3.3). M2. Geen ffmpeg, geen kind.

De bron is ctx.inputs[0]: een gewoon bestand, gelezen met pread vanaf 0 (de positie is gedeeld
met de app), hooguit args.max_bytes (standaard 1 MiB). Groter is too_large.

Wat de lezer doet, in deze volgorde (de inhoud is van een gebruiker, dus onvertrouwd):
1. ontcijferen: een BOM (UTF-8, UTF-16), anders UTF-8, anders de codering uit de XML-kop als
   Python die kent, anders CP1252. Stuurtekens gaan weg;
2. elke `<!DOCTYPE` of `<!ENTITY` (ook in kleine letters) weigeren vóór het parsen. Zonder DTD
   kent expat alleen de vijf vaste entiteiten: geen XXE, geen "billion laughs";
3. de XML-kop eraf en alles in één eigen wortel (`<nfo>…</nfo>`): een NFO met meerdere
   afleveringen heeft meer dan één `<episodedetails>`, en tekst eromheen (een link) mag;
4. XMLPullParser in stukken van 4 KB, met een grens op de diepte (64) en het aantal elementen
   (100.000); daarboven is het kapotte XML;
5. de velden van het eerste bekende element (`movie`, `tvshow`, `episodedetails`, `season`,
   `musicvideo`): title, originaltitle, sorttitle, showtitle, year, plot (hooguit 4000 tekens),
   genre (hooguit 10), uniqueid (tmdb, imdb, tvdb) en de oude tags (`id`, `imdbid`, `tmdbid`,
   `tvdbid`), season, episode, premiered en aired;
6. links naar IMDb, TMDB en TheTVDB, overal in het bestand: een NFO met alleen een link geeft
   zo het id. Een id uit uniqueid gaat voor een link.

Antwoord (ruim onder wire.MAX_MESSAGE: elk veld is begrensd):
    {"root": "movie" | ... | None, "fields": {...}, "episodes": [{"season", "episode",
     "title"}] (alleen bij meer dan één), "error": None | "doctype" | "xml" | "empty"}
Bij een error staan in fields alleen de id's uit links (de scanner valt terug op de naam).
"""

from __future__ import annotations

import asyncio
import codecs
import datetime as dt
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

from app.media.wire import Code
from app.media.worker.args import NfoArgs
from app.media.worker.server import Context, OpError

CHUNK = 4096
MAX_DEPTH = 64
MAX_ELEMENTS = 100_000  # ± 20 MB aan elementen; een NFO van Kodi heeft er een paar honderd
MAX_TEXT = 255
MAX_PLOT = 4000
MAX_GENRES = 10
MAX_GENRE = 64
MAX_EPISODES = 10
YIELD_EVERY = 16  # stukken; dan krijgt de lus van de worker de beurt

KINDS = ("movie", "tvshow", "episodedetails", "season", "musicvideo")
_FORBIDDEN = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_DECL = re.compile(r"^\s*<\?xml[^>]{0,200}\?>", re.IGNORECASE)
_DECL_ENCODING = re.compile(rb"^\s*<\?xml[^>]{0,200}?encoding\s*=\s*[\"']([A-Za-z0-9._\-]{1,40})")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")
_SPACES = re.compile(r"\s+")

_IMDB = re.compile(r"^tt\d{5,10}$")
_NUMBER = re.compile(r"^\d{1,10}$")
_LINKS = (
    ("imdb", re.compile(r"imdb\.com/(?:[a-z]{2}/)?title/(tt\d{5,10})(?![0-9])", re.IGNORECASE)),
    ("tmdb", re.compile(r"themoviedb\.org/(?:movie|tv)/(\d{1,10})(?![0-9])", re.IGNORECASE)),
    (
        "tvdb",
        re.compile(
            r"thetvdb\.com/(?:[^\s<>\"']{0,80}?[?&](?:amp;)?(?:id|seriesid)=|(?:dereferrer/)?"
            r"series/)(\d{1,10})(?![0-9])",
            re.IGNORECASE,
        ),
    ),
)
_OLD_IDS = {"imdbid": "imdb", "imdb": "imdb", "tmdbid": "tmdb", "tmdb": "tmdb", "tvdbid": "tvdb"}


class NfoError(Exception):
    """Kapot of verboden: code is "doctype" of "xml"."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def read_file(fd: int, limit: int) -> bytes | None:
    """Een gewoon bestand vanaf 0 (pread), hooguit limit bytes; None als het groter is."""
    chunks: list[bytes] = []
    offset = 0
    while offset <= limit:
        chunk = os.pread(fd, min(1 << 20, limit + 1 - offset), offset)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)
    return None


def decode(data: bytes) -> str:
    """De tekst van een NFO; faalt nooit (CP1252 met vervanging als laatste)."""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    found = _DECL_ENCODING.match(data[:300])
    if found is not None:
        try:
            name = codecs.lookup(found.group(1).decode("ascii")).name
        except LookupError:
            name = ""
        if name and not name.startswith(("utf-16", "utf-32", "utf-8")):
            try:
                return data.decode(name, "replace")
            except (LookupError, ValueError):
                pass  # een codec die geen tekst geeft (rot13, zlib): niet gebruiken
    return data.decode("cp1252", "replace")


def _text(element: ET.Element | None, limit: int = MAX_TEXT) -> str | None:
    if element is None:
        return None
    text = _SPACES.sub(" ", "".join(element.itertext())).strip()
    return text[:limit] or None


def _plot(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    text = "".join(element.itertext())[: MAX_PLOT * 4]
    lines = (_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)[:MAX_PLOT] or None


def _int(element: ET.Element | None, low: int, high: int) -> int | None:
    text = _text(element, 16)
    if text is None:
        return None
    text = text.lstrip("+")
    if not (text.isascii() and text.isdigit()):
        return None
    value = int(text)
    return value if low <= value <= high else None


def _date(element: ET.Element | None) -> str | None:
    text = _text(element, 32)
    if text is None or len(text) < 10:
        return None
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _id(kind: str, value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if kind == "imdb":
        return value if _IMDB.match(value) else None
    return value if _NUMBER.match(value) and int(value) > 0 else None


def links(text: str) -> dict[str, str]:
    """De id's uit links naar IMDb, TMDB en TheTVDB (de eerste van elk)."""
    out: dict[str, str] = {}
    for kind, pattern in _LINKS:
        found = pattern.search(text)
        if found is not None:
            value = _id(kind, found.group(1))
            if value is not None:
                out[kind] = value
    return out


def _ids(details: ET.Element, kind: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for unique in details.findall("uniqueid"):
        which = (unique.get("type") or "").strip().lower()
        if which in ("imdb", "tmdb", "tvdb"):
            value = _id(which, _text(unique, 32))
            if value is not None:
                out.setdefault(which, value)
    for tag, which in _OLD_IDS.items():
        value = _id(which, _text(details.find(tag), 32))
        if value is not None:
            out.setdefault(which, value)
    old = _text(details.find("id"), 32)
    if old is not None:
        if _id("imdb", old):
            out.setdefault("imdb", old.lower())
        elif _id("tvdb", old) and kind in ("tvshow", "episodedetails"):
            out.setdefault("tvdb", old)  # Kodi: <id> bij een serie is TheTVDB
        elif _id("tmdb", old) and kind == "movie":
            out.setdefault("tmdb", old)
    return out


def _genres(details: ET.Element) -> list[str]:
    out: list[str] = []
    for element in details.findall("genre"):
        text = _text(element, 200)
        if text is None:
            continue
        for part in text.split(" / "):
            part = part.strip()[:MAX_GENRE]
            if part and part not in out:
                out.append(part)
            if len(out) >= MAX_GENRES:
                return out
    return out


def fields(details: ET.Element, kind: str) -> dict[str, Any]:
    """De velden van Keuze 9 uit één element; alleen wat er is."""
    out: dict[str, Any] = {}
    for tag in ("title", "originaltitle", "sorttitle", "showtitle"):
        value = _text(details.find(tag))
        if value is not None:
            out[tag] = value
    plot = _plot(details.find("plot")) or _plot(details.find("outline"))
    if plot is not None:
        out["plot"] = plot
    genres = _genres(details)
    if genres:
        out["genres"] = genres
    premiered = _date(details.find("premiered"))
    aired = _date(details.find("aired"))
    if premiered is not None:
        out["premiered"] = premiered
    if aired is not None:
        out["aired"] = aired
    year = _int(details.find("year"), 1800, 2200)
    if year is None and (premiered or aired):
        year = int((premiered or aired or "0")[:4])
    if year is not None:
        out["year"] = year
    if kind in ("episodedetails", "season"):
        season = _int(details.find("season"), 0, 9999)
        if season is None and kind == "season":
            season = _int(details.find("seasonnumber"), 0, 9999)
        if season is not None:
            out["season"] = season
    if kind == "episodedetails":
        episode = _int(details.find("episode"), 0, 99999)
        if episode is not None:
            out["episode"] = episode
    ids = _ids(details, kind)
    if ids:
        out["ids"] = ids
    return out


class Reader:
    """De XML in stukken (feed), met grenzen op diepte en aantal; close geeft het antwoord."""

    def __init__(self, text: str) -> None:
        self.text = text
        body = _DECL.sub("", text, count=1)
        self.parts = [body[i : i + CHUNK] for i in range(0, len(body), CHUNK)]
        self.parser = ET.XMLPullParser(events=("start", "end"))
        self.depth = 0
        self.elements = 0
        self.root: ET.Element | None = None
        self.feed("<nfo>")

    def _events(self) -> None:
        for event, element in self.parser.read_events():
            if event == "start":
                if self.root is None:
                    self.root = element  # de eigen wortel
                self.depth += 1
                self.elements += 1
                if self.depth > MAX_DEPTH or self.elements > MAX_ELEMENTS:
                    raise NfoError("xml")
            else:
                self.depth -= 1

    def feed(self, part: str) -> None:
        try:
            self.parser.feed(part)
            self._events()
        except ET.ParseError:
            raise NfoError("xml") from None

    def close(self) -> dict[str, Any]:
        self.feed("</nfo>")
        try:
            self.parser.close()
        except ET.ParseError:
            raise NfoError("xml") from None
        root = self.root
        if root is None:
            raise NfoError("xml")
        details = [child for child in root if child.tag in KINDS]
        found = links(self.text)
        if not details:
            return {"root": None, "fields": {"ids": found} if found else {}, "error": None}
        first = details[0]
        out = fields(first, first.tag)
        if found:
            out["ids"] = {**found, **out.get("ids", {})}
        answer: dict[str, Any] = {"root": first.tag, "fields": out, "error": None}
        episodes = [d for d in details if d.tag == "episodedetails"]
        if first.tag == "episodedetails" and len(episodes) > 1:
            answer["episodes"] = [
                {
                    "season": _int(d.find("season"), 0, 9999),
                    "episode": _int(d.find("episode"), 0, 99999),
                    "title": _text(d.find("title"), 200),
                }
                for d in episodes[:MAX_EPISODES]
            ]
        return answer


def _only_links(text: str, error: str | None) -> dict[str, Any]:
    found = links(text)
    return {"root": None, "fields": {"ids": found} if found else {}, "error": error}


def start(data: bytes) -> Reader | dict[str, Any]:
    """Het begin: het antwoord als er niets te parsen is (leeg, alleen een link, DOCTYPE),
    anders een Reader om te voeden."""
    text = _CONTROL.sub("", decode(data))
    if not text.strip():
        return {"root": None, "fields": {}, "error": "empty"}
    if "<" not in text:
        return _only_links(text, None)  # Kodi kent een NFO met alleen een adres
    if _FORBIDDEN.search(text):
        return _only_links(text, "doctype")
    try:
        return Reader(text)
    except NfoError as exc:
        return _only_links(text, exc.code)


def parse(data: bytes) -> dict[str, Any]:
    """Het antwoord van de opdracht voor deze bytes (de grens op de grootte zit in run)."""
    reader = start(data)
    if isinstance(reader, dict):
        return reader
    try:
        for part in reader.parts:
            reader.feed(part)
        return reader.close()
    except NfoError as exc:
        return _only_links(reader.text, exc.code)


async def run(ctx: Context) -> dict:
    args = ctx.args
    assert isinstance(args, NfoArgs)
    data = read_file(ctx.inputs[0], args.max_bytes)
    if data is None:
        raise OpError(Code.TOO_LARGE, f"meer dan {args.max_bytes} bytes")
    reader = start(data)
    if isinstance(reader, dict):
        return reader
    try:
        for i, part in enumerate(reader.parts):
            reader.feed(part)
            if i % YIELD_EVERY == YIELD_EVERY - 1:
                await asyncio.sleep(0)  # 1 MB XML kost ± 20 ms: de andere opdrachten lopen door
        return reader.close()
    except NfoError as exc:
        return _only_links(reader.text, exc.code)
