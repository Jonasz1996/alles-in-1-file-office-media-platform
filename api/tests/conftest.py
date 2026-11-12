import logging
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


@pytest.fixture
async def settings(tmp_path):
    s = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8)
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
