"""python -m app.cli sleutels-check [--opruimen] en backup-wipe-tables (0.8.2, P4).

sleutels-check kijkt of elke eigenaar die een sleutel nodig heeft (face_rows.key_owners:
gezichten aan, minstens één versleutelde vector) er een heeft zoals het hoort: een gewoon
bestand (geen symlink) van 32 bytes, rechten 0600, eigenaar KEY_UID (10001, de gebruiker van de
app in de container), in een sleutelmap die zelf geen symlink is, en die één versleuteld gezicht
van die eigenaar opent (crypto.key_opens: anders is het een andere sleutel). Exitcode 0 alleen
als dat voor iedereen klopt; anders 1. Op een cluster draait het op elke node: de sleutels
moeten op elke node gelijk zijn, en een node met een andere sleutel geeft zo exitcode 1.

--opruimen haalt de sleutels weg van eigenaren zonder rijen van gezichten (face, face_scan,
face_owner) die gezichten niet aan hebben, of die niet meer bestaan: een sleutel die op een
andere node al weg is (na "uit" daar). Per sleutel in één transactie: eerst de schakelaar van
die eigenaar vast (face_rows.lock_pref), dan kijken, dan unlink en fsync van de map, dan de
commit. Zo kan niemand intussen gezichten aanzetten. Een eigenaar zonder rij in photo_pref die
wel bestaat, blijft staan (zijn schakelaar kan niet vast).

backup-wipe-tables zegt welke tabellen een reservekopie leeg moet maken (backup.WIPE), één per
regel: op PostgreSQL maakt de beheerder de kopie zelf (pg_dump) en leegt deze tabellen erin.

Op het scherm alleen aantallen: nooit een sleutel, een byte daarvan, een pad of een eigenaar.
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import backup, face_rows
from app.config import Settings
from app.db import get_engine
from app.people.faces import crypto

COMMAND = "sleutels-check"
WIPE_COMMAND = "backup-wipe-tables"
KEY_UID = 10001  # de gebruiker van de app in de container (Dockerfile)
KEY_MODE = 0o600
NAME = re.compile(r"^([0-9a-f]{32})\.key$")

# De fouten per sleutel, in de volgorde van de regels op het scherm
PROBLEMS = {
    "missing": "sleutel ontbreekt",
    "not_file": "geen gewoon bestand (symlink of iets anders)",
    "size": f"niet {crypto.KEY_BYTES} bytes",
    "mode": "rechten niet 0600",
    "uid": f"eigenaar niet {KEY_UID}",
    "wrong": "sleutel opent de gezichten niet",
}


@dataclass
class Report:
    folder: str = "in orde"  # of wat er mis is met de sleutelmap
    folder_ok: bool = True
    needed: int = 0  # eigenaren die een sleutel nodig hebben
    problems: dict[str, int] = field(default_factory=lambda: dict.fromkeys(PROBLEMS, 0))
    spare: int = 0  # sleutels van eigenaren die er geen nodig hebben
    other: int = 0  # bestanden in de map die geen sleutel zijn
    removed: int = 0  # --opruimen

    @property
    def ok(self) -> bool:
        return self.folder_ok and not any(self.problems.values())

    def lines(self, cleanup: bool) -> list[str]:
        out = [f"sleutelmap: {self.folder}", f"eigenaren met een sleutel nodig: {self.needed}"]
        out += [f"  {text}: {self.problems[key]}" for key, text in PROBLEMS.items()]
        out.append(f"sleutels die niet nodig zijn: {self.spare}")
        if self.other:
            out.append(f"andere bestanden in de sleutelmap: {self.other}")
        if cleanup:
            out.append(f"weggehaald (--opruimen): {self.removed}")
        return out


def _folder(settings: Settings) -> tuple[Path | None, str]:
    """De sleutelmap, of None met wat er mis is."""
    try:
        folder = crypto.keys_dir(settings)
    except crypto.NoKeys:
        return None, "niet ingesteld (PLATFORM_FACES_KEYS)"
    try:
        found = os.lstat(folder)
    except FileNotFoundError:
        return None, "ontbreekt"
    if stat.S_ISLNK(found.st_mode):
        return None, "is een symlink"
    if not stat.S_ISDIR(found.st_mode):
        return None, "is geen map"
    return folder, "in orde"


def problem(path: Path) -> str | None:
    """Wat er mis is met dit sleutelbestand (een sleutel uit PROBLEMS), of None. lstat: een
    symlink telt als fout, ook als hij naar een goede sleutel wijst."""
    try:
        found = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISREG(found.st_mode):
        return "not_file"
    if found.st_size != crypto.KEY_BYTES:
        return "size"
    if stat.S_IMODE(found.st_mode) != KEY_MODE:
        return "mode"
    if found.st_uid != KEY_UID:
        return "uid"
    return None


def _key_files(folder: Path) -> tuple[list[uuid.UUID], int]:
    """De eigenaren met een sleutelbestand in de map, en hoeveel andere namen er staan."""
    owners, other = [], 0
    for entry in os.scandir(folder):
        match = NAME.match(entry.name)
        if match is None:
            other += 1
            continue
        owners.append(uuid.UUID(hex=match.group(1)))
    return sorted(owners), other


async def _spare(db: AsyncSession, owner: uuid.UUID) -> bool:
    """Mag de sleutel van owner weg? Geen rijen van gezichten, en gezichten niet aan (de
    schakelaar staat vast tot de commit van db) of de gebruiker bestaat niet meer."""
    faces = await face_rows.lock_pref(db, owner)
    if faces is None and not await face_rows.user_gone(db, owner):
        return False
    if faces == "aan":
        return False
    return not await face_rows.has_rows(db, owner)


async def check(settings: Settings, cleanup: bool = False) -> Report:
    report = Report()
    folder, report.folder = _folder(settings)
    report.folder_ok = folder is not None or report.folder.startswith("niet ingesteld")
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as db:
        needed = await face_rows.key_owners(db)
    report.needed = len(needed)
    if folder is None:
        if needed:
            report.folder_ok = False
            report.problems["missing"] = len(needed)
        return report
    async with maker() as db:
        for owner in needed:
            found = problem(crypto.key_path(settings, owner))
            if found is None:
                # Een goed bestand kan nog een andere sleutel zijn: open één gezicht ermee
                key = crypto.load_key(settings, owner)
                if key is None:
                    found = "missing"
                elif not await crypto.key_opens(db, owner, key):
                    found = "wrong"
            if found is not None:
                report.problems[found] += 1
    present, report.other = _key_files(folder)
    wanted = set(needed)
    for owner in present:
        if owner in wanted:
            continue
        report.spare += 1
        if not cleanup:
            continue
        async with maker() as db:
            if await _spare(db, owner):
                # Eerst de sleutel weg, dan de commit die de schakelaar weer vrijgeeft
                if crypto.delete_key(settings, owner):
                    report.removed += 1
            await db.commit()
    return report


def run(settings: Settings, cleanup: bool) -> int:
    async def go() -> Report:
        try:
            return await check(settings, cleanup)
        finally:
            await get_engine(settings).dispose()

    report = asyncio.run(go())
    for line in report.lines(cleanup):
        print(line)
    return 0 if report.ok else 1


def wipe_tables() -> int:
    for name in backup.WIPE:
        print(name)
    return 0
