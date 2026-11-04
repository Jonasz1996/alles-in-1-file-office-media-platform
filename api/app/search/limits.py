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
  typen. Wacht de vorige nog in de rij, dan neemt de nieuwe haar plek in de rij over, en de
  vorige gaat weg met Replaced, zonder plek en zonder werk (review golf 1, H4: anders kwam de
  nieuwe achter iedereen die intussen in de rij kwam). In de rij staat zo nooit een gestopte
  vraag.
- **Wachten zonder verbinding**: wie in de rij komt (of wacht tot zijn vorige vraag weg is),
  roept eerst release aan (de router geeft session.commit mee): zo houdt niemand in de rij een
  verbinding uit de pool vast (dat zijn er 15; de rij is 20 lang). Wie meteen een plek heeft,
  houdt zijn verbinding: eerst gaf elke vraag hem terug en nam hem daarna opnieuw (commit, de
  rollback van de pool, en de ping bij het opnieuw nemen: ±6 keer heen en weer naar de thread
  van aiosqlite, ook zonder wachten; review golf 1, H2).
- **De snelheid per gebruiker**: hooguit 10 zoekvragen en 20 suggesties per seconde, anders
  429 rate_limited met Retry-After: 1. Niet met settings.rate_limits uit (PLATFORM_RATE_LIMITS=
  false, alleen voor een testserver), zoals de andere limieten: de meting van §4.4 stelt 200
  vragen na elkaar. De grens op tegelijk blijft.

Alles leeft in de event loop van de app; een nieuwe loop (zoals in de tests) begint leeg.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
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


class Replaced(Exception):
    """Een nieuwere vraag van dezelfde gebruiker nam de plek in de rij over; deze vraag is
    gestopt voor ze liep. De router geeft een lege, gedeeltelijke pagina."""


@dataclass
class _Place:
    """Een plek in de rij. turn krijgt True als de plek in de app vrij is, en False als een
    nieuwere vraag van dezelfde gebruiker de plek overnam (die wacht dan op een nieuwe turn)."""

    turn: asyncio.Future[bool]


@dataclass
class _Mine:
    """Eén gebruiker, één soort vraag: wie er loopt of wacht, en wanneer hij vroeg."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    latest: Stop | None = None
    asked: deque[float] = field(default_factory=deque)
    place: _Place | None = None  # de plek in de rij van de vraag die nu wacht


@dataclass
class _State:
    loop: asyncio.AbstractEventLoop
    running: int = 0
    waiting: deque[_Place] = field(default_factory=deque)
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


def _take_over(state: _State, mine: _Mine) -> asyncio.Future[bool] | None:
    """Wacht de vorige vraag van deze gebruiker nog in de rij, dan krijgt deze vraag die plek:
    een nieuwe turn op dezelfde plek, en de vorige hoort False (Replaced). Anders None."""
    place = mine.place
    if place is None or place.turn.done():
        return None
    turn: asyncio.Future[bool] = state.loop.create_future()
    old, place.turn = place.turn, turn
    old.set_result(False)
    return turn


Release = Callable[[], Awaitable[object]]


async def _enter(
    state: _State,
    settings: Settings,
    mine: _Mine,
    turn: asyncio.Future[bool] | None,
    release: Release | None,
) -> None:
    """Een plek in de app: meteen, in de rij (of op de plek die deze vraag overnam: turn), of
    429 search_busy als de rij vol is. Replaced als een nieuwere vraag de plek overnam."""
    if turn is None:
        if state.running < settings.search_inflight and not state.waiting:
            state.running += 1
            return
        if len(state.waiting) >= settings.search_queue:
            raise errors.fail(429, "search_busy", "Zoeken is even te druk", headers=RETRY)
        turn = state.loop.create_future()
        mine.place = _Place(turn)
        state.waiting.append(mine.place)
    await _wait(state, mine, turn, release)


async def _wait(
    state: _State, mine: _Mine, turn: asyncio.Future[bool], release: Release | None
) -> None:
    """Wacht op turn: True is een plek in de app (_leave gaf hem door, running blijft gelijk),
    False is Replaced. Eerst release (de verbinding terug). Gaat daarbij iets mis, of wordt de
    taak geannuleerd, dan gaat de plek weg (_drop): anders bleef hij in de rij, of kreeg hij
    de plek in de app en gaf hem nooit door."""
    try:
        if release is not None and not turn.done():
            await release()
        mine_now = await turn
    except BaseException:
        _drop(state, mine, turn)
        raise
    if mine.place is not None and mine.place.turn is turn:
        mine.place = None
    if not mine_now:
        raise Replaced


def _drop(state: _State, mine: _Mine, turn: asyncio.Future[bool]) -> None:
    """De vraag met turn valt weg (de browser sluit): had ze de plek in de app al, dan geeft ze
    hem door; wacht ze nog, dan gaat haar plek uit de rij. Nam een nieuwere vraag de plek over
    (False), dan niets."""
    place = mine.place
    ours = place is not None and place.turn is turn
    if turn.done() and not turn.cancelled():
        if turn.result():
            _leave(state)
    elif ours and place is not None:
        state.waiting.remove(place)
    if ours:
        mine.place = None


def _leave(state: _State) -> None:
    while state.waiting:
        place = state.waiting.popleft()
        if not place.turn.done():
            place.turn.set_result(True)
            return
    state.running -= 1


@asynccontextmanager
async def admit(
    user: User,
    kind: Kind = "search",
    settings: Settings | None = None,
    release: Release | None = None,
) -> AsyncIterator[None]:
    """release: wordt aangeroepen vlak voordat deze vraag moet wachten (op haar vorige vraag of
    in de rij), niet als ze meteen een plek heeft."""
    settings = settings or get_settings()
    state = _now_state()
    mine = state.users.setdefault((user.id, kind), _Mine())
    if settings.rate_limits:
        _rate(mine, kind)
    stop = Stop()
    if mine.latest is not None:
        mine.latest()  # de vorige vraag van deze gebruiker stopt
    mine.latest = stop
    turn = _take_over(state, mine)  # wacht ze nog in de rij: haar plek is voor deze vraag
    token = STOP.set(stop)
    try:
        try:
            if release is not None and mine.lock.locked():
                await release()
            await mine.lock.acquire()  # de vorige vraag van deze gebruiker is eerst weg
        except BaseException:
            if turn is not None:
                _drop(state, mine, turn)
            raise
        try:
            await _enter(state, settings, mine, turn, release)
            try:
                yield
            finally:
                _leave(state)
        finally:
            mine.lock.release()
    finally:
        STOP.reset(token)
        if mine.latest is stop:
            mine.latest = None
