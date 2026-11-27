"""Wie kijkt er: profielen binnen een account en het kinderslot (fase 11, stap 7).

Een account heeft altijd zijn eigen profiel (id ""), en kan er tot `MAX_PROFILES` bij maken
(bijv. "Kinderen"). Elk profiel heeft zijn eigen voortgang en gezien (playback_state.profile).

- **Kiezen:** POST …/profiles/{id}/select geeft een token, met de pincode als het profiel er
  een heeft. Het toestel stuurt dat token mee in de kop `X-Media-Profile`; de server kijkt het
  na (HMAC met het geheim van de app, en de pincode-hash erin: een nieuwe pincode maakt oude
  tokens ongeldig).
- **Zonder (geldig) token** kijkt het account zelf, tenzij het account zelf een pincode heeft:
  dan het strengste profiel (zo kan een kind niet terug door het token weg te gooien).
- **Kinderslot:** `max_age` is de hoogste kijkwijzer die een profiel mag zien. Het account zelf
  kan een grens krijgen van een beheerder (voor een kinderaccount); die geldt dan altijd.
- **Beheren** (profielen maken, wijzigen, weghalen) mag alleen vanuit een profiel zonder grens.

`resolve` is een dependency op de media-routers: die zet `current()` voor de Reader en de
voortgang van deze vraag.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import Session
from app.models import MediaProfile, User
from app.security import CurrentUser

HEADER = "X-Media-Profile"
MAX_PROFILES = 8
MAIN = ""
PIN_TRIES = 5  # verkeerde pincodes per account ...
PIN_WINDOW_S = 300.0  # ... in zoveel seconden; daarna even niet


@dataclass(frozen=True)
class Viewer:
    profile: str = MAIN
    max_age: int | None = None

    @property
    def restricted(self) -> bool:
        return self.max_age is not None


_current: ContextVar[Viewer | None] = ContextVar("media_viewer", default=None)


def current() -> Viewer:
    return _current.get() or Viewer()


def use(viewer: Viewer) -> None:
    _current.set(viewer)


def signature(settings: Settings, user_id: uuid.UUID, profile: MediaProfile | None, pid: str):
    pin = (profile.pin_hash if profile is not None else None) or ""
    message = f"media-profile|{user_id}|{pid}|{pin}".encode()
    return hmac.new(settings.secret().encode(), message, hashlib.sha256).hexdigest()[:32]


def token(settings: Settings, user_id: uuid.UUID, pid: str, profile: MediaProfile | None) -> str:
    return f"{pid}.{signature(settings, user_id, profile, pid)}"


async def profiles(session: AsyncSession, user_id: uuid.UUID) -> dict[str, MediaProfile]:
    rows = await session.scalars(select(MediaProfile).where(MediaProfile.user_id == user_id))
    return {p.id: p for p in rows}


def strictest(found: dict[str, MediaProfile]) -> str:
    """Het profiel met de laagste grens (zonder grenzen: het account zelf)."""
    limited = [p for p in found.values() if p.id != MAIN and p.max_age is not None]
    if not limited:
        return MAIN
    return min(limited, key=lambda p: (p.max_age, p.created_at, p.id)).id


def pick(
    settings: Settings, user_id: uuid.UUID, found: dict[str, MediaProfile], raw: str | None
) -> Viewer:
    main = found.get(MAIN)
    chosen: str | None = None
    if raw and "." in raw and len(raw) <= 80:
        pid, sig = raw.rsplit(".", 1)
        if (pid == MAIN or pid in found) and hmac.compare_digest(
            sig, signature(settings, user_id, found.get(pid), pid)
        ):
            chosen = pid
    if chosen is None:
        chosen = strictest(found) if main is not None and main.pin_hash else MAIN
    profile = found.get(chosen)
    age = profile.max_age if profile is not None else None
    if main is not None and main.max_age is not None:
        # De grens van een beheerder op het account geldt voor elk profiel
        age = main.max_age if age is None else min(age, main.max_age)
    return Viewer(profile=chosen, max_age=age)


async def resolve(
    request: Request,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Viewer:
    """Dependency: het profiel van deze vraag (en current() voor de rest ervan)."""
    found = await profiles(session, user.id)
    viewer = pick(settings, user.id, found, request.headers.get(HEADER)) if found else Viewer()
    use(viewer)
    return viewer


# Pincodes: een eenvoudige rem per account (in het geheugen)
_tries: dict[uuid.UUID, list[float]] = {}


def pin_blocked(user: User) -> bool:
    now = time.monotonic()
    recent = [t for t in _tries.get(user.id, []) if now - t < PIN_WINDOW_S]
    _tries[user.id] = recent
    return len(recent) >= PIN_TRIES


def pin_failed(user: User) -> None:
    _tries.setdefault(user.id, []).append(time.monotonic())


def pin_reset() -> None:
    """Alleen voor tests."""
    _tries.clear()
