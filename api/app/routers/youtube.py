"""De YouTube-module voor de web-app (fixlijst 26-11-2026, punt 7).

GET  /api/v1/youtube              → {"enabled": bool, "yt_dlp": "<versie>" | null}
GET  /api/v1/youtube/zoek?q=…     → [{id, title, channel, duration, live}] | 503 youtube_off
                                    | 502 youtube_failed
POST /api/v1/youtube/mp3          {video, folder, title} → {"job": "<uuid>"} (202)
GET  /api/v1/youtube/mp3          → je laatste omzettingen (hooguit 30, nieuwste eerst)

Kijken (ook live) gebeurt in de web-app zelf, met de speler van YouTube zonder cookies
(youtube-nocookie.com, zie WEB_CSP in main.py); de server ziet daar niets van. Een mp3 is een
taak (app/youtube/service.py); het bestand landt in de gekozen map, en staat die in een
muziekbibliotheek, dan indexeert de scanner het meteen. Niet voor gasten (die hebben geen eigen
bestanden). Een map waar je niet mag schrijven: 403 read_only, weg: 404 folder_not_found.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.models import Job, User
from app.routers.office_new import target_folder
from app.security import CurrentUser
from app.youtube import service

router = APIRouter(prefix="/api/v1/youtube", tags=["youtube"])
Config = Annotated[Settings, Depends(get_settings)]

VIDEO_ID = r"^[A-Za-z0-9_-]{11}$"
_ID_IN_URL = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


class Mp3In(BaseModel):
    video: str = Field(pattern=VIDEO_ID)
    folder: uuid.UUID
    title: str = Field(default="", max_length=300)


class Result(BaseModel):
    id: str
    title: str
    channel: str
    duration: int | None
    live: bool


class Conversion(BaseModel):
    job: str
    video: str
    title: str
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
    job = await service.enqueue(session, user, body.video, body.folder, body.title)
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
                state=job.status,
                error=job.error,
                created_at=job.created_at.isoformat(),
            )
        )
        if len(out) >= 30:
            break
    return out
