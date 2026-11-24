"""Activiteit en statistieken op /beheer → Media (fase 11, stap 7; zoals Tautulli, eigen code).

GET /api/v1/admin/media/stats?days=30 → MediaStats

Wie nu kijkt (met apparaat en direct play of transcode) staat al in GET /api/v1/admin/media
(sessions, fase 5). Hier de rest, uit playback_state: wat er laatst gekeken is (per gebruiker en
profiel), wat het meest gekeken wordt, en per gebruiker hoeveel. Alleen voor beheerders.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db import Session
from app.media import runtime
from app.models import MediaItem, MediaProfile, MediaShow, PlaybackState, User
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin/media/stats", tags=["admin"])

RECENT = 50
TOP = 20


class Watched(BaseModel):
    user: str
    profile: str | None = None
    title: str
    kind: str
    at: dt.datetime
    watched: bool
    position_ms: int
    duration_ms: int


class Popular(BaseModel):
    node_id: uuid.UUID
    title: str
    kind: str
    plays: int
    viewers: int


class PerUser(BaseModel):
    user: str
    titles: int  # verschillende titels bekeken in de periode
    watched: int  # waarvan helemaal gezien
    hours: float  # ongeveer: de positie van elke stand (of de duur als gezien)


class MediaStats(BaseModel):
    days: int
    recent: list[Watched] = Field(default_factory=list)
    popular: list[Popular] = Field(default_factory=list)
    users: list[PerUser] = Field(default_factory=list)


def _title(item: MediaItem, shows: dict[uuid.UUID, MediaShow]) -> str:
    if item.kind == "episode" and item.show_id in shows:
        code = ""
        if item.season is not None and item.episode is not None:
            code = f" S{item.season:02d}E{item.episode:02d}"
        return f"{shows[item.show_id].title}{code}"
    return item.title


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


@router.get("", response_model=MediaStats)
async def stats(
    admin: AdminUser,
    request: Request,
    session: Session,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
):
    runtime.require(request)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    rows = list(
        await session.execute(
            select(PlaybackState, MediaItem, User)
            .join(MediaItem, MediaItem.node_id == PlaybackState.node_id)
            .join(User, User.id == PlaybackState.user_id)
            .where(PlaybackState.updated_at >= since)
            .order_by(PlaybackState.updated_at.desc())
            .limit(5000)
        )
    )
    show_ids = {i.show_id for _, i, _ in rows if i.show_id}
    shows = (
        {
            s.id: s
            for s in await session.scalars(select(MediaShow).where(MediaShow.id.in_(show_ids)))
        }
        if show_ids
        else {}
    )
    names = {
        (p.user_id, p.id): p.name
        for p in await session.scalars(select(MediaProfile).where(MediaProfile.id != ""))
    }
    recent = [
        Watched(
            user=u.display_name,
            profile=names.get((s.user_id, s.profile)) if s.profile else None,
            title=_title(i, shows),
            kind=i.kind,
            at=_aware(s.updated_at),
            watched=bool(s.watched),
            position_ms=s.position_ms or 0,
            duration_ms=s.duration_ms or 0,
        )
        for s, i, u in rows[:RECENT]
    ]
    plays = await session.execute(
        select(
            PlaybackState.node_id,
            func.sum(PlaybackState.play_count),
            func.count(func.distinct(PlaybackState.user_id)),
        )
        .where(PlaybackState.updated_at >= since, PlaybackState.play_count > 0)
        .group_by(PlaybackState.node_id)
        .order_by(func.sum(PlaybackState.play_count).desc())
        .limit(TOP)
    )
    top = list(plays)
    items = {
        i.node_id: i
        for i in await session.scalars(
            select(MediaItem).where(MediaItem.node_id.in_([n for n, _, _ in top]))
        )
    }
    popular = [
        Popular(
            node_id=n,
            title=_title(items[n], shows),
            kind=items[n].kind,
            plays=int(p or 0),
            viewers=int(v or 0),
        )
        for n, p, v in top
        if n in items
    ]
    per: dict[str, PerUser] = {}
    for s, _, u in rows:
        row = per.setdefault(
            u.display_name, PerUser(user=u.display_name, titles=0, watched=0, hours=0)
        )
        row.titles += 1
        row.watched += bool(s.watched)
        ms = (s.duration_ms or 0) if s.watched else (s.position_ms or 0)
        row.hours = round(row.hours + ms / 3_600_000, 1)
    return MediaStats(
        days=days,
        recent=recent,
        popular=popular,
        users=sorted(per.values(), key=lambda r: (-r.hours, r.user)),
    )
