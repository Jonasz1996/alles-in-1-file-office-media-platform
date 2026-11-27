"""Live tv op /beheer → Media → Live tv. Alleen beheerders.

GET    /api/v1/admin/iptv                    → IptvView (nooit de m3u zelf)
PUT    /api/v1/admin/iptv                    IptvIn → IptvSaved | 422 iptv_playlist
                                             | 503 iptv_unreachable
POST   /api/v1/admin/iptv/refresh            → 202 (de taak iptv.refresh)
DELETE /api/v1/admin/iptv                    → 204 (instellingen, kanalen en gids weg)
GET    /api/v1/admin/iptv/channels           → [AdminChannel] (ook de verborgen)
PATCH  /api/v1/admin/iptv/channels/{id}      ChannelPatch → AdminChannel

De m3u is een adres (playlist_url) of de inhoud van een bestand (playlist_content). Opslaan leest
hem eerst (wie niet kan lezen, bewaart niet) en zet daarna een ronde bijwerken klaar. Een fout
zegt nooit het adres of een stuk van de inhoud: daar staan de logins van de aanbieder in.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.iptv import conf as iptv_conf
from app.iptv import m3u, refresh, tuner
from app.models import IptvChannel, IptvFavorite, IptvProgramme
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin/iptv", tags=["beheer"])
Config = Annotated[Settings, Depends(get_settings)]


class IptvIn(BaseModel):
    playlist_url: str | None = Field(default=None, max_length=4000)
    playlist_content: str | None = Field(default=None, max_length=iptv_conf.MAX_FILE_BYTES)
    guide_url: str | None = Field(default=None, max_length=2000)
    max_streams: int = Field(default=iptv_conf.DEFAULT_MAX_STREAMS, ge=1, le=20)
    video: Literal["auto", "transcode"] = "auto"


class IptvSaved(BaseModel):
    channels: int | None  # het aantal kanalen in de nieuwe m3u (None: de oude bleef)
    guide_url: str | None  # de gids uit de m3u (url-tvg) als er geen ingesteld is


class AdminChannel(BaseModel):
    id: uuid.UUID
    name: str
    number: int | None
    group: str | None
    xmltv_id: str | None
    hidden: bool
    programmes: bool


class ChannelPatch(BaseModel):
    hidden: bool


@router.get("")
async def overview(admin: AdminUser, session: Session, settings: Config) -> dict:
    out = await iptv_conf.view(session, settings)
    channels, programmes = await refresh.counts(session)
    out["channels"] = channels
    out["programmes"] = programmes
    out["tuners"] = [t.info() for t in tuner.running()]
    return out


@router.put("", response_model=IptvSaved)
async def save(body: IptvIn, admin: AdminUser, session: Session, settings: Config):
    guide = (body.guide_url or "").strip() or None
    if guide is not None:
        try:
            guide = refresh.check_url(guide)
        except refresh.FetchError as exc:
            raise fail(422, "invalid_input", str(exc), field="guide_url") from None
    playlist: str | None = None
    kind: Literal["url", "file"] | None = None
    count: int | None = None
    found_guide: str | None = None
    if body.playlist_content and body.playlist_content.strip():
        playlist, kind = body.playlist_content, "file"
        text = playlist
    elif body.playlist_url and body.playlist_url.strip():
        try:
            playlist, kind = refresh.check_url(body.playlist_url), "url"
        except refresh.FetchError as exc:
            raise fail(422, "invalid_input", str(exc), field="playlist_url") from None
        await session.commit()  # geen databaseverbinding open tijdens het ophalen (regel 8)
        try:
            text = await refresh.fetch_text(playlist)
        except refresh.FetchError as exc:
            raise fail(503, "iptv_unreachable", str(exc)) from None
    else:
        text = None
    if text is not None:
        try:
            parsed = m3u.parse(text)
        except m3u.M3UError as exc:
            raise fail(422, "iptv_playlist", str(exc)) from None
        count = len(parsed.entries)
        found_guide = parsed.guide_url
    elif (await iptv_conf.load(session, settings)).playlist is None:
        raise fail(422, "iptv_playlist", "Kies een m3u-adres of een m3u-bestand")
    await iptv_conf.store(
        session,
        settings,
        playlist=playlist,
        playlist_kind=kind,
        guide_url=guide,
        max_streams=body.max_streams,
        video=body.video,
    )
    await refresh.enqueue(session)
    return IptvSaved(channels=count, guide_url=found_guide if guide is None else None)


@router.post("/refresh", status_code=202)
async def refresh_now(admin: AdminUser, session: Session, settings: Config):
    if (await iptv_conf.load(session, settings)).playlist is None:
        raise fail(404, "not_found", "Live tv is nog niet ingesteld")
    await refresh.enqueue(session)
    return Response(status_code=202)


@router.delete("", status_code=204)
async def forget(admin: AdminUser, session: Session):
    tuner.stop_all()
    await session.execute(delete(IptvProgramme))
    await session.execute(delete(IptvChannel))
    await session.execute(delete(IptvFavorite))
    await session.commit()
    await iptv_conf.forget(session)
    return Response(status_code=204)


async def _admin_channel(session, c: IptvChannel) -> AdminChannel:
    has = False
    if c.xmltv_id:
        has = (
            await session.scalar(
                select(IptvProgramme.id).where(IptvProgramme.xmltv_id == c.xmltv_id).limit(1)
            )
        ) is not None
    return AdminChannel(
        id=c.id,
        name=c.name,
        number=c.number,
        group=c.group_title,
        xmltv_id=c.xmltv_id,
        hidden=c.hidden,
        programmes=has,
    )


@router.get("/channels", response_model=list[AdminChannel])
async def admin_channels(admin: AdminUser, session: Session):
    rows = list(await session.scalars(select(IptvChannel).order_by(IptvChannel.position)))
    with_guide = set(await session.scalars(select(IptvProgramme.xmltv_id).distinct()))
    return [
        AdminChannel(
            id=c.id,
            name=c.name,
            number=c.number,
            group=c.group_title,
            xmltv_id=c.xmltv_id,
            hidden=c.hidden,
            programmes=bool(c.xmltv_id and c.xmltv_id in with_guide),
        )
        for c in rows
    ]


@router.patch("/channels/{channel_id}", response_model=AdminChannel)
async def patch_channel(
    channel_id: uuid.UUID, body: ChannelPatch, admin: AdminUser, session: Session
):
    c = await session.get(IptvChannel, channel_id)
    if c is None:
        raise fail(404, "not_found", "Niet gevonden")
    c.hidden = body.hidden
    await session.commit()
    if body.hidden:
        running = tuner.get(channel_id)
        if running is not None:
            running.stop()
    return await _admin_channel(session, c)
