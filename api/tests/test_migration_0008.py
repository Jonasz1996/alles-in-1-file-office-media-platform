"""Migratie 0008 (bouwontwerp fase 5, §3.1 en §3.8): een database van 0007 met data, omhoog,
de tabellen van media met hun kolommen, indexen en foreign keys, de standaarden en CASCADE, en
weer terug naar precies een verse 0007 (zodat v0.6.0 weer start: db-downgrade 0007, Keuze 47).
Daarna weer omhoog."""

import uuid
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app.db import get_engine
from app.models import Base
from tests.test_migration_0006 import migrate
from tests.test_migration_0007 import (
    SESSION,
    fill_0006,
    schema,
    session_row,
    settings_at,
)

NOW = datetime(2026, 10, 28, 9, 0)
SHA = "a" * 64

MEDIA_TABLES = {
    "media_library",
    "media_probe",
    "media_show",
    "media_album",
    "media_item",
    "playback_state",
    "media_playlist",
    "media_playlist_item",
}

COLUMNS = {
    "media_library": [
        "id", "owner_id", "root_id", "kind", "name", "language", "online", "scan_state",
        "scanned_at", "item_count", "created_at",
    ],
    "media_probe": [
        "blob_sha", "version", "state", "container", "duration_ms", "bit_rate", "start_ms",
        "streams", "chapters", "tags", "keyframes", "keyframes_state", "max_gop_ms",
        "subs_state", "trick_state", "trick", "audio_state", "audio", "truncated", "error",
        "probed_at",
    ],
    "media_show": [
        "id", "library_id", "folder_id", "title", "sort_title", "year", "overview", "ids",
        "art", "locked", "match_state", "candidates",
    ],
    "media_album": [
        "id", "library_id", "key", "title", "sort_title", "album_artist", "year",
        "compilation", "mbid", "art", "match_state", "track_count", "duration_ms",
    ],
    "media_item": [
        "node_id", "library_id", "kind", "extra", "parent_id", "present", "title",
        "sort_title", "year", "show_id", "season", "episode", "episode_end", "absolute",
        "aired", "album_id", "disc", "track", "artist", "part", "edition", "overview", "ids",
        "art", "subs", "source", "confidence", "names_version", "locked", "match_state",
        "candidates", "added_at", "updated_at",
    ],
    "playback_state": [
        "user_id", "profile", "node_id", "position_ms", "duration_ms", "watched",
        "play_count", "audio", "subtitle", "blob_sha", "origin", "seq", "play_started_at",
        "watched_at", "updated_at",
    ],
    "media_playlist": ["id", "owner_id", "name", "created_at", "updated_at"],
    "media_playlist_item": ["id", "playlist_id", "node_id", "position", "added_at"],
}  # fmt: skip

INDEXES = {
    "media_library": {"ix_media_library_owner": ["owner_id"]},
    "media_item": {
        "ix_media_item_lib": ["library_id", "present", "kind", "sort_title"],
        "ix_media_item_added": ["library_id", "present", "added_at"],
        "ix_media_item_show": ["show_id", "season", "episode"],
        "ix_media_item_album": ["album_id", "disc", "track"],
        "ix_media_item_parent": ["parent_id"],
    },
    "playback_state": {"ix_playback_recent": ["user_id", "profile", "watched", "updated_at"]},
    "media_playlist": {"ix_media_playlist_owner": ["owner_id"]},
    "media_playlist_item": {"ix_media_playlist_item": ["playlist_id", "position"]},
}

# (tabel, kolom) -> (doeltabel, on_delete)
FKS = {
    ("media_library", "owner_id"): ("user", "CASCADE"),
    ("media_library", "root_id"): ("node", "CASCADE"),
    ("media_probe", "blob_sha"): ("blob", "CASCADE"),
    ("media_show", "library_id"): ("media_library", "CASCADE"),
    ("media_show", "folder_id"): ("node", "SET NULL"),
    ("media_album", "library_id"): ("media_library", "CASCADE"),
    ("media_item", "node_id"): ("node", "CASCADE"),
    ("media_item", "library_id"): ("media_library", "CASCADE"),
    ("media_item", "parent_id"): ("media_item", "SET NULL"),
    ("media_item", "show_id"): ("media_show", "SET NULL"),
    ("media_item", "album_id"): ("media_album", "SET NULL"),
    ("playback_state", "user_id"): ("user", "CASCADE"),
    ("playback_state", "node_id"): ("node", "CASCADE"),
    ("media_playlist", "owner_id"): ("user", "CASCADE"),
    ("media_playlist_item", "playlist_id"): ("media_playlist", "CASCADE"),
    ("media_playlist_item", "node_id"): ("node", "CASCADE"),
}


async def ids_of(settings) -> dict[str, uuid.UUID]:
    """De gebruiker, zijn map en het bestand uit fill_0006."""
    async with get_engine(settings).connect() as conn:
        user = await conn.scalar(sa.text("SELECT id FROM user"))
        root = await conn.scalar(sa.text("SELECT id FROM node WHERE kind = 'dir'"))
        file = await conn.scalar(sa.text("SELECT id FROM node WHERE kind = 'file'"))
    return {"user": uuid.UUID(str(user)), "root": uuid.UUID(str(root)), "file": uuid.UUID(file)}


async def fill_media(settings, ids: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    """In elke tabel van 0008 een rij, met zo min mogelijk velden (de rest is standaard)."""
    lib, show, album, playlist = (uuid.uuid4() for _ in range(4))
    t = datetime.now(UTC).isoformat()
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO media_library (id, owner_id, root_id, kind, name, created_at) "
                "VALUES (:id, :owner, :root, 'movies', 'Films', :t)"
            ),
            {"id": lib.hex, "owner": ids["user"].hex, "root": ids["root"].hex, "t": t},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO media_probe (blob_sha, version, state, probed_at) "
                "VALUES (:sha, 1, 'ready', :t)"
            ),
            {"sha": SHA, "t": t},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO media_show (id, library_id, folder_id, title, sort_title) "
                "VALUES (:id, :lib, :root, 'Serie', 'serie')"
            ),
            {"id": show.hex, "lib": lib.hex, "root": ids["root"].hex},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO media_album (id, library_id, key, title, sort_title, album_artist) "
                "VALUES (:id, :lib, 'a|b', 'B', 'b', 'A')"
            ),
            {"id": album.hex, "lib": lib.hex},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO media_item (node_id, library_id, kind, title, sort_title, "
                "show_id, album_id, aired, added_at, updated_at) VALUES (:node, :lib, "
                "'episode', 'Aflevering', 'aflevering', :show, :album, :aired, :t, :t)"
            ),
            {
                "node": ids["file"].hex,
                "lib": lib.hex,
                "show": show.hex,
                "album": album.hex,
                "aired": date(2024, 1, 2).isoformat(),
                "t": t,
            },
        )
        await conn.execute(
            sa.text(
                "INSERT INTO playback_state (user_id, node_id, updated_at) "
                "VALUES (:user, :node, :t)"
            ),
            {"user": ids["user"].hex, "node": ids["file"].hex, "t": t},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO media_playlist (id, owner_id, name, created_at, updated_at) "
                "VALUES (:id, :owner, 'Lijst', :t, :t)"
            ),
            {"id": playlist.hex, "owner": ids["user"].hex, "t": t},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO media_playlist_item (id, playlist_id, node_id, position, added_at) "
                "VALUES (:id, :pl, :node, 0, :t)"
            ),
            {"id": uuid.uuid4().hex, "pl": playlist.hex, "node": ids["file"].hex, "t": t},
        )
    return {"library": lib, "show": show, "album": album, "playlist": playlist}


async def count(settings, table: str) -> int:
    async with get_engine(settings).connect() as conn:
        return await conn.scalar(sa.text(f'SELECT count(*) FROM "{table}"'))


@pytest.mark.anyio
async def test_van_0007_naar_0008_en_terug(tmp_path):
    settings = settings_at(tmp_path, "oud")
    fresh = settings_at(tmp_path, "vers")
    await migrate(settings, command.upgrade, "0007")
    await migrate(fresh, command.upgrade, "0007")
    file_id = await fill_0006(settings)
    async with get_engine(settings).begin() as conn:
        await conn.execute(SESSION.insert().values(**session_row("k1", file_id, "open")))
    before = await schema(settings)
    assert before == await schema(fresh)
    assert not MEDIA_TABLES & set(before)

    await migrate(settings, command.upgrade, "0008")
    after = await schema(settings)
    assert after["alembic_version"] == ["0008"]
    assert MEDIA_TABLES <= set(after)
    # De tabellen van 0007 en eerder veranderen niet
    for table, info in before.items():
        if table not in ("index_sql", "alembic_version"):
            assert after[table] == info, table
    for table, names in COLUMNS.items():
        assert [c[1] for c in after[table]["columns"]] == names, table
    for table, wanted in INDEXES.items():
        found = after[table]["indexes"]
        for name, cols in wanted.items():
            assert found[name][3] == cols, (table, name)
            assert found[name][0] == 0, (table, name)  # niet uniek
    # De unieke grenzen (benoemd, zodat de downgrade en de modellen ze herkennen)
    uniques = {
        table: sorted(tuple(i[3]) for i in info["indexes"].values() if i[0] == 1 and i[1] == "u")
        for table, info in after.items()
        if table in MEDIA_TABLES
    }
    assert uniques["media_library"] == [("root_id",)]
    assert uniques["media_show"] == [("library_id", "folder_id")]
    assert uniques["media_album"] == [("library_id", "key")]
    found_fks = {}
    for table in MEDIA_TABLES:
        for fk in after[table]["fks"]:
            # PRAGMA foreign_key_list: (table, from, to, on_update, on_delete, match)
            found_fks[(table, fk[1])] = (fk[0], fk[4])
    assert found_fks == FKS

    # De standaarden van §3.1
    await fill_media(settings, await ids_of(settings))
    async with get_engine(settings).connect() as conn:
        lib = (
            await conn.execute(
                sa.text("SELECT language, online, scan_state, item_count FROM media_library")
            )
        ).one()
        probe = (
            await conn.execute(
                sa.text(
                    "SELECT start_ms, streams, chapters, tags, keyframes_state, subs_state, "
                    "trick_state, audio_state, truncated FROM media_probe"
                )
            )
        ).one()
        item = (
            await conn.execute(
                sa.text(
                    "SELECT present, source, confidence, names_version, match_state, ids, "
                    "subs, locked, candidates, aired FROM media_item"
                )
            )
        ).one()
        progress = (
            await conn.execute(
                sa.text(
                    "SELECT profile, position_ms, duration_ms, watched, play_count, seq "
                    "FROM playback_state"
                )
            )
        ).one()
        album = (
            await conn.execute(
                sa.text("SELECT compilation, match_state, track_count FROM media_album")
            )
        ).one()
    assert tuple(lib) == ("nl-NL", 0, "idle", 0)
    assert tuple(probe) == (0, "[]", "[]", "{}", "none", "none", "none", "none", "{}")
    assert tuple(item) == (1, "name", 0.0, 0, "local", "{}", "[]", "[]", "[]", "2024-01-02")
    assert tuple(progress) == ("", 0, 0, 0, 0, 0)
    assert tuple(album) == (0, "local", 0)

    # Uniek: één bibliotheek per map
    with pytest.raises(IntegrityError):
        async with get_engine(settings).begin() as conn:
            ids = await ids_of(settings)
            await conn.execute(
                sa.text(
                    "INSERT INTO media_library (id, owner_id, root_id, kind, name, created_at) "
                    "VALUES (:id, :owner, :root, 'music', 'Muziek', :t)"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "owner": ids["user"].hex,
                    "root": ids["root"].hex,
                    "t": NOW.isoformat(),
                },
            )

    # Het bestand definitief weg: item, voortgang en afspeellijstregel gaan mee (CASCADE)
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text("DELETE FROM node WHERE kind = 'file'"))
    for table in ("media_item", "playback_state", "media_playlist_item"):
        assert await count(settings, table) == 0, table
    assert await count(settings, "media_playlist") == 1
    assert await count(settings, "media_library") == 1
    # De blob weg: de probe gaat mee
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text("DELETE FROM blob"))
    assert await count(settings, "media_probe") == 0

    await migrate(settings, command.downgrade, "0007")
    back = await schema(settings)
    assert back == before
    assert await count(settings, "user") == 1
    assert await count(settings, "office_session") == 1

    # En weer omhoog gaat ook
    await migrate(settings, command.upgrade, "head")
    assert (await schema(settings))["alembic_version"] == ["0008"]
    assert await count(settings, "media_library") == 0


@pytest.mark.anyio
async def test_media_item_extra_naar_de_film(tmp_path):
    """parent_id: de film van een extra. De film weg, dan blijft de extra (SET NULL)."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head")
    await fill_0006(settings)
    ids = await ids_of(settings)
    made = await fill_media(settings, ids)
    extra_node = uuid.uuid4()
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO node (id, owner_id, parent_id, name, kind, size, search_name, "
                "created_at, modified_at) VALUES (:id, :owner, :root, 'trailer.mkv', 'file', "
                "1, 'trailer.mkv', :t, :t)"
            ),
            {
                "id": extra_node.hex,
                "owner": ids["user"].hex,
                "root": ids["root"].hex,
                "t": NOW.isoformat(),
            },
        )
        await conn.execute(
            sa.text(
                "INSERT INTO media_item (node_id, library_id, kind, extra, parent_id, title, "
                "sort_title, added_at, updated_at) VALUES (:node, :lib, 'extra', 'trailer', "
                ":parent, 'Trailer', 'trailer', :t, :t)"
            ),
            {
                "node": extra_node.hex,
                "lib": made["library"].hex,
                "parent": ids["file"].hex,
                "t": NOW.isoformat(),
            },
        )
        await conn.execute(sa.text("DELETE FROM node WHERE name = 'verslag.docx'"))
        left = (await conn.execute(sa.text("SELECT kind, parent_id FROM media_item"))).all()
    assert [tuple(r) for r in left] == [("extra", None)]
    # De map weg: de bibliotheek, de serie en het album gaan mee
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text("DELETE FROM node"))
    for table in ("media_library", "media_show", "media_album", "media_item"):
        assert await count(settings, table) == 0, table


@pytest.mark.anyio
async def test_modellen_gelijk_aan_de_migratie(tmp_path):
    """De klassen van media in models.py zijn precies wat 0008 maakt."""
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
        return any(name in text for name in MEDIA_TABLES) or "playback" in text

    assert [c for c in changes if ours(c)] == []


@pytest.mark.anyio
async def test_lege_database_naar_0008(tmp_path):
    settings = settings_at(tmp_path, "leeg")
    await migrate(settings, command.upgrade, "head")
    found = await schema(settings)
    assert MEDIA_TABLES <= set(found)
    assert found["alembic_version"] == ["0008"]
