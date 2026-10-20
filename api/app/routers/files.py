import mimetypes
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import Session
from app.models import Blob, Node, Upload, User
from app.routers.auth import used_bytes
from app.security import CurrentUser, download_token, read_download_token
from app.storage import BlobStore, TooLarge

router = APIRouter(prefix="/api/v1", tags=["bestanden"])
FORBIDDEN = re.compile(r"[/\\\x00-\x1f]")


class NodeOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    kind: str
    mime: str | None
    size: int
    etag: str
    created_at: datetime
    modified_at: datetime
    deleted_at: datetime | None

    model_config = {"from_attributes": True}


class Listing(BaseModel):
    folder: NodeOut
    path: list[NodeOut]
    children: list[NodeOut]


class NewFolder(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class Change(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None


def store(settings: Annotated[Settings, Depends(get_settings)]) -> BlobStore:
    return BlobStore(settings.blob_dir)


Store = Annotated[BlobStore, Depends(store)]


class NameTaken(HTTPException):
    """409 met code name_taken: opnieuw proberen heeft geen zin."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        super().__init__(
            409, {"message": "Er bestaat al iets met deze naam", "code": "name_taken"}, headers
        )


def clean_name(name: str) -> str:
    name = name.strip()
    if not name or name in (".", "..") or FORBIDDEN.search(name) or len(name) > 255:
        raise HTTPException(422, "Ongeldige naam")
    return name


async def get_node(session: AsyncSession, user: User, node_id: uuid.UUID) -> Node:
    node = await session.get(Node, node_id)
    if node is None or node.owner_id != user.id:
        raise HTTPException(404, "Niet gevonden")
    return node


async def get_dir(session: AsyncSession, user: User, node_id: uuid.UUID) -> Node:
    node = await get_node(session, user, node_id)
    if node.kind != "dir" or node.deleted_at is not None:
        raise HTTPException(404, "Map niet gevonden")
    return node


async def ancestors(session: AsyncSession, node: Node) -> list[Node]:
    """Van de root tot en met node."""
    chain = [node]
    while chain[-1].parent_id is not None:
        parent = await session.get(Node, chain[-1].parent_id)
        if parent is None:
            break
        chain.append(parent)
    return list(reversed(chain))


async def is_trashed(session: AsyncSession, node: Node) -> bool:
    return any(n.deleted_at is not None for n in await ancestors(session, node))


async def sibling(session: AsyncSession, parent_id: uuid.UUID, name: str) -> Node | None:
    return await session.scalar(
        select(Node).where(
            Node.parent_id == parent_id,
            func.lower(Node.name) == name.lower(),
            Node.deleted_at.is_(None),
        )
    )


async def free_name(session: AsyncSession, parent_id: uuid.UUID, name: str) -> str:
    if await sibling(session, parent_id, name) is None:
        return name
    stem, dot, ext = name.rpartition(".")
    if not stem:
        stem, dot, ext = name, "", ""
    for i in range(1, 10_000):
        candidate = f"{stem} ({i}){dot}{ext}"
        if await sibling(session, parent_id, candidate) is None:
            return candidate
    raise HTTPException(409, "Geen vrije naam gevonden")


async def descendants(session: AsyncSession, node: Node) -> list[Node]:
    result, todo = [], [node]
    while todo:
        current = todo.pop()
        result.append(current)
        if current.kind == "dir":
            todo.extend(
                (await session.scalars(select(Node).where(Node.parent_id == current.id))).all()
            )
    return result


@router.get("/files/root")
async def root(user: CurrentUser, session: Session) -> Listing:
    return await listing(user.root_id, user, session)


@router.get("/files/{node_id}/children")
async def listing(node_id: uuid.UUID, user: CurrentUser, session: Session) -> Listing:
    folder = await get_dir(session, user, node_id)
    children = (
        await session.scalars(
            select(Node)
            .where(Node.parent_id == folder.id, Node.deleted_at.is_(None))
            .order_by(Node.kind, func.lower(Node.name))
        )
    ).all()
    return Listing(
        folder=NodeOut.model_validate(folder),
        path=[NodeOut.model_validate(n) for n in await ancestors(session, folder)],
        children=[NodeOut.model_validate(c) for c in children],
    )


@router.get("/files/{node_id}")
async def info(node_id: uuid.UUID, user: CurrentUser, session: Session) -> NodeOut:
    return NodeOut.model_validate(await get_node(session, user, node_id))


@router.post("/files/{node_id}/folders", status_code=201)
async def new_folder(
    node_id: uuid.UUID, body: NewFolder, user: CurrentUser, session: Session
) -> NodeOut:
    parent = await get_dir(session, user, node_id)
    name = clean_name(body.name)
    if await sibling(session, parent.id, name):
        raise HTTPException(409, "Er bestaat al iets met deze naam")
    folder = Node(parent_id=parent.id, owner_id=user.id, name=name, kind="dir")
    session.add(folder)
    await session.commit()
    return NodeOut.model_validate(folder)


async def room_left(
    session: AsyncSession, user: User, exclude_upload: uuid.UUID | None = None
) -> int | None:
    """Hoeveel bytes de gebruiker nog mag opslaan, of None bij onbeperkt. Lopende
    hervatbare uploads tellen al mee, zodat twee uploads samen niet over het quotum gaan."""
    if not user.quota_bytes:
        return None
    query = select(func.coalesce(func.sum(Upload.size), 0)).where(
        Upload.owner_id == user.id, Upload.node_id.is_(None)
    )
    if exclude_upload:
        query = query.where(Upload.id != exclude_upload)
    reserved = await session.scalar(query)
    return max(0, user.quota_bytes - await used_bytes(session, user.id) - int(reserved or 0))


async def add_ref(session: AsyncSession, sha: str, size: int) -> None:
    """ref_count + 1 in de database zelf, of een nieuwe blob-rij. Alleen onder BlobStore.lock."""
    result = await session.execute(
        update(Blob).where(Blob.sha256 == sha).values(ref_count=Blob.ref_count + 1)
    )
    if result.rowcount == 0:
        session.add(Blob(sha256=sha, size=size, ref_count=1))


async def drop_ref(session: AsyncSession, sha: str) -> None:
    """ref_count - 1 in de database zelf. Alleen onder BlobStore.lock."""
    await session.execute(
        update(Blob).where(Blob.sha256 == sha).values(ref_count=Blob.ref_count - 1)
    )


async def place_file(
    session: AsyncSession,
    blobs: BlobStore,
    user: User,
    parent: Node,
    name: str,
    conflict: str,
    sha: str,
    size: int,
    source: Path,
    upload: Upload | None = None,
) -> Node:
    """Zet de inhoud in bestand `source` (SHA-256 `sha`) als bestand `name` in map `parent`.
    conflict: rename = nieuwe naam als het al bestaat, replace = vervangen, fail = NameTaken.
    Gelukt: `source` is weg (verplaatst naar de blob-opslag, of weggegooid omdat die inhoud
    er al was). Mislukt: `source` staat er nog en de database is niet veranderd. Komt het van
    een hervatbare upload, dan wordt die in dezelfde transactie afgerond."""
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    moved = False
    async with blobs.lock:
        try:
            existing = await sibling(session, parent.id, name)
            if existing and conflict == "fail":
                raise NameTaken
            await add_ref(session, sha, size)
            if existing and conflict == "replace" and existing.kind == "file":
                if existing.blob_id:
                    await drop_ref(session, existing.blob_id)
                existing.blob_id, existing.size, existing.mime = sha, size, mime
                existing.modified_at = datetime.now(UTC)
                node = existing
            else:
                if existing:
                    name = await free_name(session, parent.id, name)
                node = Node(
                    id=uuid.uuid4(),
                    parent_id=parent.id,
                    owner_id=user.id,
                    name=name,
                    kind="file",
                    mime=mime,
                    size=size,
                    blob_id=sha,
                )
                session.add(node)
            if upload is not None:
                upload.node_id = node.id
            await session.flush()
            # Pas nu de schijf; wat hierna nog misgaat, draait give_back() terug
            moved = await blobs.adopt(source, sha)
            await session.commit()
        except BaseException:
            with anyio.CancelScope(shield=True):
                await session.rollback()
                # Terug waar het stond, tenzij de commit toch doorkwam (dan is er een rij)
                if moved and not await session.scalar(
                    select(Blob.sha256).where(Blob.sha256 == sha)
                ):
                    await blobs.give_back(source, sha)
            raise
    if not moved:
        await anyio.Path(source).unlink(missing_ok=True)  # die inhoud stond er al
    await collect_garbage(session, blobs)
    return node


@router.put("/files/{node_id}/upload", status_code=201)
async def upload(
    node_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
    name: Annotated[str, Query(min_length=1, max_length=255)],
    conflict: Literal["rename", "replace", "fail"] = "rename",
    content_length: Annotated[int | None, Header()] = None,
) -> NodeOut:
    """Uploadt de request-body in één keer als bestand `name` in map `node_id`.
    Voor grote bestanden is /api/v1/tus (hervatbaar) beter.
    Dezelfde inhoud wordt maar één keer opgeslagen (SHA-256)."""
    parent = await get_dir(session, user, node_id)
    name = clean_name(name)
    limit = settings.max_upload_bytes
    room = await room_left(session, user)
    if room is not None:
        if content_length is not None and content_length > room:
            raise HTTPException(413, "Onvoldoende quotum")
        limit = min(limit, room) if limit else room
        if limit == 0 and content_length != 0:  # 0 betekent voor receive() onbeperkt
            raise HTTPException(413, "Onvoldoende quotum")
    if content_length is not None and limit and content_length > limit:
        raise HTTPException(413, "Bestand is te groot")
    await session.commit()  # geen databaseverbinding vasthouden terwijl de body binnenkomt
    try:
        tmp, sha, size = await blobs.receive(request.stream(), limit)
    except TooLarge as e:
        raise HTTPException(413, "Bestand is te groot of quotum is op") from e
    except TimeoutError as e:
        raise HTTPException(408, "Er kwam te lang niets binnen; probeer het opnieuw") from e
    try:
        node = await place_file(session, blobs, user, parent, name, conflict, sha, size, tmp)
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)  # alleen nog over als het misging
    return NodeOut.model_validate(node)


@router.patch("/files/{node_id}")
async def change(node_id: uuid.UUID, body: Change, user: CurrentUser, session: Session) -> NodeOut:
    """Hernoemen en/of verplaatsen."""
    node = await get_node(session, user, node_id)
    if node.parent_id is None:
        raise HTTPException(422, "De hoofdmap kan niet worden gewijzigd")
    name = clean_name(body.name) if body.name else node.name
    parent_id = body.parent_id or node.parent_id
    if body.parent_id:
        target = await get_dir(session, user, body.parent_id)
        if any(a.id == node.id for a in await ancestors(session, target)):
            raise HTTPException(422, "Een map kan niet in zichzelf worden geplaatst")
    other = await sibling(session, parent_id, name)
    if other and other.id != node.id:
        raise HTTPException(409, "Er bestaat al iets met deze naam")
    node.name, node.parent_id = name, parent_id
    node.modified_at = datetime.now(UTC)
    await session.commit()
    return NodeOut.model_validate(node)


@router.delete("/files/{node_id}", status_code=204)
async def trash(node_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """Naar de prullenbak; de inhoud blijft bewaard tot de prullenbak wordt geleegd."""
    node = await get_node(session, user, node_id)
    if node.parent_id is None:
        raise HTTPException(422, "De hoofdmap kan niet worden verwijderd")
    node.deleted_at = datetime.now(UTC)
    await session.commit()


@router.get("/trash")
async def list_trash(user: CurrentUser, session: Session) -> list[NodeOut]:
    rows = (
        await session.scalars(
            select(Node)
            .where(Node.owner_id == user.id, Node.deleted_at.is_not(None))
            .order_by(Node.deleted_at.desc())
        )
    ).all()
    return [NodeOut.model_validate(n) for n in rows]


@router.post("/files/{node_id}/restore")
async def restore(node_id: uuid.UUID, user: CurrentUser, session: Session) -> NodeOut:
    node = await get_node(session, user, node_id)
    if node.deleted_at is None:
        raise HTTPException(422, "Staat niet in de prullenbak")
    parent = await session.get(Node, node.parent_id)
    if parent is None or await is_trashed(session, parent):
        node.parent_id = user.root_id
    node.name = await free_name(session, node.parent_id, node.name)
    node.deleted_at = None
    await session.commit()
    return NodeOut.model_validate(node)


@router.delete("/trash/{node_id}", status_code=204)
async def purge(node_id: uuid.UUID, user: CurrentUser, session: Session, blobs: Store) -> None:
    """Definitief verwijderen, inclusief alles eronder."""
    node = await get_node(session, user, node_id)
    if node.deleted_at is None:
        raise HTTPException(422, "Verplaats het eerst naar de prullenbak")
    async with blobs.lock:
        for n in reversed(await descendants(session, node)):
            if n.blob_id:
                await drop_ref(session, n.blob_id)
            await session.delete(n)
            await session.flush()
        await session.commit()
    await collect_garbage(session, blobs)


async def collect_garbage(session: AsyncSession, blobs: BlobStore) -> None:
    """Verwijdert blobs waar niemand meer naar verwijst. De database beslist: alleen een rij
    met ref_count 0 waar ook echt geen bestand naar wijst, gaat weg, en pas als dat vastligt
    ook de inhoud op schijf."""
    async with blobs.lock:
        orphans = (await session.scalars(select(Blob.sha256).where(Blob.ref_count <= 0))).all()
        gone = []
        for sha in orphans:
            result = await session.execute(
                delete(Blob)
                .where(
                    Blob.sha256 == sha,
                    Blob.ref_count <= 0,
                    ~select(Node.id).where(Node.blob_id == sha).exists(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount:
                gone.append(sha)
        await session.commit()
        for sha in gone:
            await blobs.delete(sha)


class DownloadLink(BaseModel):
    url: str
    expires_in: int


@router.post("/files/{node_id}/download-link")
async def download_link(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DownloadLink:
    """Kortlevende link voor de browser (een <a>- of <video>-tag kan geen header meesturen)."""
    node = await get_node(session, user, node_id)
    if node.kind != "file":
        raise HTTPException(422, "Alleen bestanden kunnen worden gedownload")
    token = download_token(settings, user.id, node.id)
    return DownloadLink(
        url=f"/api/v1/download/{node.id}?token={token}",
        expires_in=settings.download_token_minutes * 60,
    )


ACTIVE_TYPES = ("html", "xml", "svg", "javascript", "ecmascript")


async def send_file(
    node: Node, settings: Settings, if_none_match: str | None, inline: bool
) -> Response:
    if node.kind != "file" or not node.blob_id:
        raise HTTPException(422, "Geen bestand")
    if if_none_match and node.etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": node.etag})
    path = BlobStore(settings.blob_dir).path(node.blob_id)
    headers = {"ETag": node.etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    mime = node.mime or "application/octet-stream"
    if any(t in mime for t in ACTIVE_TYPES):
        # Geüploade HTML/SVG mag geen scripts draaien onder het adres van het platform
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(
        path,
        media_type=mime,
        filename=node.name,
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )


@router.get("/download/{node_id}")
async def download(
    node_id: uuid.UUID,
    token: str,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    inline: bool = False,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    user_id, token_node = read_download_token(settings, token)
    if token_node != node_id:
        raise HTTPException(403, "Token hoort bij een ander bestand")
    node = await session.get(Node, node_id)
    if node is None or node.owner_id != user_id:
        raise HTTPException(404, "Niet gevonden")
    return await send_file(node, settings, if_none_match, inline)


@router.get("/files/{node_id}/content")
async def content(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    inline: bool = False,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Download met een gewone Authorization-header (voor apps); ondersteunt Range en ETag."""
    node = await get_node(session, user, node_id)
    return await send_file(node, settings, if_none_match, inline)
