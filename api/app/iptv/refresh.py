"""Lijst en gids ophalen en opslaan: de taak `iptv.refresh` (rij media_net).

- Vanzelf: het opruimwerk `iptv.schedule` kijkt elke CHECK_S of de laatste ronde ouder is dan
  EVERY_S (of er nog geen was) en zet dan de taak klaar. Met de hand: /beheer → Live tv →
  Nu bijwerken.
- Tijdens het verkeer naar buiten staat er geen databaseverbinding open (regel 8): eerst de
  instellingen lezen, dan ophalen, dan in een eigen korte transactie schrijven.
- De kanalen: nieuw erbij, bestaande bijgewerkt (op hun vaste sleutel), wat niet meer in de lijst
  staat weg. Een kanaal dat de beheerder verborg, blijft verborgen.
- De gids: alleen voor de tvg-id's van de lijst. Alles van die kanalen gaat weg en het nieuwe
  komt erin (in stukken van BATCH); wat langer dan een dag voorbij is, gaat ook weg.
- Een fout (geen verbinding, geen m3u, geen XMLTV) staat bij `last` in de instellingen en op
  /beheer, met een vaste reden: nooit een adres (daar staan logins in). De volgende ronde
  probeert het gewoon opnieuw.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import jobs
from app.config import Settings
from app.db import get_engine
from app.iptv import conf as iptv_conf
from app.iptv import m3u, xmltv
from app.media.providers.secrets import seal, unseal
from app.models import IptvChannel, IptvProgramme

log = logging.getLogger("platform.iptv")

TYPE = "iptv.refresh"
SCHEDULE = "iptv.schedule"
EVERY_S = 6 * 3600
CHECK_S = 600.0
BATCH = 1000
STREAM_SECRET = "iptv.stream"
PLAYLIST_MAX = 20 * 1024 * 1024
GUIDE_MAX = 512 * 1024 * 1024
TIMEOUT = httpx.Timeout(30.0, read=120.0)
USER_AGENT = "platform-iptv/1"


class FetchError(Exception):
    """Ophalen mislukte (vaste reden, nooit het adres)."""


def http_client() -> httpx.AsyncClient:
    """De client naar de aanbieder en de gids (tests vervangen deze functie)."""
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=True,
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )


def check_url(url: str) -> str:
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")) or any(c.isspace() for c in url):
        raise FetchError("het adres moet met http:// of https:// beginnen")
    return url


async def _download(url: str, limit: int, sink) -> None:
    try:
        async with http_client() as client, client.stream("GET", url) as res:
            if res.status_code in (401, 403):
                raise FetchError(f"de server weigert de toegang ({res.status_code})")
            if res.status_code != 200:
                raise FetchError(f"de server antwoordt {res.status_code}")
            size = 0
            async for chunk in res.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise FetchError(f"groter dan {limit // (1024 * 1024)} MB")
                await sink(chunk)
    except FetchError:
        raise
    except httpx.TimeoutException:
        raise FetchError("de server antwoordt niet op tijd") from None
    except httpx.HTTPError as exc:
        raise FetchError(f"geen verbinding ({exc.__class__.__name__})") from None


async def fetch_text(url: str, limit: int = PLAYLIST_MAX) -> str:
    parts: list[bytes] = []

    async def sink(chunk: bytes) -> None:
        parts.append(chunk)

    await _download(check_url(url), limit, sink)
    return b"".join(parts).decode("utf-8", "replace")


async def fetch_file(url: str, path: Path, limit: int = GUIDE_MAX) -> None:
    f = await asyncio.to_thread(open, path, "wb")
    try:

        async def sink(chunk: bytes) -> None:
            await asyncio.to_thread(f.write, chunk)

        await _download(check_url(url), limit, sink)
    finally:
        await asyncio.to_thread(f.close)


def seal_stream(settings: Settings, entry: m3u.Entry) -> dict[str, Any]:
    payload = json.dumps({"url": entry.url, "ua": entry.user_agent})
    return seal(settings, STREAM_SECRET, payload)


def unseal_stream(settings: Settings, value: Any) -> tuple[str, str | None] | None:
    text = unseal(settings, STREAM_SECRET, value)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    url = data.get("url")
    if not isinstance(url, str):
        return None
    ua = data.get("ua")
    return url, ua if isinstance(ua, str) and ua else None


def logo_key(url: str | None) -> str | None:
    return hashlib.sha256(url.encode()).hexdigest()[:32] if url else None


async def store_channels(
    session: AsyncSession, settings: Settings, playlist: m3u.Playlist
) -> set[str]:
    """De kanalen van de lijst opslaan; geeft de tvg-id's (voor de gids)."""
    now = dt.datetime.now(dt.UTC)
    existing = {c.key: c for c in await session.scalars(select(IptvChannel))}
    keep: set[str] = set()
    for entry in playlist.entries:
        keep.add(entry.key)
        row = existing.get(entry.key)
        if row is None:
            row = IptvChannel(key=entry.key, hidden=False)
            session.add(row)
        row.position = entry.position
        row.number = entry.number
        row.name = entry.name
        row.group_title = entry.group
        row.xmltv_id = entry.tvg_id
        row.logo = entry.logo
        row.logo_key = logo_key(entry.logo)
        row.stream = seal_stream(settings, entry)
        row.updated_at = now
    gone = [key for key in existing if key not in keep]
    for i in range(0, len(gone), BATCH):
        await session.execute(delete(IptvChannel).where(IptvChannel.key.in_(gone[i : i + BATCH])))
    await session.commit()
    return {e.tvg_id for e in playlist.entries if e.tvg_id}


def _read_guide(path: Path, ids: set[str]) -> list[xmltv.Programme]:
    return list(xmltv.read(path, ids))


async def store_programmes(
    session: AsyncSession, ids: set[str], programmes: list[xmltv.Programme]
) -> int:
    now = dt.datetime.now(dt.UTC)
    have = {p.channel for p in programmes}
    old = sorted(have)
    for i in range(0, len(old), BATCH):
        await session.execute(
            delete(IptvProgramme).where(IptvProgramme.xmltv_id.in_(old[i : i + BATCH]))
        )
    await session.execute(
        delete(IptvProgramme).where(IptvProgramme.stop < now - dt.timedelta(days=1))
    )
    rows = [
        {
            "xmltv_id": p.channel,
            "start": p.start,
            "stop": p.stop,
            "title": p.title,
            "subtitle": p.subtitle,
            "description": p.description,
            "category": p.category,
            "episode": p.episode,
        }
        for p in programmes
    ]
    for i in range(0, len(rows), BATCH):
        await session.execute(insert(IptvProgramme), rows[i : i + BATCH])
    await session.commit()
    return len(rows)


async def refresh(settings: Settings) -> dict[str, Any]:
    """Eén ronde: lijst, dan gids. Geeft wat in `last` komt."""
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        conf = await iptv_conf.load(session, settings)
    started = dt.datetime.now(dt.UTC)
    last: dict[str, Any] = {
        "at": started.isoformat(),
        "ok": False,
        "error": None,
        "guide_error": None,
        "channels": 0,
        "programmes": 0,
        "skipped": 0,
    }
    if conf.playlist is None:
        last["error"] = "er is nog geen kanaallijst (m3u) ingesteld"
        return last
    try:
        if conf.playlist_kind == "url":
            text = await fetch_text(conf.playlist)
        else:
            text = conf.playlist
        playlist = m3u.parse(text)
    except (FetchError, m3u.M3UError) as exc:
        last["error"] = f"kanaallijst: {exc}"
        return last
    async with maker() as session:
        ids = await store_channels(session, settings, playlist)
    last["channels"] = len(playlist.entries)
    last["skipped"] = playlist.skipped
    last["ok"] = True
    guide_url = conf.guide_url or playlist.guide_url
    if not guide_url:
        last["guide_error"] = "er is geen gids ingesteld"
        return last
    cache = settings.data_dir / "cache" / "iptv"
    await asyncio.to_thread(cache.mkdir, parents=True, exist_ok=True)
    fd, name = await asyncio.to_thread(tempfile.mkstemp, ".xml", "gids-", str(cache))
    os.close(fd)
    path = Path(name)
    try:
        await fetch_file(guide_url, path)
        programmes = await asyncio.to_thread(_read_guide, path, ids)
        async with maker() as session:
            last["programmes"] = await store_programmes(session, ids, programmes)
    except (FetchError, xmltv.GuideError, OSError) as exc:
        last["guide_error"] = f"gids: {exc}" if not isinstance(exc, OSError) else "gids: schijffout"
    finally:
        with contextlib.suppress(OSError):
            await asyncio.to_thread(path.unlink)
    return last


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    last = await refresh(settings)
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        await iptv_conf.store_last(session, last)
    if last["error"] or last["guide_error"]:
        log.warning("live tv bijwerken: %s", last["error"] or last["guide_error"])
    else:
        log.info(
            "live tv bijgewerkt: %s kanalen, %s programma's", last["channels"], last["programmes"]
        )


async def enqueue(session: AsyncSession) -> bool:
    queued = await jobs.enqueue(session, TYPE, {}, key=TYPE)
    await session.commit()
    return queued is not None


async def schedule(settings: Settings) -> None:
    """Het opruimwerk: de taak klaarzetten als de laatste ronde te oud is."""
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        value = await iptv_conf.raw(session)
        if not isinstance(value.get("playlist"), dict):
            return
        last = value.get("last") if isinstance(value.get("last"), dict) else {}
        at = None
        with contextlib.suppress(TypeError, ValueError):
            at = dt.datetime.fromisoformat(str(last.get("at")))
        if at is not None and (dt.datetime.now(dt.UTC) - at).total_seconds() < EVERY_S:
            return
        await enqueue(session)


async def counts(session: AsyncSession) -> tuple[int, int]:
    channels = await session.scalar(select(func.count()).select_from(IptvChannel)) or 0
    programmes = await session.scalar(select(func.count()).select_from(IptvProgramme)) or 0
    return int(channels), int(programmes)


def register() -> None:
    jobs.register(TYPE, run, lane="media_net", timeout=1800.0, max_attempts=2)
    jobs.housekeeping(SCHEDULE, schedule, CHECK_S)


register()
