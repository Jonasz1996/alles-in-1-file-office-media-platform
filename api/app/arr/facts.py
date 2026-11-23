"""Collectie en kijkwijzer uit TMDB (fase 11, stap 7).

Elke `ROUND` (vanuit de lus van mediabeheer) vraagt de app voor hooguit `BATCH` films en series
met een TMDB-id wat TMDB weet over hun collectie (alle delen van een filmreeks) en de kijkwijzer
(voor het kinderslot), en bewaart dat in `media_facts`. Wat er al staat, komt na `REFRESH`
opnieuw aan de beurt. Zonder sleutel van TMDB doet een ronde niets; een fout bij één titel slaat
die over tot de volgende ronde.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import get_engine
from app.media.providers import http, tmdb
from app.media.providers.base import ProviderAuth, ProviderRate
from app.media.providers.secrets import load as load_tmdb
from app.models import MediaFacts, MediaItem, MediaShow

log = logging.getLogger("platform.arr")

ROUND = timedelta(minutes=10)
BATCH = 40
REFRESH = timedelta(days=30)


@dataclass
class _State:
    last: datetime | None = None


STATE = _State()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def tmdb_number(ids: Any) -> str | None:
    """Het TMDB-id uit `ids` als getal in tekst, of None."""
    value = str((ids or {}).get("tmdb") or "").strip() if isinstance(ids, dict) else ""
    m = re.fullmatch(r"(?:movie:|tv:)?(\d{1,10})", value)
    return str(int(m.group(1))) if m else None


async def todo(session: AsyncSession, now: datetime, limit: int) -> list[tuple[str, str]]:
    """(kind, tmdb_id) die nog niets hebben of te oud zijn; films eerst."""
    wanted: dict[tuple[str, str], None] = {}
    movies = await session.scalars(
        select(MediaItem.ids).where(
            MediaItem.kind == "movie", MediaItem.present.is_(True), MediaItem.extra.is_(None)
        )
    )
    for ids in movies:
        if (n := tmdb_number(ids)) is not None:
            wanted[("movie", n)] = None
    for ids in await session.scalars(select(MediaShow.ids)):
        if (n := tmdb_number(ids)) is not None:
            wanted[("tv", n)] = None
    if not wanted:
        return []
    have = {
        (f.kind, f.tmdb_id): _aware(f.fetched_at) for f in await session.scalars(select(MediaFacts))
    }
    out = [key for key in wanted if key not in have or now - have[key] >= REFRESH]
    return out[:limit]


async def _provider(session: AsyncSession, settings: Settings):
    """TMDB met een werkende sleutel, of None. Tests vervangen deze functie."""
    await load_tmdb(session, settings)
    found = tmdb.provider()
    return found if found is not None and hasattr(found, "facts") else None


async def store(session: AsyncSession, kind: str, number: str, facts: tmdb.Facts, now) -> None:
    row = await session.get(MediaFacts, (kind, number))
    if row is None:
        row = MediaFacts(kind=kind, tmdb_id=number)
        session.add(row)
    row.collection_id = facts.collection_id
    row.collection_name = facts.collection_name
    row.age = facts.age
    row.fetched_at = now
    await session.commit()


async def tick(settings: Settings, *, force: bool = False) -> int:
    """Eén ronde; geeft hoeveel titels nieuwe gegevens kregen."""
    now = _now()
    if not force and STATE.last is not None and now - STATE.last < ROUND:
        return 0
    STATE.last = now
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    done = 0
    async with maker() as session:
        work = await todo(session, now, BATCH)
        if not work:
            return 0
        provider = await _provider(session, settings)
        if provider is None:
            return 0
        for kind, number in work:
            try:
                facts = await provider.facts(kind, int(number))
            except http.NotFound:
                facts = tmdb.Facts()  # bestaat niet (meer) bij TMDB: niet elke ronde opnieuw
            except (ProviderAuth, ProviderRate) as exc:
                log.info("arr: TMDB stopt deze ronde: %s", exc)
                break
            except Exception as exc:  # noqa: BLE001 - de volgende ronde opnieuw
                log.info("arr: TMDB %s/%s: %s", kind, number, exc)
                continue
            await store(session, kind, number, facts, now)
            done += 1
    return done
