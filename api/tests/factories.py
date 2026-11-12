"""Hulpjes voor tests.

Foutantwoorden ({"detail": {"message", "code", "params"?}}, zie app/errors.py):
- message_of(r), code_of(r), params_of(r), detail_of(r): de tekst, de code, de params en de
  hele detail van een antwoord. Een oud antwoord met alleen een tekst als detail werkt ook.

Rijen rechtstreeks in de database, zonder de API (voor veel data in één keer, of voor iets
wat de API nog niet kan maken, zoals een album vóór P5). Alles werkt in de transactie van de
aanroeper: niets commit zelf, dus `await session.commit()` na afloop.
- maker(settings): een async_sessionmaker op de database van de test;
- create_user(session, email, ...): een gebruiker met zijn hoofdmap (root_id gezet), met
  wachtwoord PASSWORD; disabled=True zet disabled_at;
- create_folder(session, owner, parent_id=None, name=...): een map (standaard in de hoofdmap
  van owner); deleted=True legt hem in de prullenbak;
- create_blob(session, data, mime=..., blobs=None): de blob-rij voor deze inhoud (of de
  bestaande); met blobs (een BlobStore) staat de inhoud ook op schijf. ref_count verandert
  niet: dat doet create_file;
- create_file(session, owner, parent_id=None, name=..., data=..., mime=..., ...): een bestand
  met blob (ref_count + 1); taken_at gezet: ook taken_day, zoals photodate.stamp dat doet;
- create_album(session, owner, name=..., items=(), description=...): een node met kind
  "album" (parent_id NULL, zoals in app/models.py) plus zijn album-rij en album_item's;
- user_by_email(session, email): de gebruiker met dit adres (of None).
"""

import hashlib
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import get_engine
from app.models import Album, AlbumItem, Blob, Node, User
from app.photodate import day_of
from app.security import hash_password
from app.storage import BlobStore

PASSWORD = "lang-genoeg-123"
_hashes: dict[str, str] = {}


def detail_of(r) -> Any:
    return r.json().get("detail")


def message_of(r) -> str:
    detail = detail_of(r)
    return detail.get("message", "") if isinstance(detail, dict) else detail


def code_of(r) -> str | None:
    detail = detail_of(r)
    return detail.get("code") if isinstance(detail, dict) else None


def params_of(r) -> dict:
    detail = detail_of(r)
    return (detail.get("params") or {}) if isinstance(detail, dict) else {}


def maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def _hash(password: str) -> str:
    # argon2 kost ~100 ms: per wachtwoord één keer
    if password not in _hashes:
        _hashes[password] = hash_password(password)
    return _hashes[password]


async def create_user(
    session: AsyncSession,
    email: str,
    *,
    display_name: str | None = None,
    password: str = PASSWORD,
    role: str = "user",
    quota_bytes: int = 0,
    disabled: bool = False,
) -> User:
    user = User(
        email=email.lower(),
        display_name=display_name or email.split("@")[0].capitalize(),
        password_hash=_hash(password),
        role=role,
        quota_bytes=quota_bytes,
        disabled_at=datetime.now(UTC) if disabled else None,
    )
    session.add(user)
    await session.flush()
    root = Node(owner_id=user.id, name="", kind="dir")
    session.add(root)
    await session.flush()
    user.root_id = root.id
    await session.flush()
    return user


async def create_folder(
    session: AsyncSession,
    owner: User,
    parent_id: uuid.UUID | None = None,
    name: str = "Map",
    *,
    deleted: bool = False,
) -> Node:
    folder = Node(
        parent_id=parent_id or owner.root_id,
        owner_id=owner.id,
        name=name,
        kind="dir",
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    session.add(folder)
    await session.flush()
    return folder


async def create_blob(
    session: AsyncSession,
    data: bytes = b"inhoud",
    *,
    mime: str | None = "text/plain",
    blobs: BlobStore | None = None,
    taken_at: datetime | None = None,
) -> Blob:
    sha = hashlib.sha256(data).hexdigest()
    blob = await session.get(Blob, sha)
    if blob is None:
        blob = Blob(sha256=sha, size=len(data), ref_count=0, mime=mime, taken_at=taken_at)
        session.add(blob)
        await session.flush()
    if blobs is not None:
        path = blobs.path(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return blob


async def create_file(
    session: AsyncSession,
    owner: User,
    parent_id: uuid.UUID | None = None,
    name: str = "bestand.txt",
    *,
    data: bytes = b"inhoud",
    mime: str | None = "text/plain",
    blobs: BlobStore | None = None,
    taken_at: datetime | None = None,
    deleted: bool = False,
) -> Node:
    blob = await create_blob(session, data, mime=mime, blobs=blobs)
    blob.ref_count += 1
    node = Node(
        parent_id=parent_id or owner.root_id,
        owner_id=owner.id,
        name=name,
        kind="file",
        mime=mime,
        size=len(data),
        blob_id=blob.sha256,
        taken_at=taken_at,
        taken_day=day_of(taken_at) if taken_at is not None else None,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    session.add(node)
    await session.flush()
    return node


async def create_album(
    session: AsyncSession,
    owner: User,
    name: str = "Album",
    *,
    items: Iterable[Node] = (),
    description: str = "",
) -> Node:
    album = Node(parent_id=None, owner_id=owner.id, name=name, kind="album")
    session.add(album)
    await session.flush()
    session.add(Album(id=album.id, description=description))
    for item in items:
        session.add(AlbumItem(album_id=album.id, node_id=item.id, added_by=owner.id))
    await session.flush()
    return album


async def user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email.lower()))
