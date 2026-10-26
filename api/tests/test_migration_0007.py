"""Migratie 0007 (§3.1, §3.8): een database van 0006 met data, omhoog, de tabel office_session
met zijn indexen en de kolommen op blob, hooguit één open sessie per bestand, en weer terug
naar precies een verse 0006 (zodat v0.5.0 weer start, M19)."""

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import get_engine
from app.models import Base
from tests.test_migration_0006 import BLOB, NODE, USER, migrate

TZ = "Europe/Amsterdam"
NOW = datetime(2026, 10, 26, 9, 0)


def settings_at(tmp_path, name: str) -> Settings:
    path = tmp_path / name
    path.mkdir()
    return Settings(data_dir=path, jwt_secret="test" * 8, timezone=TZ)


async def schema(settings: Settings) -> dict:
    """Alles wat SQLite van het schema weet, zonder de tekst van CREATE (die schrijft SQLite
    bij ALTER TABLE anders op): kolommen, indexen met hun kolommen en voorwaarde, foreign
    keys, en de revisie."""

    def read(connection) -> dict:
        out: dict = {}
        names = [
            r[0]
            for r in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in sorted(names):
            columns = connection.exec_driver_sql(f'PRAGMA table_info("{table}")').all()
            fks = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').all()
            indexes = {}
            for row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")').all():
                cols = connection.exec_driver_sql(f'PRAGMA index_info("{row[1]}")').all()
                indexes[row[1]] = (row[2], row[3], row[4], [c[2] for c in cols])
            out[table] = {
                "columns": [tuple(c) for c in columns],
                "fks": sorted(tuple(f[2:]) for f in fks),
                "indexes": indexes,
            }
        # De voorwaarde van gedeeltelijke indexen staat alleen in de SQL van de index
        out["index_sql"] = sorted(
            (r[0], " ".join((r[1] or "").split()))
            for r in connection.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE type='index'"
            )
        )
        out["alembic_version"] = [
            r[0] for r in connection.exec_driver_sql("SELECT version_num FROM alembic_version")
        ]
        return out

    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(read)


SESSION = sa.table(
    "office_session",
    *(
        sa.column(c)
        for c in ("key", "name", "file_type", "blob_id", "state", "node_name", "versions_made")
    ),
    sa.column("node_id", sa.Uuid()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
    sa.column("members", sa.JSON()),
)


def session_row(key: str, node_id: uuid.UUID | None, state: str = "open") -> dict:
    return {
        "key": key,
        "name": "verslag.docx",
        "file_type": "docx",
        "blob_id": "a" * 64,
        "state": state,
        "node_name": "node-a",
        "node_id": node_id,
        "created_at": NOW,
        "updated_at": NOW,
    }


async def fill_0006(settings: Settings) -> uuid.UUID:
    """Een gebruiker met een map en een docx, op revisie 0006."""
    user_id, root_id, file_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            USER.insert().values(
                id=user_id,
                email="jonas@example.nl",
                display_name="Jonas",
                password_hash="x",
                role="admin",
                quota_bytes=0,
                root_id=root_id,
                created_at=NOW,
            )
        )
        await conn.execute(
            BLOB.insert().values(
                sha256="a" * 64,
                size=10,
                ref_count=1,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                created_at=NOW,
            )
        )
        base = {"owner_id": user_id, "size": 10, "created_at": NOW, "modified_at": NOW}
        await conn.execute(
            NODE.insert(),
            [
                {**base, "id": root_id, "parent_id": None, "name": "", "kind": "dir",
                 "mime": None, "blob_id": None, "search_name": ""},
                {**base, "id": file_id, "parent_id": root_id, "name": "verslag.docx",
                 "kind": "file", "mime": "application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document", "blob_id": "a" * 64,
                 "search_name": "verslag.docx"},
            ],
        )  # fmt: skip
    return file_id


@pytest.mark.anyio
async def test_van_0006_naar_0007_en_terug(tmp_path):
    settings = settings_at(tmp_path, "oud")
    fresh = settings_at(tmp_path, "vers")
    await migrate(settings, command.upgrade, "0006")
    await migrate(fresh, command.upgrade, "0006")
    file_id = await fill_0006(settings)
    before = await schema(settings)
    assert before == await schema(fresh)

    await migrate(settings, command.upgrade, "0007")
    after = await schema(settings)
    assert after["alembic_version"] == ["0007"]
    office = after["office_session"]
    assert [c[1] for c in office["columns"]] == [
        "key", "node_id", "owner_id", "name", "file_type", "blob_id", "checkpoint",
        "versioned_at", "versions_made", "saved_at", "applied_mark", "state", "members",
        "banned", "connected", "last_error", "closed_reason", "node_name", "created_at",
        "updated_at", "closed_at",
    ]  # fmt: skip
    assert set(office["indexes"]) >= {
        "ix_office_session_node",
        "ix_office_session_state",
        "ux_office_session_open",
    }
    assert office["indexes"]["ux_office_session_open"][:2] == (1, "c")  # uniek
    assert office["indexes"]["ux_office_session_open"][2] == 1  # gedeeltelijk
    sql = dict(after["index_sql"])["ux_office_session_open"]
    assert "WHERE state <> 'closed' AND node_id IS NOT NULL" in sql
    assert sorted(f[0] for f in office["fks"]) == ["node", "user"]
    assert {f[-2] for f in office["fks"]} == {"SET NULL"}  # on_delete
    blob_columns = {c[1]: c for c in after["blob"]["columns"]}
    assert blob_columns["pdf_state"][3] == 0  # NULL mag
    assert blob_columns["pdf_version"][3] == 1 and blob_columns["pdf_version"][4] == "'0'"
    assert "ix_blob_pdf" in after["blob"]["indexes"]

    # Bestaande blobs: nog geen PDF, generatie 0
    async with get_engine(settings).connect() as conn:
        rows = (await conn.execute(sa.text("SELECT pdf_state, pdf_version FROM blob"))).all()
    assert rows == [(None, 0)]

    # Hooguit één niet-dichte sessie per bestand; dichte en losse (node_id NULL) wel
    async with get_engine(settings).begin() as conn:
        await conn.execute(SESSION.insert().values(**session_row("k1", file_id, "closed")))
        await conn.execute(SESSION.insert().values(**session_row("k2", file_id, "closed")))
        await conn.execute(SESSION.insert().values(**session_row("k3", file_id, "open")))
        await conn.execute(SESSION.insert().values(**session_row("k4", None, "saving")))
        await conn.execute(SESSION.insert().values(**session_row("k5", None, "open")))
        defaults = (
            await conn.execute(
                sa.text("SELECT members, banned, connected, versions_made FROM office_session")
            )
        ).first()
    assert tuple(defaults) == ("{}", "[]", "[]", 0)
    for state in ("pending", "open", "saving"):
        with pytest.raises(IntegrityError):
            async with get_engine(settings).begin() as conn:
                await conn.execute(SESSION.insert().values(**session_row("k9", file_id, state)))

    # Definitief verwijderen zet node_id op NULL; de rij blijft (Keuze 19)
    async with get_engine(settings).begin() as conn:
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.execute(sa.text("DELETE FROM node WHERE kind = 'file'"))
        left = (await conn.execute(sa.text("SELECT key, node_id FROM office_session"))).all()
    assert {k for k, n in left if n is None} == {"k1", "k2", "k3", "k4", "k5"}

    await migrate(settings, command.downgrade, "0006")
    assert await schema(settings) == before
    async with get_engine(settings).connect() as conn:
        assert await conn.scalar(sa.text("SELECT count(*) FROM blob")) == 1
        assert await conn.scalar(sa.text("SELECT count(*) FROM user")) == 1

    # En weer omhoog gaat ook
    await migrate(settings, command.upgrade, "head")
    assert (await schema(settings))["alembic_version"] == ["0007"]


@pytest.mark.anyio
async def test_modellen_gelijk_aan_de_migratie(tmp_path):
    """OfficeSession en de kolommen op blob in models.py zijn precies wat 0007 maakt."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head")

    def diff(connection):
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": False}
        )
        return compare_metadata(context, Base.metadata)

    async with get_engine(settings).connect() as conn:
        changes = await conn.run_sync(diff)

    def ours(change) -> bool:
        text = repr(change)
        return "office_session" in text or "pdf_" in text or "ix_blob_pdf" in text

    assert [c for c in changes if ours(c)] == []


@pytest.mark.anyio
async def test_lege_database_naar_0007(tmp_path):
    settings = settings_at(tmp_path, "leeg")
    await migrate(settings, command.upgrade, "head")
    found = await schema(settings)
    assert "office_session" in found
    assert found["alembic_version"] == ["0007"]
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO office_session (key, name, file_type, blob_id, node_name, "
                "created_at, updated_at) VALUES ('k', 'a.docx', 'docx', :sha, 'n', :t, :t)"
            ),
            {"sha": "b" * 64, "t": datetime.now(UTC).isoformat()},
        )
        state = await conn.scalar(sa.text("SELECT state FROM office_session"))
    assert state == "pending"
