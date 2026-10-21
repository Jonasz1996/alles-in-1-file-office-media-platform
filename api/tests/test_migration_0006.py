"""Migratie 0006 op een database van fase 2 (0005) met data: de nieuwe kolommen en tabellen,
de terugvaldatum voor bestaande foto's en video's, en weer terug."""

import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from alembic import command

from app.config import Settings
from app.db import get_engine
from app.migrate import alembic_config
from app.models import Album, AlbumItem, Node
from tests.factories import create_album, create_file, create_user, maker

TZ = "Europe/Amsterdam"


async def migrate(settings: Settings, fn, revision: str) -> None:
    cfg = alembic_config(settings.db_url)

    def run(connection) -> None:
        cfg.attributes["connection"] = connection
        cfg.attributes["timezone"] = TZ
        fn(cfg, revision)

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(run)


async def columns(settings: Settings, table: str) -> set[str]:
    async with get_engine(settings).connect() as conn:
        rows = (await conn.execute(sa.text(f'PRAGMA table_info("{table}")'))).all()
    return {r[1] for r in rows}


async def tables(settings: Settings) -> set[str]:
    async with get_engine(settings).connect() as conn:
        rows = await conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table'"))
        return {r[0] for r in rows}


async def indexes(settings: Settings) -> set[str]:
    async with get_engine(settings).connect() as conn:
        rows = await conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='index'"))
        return {r[0] for r in rows}


# Alleen de kolommen van 0005: de modellen van nu kennen er meer
USER = sa.table(
    "user",
    *(sa.column(c) for c in ("email", "display_name", "password_hash", "role", "quota_bytes")),
    sa.column("id", sa.Uuid()),
    sa.column("root_id", sa.Uuid()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
NODE = sa.table(
    "node",
    sa.column("id", sa.Uuid()),
    sa.column("parent_id", sa.Uuid()),
    sa.column("owner_id", sa.Uuid()),
    *(sa.column(c) for c in ("name", "kind", "mime", "size", "blob_id", "search_name")),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("modified_at", sa.DateTime(timezone=True)),
)
BLOB = sa.table(
    "blob",
    *(sa.column(c) for c in ("sha256", "size", "ref_count", "mime")),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


@pytest.mark.anyio
async def test_van_0005_naar_0006_en_terug(tmp_path):
    settings = Settings(data_dir=tmp_path, jwt_secret="test" * 8, timezone=TZ)
    await migrate(settings, command.upgrade, "0005")
    assert "album" not in await tables(settings)

    user_id, root_id = uuid.uuid4(), uuid.uuid4()
    ids = {k: uuid.uuid4() for k in ("foto", "video", "tekst", "map")}
    summer = datetime(2024, 7, 1, 22, 30)  # UTC; in Amsterdam al 2 juli, 00:30
    winter = datetime(2024, 1, 15, 8, 0)  # UTC; in Amsterdam 09:00
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
                created_at=winter,
            )
        )
        await conn.execute(
            BLOB.insert(),
            [
                {"sha256": c * 64, "size": 1, "ref_count": 1, "mime": m, "created_at": winter}
                for c, m in (("a", "image/jpeg"), ("b", "text/plain"))
            ],
        )
        base = {"owner_id": user_id, "size": 1, "created_at": winter}
        await conn.execute(
            NODE.insert(),
            [
                {**base, "id": root_id, "parent_id": None, "name": "", "kind": "dir",
                 "mime": None, "blob_id": None, "search_name": "", "modified_at": winter},
                {**base, "id": ids["foto"], "parent_id": root_id, "name": "a.jpg",
                 "kind": "file", "mime": "image/jpeg", "blob_id": "a" * 64,
                 "search_name": "a.jpg", "modified_at": summer},
                {**base, "id": ids["video"], "parent_id": root_id, "name": "v.mp4",
                 "kind": "file", "mime": "video/mp4", "blob_id": "a" * 64,
                 "search_name": "v.mp4", "modified_at": winter},
                {**base, "id": ids["tekst"], "parent_id": root_id, "name": "t.txt",
                 "kind": "file", "mime": "text/plain", "blob_id": "b" * 64,
                 "search_name": "t.txt", "modified_at": summer},
                {**base, "id": ids["map"], "parent_id": root_id, "name": "Map", "kind": "dir",
                 "mime": None, "blob_id": None, "search_name": "map", "modified_at": summer},
            ],
        )  # fmt: skip

    await migrate(settings, command.upgrade, "head")
    assert {"locale", "disabled_at"} <= await columns(settings, "user")
    assert {"taken_at", "meta_version", "render_version"} <= await columns(settings, "blob")
    assert {"taken_at", "taken_day"} <= await columns(settings, "node")
    assert "client_mtime" in await columns(settings, "upload")
    assert {"album", "album_item"} <= await tables(settings)
    assert {
        "ix_node_owner_taken",
        "ix_node_blob",
        "ix_node_parent_order",
        "ix_album_item_node",
    } <= await indexes(settings)

    node = sa.table(
        "node",
        sa.column("id", sa.Uuid()),
        sa.column("taken_at", sa.DateTime()),
        sa.column("taken_day", sa.Integer()),
    )
    blob = sa.table("blob", sa.column("sha256"), sa.column("meta_version"), sa.column("taken_at"))
    async with get_engine(settings).connect() as conn:
        found = {
            r.id: (r.taken_at, r.taken_day)
            for r in await conn.execute(sa.select(node.c.id, node.c.taken_at, node.c.taken_day))
        }
        blobs = (await conn.execute(sa.select(blob.c.meta_version, blob.c.taken_at))).all()
    # De kloktijd in de tijdzone van de server, als zwevende tijd
    assert found[ids["foto"]] == (datetime(2024, 7, 2, 0, 30), 20240702)
    assert found[ids["video"]] == (datetime(2024, 1, 15, 9, 0), 20240115)
    assert found[ids["tekst"]] == (None, None)
    assert found[ids["map"]] == (None, None)
    assert found[root_id] == (None, None)
    assert blobs == [(0, None), (0, None)]

    await migrate(settings, command.downgrade, "0005")
    assert not {"locale", "disabled_at"} & await columns(settings, "user")
    assert not {"taken_at", "meta_version", "render_version"} & await columns(settings, "blob")
    assert not {"taken_at", "taken_day"} & await columns(settings, "node")
    assert "client_mtime" not in await columns(settings, "upload")
    assert not {"album", "album_item"} & await tables(settings)
    assert not {"ix_node_owner_taken", "ix_node_blob", "ix_node_parent_order"} & await indexes(
        settings
    )
    async with get_engine(settings).connect() as conn:
        count = await conn.scalar(sa.text("SELECT count(*) FROM node"))
    assert count == 5  # de data zelf blijft

    # En weer omhoog: dezelfde terugvaldatums
    await migrate(settings, command.upgrade, "head")
    async with get_engine(settings).connect() as conn:
        again = await conn.scalar(sa.select(node.c.taken_day).where(node.c.id == ids["foto"]))
    assert again == 20240702


@pytest.mark.anyio
async def test_tijdzone_uit_de_instellingen(tmp_path):
    """app.migrate geeft settings.timezone mee aan de migratie."""
    from app.migrate import upgrade

    settings = Settings(data_dir=tmp_path, jwt_secret="test" * 8, timezone="UTC")
    await migrate(settings, command.upgrade, "0005")
    user_id, node_id = uuid.uuid4(), uuid.uuid4()
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            USER.insert().values(
                id=user_id,
                email="a@example.nl",
                display_name="A",
                password_hash="x",
                role="user",
                quota_bytes=0,
                root_id=None,
                created_at=datetime(2024, 1, 1),
            )
        )
        await conn.execute(
            NODE.insert().values(
                id=node_id,
                parent_id=None,
                owner_id=user_id,
                name="x.png",
                kind="file",
                mime="image/png",
                size=0,
                blob_id=None,
                search_name="x.png",
                created_at=datetime(2024, 1, 1),
                modified_at=datetime(2024, 7, 1, 22, 30),
            )
        )
    await upgrade(settings)
    async with get_engine(settings).connect() as conn:
        day = await conn.scalar(sa.text("SELECT taken_day FROM node"))
    assert day == 20240701  # in UTC nog 1 juli


@pytest.mark.anyio
async def test_album_als_node_met_items(settings):
    """De albumtabellen (via tests/factories.py): weg met het album gaan ook zijn items, en
    weg met een foto ook het item, niet het album."""
    async with maker(settings)() as session:
        owner = await create_user(session, "a@example.nl")
        photo = await create_file(session, owner, name="a.jpg", data=b"f", mime="image/jpeg")
        other = await create_file(session, owner, name="b.jpg", data=b"g", mime="image/jpeg")
        album = await create_album(session, owner, "Vakantie", items=[photo, other])
        await session.commit()
        album_id, photo_id = album.id, photo.id
        assert album.kind == "album" and album.parent_id is None
        assert (await session.get(Album, album_id)).description == ""

    count = sa.select(sa.func.count()).select_from(AlbumItem)
    async with maker(settings)() as session:
        assert await session.scalar(count) == 2
        await session.execute(sa.delete(Node).where(Node.id == photo_id))
        await session.commit()
        assert await session.scalar(count) == 1
        await session.execute(sa.delete(Node).where(Node.id == album_id))
        await session.commit()
        assert await session.scalar(count) == 0
        assert await session.scalar(sa.select(Album.id)) is None
