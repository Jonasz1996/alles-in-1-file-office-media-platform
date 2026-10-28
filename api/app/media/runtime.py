"""Welke app en welke instellingen media gebruikt, en het starten en stoppen (fase 5, §3.3).

De lifespan roept bind(app, settings) en dan `await start(app, settings)` aan, en bij het
stoppen `await stop(app)`. Zonder PLATFORM_MEDIA_ENABLED doet start niets: er draait geen
worker, elk media-eindpunt geeft 503 media_off (require), en de app is precies 0.6.0.

Met media aan:
- PLATFORM_MEDIA_WORKER leeg: de app start zelf een lokale worker (local.py, Keuze 13);
- PLATFORM_MEDIA_WORKER gezet: de app verbindt met die socket, en valt nooit stil terug op
  lokaal (dat zou de afscherming uitzetten);
- health.py houdt MediaState.worker bij met caps (telt niet mee in de plaatsen).
"""

from __future__ import annotations

import logging

from fastapi import Request

from app.config import Settings, get_settings
from app.media import client
from app.media import errors as media_errors
from app.media.state import MediaState, media_policy, media_state

log = logging.getLogger("platform.media")

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
    from app.media import health, local

    state = media_state(app)
    state.enabled = settings.media_enabled
    if not state.enabled:
        return state
    state.mode = "container" if settings.media_worker else "local"
    client.bind(settings.media_socket)
    if state.mode == "local":
        try:
            app.state.media_local = await local.start(settings)
        except Exception as exc:  # de app start ook zonder worker
            log.warning("lokale mediaworker start niet: %s", exc)
            state.set_worker("down", str(exc)[:200])
    app.state.media_health = health.start(app, settings)
    return state


async def stop(app) -> None:
    from app.media import health, local

    await health.stop(getattr(app.state, "media_health", None))
    app.state.media_health = None
    worker = getattr(app.state, "media_local", None)
    if worker is not None:
        await local.stop(worker)
        app.state.media_local = None
    client.bind(None)


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
