import logging
import os
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import db
from app.certs import CertManager
from app.config import Settings, get_settings
from app.main import app
from app.migrate import upgrade
from app.security import SecurityConfig, SecurityPolicy, login_limiter

# De eerste map-node per map (zie pytest_make_collect_report hieronder)
_MAPNODES: dict[Path, pytest.Directory] = {}


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    """pytest 9.1 hangt de fixtures van een conftest.py aan de eerste map-node van die map. Staat
    op de commandoregel een bestand uit tests/ tussen twee bestanden uit één submap (zoals
    "tests/search/a.py tests/test_b.py tests/search/c.py"), dan verzamelt pytest tests/ opnieuw en
    krijgt de submap een tweede node, zonder de fixtures van zijn conftest: autouse-fixtures als
    _databases_dicht en _aangemeld vielen dan stil weg, en plaatsen of stappen bestonden niet.
    Zo'n tweede node krijgt ze hier alsnog (I1, C2 en review R1; test_conftest_volgorde.py)."""
    rapport = yield
    if isinstance(collector, pytest.Directory):
        eerste = _MAPNODES.setdefault(collector.path, collector)
        conftest = collector.config.pluginmanager.get_plugin(str(collector.path / "conftest.py"))
        if eerste is not collector and conftest is not None:
            collector.session._fixturemanager.parsefactories(holder=conftest, node=collector)
    return rapport


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


@pytest.fixture(autouse=True)
def _schrijfpoort():
    """Geen schrijfactie buiten de schrijfpoort van SQLite (app/dbgate.py), geen tweede
    verbinding die schrijft terwijl dezelfde taak de poort heeft, en niet wachten op
    BlobStore.lock of een gebruikersslot met de poort in handen: dat kan vastlopen tot de
    poort na 5 s opgeeft. Een test die dat expres doet, haalt het zelf op (take_violations)."""
    from app import dbgate

    dbgate.take_violations()
    yield
    found = [v for v in dbgate.take_violations() if v[0] in ("ungated", "same_task", "lock_in_txn")]
    assert not found, f"schrijfpoort: {found}"


@pytest.fixture
def mismatch_verwacht():
    """Deze test lokt search_access_mismatch expres uit en controleert de regel zelf."""


# Met PLATFORM_TEST_DATABASE_URL (postgresql+asyncpg://…/postgres) draait elke test op een eigen,
# nieuwe PostgreSQL-database in plaats van SQLite: zo houdt CI het cluster (B12, B15) eerlijk.
PG_URL = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")
# Eén keer per run migreren, in een sjabloon; elke test krijgt dan een kopie met
# CREATE DATABASE ... TEMPLATE (0,02 s in plaats van een hele migratie). Uit met
# PLATFORM_TEST_PG_TEMPLATE=0: dan migreert elke test zijn eigen lege database.
PG_TEMPLATE_ON = os.environ.get("PLATFORM_TEST_PG_TEMPLATE", "1") != "0"
_PG_TEMPLATE: list[str] = []


def _pg_url(name: str) -> str:
    return PG_URL.rsplit("/", 1)[0] + "/" + name


async def _pg_exec(sql: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _pg_make_template(name: str) -> None:
    """Het sjabloon: een eigen database, gemigreerd tot de nieuwste revisie, zonder verbinding
    erop (CREATE DATABASE ... TEMPLATE weigert een sjabloon met een open verbinding)."""
    import shutil
    import tempfile

    await _pg_exec(f'CREATE DATABASE "{name}"')
    tmp = Path(tempfile.mkdtemp(prefix="pgsjabloon"))
    try:
        s = Settings(data_dir=tmp, web_dist=tmp / "web", jwt_secret="test" * 8,
                     database_url=_pg_url(name))  # fmt: skip
        await upgrade(s)
        await db.get_engine(s).dispose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _pg_template() -> str:
    """De naam van het sjabloon van deze run; de eerste keer gemaakt. In een eigen draad met een
    eigen lus: zo raakt het de engines van de lopende test niet (db._engine blijft gelijk; de
    engine van het sjabloon is dicht)."""
    import asyncio

    if not _PG_TEMPLATE:
        name = "tpl_" + uuid.uuid4().hex
        await asyncio.to_thread(asyncio.run, _pg_make_template(name))
        _PG_TEMPLATE.append(name)
    return _PG_TEMPLATE[0]


def pytest_sessionfinish(session, exitstatus):
    """Het sjabloon weg aan het eind van de run."""
    if _PG_TEMPLATE:
        import asyncio

        asyncio.run(_pg_exec(f'DROP DATABASE IF EXISTS "{_PG_TEMPLATE.pop()}" WITH (FORCE)'))


async def _pg_database(name: str, drop: bool = False, migrated: bool = False) -> str:
    """Maak (of met drop: wis) de database name. migrated: als kopie van het sjabloon, dus al
    op de nieuwste revisie (zonder sjabloon gewoon leeg; de fixture settings migreert dan)."""
    if drop:
        await _pg_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    elif migrated and PG_TEMPLATE_ON:
        await _pg_exec(f'CREATE DATABASE "{name}" TEMPLATE "{await _pg_template()}"')
    else:
        await _pg_exec(f'CREATE DATABASE "{name}"')
    return _pg_url(name)


def pytest_collection_modifyitems(config, items):
    """Marker pg: de test vraagt PostgreSQL (PLATFORM_TEST_DATABASE_URL), zonder slaat hij over.
    Marker sqlite_only: de test gaat alleen over SQLite, met PostgreSQL slaat hij over, met de
    reden uit de marker (@pytest.mark.sqlite_only("waarom"))."""
    for item in items:
        if not PG_URL and item.get_closest_marker("pg") is not None:
            item.add_marker(
                pytest.mark.skip(reason="vraagt PostgreSQL (PLATFORM_TEST_DATABASE_URL)")
            )
        only = item.get_closest_marker("sqlite_only")
        if PG_URL and only is not None:
            why = only.args[0] if only.args else "alleen SQLite"
            item.add_marker(pytest.mark.skip(reason=f"alleen SQLite: {why}"))


@pytest.fixture
async def database_url(tmp_path):
    """De database van deze test als URL: zonder PLATFORM_TEST_DATABASE_URL het SQLite-bestand
    platform.db in tmp_path (gelijk aan Settings.db_url), anders een eigen, nieuwe en lege
    PostgreSQL-database die na de test weer weg is. Voor tests en sub-conftests die zelf
    Settings maken en zelf migreren (ook naar een oudere revisie)."""
    if not PG_URL:
        yield f"sqlite+aiosqlite:///{tmp_path / 'platform.db'}"
        return
    name = "t_" + uuid.uuid4().hex
    yield await _pg_database(name)
    await _pg_database(name, drop=True)


@pytest.fixture
async def database_url_head(tmp_path):
    """Als database_url, maar op PostgreSQL al op de nieuwste revisie: een kopie van het sjabloon
    van deze run (zie _pg_template). Op SQLite hetzelfde bestand als database_url; daar migreert
    wie het gebruikt zelf (upgrade is dan nodig, op PostgreSQL een no-op)."""
    if not PG_URL:
        yield f"sqlite+aiosqlite:///{tmp_path / 'platform.db'}"
        return
    name = "t_" + uuid.uuid4().hex
    yield await _pg_database(name, migrated=True)
    await _pg_database(name, drop=True)


@pytest.fixture
async def settings(tmp_path, database_url_head):
    extra = {"database_url": database_url_head} if PG_URL else {}
    s = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8, **extra)
    await upgrade(s)
    yield s
    # De engine van deze datamap dicht en uit de cache: anders blijven zijn verbindingen (elk met
    # een eigen thread) en alles eromheen tot het eind van de run, en worden latere tests (de
    # snelheidstests) trager naarmate de run langer is
    await db.get_engine(s).dispose()
    db._engine.cache_clear()


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
