"""De grenzen van een beeldvraag (bouwontwerp fase 6, X3; Keuze 78; X3b).

admit() van zoeken (app/search/limits.py) telt geen werk in een container; een beeldvraag krijgt
daarom zijn eigen grenzen, los van die van gewoon zoeken:

- **Voor de hele app**: hooguit PLATFORM_BEELD_INFLIGHT (1) tegelijk, met hooguit
  PLATFORM_BEELD_QUEUE (10) wachtenden; daarboven meteen 429 search_busy met Retry-After: 1.
- **Per gebruiker**: één tegelijk. Een nieuwe vraag stopt de vorige: wacht die nog, dan gaat ze
  weg met Replaced; loopt ze al (bij de container), dan breekt haar CancelScope af en sluit de
  verbinding, zodat de worker de opdracht afbreekt.
- **De snelheid per gebruiker**: hooguit RATE (2) per seconde, anders 429 rate_limited (niet met
  settings.rate_limits uit, zoals de andere limieten).

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

import anyio

from app import errors
from app.config import Settings

RATE = 2
WINDOW_S = 1.0
RETRY = {"Retry-After": "1"}


class Replaced(Exception):
    """Een nieuwere vraag van dezelfde gebruiker stopte deze."""


@dataclass
class _Mine:
    scope: anyio.CancelScope | None = None
    replaced: bool = False
    asked: deque[float] = field(default_factory=deque)


@dataclass
class _State:
    running: int = 0
    waiting: deque[asyncio.Future[None]] = field(default_factory=deque)
    users: dict[uuid.UUID, _Mine] = field(default_factory=dict)


_states: dict[int, _State] = {}


def _state() -> _State:
    loop = asyncio.get_running_loop()
    key = id(loop)
    found = _states.get(key)
    if found is None:
        _states.clear()
        found = _states[key] = _State()
    return found


def _rate(mine: _Mine, settings: Settings) -> None:
    if not settings.rate_limits:
        return
    at = time.monotonic()
    while mine.asked and at - mine.asked[0] > WINDOW_S:
        mine.asked.popleft()
    if len(mine.asked) >= RATE:
        raise errors.fail(
            429, "rate_limited", "Even rustig aan; probeer het zo opnieuw", headers=RETRY
        )
    mine.asked.append(at)


def _release(state: _State) -> None:
    """Een plek vrij: naar de eerste die nog wacht."""
    state.running -= 1
    while state.waiting:
        nxt = state.waiting.popleft()
        if not nxt.done():
            state.running += 1
            nxt.set_result(None)
            break


@asynccontextmanager
async def admit(user_id: uuid.UUID, settings: Settings) -> AsyncIterator[anyio.CancelScope]:
    """Een plek voor een beeldvraag. Geeft de CancelScope waarin de vraag loopt: een nieuwere
    vraag van dezelfde gebruiker breekt hem af (dan Replaced)."""
    state = _state()
    mine = state.users.setdefault(user_id, _Mine())
    _rate(mine, settings)
    if mine.scope is not None:
        mine.replaced = True
        mine.scope.cancel()
    scope = anyio.CancelScope()
    mine.scope = scope
    mine.replaced = False
    turn: asyncio.Future[None] | None = None
    if state.running >= settings.beeld_inflight:
        if len(state.waiting) >= settings.beeld_queue:
            if mine.scope is scope:
                mine.scope = None
            raise errors.fail(
                429, "search_busy", "Het is even druk; probeer het zo opnieuw", headers=RETRY
            )
        turn = asyncio.get_running_loop().create_future()
        state.waiting.append(turn)
    try:
        if turn is not None:
            with scope:
                await turn
            if scope.cancelled_caught:
                if turn.done() and not turn.cancelled():
                    _release(state)  # de plek kwam net op tijd: meteen door naar de volgende
                raise Replaced
        else:
            state.running += 1
        try:
            with scope:
                yield scope
            if scope.cancelled_caught:
                raise Replaced
        finally:
            _release(state)
    finally:
        if turn is not None and not turn.done():
            turn.cancel()
            if turn in state.waiting:
                state.waiting.remove(turn)
        if mine.scope is scope:
            mine.scope = None


def reset() -> None:
    """Alleen voor tests."""
    _states.clear()
