"""De stroom van de aanbieder lezen, als MPEG-TS-bytes voor de worker.

- Een gewoon adres (Xtream Codes: `…/live/<gebruiker>/<wachtwoord>/<id>.ts`, of zonder .ts):
  de bytes zoals ze komen.
- HLS (`.m3u8`, of een antwoord dat met #EXTM3U begint): de app leest de playlist zelf (bij een
  master de variant met de hoogste BANDWIDTH), haalt de nieuwe stukken op volgorde en geeft hun
  bytes door (TS-stukken plakken gewoon aan elkaar). Begint bij de laatste LIVE_EDGE stukken.
  Versleutelde stukken (#EXT-X-KEY) en fMP4-stukken (#EXT-X-MAP) kan de worker niet lezen:
  dan een duidelijke fout.

Een fout is een UpstreamError met een vaste reden, nooit het adres (daar staan logins in).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

DEFAULT_UA = "VLC/3.0.21 LibVLC/3.0.21"
TIMEOUT = httpx.Timeout(10.0, read=20.0)
PLAYLIST_MAX = 1024 * 1024
LIVE_EDGE = 3
CHUNK = 64 * 1024
_BANDWIDTH = re.compile(r"BANDWIDTH=(\d+)")
_KEY = re.compile(r"METHOD=([A-Z0-9-]+)")


class UpstreamError(Exception):
    """De aanbieder geeft geen stroom (vaste reden)."""


def client(ua: str | None) -> httpx.AsyncClient:
    """De client naar de aanbieder (tests vervangen deze functie)."""
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=True,
        timeout=TIMEOUT,
        headers={"User-Agent": ua or DEFAULT_UA},
    )


def _status(code: int) -> UpstreamError:
    if code in (401, 403):
        return UpstreamError(f"de aanbieder weigert de toegang ({code})")
    if code == 404:
        return UpstreamError("de aanbieder kent dit kanaal niet (404)")
    if code in (429, 456, 458, 509):
        return UpstreamError(f"de aanbieder laat niet meer verbindingen toe ({code})")
    return UpstreamError(f"de aanbieder antwoordt {code}")


def _is_playlist(url: str, content_type: str, head: bytes) -> bool:
    if "mpegurl" in content_type.lower():
        return True
    if url.split("?")[0].lower().endswith(".m3u8"):
        return True
    return head.lstrip()[:7] == b"#EXTM3U"


@dataclass
class MediaPlaylist:
    target: float
    sequence: int
    segments: list[tuple[int, str]]
    ended: bool


def parse_media(text: str, base: str) -> MediaPlaylist:
    target = 6.0
    sequence = 0
    segments: list[tuple[int, str]] = []
    ended = False
    expect = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                target = float(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                sequence = int(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("#EXT-X-KEY:"):
            m = _KEY.search(line)
            if m and m.group(1) != "NONE":
                raise UpstreamError("versleutelde HLS (#EXT-X-KEY) kan het platform niet lezen")
        elif line.startswith("#EXT-X-MAP:"):
            raise UpstreamError("HLS met fMP4-stukken kan het platform nog niet lezen")
        elif line.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif line.startswith("#EXTINF:"):
            expect = True
        elif not line.startswith("#") and expect:
            segments.append((sequence + len(segments), urljoin(base, line)))
            expect = False
    return MediaPlaylist(max(1.0, min(target, 30.0)), sequence, segments, ended)


def pick_variant(text: str, base: str) -> str | None:
    best: tuple[int, str] | None = None
    bandwidth = -1
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            m = _BANDWIDTH.search(line)
            bandwidth = int(m.group(1)) if m else 0
        elif line and not line.startswith("#") and bandwidth >= 0:
            if best is None or bandwidth > best[0]:
                best = (bandwidth, urljoin(base, line))
            bandwidth = -1
    return best[1] if best else None


async def _get_text(http: httpx.AsyncClient, url: str) -> tuple[str, str]:
    try:
        res = await http.get(url)
    except httpx.TimeoutException:
        raise UpstreamError("de aanbieder antwoordt niet op tijd") from None
    except httpx.HTTPError as exc:
        raise UpstreamError(
            f"geen verbinding met de aanbieder ({exc.__class__.__name__})"
        ) from None
    if res.status_code != 200:
        raise _status(res.status_code)
    if len(res.content) > PLAYLIST_MAX:
        raise UpstreamError("de playlist van de aanbieder is te groot")
    return res.text, str(res.url)


async def _hls(http: httpx.AsyncClient, text: str, url: str) -> AsyncIterator[bytes]:
    if "#EXT-X-STREAM-INF" in text:
        variant = pick_variant(text, url)
        if variant is None:
            raise UpstreamError("de HLS-master van de aanbieder heeft geen variant")
        text, url = await _get_text(http, variant)
    last: int | None = None
    while True:
        media = parse_media(text, url)
        fresh = [s for s in media.segments if last is None or s[0] > last]
        if last is None:
            fresh = fresh[-LIVE_EDGE:]
        for seq, seg_url in fresh:
            try:
                async with http.stream("GET", seg_url) as res:
                    if res.status_code != 200:
                        raise _status(res.status_code)
                    async for chunk in res.aiter_bytes(CHUNK):
                        yield chunk
            except httpx.TimeoutException:
                raise UpstreamError("de aanbieder antwoordt niet op tijd") from None
            except httpx.HTTPError as exc:
                raise UpstreamError(
                    f"verbinding met de aanbieder verbroken ({exc.__class__.__name__})"
                ) from None
            last = seq
        if media.ended and not fresh:
            return
        if not fresh:
            await asyncio.sleep(max(1.0, media.target / 2))
        text, url = await _get_text(http, url)


async def read(url: str, ua: str | None) -> AsyncIterator[bytes]:
    """De bytes van het kanaal (MPEG-TS), tot de aanbieder stopt of de lezer weggaat."""
    async with client(ua) as http:
        try:
            async with http.stream("GET", url) as res:
                if res.status_code != 200:
                    raise _status(res.status_code)
                kind = res.headers.get("content-type", "")
                stream = res.aiter_bytes(CHUNK)
                first = b""
                async for chunk in stream:
                    first = chunk
                    break
                if _is_playlist(str(res.url), kind, first):
                    body = bytearray(first)
                    async for chunk in stream:
                        body += chunk
                        if len(body) > PLAYLIST_MAX:
                            raise UpstreamError("de playlist van de aanbieder is te groot")
                    text, base = body.decode("utf-8", "replace"), str(res.url)
                else:
                    if first:
                        yield first
                    async for chunk in stream:
                        yield chunk
                    return
        except httpx.TimeoutException:
            raise UpstreamError("de aanbieder antwoordt niet op tijd") from None
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"verbinding met de aanbieder verbroken ({exc.__class__.__name__})"
            ) from None
        async for chunk in _hls(http, text, base):
            yield chunk
