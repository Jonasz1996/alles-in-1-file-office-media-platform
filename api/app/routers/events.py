"""Live gebeurtenissen voor de web-app en apps (Server-Sent Events).

De web-app leest deze stroom met fetch() en de Authorization-header, niet met EventSource:
zo komt het token nooit in een adres (en dus niet in logs of de geschiedenis).

    event: node.created
    data: {"id": "...", "parent_id": "..."}

Elke 20 seconden een `: ping`, zodat een verbinding die stilletjes wegviel opvalt. De stroom
stopt als het access-token verloopt (de client verbindt dan opnieuw met een vers token): zo
krijgt een ingetrokken sessie na hooguit één tokenduur geen gebeurtenissen meer.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app import events
from app.db import Session
from app.security import CurrentUser, access_claims

router = APIRouter(prefix="/api/v1", tags=["gebeurtenissen"])
PING = 20.0
RETRY_MS = 5000


async def _next(incoming: AsyncIterator[dict]) -> dict | None:
    return await anext(incoming, None)


def message(event: dict) -> str:
    data = json.dumps(event["data"], separators=(",", ":"), ensure_ascii=False)
    return f"event: {event['type']}\ndata: {data}\n\n"


async def stream(user_id, expires: float) -> AsyncIterator[str]:
    """Tot de hub sluit (de server stopt), de client weggaat of het token verloopt."""
    with events.hub.subscribe(user_id) as sub:
        incoming = aiter(sub)
        yield f"retry: {RETRY_MS}\n\n"
        pending: asyncio.Future | None = None
        try:
            while (left := expires - time.time()) > 0:
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
    user: CurrentUser, session: Session, claims: Annotated[dict, Depends(access_claims)]
) -> StreamingResponse:
    """text/event-stream met gebeurtenissen voor de ingelogde gebruiker."""
    user_id, expires = user.id, float(claims.get("exp", 0))
    await session.commit()  # de stroom kan lang openstaan: geen databaseverbinding vasthouden
    return StreamingResponse(
        stream(user_id, expires),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
