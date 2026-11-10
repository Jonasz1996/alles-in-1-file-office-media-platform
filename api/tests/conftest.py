import logging
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app import db
from app.certs import CertManager
from app.config import Settings, get_settings
from app.main import app
from app.migrate import upgrade
from app.security import SecurityConfig, SecurityPolicy, login_limiter


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _vrije_schijf(monkeypatch):
    """De toelating van media (Keuze 31) weigert onder 10 GiB vrij op data_dir. In een test is
    dat de schijf van de host, dus zonder dit faalden op een machine met weinig vrije ruimte tests
    met media_busy (disk). Tests over die grens zetten disk_free zelf."""
    from app.media import admission

    monkeypatch.setattr(admission, "disk_free", lambda path=None: admission.DISK_MIN * 4)


@pytest.fixture(autouse=True)
def _geen_mismatch(request):
    """Geen search_access_mismatch in welke test ook (fase 6, §4.1; tests/search/mismatch.py)."""
    from tests.search.mismatch import LOGGER, Mismatches, check

    handler = Mismatches()
    logging.getLogger(LOGGER).addHandler(handler)
    try:
        yield handler
    finally:
        logging.getLogger(LOGGER).removeHandler(handler)
    check(handler.lines, "mismatch_verwacht" in request.fixturenames)


@pytest.fixture
def mismatch_verwacht():
    """Deze test lokt search_access_mismatch expres uit en controleert de regel zelf."""


# Met PLATFORM_TEST_DATABASE_URL (postgresql+asyncpg://…/postgres) draait elke test op een eigen,
# nieuwe PostgreSQL-database in plaats van SQLite: zo houdt CI het cluster (B12, B15) eerlijk.
PG_URL = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")


async def _pg_database(name: str, drop: bool = False) -> str:
    import asyncpg

    base = PG_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(base)
    try:
        if drop:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        else:
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    return PG_URL.rsplit("/", 1)[0] + "/" + name


@pytest.fixture
async def settings(tmp_path):
    extra = {}
    name = ""
    if PG_URL:
        name = "t_" + uuid.uuid4().hex
        extra["database_url"] = await _pg_database(name)
    s = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8, **extra)
    await upgrade(s)
    yield s
    # De engine van deze datamap dicht en uit de cache: anders blijven zijn verbindingen (elk met
    # een eigen thread) en alles eromheen tot het eind van de run, en worden latere tests (de
    # snelheidstests) trager naarmate de run langer is
    await db.get_engine(s).dispose()
    db._engine.cache_clear()
    if name:
        await _pg_database(name, drop=True)


@pytest.fixture
def certs(settings):
    """CertManager zonder achtergrondtaak; tests zetten zelf certificaten neer."""
    return CertManager(settings)


@pytest.fixture
async def client(settings, certs):
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.certs = certs
    # De meeste tests gaan niet over 2FA: die draaien zonder verplichte 2FA
    app.state.security = SecurityPolicy(SecurityConfig(require_2fa=False))
    login_limiter.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    del app.state.certs
    del app.state.security


ADMIN = {"email": "jonas@example.nl", "display_name": "Jonas", "password": "lang-genoeg-123"}


@pytest.fixture
async def admin(client):
    """Ingelogde beheerder: geeft (headers, tokens) terug."""
    r = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert r.status_code == 201, r.text
    tokens = r.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, tokens
