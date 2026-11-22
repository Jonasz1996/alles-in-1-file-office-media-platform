"""Het wissen van gezichten: de job faces.wipe (rij default; fase 6, Keuze 64 en 68; X2b).

Scopes:
- all: "uit", een verbod, een gebruiker die gewist wordt. Eerst de sleutel weg (unlink, fsync
  van de map): vanaf dan is elke kopie van zijn vectoren onleesbaar. Dan face_owner.wiping = 1
  (faces.plan plant dan niets), dan in stukken van 2.000 met een commit face_person, face,
  face_scan, en tot slot face_owner.
- unnamed: de knop "Niet benoemde gezichten wissen": open gezichten worden verborgen, zonder
  vector, zodat ze niet terugkomen.
- checkpoint: na "niet tonen", "ongedaan maken" en het wissen van een persoon (K1-15).

Daarna PRAGMA wal_checkpoint(TRUNCATE) op een eigen verbinding met busy_timeout 0, opnieuw met
backoff tot busy = 0, hooguit CHECKPOINT_BUDGET. Lukt het niet, dan Defer(60) en /beheer zegt
"wissen wacht op een lezer". secure_delete staat op elke verbinding aan (app/db.py).

Een wisronde die halverwege stopt, gaat bij de start verder (resume) en in de ronde van elk uur.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import db as app_db
from app import dbdialect, face_rows, jobs
from app.config import Settings
from app.db import get_engine
from app.models import FaceOwner
from app.people import people as P
from app.people.faces import crypto, state

WIPE = "faces.wipe"
SCOPES = ("all", "unnamed", "checkpoint")
CHECKPOINT_BUDGET = 300.0  # 5 minuten
WAIT_S = 60.0
WAITING: set[str] = set()  # eigenaren (hex) waarvan het checkpoint op een lezer wacht


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def queue_wipe(db: AsyncSession, owner: uuid.UUID, scope: str) -> None:
    """Zet faces.wipe klaar in de transactie van de aanroeper. Bij all en unnamed meteen
    wiping = 1: dan plant faces.plan niets meer en toont de status "wissen"."""
    if scope in ("all", "unnamed"):
        row = await state.owner_row(db, owner)
        row.wiping = 1
    await jobs.enqueue(
        db, WIPE, {"owner": owner.hex, "scope": scope}, key=f"{WIPE}:{scope}:{owner.hex}"
    )


async def _checkpoint(settings: Settings, owner: uuid.UUID) -> None:
    path = app_db.sqlite_path(settings.db_url)
    if path is None:
        return
    found = await app_db.wal_truncate_retry(path, CHECKPOINT_BUDGET)
    if found.busy:
        WAITING.add(owner.hex)
        raise jobs.Defer(WAIT_S)
    WAITING.discard(owner.hex)


async def wipe_job(settings: Settings, payload: dict[str, Any]) -> None:
    owner = uuid.UUID(payload["owner"])
    scope = payload.get("scope", "all")
    if scope not in SCOPES:
        return
    if scope == "all":
        crypto.delete_key(settings, owner)
    if scope in ("all", "unnamed"):
        while True:
            async with P.lock(owner), _maker(settings)() as db:
                if dbdialect.name_of(db) == dbdialect.SQLITE:
                    # Alleen SQLite kent secure_delete; PostgreSQL ruimt oude rijen op met
                    # (auto)vacuum, en _checkpoint() doet daar niets
                    await db.execute(text("PRAGMA secure_delete = ON"))
                if scope == "all":
                    n = await face_rows.wipe_owner_chunk(db, owner)
                    if not n:
                        await face_rows.drop_owner_row(db, owner)
                else:
                    n = await face_rows.hide_unnamed_chunk(db, owner)
                    if not n:
                        await db.execute(
                            update(FaceOwner).where(FaceOwner.owner_id == owner).values(wiping=0)
                        )
                await db.commit()
            if not n:
                break
        state.changed(owner)
    await _checkpoint(settings, owner)


async def resume(settings: Settings) -> int:
    """Bij de start en elk uur: elke eigenaar met wiping = 1 krijgt zijn wisronde opnieuw
    (scope all als hij gezichten uit heeft of het verbod staat, anders unnamed); wie uit staat
    en nog rijen heeft, ook."""
    async with _maker(settings)() as db:
        wiping = list(await db.scalars(select(FaceOwner.owner_id).where(FaceOwner.wiping == 1)))
        allowed = await state.allowed(db)
        on = set(await state.owners_on(db)) if allowed else set()
        leftovers = [o for o in await face_rows.owners_with_rows(db) if o not in on]
        todo = sorted(set(wiping) | set(leftovers))
        for owner in todo:
            scope = "all" if owner not in on else "unnamed"
            await jobs.enqueue(
                db, WIPE, {"owner": owner.hex, "scope": scope}, key=f"{WIPE}:{scope}:{owner.hex}"
            )
        await db.commit()
    return len(todo)
