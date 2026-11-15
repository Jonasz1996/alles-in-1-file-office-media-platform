"""Groeperen en voorstellen, de kant van de app (fase 6, Keuze 62; X2b).

De app leest per eigenaar de bevestigde gezichten (ja, persoon niet verborgen) en de open
gezichten (niet bevestigd, niet verborgen, met vector; hooguit PLATFORM_FACES_GROUP_MAX, de
nieuwste eerst), ontsleutelt de vectoren (Keuze 68; een rij die niet open gaat, telt als gewist),
en schrijft ze in een verzegelde memfd (PFG1, wire.GROUP_HEAD). De afwijzingen (nee) gaan mee.
De worker ziet indexen, geen ids of namen. De uitkomst (n × wire.GROUP_OUT) moet precies zo groot
zijn; anders is het bad_output en schrijft de app niets.

Schrijven in stukken van 2.000 per transactie (face_rows). Bij full gaat face_owner.run één
omhoog; een groep heet naar buiten "<run>:<cluster>".

De job faces.group (rij analyse, ontdubbeld per eigenaar en mode): suggest wacht tot 30 s na de
laatste keuze; full hooguit één keer per uur terwijl er nieuwe gezichten komen en de rij van de
eigenaar leeg is, en na een nieuwe versie.
"""

from __future__ import annotations

import logging
import struct
import uuid
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import face_rows, jobs
from app.analyse import client, monitor, wire
from app.config import Settings
from app.db import get_engine
from app.models import Face, FaceOwner, FacePerson, Node, Person
from app.people import people as P
from app.people.faces import crypto, drempels, state
from app.search.live import own_live

log = logging.getLogger("platform.faces")

GROUP = "faces.group"
VERDICT_WAIT = timedelta(seconds=30)
FULL_EVERY = timedelta(hours=1)
CHUNK = 2000


class BadOutput(Exception):
    """De uitvoer van group klopt niet (grootte, of een index buiten bereik)."""


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def queue_group(db: AsyncSession, owner: uuid.UUID, mode: str, *, wait: bool = False) -> None:
    run_after = state.now() + VERDICT_WAIT if wait else None
    await jobs.enqueue(
        db,
        GROUP,
        {"owner": owner.hex, "mode": mode},
        key=f"{GROUP}:{mode}:{owner.hex}",
        run_after=run_after,
    )


async def _read(
    settings: Settings, owner: uuid.UUID, only: list[uuid.UUID] | None
) -> tuple[
    list[tuple[uuid.UUID, bytes, int]],
    list[tuple[uuid.UUID, bytes]],
    list[uuid.UUID],
    list[tuple[int, int]],
]:
    """(bevestigd: id, vector, persoon-index), (open: id, vector), de personen, de afwijzingen
    als (gezicht-index, persoon-index)."""
    key = crypto.load_key(settings, owner)
    if key is None:
        return [], [], [], []
    async with _maker(settings)() as db:
        known_rows = list(
            await db.execute(
                select(Face.id, Face.vec, FacePerson.person_id)
                .join(FacePerson, FacePerson.face_id == Face.id)
                .join(Person, Person.id == FacePerson.person_id)
                .join(Node, Node.id == Face.node_id)
                .where(
                    own_live(Node, owner),
                    Face.owner_id == owner,
                    Face.hidden == 0,
                    Face.vec.is_not(None),
                    FacePerson.verdict == "ja",
                    Person.hidden == 0,
                )
                .order_by(FacePerson.person_id, Face.id)
            )
        )
        ja = select(FacePerson.face_id).where(FacePerson.verdict == "ja")
        # Met only (de nieuwe gezichten na een batch): per id, dus zonder index op de eigenaar
        mine = face_rows.no_index(Face.owner_id) if only is not None else Face.owner_id
        q = (
            select(Face.id, Face.vec)
            .join(Node, Node.id == Face.node_id)
            .where(
                own_live(Node, owner),
                mine == owner,
                Face.hidden == 0,
                Face.vec.is_not(None),
                Face.id.not_in(ja),
            )
            .order_by(Face.created_at.desc(), Face.id)
        )
        if only is not None:
            q = q.where(Face.id.in_(only))
        else:
            q = q.limit(max(1, settings.faces_group_max))
        open_rows = list(await db.execute(q))
        nee = list(
            await db.execute(
                select(FacePerson.face_id, FacePerson.person_id).where(
                    FacePerson.owner_id == owner, FacePerson.verdict == "nee"
                )
            )
        )
    persons: list[uuid.UUID] = []
    index: dict[uuid.UUID, int] = {}
    known: list[tuple[uuid.UUID, bytes, int]] = []
    for face_id, sealed, person in known_rows:
        vec = crypto.open_vec(key, face_id, sealed)
        if vec is None:
            continue
        if person not in index:
            index[person] = len(persons)
            persons.append(person)
        known.append((face_id, vec, index[person]))
    opened: list[tuple[uuid.UUID, bytes]] = []
    for face_id, sealed in open_rows:
        vec = crypto.open_vec(key, face_id, sealed)
        if vec is not None:
            opened.append((face_id, vec))
    at = {face_id: len(known) + i for i, (face_id, _v) in enumerate(opened)}
    rejects = sorted(
        (at[face_id], index[person]) for face_id, person in nee if face_id in at and person in index
    )
    return known, opened, persons, rejects


def build_input(
    known: list[tuple[uuid.UUID, bytes, int]],
    opened: list[tuple[uuid.UUID, bytes]],
    persons: int,
    rejects: list[tuple[int, int]],
) -> bytes:
    """De invoer van group (PFG1): kop, vectoren (eerst bevestigd), persoon-index, afwijzingen."""
    n = len(known) + len(opened)
    parts = [wire.GROUP_HEAD.pack(b"PFG1", n, len(known), persons, len(rejects))]
    parts += [vec for _id, vec, _p in known]
    parts += [vec for _id, vec in opened]
    parts.append(struct.pack(f"<{len(known)}i", *(p for _id, _v, p in known)))
    parts.append(struct.pack(f"<{2 * len(rejects)}i", *(x for pair in rejects for x in pair)))
    return b"".join(parts)


def parse_output(raw: bytes, n: int, persons: int) -> list[tuple[int, float, int]]:
    """De uitvoer: precies n × GROUP_OUT, elke persoon-index binnen bereik; anders BadOutput."""
    if len(raw) != n * wire.GROUP_OUT.size:
        raise BadOutput
    found = list(wire.GROUP_OUT.iter_unpack(raw))
    for s, f, _g in found:
        if s < -1 or s >= persons or f != f:
            raise BadOutput
    return found


async def run_group(
    settings: Settings, owner: uuid.UUID, mode: str, *, only: list[uuid.UUID] | None = None
) -> int:
    """Eén ronde voor owner (suggest of full; only: alleen deze open gezichten). Geeft hoeveel
    open gezichten erin zaten. Fouten van de worker gaan door (de job beslist)."""
    known, opened, persons, rejects = await _read(settings, owner, only)
    if not opened:
        return 0
    result = await call(settings, mode, known, opened, len(persons), rejects)
    await _store(settings, owner, mode, opened, persons, result)
    return len(opened)


async def call(
    settings: Settings,
    mode: str,
    known: list[tuple[uuid.UUID, bytes, int]],
    opened: list[tuple[uuid.UUID, bytes]],
    persons: int,
    rejects: list[tuple[int, int]],
) -> list[tuple[int, float, int]]:
    """De opdracht group met de drempels van drempels.py; geeft (persoon-index of -1, score,
    groep of -1) per open gezicht. Ook voor app.cli analyse-kwaliteit (meten.py)."""
    data = build_input(known, opened, persons, rejects)
    n = len(known) + len(opened)
    src, out = wire.sealed_memfd("in", data), wire.out_memfd("uit")
    del data
    try:
        args = wire.GroupArgs(
            mode=mode,
            k=drempels.K,
            t_link=drempels.T_LINK,
            t_suggest=drempels.T_SUGGEST,
            margin=drempels.MARGIN,
            min_group=drempels.MIN_GROUP,
        )
        answer = await client.call(settings, wire.Op.GROUP, args, fds=[src, out])
        try:
            if wire.GroupData.model_validate(answer).n != n:
                raise BadOutput
            raw = wire.read_all(out, n * wire.GROUP_OUT.size + 1)
        except (ValidationError, ValueError):
            raise BadOutput from None
    finally:
        wire.close_all([src, out])
    return parse_output(raw, n, persons)[len(known) :]


async def _store(
    settings: Settings,
    owner: uuid.UUID,
    mode: str,
    opened: list[tuple[uuid.UUID, bytes]],
    persons: list[uuid.UUID],
    result: list[tuple[int, float, int]],
) -> None:
    rows = [
        {
            "b_id": face_id,
            "suggest_id": persons[s] if s >= 0 else None,
            "suggest_score": float(f) if s >= 0 else None,
            "cluster": g if g >= 0 else None,
        }
        for (face_id, _vec), (s, f, g) in zip(opened, result, strict=True)
    ]
    for start in range(0, len(rows), CHUNK):
        async with P.lock(owner), _maker(settings)() as db:
            first = start == 0
            await face_rows.write_group(
                db,
                owner,
                rows[start : start + CHUNK],
                full=mode == "full",
                first=first,
                at=state.now(),
            )
            await db.commit()
    state.changed(owner)


async def maybe_full(settings: Settings, owner: uuid.UUID) -> None:
    """Na een batch met nieuwe gezichten: full als de vorige langer dan een uur geleden is en de
    rij van deze eigenaar leeg is."""
    async with _maker(settings)() as db:
        row = await db.get(FaceOwner, owner)
        if row is None:
            return
        last = row.grouped_at
        if last is not None and state.now() - _aware(last) < FULL_EVERY:
            return
        if not await face_rows.queue_quiet(db, owner):
            return
        await queue_group(db, owner, "full")
        await db.commit()


def _aware(when):
    from datetime import UTC

    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


async def group_job(settings: Settings, payload: dict[str, Any]) -> None:
    from app.people.faces import scan

    if not state.running(settings):
        return
    state.configure(settings)
    owner = uuid.UUID(payload["owner"])
    mode = payload.get("mode", "suggest")
    async with _maker(settings)() as db:
        if not await state.allowed(db) or not await state.owner_on(db, owner):
            return
        row = await db.get(FaceOwner, owner)
        if row is not None and row.wiping:
            return
        if mode == "suggest" and row is not None and row.verdict_at is not None:
            left = (_aware(row.verdict_at) + VERDICT_WAIT - state.now()).total_seconds()
            if left > 0:
                raise jobs.Defer(left)
    if scan.must_yield(settings):
        raise jobs.Defer(scan.YIELD_S)
    wait = monitor.wait(())
    if wait is not None:
        raise wait
    try:
        await run_group(settings, owner, mode)
    except BadOutput:
        # De uitkomst klopt niet: niets geschreven, en geen nieuwe poging (zoals bad_output bij
        # een foto); de volgende keuze of batch zet een nieuwe ronde klaar
        log.warning("faces.group: bad_output (%s)", mode)
    except (client.AnalyseDown, client.AnalyseBusy, client.AnalyseFailed) as exc:
        deferral = monitor.deferral(exc)
        if deferral is not None:
            raise deferral from None
        # Alleen de code naar de taak (en /beheer), nooit het detail van het kind (K1-16)
        raise jobs.JobError(f"faces.group: {getattr(exc, 'code', 'down')}") from None
