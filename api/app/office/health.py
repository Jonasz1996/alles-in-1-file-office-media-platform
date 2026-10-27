"""De enige gezondheidslus van office (Keuze 28). Schrijft OfficeState; de rest leest alleen.

Elke office_health_seconds: GET /healthcheck. Bij een overgang naar gezond, en daarna elke
10 minuten, ook version, license en de tokencontrole (version en een conversie zonder token
moeten error 6 en -8 geven). Eist DS geen tokens, dan is de toestand misconfigured: elke
ongetekende callback zou de app weigeren, en elke opslag zou stil een forgotten file worden.

Toestanden: starting (sinds de start van de app heeft DS nog nooit geantwoord, en nog geen
STARTING_SECONDS; of DS leeft maar is settling: zijn font-herstart na een start komt nog,
hooguit SETTLING_SECONDS), ok, down, misconfigured (DS eist geen tokens, DS weigert onze
getekende commando's omdat het office-geheim niet klopt met zijn JWT_SECRET, of de instellingen
kloppen niet). Gezond is pas gezond na een geslaagde grote controle en als DS niet meer
settling is: die herstart gooit open sessies weg zonder callback (spike, extra bevinding 1),
dus bewerken pas daarna. Settling kijkt elke ronde, ook als de toestand ok is: een DS die
tussen twee rondes opnieuw opkwam, telt zo ook. Bij "weer gezond": convert.on_healthy() en
recovery.kick(). De melding office_down (down_too_long) komt na 2 minuten down; starting telt
daar niet voor (down_since begint pas als de toestand down wordt). Een fout geheim is geen
uitval: geen down_since, en elke ronde opnieuw de grote controle, zodat een goed geheim (een
nieuwe DS) meteen telt.
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
# Zo lang wacht de app hooguit op de font-herstart (gemeten: 2 à 3 minuten na de start, op een
# drukke machine 713 s); daarna mag bewerken toch, met een waarschuwing in het log
SETTLING_SECONDS = 900.0
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
    starting = not state.ever_ok and now - state.checking_since < timedelta(
        seconds=STARTING_SECONDS
    )
    state.health = "starting" if starting else "down"
    state.settling_since = None  # bij terugkomst opnieuw tellen
    if starting:
        state.down_since = None  # starten telt niet als weg (fonts)
    elif state.down_since is None:
        state.down_since = now
    state.deep_checked_at = None  # bij terugkomst altijd opnieuw controleren
    if was == "ok":
        log.warning("office: de document server is niet bereikbaar")


def _settling(state: OfficeState, settling: bool, now: datetime, was: str) -> bool:
    """Komt de font-herstart van DS nog? Dan starting (openen geeft 503 office_unavailable,
    conversies wachten, down_since loopt niet), hooguit SETTLING_SECONDS na de eerste keer
    dat de app het zag; daarna False, met een waarschuwing."""
    if not settling:
        state.settling_since = None
        return False
    if state.settling_since is None:
        state.settling_since = now
        log.info(
            "office: de document server leeft, maar herstart zich nog voor de fonts; "
            "bewerken wacht daarop"
        )
    if now - state.settling_since < timedelta(seconds=SETTLING_SECONDS):
        state.health = "starting"
        state.down_since = None
        state.ever_ok = True  # hij antwoordde: weg is daarna down, niet starting
        return True
    if was != "ok":
        log.warning(
            "office: de font-herstart van de document server is na %d minuten nog niet "
            "gezien; bewerken staat toch aan",
            SETTLING_SECONDS // 60,
        )
    return False


SECRET_PROBLEM = (
    "PLATFORM_OFFICE_SECRET klopt niet met JWT_SECRET van de document server: DS weigert "
    "onze getekende commando's (error 6)"
)


def _wrong_secret(state: OfficeState, was: str, problem: str | None) -> None:
    """DS leeft, maar weigert ons token: een fout in de instellingen (Keuze 10 en 28).
    problem is de reden van de vorige ronde: alleen bij een overgang een regel in het log."""
    if was != "misconfigured" or problem != SECRET_PROBLEM:
        log.error("office: %s; bewerken en conversie staan uit", SECRET_PROBLEM)
    state.health = "misconfigured"
    state.problem = SECRET_PROBLEM
    state.down_since = None
    state.ever_ok = True  # hij antwoordde: weg is daarna down, niet starting
    state.version = state.license_connections = state.tokens_enforced = None
    state.deep_checked_at = None  # de volgende ronde opnieuw


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
    was = state.health
    previous_problem, state.problem = state.problem, None
    try:
        shallow = await current.health(deep=False)
    except Exception:  # noqa: BLE001 - elke fout is "niet gezond"
        shallow = engine.Health(ok=False)
    ok = shallow.ok
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
        except engine.TokenError:
            _wrong_secret(state, was, previous_problem)
            return
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
    if _settling(state, shallow.settling, now, was):
        return
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
