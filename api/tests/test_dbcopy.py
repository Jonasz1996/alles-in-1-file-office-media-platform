"""db-copy: SQLite van één node naar PostgreSQL (B12, B15).

Alleen met PLATFORM_TEST_DATABASE_URL."""

import os
import uuid

import pytest
from sqlalchemy import func, select

from app import db
from app.config import Settings
from app.dbcopy import CopyError, db_copy
from app.migrate import upgrade
from app.models import Blob, Node, User

PG = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not PG, reason="geen PLATFORM_TEST_DATABASE_URL"),
]


async def _database(name: str, drop: bool = False) -> str:
    import asyncpg

    conn = await asyncpg.connect(PG.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        sql = (
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
            if drop
            else f'CREATE DATABASE "{name}"'
        )
        await conn.execute(sql)
    finally:
        await conn.close()
    return PG.rsplit("/", 1)[0] + "/" + name


@pytest.fixture
async def sqlite_with_data(tmp_path):
    """Een node met SQLite, een gebruiker, een map en een bestand, via de echte API."""
    from httpx import ASGITransport, AsyncClient

    from app.certs import CertManager
    from app.config import get_settings
    from app.main import app
    from app.security import SecurityConfig, SecurityPolicy, login_limiter

    s = Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8, database_url=""
    )
    await upgrade(s)
    app.dependency_overrides[get_settings] = lambda: s
    app.state.certs = CertManager(s)
    app.state.security = SecurityPolicy(SecurityConfig(require_2fa=False))
    login_limiter.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        from tests.conftest import ADMIN

        r = await c.post("/api/v1/auth/setup", json=ADMIN)
        assert r.status_code < 300, r.text
    from datetime import UTC, datetime

    from tests.factories import create_file, create_folder, create_user, maker

    async with maker(s)() as session:
        owner = await create_user(session, "foto@example.nl", disabled=True)
        late = await create_file(
            session,
            owner,
            name="foto.jpg",
            data=b"jpg",
            mime="image/jpeg",
            taken_at=datetime(2026, 7, 1, 12, 30, tzinfo=UTC),
        )
        # Een map die later komt dan wat erin staat (verplaatst): foreign keys op volgorde
        # van id volstaan dan niet
        folder = await create_folder(session, owner, name="Later", deleted=True)
        late.parent_id = folder.id
        await session.commit()
    app.dependency_overrides.clear()
    del app.state.certs
    del app.state.security
    yield s
    await db.get_engine(s).dispose()
    db._engine.cache_clear()


async def _count(settings: Settings, model) -> int:
    async with db.get_engine(settings).connect() as conn:
        return (await conn.execute(select(func.count()).select_from(model))).scalar_one()


async def test_copy_to_postgres(sqlite_with_data):
    s = sqlite_with_data
    name = "c_" + uuid.uuid4().hex
    url = await _database(name)
    try:
        said = []
        res = await db_copy(s, url, say=said.append)
        assert res.rows["user"] == await _count(s, User) == 2
        assert res.snapshot is not None and res.snapshot.exists()

        pg = Settings(
            data_dir=s.data_dir, web_dist=s.web_dist, jwt_secret="test" * 8, database_url=url
        )
        assert await _count(pg, User) == 2
        assert await _count(pg, Node) == await _count(s, Node)
        assert await _count(pg, Blob) == await _count(s, Blob)
        async with db.get_engine(pg).connect() as conn:
            row = (
                await conn.execute(
                    select(Node.taken_at, Node.parent_id).where(Node.name == "foto.jpg")
                )
            ).one()
            folder = (
                await conn.execute(select(Node.id, Node.deleted_at).where(Node.name == "Later"))
            ).one()
            user = (
                await conn.execute(select(User.disabled_at).where(User.email == "foto@example.nl"))
            ).one()
        assert row.taken_at.year == 2026 and row.parent_id == folder.id
        assert folder.deleted_at is not None and user.disabled_at is not None
        # De app start op het doel: de migraties zien dezelfde head en doen niets
        await upgrade(pg)

        with pytest.raises(CopyError, match="al rijen"):
            await db_copy(s, url, say=said.append)
        again = await db_copy(s, url, empty_first=True, say=said.append)
        assert again.rows["user"] == 2
        await db.get_engine(pg).dispose()
    finally:
        db._engine.cache_clear()
        await _database(name, drop=True)


async def test_refuses_sqlite_target(sqlite_with_data):
    with pytest.raises(CopyError, match="PostgreSQL"):
        await db_copy(sqlite_with_data, "sqlite+aiosqlite:///x.db")
