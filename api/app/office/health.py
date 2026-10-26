"""De enige gezondheidslus van office (Keuze 28). Schrijft OfficeState; de rest leest alleen.

Elke office_health_seconds: GET /healthcheck. Bij een overgang naar gezond, en daarna elke
10 minuten, ook version, license en de tokencontrole (version en een conversie zonder token
moeten error 6 en -8 geven). Eist DS geen tokens, dan is de toestand misconfigured: elke
ongetekende callback zou de app weigeren, en elke opslag zou stil een forgotten file worden.

Toestanden: starting (sinds de start van de app heeft DS nog nooit geantwoord, en nog geen
STARTING_SECONDS: DS bouwt na een start tot ±280 s fonts), ok, down, misconfigured (DS eist
geen tokens, of de instellingen kloppen niet). Gezond is pas gezond na een geslaagde grote
controle. Bij "weer gezond": convert.on_healthy() en recovery.kick(). De melding office_down
(down_too_long) komt na 2 minuten down; starting telt daar niet voor.
"""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from app.office import convert, engine, recovery, runtime
from app.office.state import OfficeState, office_state

log = logging.getLogger("platform.office")

DEEP_SECONDS = 600.0
STARTING_SECONDS = 300.0
# Zo lang onbereikbaar geeft de melding office_down (O5 leest down_too_long)
DOWN_NOTICE_SECONDS = 120.0


def down_too_long(state: OfficeState, now: datetime | None = None) -> bool:
    """Is DS langer dan DOWN_NOTICE_SECONDS onbereikbaar? Tijdens starting nog niet: dat
    duurt na een start van DS gewoon even (fonts); daarna wordt het down."""
    if state.health != "down" or state.down_since is None:
        return False
    now = now or datetime.now(UTC)
    return now - state.down_since >= timedelta(seconds=DOWN_NOTICE_SECONDS)


def _came_back() -> None:
    for hook in (convert.on_healthy, recovery.kick):
        try:
            hook()
        except Exception:  # noqa: BLE001 - een haak mag de lus nooit stoppen
            log.exception("office: haak bij weer gezond mislukt")


def _not_ok(state: OfficeState, now: datetime, was: str) -> None:
    if state.down_since is None:
        state.down_since = now
    starting = not state.ever_ok and now - state.checking_since < timedelta(
        seconds=STARTING_SECONDS
    )
    state.health = "starting" if starting else "down"
    state.deep_checked_at = None  # bij terugkomst altijd opnieuw controleren
    if was == "ok":
        log.warning("office: de document server is niet bereikbaar")


async def check_once(app, now: datetime | None = None) -> None:
    """Eén keer kijken en OfficeState bijwerken. now is in te spuiten (tests)."""
    state = office_state(app)
    now = now or datetime.now(UTC)
    settings = runtime.settings()
    if state.checking_since is None:
        state.checking_since = now
    current = engine.current()
    if current is None:
        state.problem = settings.office_problem()
        state.health = "misconfigured" if state.problem else "off"
        return
    state.problem = None
    was = state.health
    try:
        ok = (await current.health(deep=False)).ok
    except Exception:  # noqa: BLE001 - elke fout is "niet gezond"
        ok = False
    # Terug van weg (of de eerste keer), of de vorige grote controle is 10 minuten oud
    returning = was not in ("ok", "misconfigured")
    stale = state.deep_checked_at is None or now - state.deep_checked_at >= timedelta(
        seconds=DEEP_SECONDS
    )
    due = ok and (returning or stale)
    deep, enforced = None, None
    if due:
        try:
            deep = await current.health(deep=True)
            enforced = await current.enforces_tokens()
        except Exception:  # noqa: BLE001 - de volgende keer opnieuw
            log.warning("office: versie, licentie of tokencontrole mislukt", exc_info=True)
            deep = None
            if was != "ok":
                ok = False  # pas gezond na een geslaagde controle
        else:
            if not deep.ok:  # intussen weer weg
                ok, deep = False, None
    if not ok:
        _not_ok(state, now, was)
        return
    if deep is not None:
        state.version, state.license_connections = deep.version, deep.limit
        state.tokens_enforced = enforced
        state.deep_checked_at = now
        if enforced is False:
            if was != "misconfigured":
                log.error(
                    "office: de document server eist geen tokens (JWT_SECRET of image "
                    "verkeerd); bewerken en conversie staan uit"
                )
            state.health = "misconfigured"
            state.down_since = None
            state.ever_ok = True  # hij antwoordde: weg is daarna down, niet starting
            return
    if was == "misconfigured" and deep is None:
        return  # blijft zo tot de volgende geslaagde controle
    state.health = "ok"
    state.down_since = None
    state.ever_ok = True
    if was != "ok":
        log.info("office: de document server is gezond (versie %s)", state.version)
        _came_back()


async def _loop(app) -> None:
    while True:
        try:
            await check_once(app)
        except Exception:  # noqa: BLE001 - de lus blijft draaien
            log.exception("office: gezondheidscontrole mislukt")
        await asyncio.sleep(runtime.settings().office_health_seconds)


async def start(app) -> None:
    """Start de lus als taak (de lifespan annuleert hem bij het stoppen)."""
    state = office_state(app)
    task = asyncio.create_task(_loop(app), name="office gezondheid")
    state.tasks.append(task)


async def stop(app) -> None:
    state = office_state(app)
    tasks, state.tasks = state.tasks, []
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError, Exception):
            await task
