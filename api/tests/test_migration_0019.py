"""Migratie 0019 (replicatie per bibliotheek): één kolom media_library.copies, NULL voor wat er
al was, met een CHECK op 1, 2 of 3. Heen, terug naar precies 0018 (de bibliotheek blijft), en
weer heen. Op SQLite en, met PLATFORM_TEST_DATABASE_URL, op PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command

from app import db as app_db
from app.config import Settings
from app.db import get_engine
from tests.search.migratie import MADE, migrate

pytestmark = pytest.mark.anyio


@pytest.fixture
async def at_0018(tmp_path, database_url):
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8,
                        database_url=database_url)  # fmt: skip
    await migrate(settings, command.upgrade, "0018")
    yield settings
    await app_db.get_engine(settings).dispose()
    app_db._engine.cache_clear()


@pytest.fixture(autouse=True)
async def _dicht(anyio_backend):
    yield
    while MADE:
        await app_db.get_engine(MADE.pop()).dispose()
    app_db._engine.cache_clear()


def _shape(connection) -> dict:
    """Kolommen (naam, nullable, standaard) en de checks van media_library, dialect-neutraal."""
    found = sa.inspect(connection)
    return {
        "columns": [
            (c["name"], c["nullable"], str(c.get("default")))
            for c in found.get_columns("media_library")
        ],
        "checks": sorted(c["name"] or "" for c in found.get_check_constraints("media_library")),
    }


async def shape(settings: Settings) -> dict:
    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(_shape)


async def test_van_0018_naar_0019_en_terug(at_0018):
    settings = at_0018
    now = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    user, root = uuid.uuid4(), uuid.uuid4()
    users = sa.table(
        "user", *(sa.column(c) for c in ("display_name", "email", "password_hash", "role")),
        sa.column("id", sa.Uuid), sa.column("root_id", sa.Uuid), sa.column("quota_bytes"),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )  # fmt: skip
    nodes = sa.table(
        "node", sa.column("id", sa.Uuid), sa.column("owner_id", sa.Uuid), sa.column("name"),
        sa.column("kind"), sa.column("size"), sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("modified_at", sa.DateTime(timezone=True)),
    )  # fmt: skip
    libs = sa.table(
        "media_library", sa.column("id", sa.Uuid), sa.column("owner_id", sa.Uuid),
        sa.column("root_id", sa.Uuid), sa.column("kind"), sa.column("name"),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )  # fmt: skip
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            users.insert().values(
                id=user, email="jonas@example.nl", display_name="Jonas", password_hash="x",
                role="admin", quota_bytes=0, root_id=root, created_at=now,
            )
        )  # fmt: skip
        await conn.execute(
            nodes.insert().values(
                id=root, owner_id=user, name="", kind="dir", size=0, created_at=now, modified_at=now
            )
        )
        await conn.execute(
            libs.insert().values(
                id=uuid.uuid4(), owner_id=user, root_id=root, kind="movies", name="Films",
                created_at=now,
            )
        )  # fmt: skip
    before = await shape(settings)

    await migrate(settings, command.upgrade, "0019")
    after = await shape(settings)
    assert after["columns"][: len(before["columns"])] == before["columns"]
    assert [c[:2] for c in after["columns"][len(before["columns"]) :]] == [("copies", True)]
    assert "ck_media_library_copies" in after["checks"]
    async with get_engine(settings).begin() as conn:
        assert (await conn.execute(sa.text("SELECT copies FROM media_library"))).all() == [(None,)]
        for copies in (1, 2, 3, None):
            await conn.execute(sa.text("UPDATE media_library SET copies = :c"), {"c": copies})
    for wrong in (0, 4):
        with pytest.raises(sa.exc.IntegrityError):
            async with get_engine(settings).begin() as conn:
                await conn.execute(sa.text("UPDATE media_library SET copies = :c"), {"c": wrong})

    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text("UPDATE media_library SET copies = 2"))
    await migrate(settings, command.downgrade, "0018")
    assert await shape(settings) == before
    async with get_engine(settings).connect() as conn:
        assert (await conn.execute(sa.text("SELECT name FROM media_library"))).all() == [("Films",)]
        assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).all() == [
            ("0018",)
        ]

    # En weer heen: de bibliotheek op de standaard
    await migrate(settings, command.upgrade, "0019")
    async with get_engine(settings).connect() as conn:
        assert (await conn.execute(sa.text("SELECT copies FROM media_library"))).all() == [(None,)]
