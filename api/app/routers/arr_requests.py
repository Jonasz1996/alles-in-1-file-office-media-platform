"""Verzoeken van gezinsleden: een film of serie vragen, een beheerder keurt goed (fase 11, stap 6).

GET    /api/v1/arr/requests/lookup?kind=movie&q=…  → [RequestCandidate] | 409 arr_tmdb_needed
GET    /api/v1/arr/requests?scope=mine|all         → [RequestOut] (all: alleen beheerders)
POST   /api/v1/arr/requests                        RequestIn → 201 RequestOut
                                                   | 409 arr_request_exists | 409 arr_wanted_exists
                                                   | 409 arr_no_library
POST   /api/v1/arr/requests/{id}/approve           ApproveIn → RequestOut (beheerder) | 409
POST   /api/v1/arr/requests/{id}/decline           DeclineIn → RequestOut (beheerder) | 409
DELETE /api/v1/arr/requests/{id}                   → 204 (eigen wachtend verzoek, of beheerder)
GET    /api/v1/admin/arr/requests                  → RequestSettings
PUT    /api/v1/admin/arr/requests                  RequestSettings → RequestSettings

Voor iedereen met een account behalve gasten. Een verzoek wacht op een beheerder, tenzij de
vrager zelf beheerder is of op de lijst "vanzelf goedkeuren" staat (standaard staat niemand
erop; per persoon aan te zetten). Goedkeuren is volgen (arr_wanted.create_wanted) in de
bibliotheek die de beheerder kiest, of de standaard (de instelling, anders de oudste van die
soort). Wie vraagt, krijgt een live melding (`arr.request`) als zijn verzoek goedgekeurd,
afgewezen of beschikbaar is; de beheerders krijgen er een bij elk nieuw verzoek.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import events
from app.arr import settings as arr_settings
from app.arr.score import BUILTIN
from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.media.providers import tmdb
from app.media.providers.base import ProviderAuth, ProviderRate, ProviderUnavailable
from app.models import ArrRequest, ArrWanted, MediaLibrary, User
from app.routers import arr_wanted
from app.security import AdminUser, CurrentUser

router = APIRouter(prefix="/api/v1/arr/requests", tags=["mediabeheer"])
admin_router = APIRouter(prefix="/api/v1/admin/arr/requests", tags=["beheer"])
Config = Annotated[Settings, Depends(get_settings)]
Kind = Literal["movie", "show"]
State = Literal["pending", "approved", "declined", "available"]
MAX_OPEN = 20  # verzoeken die per persoon tegelijk mogen wachten


class RequestCandidate(BaseModel):
    tmdb_id: int
    kind: Kind
    title: str
    year: int | None
    overview: str | None
    poster: str | None
    # none: nog niets; requested: iemand vroeg het al; following: de app volgt hem al
    status: Literal["none", "requested", "following"]


class RequestIn(BaseModel):
    kind: Kind
    tmdb_id: int = Field(ge=1)
    seasons: list[int] | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class ApproveIn(BaseModel):
    library_id: uuid.UUID | None = None
    profile: str | None = None


class DeclineIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RequestOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    tmdb_id: int
    title: str
    year: int | None
    seasons: list[int] | None
    note: str | None
    state: State
    reason: str | None
    user_id: uuid.UUID
    user_name: str
    created_at: dt.datetime
    decided_at: dt.datetime | None


class RequestSettings(BaseModel):
    auto: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    profile: str = "hd-1080p"
    movies: uuid.UUID | None = None
    shows: uuid.UUID | None = None


def _no_guest(user: User) -> None:
    if user.role == "guest":
        raise fail(403, "forbidden", "Een gast kan geen verzoek doen")


async def _out(session, row: ArrRequest) -> RequestOut:
    user = await session.get(User, row.user_id)
    return RequestOut(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        tmdb_id=row.tmdb_id,
        title=row.title,
        year=row.year,
        seasons=row.seasons,
        note=row.note,
        state=row.state,  # type: ignore[arg-type]
        reason=row.reason,
        user_id=row.user_id,
        user_name=(user.display_name or user.email) if user is not None else "?",
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


async def _admins(session) -> list[uuid.UUID]:
    return list(await session.scalars(select(User.id).where(User.role == "admin")))


def _tell(user_ids, row: ArrRequest) -> None:
    events.publish(
        user_ids,
        "arr.request",
        {"id": str(row.id), "state": row.state, "title": row.title, "kind": row.kind},
    )


async def default_library(session, conf: arr_settings.RequestConf, kind: str) -> uuid.UUID:
    """De bibliotheek voor een goedgekeurd verzoek: de instelling, anders de oudste."""
    chosen = conf.movies if kind == "movie" else conf.shows
    want = arr_wanted.LIBRARY_KIND[kind]
    if chosen:
        lib = await session.get(MediaLibrary, uuid.UUID(chosen))
        if lib is not None and lib.kind == want:
            return lib.id
    found = await session.scalar(
        select(MediaLibrary.id)
        .where(MediaLibrary.kind == want)
        .order_by(MediaLibrary.created_at)
        .limit(1)
    )
    if found is None:
        raise fail(
            409, "arr_no_library", "Er is nog geen bibliotheek voor films of series om in te zetten"
        )
    return found


async def _approve(
    session,
    settings: Settings,
    row: ArrRequest,
    by: uuid.UUID,
    library_id: uuid.UUID | None = None,
    profile: str | None = None,
) -> None:
    conf = await arr_settings.load_requests(session)
    library_id = library_id or await default_library(session, conf, row.kind)
    existing = await session.scalar(
        select(ArrWanted).where(
            ArrWanted.library_id == library_id,
            ArrWanted.kind == row.kind,
            ArrWanted.tmdb_id == row.tmdb_id,
        )
    )
    if existing is None:
        existing = await arr_wanted.create_wanted(
            session,
            settings,
            kind=row.kind,
            tmdb_id=row.tmdb_id,
            library_id=library_id,
            profile=profile or conf.profile,
            seasons=row.seasons,
            added_by=by,
        )
    row.state = "approved"
    row.wanted_id = existing.id
    row.decided_by = by
    row.decided_at = dt.datetime.now(dt.UTC)
    row.reason = None
    await session.commit()


@router.get("/lookup", response_model=list[RequestCandidate])
async def lookup(
    user: CurrentUser,
    session: Session,
    settings: Config,
    kind: Kind,
    q: Annotated[str, Query(min_length=1, max_length=200)],
):
    _no_guest(user)
    tmdb_provider = await arr_wanted.provider(session, settings)
    try:
        found = await tmdb_provider.search(kind, q, None, "nl-NL")
    except (ProviderAuth, ProviderRate, ProviderUnavailable) as exc:
        raise arr_wanted._provider_error(exc) from None
    following = set(await session.scalars(select(ArrWanted.tmdb_id).where(ArrWanted.kind == kind)))
    requested = set(
        await session.scalars(
            select(ArrRequest.tmdb_id).where(
                ArrRequest.kind == kind, ArrRequest.state.in_(("pending", "approved"))
            )
        )
    )
    out = []
    for c in found:
        _, number = tmdb.parse_id(c.id)
        status = (
            "following" if number in following else "requested" if number in requested else "none"
        )
        out.append(
            RequestCandidate(
                tmdb_id=number,
                kind=kind,
                title=c.title,
                year=c.year,
                overview=c.overview,
                poster=c.poster,
                status=status,
            )
        )
    return out


@router.get("", response_model=list[RequestOut])
async def requests(
    user: CurrentUser,
    session: Session,
    scope: Literal["mine", "all"] = "mine",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    _no_guest(user)
    query = select(ArrRequest).order_by(ArrRequest.created_at.desc()).limit(limit)
    if scope == "all":
        if user.role != "admin":
            raise fail(403, "forbidden", "Alleen een beheerder ziet alle verzoeken")
    else:
        query = query.where(ArrRequest.user_id == user.id)
    return [await _out(session, r) for r in await session.scalars(query)]


@router.post("", status_code=201, response_model=RequestOut)
async def ask(body: RequestIn, user: CurrentUser, session: Session, settings: Config):
    _no_guest(user)
    if await session.scalar(
        select(ArrWanted.id).where(ArrWanted.kind == body.kind, ArrWanted.tmdb_id == body.tmdb_id)
    ):
        raise fail(409, "arr_wanted_exists", "Deze titel wordt al gevolgd")
    mine = list(
        await session.scalars(
            select(ArrRequest).where(
                ArrRequest.user_id == user.id, ArrRequest.state.in_(("pending", "approved"))
            )
        )
    )
    if any(r.kind == body.kind and r.tmdb_id == body.tmdb_id for r in mine):
        raise fail(409, "arr_request_exists", "Dit heb je al gevraagd")
    if sum(r.state == "pending" for r in mine) >= MAX_OPEN:
        raise fail(409, "arr_request_limit", "Je hebt al veel verzoeken die wachten")
    tmdb_provider = await arr_wanted.provider(session, settings)
    tmdb_kind = "movie" if body.kind == "movie" else "tv"
    try:
        details = await tmdb_provider.details(body.kind, f"{tmdb_kind}:{body.tmdb_id}", "nl-NL")
    except (ProviderAuth, ProviderRate, ProviderUnavailable) as exc:
        raise arr_wanted._provider_error(exc) from None
    row = ArrRequest(
        user_id=user.id,
        kind=body.kind,
        tmdb_id=body.tmdb_id,
        title=details.title,
        year=details.year,
        seasons=arr_wanted._seasons(body.seasons) if body.kind == "show" else None,
        note=(body.note or "").strip() or None,
        state="pending",
    )
    session.add(row)
    await session.commit()
    conf = await arr_settings.load_requests(session)
    if user.role == "admin" or str(user.id) in conf.auto:
        await _approve(session, settings, row, user.id)
    else:
        _tell(await _admins(session), row)
    return await _out(session, row)


async def _get(session, request_id: uuid.UUID) -> ArrRequest:
    row = await session.get(ArrRequest, request_id)
    if row is None:
        raise fail(404, "not_found", "Dat verzoek bestaat niet")
    return row


@router.post("/{request_id}/approve", response_model=RequestOut)
async def approve(
    request_id: uuid.UUID, body: ApproveIn, admin: AdminUser, session: Session, settings: Config
):
    row = await _get(session, request_id)
    if row.state != "pending" and row.state != "declined":
        raise fail(409, "arr_request_decided", "Dit verzoek is al goedgekeurd")
    if body.profile is not None and body.profile not in BUILTIN:
        raise fail(422, "invalid_input", "Onbekend kwaliteitsprofiel", field="profile")
    await _approve(session, settings, row, admin.id, body.library_id, body.profile)
    _tell([row.user_id], row)
    return await _out(session, row)


@router.post("/{request_id}/decline", response_model=RequestOut)
async def decline(request_id: uuid.UUID, body: DeclineIn, admin: AdminUser, session: Session):
    row = await _get(session, request_id)
    if row.state != "pending":
        raise fail(409, "arr_request_decided", "Over dit verzoek is al beslist")
    row.state = "declined"
    row.reason = (body.reason or "").strip() or None
    row.decided_by = admin.id
    row.decided_at = dt.datetime.now(dt.UTC)
    await session.commit()
    _tell([row.user_id], row)
    return await _out(session, row)


@router.delete("/{request_id}", status_code=204)
async def withdraw(request_id: uuid.UUID, user: CurrentUser, session: Session):
    row = await _get(session, request_id)
    if user.role != "admin" and (row.user_id != user.id or row.state != "pending"):
        raise fail(404, "not_found", "Dat verzoek bestaat niet")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


@admin_router.get("", response_model=RequestSettings)
async def get_settings_(admin: AdminUser, session: Session):
    conf = await arr_settings.load_requests(session)
    return RequestSettings(
        auto=[uuid.UUID(x) for x in conf.auto],
        profile=conf.profile,
        movies=uuid.UUID(conf.movies) if conf.movies else None,
        shows=uuid.UUID(conf.shows) if conf.shows else None,
    )


@admin_router.put("", response_model=RequestSettings)
async def put_settings(body: RequestSettings, admin: AdminUser, session: Session):
    if body.profile not in BUILTIN:
        raise fail(422, "invalid_input", "Onbekend kwaliteitsprofiel", field="profile")
    for field, kind in (("movies", "movies"), ("shows", "shows")):
        lib_id = getattr(body, field)
        if lib_id is not None:
            lib = await session.get(MediaLibrary, lib_id)
            if lib is None or lib.kind != kind:
                raise fail(422, "arr_library_kind", "Die bibliotheek past niet", field=field)
    await arr_settings.store_requests(
        session,
        arr_settings.RequestConf(
            auto=tuple(dict.fromkeys(str(x) for x in body.auto)),
            profile=body.profile,
            movies=str(body.movies) if body.movies else None,
            shows=str(body.shows) if body.shows else None,
        ),
    )
    return body


async def mark_available(session, wanted_id: uuid.UUID) -> None:
    """Na een geslaagde import (engine): de goedgekeurde verzoeken voor deze titel zijn er."""
    rows = list(
        await session.scalars(
            select(ArrRequest).where(
                ArrRequest.wanted_id == wanted_id, ArrRequest.state == "approved"
            )
        )
    )
    if not rows:
        return
    for row in rows:
        row.state = "available"
    await session.commit()
    for row in rows:
        _tell([row.user_id], row)
