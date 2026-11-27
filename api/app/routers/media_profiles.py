"""Profielen en kinderslot (fase 11, stap 7; de regels in app.media.viewer).

GET    /api/v1/media/profiles               → ProfilesOut (wie kijkt nu, en de profielen)
POST   /api/v1/media/profiles               ProfileIn → 201 ProfileOut | 403 media_profile_locked
                                            | 422 media_profile_limit
PATCH  /api/v1/media/profiles/{id}          ProfilePatch → ProfileOut (id "self": het account zelf)
DELETE /api/v1/media/profiles/{id}          → 204 (met zijn voortgang)
POST   /api/v1/media/profiles/{id}/select   SelectIn → SelectOut {token} | 403 media_profile_pin
                                            | 429 rate_limited
GET    /api/v1/admin/media/profiles         → [AccountLimit] (accounts met een grens)
PUT    /api/v1/admin/media/profiles/{user_id}  AdminLimitIn → 204 (de grens op een account)

Beheren mag alleen vanuit een profiel zonder grens; de grens van het account zelf zet alleen een
beheerder. Media uit: 503.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.media import runtime
from app.media import viewer as media_viewer
from app.models import MediaProfile, PlaybackState, User
from app.security import AdminUser, CurrentUser, check_password, new_password_hash

router = APIRouter(
    prefix="/api/v1/media/profiles",
    tags=["media"],
    dependencies=[Depends(media_viewer.resolve)],
)
admin_router = APIRouter(prefix="/api/v1/admin/media/profiles", tags=["admin"])

Config = Annotated[Settings, Depends(get_settings)]
Viewer = Annotated[media_viewer.Viewer, Depends(media_viewer.resolve)]
Name = Annotated[str, Field(min_length=1, max_length=40)]
Age = Annotated[int, Field(ge=0, le=18)]
Pin = Annotated[str, Field(pattern=r"^[0-9]{4,8}$")]


class ProfileOut(BaseModel):
    id: str
    name: str
    max_age: int | None = None
    has_pin: bool = False


class ProfilesOut(BaseModel):
    current: str
    max_age: int | None = None  # de grens die nu geldt
    can_manage: bool
    profiles: list[ProfileOut]


class ProfileIn(BaseModel):
    name: Name
    max_age: Age | None = None
    pin: Pin | None = None


class ProfilePatch(BaseModel):
    name: Name | None = None
    max_age: Age | None = None
    clear_age: bool = False  # true: geen grens meer
    pin: Pin | None = None
    clear_pin: bool = False


class SelectIn(BaseModel):
    pin: Annotated[str, Field(max_length=8)] | None = None


class SelectOut(BaseModel):
    token: str


class AdminLimitIn(BaseModel):
    max_age: Age | None = None


class AccountLimit(BaseModel):
    user_id: uuid.UUID
    max_age: int


SELF = "self"  # het account zelf in de API (intern "")


def _pid(value: str) -> str:
    return media_viewer.MAIN if value == SELF else value


def _api_id(pid: str) -> str:
    return SELF if pid == media_viewer.MAIN else pid


def _out(p: MediaProfile | None, pid: str, fallback: str) -> ProfileOut:
    if p is None:
        return ProfileOut(id=_api_id(pid), name=fallback)
    return ProfileOut(id=_api_id(p.id), name=p.name, max_age=p.max_age, has_pin=bool(p.pin_hash))


def _manage(viewer: media_viewer.Viewer, user: User) -> None:
    if viewer.restricted or user.role == "guest":
        raise fail(403, "media_profile_locked", "Profielen beheren kan niet vanuit dit profiel")


async def _get(session, user: User, pid: str) -> MediaProfile:
    found = await session.get(MediaProfile, (user.id, pid))
    if found is None:
        raise fail(404, "not_found", "Niet gevonden")
    return found


def _display(user: User) -> str:
    return (user.display_name or user.email.split("@")[0])[:40]


@router.get("", response_model=ProfilesOut)
async def list_profiles(user: CurrentUser, request: Request, session: Session, viewer: Viewer):
    runtime.require(request)
    found = await media_viewer.profiles(session, user.id)
    main = found.get(media_viewer.MAIN)
    items = [_out(main, media_viewer.MAIN, _display(user))]
    others = sorted(
        (p for p in found.values() if p.id != media_viewer.MAIN),
        key=lambda p: (p.created_at, p.id),
    )
    items += [_out(p, p.id, p.name) for p in others]
    return ProfilesOut(
        current=_api_id(viewer.profile),
        max_age=viewer.max_age,
        can_manage=not viewer.restricted and user.role != "guest",
        profiles=items,
    )


@router.post("", status_code=201, response_model=ProfileOut)
async def create(
    body: ProfileIn, user: CurrentUser, request: Request, session: Session, viewer: Viewer
):
    runtime.require(request)
    _manage(viewer, user)
    found = await media_viewer.profiles(session, user.id)
    if len([p for p in found if p != media_viewer.MAIN]) >= media_viewer.MAX_PROFILES:
        raise fail(422, "media_profile_limit", "Hooguit 8 profielen per account")
    row = MediaProfile(
        user_id=user.id,
        id=str(uuid.uuid4()),
        name=body.name.strip() or "?",
        max_age=body.max_age,
        pin_hash=await new_password_hash(body.pin) if body.pin else None,
    )
    session.add(row)
    await session.commit()
    return _out(row, row.id, row.name)


@router.patch("/{pid}", response_model=ProfileOut)
async def update(
    pid: str,
    body: ProfilePatch,
    user: CurrentUser,
    request: Request,
    session: Session,
    viewer: Viewer,
):
    runtime.require(request)
    pid = _pid(pid)
    _manage(viewer, user)
    if pid == media_viewer.MAIN:
        row = await session.get(MediaProfile, (user.id, pid))
        if row is None:
            row = MediaProfile(user_id=user.id, id=pid, name=_display(user))
            session.add(row)
        if body.max_age is not None or body.clear_age:
            raise fail(403, "media_profile_locked", "De grens van het account zet een beheerder")
    else:
        row = await _get(session, user, pid)
        if body.clear_age:
            row.max_age = None
        elif body.max_age is not None:
            row.max_age = body.max_age
    if body.name is not None:
        row.name = body.name.strip() or row.name
    if body.clear_pin:
        row.pin_hash = None
    elif body.pin:
        row.pin_hash = await new_password_hash(body.pin)
    await session.commit()
    return _out(row, row.id, row.name)


@router.delete("/{pid}", status_code=204)
async def remove(pid: str, user: CurrentUser, request: Request, session: Session, viewer: Viewer):
    runtime.require(request)
    pid = _pid(pid)
    _manage(viewer, user)
    if pid == media_viewer.MAIN:
        raise fail(422, "invalid_input", "Het eigen profiel blijft", field="id")
    row = await _get(session, user, pid)
    await session.execute(
        delete(PlaybackState).where(PlaybackState.user_id == user.id, PlaybackState.profile == pid)
    )
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


@router.post("/{pid}/select", response_model=SelectOut)
async def select_profile(
    pid: str,
    body: SelectIn,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Config,
):
    runtime.require(request)
    pid = _pid(pid)
    found = await media_viewer.profiles(session, user.id)
    if pid != media_viewer.MAIN and pid not in found:
        raise fail(404, "not_found", "Niet gevonden")
    row = found.get(pid)
    if row is not None and row.pin_hash:
        if media_viewer.pin_blocked(user):
            raise fail(429, "rate_limited", "Te vaak een verkeerde pincode; wacht even")
        if not body.pin or not await check_password(row.pin_hash, body.pin):
            media_viewer.pin_failed(user)
            raise fail(403, "media_profile_pin", "De pincode klopt niet")
    return SelectOut(token=media_viewer.token(settings, user.id, pid, row))


@admin_router.get("", response_model=list[AccountLimit])
async def admin_limits(admin: AdminUser, request: Request, session: Session):
    runtime.require(request)
    rows = await session.scalars(
        select(MediaProfile).where(
            MediaProfile.id == media_viewer.MAIN, MediaProfile.max_age.is_not(None)
        )
    )
    return [AccountLimit(user_id=r.user_id, max_age=r.max_age) for r in rows]


@admin_router.put("/{user_id}", status_code=204)
async def admin_limit(
    user_id: uuid.UUID, body: AdminLimitIn, admin: AdminUser, request: Request, session: Session
):
    """Een grens op het account zelf (een kinderaccount): geldt voor elk profiel ervan."""
    runtime.require(request)
    target = await session.get(User, user_id)
    if target is None:
        raise fail(404, "not_found", "Niet gevonden")
    row = await session.get(MediaProfile, (user_id, media_viewer.MAIN))
    if row is None:
        if body.max_age is None:
            return Response(status_code=204)
        row = MediaProfile(user_id=user_id, id=media_viewer.MAIN, name=_display(target))
        session.add(row)
    row.max_age = body.max_age
    await session.commit()
    return Response(status_code=204)
