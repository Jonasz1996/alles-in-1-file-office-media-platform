"""Rate limits aan en uit: settings.rate_limits (PLATFORM_RATE_LIMITS), door de lifespan van
de app op RateLimiter.enabled gezet. Uit alleen voor een testserver (de e2e-tests)."""

import asyncio
import contextlib

import pytest

from app import main, photometa, previews
from app.config import Settings, get_settings
from app.main import app
from app.routers import uploads
from app.security import RateLimiter, login_limiter
from tests.conftest import ADMIN
from tests.factories import code_of


@contextlib.asynccontextmanager
async def started(tmp_path, monkeypatch, **env):
    """De lifespan van de app, met deze omgeving (zoals python -m app.serve hem start)."""
    for name, value in {
        "PLATFORM_DATA_DIR": str(tmp_path),
        "PLATFORM_WEB_DIST": str(tmp_path / "web"),
        "PLATFORM_JWT_SECRET": "test" * 8,
        **env,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    # Zonder het opruimen van uploads: dat zou midden in zijn eerste ronde (een query) worden
    # afgebroken, en daar gaat deze test niet over
    async def idle(_settings):
        await asyncio.sleep(3600)

    monkeypatch.setattr(uploads, "cleanup_loop", idle)
    try:
        async with main.lifespan(app):
            yield
    finally:
        RateLimiter.enabled = True
        get_settings.cache_clear()
        for name in ("certs", "security"):
            with contextlib.suppress(AttributeError, KeyError):
                delattr(app.state, name)


def test_standaard_aan(monkeypatch):
    monkeypatch.delenv("PLATFORM_RATE_LIMITS", raising=False)
    assert Settings().rate_limits is True
    assert RateLimiter.enabled is True


def test_uit_via_de_omgeving(monkeypatch):
    monkeypatch.setenv("PLATFORM_RATE_LIMITS", "false")
    assert Settings().rate_limits is False


@pytest.mark.anyio
async def test_lifespan_zet_ze_uit_en_logt_dat(tmp_path, monkeypatch, caplog):
    async with started(tmp_path, monkeypatch, PLATFORM_RATE_LIMITS="false"):
        assert RateLimiter.enabled is False
        assert app.state.started_at is not None
    assert any("Rate limits staan uit" in r.getMessage() for r in caplog.records)
    assert RateLimiter.enabled is True


@pytest.mark.anyio
async def test_lifespan_laat_ze_aan(tmp_path, monkeypatch):
    async with started(tmp_path, monkeypatch, PLATFORM_RATE_LIMITS="true"):
        assert RateLimiter.enabled is True


@pytest.mark.anyio
async def test_starten_gaat_door_als_klaarzetten_mislukt(tmp_path, monkeypatch):
    async def broken(_settings):
        raise RuntimeError("kapot")

    monkeypatch.setattr(previews, "enqueue_outdated", broken)
    monkeypatch.setattr(photometa, "enqueue_backfill", broken)
    async with started(tmp_path, monkeypatch):
        assert app.state.started_at is not None


@pytest.mark.anyio
async def test_aan_geeft_de_elfde_login_429(client, admin):
    login_limiter.reset()
    wrong = {"email": ADMIN["email"], "password": "verkeerd-wachtwoord"}
    for _ in range(10):
        assert (await client.post("/api/v1/auth/login", json=wrong)).status_code == 401
    r = await client.post("/api/v1/auth/login", json=wrong)
    assert (r.status_code, code_of(r)) == (429, "rate_limited")
    assert int(r.headers["retry-after"]) >= 1


@pytest.mark.anyio
async def test_uit_geen_429(client, admin, monkeypatch):
    monkeypatch.setattr(RateLimiter, "enabled", False)
    login_limiter.reset()
    wrong = {"email": ADMIN["email"], "password": "verkeerd-wachtwoord"}
    for _ in range(12):
        assert (await client.post("/api/v1/auth/login", json=wrong)).status_code == 401
