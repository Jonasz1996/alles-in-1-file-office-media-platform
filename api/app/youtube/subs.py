"""Abonnementen op YouTube-kanalen, zonder Google-account (de RSS-feed van elk kanaal).

Per gebruiker één rij in de tabel setting, sleutel `youtube.subs.<user-id>` (geen migratie):

    {"subs": [{"channel": "UC…", "title": "…", "auto": "off"|"mp3"|"mp4",
               "folder": "<uuid>"|null, "sponsor": bool, "seen": ["<video-id>", …]}]}

`seen` onthoudt de video's die er al waren of al bewaard zijn (hooguit SEEN_MAX per kanaal): bij
het abonneren krijgt het de video's van dat moment, zodat automatisch bewaren alleen nieuwe
uploads pakt. `loop` kijkt elk uur (CHECK_EVERY) naar elk kanaal met auto aan en zet voor elke
nieuwe video een taak youtube.mp3 klaar (mp3 of mp4, met of zonder SponsorBlock) in de gekozen
map, met de rechten van de gebruiker. De feeds komen via de helper: de app praat zelf niet met
YouTube.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import get_engine
from app.models import Setting, User
from app.youtube import service

log = logging.getLogger("platform.youtube")

PREFIX = "youtube.subs."
MAX_SUBS = 100
SEEN_MAX = 100
CHECK_EVERY = 3600.0
FEED_CACHE = 900.0  # een feed geldt een kwartier (de pagina Abonnementen vraagt ze allemaal)
AUTO = ("off", "mp3", "mp4")

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def key(user: User | uuid.UUID) -> str:
    return PREFIX + str(user.id if isinstance(user, User) else user)


async def load(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    row = await session.get(Setting, key(user))
    subs = row.value.get("subs") if row is not None and isinstance(row.value, dict) else None
    return [s for s in subs if isinstance(s, dict) and s.get("channel")] if subs else []


async def store(session: AsyncSession, user: User, subs: list[dict[str, Any]]) -> None:
    row = await session.get(Setting, key(user))
    value = {"subs": subs}
    if row is None:
        session.add(Setting(key=key(user), value=value))
    else:
        row.value = value  # een nieuw dict: SQLAlchemy ziet de wijziging


async def feed(settings: Settings, channel: str) -> dict[str, Any]:
    """De feed van één kanaal, een kwartier bewaard in het geheugen."""
    hit = _cache.get(channel)
    if hit is not None and time.monotonic() - hit[0] < FEED_CACHE:
        return hit[1]
    data = await service.channel(settings, channel)
    _cache[channel] = (time.monotonic(), data)
    return data


def public(sub: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": sub["channel"],
        "title": str(sub.get("title") or ""),
        "auto": sub.get("auto") if sub.get("auto") in AUTO else "off",
        "folder": sub.get("folder"),
        "sponsor": bool(sub.get("sponsor")),
    }


async def check_user(settings: Settings, session: AsyncSession, row: Setting, user: User) -> int:
    """Zet de taken klaar voor de nieuwe video's van één gebruiker; het aantal taken."""
    subs = [dict(s) for s in (row.value or {}).get("subs", []) if isinstance(s, dict)]
    made = 0
    for sub in subs:
        if sub.get("auto") not in ("mp3", "mp4") or not sub.get("folder"):
            continue
        try:
            data = await service.channel(settings, sub["channel"])
        except service.YoutubeError as exc:
            log.warning("youtube: feed van %s: %s", sub["channel"], exc)
            continue
        _cache[sub["channel"]] = (time.monotonic(), data)
        seen = list(sub.get("seen") or [])
        # Oudste eerst klaarzetten, zodat ze in volgorde van uploaden binnenkomen
        for video in reversed(data["videos"]):
            vid = str(video.get("id", ""))
            if not vid or vid in seen:
                continue
            await service.enqueue(
                session,
                user,
                vid,
                uuid.UUID(str(sub["folder"])),
                str(video.get("title") or ""),
                kind=sub["auto"],
                sponsor=bool(sub.get("sponsor")),
                commit=False,
            )
            seen.append(vid)
            made += 1
        sub["seen"] = seen[-SEEN_MAX:]
    if made:
        row.value = {"subs": subs}
    return made


async def tick(settings: Settings) -> int:
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    made = 0
    async with maker() as session:
        rows = (await session.scalars(select(Setting).where(Setting.key.like(PREFIX + "%")))).all()
        for row in rows:
            try:
                user = await session.get(User, uuid.UUID(row.key[len(PREFIX) :]))
            except ValueError:
                continue
            if user is None or user.disabled_at is not None:
                continue
            made += await check_user(settings, session, row, user)
            await session.commit()
    if made:
        log.info("youtube: %d nieuwe video's van abonnementen klaargezet", made)
    return made


async def loop(settings: Settings) -> None:
    await asyncio.sleep(120)  # niet meteen bij het starten
    while True:
        try:
            await tick(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - de volgende ronde probeert het opnieuw
            log.exception("youtube: een ronde abonnementen mislukte")
        await asyncio.sleep(CHECK_EVERY)
