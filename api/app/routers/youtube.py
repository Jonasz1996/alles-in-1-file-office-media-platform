"""De YouTube-module voor de web-app (fixlijst 26-11-2026, punt 7).

GET  /api/v1/youtube              → {"enabled": bool, "yt_dlp": "<versie>" | null}
GET  /api/v1/youtube/zoek?q=…     → [{id, title, channel, duration, live}] | 503 youtube_off
                                    | 502 youtube_failed
POST /api/v1/youtube/mp3          {video, folder, title, kind: mp3|mp4, sponsor}
                                    → {"job": "<uuid>"} (202)
GET  /api/v1/youtube/mp3          → je laatste omzettingen (hooguit 30, nieuwste eerst)
GET  /api/v1/youtube/abonnementen → [{channel, title, auto, folder, sponsor}]
POST /api/v1/youtube/abonnementen {channel: link, @naam of UC…} → het abonnement (201)
PUT  /api/v1/youtube/abonnementen/{channel} {auto: off|mp3|mp4, folder, sponsor}
DELETE /api/v1/youtube/abonnementen/{channel}
GET  /api/v1/youtube/feed         → de nieuwste video's van je kanalen [{id, title, channel,
                                    channel_id, published}] (nieuwste eerst, hooguit 100)

Abonnementen (app/youtube/subs.py) werken zonder Google-account, met de RSS-feed van elk kanaal.
Met auto mp3 of mp4 zet de server elk uur de nieuwe video's van dat kanaal in de gekozen map.

Kijken (ook live) gebeurt in de web-app zelf, met de speler van YouTube zonder cookies
(youtube-nocookie.com, zie WEB_CSP in main.py); de server ziet daar niets van. Een mp3 is een
taak (app/youtube/service.py); het bestand landt in de gekozen map, en staat die in een
muziekbibliotheek, dan indexeert de scanner het meteen. Niet voor gasten (die hebben geen eigen
bestanden). Een map waar je niet mag schrijven: 403 read_only, weg: 404 folder_not_found.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi import Path as PathParam
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.models import Job, User
from app.routers.office_new import target_folder
from app.security import CurrentUser
from app.youtube import service, subs

router = APIRouter(prefix="/api/v1/youtube", tags=["youtube"])
Config = Annotated[Settings, Depends(get_settings)]

VIDEO_ID = r"^[A-Za-z0-9_-]{11}$"
_ID_IN_URL = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")
_CHANNEL = re.compile(r"(?:^|/channel/)(UC[A-Za-z0-9_-]{22})(?:$|[/?#])")
_HANDLE = re.compile(r"(?:^|/)(@[A-Za-z0-9._-]{3,100})(?:$|[/?#])")
CHANNEL_ID = r"^UC[A-Za-z0-9_-]{22}$"


class Mp3In(BaseModel):
    video: str = Field(pattern=VIDEO_ID)
    folder: uuid.UUID
    title: str = Field(default="", max_length=300)
    kind: Literal["mp3", "mp4"] = "mp3"
    sponsor: bool = False


class SubIn(BaseModel):
    channel: str = Field(min_length=3, max_length=300)


class SubPut(BaseModel):
    auto: Literal["off", "mp3", "mp4"] = "off"
    folder: uuid.UUID | None = None
    sponsor: bool = False


class Result(BaseModel):
    id: str
    title: str
    channel: str
    channel_id: str | None = None
    duration: int | None
    live: bool


class Conversion(BaseModel):
    job: str
    video: str
    title: str
    kind: str = "mp3"
    state: str  # queued | running | done | failed
    error: str | None
    created_at: str


def _no_guest(user: User) -> None:
    if user.role == "guest":
        raise fail(403, "guest", "Gasten hebben geen eigen bestanden")


def _on(settings: Settings) -> None:
    if not service.enabled(settings):
        raise fail(503, "youtube_off", "De YouTube-module staat op deze server niet aan")


@router.get("")
async def status(user: CurrentUser, settings: Config) -> dict:
    _no_guest(user)
    if not service.enabled(settings):
        return {"enabled": False, "yt_dlp": None}
    return {"enabled": True, "yt_dlp": await service.helper_version(settings)}


@router.get("/zoek", response_model=list[Result])
async def search(
    user: CurrentUser, settings: Config, q: str = Query(min_length=1, max_length=200)
) -> list[dict]:
    _no_guest(user)
    _on(settings)
    # Een geplakte link: meteen die ene video (de helper zoekt dan op zijn id)
    found = _ID_IN_URL.search(q)
    query = found.group(1) if found else q.strip()
    try:
        return await service.search(settings, query)
    except service.YoutubeError as exc:
        raise fail(502, "youtube_failed", str(exc)) from None


@router.post("/mp3", status_code=202)
async def to_mp3(body: Mp3In, user: CurrentUser, session: Session, settings: Config) -> dict:
    _no_guest(user)
    _on(settings)
    await target_folder(session, user, body.folder)  # 403/404/422 zoals bij uploaden
    job = await service.enqueue(
        session, user, body.video, body.folder, body.title, kind=body.kind, sponsor=body.sponsor
    )
    return {"job": str(job)}


@router.get("/mp3", response_model=list[Conversion])
async def conversions(user: CurrentUser, session: Session) -> list[Conversion]:
    _no_guest(user)
    rows = await session.scalars(
        select(Job).where(Job.type == service.TYPE).order_by(Job.created_at.desc()).limit(500)
    )
    out: list[Conversion] = []
    for job in rows:
        p = job.payload if isinstance(job.payload, dict) else {}
        if p.get("user") != str(user.id):
            continue
        out.append(
            Conversion(
                job=str(p.get("job", job.id)),
                video=str(p.get("video", "")),
                title=str(p.get("title", "")),
                kind="mp4" if p.get("kind") == "mp4" else "mp3",
                state=job.status,
                error=job.error,
                created_at=job.created_at.isoformat(),
            )
        )
        if len(out) >= 30:
            break
    return out


def _channel_ref(text: str) -> str:
    text = text.strip()
    for pattern in (_CHANNEL, _HANDLE):
        if found := pattern.search(text):
            return found.group(1)
    raise fail(422, "youtube_channel", "Geef een kanaal: een link, @naam of UC…")


def _find(rows: list[dict], channel: str) -> dict:
    for row in rows:
        if row["channel"] == channel:
            return row
    raise fail(404, "youtube_sub_not_found", "Dat abonnement bestaat niet")


@router.get("/abonnementen")
async def list_subs(user: CurrentUser, session: Session) -> list[dict]:
    _no_guest(user)
    return [subs.public(s) for s in await subs.load(session, user)]


@router.post("/abonnementen", status_code=201)
async def add_sub(body: SubIn, user: CurrentUser, session: Session, settings: Config) -> dict:
    _no_guest(user)
    _on(settings)
    rows = await subs.load(session, user)
    if len(rows) >= subs.MAX_SUBS:
        raise fail(409, "youtube_subs_full", f"Hooguit {subs.MAX_SUBS} abonnementen")
    try:
        data = await subs.feed(settings, _channel_ref(body.channel))
    except service.YoutubeError as exc:
        raise fail(502, "youtube_failed", str(exc)) from None
    for row in rows:
        if row["channel"] == data["id"]:
            return subs.public(row)
    row = {
        "channel": data["id"],
        "title": str(data.get("title") or data["id"]),
        "auto": "off",
        "folder": None,
        "sponsor": False,
        "seen": [str(v["id"]) for v in data["videos"]],
    }
    await subs.store(session, user, [*rows, row])
    await session.commit()
    return subs.public(row)


@router.put("/abonnementen/{channel}")
async def put_sub(
    channel: Annotated[str, PathParam(pattern=CHANNEL_ID)],
    body: SubPut,
    user: CurrentUser,
    session: Session,
) -> dict:
    _no_guest(user)
    rows = [dict(r) for r in await subs.load(session, user)]
    row = _find(rows, channel)
    if body.auto != "off":
        if body.folder is None:
            raise fail(422, "youtube_no_folder", "Kies een map voor automatisch bewaren")
        await target_folder(session, user, body.folder)  # 403/404/422 zoals bij uploaden
    row.update(
        auto=body.auto, folder=str(body.folder) if body.folder else None, sponsor=body.sponsor
    )
    await subs.store(session, user, rows)
    await session.commit()
    return subs.public(row)


@router.delete("/abonnementen/{channel}", status_code=204)
async def delete_sub(
    channel: Annotated[str, PathParam(pattern=CHANNEL_ID)], user: CurrentUser, session: Session
) -> None:
    _no_guest(user)
    rows = await subs.load(session, user)
    _find(rows, channel)
    await subs.store(session, user, [r for r in rows if r["channel"] != channel])
    await session.commit()


@router.get("/feed")
async def feed(user: CurrentUser, session: Session, settings: Config) -> list[dict]:
    _no_guest(user)
    _on(settings)
    rows = await subs.load(session, user)
    gate = asyncio.Semaphore(4)

    async def one(row: dict) -> list[dict]:
        async with gate:
            try:
                data = await subs.feed(settings, row["channel"])
            except service.YoutubeError:
                return []
        title = str(row.get("title") or data.get("title") or "")
        return [
            {
                "id": v["id"],
                "title": str(v.get("title") or ""),
                "channel": title,
                "channel_id": row["channel"],
                "published": str(v.get("published") or ""),
            }
            for v in data["videos"]
        ]

    found = [v for part in await asyncio.gather(*(one(r) for r in rows)) for v in part]
    found.sort(key=lambda v: v["published"], reverse=True)
    return found[:100]
