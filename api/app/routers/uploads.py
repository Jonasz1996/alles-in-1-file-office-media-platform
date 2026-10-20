"""Hervatbare uploads volgens het tus-protocol 1.0.0 (https://tus.io/protocols/resumable-upload),
zelf gebouwd met de extensies creation, termination en expiration.

1. POST /api/v1/tus met Upload-Length en Upload-Metadata (filename, parent, conflict)
   maakt een upload aan; het antwoord bevat de Location.
2. PATCH <location> met Upload-Offset stuurt een stuk; valt de verbinding weg, dan
   blijft wat binnen is bewaard.
3. HEAD <location> vertelt hoeveel er binnen is; daar gaat de client verder.
Zodra alle bytes binnen zijn, wordt het bestand in de map gezet (met ontdubbeling).
Is dat toen misgegaan, dan gebeurt het alsnog bij de volgende HEAD.
"""

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import ClientDisconnect

from app.config import Settings, get_settings
from app.db import Session, get_engine
from app.models import Node, Upload, User
from app.routers.files import (
    NameTaken,
    Store,
    clean_name,
    get_dir,
    is_trashed,
    place_file,
    room_left,
    sibling,
)
from app.security import CurrentUser
from app.storage import CHUNK_TIMEOUT, BlobStore, hash_file, with_timeout

router = APIRouter(prefix="/api/v1/tus", tags=["bestanden"])
log = logging.getLogger("platform.uploads")

TUS_VERSION = "1.0.0"
CONFLICTS = {"rename", "replace", "fail"}
BUSY = {"message": "Er loopt al een verzoek voor deze upload", "code": "upload_busy"}
# Een bestand in uploads/ zonder upload-rij (bijv. weg met zijn map) mag na zo lang weg;
# een upload die net is aangemaakt wordt zo nooit geraakt.
STRAY_AFTER = timedelta(hours=1)
# Zo lang (seconden) wacht het opruimen op een lopend verzoek voor één upload. Een PATCH kan
# uren duren (een groot bestand in één keer, of een client die heel traag stuurt); dan komt
# die upload de volgende ronde aan de beurt, en wacht de rest niet.
CLEANUP_WAIT = 2 * CHUNK_TIMEOUT

# Per upload één verzoek tegelijk (PATCH, afronden, DELETE, opruimen), en de SHA-256 tot nu
# toe, zodat het bestand aan het eind niet opnieuw gelezen hoeft te worden. Na een herstart
# wordt er gewoon opnieuw gehasht. Wie het slot krijgt, leest de upload opnieuw: hij kan
# intussen af of weg zijn. Een slot verdwijnt pas met de rij.
_locks: dict[uuid.UUID, asyncio.Lock] = {}
_hashers: dict[uuid.UUID, tuple["hashlib._Hash", int]] = {}
# Per gebruiker: quotum controleren en ruimte reserveren in één keer
_user_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _lock(upload_id: uuid.UUID) -> asyncio.Lock:
    return _locks.setdefault(upload_id, asyncio.Lock())


def _forget(upload_id: uuid.UUID) -> None:
    _locks.pop(upload_id, None)
    _hashers.pop(upload_id, None)


def tus_version(tus_resumable: Annotated[str | None, Header()] = None) -> None:
    if tus_resumable != TUS_VERSION:
        raise HTTPException(
            412, "Alleen tus 1.0.0 wordt ondersteund", headers={"Tus-Version": TUS_VERSION}
        )


Tus = Annotated[None, Depends(tus_version)]


def upload_path(settings: Settings, upload_id: uuid.UUID) -> Path:
    return settings.upload_dir / upload_id.hex


def _expires(upload: Upload) -> datetime:
    return upload.expires_at.replace(tzinfo=upload.expires_at.tzinfo or UTC)


def _headers(upload: Upload | None = None, **extra: str) -> dict[str, str]:
    headers = {"Tus-Resumable": TUS_VERSION, "Cache-Control": "no-store", **extra}
    if upload is not None:
        headers["Upload-Expires"] = format_datetime(_expires(upload), usegmt=True)
    return headers


def parse_metadata(header: str | None) -> dict[str, str]:
    """'filename d29ybGQ=,parent YWJj' -> {'filename': 'world', 'parent': 'abc'}"""
    result: dict[str, str] = {}
    for pair in (header or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, _, value = pair.partition(" ")
        try:
            result[key] = base64.b64decode(value.strip(), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError) as e:
            raise HTTPException(400, f"Ongeldige Upload-Metadata voor '{key}'") from e
    return result


async def get_upload(session: AsyncSession, user: User, upload_id: uuid.UUID) -> Upload:
    # Altijd vers uit de database: na het wachten op een slot kan hij af of weg zijn
    upload = await session.get(Upload, upload_id, populate_existing=True)
    if upload is None or upload.owner_id != user.id:
        raise HTTPException(404, "Upload niet gevonden", headers=_headers())
    return upload


async def _drop(session: AsyncSession, settings: Settings, upload_id: uuid.UUID) -> None:
    """Weg met de upload: de rij (als die er nog is), de bytes in uploads/ en wat we onthielden."""
    await session.execute(delete(Upload).where(Upload.id == upload_id))
    await session.commit()
    await anyio.Path(upload_path(settings, upload_id)).unlink(missing_ok=True)
    _forget(upload_id)


def _offset(settings: Settings, upload: Upload) -> int:
    """Wat er binnen is, is wat er op schijf staat; een afgeronde upload is compleet."""
    if upload.node_id is not None:
        return upload.size
    try:
        return upload_path(settings, upload.id).stat().st_size
    except FileNotFoundError as e:
        raise HTTPException(404, "Upload niet gevonden", headers=_headers()) from e


@router.options("")
async def options(settings: Annotated[Settings, Depends(get_settings)]) -> Response:
    return Response(
        status_code=204,
        headers={
            "Tus-Resumable": TUS_VERSION,
            "Tus-Version": TUS_VERSION,
            "Tus-Extension": "creation,termination,expiration",
            "Tus-Max-Size": str(settings.max_upload_bytes),
        },
    )


@router.post("", status_code=201)
async def create(
    _: Tus,
    user: CurrentUser,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
    upload_length: Annotated[int, Header()],
    upload_metadata: Annotated[str | None, Header()] = None,
) -> Response:
    meta = parse_metadata(upload_metadata)
    name = clean_name(meta.get("filename", ""))
    try:
        parent_id = uuid.UUID(meta.get("parent", ""))
    except ValueError as e:
        raise HTTPException(400, "Upload-Metadata mist een geldige 'parent'") from e
    parent = await get_dir(session, user, parent_id)
    conflict = meta.get("conflict", "rename")
    if conflict not in CONFLICTS:
        raise HTTPException(400, "conflict moet rename, replace of fail zijn")
    if upload_length < 0:
        raise HTTPException(400, "Upload-Length mag niet negatief zijn")
    if settings.max_upload_bytes and upload_length > settings.max_upload_bytes:
        raise HTTPException(413, "Bestand is te groot")
    if conflict == "fail" and await sibling(session, parent.id, name):
        raise NameTaken(_headers())  # nu al, niet pas als alle bytes binnen zijn

    # Controleren en reserveren in één keer; anders komen twee gelijktijdige uploads allebei
    # door en blijkt pas als alle bytes binnen zijn dat er geen ruimte was
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _user_locks.setdefault(user.id, asyncio.Lock()):
        room = await room_left(session, user)
        if room is not None and upload_length > room:
            raise HTTPException(413, "Onvoldoende quotum")
        upload = Upload(
            owner_id=user.id,
            parent_id=parent.id,
            name=name,
            conflict=conflict,
            size=upload_length,
            expires_at=datetime.now(UTC) + timedelta(hours=settings.upload_expiry_hours),
        )
        session.add(upload)
        await session.commit()
    path = upload_path(settings, upload.id)
    await anyio.Path(path.parent).mkdir(parents=True, exist_ok=True)
    await anyio.Path(path).touch()
    headers = _headers(upload, Location=f"/api/v1/tus/{upload.id}")
    if upload_length == 0:
        node = await finish(session, settings, blobs, user, upload)
        headers["Platform-Node-Id"] = str(node.id)
    return Response(status_code=201, headers=headers)


@router.head("/{upload_id}")
async def head(
    upload_id: uuid.UUID,
    _: Tus,
    user: CurrentUser,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    upload = await get_upload(session, user, upload_id)
    lock = _lock(upload.id)
    if lock.locked() and upload.node_id is None and not upload_path(settings, upload.id).exists():
        # Wie het slot heeft, verplaatst het bestand net (afronden of afbreken): wacht op de
        # uitkomst, anders lijkt de upload ten onrechte verlopen
        await session.commit()
        async with lock:
            upload = await get_upload(session, user, upload_id)
    offset = _offset(settings, upload)
    if upload.node_id is None and offset == upload.size and not lock.locked():
        # Alle bytes binnen maar nog geen bestand: het afronden mislukte of de server stopte
        # halverwege. Een gewone tus-client vraagt dan niets meer, dus nu afronden.
        async with lock:
            upload = await get_upload(session, user, upload_id)
            if upload.node_id is None and _offset(settings, upload) == upload.size:
                await finish(session, settings, blobs, user, upload)
    headers = _headers(upload, **{"Upload-Offset": str(offset), "Upload-Length": str(upload.size)})
    if upload.node_id is not None:
        headers["Platform-Node-Id"] = str(upload.node_id)
    return Response(status_code=200, headers=headers)


@router.patch("/{upload_id}")
async def patch(
    upload_id: uuid.UUID,
    request: Request,
    _: Tus,
    user: CurrentUser,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
    upload_offset: Annotated[int, Header()],
    content_type: Annotated[str | None, Header()] = None,
) -> Response:
    if (content_type or "").split(";")[0].strip() != "application/offset+octet-stream":
        raise HTTPException(415, "Content-Type moet application/offset+octet-stream zijn")
    upload = await get_upload(session, user, upload_id)
    lock = _lock(upload.id)
    if lock.locked():
        raise HTTPException(409, BUSY, headers=_headers())
    async with lock:
        upload = await get_upload(session, user, upload_id)
        offset = _offset(settings, upload)
        if upload.node_id is not None and upload_offset == offset:
            # Al klaar; de client miste waarschijnlijk het vorige antwoord
            return Response(
                status_code=204,
                headers=_headers(
                    upload,
                    **{"Upload-Offset": str(offset), "Platform-Node-Id": str(upload.node_id)},
                ),
            )
        if upload_offset != offset:
            raise HTTPException(
                409,
                "Upload-Offset klopt niet",
                headers=_headers(upload, **{"Upload-Offset": str(offset)}),
            )
        remaining = upload.size - offset
        hasher, hashed = _hashers.get(upload.id, (None, -1))
        if hashed != offset:
            hasher = hashlib.sha256() if offset == 0 else None
        # De bytes kunnen lang onderweg zijn: geen databaseverbinding vasthouden
        await session.commit()
        received, too_much, gone = 0, False, False
        try:
            # r+b en niet ab: een bestand dat er niet meer is, niet stilletjes opnieuw maken
            async with await anyio.open_file(upload_path(settings, upload.id), "r+b") as f:
                await f.seek(0, os.SEEK_END)
                async for chunk in with_timeout(request.stream()):
                    if received + len(chunk) > remaining:
                        too_much = True
                        break
                    await f.write(chunk)
                    received += len(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
        except (ClientDisconnect, TimeoutError):
            # Verbinding weg of stilgevallen: wat binnen is blijft staan, het slot komt vrij
            # en de client gaat later verder
            gone = True
        offset += received
        if hasher is not None:
            _hashers[upload.id] = (hasher, offset)
        expires = datetime.now(UTC) + timedelta(hours=settings.upload_expiry_hours)
        result = await session.execute(
            update(Upload).where(Upload.id == upload.id).values(expires_at=expires)
        )
        await session.commit()
        if result.rowcount == 0:
            # Intussen weg: de map is definitief verwijderd en de upload met hem
            await _drop(session, settings, upload.id)
            raise HTTPException(404, "Upload niet gevonden", headers=_headers())
        if too_much:
            raise HTTPException(
                413,
                "Meer bytes dan Upload-Length",
                headers=_headers(upload, **{"Upload-Offset": str(offset)}),
            )
        headers = _headers(upload, **{"Upload-Offset": str(offset)})
        if offset == upload.size and not gone:
            node = await finish(session, settings, blobs, user, upload)
            headers["Platform-Node-Id"] = str(node.id)
        return Response(status_code=204, headers=headers)


@router.delete("/{upload_id}", status_code=204)
async def terminate(
    upload_id: uuid.UUID,
    _: Tus,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    upload = await get_upload(session, user, upload_id)
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    # Een lopende PATCH of afronding gaat eerst door (een stilgevallen PATCH stopt vanzelf)
    async with _lock(upload.id):
        # Een afgeronde upload verwijderen laat het bestand zelf staan
        await _drop(session, settings, upload.id)
    return Response(status_code=204, headers=_headers())


async def finish(
    session: AsyncSession, settings: Settings, blobs: BlobStore, user: User, upload: Upload
) -> Node:
    """Alle bytes zijn binnen: inhoud naar de blob-opslag en het bestand in de map. Draait
    onder het slot van de upload (of voor een lege upload die nog niemand kent). Mislukt het,
    dan blijft de upload zoals hij was en kan het opnieuw, behalve als dat geen zin heeft
    (quotum op, naam bezet, upload weg)."""
    upload_id, path = upload.id, upload_path(settings, upload.id)
    await session.commit()  # geen databaseverbinding vasthouden tijdens het hashen
    hasher, hashed = _hashers.get(upload_id, (None, -1))
    if hasher is not None and hashed == upload.size:
        sha = hasher.hexdigest()
    else:
        sha = await anyio.to_thread.run_sync(hash_file, path)

    room = await room_left(session, user, exclude_upload=upload_id)
    if room is not None and upload.size > room:
        await _drop(session, settings, upload_id)
        raise HTTPException(413, "Onvoldoende quotum", headers=_headers())
    conflict = upload.conflict
    parent = await session.get(Node, upload.parent_id)
    if parent is None or parent.deleted_at is not None or await is_trashed(session, parent):
        # De map is weg: dan in de hoofdmap, en daar nooit iets vervangen of weigeren
        # (replace en fail golden voor de map die de client koos)
        parent = await session.get(Node, user.root_id)
        conflict = "rename"
    try:
        node = await place_file(
            session, blobs, user, parent, upload.name, conflict, sha, upload.size, path, upload
        )
    except NameTaken as e:
        # Er kwam intussen iets met deze naam: opnieuw proberen heeft geen zin
        await _drop(session, settings, upload_id)
        raise NameTaken(_headers()) from e
    except Exception:
        if await session.scalar(select(Upload.id).where(Upload.id == upload_id)) is None:
            # Intussen weg: de map is definitief verwijderd en de upload met hem
            await _drop(session, settings, upload_id)
            raise HTTPException(404, "Upload niet gevonden", headers=_headers()) from None
        raise
    _hashers.pop(upload_id, None)
    return node


async def remove_expired(settings: Settings) -> int:
    """Ruimt verlopen uploads op, en bestanden in uploads/ waar geen upload meer bij hoort."""
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    removed = 0
    async with maker() as session:
        now = datetime.now(UTC)
        uploads = (await session.scalars(select(Upload))).all()
        expired = [u.id for u in uploads if _expires(u) < now]
        await session.commit()  # niet met een databaseverbinding op een slot wachten
        for upload_id in expired:
            # Wacht op een lopend verzoek (een PATCH verlengt de upload misschien nog), maar
            # niet eindeloos. Overslaan kan: het slot blijft bestaan zolang de rij er is.
            lock, acquired = _lock(upload_id), False
            with anyio.move_on_after(CLEANUP_WAIT):
                await lock.acquire()
                acquired = True
            if not acquired:
                continue
            try:
                upload = await session.get(Upload, upload_id, populate_existing=True)
                if upload is not None and _expires(upload) < now:
                    await _drop(session, settings, upload_id)
                    removed += 1
                else:
                    await session.commit()
            finally:
                lock.release()
        known = {u.hex for u in (await session.scalars(select(Upload.id))).all()}
    if settings.upload_dir.exists():
        cutoff = now - STRAY_AFTER
        for path in settings.upload_dir.iterdir():
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            except FileNotFoundError:
                continue
            if path.name not in known and mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
    # Wat we onthielden van uploads die er niet meer zijn (bijv. weg met hun map)
    for upload_id in set(_locks) | set(_hashers):
        lock = _locks.get(upload_id)
        if upload_id.hex not in known and not (lock and lock.locked()):
            _forget(upload_id)
    return removed


async def cleanup_loop(settings: Settings) -> None:
    while True:
        try:
            if removed := await remove_expired(settings):
                log.info("%s verlopen uploads opgeruimd", removed)
        except Exception:  # noqa: BLE001 - opruimen mag de app nooit stoppen
            log.exception("Opruimen van uploads mislukt")
        await asyncio.sleep(3600)
