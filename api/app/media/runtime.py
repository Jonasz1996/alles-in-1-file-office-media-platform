"""Welke app en welke instellingen media gebruikt, en het starten en stoppen (fase 5, §3.3).

De lifespan roept bind(app, settings) en dan `await start(app, settings)` aan, en bij het
stoppen `await stop(app)`. Zonder PLATFORM_MEDIA_ENABLED doet start niets: er draait geen
worker, elk media-eindpunt geeft 503 media_off (require), en de app is precies 0.6.0.

Met media aan:
- PLATFORM_MEDIA_WORKER leeg: de app start zelf een lokale worker (local.py, Keuze 13);
- PLATFORM_MEDIA_WORKER gezet: de app verbindt met die socket, en valt nooit stil terug op
  lokaal (dat zou de afscherming uitzetten). De worker bewijst zich met
  PLATFORM_MEDIA_WORKER_KEY (hello, review S1); zonder geldige sleutel is de worker down met
  de reden (Settings.media_problem), en gaat er nooit een fd naar de socket;
- health.py houdt MediaState.worker bij met caps (telt niet mee in de plaatsen);
- scanner.start(app, settings) start de lus van de scanner (M2; de stub niets), en stop roept
  scanner.stop aan met wat start teruggaf (review K1);
- de map media/sessions/ (de stukken van lopende sessies) gaat leeg: na een herstart is elke
  sessie weg;
- het opruimwerk media.sweep komt in de rij default (cache.sweep, M4 en M5); stop haalt het
  weer weg.

Netjes stoppen (F3, §3.5): serve.run roept bij het begin van het stoppen begin_stop(app) aan,
vóór alles. Vanaf dan krijgt elk verzoek onder /api/v1/stream/ 503 media_busy {reason: worker}
met Retry-After: 2 (StreamHeaders in main.py), een verzoek dat op iets wacht via
until_stopped(...) ook, en alle sessies gaan dicht (sessions.close_all). De speler vraagt na de
herstart stil een nieuwe sessie (Keuze 32).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from collections.abc import Awaitable

import anyio.to_thread
from fastapi import HTTPException, Request

from app import jobs
from app.config import Settings, get_settings
from app.media import client
from app.media import errors as media_errors
from app.media.state import MediaState, media_policy, media_state

log = logging.getLogger("platform.media")

SWEEP = "media.sweep"
SWEEP_EVERY = 300.0  # seconden
STOP_RETRY = 2  # Retry-After bij het stoppen (F3)

_app = None
_settings: Settings | None = None


def bind(app, settings: Settings | None = None) -> None:
    global _app, _settings
    _app = app
    _settings = settings


def unbind() -> None:
    global _app, _settings
    _app = None
    _settings = None


def app():
    """De gebonden app (FastAPI), of None."""
    return _app


def settings() -> Settings:
    return _settings if _settings is not None else get_settings()


async def start(app, settings: Settings) -> MediaState:
    """Media aanzetten als PLATFORM_MEDIA_ENABLED het zegt. Faalt de worker, dan start de app
    toch: de staat zegt down en /beheer toont waarom."""
    from app.media import health, local, scanner

    state = media_state(app)
    state.enabled = settings.media_enabled
    state.stopping = False  # een vorige keer gestopt in hetzelfde proces (tests)
    if not state.enabled:
        return state
    state.mode = "container" if settings.media_worker else "local"
    await anyio.to_thread.run_sync(clear_sessions, settings)
    jobs.housekeeping(SWEEP, _sweep, SWEEP_EVERY)
    client.bind(settings.media_socket, settings.media_key())
    problem = settings.media_problem()
    if problem is not None:
        log.warning("mediaworker: %s", problem)
        state.set_worker("down", problem)
    elif state.mode == "local":
        try:
            app.state.media_local = await local.start(settings)
        except Exception as exc:  # de app start ook zonder worker
            log.warning("lokale mediaworker start niet: %s", exc)
            state.set_worker("down", str(exc)[:200])
    app.state.media_health = health.start(app, settings)
    app.state.media_scanner = scanner.start(app, settings)
    return state


async def stop(app) -> None:
    from app.media import health, local, scanner

    jobs.forget_housekeeping(SWEEP)
    await scanner.stop(getattr(app.state, "media_scanner", None))
    app.state.media_scanner = None
    await health.stop(getattr(app.state, "media_health", None))
    app.state.media_health = None
    worker = getattr(app.state, "media_local", None)
    if worker is not None:
        await local.stop(worker)
        app.state.media_local = None
    client.bind(None)


def sessions_dir(settings: Settings):
    """<data_dir>/media/sessions: de stukken van lopende sessies (M4)."""
    return settings.data_dir / "media" / "sessions"


def clear_sessions(settings: Settings) -> None:
    """Bij het starten: wat van een vorige keer bleef staan weg (draai in een thread)."""
    shutil.rmtree(sessions_dir(settings), ignore_errors=True)


async def _sweep(settings: Settings) -> None:
    """Het opruimwerk van media; alleen zolang media aan staat."""
    bound = app()
    if bound is None or not enabled(bound):
        return
    from app.media import cache

    await cache.sweep(settings)


def stopping(app) -> bool:
    """Is het stoppen begonnen (F3)?"""
    return media_state(app).stopping


def stopping_error() -> HTTPException:
    """Het antwoord zolang de app stopt: 503 media_busy {reason: worker}, Retry-After: 2."""
    return media_errors.busy("worker", STOP_RETRY)


def begin_stop(app) -> int:
    """Het begin van het stoppen (F3), vóór alles: geen nieuwe verzoeken meer onder
    /api/v1/stream/, wie wacht krijgt 503 met Retry-After: 2, en alle sessies dicht. Twee keer
    is één keer; zonder media gebeurt er niets. Geeft het aantal gesloten sessies. Synchroon:
    ook aan te roepen vanuit een signaal (via call_soon_threadsafe)."""
    state = media_state(app)
    if state.stopping or not state.enabled:
        return 0
    state.stopping = True
    for waiter in list(state.stop_waiters):
        if not waiter.done():
            waiter.set_result(None)
    state.stop_waiters.clear()
    from app.media import sessions

    try:
        closed = sessions.close_all()
    except Exception:  # stoppen gaat altijd door
        log.exception("mediasessies sluiten bij het stoppen")
        return 0
    if closed:
        log.info("%s mediasessies gesloten bij het stoppen", closed)
    return closed


async def until_stopped[T](app, awaitable: Awaitable[T]) -> T:
    """Wacht op awaitable (een segment, de worker), tenzij het stoppen begint: dan wordt het
    wachten afgebroken en komt er 503 media_busy met Retry-After: 2 (F3). Voor M3, M4, M5 en M9:
    elk wachten in een route onder /api/v1/stream/ gaat hierlangs."""
    state = media_state(app)
    if state.stopping:
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        raise stopping_error()
    task = asyncio.ensure_future(awaitable)
    waiter = asyncio.get_running_loop().create_future()
    state.stop_waiters.add(waiter)
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        task.cancel()
        raise
    finally:
        state.stop_waiters.discard(waiter)
        if not waiter.done():
            waiter.cancel()
    if task.done():
        return task.result()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    raise stopping_error()


def enabled(app) -> bool:
    """Staat media aan: de vlag in de omgeving, en niet uitgezet op /beheer."""
    state = media_state(app)
    if not state.enabled:
        return False
    config = media_policy(app).config
    return config is None or config.enabled


def require(request: Request) -> MediaState:
    """Dependency voor elk media-eindpunt: 503 media_off als media uit staat."""
    if not enabled(request.app):
        raise media_errors.off()
    return media_state(request.app)
