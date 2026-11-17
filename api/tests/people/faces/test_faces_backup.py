"""Reservekopieën zonder gezichten (fase 6, Keuze 52 en X2 "Reservekopieën"; X2b).

app/backup.py is van Z7 (één tekst voor app.cli db-backup en de snippet van install.sh); X2b
test hier alleen dat hij de tabellen van gezichten dekt, met echte rijen:

- backup.WIPE noemt elke tabel van gezichten (en blob_embed van X3);
- de kopie (via copy(), en via de tekst zoals install.sh hem draait: python -I -c) heeft 0 rijen
  in die tabellen en 0 merktekens (stukken van de verzegelde vectoren), geen -journal en geen
  -wal; personen en photo_person blijven (na het terugzetten rekent de container opnieuw);
- de database die draait, is niet veranderd.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import select

import app.backup as backup
from app import db as app_db
from app.models import Base, Face
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
FACE_TABLES = ("face_person", "face", "face_scan", "face_owner")


def test_wipe_dekt_de_tabellen_van_gezichten():
    tables = set(Base.metadata.tables)
    faces = {name for name in tables if name.startswith("face")}
    assert faces == set(FACE_TABLES)
    assert backup.WIPE == (*FACE_TABLES, "blob_embed")
    assert set(backup.WIPE) <= tables


async def _world(client, settings, nep) -> tuple[Path, list[bytes], dict[str, int]]:
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    for i in range(2):
        await foto(settings, nep, user.id, f"{i}.jpg", [(A, 1), (B, 2)])
    await aan(settings, user.id)
    await draai(settings)
    h = await login(client, "eva@example.nl")
    async with maker(settings)() as session:
        rows = list(await session.scalars(select(Face).order_by(Face.x)))
    r = await client.post(
        "/api/v1/people/faces/confirm",
        json={"faces": [str(rows[0].id)], "name": "Oma"},
        headers=h,
    )
    assert r.status_code == 204
    path = app_db.sqlite_path(settings.db_url)
    assert path is not None
    wanted = [f.vec[16:48] for f in rows]
    return path, wanted, counts(path)


def counts(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as con:
        return {
            name: con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            for name in (*FACE_TABLES, "person", "photo_person")
        }


def marks_in(path: Path, wanted: list[bytes]) -> int:
    total = 0
    for name in (path, path.with_name(path.name + "-wal")):
        if name.exists():
            data = name.read_bytes()
            total += sum(data.count(m) for m in wanted)
    return total


def check_copy(target: Path, wanted: list[bytes]) -> None:
    assert target.exists()
    assert sorted(p.name for p in target.parent.iterdir()) == [target.name]  # geen -journal/-wal
    assert counts(target) == {
        "face_person": 0,
        "face": 0,
        "face_scan": 0,
        "face_owner": 0,
        "person": 1,
        "photo_person": 1,
    }
    assert sum(target.read_bytes().count(m) for m in wanted) == 0


async def test_de_kopie_van_db_backup(client, settings, nep, tmp_path):
    path, wanted, before = await _world(client, settings, nep)
    assert before["face"] == 4 and before["face_person"] == 1 and before["face_owner"] == 1
    assert marks_in(path, wanted) >= len(wanted)
    target = backup.copy(path, tmp_path / "backup")
    check_copy(target, wanted)
    # De database die draait: niets veranderd
    assert counts(path) == before
    assert marks_in(path, wanted) >= len(wanted)


def run_snippet(path: Path, folder: Path) -> Path:
    text = Path(backup.__file__).read_text(encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-I", "-c", text, str(path), str(folder)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return Path(done.stdout.strip())


async def test_de_kopie_van_install_sh(client, settings, nep, tmp_path):
    """Zoals install.sh: de tekst van app/backup.py met python -I -c, zonder iets uit app."""
    path, wanted, before = await _world(client, settings, nep)
    target = run_snippet(path, tmp_path / "backup-sh")
    check_copy(target, wanted)
    assert counts(path) == before
