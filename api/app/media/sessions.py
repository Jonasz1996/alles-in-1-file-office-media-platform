"""Afspeelsessies in het geheugen (Keuze 21). Stub van M0; M3 bouwt ze.

De signaturen zijn frozen (§3.3). Zonder sessies is elke sid onbekend: guard geeft 410
media_session_gone (en 503 media_off als media uit staat), en de haken sluiten niets.
"""

from __future__ import annotations

import builtins
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, Request

from app.media import errors as media_errors
from app.media import runtime
from app.media.types import Grant, LoadtestFile, Plan, Probe, SessionView
from app.models import Node, User

SessionSource = Node | LoadtestFile


@dataclass
class Session:
    """Eén sessie. sid staat alleen in het pad, nooit in een log (Keuze 35)."""

    id: uuid.UUID
    sid: str
    user_id: uuid.UUID
    sha: str
    size: int
    plan: Plan
    node_id: uuid.UUID | None = None
    link_id: uuid.UUID | None = None
    family: uuid.UUID | None = None
    grant: Grant | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed: str | None = None  # de reden als de app hem sloot (de grafsteen)


class SessionTable:
    """Op MediaState.sessions (M3)."""

    def __init__(self) -> None:
        self.by_sid: dict[str, Session] = {}


async def create(
    user: User,
    source: SessionSource,
    probe: Probe,
    plan: Plan,
    grant: Grant | None,
    *,
    family: uuid.UUID | None,
    link_id: uuid.UUID | None = None,
    replace: str | None = None,
) -> Session:
    raise media_errors.off()


async def create_audio(
    user: User, node: Node, probe: Probe, *, family: uuid.UUID | None
) -> Session:
    raise media_errors.off()


async def guard(request: Request, sid: str) -> Session:
    """De poort van elk verzoek onder /api/v1/stream/ (regel 6)."""
    if not runtime.enabled(request.app):
        raise media_errors.off()
    raise media_errors.session_gone()


async def guarded(request: Request, sid: str) -> Session:
    """De dependency van elke router onder /api/v1/stream/ (regel 6): als dependency van de
    router zelf loopt hij vóór alles van de route, ook vóór de controle van de andere
    parameters. Een route die de sessie nodig heeft, neemt hem als parameter (Guarded); FastAPI
    roept guard dan één keer aan."""
    return await guard(request, sid)


Guarded = Annotated[Session, Depends(guarded)]


async def guarded_body(session: Session, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Per stuk (≤ 4 MB of 5 s) de sessie en de toegang nakijken (Keuze 21). De stub geeft de
    stukken door zolang de sessie open is."""
    async for chunk in chunks:
        if session.closed:
            return
        yield chunk


def close_user(user_id: uuid.UUID, keep_family: uuid.UUID | None = None) -> int:
    return 0


def close_login(family: uuid.UUID) -> int:
    return 0


def close_node(node_id: uuid.UUID) -> int:
    return 0


def close_sha(sha: str) -> int:
    return 0


def stop(session_id: uuid.UUID, reason: Literal["stopped", "revoked"]) -> bool:
    return False


def close_all() -> int:
    """Bij het stoppen van de app (F3, runtime.begin_stop): elke sessie dicht, ook de
    producenten (streams.close), zonder grafsteen voor de speler (na de herstart is de sid
    onbekend: 410, en de speler vraagt stil een nieuwe). Geeft het aantal."""
    return 0


def list() -> builtins.list[SessionView]:
    return []
