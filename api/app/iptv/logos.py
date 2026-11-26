"""De logo's van de kanalen, via de app: `/api/v1/iptv/logo/{logo_key}?sig=…`.

Een <img> stuurt geen Authorization-kop, en de web-app laadt geen plaatjes van buiten (CSP).
Daarom een ondertekend adres (HMAC met een sleutel uit het app-geheim, zoals de plaatjes van
TMDB) zonder login. De app haalt het logo één keer op en bewaart het in
data_dir/cache/iptv/logos/<logo_key>; daarna komt het uit de cache. Alleen plaatjes (png, jpeg,
gif, webp, svg als image/svg+xml niet: een svg kan script bevatten), hooguit MAX_BYTES.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path

import httpx

from app.config import Settings

MAX_BYTES = 1024 * 1024
TYPES = {
    b"\x89PNG": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF8": "image/gif",
}


def _key(settings: Settings) -> bytes:
    return hashlib.sha256(b"platform iptv logos v1|" + settings.secret().encode()).digest()


def sign(settings: Settings, logo_key: str) -> str:
    return hmac.new(_key(settings), logo_key.encode(), hashlib.sha256).hexdigest()[:32]


def url(settings: Settings, logo_key: str | None) -> str | None:
    if not logo_key:
        return None
    return f"/api/v1/iptv/logo/{logo_key}?sig={sign(settings, logo_key)}"


def valid(settings: Settings, logo_key: str, sig: str) -> bool:
    return hmac.compare_digest(sign(settings, logo_key), sig)


def sniff(data: bytes) -> str | None:
    for magic, mime in TYPES.items():
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def cache_dir(settings: Settings) -> Path:
    return settings.data_dir / "cache" / "iptv" / "logos"


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, follow_redirects=True, timeout=httpx.Timeout(10.0))


async def fetch(settings: Settings, logo_key: str, source: str) -> tuple[bytes, str] | None:
    """Het logo uit de cache, of opgehaald en bewaard. None als het geen plaatje is."""
    path = cache_dir(settings) / logo_key
    try:
        data = await asyncio.to_thread(path.read_bytes)
        mime = sniff(data)
        if mime:
            return data, mime
    except OSError:
        pass
    try:
        async with http_client() as client, client.stream("GET", source) as res:
            if res.status_code != 200:
                return None
            parts: list[bytes] = []
            size = 0
            async for chunk in res.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    return None
                parts.append(chunk)
    except httpx.HTTPError:
        return None
    data = b"".join(parts)
    mime = sniff(data)
    if mime is None:
        return None

    def save() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)

    try:
        await asyncio.to_thread(save)
    except OSError:
        pass
    return data, mime
