"""Gezichten aanmelden bij de rest van de app (fase 6, X2b). Eén keer, bij de import van
routers/people_faces.py (en dus van app.main); tests die de aanmeldingen weghaalden, roepen
register() opnieuw aan.

- de jobs faces.plan en faces.wipe (rij default), faces.scan en faces.group (rij analyse);
- de schakelaar faces van PATCH /me/photo-settings (prefs.register_switch);
- het blok "Gezichten" van /beheer (search.hooks.register_admin("faces"));
- de haken van previews (register_large, register_ready), van een kopie (register_photo_copy),
  van de start (register_backfill) en van Z6 (people.hooks);
- de ronde van elk uur (jobs.housekeeping).

**Uit is onzichtbaar.** Zonder PLATFORM_ANALYSE_ENABLED (of zonder PLATFORM_FACES_KEYS) is
gezichten "niet draaiend" (state.running): de schakelaar zegt niet beschikbaar en niet verboden
(zoals 0.8.0), het blok op /beheer is None, de schakelaar en "opnieuw" van /beheer geven 501
not_built("X2") zoals 0.8.0, en geen haak zet een job klaar of schrijft iets onder DATA_ROOT.
"Uit" zetten mag altijd: dat wist (rij default), maar alleen als er iets te wissen is.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors, face_rows, jobs, prefs, previews
from app.analyse import monitor
from app.config import Settings
from app.models import Face, FacePerson, FaceScan, Node, PhotoPref, Setting, User
from app.people import hooks as people_hooks
from app.people.faces import crypto, group, scan, state, verdicts, wipe
from app.people.faces.version import FACES_VERSION
from app.routers.stubs import not_built
from app.search import hooks as search_hooks
from app.search.live import own_live
from app.search.types import AdminFaces, AnalyseModel, AnalyseQueue, CodeCount

HOURLY = "faces.hourly"
BYTES_PER_FACE = 800  # ±0,8 kB per gezicht in de database (Keuze 67)


# --- de schakelaar (PATCH /me/photo-settings) ---------------------------------------------------


async def switch_state(db: AsyncSession, user: User) -> prefs.SwitchState:
    if not state.running(state.settings_for(db)):
        return prefs.SwitchState()
    return prefs.SwitchState(forbidden=not await state.allowed(db), available=True)


async def switch_changed(db: AsyncSession, user: User, value: str) -> None:
    """aan: faces.plan voor deze eigenaar. uit: eerst de sleutel weg, dan faces.wipe (all) als
    er iets te wissen is."""
    settings = state.settings_for(db)
    if value == "aan":
        if state.running(settings):
            await scan.queue_plan(db, "owner", owner=user.id)
        return
    if settings is not None and settings.faces_keys.strip():
        crypto.delete_key(settings, user.id)
    if await face_rows.has_rows(db, user.id):
        await wipe.queue_wipe(db, user.id, "all")
    state.changed(user.id)


# --- /beheer ------------------------------------------------------------------------------------


async def admin_block(db: AsyncSession) -> AdminFaces | None:
    settings = state.settings_for(db)
    if not state.running(settings):
        return None
    assert settings is not None
    found = monitor.status()
    counts = await face_rows.server_counts(db)
    done_hour = await face_rows.done_since(db, state.now() - timedelta(hours=1))
    caps = found.caps
    models = [
        AnalyseModel(name=m.name, version=getattr(m, "version", ""), sha_ok=m.state == "ok")
        for m in (caps.models if caps is not None else [])
    ]
    return AdminFaces(
        allowed=await state.allowed(db),
        analyse=found.state,
        reason=found.reason,
        models=models,
        limits=None,
        users_on=counts["users_on"],
        queue=AnalyseQueue(
            waiting=counts["waiting"],
            done_hour=done_hour,
            waits_for=scan.waits_for(settings),  # type: ignore[arg-type]
        ),
        failed_by_code=[
            CodeCount(code=k, n=v) for k, v in sorted(counts["failed_by_code"].items())
        ],
        faces=counts["faces"],
        named=counts["named"],
        db_bytes=counts["faces"] * BYTES_PER_FACE,
        bench=None,
        eta_s=None,
        wipe_waiting=bool(wipe.WAITING),
        quality=None,
    )


async def admin_switch(db: AsyncSession, value: bool) -> None:
    """Gezichtsherkenning toestaan (Keuze 64). Een verbod zet iedereen uit en wist; weer
    toestaan zet niemand vanzelf aan."""
    settings = state.settings_for(db)
    if not state.running(settings):
        raise not_built("X2")
    assert settings is not None
    row = await db.get(Setting, state.SEARCH, populate_existing=True)
    current = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    current["faces_allowed"] = bool(value)
    if row is None:
        db.add(Setting(key=state.SEARCH, value=current))
    else:
        row.value = current
    if value:
        return
    owners = await state.owners_on(db)
    await db.execute(update(PhotoPref).where(PhotoPref.faces == "aan").values(faces="uit"))
    for owner in sorted(set(owners) | set(await face_rows.owners_with_rows(db))):
        crypto.delete_key(settings, owner)
        await wipe.queue_wipe(db, owner, "all")


async def admin_rebuild(db: AsyncSession) -> None:
    """ "Mislukte opnieuw" (RebuildWhat faces): failed terug naar wait, en de versie nalopen."""
    settings = state.settings_for(db)
    if not state.running(settings):
        raise not_built("X2")
    await face_rows.retry_failed(db, state.now())
    await scan.queue_plan(db, "sweep")
    await scan.queue_scan(db)


# --- haken --------------------------------------------------------------------------------------


async def after_large(maker: Any, sha: str) -> None:
    """previews.register_large: de 1024 is er; faces.plan met scope sha."""
    async with maker() as db:
        if not state.running(state.settings_for(db)):
            return
        await scan.queue_plan(db, "sha", sha=sha)
        await db.commit()


async def after_ready(maker: Any, sha: str, dated: bool) -> None:
    """previews.register_ready: ook een kleine foto (geen 1024) krijgt zo een plan (too_small)."""
    await after_large(maker, sha)


async def on_photo_copy(db: AsyncSession, pairs: Sequence[tuple[uuid.UUID, uuid.UUID]]) -> None:
    """Een kopie door dezelfde eigenaar: is de bron klaar op deze versie met dezelfde blob, dan
    gaan de gezichten (opnieuw versleuteld, met de nieuwe id als AAD), de oordelen en face_scan
    mee; anders wacht de kopie. Een kopie door een ander krijgt nooit iets mee."""
    settings = state.settings_for(db)
    if not state.running(settings) or not await state.allowed(db):
        return
    assert settings is not None
    for source, copy in pairs:
        await _copy_one(settings, db, source, copy)


async def _copy_one(
    settings: Settings, db: AsyncSession, source: uuid.UUID, copy: uuid.UUID
) -> None:
    owner = await db.scalar(select(Node.owner_id).where(Node.id == copy))
    if owner is None or not await state.owner_on(db, owner) or await state.wiping(db, owner):
        return
    src = (
        await db.execute(
            select(Node.blob_id, FaceScan.state, FaceScan.version, FaceScan.prio)
            .outerjoin(FaceScan, FaceScan.node_id == Node.id)
            .where(Node.id == source, own_live(Node, owner))
        )
    ).first()
    dst_blob = await db.scalar(select(Node.blob_id).where(Node.id == copy, own_live(Node, owner)))
    if dst_blob is None:
        return
    key = crypto.load_key(settings, owner)
    ready = (
        src is not None
        and src.state == "done"
        and src.version == FACES_VERSION
        and src.blob_id == dst_blob
        and key is not None
    )
    if not ready:
        await scan.plan_nodes(db, owner, sha=dst_blob)
        await scan.queue_scan(db)
        return
    at = state.now()
    faces = list(await db.execute(select(Face).where(Face.node_id == source)))
    verdicts_of = list(
        await db.execute(select(FacePerson).where(FacePerson.face_id.in_([f.id for (f,) in faces])))
    )
    new_ids: dict[uuid.UUID, uuid.UUID] = {}
    for (f,) in faces:
        new_id = uuid.uuid4()
        new_ids[f.id] = new_id
        vec = crypto.open_vec(key, f.id, f.vec) if f.vec is not None else None
        db.add(
            Face(
                id=new_id,
                node_id=copy,
                owner_id=owner,
                x=f.x,
                y=f.y,
                w=f.w,
                h=f.h,
                score=f.score,
                px=f.px,
                vec=crypto.seal(key, new_id, vec) if vec else None,
                version=f.version,
                cluster=None,
                suggest_id=f.suggest_id,
                suggest_score=f.suggest_score,
                suggest_why=f.suggest_why,
                hidden=f.hidden,
                created_at=at,
            )
        )
    await db.flush()
    for (v,) in verdicts_of:
        db.add(
            FacePerson(
                face_id=new_ids[v.face_id],
                person_id=v.person_id,
                owner_id=owner,
                verdict=v.verdict,
                created_at=at,
            )
        )
    db.add(
        FaceScan(
            node_id=copy,
            owner_id=owner,
            version=FACES_VERSION,
            state="done",
            faces=len(faces),
            tries=0,
            code=None,
            prio=src.prio or "",
            queued_at=at,
            updated_at=at,
        )
    )
    await db.flush()


async def on_start(settings: Settings) -> None:
    """search.hooks.register_backfill: bij de start. busy terug naar wait (een crash), een
    halve wisronde verder, en een nieuwe versie nalopen."""
    state.configure(settings)
    async with scan._maker(settings)() as db:
        await face_rows.reset_busy(db)
        if state.running(settings):
            if await face_rows.old_version(db, FACES_VERSION):
                await scan.queue_plan(db, "version")
            if await face_rows.has_waiting(db):
                await scan.queue_scan(db)
        await db.commit()
    await wipe.resume(settings)


async def hourly(settings: Settings) -> None:
    """Elk uur: de sweep (alleen als gezichten draaien) en de wisrondes die nog open zijn."""
    state.configure(settings)
    if state.running(settings):
        async with scan._maker(settings)() as db:
            await scan.queue_plan(db, "sweep")
            await db.commit()
    await wipe.resume(settings)


def guest_check(user: User) -> None:
    if user.role == "guest":
        raise errors.fail(403, "guest", "Gasten hebben geen eigen foto's")


def register() -> None:
    jobs.register(scan.PLAN, scan.plan_job, lane="default", timeout=600.0)
    jobs.register(scan.SCAN, scan.scan_job, lane="analyse", timeout=600.0)
    jobs.register(group.GROUP, group.group_job, lane="analyse", timeout=600.0)
    jobs.register(wipe.WIPE, wipe.wipe_job, lane="default", timeout=900.0)
    jobs.housekeeping(HOURLY, hourly, jobs.HOUSEKEEPING)
    prefs.register_switch("faces", prefs.Switch(state=switch_state, changed=switch_changed))
    search_hooks.register_admin(
        "faces",
        search_hooks.AdminPart(block=admin_block, switch=admin_switch, rebuild=admin_rebuild),
    )
    search_hooks.register_photo_copy(on_photo_copy)
    search_hooks.register_backfill(on_start)
    previews.register_large(after_large)
    previews.register_ready(after_ready)
    people_hooks.register_person_removed(verdicts.person_removed)
    people_hooks.register_persons_merged(verdicts.persons_merged)
    people_hooks.register_person_deleted(verdicts.person_deleted)


register()
