"""De engine van app/db.py."""

from app import db


def test_sqlite_zonder_ping_bij_elke_verbinding():
    """Review golf 1 van fase 6, H2: pool_pre_ping deed bij elk verzoek een SELECT 1, met
    aiosqlite vier keer heen en weer naar zijn thread. Een verbinding met een bestand valt
    niet weg; bij een server (PostgreSQL) blijft de ping."""
    sqlite = db._engine.__wrapped__("sqlite+aiosqlite:///:memory:")
    assert sqlite.pool._pre_ping is False
    server = db._engine.__wrapped__("postgresql+asyncpg://u:p@127.0.0.1:1/x")
    assert server.pool._pre_ping is True


def test_alleen_sqlite_krijgt_de_schrijfpoort():
    """De schrijfpoort (app/dbgate.py, de nacontrole van golf 4) zit alleen op SQLite: daar
    is één schrijfslot zonder rij. PostgreSQL krijgt geen poort, geen omwikkelde commit en geen
    luisteraar, en houdt zijn checkpoint."""
    sqlite = db._engine.__wrapped__("sqlite+aiosqlite:///:memory:")
    assert hasattr(sqlite.sync_engine, "_dbgate_gates")
    assert getattr(sqlite.sync_engine.dialect.do_commit, "_dbgate", False)
    assert len(sqlite.sync_engine.dispatch.before_cursor_execute) == 1
    assert len(sqlite.sync_engine.dialect.dispatch.handle_error) == 1
    server = db._engine.__wrapped__("postgresql+asyncpg://u:p@127.0.0.1:1/x")
    assert not hasattr(server.sync_engine, "_dbgate_gates")
    assert not hasattr(server.sync_engine, "_dbgate_ckpts")
    assert not getattr(server.sync_engine.dialect.do_commit, "_dbgate", False)
    assert not getattr(server.sync_engine.dialect.do_rollback, "_dbgate", False)
    assert len(server.sync_engine.dispatch.before_cursor_execute) == 0
    assert len(server.sync_engine.dispatch.after_cursor_execute) == 0
    assert len(server.sync_engine.dialect.dispatch.handle_error) == 0
