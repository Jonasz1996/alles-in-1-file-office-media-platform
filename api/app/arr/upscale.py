"""De rij en het venster van de AI-upscaling (fase 11, stap 5c).

Ontwerp: docs/fase-11/ai-upscaling.md.

De upscaler is een eigen container op de node met de RTX 3060 Ti (jbogaertmedia2). Hij haalt
werk bij de app (routers/arr_upscale.py, met een eigen token) en werkt alleen binnen het
venster: standaard van minuut 20 tot 40 van elk uur, want de kaart is daarbuiten van Jonas' AI-
verslag (:00-:15) en de security-analyse (:45-:00). Buiten het venster geeft de app geen werk;
de upscaler stopt na het lopende stuk als `seconds_left` op is.

Instellingen in de rij `arr_settings` onder "upscale":

    {"enabled": false, "start": 20, "end": 40, "vram_mb": 4096, "token_sha": "<sha256>"}

Het token zelf bewaart de app niet, alleen zijn SHA-256; /beheer toont het één keer.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.arr import settings as arr_settings

KEY = "upscale"


@dataclass(frozen=True)
class UpscaleConf:
    enabled: bool = False
    start: int = 20
    end: int = 40
    vram_mb: int = 4096
    token_sha: str | None = None


async def load(session: AsyncSession) -> UpscaleConf:
    entry: Any = (await arr_settings._raw(session)).get(KEY)
    if not isinstance(entry, dict):
        return UpscaleConf()
    d = UpscaleConf()
    return UpscaleConf(
        enabled=bool(entry.get("enabled", d.enabled)),
        start=int(entry.get("start", d.start)),
        end=int(entry.get("end", d.end)),
        vram_mb=int(entry.get("vram_mb", d.vram_mb)),
        token_sha=entry.get("token_sha") or None,
    )


async def store(session: AsyncSession, conf: UpscaleConf) -> None:
    raw = await arr_settings._raw(session)
    raw[KEY] = {
        "enabled": conf.enabled,
        "start": conf.start,
        "end": conf.end,
        "vram_mb": conf.vram_mb,
        "token_sha": conf.token_sha,
    }
    await arr_settings._write(session, raw)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def new_token(session: AsyncSession) -> str:
    """Een nieuw token voor de upscaler; het oude werkt dan niet meer."""
    token = "ups_" + secrets.token_urlsafe(32)
    await store(session, replace(await load(session), token_sha=_sha(token)))
    return token


def token_ok(conf: UpscaleConf, token: str | None) -> bool:
    if not token or not conf.token_sha:
        return False
    return hmac.compare_digest(_sha(token), conf.token_sha)


def seconds_left(now: datetime, start: int, end: int) -> int:
    """Seconden tot het venster [start, end) dicht gaat (minuten van het uur), of 0 als het nu
    dicht is. Alleen de minuten tellen: België wijkt een heel uur af van UTC. Een venster over
    het hele uur heen (start > end, bv. 50-10) kan ook."""
    minute = now.minute + now.second / 60
    if start == end:
        return 0
    if start < end:
        inside = start <= minute < end
        until = end - minute
    else:
        inside = minute >= start or minute < end
        until = (end + 60 - minute) if minute >= start else end - minute
    return int(until * 60) if inside else 0
