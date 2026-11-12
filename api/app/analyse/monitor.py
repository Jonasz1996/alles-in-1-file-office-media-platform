"""Merkt de app of de analyse er is (fase 6, Keuze 59, §3.11). Een contract van A0; X2a neemt
het over (bouwontwerp §2.0).

Vier standen (app.search.types.AnalyseState):

- off: PLATFORM_ANALYSE_ENABLED staat niet aan. Dan start er geen taak en raakt de app de
  socket nooit aan; de rij analyse draait niet (jobs.active_lanes).
- down: met een reden uit client.REASONS. Een sleutel die niet deugt (key_missing, key_reused)
  zegt de app bij de start, zonder lus: de sleutel verandert pas met een nieuwe start. Zo ook
  no_sqlite: op PostgreSQL blijft de analyse uit (Settings.analyse_on; review A0, veilig F5).
- models_missing: caps meldt een model dat ontbreekt of anders is (reason: de namen).
- ok: de handdruk klopt en de modellen kloppen.

Elke INTERVAL s één keer caps, met hooguit wire.ANSWER_S voor een antwoord. Is de analyse down,
dan kijkt de lus vaker: na 1, 2, 4 … hooguit INTERVAL s. Elke overgang komt één keer in het log,
met analyse_problem(): nooit de sleutel, nooit een bericht.

status() is de stand van nu, voor jobs en routes (X2, X3). ready(names) zegt of precies die
modellen er zijn: groeperen heeft geen model nodig, gezichten en beeld elk hun eigen.

Voor een job in de rij analyse (§3.11, "Fouten"; Keuze 59): wait(names) vóór de opdracht en
deferral(fout) erna geven een jobs.Defer, en die telt geen poging:

    if (later := monitor.wait(FACES_MODELS)) is not None:
        raise later                      # down of off: 300 s; een model weg: 3600 s
    try:
        data = await client.call(...)
    except Exception as exc:
        if (later := monitor.deferral(exc)) is not None:
            raise later from None        # down 300 s, busy 30 s, model_missing 3600 s
        raise                            # timeout, unreadable …: de job beslist
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from app import jobs
from app.analyse import client, wire
from app.config import Settings
from app.search.types import AnalyseState

log = logging.getLogger("platform.analyse")

INTERVAL = 30.0
FIRST = 0.2  # snel na de start, daarna INTERVAL
RETRY_FIRST = 1.0  # down: na 1, 2, 4 … s opnieuw, hooguit INTERVAL

# Hoe lang een job van de rij analyse wacht, zonder poging (§3.11, "Fouten")
DEFER_DOWN = 300.0  # de analyse is er niet (down, of off)
DEFER_BUSY = 30.0  # het kind van de opdracht is bezig
DEFER_MODEL = 3600.0  # een model ontbreekt of klopt niet

TEXT = {
    "key_missing": "PLATFORM_ANALYSE_WORKER_KEY ontbreekt of is korter dan 32 tekens",
    "key_reused": (
        "PLATFORM_ANALYSE_WORKER_KEY is gelijk aan het geheim van de app of aan de sleutel van "
        "de mediaworker"
    ),
    "no_socket": "de socket van de analyse is er niet (draait de container analyse?)",
    "refused": "de analyse weigert de verbinding",
    "handshake": (
        "de analyse bewijst zich niet: een andere sleutel, een andere versie, of iets anders "
        "op de socket"
    ),
    "no_answer": "de analyse antwoordt niet binnen 5 s",
    "sandbox": "de afscherming van de container analyse klopt niet (de zelftest faalde)",
    "bad_answer": "de analyse geeft een antwoord op caps dat niet klopt",
    "no_sqlite": (
        "de analyse werkt alleen met SQLite: op PostgreSQL blijven gezichten en beeld uit "
        "(Keuze 76)"
    ),
}


@dataclass(frozen=True)
class AnalyseStatus:
    """De stand van de analyse. reason: bij down een sleutel van TEXT, bij models_missing de
    namen van de modellen (komma's). missing: de modellen die niet ok zijn."""

    state: AnalyseState
    reason: str | None = None
    missing: tuple[str, ...] = ()
    caps: wire.CapsData | None = None

    def ready(self, names: Iterable[str] = ()) -> bool:
        """De worker is er (ok of models_missing) en elk van deze modellen is ok."""
        if self.state not in ("ok", "models_missing"):
            return False
        return not set(names) & set(self.missing)

    def built(self, op: wire.Op) -> bool:
        """De worker kent deze opdracht (caps.built)."""
        return self.caps is not None and op.value in self.caps.built


OFF = AnalyseStatus("off")
NO_SQLITE = AnalyseStatus("down", "no_sqlite")  # Settings.analyse_on (review A0, veilig F5)
_status: AnalyseStatus = OFF
_task: asyncio.Task | None = None


def status() -> AnalyseStatus:
    """De stand van nu (OFF zolang er geen monitor loopt)."""
    return _status


def analyse_problem(found: AnalyseStatus | None = None) -> str | None:
    """Wat er aan de hand is, in het Nederlands, voor /beheer, het log en analyse-status; None
    bij ok."""
    found = _status if found is None else found
    if found.state == "ok":
        return None
    if found.state == "off":
        return "de analyse staat uit (PLATFORM_ANALYSE_ENABLED)"
    if found.state == "models_missing":
        return "een model ontbreekt of klopt niet: " + ", ".join(found.missing)
    return TEXT.get(found.reason or "", "de analyse is niet bereikbaar")


def wait(names: Iterable[str] = (), found: AnalyseStatus | None = None) -> jobs.Defer | None:
    """Vóór een opdracht: Defer(DEFER_DOWN) als de analyse er niet is (off of down),
    Defer(DEFER_MODEL) als een van deze modellen niet ok is, anders None."""
    found = _status if found is None else found
    if found.state in ("off", "down"):
        return jobs.Defer(DEFER_DOWN)
    if not found.ready(names):
        return jobs.Defer(DEFER_MODEL)
    return None


def deferral(exc: BaseException) -> jobs.Defer | None:
    """Na een fout van client: AnalyseDown is Defer(DEFER_DOWN), AnalyseBusy Defer(DEFER_BUSY),
    AnalyseFailed met model_missing Defer(DEFER_MODEL). Al het andere (timeout, unreadable …)
    is None: dan beslist de job (een poging, of failed)."""
    if isinstance(exc, client.AnalyseDown):
        return jobs.Defer(DEFER_DOWN)
    if isinstance(exc, client.AnalyseBusy):
        return jobs.Defer(DEFER_BUSY)
    if isinstance(exc, client.AnalyseFailed) and exc.code == wire.Code.MODEL_MISSING:
        return jobs.Defer(DEFER_MODEL)
    return None


async def check(settings: Settings) -> AnalyseStatus:
    """Eén keer caps; zonder PLATFORM_ANALYSE_ENABLED meteen OFF, op PostgreSQL meteen down met
    no_sqlite (allebei zonder de socket)."""
    if not settings.analyse_enabled:
        return OFF
    if not settings.analyse_on:
        return NO_SQLITE
    try:
        caps = await client.caps(settings)
    except client.AnalyseDown as exc:
        return AnalyseStatus("down", exc.reason)
    except (client.AnalyseBusy, client.AnalyseFailed):
        return AnalyseStatus("down", "bad_answer")
    missing = tuple(caps.missing())
    if missing:
        return AnalyseStatus("models_missing", ", ".join(missing), missing, caps)
    return AnalyseStatus("ok", caps=caps)


def _set(found: AnalyseStatus) -> None:
    global _status
    before = _status
    _status = found
    if (found.state, found.reason) == (before.state, before.reason):
        return
    if found.state == "ok":
        log.info("analyse bereikbaar (was %s)", before.state)
    else:
        log.warning("analyse %s: %s", found.state, analyse_problem(found))


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)  # apart, zodat een test de tijden van de lus kan zien


async def _loop(settings: Settings) -> None:
    delay = FIRST
    retry = RETRY_FIRST
    while True:
        await _pause(delay)
        try:
            found = await check(settings)
        except Exception:  # de lus mag nooit stoppen
            log.exception("de stand van de analyse")
            found = AnalyseStatus("down", "bad_answer")
        _set(found)
        if found.state == "down":
            delay, retry = retry, min(retry * 2, INTERVAL)
        else:
            delay, retry = INTERVAL, RETRY_FIRST


def start(settings: Settings) -> asyncio.Task | None:
    """Vanuit de lifespan. Zonder PLATFORM_ANALYSE_ENABLED gebeurt er niets: geen taak, geen
    socket, geen sleutel, de stand blijft OFF. Op PostgreSQL: down met no_sqlite, en ook geen
    taak, socket of sleutel. Met een sleutel die niet deugt: down, zonder taak."""
    global _task
    _set_quiet(OFF)
    if not settings.analyse_enabled:
        return None
    if not settings.analyse_on:
        _set(NO_SQLITE)
        return None
    problem = settings.analyse_key_problem()
    if problem is not None:
        _set(AnalyseStatus("down", problem))
        return None
    _task = asyncio.create_task(_loop(settings), name="analyse-monitor")
    return _task


def _set_quiet(found: AnalyseStatus) -> None:
    global _status
    _status = found


async def stop() -> None:
    """De taak weg en de stand terug op OFF (een volgende start in hetzelfde proces: tests)."""
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _set_quiet(OFF)
