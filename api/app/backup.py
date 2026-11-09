"""Een reservekopie van platform.db, zonder gezichten en zonder beeld (bouwontwerp fase 6,
Keuze 52 en "Reservekopieën" bij X2).

Eén tekst voor twee plekken. `app.cli db-backup` importeert hem. install.sh draait hem vóór elke
update, terwijl de oude app nog draait, met het image dat er nu is; ook dat van 0.7.1, waar dit
bestand nog niet in zit (Z7, §3.10 stap 4):

    docker compose run --rm --no-deps -T platform python -I -c "<deze tekst>" \\
        /data/platform.db /data/backup

Daarom alleen de standaardbibliotheek, en niets uit app. De stappen:

1. `sqlite3.Connection.backup` naar `<naam>.part`: een kopie die klopt, ook met de app erbij;
2. op de kopie `journal_mode = OFF` (geen journaal met de oude pagina's) en `secure_delete = ON`;
3. de rijen van `face_person`, `face`, `face_scan`, `face_owner` en `blob_embed` weg, voor elke
   tabel die bestaat (X2, X3; K1-01, K1-08). Na het terugzetten rekent de container ze opnieuw;
4. commit, fsync, rename naar de echte naam, fsync van de map.

De naam is `platform-<alembic-head>-<tijd in UTC>.db`, met rechten 600. Per head blijft daarna
alleen de nieuwste. Zo gaat de laatste kopie van vóór een migratie nooit weg: die heeft een eigen
head. Mislukt een stap, dan is er geen kopie (ook geen .part) en is de exitcode 1; install.sh
stopt dan, vóór er iets veranderd is. De database zelf leest dit alleen.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

HEAD_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
# platform-<head>-<tijd>.db, met de tijd in UTC, tot milliseconden (een oudere kopie zonder)
NAME_RE = re.compile(
    r"^platform-(?P<head>[A-Za-z0-9_]{1,40})-(?P<tijd>\d{8}T\d{6}(?:\.\d{3})?Z)\.db$"
)
# Nooit in een kopie: de gezichten (X2) en de vectoren van beeld (X3)
WIPE = ("face_person", "face", "face_scan", "face_owner", "blob_embed")


class NoHead(Exception):
    """De database staat op geen enkele (leesbare) alembic-head."""


def stamp() -> str:
    """De tijd in de naam: UTC, tot milliseconden, zo dat namen op volgorde staan."""
    moment = datetime.now(UTC)
    return f"{moment:%Y%m%dT%H%M%S}.{moment.microsecond // 1000:03d}Z"


def read_head(conn: sqlite3.Connection) -> str | None:
    """De alembic-head in deze database (None: geen of meer dan één)."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
    ).fetchone():
        return None
    rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(rows) != 1 or not HEAD_RE.match(str(rows[0][0])):
        return None
    return str(rows[0][0])


def keep_newest(folder: Path) -> list[Path]:
    """Per head blijft de nieuwste kopie; de rest gaat weg (Keuze 52). De naam draagt de tijd,
    dus de nieuwste is de laatste op naam. Wat niet naar een kopie van ons lijkt, blijft staan."""
    found: dict[str, list[Path]] = {}
    for path in sorted(folder.glob("platform-*.db")):
        match = NAME_RE.match(path.name)
        if match is not None:
            found.setdefault(match["head"], []).append(path)
    newest = []
    for paths in found.values():
        newest.append(paths[-1])
        for old in paths[:-1]:
            old.unlink(missing_ok=True)
    return sorted(newest)


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _wipe(dst: sqlite3.Connection) -> None:
    """Stap 2 en 3 op de kopie, in één transactie."""
    dst.execute("PRAGMA journal_mode = OFF")
    dst.execute("PRAGMA secure_delete = ON")
    dst.execute("BEGIN")
    for name in WIPE:
        if dst.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone():
            dst.execute(f'DELETE FROM "{name}"')
    dst.execute("COMMIT")


def copy(source: Path, folder: Path) -> Path:
    """De kopie van source in folder (de map komt er met rechten 700); het pad van de kopie.
    NoHead als de database op geen revisie staat."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    with closing(sqlite3.connect(source)) as src:
        src.execute("PRAGMA busy_timeout = 5000")
        head = read_head(src)
        if head is None:
            raise NoHead(str(source))
        target = folder / f"platform-{head}-{stamp()}.db"
        for _ in range(2000):  # twee kopieën in dezelfde milliseconde: wacht op de volgende
            if not target.exists():
                break
            target = folder / f"platform-{head}-{stamp()}.db"
        part = target.with_name(target.name + ".part")
        extra = [part.with_name(part.name + end) for end in ("-journal", "-wal", "-shm")]
        for path in (part, *extra):
            path.unlink(missing_ok=True)
        try:
            # Meteen 600: de kopie heeft de hele database
            os.close(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            with closing(sqlite3.connect(part, isolation_level=None)) as dst:
                src.backup(dst)
                _wipe(dst)
            part.chmod(0o600)
            _fsync(part)
            os.replace(part, target)
            _fsync(folder)
        except BaseException:
            for path in (part, *extra):
                path.unlink(missing_ok=True)
            raise
    keep_newest(folder)
    return target


def main(argv: list[str]) -> int:
    """Voor install.sh: `<platform.db> <map>`. Print het pad van de kopie; exitcode 1 met een
    regel uitleg als het niet lukt."""
    if len(argv) != 2:
        print("Gebruik: <platform.db> <map voor de kopie>", file=sys.stderr)
        return 2
    source, folder = Path(argv[0]), Path(argv[1])
    if not source.is_file():
        print(f"Reservekopie mislukt: er is geen database ({source}).", file=sys.stderr)
        return 1
    try:
        target = copy(source, folder)
    except NoHead:
        print("Reservekopie mislukt: de database staat op geen enkele revisie.", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error) as exc:
        print(f"Reservekopie mislukt: {exc}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
