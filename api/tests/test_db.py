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
