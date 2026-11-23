"""De stand van gezichten: aan of uit, voor het platform en per eigenaar (fase 6, X2b).

- **De instellingen.** De haken van prefs, search.hooks en people.hooks krijgen een sessie, geen
  Settings. configure(settings) onthoudt ze per database (de routes, de jobs en de start doen
  dat), en settings_for(db) zoekt ze op via de engine van de sessie. Onbekend telt als uit.
- **Draait gezichten?** running(settings): de analyse aan (analyse_on: PLATFORM_ANALYSE_ENABLED,
  op PostgreSQL ook PLATFORM_ANALYSE_POSTGRES en de triggers van 0013) én een map met sleutels
  (PLATFORM_FACES_KEYS, Keuze 68). Anders komt er niets in de rij analyse en niets op schijf, en
  geven de routes van gezichten met de analyse uit 501 not_built("X2") zoals 0.8.0, met de
  analyse aan zonder sleutels 409 faces_unavailable: met ANALYSE=uit is gezichten onzichtbaar.
- **Het verbod** van de beheerder staat in Setting "search", veld faces_allowed (standaard aan).
- **De eigenaar** heeft gezichten aan in photo_pref.faces.
- **people.changed** hooguit één keer per 10 s per eigenaar; wat binnen die tijd komt, gaat aan
  het eind ervan (één keer).
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import FaceOwner, PhotoPref, Setting
from app.people import people as P

SEARCH = "search"  # Setting met faces_allowed (app/search/admin.py)
EVENT_GAP = 10.0  # people.changed hooguit zo vaak per eigenaar (s)

_by_url: dict[str, Settings] = {}
_last_event: dict[uuid.UUID, float] = {}
_pending_event: dict[uuid.UUID, asyncio.TimerHandle] = {}


def now() -> datetime:
    return datetime.now(UTC)


def _key(url: Any) -> str:
    # str() van een URL verbergt het wachtwoord ("***"): een URL dus zelf weergeven, anders
    # vond settings_for op PostgreSQL (met een wachtwoord in de URL) nooit iets
    found = url if isinstance(url, URL) else make_url(str(url))
    return found.render_as_string(hide_password=False)


def configure(settings: Settings) -> None:
    """Onthoud de Settings van deze database (voor de haken zonder Settings)."""
    _by_url[_key(settings.db_url)] = settings


def settings_for(db: Any) -> Settings | None:
    """De Settings van de database van deze sessie (AsyncSession of Session), of None."""
    bind = getattr(db, "bind", None)
    if bind is None:
        return None
    # Een Engine of AsyncEngine heeft url; een (Async)Connection heeft die via engine (zo zit een
    # sessie aan een verbinding vast, zoals in de tests)
    url = getattr(bind, "url", None) or getattr(getattr(bind, "engine", None), "url", None)
    if url is None:
        return None
    return _by_url.get(_key(url))


def running(settings: Settings | None) -> bool:
    """Gezichten draaien op dit platform: de analyse staat aan (analyse_on) en de map met sleutels
    (PLATFORM_FACES_KEYS) is er. Zonder die map kan geen vector versleuteld worden: dan is het
    "niet geïnstalleerd", niet een job die elke keer faalt."""
    if settings is None or not settings.analyse_on:
        return False
    keys = settings.faces_keys.strip()
    return bool(keys) and os.path.isdir(keys)


def reset() -> None:
    """Alleen voor tests."""
    _by_url.clear()
    _last_event.clear()
    for handle in _pending_event.values():
        handle.cancel()
    _pending_event.clear()


async def allowed(db: AsyncSession) -> bool:
    """De beheerder staat gezichten toe (Setting search.faces_allowed, standaard aan)."""
    row = await db.get(Setting, SEARCH, populate_existing=True)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    return value.get("faces_allowed", True) is not False


async def owner_on(db: AsyncSession, owner: uuid.UUID) -> bool:
    found = await db.scalar(select(PhotoPref.faces).where(PhotoPref.user_id == owner))
    return found == "aan"


async def owners_on(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(select(PhotoPref.user_id).where(PhotoPref.faces == "aan"))
    return list(rows)


async def owner_row(db: AsyncSession, owner: uuid.UUID) -> FaceOwner:
    """De rij van face_owner (nieuw als hij er niet is; nog niet gecommit)."""
    row = await db.get(FaceOwner, owner, populate_existing=True)
    if row is None:
        row = FaceOwner(owner_id=owner, run=0, wiping=0)
        db.add(row)
        await db.flush()
    return row


async def wiping(db: AsyncSession, owner: uuid.UUID) -> bool:
    found = await db.scalar(select(FaceOwner.wiping).where(FaceOwner.owner_id == owner))
    return bool(found)


def changed(owner: uuid.UUID) -> None:
    """people.changed naar de eigenaar, hooguit één keer per EVENT_GAP seconden. Zonder lopende
    event loop (een CLI) meteen."""
    at = time.monotonic()
    last = _last_event.get(owner)
    if last is None or at - last >= EVENT_GAP:
        _last_event[owner] = at
        P.changed(owner)
        return
    if owner in _pending_event:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    def fire() -> None:
        _pending_event.pop(owner, None)
        _last_event[owner] = time.monotonic()
        P.changed(owner)

    _pending_event[owner] = loop.call_later(last + EVENT_GAP - at, fire)
