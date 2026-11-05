"""Welke app en welke instellingen office gebruikt.

De lifespan roept bind(app) aan; de tunnel (een asyncio.Protocol, geen ASGI) en de
achtergrondtaken vinden de app daarna via app(). Zonder bind gelden de instellingen uit de
omgeving (get_settings), en daarin staat zonder PLATFORM_OFFICE_URL geen office: de app is
dan precies fase 3. Tests en app.cli geven hun eigen instellingen mee.
"""

from app.config import Settings, get_settings

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
