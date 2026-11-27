"""Live tv (IPTV) voor wie ingelogd is (geen gasten).

GET    /api/v1/iptv/channels                 → Channels (met nu en straks uit de gids)
GET    /api/v1/iptv/guide?start=&hours=      → Guide (hooguit 24 uur, zonder beschrijving)
GET    /api/v1/iptv/programmes/{id}          → Programme (met beschrijving)
PUT    /api/v1/iptv/favorites/{key}          → 204
DELETE /api/v1/iptv/favorites/{key}          → 204
GET    /api/v1/iptv/channels/{id}/live       → video/mp4 (fMP4 zonder einde; X-Codecs)
                                               | 503 iptv_busy {channels} | 502 iptv_upstream
                                               | 503 media_off
GET    /api/v1/iptv/logo/{logo_key}?sig=     → het logo (ondertekend adres, zonder login)

Een kanaal dat de beheerder verborg, staat er niet in en speelt niet. Het stroomadres van een
kanaal (met de logins van de aanbieder) gaat nooit naar de client: de app haalt de stroom zelf
(app/iptv/tuner.py).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, delete, select

from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.iptv import conf as iptv_conf
from app.iptv import logos, refresh, tuner
from app.media import runtime
from app.models import IptvChannel, IptvFavorite, IptvProgramme, User
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/iptv", tags=["iptv"])
Config = Annotated[Settings, Depends(get_settings)]
KEY = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


def _member(user: User) -> User:
    if user.role == "guest":
        raise fail(403, "forbidden", "Live tv is er niet voor gasten")
    return user


class Slot(BaseModel):
    id: int
    start: dt.datetime
    stop: dt.datetime
    title: str
    subtitle: str | None = None
    category: str | None = None
    episode: str | None = None


class Programme(Slot):
    channel: str
    description: str | None = None


class Channel(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    number: int | None
    group: str | None
    xmltv_id: str | None
    logo: str | None
    favorite: bool
    now: Slot | None = None
    next: Slot | None = None


class Channels(BaseModel):
    configured: bool
    channels: list[Channel]
    groups: list[str]


class Guide(BaseModel):
    start: dt.datetime
    stop: dt.datetime
    programmes: dict[str, list[Slot]]


def _slot(p: IptvProgramme) -> Slot:
    return Slot(
        id=p.id,
        start=p.start,
        stop=p.stop,
        title=p.title,
        subtitle=p.subtitle,
        category=p.category,
        episode=p.episode,
    )


def _utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


@router.get("/channels", response_model=Channels)
async def channels(user: CurrentUser, session: Session, settings: Config):
    _member(user)
    rows = list(
        await session.scalars(
            select(IptvChannel).where(IptvChannel.hidden.is_(False)).order_by(IptvChannel.position)
        )
    )
    favorites = set(
        await session.scalars(
            select(IptvFavorite.channel_key).where(IptvFavorite.user_id == user.id)
        )
    )
    now = dt.datetime.now(dt.UTC)
    ids = sorted({c.xmltv_id for c in rows if c.xmltv_id})
    upcoming: dict[str, list[IptvProgramme]] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        progs = await session.scalars(
            select(IptvProgramme)
            .where(
                IptvProgramme.xmltv_id.in_(chunk),
                IptvProgramme.stop > now,
                IptvProgramme.start < now + dt.timedelta(hours=12),
            )
            .order_by(IptvProgramme.xmltv_id, IptvProgramme.start)
        )
        for p in progs:
            have = upcoming.setdefault(p.xmltv_id, [])
            if len(have) < 2:
                have.append(p)
    configured = bool(rows) or (await iptv_conf.load(session, settings)).playlist is not None
    out: list[Channel] = []
    groups: list[str] = []
    for c in rows:
        progs = upcoming.get(c.xmltv_id or "", [])
        current = progs[0] if progs and _utc(progs[0].start) <= now else None
        following = progs[1] if current is not None and len(progs) > 1 else None
        if current is None and progs:
            following = progs[0]
        if c.group_title and c.group_title not in groups:
            groups.append(c.group_title)
        out.append(
            Channel(
                id=c.id,
                key=c.key,
                name=c.name,
                number=c.number,
                group=c.group_title,
                xmltv_id=c.xmltv_id,
                logo=logos.url(settings, c.logo_key),
                favorite=c.key in favorites,
                now=_slot(current) if current else None,
                next=_slot(following) if following else None,
            )
        )
    return Channels(configured=configured, channels=out, groups=groups)


@router.get("/guide", response_model=Guide)
async def guide(
    user: CurrentUser,
    session: Session,
    start: dt.datetime | None = None,
    hours: Annotated[int, Query(ge=1, le=24)] = 6,
):
    _member(user)
    begin = _utc(start) if start else dt.datetime.now(dt.UTC)
    begin = begin.replace(minute=0 if begin.minute < 30 else 30, second=0, microsecond=0)
    end = begin + dt.timedelta(hours=hours)
    visible = select(IptvChannel.xmltv_id).where(
        IptvChannel.hidden.is_(False), IptvChannel.xmltv_id.is_not(None)
    )
    rows = await session.scalars(
        select(IptvProgramme)
        .where(
            IptvProgramme.xmltv_id.in_(visible),
            IptvProgramme.stop > begin,
            IptvProgramme.start < end,
        )
        .order_by(IptvProgramme.xmltv_id, IptvProgramme.start)
    )
    out: dict[str, list[Slot]] = {}
    for p in rows:
        out.setdefault(p.xmltv_id, []).append(_slot(p))
    return Guide(start=begin, stop=end, programmes=out)


@router.get("/programmes/{programme_id}", response_model=Programme)
async def programme(programme_id: int, user: CurrentUser, session: Session):
    _member(user)
    p = await session.get(IptvProgramme, programme_id)
    if p is None:
        raise fail(404, "not_found", "Niet gevonden")
    return Programme(**_slot(p).model_dump(), channel=p.xmltv_id, description=p.description)


@router.put("/favorites/{key}", status_code=204)
async def add_favorite(key: KEY, user: CurrentUser, session: Session):
    _member(user)
    if await session.get(IptvFavorite, (user.id, key)) is None:
        session.add(IptvFavorite(user_id=user.id, channel_key=key))
        await session.commit()
    return Response(status_code=204)


@router.delete("/favorites/{key}", status_code=204)
async def remove_favorite(key: KEY, user: CurrentUser, session: Session):
    _member(user)
    await session.execute(
        delete(IptvFavorite).where(
            and_(IptvFavorite.user_id == user.id, IptvFavorite.channel_key == key)
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/channels/{channel_id}/live")
async def live(
    channel_id: uuid.UUID, request: Request, user: CurrentUser, session: Session, settings: Config
):
    _member(user)
    runtime.require(request)
    channel = await session.get(IptvChannel, channel_id)
    if channel is None or channel.hidden:
        raise fail(404, "not_found", "Niet gevonden")
    conf = await iptv_conf.load(session, settings)
    stream = refresh.unseal_stream(settings, channel.stream)
    name = channel.name
    await session.commit()  # geen databaseverbinding open tijdens de stroom (regel 8)
    if stream is None:
        raise fail(
            502, "iptv_upstream", "Het adres van dit kanaal is niet te lezen; werk de lijst bij"
        )
    url, ua = stream
    spec = tuner.Spec(
        channel_id=channel_id,
        name=name,
        url=url,
        user_agent=ua,
        video=conf.video,
        max_streams=conf.max_streams,
    )
    try:
        current, body = await tuner.watch(spec)
    except tuner.Busy as exc:
        raise fail(
            503,
            "iptv_busy",
            "Alle tuners zijn bezet",
            headers={"Retry-After": "10"},
            channels=exc.channels,
        ) from None
    except tuner.TuneError as exc:
        raise fail(502, "iptv_upstream", str(exc), reason=str(exc)) from None
    return StreamingResponse(
        body,
        media_type="video/mp4",
        headers={
            "X-Codecs": current.codecs,
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/logo/{logo_key}")
async def logo(
    logo_key: KEY,
    session: Session,
    settings: Config,
    sig: Annotated[str, Query(max_length=64)] = "",
):
    if not logos.valid(settings, logo_key, sig):
        raise fail(404, "not_found", "Niet gevonden")
    source = await session.scalar(
        select(IptvChannel.logo).where(IptvChannel.logo_key == logo_key).limit(1)
    )
    await session.commit()
    if not source:
        raise fail(404, "not_found", "Niet gevonden")
    found = await logos.fetch(settings, logo_key, source)
    if found is None:
        raise fail(404, "not_found", "Niet gevonden")
    data, mime = found
    return Response(
        data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
