"""De stroom van een sessie: producent, knipper en playlists (Keuze 22–25). Stub van M0; M4
bouwt hem. Frozen signaturen (§3.3): tot M4 geeft open 503 media_off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.media import errors as media_errors
from app.media.types import Plan, StreamInfo

if TYPE_CHECKING:
    from app.media.sessions import Session


async def open(session: Session, plan: Plan) -> StreamInfo:
    raise media_errors.off()


async def close(session: Session) -> None:
    return None
