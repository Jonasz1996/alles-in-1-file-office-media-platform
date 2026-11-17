"""Echte JPEG's voor de nep-plaatjeshosts, gemaakt met Pillow (zit er al in), in de gevraagde
maat (§4.4). Per (pad, breedte) één keer gemaakt en bewaard: dezelfde vraag geeft dezelfde bytes.

Het beeld is een vlak in een kleur uit de hash van het pad, met een band en een kader, zodat twee
plaatjes nooit dezelfde bytes hebben en een test kan zien welk plaatje het is.
"""

from __future__ import annotations

import hashlib
import io
import threading

from PIL import Image, ImageDraw

# breedte gedeeld door hoogte, per soort
ASPECT = {"poster": 2 / 3, "backdrop": 16 / 9, "still": 16 / 9, "cover": 1.0}
# "original" per soort: de grootte die de nep dan geeft
ORIGINAL = {"poster": 1000, "backdrop": 1920, "still": 1280, "cover": 1400}


class Images:
    def __init__(self, limit: int = 256) -> None:
        self._cache: dict[tuple[str, str, int], bytes] = {}
        self._limit = limit
        self._lock = threading.Lock()

    def jpeg(self, path: str, kind: str, width: int | None) -> bytes:
        """De JPEG voor `path` op `width` pixels breed (None: de originele maat)."""
        width = width or ORIGINAL[kind]
        key = (path, kind, width)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = make_jpeg(path, width, max(1, round(width / ASPECT[kind])))
        with self._lock:
            if len(self._cache) >= self._limit:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = data
        return data


def make_jpeg(seed: str, width: int, height: int) -> bytes:
    digest = hashlib.sha256(seed.encode()).digest()
    color = (digest[0], digest[1], digest[2])
    accent = (255 - digest[0], 255 - digest[1], 255 - digest[2])
    image = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(image)
    band = max(1, height // 6)
    top = (digest[3] * (height - band)) // 255
    draw.rectangle((0, top, width, top + band), fill=accent)
    edge = max(1, min(width, height) // 40)
    draw.rectangle((0, 0, width - 1, height - 1), outline=accent, width=edge)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()
