"""De analyse op PostgreSQL: de schakelaar en de triggers van 0013 (0.8.2, P4).

- Settings.analyse_on op PostgreSQL alleen met PLATFORM_ANALYSE_POSTGRES (anders down met
  pg_not_enabled) en als app/analyse/pgcheck.py alle triggers van 0013 aan zag (anders down met
  no_pg_triggers). Op SQLite verandert er niets, ook niet met PLATFORM_ANALYSE_POSTGRES.
- pgcheck.TRIGGERS is de lijst van migratie 0013.
- pgcheck.check vraagt op SQLite, of zonder de twee instellingen, niets aan de database.
- Op PostgreSQL (pg): één trigger uit (DISABLE TRIGGER) houdt de analyse uit, met een fout in
  het log met alleen de naam van de trigger; weer aan, dan weer aan.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest
from sqlalchemy import event, text

from app import config
from app.analyse import monitor, pgcheck
from app.config import Settings
from app.db import get_engine

pytestmark = pytest.mark.anyio

API = Path(__file__).resolve().parents[2]
PG = "postgresql+asyncpg://u:p@127.0.0.1:1/x"  # bestaat niet: er mag niets gevraagd worden
ANDER = "postgresql+asyncpg://u:p@127.0.0.1:1/y"


def _migration():
    path = API / "migrations" / "versions" / "0013_postgres.py"
    spec = importlib.util.spec_from_file_location("m0013_analyse", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(tmp_path, **extra) -> Settings:
    values = {"data_dir": tmp_path, "web_dist": tmp_path / "web", "jwt_secret": "test" * 8}
    return Settings(**{**values, **extra})


@pytest.fixture
def vergeten():
    """Wat pgcheck per database onthield, na de test weer weg."""
    yield
    for url in (PG, ANDER):
        config.pg_triggers_seen(url, False)


def test_de_triggers_zijn_die_van_0013():
    assert pgcheck.TRIGGERS == _migration().TRIGGERS


def test_de_schakelaar_per_database(tmp_path, vergeten):
    # SQLite: zoals vóór 0.8.2, ook met PLATFORM_ANALYSE_POSTGRES
    for extra in ({}, {"analyse_postgres": True}):
        s = _settings(tmp_path, analyse_enabled=True, **extra)
        assert s.analyse_pg_block() is None and s.analyse_on
        assert monitor.blocked(s) is None
    assert not _settings(tmp_path).analyse_on
    # PostgreSQL zonder PLATFORM_ANALYSE_POSTGRES
    s = _settings(tmp_path, analyse_enabled=True, database_url=PG)
    assert (s.analyse_pg_block(), s.analyse_on) == ("pg_not_enabled", False)
    assert monitor.blocked(s) == monitor.AnalyseStatus("down", "pg_not_enabled")
    # Met de instelling, maar de triggers (nog) niet gezien
    s = _settings(tmp_path, analyse_enabled=True, analyse_postgres=True, database_url=PG)
    assert (s.analyse_pg_block(), s.analyse_on) == ("no_pg_triggers", False)
    assert monitor.blocked(s) == monitor.AnalyseStatus("down", "no_pg_triggers")
    # Gezien voor deze database: aan; een andere database niet
    config.pg_triggers_seen(PG, True)
    assert s.analyse_pg_block() is None and s.analyse_on and monitor.blocked(s) is None
    ander = s.model_copy(update={"database_url": ANDER})
    assert ander.analyse_pg_block() == "no_pg_triggers"
    # De instelling blijft nodig, en PLATFORM_ANALYSE_ENABLED ook
    assert s.model_copy(update={"analyse_postgres": False}).analyse_pg_block() == "pg_not_enabled"
    assert not s.model_copy(update={"analyse_enabled": False}).analyse_on
    # Een latere controle die er een mist: weer uit
    config.pg_triggers_seen(PG, False)
    assert s.analyse_pg_block() == "no_pg_triggers"


async def test_pgcheck_vraagt_niets_zonder_de_instellingen(tmp_path, vergeten):
    assert await pgcheck.check(_settings(tmp_path, analyse_enabled=True))  # SQLite: altijd
    # PG bestaat niet: een vraag zou een fout geven
    for extra in ({}, {"analyse_enabled": True}, {"analyse_postgres": True}):
        s = _settings(tmp_path, database_url=PG, **extra)
        assert await pgcheck.check(s) is False
        pgcheck.check_cli(s)
        assert s.analyse_pg_block() in ("pg_not_enabled", "no_pg_triggers")
    assert pgcheck.missing(set(pgcheck.TRIGGERS)) == []
    assert pgcheck.missing(set(pgcheck.TRIGGERS[1:])) == [pgcheck.TRIGGERS[0][0]]


@pytest.mark.pg
async def test_een_trigger_uit_houdt_de_analyse_uit(settings, caplog):
    s = settings.model_copy(update={"analyse_enabled": True, "analyse_postgres": True})
    engine = get_engine(s)
    try:
        assert await pgcheck.check(s) and s.analyse_on
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE person DISABLE TRIGGER person_bd_face"))
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="platform.analyse"):
            assert await pgcheck.check(s) is False
        assert not s.analyse_on and s.analyse_pg_block() == "no_pg_triggers"
        assert monitor.blocked(s) == monitor.AnalyseStatus("down", "no_pg_triggers")
        logged = [r.getMessage() for r in caplog.records if r.name == "platform.analyse"]
        assert len(logged) == 1 and logged[0].endswith(": person_bd_face"), logged
        assert s.database_url not in logged[0] and "127.0.0.1" not in logged[0]
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE person ENABLE TRIGGER person_bd_face"))
        assert await pgcheck.check(s) and s.analyse_on

        # Zonder PLATFORM_ANALYSE_POSTGRES: geen enkele vraag aan de database, en uit
        gezien: list[str] = []

        def kijk(conn, cursor, statement, parameters, context, executemany):
            gezien.append(statement)

        uit = settings.model_copy(update={"analyse_enabled": True})
        event.listen(engine.sync_engine, "before_cursor_execute", kijk)
        try:
            assert await pgcheck.check(uit) is False
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", kijk)
        assert gezien == [] and uit.analyse_pg_block() == "pg_not_enabled"
    finally:
        config.pg_triggers_seen(s.db_url, False)
