"""sleutels-check (0.8.2, P4): welke eigenaren hebben versleutelde gezichten zonder sleutel?

In het cluster staan de sleutels per node (/var/lib/cf-platform/sleutels/gezichten, elke minuut
gesynchroniseerd). Ontbreekt de sleutel van een eigenaar die nog gezichten heeft, dan zoekt
faces.scan voor hem niet verder (scan.keys_lost): een nieuwe sleutel zou al zijn gezichten in één
klap onleesbaar maken. Dit commando zegt wie dat zijn; met --wissen wist het hun gezichten bewust
(de wisronde faces.wipe, scope all), waarna de volgende scan met een nieuwe sleutel begint.

Alleen aantallen en id's, nooit een sleutel of een vector.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app import face_rows, jobs
from app.config import Settings
from app.db import get_engine
from app.people.faces import crypto
from app.people.faces.wipe import WIPE


@dataclass(frozen=True)
class KeyCheck:
    keys_dir: str | None  # None: PLATFORM_FACES_KEYS ontbreekt
    owners: int  # eigenaren met versleutelde gezichten
    lost: dict[uuid.UUID, int]  # zonder sleutel: het aantal versleutelde gezichten
    queued: int = 0  # wisrondes die --wissen in de rij zette


async def check(settings: Settings, wipe: bool = False) -> KeyCheck:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as db:
        rows = list((await face_rows.sealed_counts(db)).items())
        try:
            folder = str(crypto.keys_dir(settings))
        except crypto.NoKeys:
            return KeyCheck(None, len(rows), {})
        lost = {o: n for o, n in rows if crypto.load_key(settings, o) is None}
        queued = 0
        if wipe:
            for owner in sorted(lost):
                await jobs.enqueue(
                    db, WIPE, {"owner": owner.hex, "scope": "all"}, key=f"{WIPE}:all:{owner.hex}"
                )
                queued += 1
            await db.commit()
    return KeyCheck(folder, len(rows), lost, queued)


def report(found: KeyCheck) -> tuple[int, list[str]]:
    """De regels voor de commandoregel en de exitcode: 0 alles goed, 1 sleutels kwijt (of de
    map ontbreekt terwijl er gezichten zijn), 0 na --wissen."""
    if found.keys_dir is None:
        if not found.owners:
            return 0, ["Geen PLATFORM_FACES_KEYS, en geen versleutelde gezichten."]
        return 1, [
            f"PLATFORM_FACES_KEYS ontbreekt, maar {found.owners} eigenaar(s) hebben "
            "versleutelde gezichten. Zet de map met de sleutels terug."
        ]
    lines = [f"Sleutels in {found.keys_dir}: {found.owners} eigenaar(s) met gezichten."]
    if not found.lost:
        lines.append("Elke eigenaar met gezichten heeft zijn sleutel: goed.")
        return 0, lines
    lines.append(f"Zonder sleutel: {len(found.lost)} eigenaar(s).")
    for owner, n in sorted(found.lost.items()):
        lines.append(f"  {owner}: {n} versleutelde gezichten")
    if found.queued:
        lines.append(
            f"Wisronde in de rij voor {found.queued} eigenaar(s); daarna zoekt de app "
            "hun gezichten opnieuw, met een nieuwe sleutel."
        )
        return 0, lines
    lines.append(
        "In het cluster: wacht een minuut (de sync van de sleutels) en kijk opnieuw. Is de "
        "sleutel echt weg, dan wist `sleutels-check --wissen` hun gezichten; de app zoekt ze "
        "daarna opnieuw."
    )
    return 1, lines
