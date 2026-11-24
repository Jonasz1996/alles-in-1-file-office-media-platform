"""Opruimregels (fase 11, stap 8; zoals Maintainerr, eigen code).

Films en series waar al lang niemand naar kijkt, gaan eerst op de lijst "binnenkort weg"
(`arr_cleanup`, met due_at = nu + `grace_days`). Een beheerder kan er een tegenhouden (kept:
blijft dan altijd staan). Wie op de lijst staat en weer bekeken wordt, valt eraf. Na due_at gaat
het naar de prullenbak van de eigenaar van de bibliotheek (gewoon terug te zetten tot de
prullenbak geleegd wordt); de scan van media ziet het daarna als weg.

Regels (instellingen in de rij `arr_settings` onder "cleanup", standaard uit):

    {"movies": false, "movie_days": 90, "shows": false, "show_days": 365, "grace_days": 7,
     "watched_only": true}

- **Film:** niet toegevoegd en door niemand bekeken binnen `movie_days`; met watched_only ook
  minstens één keer helemaal gezien.
- **Serie:** geen aflevering toegevoegd of bekeken binnen `show_days`; met watched_only ook
  minstens één aflevering helemaal gezien.

Wat weg gaat: bij een film zijn eigen map (als die onder de bibliotheek alleen die film heeft),
anders het bestand; bij een serie haar map, of zonder map elke aflevering.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.arr import settings as arr_settings
from app.models import ArrCleanup, MediaItem, MediaLibrary, MediaShow, Node, PlaybackState

log = logging.getLogger(__name__)

KEY = "cleanup"
ROUND_S = 3600.0  # één ronde per uur


@dataclass(frozen=True)
class CleanupConf:
    movies: bool = False
    movie_days: int = 90
    shows: bool = False
    show_days: int = 365
    grace_days: int = 7
    watched_only: bool = True


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


async def load(session: AsyncSession) -> CleanupConf:
    entry: Any = (await arr_settings._raw(session)).get(KEY)
    d = CleanupConf()
    if not isinstance(entry, dict):
        return d
    return CleanupConf(
        movies=bool(entry.get("movies", d.movies)),
        movie_days=_int(entry.get("movie_days"), d.movie_days, 7, 3650),
        shows=bool(entry.get("shows", d.shows)),
        show_days=_int(entry.get("show_days"), d.show_days, 7, 3650),
        grace_days=_int(entry.get("grace_days"), d.grace_days, 1, 90),
        watched_only=bool(entry.get("watched_only", d.watched_only)),
    )


async def store(session: AsyncSession, conf: CleanupConf) -> None:
    raw = await arr_settings._raw(session)
    raw[KEY] = {
        "movies": conf.movies,
        "movie_days": conf.movie_days,
        "shows": conf.shows,
        "show_days": conf.show_days,
        "grace_days": conf.grace_days,
        "watched_only": conf.watched_only,
    }
    await arr_settings._write(session, raw)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


async def _movies(session: AsyncSession, conf: CleanupConf, now: dt.datetime) -> dict:
    """{node_id: (library_id, titel)} van de films die volgens de regel weg mogen."""
    since = now - dt.timedelta(days=conf.movie_days)
    activity = (
        select(
            PlaybackState.node_id.label("node_id"),
            func.max(PlaybackState.updated_at).label("last"),
            func.max(PlaybackState.watched.cast(Integer)).label("seen"),
        )
        .group_by(PlaybackState.node_id)
        .subquery()
    )
    rows = await session.execute(
        select(MediaItem, activity.c.last, activity.c.seen)
        .join(Node, Node.id == MediaItem.node_id)
        .outerjoin(activity, activity.c.node_id == MediaItem.node_id)
        .where(
            MediaItem.kind == "movie",
            MediaItem.present.is_(True),
            Node.deleted_at.is_(None),
            MediaItem.added_at < since,
        )
    )
    out: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    for item, last, seen in rows:
        if conf.watched_only and not seen:
            continue
        last = _aware(last)
        if last is not None and last >= since:
            continue
        title = f"{item.title} ({item.year})" if item.year else item.title
        out[item.node_id] = (item.library_id, title)
    return out


async def _shows(session: AsyncSession, conf: CleanupConf, now: dt.datetime) -> dict:
    """{show_id: (library_id, titel)} van de series die volgens de regel weg mogen."""
    since = now - dt.timedelta(days=conf.show_days)
    added = dict(
        list(
            await session.execute(
                select(MediaItem.show_id, func.max(MediaItem.added_at))
                .where(MediaItem.kind == "episode", MediaItem.show_id.is_not(None))
                .group_by(MediaItem.show_id)
            )
        )
    )
    played = {
        show: (last, seen)
        for show, last, seen in await session.execute(
            select(
                MediaItem.show_id,
                func.max(PlaybackState.updated_at),
                func.max(PlaybackState.watched.cast(Integer)),
            )
            .join(MediaItem, MediaItem.node_id == PlaybackState.node_id)
            .where(MediaItem.kind == "episode", MediaItem.show_id.is_not(None))
            .group_by(MediaItem.show_id)
        )
    }
    present = set(
        (
            await session.scalars(
                select(MediaItem.show_id)
                .join(Node, Node.id == MediaItem.node_id)
                .where(
                    MediaItem.kind == "episode",
                    MediaItem.present.is_(True),
                    Node.deleted_at.is_(None),
                )
                .distinct()
            )
        ).all()
    )
    out: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    if not present:
        return out
    for show in await session.scalars(select(MediaShow).where(MediaShow.id.in_(present))):
        last_added = _aware(added.get(show.id))
        if last_added is None or last_added >= since:
            continue
        last, seen = played.get(show.id, (None, 0))
        if conf.watched_only and not seen:
            continue
        last = _aware(last)
        if last is not None and last >= since:
            continue
        out[show.id] = (show.library_id, show.title)
    return out


async def plan(session: AsyncSession, conf: CleanupConf, now: dt.datetime) -> int:
    """De lijst bijwerken: nieuwe kandidaten erop, wie weer bekeken wordt eraf. Geeft hoeveel
    er nieuw bij kwamen."""
    found: dict[tuple[str, uuid.UUID], tuple[uuid.UUID, str]] = {}
    if conf.movies:
        found |= {("movie", k): v for k, v in (await _movies(session, conf, now)).items()}
    if conf.shows:
        found |= {("show", k): v for k, v in (await _shows(session, conf, now)).items()}
    rows = {(r.kind, r.target_id): r for r in await session.scalars(select(ArrCleanup))}
    for key, row in rows.items():
        if key not in found and not row.kept and row.done_at is None:
            await session.delete(row)
    added = 0
    for (kind, target), (library_id, title) in found.items():
        if (kind, target) in rows:
            continue
        session.add(
            ArrCleanup(
                kind=kind,
                target_id=target,
                library_id=library_id,
                title=title[:255],
                reason="unwatched",
                due_at=now + dt.timedelta(days=conf.grace_days),
            )
        )
        added += 1
    await session.commit()
    return added


async def _nodes(session: AsyncSession, row: ArrCleanup) -> list[Node]:
    """De nodes die voor deze rij naar de prullenbak gaan."""
    library = await session.get(MediaLibrary, row.library_id)
    if library is None:
        return []
    if row.kind == "movie":
        node = await session.get(Node, row.target_id)
        if node is None or node.deleted_at is not None:
            return []
        parent = node.parent_id
        if parent is not None and parent != library.root_id:
            others = await session.scalar(
                select(func.count())
                .select_from(MediaItem)
                .join(Node, Node.id == MediaItem.node_id)
                .where(
                    Node.parent_id == parent,
                    MediaItem.kind == "movie",
                    MediaItem.node_id != node.id,
                    Node.deleted_at.is_(None),
                )
            )
            folder = await session.get(Node, parent)
            if not others and folder is not None and folder.deleted_at is None:
                return [folder]
        return [node]
    show = await session.get(MediaShow, row.target_id)
    if show is None:
        return []
    if show.folder_id is not None and show.folder_id != library.root_id:
        folder = await session.get(Node, show.folder_id)
        if folder is not None and folder.deleted_at is None:
            return [folder]
    return list(
        await session.scalars(
            select(Node)
            .join(MediaItem, MediaItem.node_id == Node.id)
            .where(MediaItem.show_id == show.id, Node.deleted_at.is_(None))
        )
    )


async def remove(session: AsyncSession, row: ArrCleanup, now: dt.datetime) -> int:
    """Naar de prullenbak; geeft hoeveel nodes."""
    from app.routers.files import notify

    nodes = await _nodes(session, row)
    for node in nodes:
        node.deleted_at = now
    row.done_at = now
    await session.commit()
    for node in nodes:
        try:
            await notify(session, node, "node.deleted")
        except Exception:  # noqa: BLE001 - de prullenbak is gezet; alleen het bericht mislukt
            log.exception("opruimen: melden van %s mislukte", node.id)
    return len(nodes)


async def due(session: AsyncSession, now: dt.datetime) -> int:
    rows = list(
        await session.scalars(
            select(ArrCleanup).where(
                ArrCleanup.kept.is_(False),
                ArrCleanup.done_at.is_(None),
                ArrCleanup.due_at <= now,
            )
        )
    )
    for row in rows:
        await remove(session, row, now)
    return len(rows)


async def forget_done(session: AsyncSession, now: dt.datetime) -> None:
    """Rijen die al 30 dagen in de prullenbak liggen, hoeven niet meer op de lijst."""
    await session.execute(
        delete(ArrCleanup).where(
            ArrCleanup.done_at.is_not(None), ArrCleanup.done_at < now - dt.timedelta(days=30)
        )
    )
    await session.commit()


@dataclass
class _State:
    last: float | None = None


STATE = _State()


async def tick(settings, *, force: bool = False) -> None:
    """Eén ronde per uur: de lijst bijwerken en wat aan de beurt is weggooien."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import get_engine

    if not settings.media_enabled:
        return
    mono = time.monotonic()
    if not force and STATE.last is not None and mono - STATE.last < ROUND_S:
        return
    STATE.last = mono
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    now = dt.datetime.now(dt.UTC)
    async with maker() as session:
        conf = await load(session)
        if conf.movies or conf.shows:
            await plan(session, conf, now)
        await due(session, now)
        await forget_done(session, now)
