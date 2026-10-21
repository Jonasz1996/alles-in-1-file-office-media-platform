"""Live gebeurtenissen voor de web-app en apps (Server-Sent Events).

De web-app leest deze stroom met fetch() en de Authorization-header, niet met EventSource:
zo komt het token nooit in een adres (en dus niet in logs of de geschiedenis).

    event: node.created
    data: {"id": "...", "parent_id": "..."}

Elke 20 seconden een `: ping`, zodat een verbinding die stilletjes wegviel opvalt. De stroom
stopt als het access-token verloopt (de client verbindt dan opnieuw met een vers token), en
binnen 20 seconden nadat de login is ingetrokken (uitgelogd op een ander apparaat, nieuw
wachtwoord, 2FA gewijzigd) of het account is geblokkeerd, ook als dat via `app.cli` in een
ander proces gebeurde.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import events
from app.config import Settings, get_settings
from app.db import Session, get_engine
from app.models import User
from app.security import CurrentUser, access_claims, family_revoked

router = APIRouter(prefix="/api/v1", tags=["gebeurtenissen"])
PING = 20.0
RETRY_MS = 5000


async def _next(incoming: AsyncIterator[dict]) -> dict | None:
    return await anext(incoming, None)


def message(event: dict) -> str:
    data = json.dumps(event["data"], separators=(",", ":"), ensure_ascii=False)
    return f"event: {event['type']}\ndata: {data}\n\n"


async def still_logged_in(
    settings: Settings, family: uuid.UUID | None, user_id: uuid.UUID | None = None
) -> bool:
    """Bestaat de gebruiker nog, is hij niet geblokkeerd en is de login niet ingetrokken? Een
    eigen, korte sessie: de stroom houdt geen databaseverbinding vast."""
    if family is None and user_id is None:
        return True
    async with async_sessionmaker(get_engine(settings))() as session:
        if user_id is not None:
            active = await session.scalar(
                select(User.id).where(User.id == user_id, User.disabled_at.is_(None))
            )
            if active is None:
                return False
        return family is None or not await family_revoked(session, family)


async def stream(
    user_id, expires: float, settings: Settings, family: uuid.UUID | None = None
) -> AsyncIterator[str]:
    """Tot de hub sluit (de server stopt), de client weggaat, het token verloopt of de login
    is ingetrokken of het account geblokkeerd (dat wordt elke PING seconden gecontroleerd)."""
    with events.hub.subscribe(user_id) as sub:
        incoming = aiter(sub)
        yield f"retry: {RETRY_MS}\n\n"
        pending: asyncio.Future | None = None
        check = time.monotonic() + PING
        try:
            while (left := expires - time.time()) > 0:
                if time.monotonic() >= check:
                    if not await still_logged_in(settings, family, user_id):
                        return
                    check = time.monotonic() + PING
                # Op dezelfde volgende gebeurtenis blijven wachten na een ping: het wachten
                # zelf afbreken zou de inschrijving afsluiten
                pending = pending or asyncio.ensure_future(_next(incoming))
                done, _ = await asyncio.wait({pending}, timeout=min(PING, left))
                if not done:
                    yield ": ping\n\n"
                    continue
                event, pending = pending.result(), None
                if event is None:
                    return
                yield message(event)
        finally:
            if pending is not None:
                pending.cancel()


@router.get("/events")
async def live(
    user: CurrentUser,
    session: Session,
    claims: Annotated[dict, Depends(access_claims)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """text/event-stream met gebeurtenissen voor de ingelogde gebruiker."""
    user_id, expires = user.id, float(claims.get("exp", 0))
    family = uuid.UUID(claims["fam"]) if claims.get("fam") else None
    await session.commit()  # de stroom kan lang openstaan: geen databaseverbinding vasthouden
    return StreamingResponse(
        stream(user_id, expires, settings, family),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
