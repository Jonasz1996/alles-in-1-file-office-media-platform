"""Migratie 0012 (bouwontwerp fase 6, §3.1a en Keuze 76): van 0011 met data heen, terug en weer
heen, met en zonder FTS5. De tabellen, de index en de triggers zijn precies die van
app/search/schema.py; photo_pref krijgt beeld (TEXT NULL) en houdt bij terug zijn rijen; terug is
precies 0011, en na de downgrade en het checkpoint staat er geen vector van beeld meer in
platform.db of -wal. De modellen zijn gelijk aan 0011 en 0012 (compare_metadata).

En de tijd: 0010 naar 0012 op 100.000 nodes maakt alleen lege tabellen en schrijft niets in node
(gemeten aan de pagina's in de WAL)."""

import time
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import cli
from app import migrate as migrate_mod
from app.db import get_engine, sqlite_path, wal_truncate
from app.migrate import checkpoint
from app.models import Base
from app.search import schema
from tests.search.gezichten import fill_beeld, fill_faces, marks, rows
from tests.search.migratie import NOW, data, fill_0009, migrate, settings_at
from tests.search.test_migration_0010 import objects, xcolumns
from tests.test_migration_0007 import schema as schema_of

pytestmark = pytest.mark.anyio

BEELD = b"MERKTEKEN-BEELD"
PREF = "SELECT user_id, location, memories, ocr_photos, faces FROM photo_pref ORDER BY user_id"


async def _pref(settings) -> list[tuple]:
    async with get_engine(settings).connect() as conn:
        return [tuple(r) for r in (await conn.exec_driver_sql(PREF)).all()]


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_van_0011_met_data_heen_terug_en_weer_heen(tmp_path, fts5):
    settings = settings_at(tmp_path, "oud")
    fresh = settings_at(tmp_path, "vers")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "0011", fts5=fts5)
    await migrate(fresh, command.upgrade, "0011", fts5=fts5)
    await fill_faces(settings, ids)
    before = await schema_of(settings)
    assert before == await schema_of(fresh)
    files = await data(settings)
    faces = {t: await rows(settings, t) for t in schema.TABLES_0011}
    old_triggers = {
        **(schema.TRIGGERS_0010 if fts5 else schema.TRIGGERS_NO_FTS_0010),
        **schema.TRIGGERS_0011,
    }
    assert await objects(settings, "trigger") == old_triggers

    await migrate(settings, command.upgrade, "0012", fts5=fts5)
    after = await schema_of(settings)
    assert after["alembic_version"] == ["0012"]
    assert set(after) - set(before) == set(schema.TABLES_0012)
    # Geen bestaande tabel veranderd behalve photo_pref, en die alleen met beeld erbij
    old = set(before) - {"alembic_version", "index_sql", "photo_pref"}
    assert {t: after[t] for t in old} == {t: before[t] for t in old}
    assert after["photo_pref"]["columns"][:-1] == before["photo_pref"]["columns"]
    assert after["photo_pref"]["columns"][-1][1:] == ("beeld", "TEXT", 0, None, 0)
    assert set(before["index_sql"]) <= set(after["index_sql"])
    assert await objects(settings, "trigger") == {**old_triggers, **schema.TRIGGERS_0012}
    indexes = await objects(settings, "index")
    assert {k: indexes[k] for k in schema.INDEXES_0012} == schema.INDEXES_0012
    assert await data(settings) == files
    assert {t: await rows(settings, t) for t in schema.TABLES_0011} == faces
    # Geen rij voor wie er al was: gen begint bij de eerste verandering (Keuze 76)
    assert await rows(settings, "beeld_owner") == []

    await fill_beeld(settings, ids)
    prefs = await _pref(settings)
    assert len(prefs) == 1
    assert marks(settings, BEELD)["platform.db"] + marks(settings, BEELD)["platform.db-wal"] > 0

    await migrate(settings, command.downgrade, "0011")
    assert await checkpoint(settings)
    assert await schema_of(settings) == before
    assert await schema_of(settings) == await schema_of(fresh)
    assert "beeld" not in await xcolumns(settings, "photo_pref")
    assert await objects(settings, "trigger") == old_triggers
    assert await data(settings) == files
    assert {t: await rows(settings, t) for t in schema.TABLES_0011} == faces
    assert await _pref(settings) == prefs  # de andere foto-instellingen blijven
    assert marks(settings, BEELD) == {"platform.db": 0, "platform.db-wal": 0}

    # En weer heen: beeld is weer NULL (de standaard), de tabellen leeg
    await migrate(settings, command.upgrade, "0012", fts5=fts5)
    async with get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT beeld FROM photo_pref")).all() == [(None,)]
    for table in schema.TABLES_0012:
        assert await rows(settings, table) == []
    assert await objects(settings, "trigger") == {**old_triggers, **schema.TRIGGERS_0012}


async def test_terug_naar_0010_en_0009_in_een_keer(tmp_path):
    """Wat db-downgrade 0010 (terug naar 0.8.0) en 0009 (0.7.1) doen: in één transactie langs
    0012 en 0011, met rijen in alle nieuwe tabellen."""
    for target in ("0010", "0009"):
        settings = settings_at(tmp_path, f"db{target}")
        fresh = settings_at(tmp_path, f"vers{target}")
        await migrate(settings, command.upgrade, "0009")
        await migrate(fresh, command.upgrade, target)
        ids = await fill_0009(settings)
        files = await data(settings)
        await migrate(settings, command.upgrade, "head")
        await fill_faces(settings, ids)
        await fill_beeld(settings, ids)
        await migrate(settings, command.downgrade, target)
        assert await checkpoint(settings)
        assert await schema_of(settings) == await schema_of(fresh)
        found = await data(settings)
        # De foto van fill_faces blijft; verder is alles zoals het was
        assert {k: [r for r in v if r not in files[k]] for k, v in found.items()} == {
            "node": [r for r in found["node"] if r[3] == "oma.jpg"],
            "share": [],
            "album": [],
            "album_item": [],
            "media_item": [],
            "blob": [],
        }
        assert marks(settings) == {"platform.db": 0, "platform.db-wal": 0}


def _wal(settings) -> int:
    path = sqlite_path(settings.db_url)
    assert path is not None
    wal = path.with_name(path.name + "-wal")
    return wal.stat().st_size if wal.exists() else 0


async def test_db_downgrade_maakt_zelf_de_wal_leeg(tmp_path):
    """Review A0, contract F1: app.cli.db_downgrade, zoals het commando hem aanroept, maakt na
    de downgrade zelf de WAL leeg. Hier geen checkpoint erna: zonder dat van db_downgrade blijven
    de gewiste gezichten en vectoren in platform.db staan (de nulpagina's staan dan alleen in
    -wal)."""
    for target in ("0010", "0009"):
        settings = settings_at(tmp_path, f"cli{target}")
        await migrate(settings, command.upgrade, "0009")
        ids = await fill_0009(settings)
        await migrate(settings, command.upgrade, "head")
        await fill_faces(settings, ids)
        await fill_beeld(settings, ids)
        path = sqlite_path(settings.db_url)
        assert path is not None
        assert not wal_truncate(path).busy  # zoals na een tijd: alles in platform.db
        assert marks(settings)["platform.db"] > 0 and _wal(settings) == 0

        assert await cli.db_downgrade(settings, target) == (0, True)
        assert marks(settings) == {"platform.db": 0, "platform.db-wal": 0}
        assert _wal(settings) == 0


async def test_db_downgrade_met_een_lezer_zegt_het_en_eindigt_met_3(tmp_path, monkeypatch, capsys):
    """Review A0, veilig F2: houdt een lezer het checkpoint na db-downgrade tegen, dan is de
    downgrade gedaan, maar staan de gewiste gezichten nog in platform.db. Het commando zegt dat
    op stderr en eindigt met 3 (niet 0); db-checkpoint maakt het daarna af."""
    import sqlite3

    import anyio

    from tests.test_cli_db import run_cli

    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "head")
    await fill_faces(settings, ids)
    await fill_beeld(settings, ids)
    path = sqlite_path(settings.db_url)
    assert path is not None
    assert not wal_truncate(path).busy
    monkeypatch.setattr(migrate_mod, "CHECKPOINT_BUDGET", 0.3)
    # Het commando draait zijn eigen event loop: de pool van deze loop eerst dicht
    await get_engine(settings).dispose()

    # De app: een verbinding die openblijft, en een verzoek dat net leest
    app = sqlite3.connect(path)
    app.execute("SELECT count(*) FROM node").fetchone()
    lezer = sqlite3.connect(path)
    try:
        lezer.execute("BEGIN")
        assert lezer.execute("SELECT count(*) FROM face").fetchone() == (4,)
        code = await anyio.to_thread.run_sync(cli._downgrade, settings, "0010", True)
        lezer.close()
        out, err = capsys.readouterr()
        assert code == 3 == cli.WAL_EXIT
        assert out.splitlines()[-1] == "De database staat nu op revisie 0010."
        assert err.strip() == cli.WAL_BUSY
        assert "db-checkpoint" in cli.WAL_BUSY
        # De downgrade is gedaan, maar de gezichten staan nog in platform.db
        assert marks(settings)["platform.db"] > 0

        result = await run_cli(settings, "db-checkpoint")
        assert result.returncode == 0, result.stderr.decode()
        assert result.stdout.decode().strip() == "De WAL is leeg."
        assert marks(settings) == {"platform.db": 0, "platform.db-wal": 0}
        assert _wal(settings) == 0
    finally:
        lezer.close()
        app.close()
    async with get_engine(settings).connect() as conn:
        found = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        assert found.scalars().all() == ["0010"]


async def test_db_checkpoint_zonder_database(tmp_path):
    from tests.test_cli_db import run_cli

    settings = settings_at(tmp_path, "leeg")
    result = await run_cli(settings, "db-checkpoint")
    assert result.returncode == 1
    assert result.stderr.decode().startswith("Er is nog geen database (")
    assert not (settings.data_dir / "platform.db").exists()


async def test_de_migratie_bij_het_starten_maakt_zelf_de_wal_leeg(tmp_path):
    """Review A0, contract F1: app.migrate.upgrade (de start van de app) maakt na de migratie
    zelf de WAL leeg; 0010 naar 0012 schrijft tientallen pagina's in -wal."""
    settings = settings_at(tmp_path, "start")
    await migrate(settings, command.upgrade, "0009")
    await fill_0009(settings)
    await migrate(settings, command.upgrade, "0010")
    path = sqlite_path(settings.db_url)
    assert path is not None
    assert not wal_truncate(path).busy

    await migrate_mod.upgrade(settings)
    async with get_engine(settings).connect() as conn:
        found = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        assert found.scalars().all() == ["0012"]
    assert _wal(settings) == 0


async def test_modellen_gelijk_aan_0011_en_0012(tmp_path):
    """compare_metadata tussen models.py en de head, voor de tabellen van 0011 en 0012 en
    photo_pref: geen verschil."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "head")

    def diff(connection):
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": False}
        )
        return compare_metadata(context, Base.metadata)

    async with get_engine(settings).connect() as conn:
        changes = await conn.run_sync(diff)
    ours = set(schema.TABLES_0011) | set(schema.TABLES_0012) | {"photo_pref"}

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
        and c[0][2] == "blob_embed"
    ]  # fmt: skip
    assert [c for c in left if c not in quirk] == []
    # De indexen staan in de modellen met dezelfde naam en kolommen als in de migratie
    model_indexes = {
        i.name for t in ours for i in Base.metadata.tables[t].indexes if i.name is not None
    }
    assert model_indexes == set(schema.INDEXES_0011) | set(schema.INDEXES_0012)


@pytest.mark.zoek_perf
async def test_0010_naar_0012_op_100000_nodes(tmp_path):
    """§3.1a: 0011 en 0012 maken lege tabellen, indexen en triggers, en een kolom in photo_pref.
    Ze herschrijven node niet: de WAL heeft na de migratie maar een handvol pagina's, terwijl
    de database (vooral node en zijn indexen) er duizenden heeft. Een grens van 2 s voor de
    tijd."""
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
            mime = None if is_dir else ("image/jpeg" if i % 2 else "text/plain")
            rows.append((node_id, parent, ids["eva"].hex, f"bestand {i}", "dir" if is_dir
                         else "file", mime, NOW, NOW, f"bestand {i}"))  # fmt: skip
            if is_dir:
                dirs.append(node_id)
        conn.exec_driver_sql(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, created_at, "
            "modified_at, search_name) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            rows,
        )

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(bulk)
    await migrate(settings, command.upgrade, "0010")
    path = sqlite_path(settings.db_url)
    assert path is not None
    assert not wal_truncate(path).busy  # de WAL leeg vóór de meting
    async with get_engine(settings).connect() as conn:
        pages = (await conn.exec_driver_sql("PRAGMA page_count")).scalar()

    start = time.monotonic()
    await migrate(settings, command.upgrade, "head")
    took = time.monotonic() - start
    # Elk frame in de WAL is een kop van 24 bytes en een pagina; de WAL zelf begint met 32 bytes
    wal = path.with_name(path.name + "-wal").stat().st_size
    frames = (wal - 32) // (24 + 4096) if wal else 0
    print(f"\n0010 -> 0012 op 100.000 nodes: {took * 1000:.0f} ms, {frames} pagina's in de WAL; "
          f"de database heeft {pages} pagina's")  # fmt: skip
    assert pages > 5000
    # ±80 pagina's: vooral sqlite_master (de tekst van alle triggers) en de nieuwe tabellen en
    # indexen. Zou node herschreven worden, dan waren het er tienduizenden
    assert 0 < frames < 300, frames
    assert took < 2, took
    async with get_engine(settings).connect() as conn:
        assert (await conn.exec_driver_sql("SELECT count(*) FROM beeld_owner")).scalar() == 0
        found = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        assert found.scalars().all() == ["0012"]
