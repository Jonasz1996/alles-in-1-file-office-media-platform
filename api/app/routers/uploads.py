"""Hervatbare uploads volgens het tus-protocol 1.0.0 (https://tus.io/protocols/resumable-upload),
zelf gebouwd met de extensies creation, termination en expiration.

1. POST /api/v1/tus met Upload-Length en Upload-Metadata (filename, parent, conflict)
   maakt een upload aan; het antwoord bevat de Location.
2. PATCH <location> met Upload-Offset stuurt een stuk; valt de verbinding weg, dan
   blijft wat binnen is bewaard.
3. HEAD <location> vertelt hoeveel er binnen is; daar gaat de client verder.
Zodra alle bytes binnen zijn, wordt het bestand in de map gezet (met ontdubbeling).
"""

import asyncio
import base64
import binascii
import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import ClientDisconnect

from app.config import Settings, get_settings
from app.db import Session, get_engine
from app.models import Node, Upload, User
from app.routers.files import Store, clean_name, get_dir, is_trashed, place_file, room_left
from app.security import CurrentUser
from app.storage import BlobStore, hash_file

router = APIRouter(prefix="/api/v1/tus", tags=["bestanden"])
log = logging.getLogger("platform.uploads")

TUS_VERSION = "1.0.0"
CONFLICTS = {"rename", "replace", "fail"}

# Per upload één PATCH tegelijk, en de SHA-256 tot nu toe, zodat het bestand aan het
# eind niet opnieuw gelezen hoeft te worden. Na een herstart wordt er gewoon opnieuw
# gehasht.
_locks: dict[uuid.UUID, asyncio.Lock] = {}
_hashers: dict[uuid.UUID, tuple["hashlib._Hash", int]] = {}


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


def _headers(upload: Upload | None = None, **extra: str) -> dict[str, str]:
    headers = {"Tus-Resumable": TUS_VERSION, "Cache-Control": "no-store", **extra}
    if upload is not None:
        expires = upload.expires_at.replace(tzinfo=upload.expires_at.tzinfo or UTC)
        headers["Upload-Expires"] = format_datetime(expires, usegmt=True)
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
    upload = await session.get(Upload, upload_id)
    if upload is None or upload.owner_id != user.id:
        raise HTTPException(404, "Upload niet gevonden", headers=_headers())
    return upload


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
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    upload = await get_upload(session, user, upload_id)
    headers = _headers(
        upload,
        **{"Upload-Offset": str(_offset(settings, upload)), "Upload-Length": str(upload.size)},
    )
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
    lock = _locks.setdefault(upload.id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "Er loopt al een verzoek voor deze upload", headers=_headers())
    async with lock:
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
        received, too_much, gone = 0, False, False
        try:
            async with await anyio.open_file(upload_path(settings, upload.id), "ab") as f:
                async for chunk in request.stream():
                    if received + len(chunk) > remaining:
                        too_much = True
                        break
                    await f.write(chunk)
                    received += len(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
        except ClientDisconnect:
            gone = True  # wat binnen is blijft staan; de client gaat later verder
        offset += received
        if hasher is not None:
            _hashers[upload.id] = (hasher, offset)
        upload.expires_at = datetime.now(UTC) + timedelta(hours=settings.upload_expiry_hours)
        await session.commit()
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
    lock = _locks.setdefault(upload.id, asyncio.Lock())
    async with lock:
        # Een afgeronde upload verwijderen laat het bestand zelf staan
        await session.delete(upload)
        await session.commit()
        await anyio.Path(upload_path(settings, upload_id)).unlink(missing_ok=True)
    _forget(upload_id)
    return Response(status_code=204, headers=_headers())


async def finish(
    session: AsyncSession, settings: Settings, blobs: BlobStore, user: User, upload: Upload
) -> Node:
    """Alle bytes zijn binnen: inhoud naar de blob-opslag en het bestand in de map."""
    path = upload_path(settings, upload.id)
    hasher, hashed = _hashers.get(upload.id, (None, -1))
    if hasher is not None and hashed == upload.size:
        sha = hasher.hexdigest()
    else:
        sha = await anyio.to_thread.run_sync(hash_file, path)
    _forget(upload.id)

    room = await room_left(session, user, exclude_upload=upload.id)
    parent = await session.get(Node, upload.parent_id)
    if parent is None or parent.deleted_at is not None or await is_trashed(session, parent):
        parent = await session.get(Node, user.root_id)  # map is weg: dan in de hoofdmap
    if room is not None and upload.size > room:
        await session.delete(upload)
        await session.commit()
        await anyio.Path(path).unlink(missing_ok=True)
        raise HTTPException(413, "Onvoldoende quotum", headers=_headers())
    await blobs.adopt(path, sha)
    return await place_file(
        session, blobs, user, parent, upload.name, upload.conflict, sha, upload.size, upload
    )


async def remove_expired(settings: Settings) -> int:
    """Ruimt verlopen uploads op, en bestanden in uploads/ waar geen upload meer bij hoort."""
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    removed = 0
    async with maker() as session:
        now = datetime.now(UTC)
        for upload in (await session.scalars(select(Upload))).all():
            expires = upload.expires_at.replace(tzinfo=upload.expires_at.tzinfo or UTC)
            lock = _locks.get(upload.id)
            if expires < now and not (lock and lock.locked()):
                await session.delete(upload)
                await anyio.Path(upload_path(settings, upload.id)).unlink(missing_ok=True)
                _forget(upload.id)
                removed += 1
        await session.commit()
        known = {u.hex for u in (await session.scalars(select(Upload.id))).all()}
    if settings.upload_dir.exists():
        cutoff = now - timedelta(hours=settings.upload_expiry_hours)
        for path in settings.upload_dir.iterdir():
            mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if path.name not in known and mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


async def cleanup_loop(settings: Settings) -> None:
    while True:
        try:
            if removed := await remove_expired(settings):
                log.info("%s verlopen uploads opgeruimd", removed)
        except Exception:  # noqa: BLE001 - opruimen mag de app nooit stoppen
            log.exception("Opruimen van uploads mislukt")
        await asyncio.sleep(3600)
