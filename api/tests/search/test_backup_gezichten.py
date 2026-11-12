"""De reservekopie (app/backup.py) van een echte database op 0012, met rijen in alle tabellen van
0011 en 0012 (bouwontwerp fase 6, Keuze 52, "Reservekopieën" bij X2, K1-01 en K1-08; A0).

tests/test_backup.py doet het met namaaktabellen; hier met de tabellen, indexen en triggers van
de migraties, op beide manieren waarop de kopie gemaakt wordt (app.cli db-backup importeert
copy(), install.sh draait de tekst met python -I -c), terwijl de app een verbinding open heeft:

- in de kopie zijn face_person, face, face_scan, face_owner en blob_embed leeg, en staat geen
  byte van een vector of een tekst van gezichten of beeld meer (secure_delete);
- de rest blijft: personen, wie er op een foto staat, node_origin, beeld_owner en photo_pref, en
  het schema (met de triggers) is dat van de database zelf; de kopie klopt (integrity_check en
  foreign_key_check);
- de database zelf is niet veranderd: dezelfde rijen, de merktekens staan er nog.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command

from app import backup
from app.db import get_engine
from tests.search.gezichten import fill_beeld, fill_faces, marks
from tests.search.migratie import fill_0009, migrate, settings_at

pytestmark = pytest.mark.anyio

TEXT = Path(backup.__file__).read_text(encoding="utf-8")
WIPE = ("face_person", "face", "face_scan", "face_owner", "blob_embed")
KEEP = ("person", "photo_person", "node_origin", "beeld_owner", "photo_pref", "node", "user")


def _rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return sorted(conn.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)


def _all(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return {t: _rows(conn, t) for t in (*WIPE, *KEEP)}


def _schema(path: Path) -> list[tuple]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()


def _install(source: Path, folder: Path, cwd: Path) -> Path:
    """Zoals install.sh: de tekst met -I en -c, in een lege map, zonder iets van app."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", TEXT, str(source), str(folder)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return Path(result.stdout.strip())


@pytest.mark.parametrize("hoe", ["db-backup", "install.sh"])
async def test_de_kopie_van_0012_zonder_gezichten_en_beeld(tmp_path, hoe):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "head")
    await fill_faces(settings, ids)
    await fill_beeld(settings, ids)
    async with get_engine(settings).begin() as conn:
        # Een foto van Eva op de foto met Oma (photo_person, 0010), naast haar bevestigde gezicht
        await conn.exec_driver_sql(
            "INSERT INTO photo_person (node_id, person_id, owner_id, created_at) "
            "SELECT f.node_id, fp.person_id, fp.owner_id, fp.created_at "
            "FROM face_person fp JOIN face f ON f.id = fp.face_id"
        )
    source = settings.data_dir / "platform.db"
    before = _all(source)
    assert all(before[t] for t in (*WIPE, *KEEP)), {t: len(v) for t, v in before.items()}
    merken = marks(settings)
    assert sum(merken.values()) > 0

    folder = settings.data_dir / "backup"
    # De app draait: een verbinding uit de pool staat open, met een lezer erop
    async with get_engine(settings).connect() as app_conn:
        await app_conn.exec_driver_sql("SELECT count(*) FROM face")
        if hoe == "db-backup":
            target = backup.copy(source, folder)
        else:
            leeg = tmp_path / "leeg"
            leeg.mkdir()
            target = _install(source, folder, leeg)

    assert target.parent == folder and target.name.startswith("platform-0012-")
    assert sorted(p.name for p in folder.iterdir()) == [target.name]  # geen .part of journaal
    data = target.read_bytes()
    assert b"MERKTEKEN" not in data  # geen vector, geen tekst, ook niet in vrije pagina's
    after = _all(target)
    assert {t: after[t] for t in WIPE} == {t: [] for t in WIPE}
    assert {t: after[t] for t in KEEP} == {t: before[t] for t in KEEP}
    assert _schema(target) == _schema(source)  # met de triggers van 0010, 0011 en 0012
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as kopie:
        assert kopie.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert kopie.execute("PRAGMA foreign_key_check").fetchall() == []
        assert kopie.execute("SELECT version_num FROM alembic_version").fetchall() == [("0012",)]

    # De database zelf: precies wat er was, met de merktekens
    assert _all(source) == before
    assert sum(marks(settings).values()) > 0
