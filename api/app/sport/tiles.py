"""Kaarttegels onder de route, via de server en met een cache op schijf.

Standaard staat dit uit: de kaart toont dan alleen de route op een raster, en niets gaat naar
een andere host (zoals de fotokaart, Keuze 31). Een beheerder kan op de sportpagina een bron
instellen, een adres met {z}, {x} en {y} (bijvoorbeeld de tegels van OpenStreetMap). De browser
vraagt de tegels dan aan de app (de CSP blijft 'self'), en de app haalt ze één keer op en
bewaart ze 30 dagen in data_dir/cache/sport-tegels/<sleutel van de bron>/z/x/y. Zo ziet de
tegelbron alleen de server, nooit wie er kijkt, en vraagt hij elke tegel maar één keer per
maand (de gebruiksregels van OpenStreetMap vragen dat).
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import anyio
import httpx

from app.config import Settings

KEY = "sport"
TTL = 30 * 24 * 3600
MAX_ZOOM = 19
MAX_TILE = 2 * 1024 * 1024
USER_AGENT = "platform-sport/1 (zelf gehost; tegels gecachet)"
_TEMPLATE = re.compile(r"^https://[^\s{}]+(\{[zxy]\}[^\s{}]*){3}$")
# Hooguit zoveel tegels tegelijk ophalen bij de bron
_LIMIT = anyio.CapacityLimiter(4)


class TileError(Exception):
    """De bron gaf geen tegel."""


def valid_template(url: str) -> bool:
    return bool(_TEMPLATE.match(url)) and all(p in url for p in ("{z}", "{x}", "{y}"))


def cache_root(settings: Settings, template: str) -> Path:
    key = hashlib.sha256(template.encode()).hexdigest()[:16]
    return settings.data_dir / "cache" / "sport-tegels" / key


def _type_of(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise TileError("geen afbeelding")


async def tile(settings: Settings, template: str, z: int, x: int, y: int) -> tuple[bytes, str]:
    """De tegel (bytes, mime). Uit de cache als die jonger is dan TTL; anders van de bron."""
    path = cache_root(settings, template) / str(z) / str(x) / str(y)
    apath = anyio.Path(path)
    try:
        st = await apath.stat()
        if time.time() - st.st_mtime < TTL:
            data = await apath.read_bytes()
            return data, _type_of(data)
    except (OSError, TileError):
        pass
    url = template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    async with _LIMIT:
        try:
            async with httpx.AsyncClient(
                timeout=10.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
            ) as client:
                r = await client.get(url)
        except httpx.HTTPError as e:
            raise TileError("bron onbereikbaar") from e
    if r.status_code != 200 or len(r.content) > MAX_TILE:
        raise TileError(f"bron gaf {r.status_code}")
    data = r.content
    mime = _type_of(data)
    try:
        await anyio.Path(path.parent).mkdir(parents=True, exist_ok=True)
        tmp = anyio.Path(f"{path}.tmp")
        await tmp.write_bytes(data)
        await tmp.rename(path)
    except OSError:
        pass  # zonder cache werkt het ook
    return data, mime
