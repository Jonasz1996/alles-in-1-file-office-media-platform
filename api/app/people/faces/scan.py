"""Gezichten vinden: de jobs faces.plan en faces.scan (fase 6, Keuze 60, 65 en 69; X2b).

**faces.plan** (rij default, in stukken van PLAN_CHUNK met een commit). Zet face_scan op wait voor
eigen, levende foto's (ftype image) van eigenaren met gezichten aan, zonder wisronde, en alleen
als de beheerder het toestaat. Keuze 69: een foto die een ander uploadde (node_origin.created_by
is niet de eigenaar) alleen als die ander zelf gezichten aan heeft; via een link nooit. Anders
skip met not_own_upload. Pas na de backfill van de index (de job analysis.backfill loopt niet
meer, Keuze 65). Scopes: owner (net aangezet), sha (een preview is klaar), sweep (elk uur),
version (een nieuwe FACES_VERSION).

**faces.scan** (rij analyse, één tegelijk). Een batch van hooguit PLATFORM_FACES_BATCH (16)
foto's, om de beurt per eigenaar (wie het langst niet aan de beurt was, face_owner.served_at),
hooguit PER_OWNER per eigenaar, de nieuwste eerst (prio; de viewer zet een foto vooraan). De app
opent 1024.webp alleen-lezen en geeft de fd's door; de worker geeft per foto kaders en scores in
JSON, en de vectoren in de memfd uit. De app controleert alles (bad_output als iets niet klopt),
en schrijft in één transactie (eerst een schrijvende statement, dus BEGIN IMMEDIATE via de
schrijfpoort; dan de instelling en het verbod; dan per node: eigen, levend, dezelfde blob, nog
busy op deze versie). De vector wordt versleuteld met de sleutel van de eigenaar (crypto).

Wijken: vóór de batch en elke POLL_S seconden tijdens de opdracht kijkt de job of er ≥ 2
transcodes lopen, OnlyOffice start, of /beheer op pauze staat. Zo ja: de opdracht afbreken, de
foto's terug naar wait (geen poging) en Defer(30). Eén opdracht per batch (de worker doet de
batch in één keer), dus "tussen twee foto's" is hier "tijdens de batch, elke seconde".

Nooit een vector of een kader in het log: alleen aantallen en codes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import struct
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import analysis, dbdialect, face_rows, jobs, previews
from app.analyse import client, monitor, wire
from app.config import Settings
from app.db import get_engine
from app.models import (
    Blob,
    Face,
    FaceOwner,
    FacePerson,
    FaceScan,
    Job,
    Node,
    NodeOrigin,
    PhotoPerson,
    PhotoPref,
)
from app.people import people as P
from app.people.faces import crypto, drempels, links, state
from app.people.faces.version import FACES_MODELS, FACES_VERSION
from app.search.live import own_live
from app.storage import BlobStore

log = logging.getLogger("platform.faces")

PLAN = "faces.plan"
SCAN = "faces.scan"
NOT_OWN_UPLOAD = "not_own_upload"  # face_scan.code (Keuze 69), alleen op /beheer, geen HTTP-fout
PLAN_CHUNK = 500
PER_OWNER = 4
MAX_TRIES = 3
YIELD_S = 30.0  # wijken (Keuze 50)
BACKFILL_WAIT_S = 60.0  # de index is nog bezig (Keuze 65)
POLL_S = 1.0  # zo vaak kijkt de job tijdens een opdracht of hij moet wijken
BUSY_STALE = timedelta(minutes=10)  # busy ouder dan dit: terug naar wait (sweep)
FRONT = "~"  # prio die vóór elke datum komt (de viewer)
LARGE_NAME = "1024.webp"
_VEC = struct.Struct(f"<{wire.FACE_DIM}f")
EPS = 1e-6


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def must_yield(settings: Settings) -> bool:
    """Wijken: transcodes of OnlyOffice (analysis.busy_elsewhere) of de pauze van /beheer."""
    from app.search import admin as search_admin

    return analysis.busy_elsewhere(settings) or search_admin.is_paused()


def waits_for(settings: Settings) -> str | None:
    """Waarop de rij analyse wacht, voor /beheer."""
    from app.search import admin as search_admin

    if search_admin.is_paused():
        return "paused"
    if analysis.busy_elsewhere(settings):
        return "transcodes"
    return None


def prio_of(taken_at: datetime | None, created_at: datetime | None) -> str:
    when = taken_at or created_at
    return when.isoformat() if when is not None else ""


async def backfill_active(db: AsyncSession) -> bool:
    """Loopt de backfill van de index nog (Keuze 65: gezichten pas daarna)?"""
    found = await db.scalar(
        select(Job.id).where(Job.key == analysis.BACKFILL, Job.status.in_(jobs.ACTIVE)).limit(1)
    )
    return found is not None


# --- plannen ------------------------------------------------------------------------------------


async def queue_plan(
    db: AsyncSession, scope: str, *, owner: uuid.UUID | None = None, sha: str | None = None
) -> None:
    payload: dict[str, Any] = {"scope": scope}
    key = f"{PLAN}:{scope}"
    if owner is not None:
        payload["owner"] = owner.hex
        key += f":{owner.hex}"
    if sha is not None:
        payload["sha"] = sha
        key += f":{sha}"
    await jobs.enqueue(db, PLAN, payload, key=key)


async def queue_scan(db: AsyncSession) -> None:
    await jobs.enqueue(db, SCAN, {}, key=SCAN)


async def _creators_on(db: AsyncSession, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    found = await db.scalars(
        select(PhotoPref.user_id).where(PhotoPref.user_id.in_(ids), PhotoPref.faces == "aan")
    )
    return set(found)


async def plan_nodes(
    db: AsyncSession,
    owner: uuid.UUID,
    *,
    after: uuid.UUID | None = None,
    sha: str | None = None,
    force: bool = False,
) -> tuple[int, uuid.UUID | None]:
    """Eén stuk van PLAN_CHUNK foto's van owner: face_scan op wait (of skip met not_own_upload,
    Keuze 69). Alleen foto's zonder scan op deze versie (force: ook die). Geeft (aantal op wait,
    de laatste node als er een volgend stuk is). Nog niet gecommit."""
    q = (
        select(
            Node.id,
            Node.taken_at,
            Node.created_at,
            NodeOrigin.created_by,
            NodeOrigin.via_link,
            FaceScan.state,
            FaceScan.version,
        )
        .outerjoin(NodeOrigin, NodeOrigin.node_id == Node.id)
        .outerjoin(FaceScan, FaceScan.node_id == Node.id)
        .where(own_live(Node, owner), Node.ftype == "image", Node.blob_id.is_not(None))
        .order_by(Node.id)
        .limit(PLAN_CHUNK)
    )
    if after is not None:
        q = q.where(Node.id > after)
    if sha is not None:
        q = q.where(Node.blob_id == sha)
    rows = list(await db.execute(q))
    others = {r.created_by for r in rows if r.created_by is not None and r.created_by != owner}
    on = await _creators_on(db, others)
    at = state.now()
    waiting = 0
    for r in rows:
        if r.state == "busy" and r.version == FACES_VERSION:
            continue
        if r.state is not None and r.version == FACES_VERSION and not force:
            continue
        foreign = bool(r.via_link) or (
            r.created_by is not None and r.created_by != owner and r.created_by not in on
        )
        values = {
            "owner_id": owner,
            "version": FACES_VERSION,
            "state": "skip" if foreign else "wait",
            "faces": 0,
            "tries": 0,
            "code": NOT_OWN_UPLOAD if foreign else None,
            "prio": prio_of(r.taken_at, r.created_at),
            "queued_at": at,
            "updated_at": at,
        }
        dialect = dbdialect.of(db)
        await db.execute(
            dialect.insert(FaceScan)
            .values(node_id=r.id, **values)
            .on_conflict_do_update(index_elements=["node_id"], set_=values)
        )
        waiting += not foreign
    last = rows[-1].id if len(rows) == PLAN_CHUNK else None
    return waiting, last


async def _plan_owner(settings: Settings, owner: uuid.UUID, payload: dict[str, Any]) -> None:
    after = payload.get("after")
    async with P.lock(owner), _maker(settings)() as db:
        if not await state.allowed(db) or not await state.owner_on(db, owner):
            return
        if await state.wiping(db, owner):
            raise jobs.Defer(YIELD_S)
        waiting, last = await plan_nodes(
            db, owner, after=uuid.UUID(after) if after else None, sha=payload.get("sha")
        )
        if waiting:
            await queue_scan(db)
        if last is not None:
            await jobs.chain(
                db, PLAN, {**payload, "after": last.hex}, key=f"{PLAN}:owner:{owner.hex}"
            )
        await db.commit()


async def _plan_sha(settings: Settings, sha: str) -> None:
    async with _maker(settings)() as db:
        owners = list(
            await db.scalars(
                select(Node.owner_id)
                .join(PhotoPref, PhotoPref.user_id == Node.owner_id)
                .where(Node.blob_id == sha, PhotoPref.faces == "aan")
                .distinct()
            )
        )
    for owner in owners:
        async with P.lock(owner), _maker(settings)() as db:
            if not await state.allowed(db) or await state.wiping(db, owner):
                continue
            # no_preview wacht op deze preview: weer gewoon wait
            await db.execute(
                update(FaceScan)
                .where(
                    FaceScan.owner_id == owner,
                    FaceScan.code == "no_preview",
                    FaceScan.node_id.in_(
                        select(Node.id).where(Node.blob_id == sha, own_live(Node, owner))
                    ),
                )
                .values(code=None, updated_at=state.now())
            )
            waiting, _last = await plan_nodes(db, owner, sha=sha)
            if waiting or await face_rows.has_waiting(db, owner):
                await queue_scan(db)
            await db.commit()


async def _sweep(settings: Settings) -> None:
    """Elk uur: busy ouder dan 10 minuten terug naar wait, no_preview opnieuw proberen, en per
    eigenaar met gezichten aan een plan (wat ontbreekt, een oude versie)."""
    async with _maker(settings)() as db:
        owners = await state.owners_on(db)
    async with _maker(settings)() as db:
        await face_rows.reset_busy(db, before=state.now() - BUSY_STALE)
        await face_rows.retry_no_preview(db)
        for owner in owners:
            await queue_plan(db, "owner", owner=owner)
        await db.commit()


async def _version(settings: Settings) -> None:
    """Een nieuwe FACES_VERSION: per eigenaar de gezichten met hidden 0 van een oude versie weg
    (in stukken, elk met een commit) en face_scan terug naar wait; daarna plannen en full."""
    from app.people.faces import group

    async with _maker(settings)() as db:
        owners = await state.owners_on(db)
    for owner in owners:
        while True:
            async with P.lock(owner), _maker(settings)() as db:
                n = await face_rows.drop_version_chunk(db, owner, FACES_VERSION, state.now())
                await db.commit()
            if not n:
                break
        async with _maker(settings)() as db:
            await queue_plan(db, "owner", owner=owner)
            await queue_scan(db)
            await group.queue_group(db, owner, "full")
            await db.commit()


async def plan_job(settings: Settings, payload: dict[str, Any]) -> None:
    if not state.running(settings):
        return
    state.configure(settings)
    async with _maker(settings)() as db:
        if await backfill_active(db):
            raise jobs.Defer(BACKFILL_WAIT_S)
    scope = payload.get("scope")
    if scope == "owner":
        await _plan_owner(settings, uuid.UUID(payload["owner"]), payload)
    elif scope == "sha":
        await _plan_sha(settings, str(payload["sha"]))
    elif scope == "sweep":
        await _sweep(settings)
    elif scope == "version":
        await _version(settings)


# --- de batch -----------------------------------------------------------------------------------


@dataclass
class Item:
    node: uuid.UUID
    owner: uuid.UUID
    sha: str
    tries: int
    fd: int | None = None
    code: str | None = None  # niet naar de worker: no_preview of too_small
    photo: wire.FacesPhoto | None = None
    vectors: list[bytes] = field(default_factory=list)


async def _pick(settings: Settings) -> list[Item]:
    """Kies de batch en zet hem op busy: om de beurt per eigenaar, hooguit PER_OWNER elk, de
    nieuwste eerst."""
    size = max(1, min(settings.faces_batch, wire.PHOTOS_PER_OP))
    async with _maker(settings)() as db:
        if not await state.allowed(db):
            return []
        # Wie iets heeft dat wacht: alleen uit ix_face_scan_todo (de rijen zelf lezen kost bij
        # 30.000 wachtende foto's 40 ms per batch); een eigenaar met alleen wachtende foto's met
        # een code (model_missing, no_preview) levert hieronder niets op
        owners = list(
            await db.scalars(select(FaceScan.owner_id).where(FaceScan.state == "wait").distinct())
        )
        served = {
            r.owner_id: r.served_at
            for r in await db.execute(
                select(FaceOwner.owner_id, FaceOwner.served_at).where(
                    FaceOwner.owner_id.in_(owners)
                )
            )
        }
        owners.sort(key=lambda o: (served.get(o) is not None, served.get(o) or state.now()))
        picked: list[Item] = []
        for owner in owners:
            if not await state.owner_on(db, owner) or await state.wiping(db, owner):
                continue
            if await crypto.key_lost(settings, db, owner):
                # De sleutel is weg met versleutelde gezichten erbij: niets nieuws versleutelen
                # (crypto.KeyLost); /beheer en sleutels-check zeggen het
                continue
            rows = await db.execute(
                select(FaceScan.node_id, Node.blob_id, FaceScan.tries)
                .join(Node, Node.id == FaceScan.node_id)
                .where(
                    FaceScan.owner_id == owner,
                    FaceScan.state == "wait",
                    FaceScan.code.is_(None),
                    FaceScan.version == FACES_VERSION,
                    own_live(Node, owner),
                    Node.ftype == "image",
                )
                .order_by(FaceScan.prio.desc(), FaceScan.node_id)
                .limit(min(PER_OWNER, size - len(picked)))
            )
            picked += [Item(r.node_id, owner, r.blob_id, r.tries) for r in rows]
            if len(picked) >= size:
                break
    if not picked:
        return []
    owners_in = sorted({i.owner for i in picked})
    async with contextlib.AsyncExitStack() as stack:
        for owner in owners_in:
            await stack.enter_async_context(P.lock(owner))
        async with _maker(settings)() as db:
            at = state.now()
            await db.execute(
                update(FaceScan)
                .where(
                    FaceScan.node_id.in_([i.node for i in picked]),
                    face_rows.no_index(FaceScan.state) == "wait",
                )
                .values(state="busy", updated_at=at)
            )
            for owner in owners_in:
                row = await state.owner_row(db, owner)
                row.served_at = at
            await db.commit()
    return picked


async def _restore(settings: Settings, items: list[Item], *, tries: bool = False) -> None:
    """De foto's terug naar wait (wijken, Defer), of met een poging erbij (timeout; na MAX_TRIES
    failed)."""
    owners = sorted({i.owner for i in items})
    async with contextlib.AsyncExitStack() as stack:
        for owner in owners:
            await stack.enter_async_context(P.lock(owner))
        async with _maker(settings)() as db:
            at = state.now()
            for item in items:
                values: dict[str, Any] = {"state": "wait", "updated_at": at}
                if tries:
                    n = item.tries + 1
                    values = {"tries": n, "updated_at": at, "state": "wait"}
                    if n >= MAX_TRIES:
                        values.update(state="failed", code="timeout")
                await face_rows.set_scan(db, item.node, values, only_busy=True)
            await db.commit()


def _open_previews(settings: Settings, items: list[Item], metas: dict[str, Any]) -> None:
    """De 1024 van elke foto alleen-lezen openen. Geen 1024: too_small als de previews klaar
    zijn en er geen 1024 komt (het origineel is te klein), anders no_preview (wacht)."""
    blobs = BlobStore(settings.blob_dir)
    for item in items:
        path = previews.directory(blobs, item.sha) / LARGE_NAME
        try:
            item.fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            meta = metas.get(item.sha)
            sizes = (meta or {}).get("sizes") or []
            done = meta is not None and not meta.get("partial") and sizes
            item.code = "too_small" if done and previews.LARGE not in sizes else "no_preview"
        except OSError:
            item.code = "no_preview"


def _vectors_ok(raw: bytes) -> bool:
    values = _VEC.unpack(raw)
    if not all(math.isfinite(v) for v in values):
        return False
    return abs(math.sqrt(sum(v * v for v in values)) - 1.0) <= 0.001


def _check(settings: Settings, sent: list[Item], answer: dict, raw: bytes | None) -> str | None:
    """De uitvoer van de worker controleren; per foto photo en vectors invullen. Geeft
    bad_output als het geheel niet klopt (dan is elke foto bad_output)."""
    if raw is None:
        return "bad_output"
    try:
        data = wire.FacesData.model_validate(answer)
    except ValidationError:
        return "bad_output"
    if len(data.photos) != len(sent) or len(raw) != data.vector_bytes():
        return "bad_output"
    pos = 0
    for item, photo in zip(sent, data.photos, strict=True):
        n = photo.n
        chunk = raw[pos : pos + n * wire.FACE_VEC_BYTES]
        pos += n * wire.FACE_VEC_BYTES
        if photo.code != "ok":
            item.code = "too_big" if photo.code == "too_big" else "unreadable"
            continue
        boxes_ok = all(
            b.x + b.w <= 1 + EPS and b.y + b.h <= 1 + EPS and 0 <= b.score <= 1 for b in photo.faces
        )
        vecs = [chunk[i * wire.FACE_VEC_BYTES : (i + 1) * wire.FACE_VEC_BYTES] for i in range(n)]
        if n > settings.faces_max_per_photo or not boxes_ok or not all(map(_vectors_ok, vecs)):
            item.code = "bad_output"
            continue
        item.photo = photo
        item.vectors = vecs
    return None


async def _worker(settings: Settings, sent: list[Item]) -> tuple[dict, bytes | None]:
    """De opdracht faces voor de foto's met een fd, met wijken tijdens het wachten."""
    out = wire.out_memfd("uit")
    try:
        args = wire.FacesArgs(
            min_px=settings.faces_min_px,
            max=min(settings.faces_max_per_photo, wire.FACES_MAX),
            score=drempels.SCORE,
        )
        fds = [i.fd for i in sent if i.fd is not None]
        job = await client.start(settings, wire.Op.FACES, args, fds=[*fds, out])
        task = asyncio.ensure_future(job.result())
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=POLL_S)
                if done:
                    break
                if must_yield(settings):
                    raise jobs.Defer(YIELD_S)
            answer = task.result()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            job.close()
        try:
            raw: bytes | None = wire.read_all(
                out, len(sent) * wire.FACES_MAX * wire.FACE_VEC_BYTES + 1
            )
        except ValueError:
            raw = None  # groter dan ooit kan: bad_output in _check
        return answer, raw
    finally:
        wire.close_all([out])


async def _write(settings: Settings, items: list[Item]) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Eén schrijftransactie voor de batch. Geeft per eigenaar de nieuwe gezichten."""
    owners = sorted({i.owner for i in items})
    new: dict[uuid.UUID, list[uuid.UUID]] = {}
    async with contextlib.AsyncExitStack() as stack:
        for owner in owners:
            await stack.enter_async_context(P.lock(owner))
        async with _maker(settings)() as db:
            at = state.now()
            # eerst schrijven: dan begint de transactie met het schrijfslot (BEGIN IMMEDIATE)
            await db.execute(
                update(FaceOwner).where(FaceOwner.owner_id.in_(owners)).values(served_at=at)
            )
            allowed = await state.allowed(db)
            ok_owner = {
                o: allowed
                and await state.owner_on(db, o)
                and not await state.wiping(db, o)
                and not await crypto.key_lost(settings, db, o)
                for o in owners
            }
            for item in items:
                current = (
                    await db.execute(
                        select(Node.blob_id, FaceScan.state, FaceScan.version)
                        .join(FaceScan, FaceScan.node_id == Node.id)
                        .where(Node.id == item.node, own_live(Node, item.owner))
                    )
                ).first()
                if current is None or current.state != "busy" or current.version != FACES_VERSION:
                    continue
                if not ok_owner[item.owner] or current.blob_id != item.sha:
                    # Uit, verboden, een wisronde, een sleutel die weg is, of een andere inhoud:
                    # niets schrijven; de foto weer op wait (een wisronde ruimt hem op, een nieuwe
                    # inhoud komt opnieuw)
                    await face_rows.set_scan(
                        db, item.node, {"state": "wait", "updated_at": at}, only_busy=True
                    )
                    continue
                if item.photo is None:
                    await _set_code(db, item, at)
                    continue
                made = await _store_faces(settings, db, item, at)
                new.setdefault(item.owner, []).extend(made)
            await db.commit()
    return new


async def _set_code(db: AsyncSession, item: Item, at: datetime) -> None:
    code = item.code or "bad_output"
    if code == "no_preview":
        values = {"state": "wait", "code": code}
    elif code == "too_small":
        values = {"state": "skip", "code": code}
    else:
        values = {"state": "failed", "code": code}
    await face_rows.set_scan(db, item.node, {"updated_at": at, **values})


async def _store_faces(
    settings: Settings, db: AsyncSession, item: Item, at: datetime
) -> list[uuid.UUID]:
    """De gezichten van één foto: verborgen blijft verborgen (IoU met een verborgen gezicht),
    een kader in photo_person wordt ja (links.reconcile), en de tag."""
    assert item.photo is not None
    hidden = list(
        await db.execute(
            select(Face.x, Face.y, Face.w, Face.h)
            .join(Node, Node.id == Face.node_id)
            .where(Face.node_id == item.node, Face.hidden == 1, own_live(Node, item.owner))
        )
    )
    await db.execute(delete(Face).where(Face.node_id == item.node, Face.hidden == 0))
    key = await crypto.ensure_key(settings, db, item.owner) if item.photo.n else None
    made: list[uuid.UUID] = []
    for box, vec in zip(item.photo.faces, item.vectors, strict=True):
        here = (box.x, box.y, box.w, box.h)
        if any(links.iou(here, (h.x, h.y, h.w, h.h)) >= drempels.IOU for h in hidden):
            continue
        face_id = uuid.uuid4()
        assert key is not None
        db.add(
            Face(
                id=face_id,
                node_id=item.node,
                owner_id=item.owner,
                x=box.x,
                y=box.y,
                w=box.w,
                h=box.h,
                score=box.score,
                px=box.px,
                vec=crypto.seal(key, face_id, vec),
                version=FACES_VERSION,
                hidden=0,
                created_at=at,
            )
        )
        made.append(face_id)
    await db.flush()
    await db.run_sync(lambda s: links.reconcile(s.connection(), item.owner, item.node))
    await db.execute(
        update(FaceScan)
        .where(FaceScan.node_id == item.node)
        .values(state="done", faces=len(made), code=None, updated_at=at)
    )
    return made


async def scan_job(settings: Settings, payload: dict[str, Any]) -> None:
    """Eén batch; daarna voorstellen voor de nieuwe gezichten, people.changed, en opnieuw in de
    rij zolang er werk is."""
    from app.people.faces import group

    if not state.running(settings):
        return
    state.configure(settings)
    if must_yield(settings):
        raise jobs.Defer(YIELD_S)
    found = monitor.status()
    wait = monitor.wait(FACES_MODELS, found)
    if wait is not None:
        raise wait
    items = await _pick(settings)
    if not items:
        return
    try:
        async with _maker(settings)() as db:
            metas = dict(
                (r.sha256, r.preview_meta)
                for r in await db.execute(
                    select(Blob.sha256, Blob.preview_meta).where(
                        Blob.sha256.in_({i.sha for i in items})
                    )
                )
            )
        _open_previews(settings, items, metas)
        sent = [i for i in items if i.fd is not None]
        if sent:
            try:
                answer, raw = await _worker(settings, sent)
            except jobs.Defer:
                await _restore(settings, items)
                raise
            except (client.AnalyseDown, client.AnalyseBusy, client.AnalyseFailed) as exc:
                deferral = monitor.deferral(exc)
                if deferral is not None:
                    await _restore(settings, items)
                    raise deferral from None
                # alleen de code, nooit het detail van het kind (K1-16)
                code = getattr(exc, "code", "down")
                log.warning("faces.scan: opdracht mislukt (%s), %d foto's", code, len(sent))
                await _restore(settings, sent, tries=True)
                await _write(settings, [i for i in items if i.fd is None])
                await _again(settings)
                return
            whole = _check(settings, sent, answer, raw)
            if whole is not None:
                for item in sent:
                    item.code = whole
    finally:
        wire.close_all([i.fd for i in items if i.fd is not None])
    new = await _write(settings, items)
    for owner, faces in new.items():
        if faces:
            with contextlib.suppress(client.AnalyseDown, client.AnalyseBusy, client.AnalyseFailed):
                await group.run_group(settings, owner, "suggest", only=faces)
            await group.maybe_full(settings, owner)
    for owner in {i.owner for i in items}:
        state.changed(owner)
    await _again(settings)


async def _again(settings: Settings) -> None:
    async with _maker(settings)() as db:
        if await face_rows.has_waiting(db):
            await jobs.chain(db, SCAN, {}, key=SCAN)
            await db.commit()


async def to_front(db: AsyncSession, owner: uuid.UUID, node: uuid.UUID) -> bool:
    """De viewer opent een foto die nog wacht: die gaat vooraan (prio FRONT + nu). Eerst lezen (een
    zoekslag op de sleutel), en alleen schrijven als hij echt wacht: de viewer vraagt dit bij elke
    foto, en een lege schrijfslag kost evenveel als een volle. True als hij vooraan ging."""
    waiting = await db.scalar(
        select(FaceScan.node_id)
        .join(Node, Node.id == FaceScan.node_id)
        .where(
            FaceScan.node_id == node,
            FaceScan.owner_id == owner,
            FaceScan.state == "wait",
            own_live(Node, owner),
        )
    )
    if waiting is None:
        return False
    await db.execute(
        update(FaceScan)
        .where(FaceScan.node_id == node, FaceScan.owner_id == owner, FaceScan.state == "wait")
        .values(prio=FRONT + state.now().isoformat())
    )
    return True


async def rescan(db: AsyncSession, owner: uuid.UUID, node: uuid.UUID) -> bool:
    """Opnieuw zoeken (POST /files/{id}/faces/rescan): de gezichten met hidden 0 weg (met hun
    oordelen), dan wait vooraan. photo_person volgt sync_links: een rij 'auto' gaat mee weg; een
    rij met de hand blijft, en met een kader geeft die bij de nieuwe scan de ja terug (IoU)."""
    live = await db.scalar(
        select(Node.id).where(Node.id == node, own_live(Node, owner), Node.ftype == "image")
    )
    if live is None:
        return False
    persons = list(
        await db.scalars(
            select(FacePerson.person_id)
            .join(Face, Face.id == FacePerson.face_id)
            .where(Face.node_id == node, Face.hidden == 0, FacePerson.verdict == "ja")
        )
    )
    await db.execute(delete(Face).where(Face.node_id == node, Face.hidden == 0))
    for person in persons:
        await links.sync_links(db, owner, node, person)
    at = state.now()
    values = {
        "owner_id": owner,
        "version": FACES_VERSION,
        "state": "wait",
        "faces": 0,
        "tries": 0,
        "code": None,
        "prio": FRONT + at.isoformat(),
        "queued_at": at,
        "updated_at": at,
    }
    dialect = dbdialect.of(db)
    await db.execute(
        dialect.insert(FaceScan)
        .values(node_id=node, **values)
        .on_conflict_do_update(index_elements=["node_id"], set_=values)
    )
    await queue_scan(db)
    return True


async def any_photo_person(db: AsyncSession, owner: uuid.UUID, node: uuid.UUID) -> bool:
    found = await db.scalar(
        select(PhotoPerson.person_id)
        .join(Node, Node.id == PhotoPerson.node_id)
        .where(PhotoPerson.node_id == node, own_live(Node, owner))
        .limit(1)
    )
    return found is not None
