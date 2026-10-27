"""Het herstel van forgotten files (Keuze 21).

start(app): een eigen taak, bij de start van de app, elke office_recovery_seconds en na
kick() (health.py roept dat aan bij "weer gezond"). run_once(): één ronde, het aantal
afgehandelde keys. restore(key) en forget(key): de knoppen Terugzetten en Verwijderen op
/beheer (O5).

Per key uit getForgottenList:
1. ophalen (Keuze 13, hooguit office_max_save_bytes; het quotum beslist place_file);
   dezelfde sha als het bestand nu, of als wat deze (dichte) sessie het laatst opsloeg (een
   herstelkopie waarna deleteForgotten mislukte) -> alleen deleteForgotten;
2. bestand of eigenaar definitief weg -> alleen deleteForgotten, niets schrijven;
3. de sessie van deze key is de laatste van het bestand (ook als hij nog saving of lost is)
   en het bestand staat nog op wat deze sessie het laatst zag -> gewoon op het origineel,
   met een schrijver van nu (Keuze 16); wat er stond, blijft altijd als versie (geen squash,
   net als een late opslag, Keuze 17);
4. anders (of zonder schrijver) -> een herstelkopie bij de eigenaar;
5. gelukt -> deleteForgotten en office.recovered;
6. lukt ook de kopie niet (quotum vol) -> de key blijft staan, met office.unsaved (één keer
   per key per proces); /beheer toont hem;
7. geen rij voor deze key -> alleen op /beheer, als onbekend.
"""

import asyncio
import contextlib
import logging
import os
import tempfile
import uuid
from pathlib import Path

import anyio
from fastapi import HTTPException
from sqlalchemy import select

from app import errors
from app.models import Node, OfficeSession
from app.office import engine, runtime, saving, sessions
from app.office.state import office_state
from app.routers.files import is_trashed
from app.storage import BlobStore

log = logging.getLogger("platform.office")

UNAVAILABLE = "De editor is nu niet bereikbaar; probeer het zo opnieuw"
OWNER_GONE = "De eigenaar van dit document bestaat niet meer"
NOT_FOUND = "Niet gevonden"

# Keys waarvoor al een melding office.unsaved uitging (niet elke ronde opnieuw)
_warned: set[str] = set()
_wake: asyncio.Event | None = None


def _event() -> asyncio.Event:
    global _wake
    if _wake is None:
        _wake = asyncio.Event()
    return _wake


async def start(app) -> None:
    """Start het herstel als taak (de lifespan annuleert hem bij het stoppen)."""
    global _wake
    _wake = asyncio.Event()
    state = office_state(app)
    task = asyncio.create_task(_loop(), name="office herstel")
    state.tasks.append(task)


def kick() -> None:
    """Meteen een ronde (DS is weer gezond)."""
    if _wake is not None:
        _wake.set()


async def _loop() -> None:
    wake = _event()
    while True:
        wake.clear()
        try:
            await run_once()
        except Exception:  # noqa: BLE001 - het herstel blijft draaien
            log.exception("office: herstel van forgotten files mislukt")
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(runtime.settings().office_recovery_seconds):
                await wake.wait()


async def run_once() -> int:
    """Eén ronde over getForgottenList; het aantal keys dat is afgehandeld (weg uit DS)."""
    eng = engine.current()
    if eng is None:
        return 0
    try:
        keys = await eng.forgotten()
    except Exception:  # noqa: BLE001 - DS weg: de volgende ronde
        log.warning("office: getForgottenList mislukt")
        return 0
    done = 0
    for key in keys:
        try:
            outcome = await handle(key)
        except Exception:  # noqa: BLE001 - de andere keys gaan door
            log.exception("office: herstel van een forgotten file mislukt")
            continue
        if outcome in ("deleted", "same", "saved", "copy"):
            done += 1
    return done


async def _fetch(eng, key: str) -> tuple[Path, str, int]:
    """Ophalen naar blobs.root/.tmp; (pad, sha, grootte). Bij een fout blijft er niets."""
    settings = runtime.settings()
    blobs = BlobStore(settings.blob_dir)
    await anyio.Path(blobs.tmp).mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=blobs.tmp)
    os.close(fd)
    tmp = Path(name)
    try:
        sha, size = await eng.fetch_forgotten(key, tmp, settings.office_max_save_bytes)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await anyio.Path(tmp).unlink(missing_ok=True)
        raise
    return tmp, sha, size


async def _delete(eng, key: str) -> bool:
    try:
        await eng.delete_forgotten(key)
    except Exception:  # noqa: BLE001 - de volgende ronde ziet dat het al is opgeslagen
        log.warning("office: deleteForgotten mislukt")
        return False
    _warned.discard(key)
    return True


def _code(exc: HTTPException) -> str | None:
    return exc.detail.get("code") if isinstance(exc.detail, dict) else None


async def handle(key: str) -> str:
    """Eén forgotten file: deleted | same | saved | copy | kept | unknown."""
    eng = engine.current()
    if eng is None:
        return "kept"
    async with sessions.maker()() as session:
        row = await sessions.find_by_key(session, key)
        if row is None:
            return "unknown"
        node, owner = await saving.alive(session, row)
        await session.commit()
    if node is None or owner is None:
        # Definitief weg (ook de eigenaar): de eigenaar wilde het weg (Keuze 19)
        return "deleted" if await _delete(eng, key) else "kept"
    tmp, sha, size = await _fetch(eng, key)
    try:
        outcome = await _store(row, tmp, sha, size)
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)
    if outcome in ("same", "saved", "copy"):
        await _delete(eng, key)
    return outcome


async def _store(row: OfficeSession, tmp: Path, sha: str, size: int) -> str:
    """Stap 1 (sha), 3 en 4, onder het slot van het bestand."""
    settings = runtime.settings()
    lock = sessions.office().node_lock(row.node_id) if row.node_id else asyncio.Lock()
    key = row.key  # na een mislukte place_file (rollback) zijn de objecten verlopen
    async with lock, sessions.maker()() as session:
        row = await sessions.find_by_key(session, key) or row
        node, owner = await saving.alive(session, row)
        if node is None or owner is None:
            return "kept"  # net weg: de volgende ronde ruimt op
        node_id = node.id
        if node.blob_id == sha or (row.state == "closed" and row.checkpoint == sha):
            # Staat er al, of deze sessie sloeg het al op (een herstelkopie waarna
            # deleteForgotten mislukte): alleen nog weg uit DS
            _close(row)
            await session.commit()
            return "same"
        newest = await sessions.latest(session, node.id)
        unchanged = newest is not None and newest.key == key and node.blob_id == row.blob_id
        try:
            if unchanged:
                actor = await saving.pick_actor(session, row, node, [])
                if actor is not None:
                    cfg = await saving.office_config(session)
                    result = await saving.save_original(
                        session,
                        settings,
                        cfg,
                        row,
                        node,
                        owner,
                        actor,
                        tmp,
                        sha,
                        size,
                        None,
                        squash=False,
                    )
                    if result != "saved":
                        return "kept"
                    row = await sessions.find_by_key(session, key) or row
                    _close(row)
                    row.last_error = None
                    await session.commit()
                    await sessions.publish_recovered(session, row, node, node.id, "forgotten")
                    await session.commit()
                    return "saved"
            result = await saving.save_copy(
                session, settings, row, node, owner, tmp, sha, size, None, "forgotten"
            )
        except HTTPException as exc:
            if _code(exc) in ("no_room", "no_room_owner"):
                await _unsaved(session, key, node_id)
                return "kept"
            raise
        if result != "copy":
            return "kept"
        row = await sessions.find_by_key(session, key) or row
        _close(row)
        row.last_error = None
        await session.commit()
        return "copy"


def _close(row: OfficeSession) -> None:
    """Een rij die nog niet dicht was, sluit als verloren (lost), niet als eigen eindopslag.
    DS laat de key van een forgotten file weer openen (gemeten): een editor die bleef of opnieuw
    verbond, kan daarna nog opslaan. Met lost is dat een late opslag (policy.late) en geen
    herhaling die niets schrijft. Een dichte rij houdt zijn reden."""
    if row.state != "closed":
        sessions.close(row, "lost")


async def _unsaved(session, key: str, node_id: uuid.UUID | None) -> None:
    """Stap 6: één melding per key; /beheer toont hem tot hij weg is."""
    row = await sessions.find_by_key(session, key)
    if row is None:
        return
    row.last_error = "no_room"
    await session.commit()
    if key in _warned:
        return
    _warned.add(key)
    node = await session.get(Node, node_id, populate_existing=True) if node_id else None
    await sessions.publish_unsaved(session, row, node, "no_room", True)
    await session.commit()


# --- de knoppen op /beheer (O5) ---


async def restore(key: str) -> Node:
    """Terugzetten: een herstelkopie bij de eigenaar (stap 4), als er nu ruimte is. Zette de
    ronde dit werk al neer (de key stond nog in DS), dan niet nog eens: het antwoord is waar het
    staat. Fouten: 404 not_found (geen rij), office_owner_gone, 413 no_room, 503
    office_unavailable."""
    eng = engine.current()
    if eng is None:
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    settings = runtime.settings()
    async with sessions.maker()() as session:
        row = await sessions.find_by_key(session, key)
        if row is None:
            raise errors.fail(404, "not_found", NOT_FOUND)
        node, owner = await saving.alive(session, row)
        if owner is None:
            raise errors.fail(404, "office_owner_gone", OWNER_GONE)
        await session.commit()
    try:
        tmp, sha, size = await _fetch(eng, key)
    except Exception:  # noqa: BLE001 - DS weg of de key bestaat niet meer
        log.warning("office: ophalen van een forgotten file mislukt")
        raise errors.fail(503, "office_unavailable", UNAVAILABLE) from None
    try:
        lock = sessions.office().node_lock(node.id) if node is not None else asyncio.Lock()
        async with lock, sessions.maker()() as session:
            row = await sessions.find_by_key(session, key) or row
            node, owner = await saving.alive(session, row)
            if owner is None:
                raise errors.fail(404, "office_owner_gone", OWNER_GONE)
            stored = await _stored(session, row, node, owner.id, sha)
            if stored is not None:
                # De ronde was eerst (en deleteForgotten mislukte, of loopt nog): niet nog eens
                await session.commit()
                copy = stored
            else:
                copy = await saving.place_copy(
                    session, settings, row, node, owner, tmp, sha, size, "forgotten", None
                )
                if isinstance(copy, str):
                    raise errors.fail(503, "office_unavailable", UNAVAILABLE)
                row = await sessions.find_by_key(session, key) or row
                row.checkpoint = sha  # wat deze sessie het laatst opsloeg (zie saving.save_copy)
                _close(row)
                row.last_error = None
                await session.commit()
                await sessions.publish_recovered(
                    session, row, copy, node.id if node else None, "forgotten"
                )
                await session.commit()
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)
    await _delete(eng, key)
    return copy


async def _stored(
    session, row: OfficeSession, node: Node | None, owner_id: uuid.UUID, sha: str
) -> Node | None:
    """Waar dit werk al staat: het origineel, of de herstelkopie die deze (dichte) sessie het
    laatst maakte. None: nog nergens, of die kopie is weg of in de prullenbak."""
    if node is not None and node.blob_id == sha:
        return node
    if row.state != "closed" or row.checkpoint != sha:
        return None
    found = await session.scalars(
        select(Node)
        .where(Node.owner_id == owner_id, Node.kind == "file", Node.blob_id == sha)
        .order_by(Node.created_at.desc())
        .limit(5)
    )
    for candidate in found.all():
        if not await is_trashed(session, candidate):
            return candidate
    return None


async def forget(key: str) -> None:
    """Verwijderen: de forgotten file weg uit DS, zonder iets op te slaan."""
    eng = engine.current()
    if eng is None:
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    try:
        await eng.delete_forgotten(key)
    except Exception:  # noqa: BLE001 - DS weg of de key bestaat niet meer
        log.warning("office: deleteForgotten mislukt")
        raise errors.fail(503, "office_unavailable", UNAVAILABLE) from None
    _warned.discard(key)
