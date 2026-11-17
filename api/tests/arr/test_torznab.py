"""Torznab: adressen, caps, resultaten en ophalen met grenzen (fase 11, stap 1)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.arr import torznab

pytestmark = pytest.mark.anyio

CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server title="Bron"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep,imdbid"/>
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>
    <music-search available="no" supportedParams="q"/>
  </searching>
  <categories>
    <category id="2000" name="Movies"><subcat id="2040" name="Movies/HD"/></category>
    <category id="5000" name="TV"/>
  </categories>
</caps>"""

RESULTS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
<channel>
  <title>Bron</title>
  <item>
    <title>Inception.2010.1080p.BluRay.x264-SPARKS</title>
    <guid>https://bron.example/t/1</guid>
    <link>https://bron.example/dl/1.torrent</link>
    <pubDate>Mon, 16 Nov 2026 10:00:00 +0000</pubDate>
    <size>8500000000</size>
    <enclosure url="https://bron.example/dl/1.torrent" length="8500000000"
      type="application/x-bittorrent"/>
    <torznab:attr name="category" value="2000"/>
    <torznab:attr name="category" value="2040"/>
    <torznab:attr name="seeders" value="120"/>
    <torznab:attr name="peers" value="150"/>
    <torznab:attr name="imdbid" value="1375666"/>
    <torznab:attr name="tmdbid" value="27205"/>
    <torznab:attr name="infohash" value="ABCDEF0123456789ABCDEF0123456789ABCDEF01"/>
  </item>
  <item>
    <title>Inception.2010.720p.WEB-DL</title>
    <guid>2</guid>
    <link>magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef02&amp;dn=x</link>
    <torznab:attr name="size" value="3000000000"/>
    <torznab:attr name="seeders" value="5"/>
    <torznab:attr name="infohash" value="geen-hash"/>
  </item>
  <item><title></title><guid>leeg</guid></item>
</channel>
</rss>"""


def test_urls() -> None:
    url = torznab.search_url(
        "https://bron.example/torznab/",
        "GEHEIM",
        kind="tv",
        query="Fargo",
        season=2,
        episode=3,
        categories=torznab.SHOWS,
    )
    parts = urlsplit(url)
    assert parts.path == "/torznab/api"
    params = parse_qs(parts.query)
    assert params["t"] == ["tvsearch"] and params["season"] == ["2"] and params["ep"] == ["3"]
    assert params["cat"] == ["5000"] and params["apikey"] == ["GEHEIM"]
    movie = torznab.search_url("https://b.example/api", "k", kind="movie", imdb="tt1375666")
    assert parse_qs(urlsplit(movie).query)["imdbid"] == ["1375666"]
    assert "t=caps" in torznab.caps_url("http://192.168.10.5:9117/x?y=1", "k")
    with pytest.raises(torznab.TorznabError):
        torznab.caps_url("file:///etc/passwd", "k")


def test_caps() -> None:
    caps = torznab.parse_caps(CAPS)
    assert caps.supports("movie-search", "tmdbid")
    assert caps.supports("tv-search", "season")
    assert not caps.supports("music-search")
    assert caps.categories[2040] == "Movies/HD"


def test_results() -> None:
    first, second = torznab.parse_results(RESULTS)
    assert first.seeders == 120 and first.peers == 150 and first.size == 8_500_000_000
    assert first.imdb == "tt1375666" and first.tmdb == 27205
    assert first.infohash == "abcdef0123456789abcdef0123456789abcdef01"
    assert first.categories == (2000, 2040) and first.published.year == 2026
    assert first.link == "https://bron.example/dl/1.torrent" and first.magnet is None
    assert second.magnet.startswith("magnet:") and second.link is None
    assert second.infohash is None and second.size == 3_000_000_000


@pytest.mark.parametrize(
    "body",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel/></rss>',
        b"geen xml",
        b'<error code="100" description="Incorrect user credentials"/>',
        b"<rss/>",
    ],
)
def test_bad_answers(body: bytes) -> None:
    with pytest.raises(torznab.TorznabError):
        torznab.parse_results(body)


def test_wrong_key_message_has_no_secret() -> None:
    with pytest.raises(torznab.TorznabError, match="sleutel") as info:
        torznab.parse_results(b'<error code="100" description="GEHEIM"/>')
    assert "GEHEIM" not in str(info.value)


async def test_fetch_limits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/groot":
            return httpx.Response(200, content=b"x" * (torznab.MAX_BODY + 1))
        if request.url.path == "/weg":
            return httpx.Response(302, headers={"location": "file:///etc/passwd"})
        if request.url.path == "/sleutel":
            return httpx.Response(401)
        return httpx.Response(200, content=RESULTS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await torznab.fetch(client, "https://b.example/ok?apikey=GEHEIM") == RESULTS
        with pytest.raises(torznab.TorznabError, match="te groot"):
            await torznab.fetch(client, "https://b.example/groot")
        with pytest.raises(torznab.TorznabError, match="302"):
            await torznab.fetch(client, "https://b.example/weg")
        with pytest.raises(torznab.TorznabError, match="sleutel") as info:
            await torznab.fetch(client, "https://b.example/sleutel?apikey=GEHEIM")
        assert "GEHEIM" not in str(info.value)


async def test_fetch_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("geweigerd: apikey=GEHEIM")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(torznab.TorznabError, match="niet bereikbaar") as info:
            await torznab.fetch(client, "https://b.example/?apikey=GEHEIM")
    assert "GEHEIM" not in str(info.value)
