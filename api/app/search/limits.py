"""Hoeveel zoekvragen tegelijk (bouwontwerp fase 6, §3.3; Keuze 13). Z0 legde de vorm vast; Z1
bouwt hem.

    async with admit(user):                       # of admit(user, "suggest", settings)
        ...

- **Voor de hele app**: hooguit settings.search_inflight vragen tegelijk (2), met hooguit
  settings.search_queue wachtenden (20); daarboven meteen 429 search_busy met Retry-After: 1.
  Zoeken en suggesties delen die grens: het zijn allebei vragen aan FTS. De app is één proces
  met één event loop; drie gebruikers die elk 10 vragen per seconde doen, legden anders alles
  stil (S12).
- **Per gebruiker**: hooguit één vraag tegelijk, apart voor zoeken en suggesties. Een nieuwe
  vraag stopt de vorige (die nog loopt of nog wacht) via haar Stop (deadline.STOP): elke grens
  van die vraag gaat dan meteen af, en ze geeft een lege, gedeeltelijke pagina. Dat past bij
  typen.
- **De snelheid per gebruiker**: hooguit 10 zoekvragen en 20 suggesties per seconde, anders
  429 rate_limited met Retry-After: 1.

Alles leeft in de event loop van de app; een nieuwe loop (zoals in de tests) begint leeg.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from app import errors
from app.config import Settings, get_settings
from app.models import User
from app.search.deadline import STOP, Stop

Kind = Literal["search", "suggest"]
# Per gebruiker per seconde (Keuze 13)
RATE: dict[Kind, int] = {"search": 10, "suggest": 20}
WINDOW_S = 1.0
RETRY = {"Retry-After": "1"}


@dataclass
class _Mine:
    """Eén gebruiker, één soort vraag: wie er loopt of wacht, en wanneer hij vroeg."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    latest: Stop | None = None
    asked: deque[float] = field(default_factory=deque)


@dataclass
class _State:
    loop: asyncio.AbstractEventLoop
    running: int = 0
    waiting: deque[asyncio.Future[None]] = field(default_factory=deque)
    users: dict[tuple[uuid.UUID, Kind], _Mine] = field(default_factory=dict)


_state: _State | None = None


def _now_state() -> _State:
    global _state
    loop = asyncio.get_running_loop()
    if _state is None or _state.loop is not loop:
        _state = _State(loop)
    return _state


def _rate(mine: _Mine, kind: Kind) -> None:
    now = time.monotonic()
    while mine.asked and mine.asked[0] <= now - WINDOW_S:
        mine.asked.popleft()
    if len(mine.asked) >= RATE[kind]:
        raise errors.fail(
            429, "rate_limited", "Even rustig aan; probeer het zo opnieuw", headers=RETRY
        )
    mine.asked.append(now)


async def _enter(state: _State, settings: Settings) -> None:
    """Een plek in de app: meteen, in de rij, of 429 search_busy als de rij vol is."""
    if state.running < settings.search_inflight and not state.waiting:
        state.running += 1
        return
    if len(state.waiting) >= settings.search_queue:
        raise errors.fail(429, "search_busy", "Zoeken is even te druk", headers=RETRY)
    turn: asyncio.Future[None] = state.loop.create_future()
    state.waiting.append(turn)
    try:
        await turn  # _leave geeft de plek door (running blijft gelijk)
    except asyncio.CancelledError:
        if turn.done() and not turn.cancelled():
            _leave(state)  # de plek was al van ons: geef hem door
        else:
            state.waiting.remove(turn)
        raise


def _leave(state: _State) -> None:
    while state.waiting:
        turn = state.waiting.popleft()
        if not turn.done():
            turn.set_result(None)
            return
    state.running -= 1


@asynccontextmanager
async def admit(
    user: User, kind: Kind = "search", settings: Settings | None = None
) -> AsyncIterator[None]:
    settings = settings or get_settings()
    state = _now_state()
    mine = state.users.setdefault((user.id, kind), _Mine())
    _rate(mine, kind)
    stop = Stop()
    if mine.latest is not None:
        mine.latest()  # de vorige vraag van deze gebruiker stopt
    mine.latest = stop
    token = STOP.set(stop)
    try:
        async with mine.lock:
            await _enter(state, settings)
            try:
                yield
            finally:
                _leave(state)
    finally:
        STOP.reset(token)
        if mine.latest is stop:
            mine.latest = None
