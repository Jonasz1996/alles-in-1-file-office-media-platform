"""Een zip van mappen en bestanden: eerst een link (POST /archive/link), dan een streamende
download (GET /archive?token=).

- De link leeft LINK_TTL seconden in het geheugen van dit proces (één node is genoeg; in het
  cluster komt hier later iets gedeelds). Het token komt in het adres (zoals een downloadlink),
  maar zegt zelf niets: zonder het geheugen van de server is het niets waard.
- Toegang in bulk, bij de link én bij het downloaden opnieuw: één query voor de nodes, één
  recursieve query omhoog voor de eigen nodes (prullenbak) en één access.resolve voor wat van
  anderen is, hoeveel ids het ook zijn. Een ingetrokken deling of een geblokkeerde gebruiker
  (security.active_user) stopt ook een link die al uit was: 404.
- De zip zelf: ZIP_STORED (foto's en video's zijn al gecomprimeerd) met ZIP64, zonder
  Content-Length of Range. Een schrijver in een thread (anyio.to_thread) zet de stukken in een
  memory object stream; valt de verbinding weg, dan stopt hij bij het volgende stuk.
- Hooguit MAX_ENTRIES mappen en bestanden; meer geeft 422 too_many vóór de eerste byte.
"""

import asyncio
import io
import secrets
import threading
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import anyio
import anyio.from_thread
import anyio.lowlevel
import anyio.to_thread
from anyio.streams.memory import MemoryObjectSendStream
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, errors
from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, User
from app.routers import files
from app.security import CurrentUser, active_user
from app.storage import BlobStore
from app.text import CONTROL

router = APIRouter(prefix="/api/v1", tags=["bestanden"])

MAX_IDS = 10_000
MAX_ENTRIES = 50_000
LINK_TTL = 600
# Zo groot is een stuk dat de schrijver doorgeeft, en zoveel stukken mogen er klaarstaan
CHUNK = 256 * 1024
BUFFERED = 8


class ArchiveRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_IDS)
    name: str = Field(default="", max_length=255)


class ArchiveLink(BaseModel):
    url: str
    expires_in: int


@dataclass
class Link:
    user_id: uuid.UUID
    ids: list[uuid.UUID]
    name: str
    expires: float


# token -> Link; verlopen links gaan eruit bij elk verzoek
_links: dict[str, Link] = {}
# De taken van lopende schrijvers (een taak zonder verwijzing kan de vuilnisman opruimen)
_tasks: set[asyncio.Task] = set()
# Hoeveel schrijvers er nu lopen (voor de tests: stopt hij als de verbinding wegvalt?)
writers = 0
_writers_lock = threading.Lock()


def _purge(now: float) -> None:
    for token in [t for t, link in _links.items() if link.expires <= now]:
        del _links[token]


def zip_name(name: str) -> str:
    """De naam van de zip zonder ".zip", zonder slashes of stuurtekens; leeg wordt "download"."""
    name = CONTROL.sub("", name).replace("/", "-").replace("\\", "-").strip()
    if name.lower().endswith(".zip"):
        name = name[:-4].rstrip()
    return name[:200] or "download"


async def checked(session: AsyncSession, user: User, ids: Sequence[uuid.UUID]) -> list[Node]:
    """De top-nodes als user ze mag lezen (zoals files.get_access(read)), in bulk: hooguit drie
    queries. 404 not_found als er één niet (meer) te zien is of in de prullenbak ligt, 422 album
    voor een album. Een node binnen een andere gevraagde map valt weg (die zit er al in)."""
    wanted = list(dict.fromkeys(ids))
    nodes = {n.id: n for n in await session.scalars(select(Node).where(Node.id.in_(wanted)))}
    missing = errors.fail(404, "not_found", "Niet gevonden")
    if len(nodes) != len(wanted):
        raise missing
    if any(n.kind == "album" for n in nodes.values()):
        raise errors.fail(422, **files.ALBUM)
    mine = [n.id for n in nodes.values() if n.owner_id == user.id]
    if mine and user.role == "guest":
        raise errors.fail(403, **files.GUEST)
    tops = set(wanted)
    inside: set[uuid.UUID] = set()
    if mine:
        up = access.ancestry(Node.id.in_(mine))
        rows = await session.execute(select(up.c.origin, up.c.id, up.c.deleted_at, up.c.depth))
        for origin, node_id, deleted_at, depth in rows:
            if deleted_at is not None:
                raise missing
            if depth > 0 and node_id in tops:
                inside.add(origin)
    others = [i for i in wanted if nodes[i].owner_id != user.id]
    if others:
        grants = await access.resolve(session, user.id, others)
        for node_id in others:
            grant = grants.get(node_id)
            if grant is None:
                raise missing
            if any(step.id in tops and step.id != node_id for step in grant.chain):
                inside.add(node_id)
    return [nodes[i] for i in wanted if i not in inside]


@router.post("/archive/link")
async def archive_link(body: ArchiveRequest, user: CurrentUser, session: Session) -> ArchiveLink:
    """Een downloadlink (LINK_TTL seconden geldig) voor een zip van deze mappen en bestanden."""
    await checked(session, user, body.ids)
    now = time.monotonic()
    _purge(now)
    token = secrets.token_urlsafe(32)
    ids = list(dict.fromkeys(body.ids))
    _links[token] = Link(user.id, ids, zip_name(body.name), now + LINK_TTL)
    return ArchiveLink(url=f"/api/v1/archive?token={token}", expires_in=LINK_TTL)


@dataclass
class Entry:
    path: str
    kind: str
    source: Path | None
    modified_at: datetime


def _numbered(path: str, taken: set[str]) -> str:
    """Een vrije naam voor een top-item als twee gekozen items dezelfde naam hebben."""
    if path.lower() not in taken:
        return path
    i = 1
    while (candidate := files.numbered(path, i)).lower() in taken:
        i += 1
    return candidate


async def entries_of(session: AsyncSession, tops: list[Node], blobs: BlobStore) -> list[Entry]:
    """Alles wat in de zip komt (mappen en bestanden, ouders voor kinderen), met het pad in de
    zip. Eén query over alle tops samen; niets uit de prullenbak. 422 too_many boven
    MAX_ENTRIES."""
    found = files.tree([t.id for t in tops], live=True)
    query = (
        select(Node.id, Node.parent_id, Node.name, Node.kind, Node.blob_id, Node.modified_at)
        .join(found, Node.id == found.c.id)
        .order_by(found.c.depth)
        .limit(MAX_ENTRIES + 1)
    )
    rows = []
    async for part in (await session.stream(query)).partitions(1000):
        rows.extend(part)
    if len(rows) > MAX_ENTRIES:
        most = f"{MAX_ENTRIES:,}".replace(",", ".")
        raise errors.fail(
            422,
            "too_many",
            f"Te veel voor één zip (meer dan {most} mappen en bestanden)",
            max=MAX_ENTRIES,
        )
    top_ids = {t.id for t in tops}
    paths: dict[uuid.UUID, str] = {}
    taken: set[str] = set()
    out: list[Entry] = []
    for row in rows:
        if row.id in paths:
            continue
        if row.id in top_ids:
            path = _numbered(row.name, taken)
            taken.add(path.lower())
        elif row.parent_id in paths:
            path = f"{paths[row.parent_id]}/{row.name}"
        else:
            continue  # hoort nergens bij (kan niet, maar dan niet in de zip)
        paths[row.id] = path
        source = blobs.path(row.blob_id) if row.kind == "file" and row.blob_id else None
        out.append(Entry(path, row.kind, source, row.modified_at))
    return out


class _Pipe(io.RawIOBase):
    """Een niet-doorzoekbaar bestand voor zipfile: wat erin gaat, gaat in stukken van CHUNK naar
    de memory object stream (vanuit de thread van de schrijver)."""

    def __init__(self, send: MemoryObjectSendStream[bytes], token) -> None:
        self.send = send
        self.token = token
        self.buffer = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self.buffer += data
        if len(self.buffer) >= CHUNK:
            self.push()
        return len(data)

    def push(self) -> None:
        if self.buffer:
            chunk, self.buffer = bytes(self.buffer), bytearray()
            # Is de ontvanger weg (verbinding verbroken), dan gooit dit en stopt de schrijver
            anyio.from_thread.run(self.send.send, chunk, token=self.token)


def _date_time(moment: datetime) -> tuple[int, int, int, int, int, int]:
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    t = moment.astimezone(UTC)
    if t.year < 1980:  # het vroegste dat zip kent
        return (1980, 1, 1, 0, 0, 0)
    return (t.year, t.month, t.day, t.hour, t.minute, t.second)


def write_zip(entries: list[Entry], pipe: _Pipe) -> None:
    """Schrijft de zip naar pipe (in een thread). Een bestand dat op schijf ontbreekt, slaat hij
    over."""
    global writers
    with _writers_lock:
        writers += 1
    try:
        with zipfile.ZipFile(pipe, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for entry in entries:
                info = zipfile.ZipInfo(
                    entry.path + ("/" if entry.kind == "dir" else ""),
                    _date_time(entry.modified_at),
                )
                if entry.kind == "dir":
                    info.external_attr = (0o40755 << 16) | 0x10
                    zf.writestr(info, b"")
                    continue
                if entry.source is None:
                    continue
                try:
                    source = open(entry.source, "rb")  # noqa: SIM115 - gesloten in de with
                except OSError:
                    continue
                info.external_attr = 0o644 << 16
                with source, zf.open(info, "w", force_zip64=True) as target:
                    while block := source.read(CHUNK):
                        target.write(block)
        pipe.push()
    finally:
        with _writers_lock:
            writers -= 1


async def _stream(entries: list[Entry]) -> AsyncIterator[bytes]:
    send, receive = anyio.create_memory_object_stream[bytes](BUFFERED)
    pipe = _Pipe(send, anyio.lowlevel.current_token())

    async def produce() -> None:
        try:
            await anyio.to_thread.run_sync(write_zip, entries, pipe)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass  # de ontvanger is weg: de verbinding viel weg
        finally:
            send.close()

    # Een eigen taak voor de schrijver: hij hoort niet bij het afbreken van deze generator (dat
    # sluit alleen de ontvanger, en daarna stopt hij zelf bij het volgende stuk)
    task = asyncio.get_running_loop().create_task(produce())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    try:
        async with receive:
            async for chunk in receive:
                yield chunk
    finally:
        receive.close()


def _disposition(name: str) -> str:
    filename = f"{name}.zip"
    ascii_name = filename.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/archive")
async def archive(
    token: str,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """De zip achter een link van POST /archive/link. Weg, verlopen, geblokkeerd of geen
    toegang meer: 404."""
    now = time.monotonic()
    _purge(now)
    link = _links.get(token)
    if link is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    user = await active_user(session, link.user_id)
    try:
        tops = await checked(session, user, link.ids)
    except Exception as e:
        # Ook een album of iets van een gast: voor de link is het gewoon niet (meer) te krijgen
        if getattr(e, "status_code", None) == 404:
            raise
        raise errors.fail(404, "not_found", "Niet gevonden") from None
    entries = await entries_of(session, tops, BlobStore(settings.blob_dir))
    await session.commit()  # geen databaseverbinding vasthouden tijdens het downloaden
    return StreamingResponse(
        _stream(entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": _disposition(link.name),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
