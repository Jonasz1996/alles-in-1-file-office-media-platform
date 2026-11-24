"""Torrentbronnen via de open standaard Torznab (een Newznab-API met torrent-attributen).

- `search_url(base, api_key, query)` maakt het adres voor `t=search`, `t=movie` of `t=tvsearch`
  (met `imdbid`, `tmdbid`, `season`, `ep` als de bron die kent), en `caps_url` voor `t=caps`.
- `parse_caps(body)` leest wat de bron kan: de zoeksoorten met hun parameters en de categorieën.
- `parse_results(body)` leest een RSS-antwoord tot `Result`s: titel, guid, link of magnet, grootte,
  seeders, peers, infohash, imdb en tmdb, de publicatiedatum en de categorieën.
- `fetch(client, url)` haalt een antwoord op met grenzen: hooguit 4 MB, 20 s, geen omleiding naar
  een ander schema dan http(s). Het adres van de bron stelt de beheerder zelf in op `/beheer`;
  een bron in het eigen netwerk mag dus. De sleutel komt nooit in de log of in een fout.

XML: een antwoord met een DOCTYPE of ENTITY weigeren we (geen entity-uitbreiding), en Python's
expat leest nooit externe bestanden. Een kapot antwoord geeft `TorznabError` met een vaste reden.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import httpx

MAX_BODY = 4 * 1024 * 1024
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
NEWZNAB_NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"

# Categorieën van de standaard (Newznab): 2000 films, 5000 tv
MOVIES = (2000,)
SHOWS = (5000,)

_BAD_XML = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_HASH = re.compile(r"^[0-9a-fA-F]{40}$")


class TorznabError(Exception):
    """Een vaste reden, nooit tekst van de bron of de sleutel."""


@dataclass(frozen=True)
class Caps:
    searching: Mapping[str, tuple[str, ...]]  # "search", "movie-search", "tv-search" -> params
    categories: Mapping[int, str]

    def supports(self, kind: str, param: str | None = None) -> bool:
        params = self.searching.get(kind)
        if params is None:
            return False
        return param is None or param in params


@dataclass(frozen=True)
class Result:
    title: str
    guid: str
    link: str | None = None  # .torrent-URL
    magnet: str | None = None
    size: int = 0
    seeders: int = 0
    peers: int = 0
    infohash: str | None = None
    imdb: str | None = None  # "tt0133093"
    tmdb: int | None = None
    published: dt.datetime | None = None
    categories: tuple[int, ...] = field(default=())


def _check_base(base: str) -> str:
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise TorznabError("ongeldig adres van de bron")
    return base.rstrip("/")


def _url(base: str, params: dict[str, str]) -> str:
    base = _check_base(base)
    joiner = "&" if "?" in base else "?"
    if "?" not in base and not base.endswith("/api"):
        base += "/api"
    return base + joiner + urlencode(params)


def caps_url(base: str, api_key: str) -> str:
    return _url(base, {"t": "caps", "apikey": api_key})


def search_url(
    base: str,
    api_key: str,
    *,
    kind: str = "search",
    query: str | None = None,
    imdb: str | None = None,
    tmdb: int | None = None,
    season: int | None = None,
    episode: int | None = None,
    categories: tuple[int, ...] = (),
    limit: int = 100,
) -> str:
    t = {"search": "search", "movie": "movie", "tv": "tvsearch"}[kind]
    params = {"t": t, "apikey": api_key, "extended": "1", "limit": str(limit)}
    if query:
        params["q"] = query
    if imdb:
        params["imdbid"] = imdb.removeprefix("tt")
    if tmdb is not None:
        params["tmdbid"] = str(tmdb)
    if season is not None:
        params["season"] = str(season)
    if episode is not None:
        params["ep"] = str(episode)
    if categories:
        params["cat"] = ",".join(str(c) for c in categories)
    return _url(base, params)


def _root(body: bytes) -> ET.Element:
    if len(body) > MAX_BODY:
        raise TorznabError("antwoord te groot")
    if _BAD_XML.search(body):
        raise TorznabError("antwoord met DOCTYPE")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise TorznabError("geen geldige XML") from exc
    if root.tag == "error":
        code = root.get("code", "")
        if code in ("100", "101", "102"):
            raise TorznabError("de bron weigert de sleutel")
        raise TorznabError("de bron geeft een fout")
    return root


def parse_caps(body: bytes) -> Caps:
    root = _root(body)
    searching: dict[str, tuple[str, ...]] = {}
    node = root.find("searching")
    if node is not None:
        for child in node:
            if child.get("available", "no").lower() == "yes":
                params = tuple(
                    p.strip().lower() for p in child.get("supportedParams", "q").split(",") if p
                )
                searching[child.tag] = params
    categories: dict[int, str] = {}
    cats = root.find("categories")
    if cats is not None:
        for cat in cats.iter("category"):
            try:
                categories[int(cat.get("id", ""))] = cat.get("name", "")
            except ValueError:
                continue
            for sub in cat.iter("subcat"):
                try:
                    categories[int(sub.get("id", ""))] = sub.get("name", "")
                except ValueError:
                    continue
    return Caps(searching, categories)


def _int(text: str | None) -> int | None:
    try:
        return int(float(text)) if text not in (None, "") else None
    except ValueError:
        return None


def parse_results(body: bytes) -> list[Result]:
    root = _root(body)
    channel = root.find("channel")
    if channel is None:
        raise TorznabError("geen RSS-kanaal")
    results: list[Result] = []
    for item in channel.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        attrs: dict[str, list[str]] = {}
        for ns in (TORZNAB_NS, NEWZNAB_NS):
            for attr in item.iter(f"{{{ns}}}attr"):
                attrs.setdefault(attr.get("name", "").lower(), []).append(attr.get("value", ""))

        def first(name: str, attrs: dict[str, list[str]] = attrs) -> str | None:
            values = attrs.get(name)
            return values[0] if values else None

        link = (item.findtext("link") or "").strip() or None
        enclosure = item.find("enclosure")
        if enclosure is not None and enclosure.get("url"):
            link = enclosure.get("url")
        magnet = first("magneturl")
        if link and link.startswith("magnet:"):
            magnet, link = magnet or link, None
        size = _int(first("size")) or _int(item.findtext("size"))
        if not size and enclosure is not None:
            size = _int(enclosure.get("length"))
        infohash = first("infohash")
        if infohash and not _HASH.match(infohash):
            infohash = None
        imdb = first("imdbid") or first("imdb")
        if imdb:
            digits = imdb.removeprefix("tt")
            imdb = f"tt{int(digits):07d}" if digits.isdigit() else None
        published = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                published = email.utils.parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                published = None
        categories = tuple(
            c for c in (_int(v) for v in attrs.get("category", [])) if c is not None
        ) or tuple(c for c in (_int(e.text) for e in item.iter("category")) if c is not None)
        seeders = _int(first("seeders")) or 0
        peers = _int(first("peers")) or 0
        results.append(
            Result(
                title=title,
                guid=(item.findtext("guid") or link or magnet or title).strip(),
                link=link,
                magnet=magnet,
                size=size or 0,
                seeders=seeders,
                peers=max(peers, seeders),
                infohash=infohash.lower() if infohash else None,
                imdb=imdb,
                tmdb=_int(first("tmdbid")),
                published=published,
                categories=categories,
            )
        )
    return results


async def fetch(client: httpx.AsyncClient, url: str) -> bytes:
    """Haal een Torznab-antwoord op. Fouten zeggen nooit het adres (de sleutel zit erin)."""
    try:
        async with client.stream("GET", url, timeout=TIMEOUT, follow_redirects=False) as resp:
            if resp.status_code in (401, 403):
                raise TorznabError("de bron weigert de sleutel")
            if resp.status_code == 429:
                raise TorznabError("de bron vraagt om te wachten")
            if resp.status_code != 200:
                raise TorznabError(f"de bron antwoordt {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_BODY:
                raise TorznabError("antwoord te groot")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > MAX_BODY:
                    raise TorznabError("antwoord te groot")
            return bytes(body)
    except httpx.TimeoutException as exc:
        raise TorznabError("de bron antwoordt niet op tijd") from exc
    except httpx.HTTPError as exc:
        raise TorznabError("de bron is niet bereikbaar") from exc
