"""Migratie 0010 (bouwontwerp fase 6, §3.1 en §3.8): leeg naar head; een database van 0009 met
data heen, terug en weer heen, met en zonder FTS5; num, de paden, dead en de FTS-tabellen
kloppen; de modellen zijn gelijk aan de migratie; en de downgrade is precies 0009 (zodat
v0.7.1 weer start: app.cli db-downgrade 0009)."""

import time
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db import get_engine
from app.models import Base
from app.search import schema
from tests.search.migratie import (
    NOW,
    check_fts,
    check_tree,
    data,
    fill_0009,
    migrate,
    names,
    settings_at,
    words,
)
from tests.test_migration_0007 import schema as schema_of

NEW_NODE_COLUMNS = {"num", "mpath", "dead", "ftype"}


async def objects(settings, kind: str) -> dict[str, str]:
    async with get_engine(settings).connect() as conn:
        rows = await conn.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = ?", (kind,)
        )
        return {r[0]: r[1] for r in rows.all()}


async def xcolumns(settings, table: str) -> set[str]:
    """Ook de verborgen (virtuele) kolommen, die PRAGMA table_info niet noemt."""
    async with get_engine(settings).connect() as conn:
        rows = await conn.exec_driver_sql(f'PRAGMA table_xinfo("{table}")')
        return {r[1] for r in rows.all()}


@pytest.mark.anyio
async def test_lege_database_naar_head(tmp_path):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head")
    found = await schema_of(settings)
    assert found["alembic_version"] == ["0010"]
    assert set(schema.NEW_TABLES) <= set(found)
    assert {"fts_node", "fts_name"} <= set(found)
    assert NEW_NODE_COLUMNS <= await xcolumns(settings, "node")
    assert {"source", "shared"} <= await xcolumns(settings, "album_item")
    assert set(schema.NODE_INDEXES) <= set(found["node"]["indexes"])
    assert await objects(settings, "trigger") == schema.TRIGGERS
    assert await objects(settings, "view") == {"search_src": schema.VIEW_SQL}
    async with get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT id, next FROM search_seq")).all() == [(1, 1)]
        state = (await conn.exec_driver_sql("SELECT * FROM search_state")).all()
        assert state == [(1, None, None, None)]


@pytest.mark.anyio
@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_van_0009_met_data_heen_terug_en_weer_heen(tmp_path, fts5):
    settings = settings_at(tmp_path, "oud")
    fresh = settings_at(tmp_path, "vers")
    await migrate(settings, command.upgrade, "0009")
    await migrate(fresh, command.upgrade, "0009")
    ids = await fill_0009(settings)
    before = await schema_of(settings)
    assert before == await schema_of(fresh)
    rows = await data(settings)

    await migrate(settings, command.upgrade, "0010", fts5=fts5)
    after = await schema_of(settings)
    assert after["alembic_version"] == ["0010"]
    assert await data(settings) == rows  # niets aan de bestanden zelf veranderd
    triggers = schema.TRIGGERS if fts5 else schema.TRIGGERS_NO_FTS
    assert await objects(settings, "trigger") == triggers
    assert bool(await objects(settings, "view")) is fts5
    assert ("fts_node" in after) is fts5
    async with get_engine(settings).connect() as conn:
        assert await conn.run_sync(check_tree) == len(rows["node"])
        dead = dict(
            (await conn.exec_driver_sql("SELECT name, dead FROM node WHERE kind <> 'dir'")).all()
        )
        # vakantie ligt in de prullenbak, dus ook strand en schelp (in diep); los zelf ook
        assert dead == {
            "verslag.x": 0,
            "strand.x": 1,
            "schelp.x": 1,
            "los.x": 1,
            "notities.x": 0,
            "zomer": 0,
        }
        shared = (await conn.exec_driver_sql("SELECT source, shared FROM album_item")).all()
        assert shared == [("hand", 1), ("hand", 1)]
        if fts5:
            await conn.run_sync(check_fts)
            # Zoeken op naam werkt meteen na de migratie; de inhoud komt met de backfill
            assert await conn.run_sync(names, "rslag.x") == {"verslag.x"}
            assert await conn.run_sync(words, "jaarverslag") == {"verslag.x"}  # de titel
            assert await conn.run_sync(words, "body:jaarverslag") == set()

    await migrate(settings, command.downgrade, "0009")
    assert await schema_of(settings) == before
    assert await schema_of(settings) == await schema_of(fresh)
    assert await objects(settings, "trigger") == {}
    assert await objects(settings, "view") == {}
    assert not NEW_NODE_COLUMNS & await xcolumns(settings, "node")
    assert await data(settings) == rows

    # En weer heen
    await migrate(settings, command.upgrade, "0010", fts5=fts5)
    assert (await schema_of(settings))["alembic_version"] == ["0010"]
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(check_tree)
        if fts5:
            await conn.run_sync(check_fts)
    assert ids["album"]


@pytest.mark.anyio
@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_de_triggers_houden_alles_bij(tmp_path, fts5):
    """Na elke stap (nieuw, hernoemen, verplaatsen, prullenbak in en uit, wissen met cascade,
    nieuwe inhoud, tekst, titel, OCR van een foto, een andere eigenaar): num, pad en dead kloppen,
    en de FTS-tabellen zijn gelijk aan hun inhoud."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "0010", fts5=fts5)
    h = {k: v.hex for k, v in ids.items()}
    new = uuid.uuid4().hex

    steps = [
        # nieuw bestand diep in de boom
        (
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, created_at, "
            "modified_at, search_name) VALUES (:new, :verslagen, :eva, 'Begroting.xlsx', 'file', "
            "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 1, :t, :t, "
            "'begroting.xlsx')"
        ),
        "UPDATE node SET name = 'Raming.xlsx', search_name = 'raming.xlsx' WHERE id = :new",
        # een map verplaatsen: de hele submap krijgt een nieuw pad
        "UPDATE node SET parent_id = :root WHERE id = :verslagen",
        "UPDATE node SET parent_id = :diep WHERE id = :werk",  # in een map in de prullenbak
        "UPDATE node SET parent_id = :root WHERE id = :werk",
        "UPDATE node SET deleted_at = :t WHERE id = :werk",
        "UPDATE node SET deleted_at = NULL WHERE id = :vakantie",  # diep komt terug
        "UPDATE node SET deleted_at = :t WHERE id = :diep",
        "UPDATE node SET deleted_at = NULL WHERE id = :werk",
        # wissen met de cascade van de foreign key
        "DELETE FROM node WHERE id = :diep",
        # nieuwe inhoud
        f"UPDATE node SET blob_id = '{'f' * 64}' WHERE id = :new",
    ]
    if fts5:
        steps += [
            "INSERT INTO blob_text (blob_sha, body, updated_at) VALUES "
            f"('{'f' * 64}', 'De Straße en GROẞE begroting', :t)",
            f"UPDATE blob_text SET meta = 'Jonas' WHERE blob_sha = '{'f' * 64}'",
            "UPDATE media_item SET title = 'Kwartaalcijfers' WHERE node_id = :verslag",
            "INSERT INTO photo_ocr (node_id, owner_id, text, words, angle, updated_at) VALUES "
            "(:strand, :eva, 'kassabon', x'00', 0, :t)",
        ]
    steps += [
        # een andere eigenaar: de foto-rijen van de oude gaan weg
        "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) VALUES "
        "(:notities, :eva, 52.1, 5.1, 1, 2, 'exif')",
        "UPDATE node SET owner_id = :bram WHERE id = :notities",
        "UPDATE node SET blob_id = NULL WHERE id = :strand",
    ]
    async with get_engine(settings).begin() as conn:
        for sql in steps:
            await conn.execute(sa.text(sql), {**h, "new": new, "t": NOW})
            await conn.run_sync(check_tree)
            if fts5:
                await conn.run_sync(check_fts)
        if fts5:
            assert await conn.run_sync(names, "raming") == {"Raming.xlsx"}
            assert await conn.run_sync(names, "begroting") == set()
            # De tekst hoort bij de inhoud: elk bestand met die blob (ook in de prullenbak;
            # de vraag filtert op dead) vindt hem
            same = {"Raming.xlsx", "los.x", "notities.x"}
            assert await conn.run_sync(words, "strasse") == same  # ß als ss
            assert await conn.run_sync(words, "grosse") == same  # ẞ als ss
            assert await conn.run_sync(words, "meta:jonas") == same
            assert await conn.run_sync(words, "kwartaalcijfers") == {"verslag.x"}
            assert await conn.run_sync(words, "jaarverslag") == set()
            # de OCR van strand ging weg met de inhoud
            assert await conn.run_sync(words, "kassabon") == set()
            text = (await conn.exec_driver_sql("SELECT body FROM blob_text")).scalar()
            assert "Straße" in text  # de opgeslagen tekst houdt de ß
        assert (await conn.exec_driver_sql("SELECT count(*) FROM photo_geo")).scalar() == 0
        kinds = dict((await conn.exec_driver_sql("SELECT name, ftype FROM node")).all())
        assert kinds["Raming.xlsx"] == "spreadsheet"
        assert kinds["werk"] == "folder"
        assert kinds["zomer"] == "other"


@pytest.mark.anyio
async def test_modellen_gelijk_aan_de_migratie(tmp_path):
    """compare_metadata tussen models.py en 0010, met de FTS-tabellen en hun schaduwtabellen
    eruit (de view ziet compare_metadata niet)."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head")

    def diff(connection):
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": False}
        )
        return compare_metadata(context, Base.metadata)

    async with get_engine(settings).connect() as conn:
        changes = await conn.run_sync(diff)
    fts = set(schema.FTS_TABLES) | set(schema.FTS_SHADOW)
    removed = {c[1].name for c in changes if isinstance(c, tuple) and c[0] == "remove_table"}
    assert removed <= fts, removed
    ours = set(schema.NEW_TABLES) | {"node", "album_item"}

    def table_of(change) -> str | None:
        if isinstance(change, list):
            return change[0][2]
        item = change[1]
        table = getattr(item, "table", item)
        return getattr(table, "name", None)

    left = [c for c in changes if table_of(c) in ours]
    # INTEGER PRIMARY KEY is in SQLite de rowid; PRAGMA table_info noemt hem nullable
    quirk = [
        c
        for c in left
        if isinstance(c, list) and c[0][0] == "modify_nullable" and c[0][3] == "id"
        and c[0][2] in ("search_seq", "search_state")
    ]  # fmt: skip
    assert [c for c in left if c not in quirk] == []


@pytest.mark.anyio
async def test_downgrade_haalt_alles_weg_ook_met_rijen_in_de_nieuwe_tabellen(tmp_path):
    """Rijen in de nieuwe tabellen (personen, plekken, tekst, regels) houden de downgrade niet
    tegen; bestanden, delingen en albums blijven."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    rows = await data(settings)
    await migrate(settings, command.upgrade, "head")
    h = {k: v.hex for k, v in ids.items()}
    person = uuid.uuid4().hex
    async with get_engine(settings).begin() as conn:
        for sql in (
            f"INSERT INTO blob_text (blob_sha, body, updated_at) VALUES ('{'d' * 64}', 'x', :t)",
            "INSERT INTO person (id, owner_id, name, search_name, created_at, updated_at) "
            "VALUES (:person, :eva, 'Oma', 'oma', :t, :t)",
            "INSERT INTO photo_person (person_id, node_id, owner_id, created_at) "
            "VALUES (:person, :strand, :eva, :t)",
            "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) VALUES "
            "(:strand, :eva, 52.1, 5.1, 1, 2, 'exif')",
            "INSERT INTO album_rule (album_id, rule, refreshed_at, count) "
            "VALUES (:album, '{}', :t, 0)",
            "UPDATE album_item SET source = 'rule', shared = 0 WHERE node_id = :strand",
            f"INSERT INTO analysis (blob_sha, step, version, state, queued_at, updated_at) "
            f"VALUES ('{'d' * 64}', 'text', 1, 'done', :t, :t)",
        ):
            await conn.execute(sa.text(sql), {**h, "person": person, "t": NOW})
    await migrate(settings, command.downgrade, "0009")
    assert await data(settings) == rows


@pytest.mark.anyio
@pytest.mark.zoek_perf
async def test_migratie_van_100000_nodes_binnen_10_seconden(tmp_path):
    """§3.1: het vullen (num, paden, dead, rebuild van beide FTS-tabellen) duurt < 10 s."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)

    def bulk(conn) -> None:
        dirs = [ids["root"].hex]
        rows = []
        for i in range(100_000):
            node_id = uuid.uuid4().hex
            is_dir = i % 17 == 0
            parent = dirs[(i * 7) % len(dirs)]
            rows.append((node_id, parent, ids["eva"].hex, f"bestand {i}", "dir" if is_dir
                         else "file", NOW, NOW, f"bestand {i}"))  # fmt: skip
            if is_dir:
                dirs.append(node_id)
        conn.exec_driver_sql(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, "
            "modified_at, search_name) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
            rows,
        )

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(bulk)
    start = time.monotonic()
    await migrate(settings, command.upgrade, "0010")
    took = time.monotonic() - start
    async with get_engine(settings).connect() as conn:
        assert await conn.run_sync(check_tree) > 100_000
    assert took < 10, took
