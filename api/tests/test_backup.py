"""De reservekopie van app/backup.py (bouwontwerp fase 6, Keuze 52 en "Reservekopieën" bij X2).

Eén tekst voor twee plekken: app.cli db-backup importeert hem (test_cli_db.py), en install.sh
draait hem met `python -I -c "<tekst>" /data/platform.db /data/backup` in het image dat er nog
draait (Z7). Deze tests draaien hem op beide manieren: de kopie heeft geen gezichten en geen
beeld (ook de bytes niet), geen journaal ernaast, rechten 600; per head blijft de nieuwste en de
kopie van vóór een migratie blijft; een mislukte kopie laat niets achter.
"""

from __future__ import annotations

import ast
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app import backup

TEXT = Path(backup.__file__).read_text(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
MERK = b"MERKTEKEN-GEZICHT-" * 200  # groter dan een pagina: ook de overloop-pagina's
FACES = ("face_person", "face", "face_scan", "face_owner", "blob_embed")


def _db(path: Path, head: str = "0010", *, faces: bool = True, wal: bool = True) -> None:
    """Een kleine database zoals die van de app: een alembic-head, gewone rijen, en (als faces)
    de tabellen van X2 en X3 met merktekens."""
    with sqlite3.connect(path) as conn:
        if wal:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (head,))
        conn.execute('CREATE TABLE "user" (id INTEGER PRIMARY KEY, email TEXT)')
        conn.executemany('INSERT INTO "user" (email) VALUES (?)', [("eva@example.nl",), ("bram",)])
        if faces:
            for name in FACES:
                conn.execute(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY, data BLOB)')
                conn.executemany(f'INSERT INTO "{name}" (data) VALUES (?)', [(MERK,)] * 5)


def _run(*args: str | Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Zoals install.sh: de tekst met -c, -I, en niets van app op het pad."""
    return subprocess.run(
        [sys.executable, "-I", "-c", TEXT, *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _leftovers(folder: Path) -> list[str]:
    return sorted(
        p.name for p in folder.iterdir() if p.name.endswith((".part", "-journal", "-wal", "-shm"))
    )


def test_alleen_de_standaardbibliotheek():
    """install.sh draait de tekst in het image van 0.7.1: geen import uit app of een pakket."""
    tree = ast.parse(TEXT)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names == {
        "__future__",
        "contextlib",
        "datetime",
        "os",
        "pathlib",
        "re",
        "signal",
        "sqlite3",
        "sys",
    }
    assert names <= set(sys.stdlib_module_names) | {"__future__"}


def test_de_kopie_zonder_gezichten_en_beeld(tmp_path):
    source, folder = tmp_path / "platform.db", tmp_path / "backup"
    _db(source)
    voor = sqlite3.connect(source)  # de app draait: een open verbinding
    try:
        target = backup.copy(source, folder)
    finally:
        voor.close()

    assert backup.NAME_RE.match(target.name) and target.name.startswith("platform-0010-")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert _leftovers(folder) == []
    data = target.read_bytes()
    assert b"MERKTEKEN" not in data  # secure_delete: ook de vrije pagina's zijn leeg
    assert data[18:20] == b"\x01\x01"  # geen WAL meer: er komt nooit een -wal naast
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as kopie:
        for name in FACES:
            assert kopie.execute(f'SELECT count(*) FROM "{name}"').fetchone() == (0,)
        assert kopie.execute('SELECT count(*) FROM "user"').fetchone() == (2,)
        assert kopie.execute("SELECT version_num FROM alembic_version").fetchall() == [("0010",)]
        assert kopie.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    # De database zelf is niet veranderd: de gezichten staan er nog
    with sqlite3.connect(source) as conn:
        for name in FACES:
            assert conn.execute(f'SELECT count(*) FROM "{name}"').fetchone() == (5,)


def test_zonder_de_tabellen_van_gezichten(tmp_path):
    """0009 en 0010 hebben die tabellen nog niet: dan gewoon een kopie."""
    source = tmp_path / "platform.db"
    _db(source, "0009", faces=False, wal=False)
    target = backup.copy(source, tmp_path / "backup")
    assert target.name.startswith("platform-0009-")
    with sqlite3.connect(target) as kopie:
        assert kopie.execute('SELECT count(*) FROM "user"').fetchone() == (2,)


def test_zoals_install_sh_hem_draait(tmp_path):
    """python -I -c, in een lege map: print het pad, exit 0. Na twee runs op 0010 staat de kopie
    van 0009 (de laatste van vóór de migratie) er nog, en van 0010 alleen de nieuwste."""
    source, folder = tmp_path / "platform.db", tmp_path / "backup"
    folder.mkdir()
    oud = folder / "platform-0009-20261101T120000Z.db"  # zonder milliseconden: ook van ons
    oud.write_bytes(b"")
    vreemd = folder / "platform-met-de-hand.db"
    vreemd.write_bytes(b"")
    _db(source)
    leeg = tmp_path / "leeg"
    leeg.mkdir()

    eerste = _run(source, folder, cwd=leeg)
    assert eerste.returncode == 0, eerste.stderr
    tweede = _run(source, folder, cwd=leeg)
    assert tweede.returncode == 0, tweede.stderr
    pad1, pad2 = Path(eerste.stdout.strip()), Path(tweede.stdout.strip())
    assert pad1.parent == folder and pad2.parent == folder and pad1.name < pad2.name
    assert sorted(p.name for p in folder.iterdir()) == sorted([oud.name, vreemd.name, pad2.name])
    assert b"MERKTEKEN" not in pad2.read_bytes()
    assert stat.S_IMODE(pad2.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("maak", "uitleg"),
    [
        ("geen", "er is geen database"),
        ("zonder_head", "de database staat op geen enkele revisie"),
        ("twee_heads", "de database staat op geen enkele revisie"),
        ("geen_database", "Reservekopie mislukt: "),
    ],
)
def test_mislukt_laat_niets_achter(tmp_path, maak, uitleg):
    """Exit 1 met één regel uitleg, en geen (halve) kopie: install.sh stopt dan."""
    source, folder = tmp_path / "platform.db", tmp_path / "backup"
    if maak == "zonder_head":
        with sqlite3.connect(source) as conn:
            conn.execute("CREATE TABLE x (id INTEGER)")
    elif maak == "twee_heads":
        _db(source)
        with sqlite3.connect(source) as conn:
            conn.execute("INSERT INTO alembic_version VALUES ('0011')")
    elif maak == "geen_database":
        source.write_bytes(b"dit is geen sqlite" * 100)
    result = _run(source, folder, cwd=tmp_path)
    assert result.returncode == 1
    assert uitleg in result.stderr and result.stdout == ""
    assert not folder.exists() or list(folder.iterdir()) == []


def test_een_fout_halverwege_laat_geen_part_achter(tmp_path):
    """Een trigger op een tabel van gezichten die faalt (stap 3): geen kopie, geen .part."""
    source, folder = tmp_path / "platform.db", tmp_path / "backup"
    _db(source)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TRIGGER face_ad AFTER DELETE ON face BEGIN SELECT RAISE(ABORT, 'nee'); END"
        )
    result = _run(source, folder, cwd=tmp_path)
    assert result.returncode == 1 and "Reservekopie mislukt" in result.stderr
    assert list(folder.iterdir()) == []


def test_een_map_die_er_niet_kan_komen(tmp_path):
    """Waar de map moet komen, staat een bestand: exit 1, en het bestand blijft zoals het was."""
    source, folder = tmp_path / "platform.db", tmp_path / "backup"
    _db(source)
    folder.write_bytes(b"van iemand anders")
    result = _run(source, folder, cwd=tmp_path)
    assert result.returncode == 1 and "Reservekopie mislukt" in result.stderr
    assert folder.read_bytes() == b"van iemand anders"
    assert not list(tmp_path.glob("platform-*"))


def test_install_sh_draait_deze_tekst():
    """install.sh leest precies dit bestand uit de broncode en geeft het aan python -I -c."""
    sh = (REPO / "infra" / "single" / "install.sh").read_text(encoding="utf-8")
    assert 'text=$(cat "$SRC/api/app/backup.py")' in sh
    assert (
        '"${compose[@]}" run --rm --no-deps -T platform python -I -c "$text" '
        "/data/platform.db /data/backup </dev/null"
    ) in sh


def test_een_oude_part_gaat_weg(tmp_path):
    """Review golf 4 (veilig F3): een .part van een kopie die ooit afgebroken werd (SIGKILL, een
    timeout van compose), met zijn journaal, gaat bij de volgende kopie weg; ook als de tijd in de
    naam anders is. Wat niet van ons is, blijft."""
    source, folder = tmp_path / "platform.db", tmp_path / "backup"
    folder.mkdir()
    _db(source)
    oud = "platform-0010-20261101T120000.123Z.db.part"
    namen = [
        oud,
        oud + "-journal",
        oud + "-wal",
        oud + "-shm",
        "platform-0009-20261101T120000Z.db.part",
    ]
    for naam in namen:
        (folder / naam).write_bytes(MERK)
    vreemd = folder / "platform-met-de-hand.db.part"
    vreemd.write_bytes(b"")
    result = _run(source, folder, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    pad = Path(result.stdout.strip())
    assert sorted(p.name for p in folder.iterdir()) == sorted([pad.name, vreemd.name])


def test_keep_newest_ruimt_ook_een_part_op(tmp_path):
    """app.cli db-backup en install.sh ruimen via dezelfde functie op."""
    folder = tmp_path / "backup"
    folder.mkdir()
    for naam in (
        "platform-0010-20261101T120000.123Z.db",
        "platform-0010-20261102T120000.123Z.db",
        "platform-0010-20261103T120000.123Z.db.part",
        "platform-0010-20261103T120000.123Z.db.part-journal",
    ):
        (folder / naam).write_bytes(b"")
    assert [p.name for p in backup.keep_newest(folder)] == ["platform-0010-20261102T120000.123Z.db"]
    assert sorted(p.name for p in folder.iterdir()) == ["platform-0010-20261102T120000.123Z.db"]


# Zoals install.sh, maar de kopie krijgt SIGTERM terwijl hij loopt (docker compose run die stopt):
# _wipe stuurt het signaal aan het eigen proces en wacht even.
DRIVER = """
import os, signal, sys, time
ns = {"__name__": "backup_tekst"}
exec(compile(sys.argv[1], "backup.py", "exec"), ns)
def wipe(dst):
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(5)
    raise AssertionError("SIGTERM kwam niet aan")
ns["_wipe"] = wipe
sys.exit(ns["main"](sys.argv[2:]))
"""


def test_sigterm_laat_niets_achter(tmp_path):
    """Review golf 4 (veilig F3): SIGTERM midden in de kopie (de standaard van Python stopt het
    proces zonder de opruiming) laat geen .part en geen journaal achter, en geen kopie."""
    source, folder = tmp_path / "platform.db", tmp_path / "backup"
    _db(source)
    result = subprocess.run(
        [sys.executable, "-I", "-c", DRIVER, TEXT, str(source), str(folder)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 128 + 15, (result.returncode, result.stderr)
    assert "SIGTERM kwam niet aan" not in result.stderr
    assert list(folder.iterdir()) == []
