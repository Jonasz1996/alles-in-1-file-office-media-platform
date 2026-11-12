"""Wachter (§4.1, §3.1a): de triggers, de view, de FTS-tabellen en de indexen van 0011 en 0012
in de database zijn precies wat app/search/schema.py zegt, en de bevroren kopie in migratie 0010,
0011 en 0012 is elk gelijk aan zijn eigen deel. Wie een trigger verandert, verandert dus beide en
schrijft een nieuwe migratie."""

import importlib.util
import uuid
from pathlib import Path

import pytest

from app.search import schema
from tests.search.migratie import check_fts

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
MIGRATION = VERSIONS / "0010_zoeken.py"


def migration(path: Path):
    spec = importlib.util.spec_from_file_location(f"migratie_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migration_0010():
    return migration(MIGRATION)


def migration_0011():
    return migration(VERSIONS / "0011_gezichten.py")


def migration_0012():
    return migration(VERSIONS / "0012_beeld.py")


async def master(settings, kind: str) -> dict[str, str]:
    from app.db import get_engine

    async with get_engine(settings).connect() as conn:
        rows = await conn.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = ?", (kind,)
        )
        return {r[0]: r[1] for r in rows.all()}


@pytest.mark.anyio
async def test_triggers_in_de_database_zijn_schema_triggers(settings):
    assert await master(settings, "trigger") == schema.TRIGGERS


@pytest.mark.anyio
async def test_view_en_fts_tabellen_in_de_database(settings):
    assert await master(settings, "view") == {"search_src": schema.VIEW_SQL}
    tables = await master(settings, "table")
    for name, sql in schema.FTS_TABLES.items():
        assert tables[name] == sql
    for name, sql in schema.SEQ_TABLES.items():
        assert tables[name] == sql
    indexes = await master(settings, "index")
    for name, sql in schema.NODE_INDEXES.items():
        assert indexes[name] == sql
    # De virtuele kolom staat in de CREATE van node, zoals ALTER TABLE hem toevoegde
    assert f"GENERATED ALWAYS AS ({schema.FTYPE_SQL}) VIRTUAL" in tables["node"]


def test_de_migratie_is_een_kopie_van_schema():
    m = migration_0010()
    assert m.TRIGGERS == schema.TRIGGERS_0010
    assert m.TRIGGERS_NO_FTS == schema.TRIGGERS_NO_FTS_0010
    for name in (
        "FTYPE_SQL",
        "NODE_COLUMNS",
        "NODE_INDEXES",
        "ALBUM_ITEM_COLUMNS",
        "SEQ_TABLES",
        "VIEW_SQL",
        "FTS_TABLES",
        "FILL",
        "FTS_FILL",
        "NEW_TABLES",
    ):
        assert getattr(m, name) == getattr(schema, name), name


def test_de_kopie_in_0011_en_0012_is_gelijk_aan_schema():
    """§3.1a: 0011 en 0012 hebben elk een bevroren kopie van hun triggers, indexen en tabellen.
    Geen van hun triggers raakt FTS: met en zonder FTS5 zijn ze gelijk."""
    for m, triggers, no_fts, indexes, tables in (
        (migration_0011(), schema.TRIGGERS_0011, schema.TRIGGERS_NO_FTS_0011,
         schema.INDEXES_0011, schema.TABLES_0011),
        (migration_0012(), schema.TRIGGERS_0012, schema.TRIGGERS_NO_FTS_0012,
         schema.INDEXES_0012, schema.TABLES_0012),
    ):  # fmt: skip
        assert m.TRIGGERS == triggers == no_fts
        assert m.INDEXES == indexes
        assert m.TABLES == tables
        for sql in triggers.values():
            assert "fts_" not in sql and "search_src" not in sql
    assert list(schema.TRIGGERS_0011) == ["node_au_owner_face", "node_au_blob_face",
                                          "person_bd_face"]  # fmt: skip
    assert list(schema.TRIGGERS_0012) == ["node_ai_beeld", "node_au_beeld", "node_ad_beeld"]


def test_de_plek_voor_de_triggers_van_0011_en_0012():
    """Review Z0b, contract F5: de triggers van 0011 en 0012 (A0) staan in dezelfde TRIGGERS als
    die van 0010, en de kopie in 0010 blijft gelijk aan alleen het deel van 0010."""
    assert schema.TRIGGERS == {
        **schema.TRIGGERS_0010,
        **schema.TRIGGERS_0011,
        **schema.TRIGGERS_0012,
    }
    assert schema.TRIGGERS_NO_FTS == {
        **schema.TRIGGERS_NO_FTS_0010,
        **schema.TRIGGERS_NO_FTS_0011,
        **schema.TRIGGERS_NO_FTS_0012,
    }
    # Geen naam dubbel: geen trigger van 0011 of 0012 vervangt er een van 0010
    assert len(schema.TRIGGERS) == len(schema.TRIGGERS_0010) + 3 + 3
    assert len(schema.TRIGGERS_NO_FTS) == len(schema.TRIGGERS_NO_FTS_0010) + 3 + 3
    for name in (*schema.TRIGGERS_0011, *schema.TRIGGERS_0012):
        assert name not in schema.TRIGGERS_0010
        assert name not in schema.TRIGGERS_NO_FTS_0010
    m = migration_0010()
    assert m.TRIGGERS == schema.TRIGGERS_0010
    assert m.TRIGGERS_NO_FTS == schema.TRIGGERS_NO_FTS_0010


@pytest.mark.anyio
async def test_indexen_van_0011_en_0012_in_de_database(settings):
    """De SQL zoals sqlite_master hem bewaart: gedeeltelijk en met DESC (dat ziet
    compare_metadata niet)."""
    indexes = await master(settings, "index")
    for name, sql in {**schema.INDEXES_0011, **schema.INDEXES_0012}.items():
        assert indexes[name] == sql, name


def test_zonder_fts_alleen_nummer_pad_dead_en_foto_rijen():
    """De vorm zonder FTS5 raakt geen FTS-tabel en geen view; de triggers die de foto-rijen
    opruimen en de paden bijhouden, zijn er wel."""
    assert set(schema.TRIGGERS_NO_FTS) == {
        "node_ai",
        "node_bu_text",
        "node_au_owner",
        "node_au_move",
        "node_au_trash",
        # de stand van de node in photo_geo (review Z0, haalbaar F2)
        "node_au_taken",
        "geo_bi",
        "geo_ai",
        "geo_bu",
        # gezichten (0011) en zoeken op beeld (0012): met en zonder FTS5 gelijk
        "node_au_owner_face",
        "node_au_blob_face",
        "person_bd_face",
        "node_ai_beeld",
        "node_au_beeld",
        "node_ad_beeld",
    }
    for sql in schema.TRIGGERS_NO_FTS.values():
        assert "fts_" not in sql and "search_src" not in sql
    assert set(schema.TRIGGERS_NO_FTS) < set(schema.TRIGGERS)
    for name in ("node_au_owner", "node_au_move", "node_au_trash", "node_au_taken", "geo_bi",
                 "geo_ai", "geo_bu", "node_au_owner_face", "node_au_blob_face", "person_bd_face",
                 "node_ai_beeld", "node_au_beeld", "node_ad_beeld"):  # fmt: skip
        assert schema.TRIGGERS_NO_FTS[name] == schema.TRIGGERS[name]


def test_elke_trigger_op_een_bekende_tabel():
    """bt_* op blob_text, po_* op photo_ocr, mi_* op media_item, geo_* op photo_geo, node_* op
    node, person_* op person (person_bd_face, 0011); en geen trigger schrijft met INSERT OR
    REPLACE (dan klopt de 'delete' in FTS niet meer) of met een upsert (Keuze 76)."""
    tables = {
        "node": "node",
        "bt": "blob_text",
        "po": "photo_ocr",
        "mi": "media_item",
        "geo": "photo_geo",
        "person": "person",
    }
    for name, sql in schema.TRIGGERS.items():
        assert f" ON {tables[name.split('_')[0]]}" in sql.split("BEGIN")[0], name
        assert "OR REPLACE" not in sql and "OR IGNORE" not in sql, name
        assert "ON CONFLICT" not in sql, name


@pytest.mark.anyio
async def test_ocr_van_een_foto_staat_alleen_in_pocr(settings):
    """Review Z0, veilig F3: OCR van een foto is alleen van de eigenaar (Keuze 16, 23, 25a).
    In fts_node heeft hij een eigen kolom pocr, los van de OCR van een PDF in ocr. Een vraag van
    een ander sluit pocr uit met een kolomfilter: dan geen treffer, geen rang en geen snippet
    uit de foto. De OCR van een PDF (wie hem mag lezen, mag de tekst zien) blijft vindbaar."""
    from app.db import get_engine

    user, root, foto, scan = (uuid.uuid4().hex for _ in range(4))
    sha_foto, sha_scan = "a" * 64, "b" * 64
    t = "2026-11-03 09:00:00.000000"
    async with get_engine(settings).begin() as conn:
        run = conn.exec_driver_sql
        assert [r[1] for r in (await run("PRAGMA table_info(fts_node)")).all()] == [
            "name", "extra", "body", "ocr", "meta", "pocr",
        ]  # fmt: skip
        await run(
            'INSERT INTO "user" (id, email, display_name, password_hash, role, quota_bytes, '
            "root_id, created_at) VALUES (?, 'eva@example.nl', 'Eva', 'x', 'user', 0, ?, ?)",
            (user, root, t),
        )
        for sha in (sha_foto, sha_scan):
            await run(
                "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES (?, 1, 1, ?)",
                (sha, t),
            )
        for node_id, parent, name, blob in (
            (root, None, "", None),
            (foto, root, "bon.jpg", sha_foto),
            (scan, root, "scan.pdf", sha_scan),
        ):
            await run(
                "INSERT INTO node (id, parent_id, owner_id, name, kind, size, blob_id, "
                "created_at, modified_at, search_name) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (node_id, parent, user, name, "dir" if blob is None else "file", blob, t, t,
                 name),
            )  # fmt: skip
        await run(
            "INSERT INTO blob_text (blob_sha, ocr, updated_at) VALUES (?, 'scanwoord', ?)",
            (sha_scan, t),
        )
        await run(
            "INSERT INTO photo_ocr (node_id, owner_id, text, words, angle, updated_at) "
            "VALUES (?, ?, 'pincode fotowoord 4711', x'00', 0, ?)",
            (foto, user, t),
        )

        async def hits(expr: str) -> set[str]:
            rows = await run(
                "SELECT n.name FROM fts_node f JOIN node n ON n.num = f.rowid "
                "WHERE fts_node MATCH ?",
                (expr,),
            )
            return {r[0] for r in rows.all()}

        assert await hits("fotowoord") == {"bon.jpg"}  # de eigenaar
        assert await hits("pocr:fotowoord") == {"bon.jpg"}
        assert await hits("ocr:fotowoord") == set()
        # zoals een vraag van een ander: alle kolommen behalve pocr
        anders = "{name extra body ocr meta}: "
        assert await hits(anders + "fotowoord") == set()
        assert await hits(anders + "pincode") == set()
        assert await hits(anders + "scanwoord") == {"scan.pdf"}
        assert await hits("ocr:scanwoord") == {"scan.pdf"}
        assert await hits("pocr:scanwoord") == set()
        await conn.run_sync(check_fts)
