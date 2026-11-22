"""Mediabeheer: films en series volgen, en de kalender (fase 11, stap 2d).

GET    /api/v1/arr/profiles                 → [Profile] (de ingebouwde kwaliteitsprofielen)
GET    /api/v1/arr/lookup?kind=movie&q=…    → [Candidate] van TMDB | 409 arr_tmdb_needed
GET    /api/v1/arr/wanted                   → [Wanted]
POST   /api/v1/arr/wanted                   WantedIn → 201 Wanted | 409 arr_tmdb_needed
                                            | 409 arr_wanted_exists | 422 arr_library_kind
PATCH  /api/v1/arr/wanted/{id}              WantedPatch → Wanted | 404
DELETE /api/v1/arr/wanted/{id}              → 204 | 404
GET    /api/v1/arr/calendar?days=30         → [CalendarEntry] | 409 arr_tmdb_needed

Voorlopig alleen beheerders; verzoeken van gezinsleden komen in stap 6. Titels komen van TMDB
(de sleutel van fase 5, /beheer → Media → Online metadata). Volgen zoekt nog niets: dat doet
stap 3. Een film of serie die je volgt, staat in precies één bibliotheek van de juiste soort.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.arr.score import BUILTIN
from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.media.providers import tmdb
from app.media.providers.base import ProviderAuth, ProviderRate, ProviderUnavailable
from app.media.providers.secrets import load as load_tmdb
from app.models import ArrWanted, MediaLibrary
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/arr", tags=["mediabeheer"])
Config = Annotated[Settings, Depends(get_settings)]
Kind = Literal["movie", "show"]
LIBRARY_KIND = {"movie": "movies", "show": "shows"}
MAX_CALENDAR_SHOWS = 50


class ProfileOut(BaseModel):
    key: str
    name: str
    cutoff: str


class CandidateOut(BaseModel):
    tmdb_id: int
    kind: Kind
    title: str
    year: int | None
    overview: str | None
    poster: str | None


class WantedIn(BaseModel):
    kind: Kind
    tmdb_id: int = Field(ge=1)
    library_id: uuid.UUID
    profile: str = "hd-1080p"
    seasons: list[int] | None = Field(default=None, max_length=200)


class WantedPatch(BaseModel):
    profile: str | None = None
    seasons: list[int] | None = Field(default=None, max_length=200)
    all_seasons: bool = False
    monitored: bool | None = None


class WantedOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    tmdb_id: int
    imdb_id: str | None
    title: str
    year: int | None
    library_id: uuid.UUID
    profile: str
    seasons: list[int] | None
    monitored: bool
    created_at: dt.datetime


class CalendarEntry(BaseModel):
    wanted_id: uuid.UUID
    title: str
    season: int
    episode: int
    episode_title: str | None
    aired: dt.date


async def provider(session, settings: Settings):
    """TMDB, of 409 arr_tmdb_needed. Tests vervangen deze functie."""
    await load_tmdb(session, settings)
    found = tmdb.provider()
    if found is None:
        raise fail(409, "arr_tmdb_needed", "Daarvoor is een werkende sleutel van TMDB nodig")
    return found


def _provider_error(exc: Exception):
    if isinstance(exc, ProviderAuth):
        return fail(422, "media_provider_auth", "De sleutel van de provider werkt niet")
    if isinstance(exc, ProviderRate):
        return fail(503, "media_provider_rate", "De provider remt; probeer het zo opnieuw")
    return fail(503, "media_provider_unavailable", "De provider is niet bereikbaar")


def _out(row: ArrWanted) -> WantedOut:
    return WantedOut.model_validate(row, from_attributes=True)


def _check_profile(name: str) -> None:
    if name not in BUILTIN:
        raise fail(422, "invalid_input", "Onbekend kwaliteitsprofiel", field="profile")


def _seasons(values: list[int] | None) -> list[int] | None:
    if values is None:
        return None
    if any(v < 0 or v > 1000 for v in values):
        raise fail(422, "invalid_input", "Een seizoen ligt tussen 0 en 1000", field="seasons")
    return sorted(set(values))


@router.get("/profiles", response_model=list[ProfileOut])
async def profiles(admin: AdminUser):
    return [ProfileOut(key=k, name=p.name, cutoff=p.cutoff) for k, p in BUILTIN.items()]


@router.get("/lookup", response_model=list[CandidateOut])
async def lookup(
    admin: AdminUser,
    session: Session,
    settings: Config,
    kind: Kind,
    q: Annotated[str, Query(min_length=1, max_length=200)],
):
    tmdb_provider = await provider(session, settings)
    try:
        found = await tmdb_provider.search(kind, q, None, "nl-NL")
    except (ProviderAuth, ProviderRate, ProviderUnavailable) as exc:
        raise _provider_error(exc) from None
    out = []
    for c in found:
        _, number = tmdb.parse_id(c.id)
        out.append(
            CandidateOut(
                tmdb_id=number,
                kind=kind,
                title=c.title,
                year=c.year,
                overview=c.overview,
                poster=c.poster,
            )
        )
    return out


@router.get("/wanted", response_model=list[WantedOut])
async def wanted(admin: AdminUser, session: Session):
    rows = await session.scalars(select(ArrWanted).order_by(ArrWanted.title))
    return [_out(r) for r in rows]


@router.post("/wanted", status_code=201, response_model=WantedOut)
async def add(body: WantedIn, admin: AdminUser, session: Session, settings: Config):
    _check_profile(body.profile)
    library = await session.get(MediaLibrary, body.library_id)
    if library is None:
        raise fail(404, "not_found", "Die bibliotheek bestaat niet")
    if library.kind != LIBRARY_KIND[body.kind]:
        raise fail(
            422,
            "arr_library_kind",
            "Een film hoort in een bibliotheek met films, een serie in een met series",
        )
    exists = await session.scalar(
        select(ArrWanted.id).where(
            ArrWanted.library_id == library.id,
            ArrWanted.kind == body.kind,
            ArrWanted.tmdb_id == body.tmdb_id,
        )
    )
    if exists is not None:
        raise fail(409, "arr_wanted_exists", "Deze titel volg je al in die bibliotheek")
    tmdb_provider = await provider(session, settings)
    tmdb_kind = "movie" if body.kind == "movie" else "tv"
    try:
        details = await tmdb_provider.details(body.kind, f"{tmdb_kind}:{body.tmdb_id}", "nl-NL")
    except (ProviderAuth, ProviderRate, ProviderUnavailable) as exc:
        raise _provider_error(exc) from None
    row = ArrWanted(
        kind=body.kind,
        tmdb_id=body.tmdb_id,
        imdb_id=details.ids.get("imdb"),
        title=details.title,
        year=details.year,
        runtime_min=details.runtime_min,
        library_id=library.id,
        profile=body.profile,
        seasons=_seasons(body.seasons) if body.kind == "show" else None,
        monitored=True,
        added_by=admin.id,
    )
    session.add(row)
    await session.commit()
    return _out(row)


async def _get(session, wanted_id: uuid.UUID) -> ArrWanted:
    row = await session.get(ArrWanted, wanted_id)
    if row is None:
        raise fail(404, "not_found", "Die titel volg je niet")
    return row


@router.patch("/wanted/{wanted_id}", response_model=WantedOut)
async def change(wanted_id: uuid.UUID, body: WantedPatch, admin: AdminUser, session: Session):
    row = await _get(session, wanted_id)
    if body.profile is not None:
        _check_profile(body.profile)
        row.profile = body.profile
    if row.kind == "show":
        if body.all_seasons:
            row.seasons = None
        elif body.seasons is not None:
            row.seasons = _seasons(body.seasons)
    if body.monitored is not None:
        row.monitored = body.monitored
    await session.commit()
    return _out(row)


@router.delete("/wanted/{wanted_id}", status_code=204)
async def remove(wanted_id: uuid.UUID, admin: AdminUser, session: Session):
    row = await _get(session, wanted_id)
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


@router.get("/calendar", response_model=list[CalendarEntry])
async def calendar(
    admin: AdminUser,
    session: Session,
    settings: Config,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
):
    """De afleveringen van gevolgde series die de komende dagen uitkomen (vandaag inbegrepen).
    Per serie het laatste seizoen (en het volgende als dat er al is), want daar staat wat komt."""
    rows = list(
        await session.scalars(
            select(ArrWanted)
            .where(ArrWanted.kind == "show", ArrWanted.monitored.is_(True))
            .order_by(ArrWanted.title)
            .limit(MAX_CALENDAR_SHOWS)
        )
    )
    if not rows:
        return []
    tmdb_provider = await provider(session, settings)
    today = dt.date.today()
    until = today + dt.timedelta(days=days)
    out: list[CalendarEntry] = []
    for row in rows:
        show_id = f"tv:{row.tmdb_id}"
        try:
            details = await tmdb_provider.details("show", show_id, "nl-NL")
            last = details.seasons or 0
            for n in sorted({max(last, 1), last + 1} if last else {1}):
                if row.seasons is not None and n not in row.seasons:
                    continue
                try:
                    episodes = await tmdb_provider.season(show_id, n, "nl-NL")
                except ProviderUnavailable:
                    continue  # het volgende seizoen bestaat (nog) niet
                for e in episodes:
                    if not e.aired:
                        continue
                    aired = dt.date.fromisoformat(e.aired)
                    if today <= aired <= until:
                        out.append(
                            CalendarEntry(
                                wanted_id=row.id,
                                title=row.title,
                                season=e.season,
                                episode=e.episode,
                                episode_title=e.title,
                                aired=aired,
                            )
                        )
        except (ProviderAuth, ProviderRate) as exc:
            raise _provider_error(exc) from None
        except ProviderUnavailable:
            continue  # één serie die niet lukt, houdt de rest niet tegen
    return sorted(out, key=lambda e: (e.aired, e.title, e.season, e.episode))
