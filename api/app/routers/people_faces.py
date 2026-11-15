"""Gezichten automatisch (bouwontwerp fase 6, X2; de API van X2, gebouwd in X2b). De vormen staan
in app/people/faces_types.py.

Deze router komt in main.py vóór routers/people.py, zodat /people/faces/... nooit bij
/people/{person_id} belandt. Alleen de eigenaar ziet gezichten; een ander die de foto mag zien
krijgt bij GET /files/{id}/faces [], anders 404. Aan- en uitzetten gaat via PATCH
/me/photo-settings (routers/photo_prefs.py, de schakelaar van app/people/faces/register.py).

**Uit is onzichtbaar.** Draaien gezichten niet op dit platform (PLATFORM_ANALYSE_ENABLED uit, of
geen PLATFORM_FACES_KEYS: state.running), dan geeft elke route hier 409 faces_unavailable, na de
inlog; er komt dan geen job en geen bestand. Een gast krijgt 403 guest. Met gezichten uit voor de
eigenaar: 409 faces_off (behalve de status, en wipe-unnamed).

GET /people/faces/suggestions heeft twee vormen: zonder person per persoon
(SuggestionsByPerson), met person de gezichten van die persoon (FacePage).
"""

import asyncio
import io
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import select

from app import access, errors, previews
from app.analyse import monitor
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node
from app.people import people as P
from app.people.faces import read, register, scan, state, verdicts, wipe
from app.people.faces_types import (
    FACES_PAGE,
    MAX_FACES,
    CropSize,
    FaceGroups,
    FaceOut,
    FacePage,
    FacesConfirmIn,
    FacesIn,
    FacesQueued,
    FacesRejectIn,
    FacesStatus,
    SuggestionsByPerson,
)
from app.routers.stubs import QueryInt
from app.search.live import own_live
from app.security import CurrentUser
from app.storage import BlobStore

router = APIRouter(prefix="/api/v1", tags=["foto's"])

Cursor = Annotated[str | None, Query(max_length=200)]
# ?size=96 komt als tekst binnen; CropSize (Literal[96, 192]) alleen neemt geen "96" (dan altijd
# 422, ook voor faceCrop van de web)
Size = Annotated[CropSize, QueryInt]
Conf = Annotated[Settings, Depends(get_settings)]

CROP_PER_S = 20  # uitsneden per seconde per gebruiker
RESCAN_PER_HOUR = 20
_crops: dict[uuid.UUID, list[float]] = {}
_rescans: dict[uuid.UUID, list[float]] = {}

_ = register  # de aanmeldingen (jobs, schakelaar, /beheer, haken) bij de import


def _limit(log: dict[uuid.UUID, list[float]], user: uuid.UUID, n: int, window: float) -> None:
    now = time.monotonic()
    recent = [t for t in log.get(user, []) if now - t < window]
    if len(recent) >= n:
        raise errors.fail(429, "rate_limited", "Even wachten")
    recent.append(now)
    log[user] = recent


def _running(settings: Settings) -> None:
    state.configure(settings)
    if not state.running(settings):
        raise errors.fail(
            409, "faces_unavailable", "Gezichtsherkenning is op deze server niet geïnstalleerd"
        )


async def _owner(session, user, settings: Settings, *, need_on: bool = True) -> None:
    _running(settings)
    P.owner_only(user)
    if need_on and not await state.owner_on(session, user.id):
        raise errors.fail(409, "faces_off", "Gezichten staan uit")


def _too_many(ids: list[uuid.UUID]) -> None:
    if len(ids) > MAX_FACES:
        raise errors.fail(422, "faces_too_many", "Te veel gezichten in één keer")


@router.get("/people/faces/status")
async def faces_status(user: CurrentUser, session: Session, settings: Conf) -> FacesStatus:
    await _owner(session, user, settings, need_on=False)
    on = await state.owner_on(session, user.id)
    photos, faces, wiping = await read.counts(session, user.id)
    return FacesStatus(
        setting="aan" if on else "uit",
        forbidden=not await state.allowed(session),
        analyse=monitor.status().state,
        photos=photos,
        faces=faces,
        wiping=wiping,
    )


@router.get("/people/faces/suggestions")
async def face_suggestions(
    user: CurrentUser,
    session: Session,
    settings: Conf,
    person: uuid.UUID | None = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=FACES_PAGE)] = FACES_PAGE,
) -> SuggestionsByPerson | FacePage:
    await _owner(session, user, settings)
    if person is None:
        return await read.suggestions(session, user.id, cursor)
    await P.own_person(session, user, person)
    return await read.person_suggestions(session, user.id, person, cursor, limit)


@router.get("/people/faces/groups")
async def face_groups(
    user: CurrentUser, session: Session, settings: Conf, cursor: Cursor = None
) -> FaceGroups:
    await _owner(session, user, settings)
    return await read.groups(session, user.id, cursor)


@router.get("/people/faces/groups/{key}")
async def face_group(
    key: Annotated[str, Path(max_length=100)],
    user: CurrentUser,
    session: Session,
    settings: Conf,
    cursor: Cursor = None,
) -> FacePage:
    """409 faces_gone na een nieuwe ronde."""
    await _owner(session, user, settings)
    try:
        run_s, cluster_s = key.split(":", 1)
        run, cluster = int(run_s), int(cluster_s)
    except ValueError:
        raise verdicts.gone() from None
    if run != await read.run_of(session, user.id):
        raise verdicts.gone()
    return await read.group(session, user.id, run, cluster, cursor)


@router.post("/people/faces/confirm", status_code=204)
async def confirm_faces(
    body: FacesConfirmIn, user: CurrentUser, session: Session, settings: Conf
) -> None:
    """422 faces_too_many, 409 faces_gone."""
    _too_many(body.faces)
    await _owner(session, user, settings)
    async with P.lock(user.id):
        await verdicts.confirm(session, user, body.faces, body.person_id, body.name)
        await session.commit()
    state.changed(user.id)


@router.post("/people/faces/reject", status_code=204)
async def reject_faces(
    body: FacesRejectIn, user: CurrentUser, session: Session, settings: Conf
) -> None:
    _too_many(body.faces)
    await _owner(session, user, settings)
    async with P.lock(user.id):
        await verdicts.reject(session, user, body.faces, body.person_id)
        await session.commit()
    state.changed(user.id)


@router.post("/people/faces/hide", status_code=204)
async def hide_faces(body: FacesIn, user: CurrentUser, session: Session, settings: Conf) -> None:
    _too_many(body.faces)
    await _owner(session, user, settings)
    async with P.lock(user.id):
        await verdicts.hide(session, user, body.faces)
        await session.commit()
    state.changed(user.id)


@router.post("/people/faces/reset", status_code=204)
async def reset_faces(body: FacesIn, user: CurrentUser, session: Session, settings: Conf) -> None:
    """Ongedaan maken, hooguit 10 minuten na de keuze."""
    _too_many(body.faces)
    await _owner(session, user, settings)
    async with P.lock(user.id):
        await verdicts.reset(session, user, body.faces)
        await session.commit()
    state.changed(user.id)


@router.post("/people/faces/wipe-unnamed", status_code=202)
async def wipe_unnamed_faces(user: CurrentUser, session: Session, settings: Conf) -> FacesQueued:
    """faces.wipe met scope unnamed."""
    await _owner(session, user, settings, need_on=False)
    async with P.lock(user.id):
        _photos, faces, _wiping = await read.counts(session, user.id)
        await wipe.queue_wipe(session, user.id, "unnamed")
        await session.commit()
    return FacesQueued(queued=faces.open)


def _crop(path: str, box: tuple[float, float, float, float], size: int) -> bytes:
    """De uitsnede als WebP, in het geheugen (niets op schijf)."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 4_000_000
    with Image.open(path, formats=["WEBP"]) as img:
        w, h = img.size
        x, y, bw, bh = box
        side = max(bw * w, bh * h) * 1.3
        cx, cy = (x + bw / 2) * w, (y + bh / 2) * h
        area = (
            max(0, int(cx - side / 2)),
            max(0, int(cy - side / 2)),
            min(w, int(cx + side / 2)),
            min(h, int(cy + side / 2)),
        )
        part = img.convert("RGB").crop(area).resize((size, size))
        out = io.BytesIO()
        part.save(out, "WEBP", quality=80)
        return out.getvalue()


@router.get("/people/faces/{face_id}/crop", response_class=Response)
async def face_crop(
    face_id: uuid.UUID, user: CurrentUser, session: Session, settings: Conf, size: Size = 96
) -> Response:
    """WebP, Cache-Control: no-store; niets op schijf. Met de inlog als Bearer-header, zoals elke
    route hier: de web haalt hem met een fetch (faceCrop), niet als adres in een <img>; geen
    token of handtekening in de query (review Z0b, veilig V2 en contract F2)."""
    await _owner(session, user, settings, need_on=False)
    _limit(_crops, user.id, CROP_PER_S, 1.0)
    found = await read.crop_source(session, user.id, face_id)
    if found is None or found.blob_id is None:
        raise P.not_found()
    path = previews.directory(BlobStore(settings.blob_dir), found.blob_id) / scan.LARGE_NAME
    try:
        data = await asyncio.to_thread(_crop, str(path), (found.x, found.y, found.w, found.h), size)
    except (OSError, ValueError):
        raise P.not_found() from None
    return Response(data, media_type="image/webp", headers={"Cache-Control": "no-store"})


@router.get("/files/{node_id}/faces")
async def file_faces(
    node_id: uuid.UUID, user: CurrentUser, session: Session, settings: Conf
) -> list[FaceOut]:
    _running(settings)
    mine = await session.scalar(select(Node.id).where(Node.id == node_id, own_live(Node, user.id)))
    if mine is None:
        grants = await access.resolve(session, user.id, [node_id])
        if node_id in grants:
            return []
        raise P.not_found()
    if user.role == "guest" or not await state.owner_on(session, user.id):
        return []
    async with P.lock(user.id):
        await scan.to_front(session, user.id, node_id)
        await session.commit()
    return await read.file_faces(session, user.id, node_id)


@router.post("/files/{node_id}/faces/rescan", status_code=202)
async def rescan_faces(
    node_id: uuid.UUID, user: CurrentUser, session: Session, settings: Conf
) -> None:
    """Opnieuw zoeken, vooraan in de rij; hooguit 20 per uur."""
    await _owner(session, user, settings)
    _limit(_rescans, user.id, RESCAN_PER_HOUR, 3600.0)
    async with P.lock(user.id):
        if not await scan.rescan(session, user.id, node_id):
            raise P.not_found()
        await session.commit()
    state.changed(user.id)
