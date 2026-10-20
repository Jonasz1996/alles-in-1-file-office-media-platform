import asyncio
import re
import uuid
from collections import Counter
from collections.abc import Iterable
from contextlib import nullcontext
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path
from typing import Annotated, Literal

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import Integer, Select, delete, func, insert, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import events, previews
from app.config import Settings, get_settings
from app.db import Session
from app.mime import read_head, sniff
from app.models import Blob, Node, NodeTag, Tag, Upload, User, Version
from app.routers.auth import used_bytes
from app.security import CurrentUser, download_token, read_download_token
from app.storage import BlobStore, TooLarge
from app.text import fold

router = APIRouter(prefix="/api/v1", tags=["bestanden"])
FORBIDDEN = re.compile(r"[/\\\x00-\x1f]")
MAX_NAME = 255
# Zoveel eerdere versies van een bestand blijven bewaard; de oudste gaan eerst
MAX_VERSIONS = 20
# Zoveel mappen en bestanden kopieert één verzoek hooguit: de kopie gebeurt in één transactie
# (onder het blob-slot), en die mag andere schrijvers niet te lang laten wachten
MAX_COPY = 10_000

# Per gebruiker: quotum controleren en ruimte innemen in één keer (hervatbare uploads
# reserveren, kopiëren maakt de kopie), zodat twee verzoeken samen niet over het quotum gaan
user_locks: dict[uuid.UUID, asyncio.Lock] = {}


def user_lock(user_id: uuid.UUID) -> asyncio.Lock:
    return user_locks.setdefault(user_id, asyncio.Lock())


def _utc(value: datetime) -> datetime:
    """SQLite geeft tijden zonder tijdzone terug; ze staan altijd in UTC opgeslagen."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


Utc = Annotated[datetime, AfterValidator(_utc)]


class TagRef(BaseModel):
    id: uuid.UUID
    name: str
    color: str

    model_config = {"from_attributes": True}


class NodeOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    kind: str
    mime: str | None
    size: int
    etag: str
    created_at: Utc
    modified_at: Utc
    deleted_at: Utc | None
    # Alleen de tags van wie het vraagt
    tags: list[TagRef] = []

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


class CopyTo(BaseModel):
    parent_id: uuid.UUID
    name: str | None = Field(default=None, min_length=1, max_length=255)


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
    if not name or name in (".", "..") or FORBIDDEN.search(name) or len(name) > MAX_NAME:
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


async def get_live_dir(session: AsyncSession, user: User, node_id: uuid.UUID) -> Node:
    """Een map waar iets in mag komen: ook niet ergens onder een map in de prullenbak."""
    folder = await get_dir(session, user, node_id)
    if await is_trashed(session, folder):
        raise HTTPException(404, "Map niet gevonden")
    return folder


async def sibling(session: AsyncSession, parent_id: uuid.UUID, name: str) -> Node | None:
    # Vers uit de database: wie op een slot wachtte, mag niet op een oude kopie beslissen
    return await session.scalar(
        select(Node)
        .where(
            Node.parent_id == parent_id,
            func.lower(Node.name) == name.lower(),
            Node.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )


def numbered(name: str, i: int) -> str:
    """x.txt -> x (i).txt; een lange naam wordt ingekort tot het binnen MAX_NAME past."""
    stem, dot, ext = name.rpartition(".")
    if not stem:
        stem, dot, ext = name, "", ""
    tail = f" ({i}){dot}{ext}"
    if len(tail) > MAX_NAME // 2:  # een heel lange "extensie" is gewoon deel van de naam
        stem, tail = name, f" ({i})"
    return stem[: MAX_NAME - len(tail)].rstrip() + tail


async def free_name(session: AsyncSession, parent_id: uuid.UUID, name: str) -> str:
    if await sibling(session, parent_id, name) is None:
        return name
    for i in range(1, 10_000):
        candidate = numbered(name, i)
        if await sibling(session, parent_id, candidate) is None:
            return candidate
    raise HTTPException(409, "Geen vrije naam gevonden")


def tree(start_id: uuid.UUID, live: bool = False):
    """Recursieve CTE met (id, depth) van start_id en alles eronder. live: niet verder dan
    wat in de prullenbak ligt (dat, en alles daaronder, komt er niet in)."""
    top = select(Node.id, literal(0, Integer).label("depth")).where(Node.id == start_id)
    found = top.cte("tree", recursive=True)
    child = aliased(Node)
    step = select(child.id, found.c.depth + 1).where(child.parent_id == found.c.id)
    if live:
        step = step.where(child.deleted_at.is_(None))
    return found.union_all(step)


async def subtree_rows(
    session: AsyncSession, node_id: uuid.UUID, *columns, live: bool = False, limit: int = 0
) -> list:
    """Deze kolommen van node_id en alles eronder in één query, ouders voor hun kinderen.
    Rijen, geen ORM-objecten, en in stukken binnengehaald: duizenden rijen in één keer
    omzetten houdt de event loop (en daarmee elk ander verzoek) te lang bezig. limit: hooguit
    zoveel rijen."""
    found = tree(node_id, live)
    query = select(*columns).join(found, Node.id == found.c.id).order_by(found.c.depth)
    if limit:
        query = query.limit(limit)
    rows = []
    async for part in (await session.stream(query)).partitions(500):
        rows.extend(part)
    return rows


async def descendants(session: AsyncSession, node: Node) -> list:
    """(id, blob_id) van node en alles eronder, ouders voor hun kinderen."""
    return await subtree_rows(session, node.id, Node.id, Node.blob_id)


async def tags_by_node(
    session: AsyncSession, user: User, node_ids: Iterable[uuid.UUID] | Select
) -> dict[uuid.UUID, list[TagRef]]:
    """De tags van deze gebruiker op deze nodes, in één query. node_ids mag ook een
    subquery zijn (bijv. alle kinderen van een map)."""
    if not isinstance(node_ids, Select):
        node_ids = list(node_ids)
        if not node_ids:
            return {}
    rows = await session.execute(
        select(NodeTag.node_id, Tag)
        .join(Tag, Tag.id == NodeTag.tag_id)
        .where(Tag.owner_id == user.id, NodeTag.node_id.in_(node_ids))
    )
    result: dict[uuid.UUID, list[TagRef]] = {}
    for node_id, tag in rows:
        result.setdefault(node_id, []).append(TagRef.model_validate(tag))
    for tags in result.values():
        tags.sort(key=tag_order)
    return result


def tag_order(tag: TagRef | Tag) -> tuple[str, str]:
    """Op naam zonder hoofdletters en accenten (in Python: lower() in SQLite kent alleen ASCII)."""
    return fold(tag.name), str(tag.id)


def node_out(node: Node, tags: dict[uuid.UUID, list[TagRef]] | None = None) -> NodeOut:
    out = NodeOut.model_validate(node)
    out.tags = (tags or {}).get(node.id, [])
    return out


async def one_out(session: AsyncSession, user: User, node: Node) -> NodeOut:
    return node_out(node, await tags_by_node(session, user, [node.id]))


def notify(node: Node, type: str, old_parent_id: uuid.UUID | None = None) -> None:
    """Na de commit: open web-apps en apps van de eigenaar laten weten dat de boom veranderde."""
    data = {"id": str(node.id), "parent_id": str(node.parent_id) if node.parent_id else None}
    if old_parent_id is not None and old_parent_id != node.parent_id:
        data["old_parent_id"] = str(old_parent_id)
    events.publish([node.owner_id], type, data)


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
            # Op de naam zonder hoofdletters en accenten: lower() in SQLite kent alleen ASCII
            .order_by(Node.kind, Node.search_name, Node.id)
        )
    ).all()
    tags = await tags_by_node(
        session,
        user,
        select(Node.id).where(or_(Node.parent_id == folder.id, Node.id == folder.id)),
    )
    return Listing(
        folder=node_out(folder, tags),
        path=[node_out(n) for n in await ancestors(session, folder)],
        children=[node_out(c, tags) for c in children],
    )


@router.get("/files/{node_id}")
async def info(node_id: uuid.UUID, user: CurrentUser, session: Session) -> NodeOut:
    return await one_out(session, user, await get_node(session, user, node_id))


@router.post("/files/{node_id}/folders", status_code=201)
async def new_folder(
    node_id: uuid.UUID, body: NewFolder, user: CurrentUser, session: Session
) -> NodeOut:
    parent = await get_live_dir(session, user, node_id)
    name = clean_name(body.name)
    if await sibling(session, parent.id, name):
        raise HTTPException(409, "Er bestaat al iets met deze naam")
    folder = Node(parent_id=parent.id, owner_id=user.id, name=name, kind="dir")
    session.add(folder)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()  # de map is intussen definitief verwijderd
        raise HTTPException(404, "Map niet gevonden") from None
    notify(folder, "node.created")
    return node_out(folder)


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


async def add_ref(session: AsyncSession, sha: str, size: int, mime: str | None = None) -> None:
    """ref_count + 1 in de database zelf, of een nieuwe blob-rij (en dan een taak voor de
    preview, die begint zodra de aanroeper commit). Alleen onder BlobStore.lock."""
    result = await session.execute(
        update(Blob)
        .where(Blob.sha256 == sha)
        .values(ref_count=Blob.ref_count + 1, mime=func.coalesce(Blob.mime, mime))
    )
    if result.rowcount == 0:
        session.add(Blob(sha256=sha, size=size, ref_count=1, mime=mime, preview="pending"))
        await previews.queue(session, sha)


async def drop_ref(session: AsyncSession, sha: str) -> None:
    """ref_count - 1 in de database zelf. Alleen onder BlobStore.lock."""
    await session.execute(
        update(Blob).where(Blob.sha256 == sha).values(ref_count=Blob.ref_count - 1)
    )


async def add_refs(session: AsyncSession, counts: Counter[str], sign: int = 1) -> None:
    """ref_count + n (of - n met sign=-1) voor bestaande blobs: één update per aantal en per
    500 blobs, niet per blob. Alleen onder BlobStore.lock."""
    by_count: dict[int, list[str]] = {}
    for sha, n in counts.items():
        by_count.setdefault(n, []).append(sha)
    for n, shas in by_count.items():
        for chunk in batched(shas, 500):
            await session.execute(
                update(Blob)
                .where(Blob.sha256.in_(chunk))
                .values(ref_count=Blob.ref_count + sign * n)
            )


async def keep_version(session: AsyncSession, node: Node, user: User, now: datetime) -> None:
    """De huidige inhoud van node wordt een versie: de verwijzing (ref_count) gaat mee naar
    de versie. Daarna mogen er hooguit MAX_VERSIONS zijn. Alleen onder BlobStore.lock."""
    session.add(
        Version(
            node_id=node.id,
            blob_id=node.blob_id,
            size=node.size,
            created_at=node.modified_at,
            replaced_at=now,
            replaced_by=user.id,
        )
    )
    await session.flush()
    too_old = (
        await session.scalars(
            select(Version)
            .where(Version.node_id == node.id)
            .order_by(Version.replaced_at.desc(), Version.id)
            .offset(MAX_VERSIONS)
        )
    ).all()
    for version in too_old:
        await drop_ref(session, version.blob_id)
        await session.delete(version)


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
    conflict: rename = nieuwe naam als het al bestaat, replace = vervangen (de oude inhoud
    blijft als versie bewaard), fail = NameTaken.
    Gelukt: `source` is weg (verplaatst naar de blob-opslag, of weggegooid omdat die inhoud
    er al was). Mislukt: `source` staat er nog en de database is niet veranderd. Komt het van
    een hervatbare upload, dan wordt die in dezelfde transactie afgerond (het quotum is dan al
    gereserveerd); anders wordt het quotum hier nog eens gecontroleerd, onder hetzelfde
    gebruikersslot als kopiëren en reserveren. Sluit eerst de transactie van de aanroeper af
    (die mag niets openstaan hebben): wie op het slot wacht, houdt geen databaseverbinding
    vast, anders kan wie het slot heeft er geen meer krijgen."""
    # Het soort volgt uit de inhoud; de naam helpt alleen waar de inhoud niets zegt
    mime = sniff(await anyio.to_thread.run_sync(read_head, source), name)
    moved, created = False, False
    await session.commit()
    async with user_lock(user.id) if upload is None else nullcontext(), blobs.lock:
        try:
            if await session.scalar(select(Node.id).where(Node.id == parent.id)) is None:
                raise HTTPException(404, "Map niet gevonden")  # intussen definitief verwijderd
            existing = await sibling(session, parent.id, name)
            if existing and conflict == "fail":
                raise NameTaken
            replacing = existing and conflict == "replace" and existing.kind == "file"
            if upload is None and not (replacing and existing.blob_id == sha):
                # Pas nu weet je wat anderen intussen innamen (de vorige inhoud blijft als
                # versie staan, dus ook vervangen kost ruimte)
                room = await room_left(session, user)
                if room is not None and size > room:
                    raise HTTPException(413, "Onvoldoende quotum")
            now = datetime.now(UTC)
            if replacing:
                if existing.blob_id != sha:
                    await add_ref(session, sha, size, mime)
                    if existing.blob_id:
                        await keep_version(session, existing, user, now)
                # Dezelfde inhoud als nu: geen nieuwe versie
                existing.blob_id, existing.size, existing.mime = sha, size, mime
                existing.modified_at = now
                node = existing
            else:
                await add_ref(session, sha, size, mime)
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
                created = True
            if upload is not None:
                upload.node_id = node.id
            await session.flush()
            node_id = node.id  # na een rollback is het object verlopen
            # Pas nu de schijf; wat hierna nog misgaat, draait give_back() terug
            moved = await blobs.adopt(source, sha)
            await session.commit()
        except BaseException:
            with anyio.CancelScope(shield=True):
                await session.rollback()
                # Terug waar het stond, tenzij de commit toch doorkwam (dan wijst dit bestand
                # naar die inhoud). Niet op de blob-rij afgaan: die kan er al zijn geweest
                # terwijl de inhoud op schijf ontbrak.
                if moved and not await session.scalar(
                    select(Node.id).where(Node.id == node_id, Node.blob_id == sha)
                ):
                    await blobs.give_back(source, sha)
            raise
    notify(node, "node.created" if created else "node.changed")
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
    Dezelfde inhoud wordt maar één keer opgeslagen (SHA-256). Met conflict=replace blijft
    de vorige inhoud bewaard als versie."""
    parent = await get_live_dir(session, user, node_id)
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
    return await one_out(session, user, node)


@router.patch("/files/{node_id}")
async def change(
    node_id: uuid.UUID, body: Change, user: CurrentUser, session: Session, blobs: Store
) -> NodeOut:
    """Hernoemen en/of verplaatsen. modified_at blijft staan: dat gaat over de inhoud (zoals
    bij een bestandssysteem), en een versie onthoudt het als het moment van die inhoud."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    # Onder het blob-slot, net als definitief verwijderen: anders kan dat iets weggooien dat
    # net uit een weggegooide map werd gehaald, of iets missen dat er net in kwam
    async with blobs.lock:
        node = await get_node(session, user, node_id)
        if node.parent_id is None:
            raise HTTPException(422, "De hoofdmap kan niet worden gewijzigd")
        name = clean_name(body.name) if body.name else node.name
        old_parent_id = node.parent_id
        parent_id = body.parent_id or node.parent_id
        if body.parent_id:
            target = await get_dir(session, user, body.parent_id)
            chain = await ancestors(session, target)
            if any(a.id == node.id for a in chain):
                raise HTTPException(422, "Een map kan niet in zichzelf worden geplaatst")
            if any(a.deleted_at is not None for a in chain):
                raise HTTPException(404, "Map niet gevonden")
        other = await sibling(session, parent_id, name)
        if other and other.id != node.id:
            raise HTTPException(409, "Er bestaat al iets met deze naam")
        node.name, node.parent_id = name, parent_id
        await session.commit()
    notify(node, "node.changed", old_parent_id)
    return await one_out(session, user, node)


@router.post("/files/{node_id}/copy", status_code=201)
async def copy(
    node_id: uuid.UUID, body: CopyTo, user: CurrentUser, session: Session, blobs: Store
) -> NodeOut:
    """Kopieert een bestand, of een map met alles erin (hooguit MAX_COPY mappen en
    bestanden), naar map parent_id. Bestaat de naam daar al, dan krijgt de kopie een vrije
    naam ("x (1)"). De inhoud zelf wordt niet nog eens opgeslagen: de kopie wijst naar
    dezelfde blobs. Versies en tags gaan niet mee."""
    await session.commit()  # niet met een databaseverbinding op een slot wachten
    # Quotum controleren en de kopie maken in één keer, en onder het blob-slot: ref_count
    # ophogen gebeurt nooit tegelijk met opruimen. Pas onder de sloten lezen: intussen kan
    # er van alles veranderd zijn.
    async with user_lock(user.id), blobs.lock:
        node = await get_node(session, user, node_id)
        if await is_trashed(session, node):
            raise HTTPException(422, "Wat in de prullenbak ligt, kan niet worden gekopieerd")
        target = await get_dir(session, user, body.parent_id)
        chain = await ancestors(session, target)
        if any(a.id == node.id for a in chain):
            raise HTTPException(422, "Een map kan niet in zichzelf worden gekopieerd")
        if any(a.deleted_at is not None for a in chain):
            raise HTTPException(404, "Map niet gevonden")
        name = clean_name(body.name) if body.name else node.name
        originals = await subtree_rows(
            session,
            node.id,
            *(Node.id, Node.parent_id, Node.name, Node.kind, Node.mime, Node.size),
            *(Node.blob_id, Node.modified_at),
            live=True,
            limit=MAX_COPY + 1,
        )
        if len(originals) > MAX_COPY:
            most = f"{MAX_COPY:,}".replace(",", ".")
            raise HTTPException(
                422, f"Te veel om in één keer te kopiëren (meer dan {most} mappen en bestanden)"
            )
        room = await room_left(session, user)
        if room is not None and sum(n.size for n in originals if n.kind == "file") > room:
            raise HTTPException(413, "Onvoldoende quotum")
        name = await free_name(session, target.id, name)
        now = datetime.now(UTC)
        new_ids: dict[uuid.UUID, uuid.UUID] = {}

        def copied(n) -> dict:
            top = n.id == node.id
            new_name = name if top else n.name
            new_ids[n.id] = uuid.uuid4()
            return {
                "id": new_ids[n.id],
                "parent_id": target.id if top else new_ids[n.parent_id],
                "owner_id": user.id,
                "name": new_name,
                "search_name": fold(new_name),  # het attribuut-event loopt hier niet
                "kind": n.kind,
                "mime": n.mime,
                "size": n.size,
                "blob_id": n.blob_id,
                "created_at": now,
                "modified_at": n.modified_at,
            }

        # Rechtstreeks in stukken, ouders voor kinderen, en tussendoor de anderen even aan
        # de beurt: één ORM-object per kopie houdt de event loop seconden bezig
        for chunk in batched(originals, 500):
            await session.execute(insert(Node), [copied(n) for n in chunk])
            await asyncio.sleep(0)
        await add_refs(session, Counter(n.blob_id for n in originals if n.blob_id))
        await session.commit()
    top = await session.get(Node, new_ids[node.id])
    notify(top, "node.created")
    return node_out(top)


@router.delete("/files/{node_id}", status_code=204)
async def trash(node_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """Naar de prullenbak; de inhoud blijft bewaard tot de prullenbak wordt geleegd."""
    node = await get_node(session, user, node_id)
    if node.parent_id is None:
        raise HTTPException(422, "De hoofdmap kan niet worden verwijderd")
    node.deleted_at = datetime.now(UTC)
    await session.commit()
    notify(node, "node.deleted")


@router.get("/trash")
async def list_trash(user: CurrentUser, session: Session) -> list[NodeOut]:
    rows = (
        await session.scalars(
            select(Node)
            .where(Node.owner_id == user.id, Node.deleted_at.is_not(None))
            .order_by(Node.deleted_at.desc())
        )
    ).all()
    tags = await tags_by_node(
        session,
        user,
        select(Node.id).where(Node.owner_id == user.id, Node.deleted_at.is_not(None)),
    )
    return [node_out(n, tags) for n in rows]


@router.post("/files/{node_id}/restore")
async def restore(node_id: uuid.UUID, user: CurrentUser, session: Session, blobs: Store) -> NodeOut:
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with blobs.lock:  # niet tegelijk met definitief verwijderen, zie change()
        node = await get_node(session, user, node_id)
        if node.deleted_at is None:
            raise HTTPException(422, "Staat niet in de prullenbak")
        parent = await session.get(Node, node.parent_id)
        if parent is None or await is_trashed(session, parent):
            node.parent_id = user.root_id
        node.name = await free_name(session, node.parent_id, node.name)
        node.deleted_at = None
        await session.commit()
    notify(node, "node.changed")
    return await one_out(session, user, node)


@router.delete("/trash/{node_id}", status_code=204)
async def purge(node_id: uuid.UUID, user: CurrentUser, session: Session, blobs: Store) -> None:
    """Definitief verwijderen, inclusief alles eronder en alle versies."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with blobs.lock:
        # Pas onder het slot lezen: intussen kan het al weg of teruggezet zijn
        node = await get_node(session, user, node_id)
        if node.deleted_at is None:
            raise HTTPException(422, "Verplaats het eerst naar de prullenbak")
        rows = await descendants(session, node)
        ids = [n.id for n in rows]
        refs = Counter(n.blob_id for n in rows if n.blob_id) + await drop_versions(session, ids)
        await add_refs(session, refs, sign=-1)
        for chunk in batched(reversed(ids), 500):  # kinderen voor hun ouders
            await session.execute(
                delete(Node).where(Node.id.in_(chunk)).execution_options(synchronize_session=False)
            )
        await session.commit()
    notify(node, "node.deleted")
    await collect_garbage(session, blobs)


async def drop_versions(session: AsyncSession, node_ids: list[uuid.UUID]) -> Counter[str]:
    """Haalt alle versies van deze nodes weg en geeft per blob hoeveel verwijzingen dat
    scheelt. Zelf, niet via de cascade van de database: anders blijven de verwijzingen van de
    versies tellen en wordt hun inhoud nooit opgeruimd. Alleen onder BlobStore.lock."""
    refs: Counter[str] = Counter()
    for chunk in batched(node_ids, 500):
        refs.update(
            (await session.scalars(select(Version.blob_id).where(Version.node_id.in_(chunk)))).all()
        )
        await session.execute(
            delete(Version)
            .where(Version.node_id.in_(chunk))
            .execution_options(synchronize_session=False)
        )
    return refs


async def collect_garbage(session: AsyncSession, blobs: BlobStore) -> None:
    """Verwijdert blobs waar niemand meer naar verwijst. De database beslist: alleen een rij
    met ref_count 0 waar ook echt geen bestand of versie naar wijst, gaat weg, en pas als dat
    vastligt ook de inhoud op schijf."""
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
                    ~select(Version.id).where(Version.blob_id == sha).exists(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount:
                gone.append(sha)
        await session.commit()
        for sha in gone:
            await blobs.delete(sha)
            await previews.discard(blobs, sha)


class UserRef(BaseModel):
    id: uuid.UUID
    display_name: str


class VersionOut(BaseModel):
    id: uuid.UUID
    size: int
    # Wanneer deze inhoud gemaakt werd, en wanneer en door wie hij vervangen werd
    created_at: Utc
    replaced_at: Utc
    replaced_by: UserRef | None


async def get_file(session: AsyncSession, user: User, node_id: uuid.UUID) -> Node:
    node = await get_node(session, user, node_id)
    if node.kind != "file":
        raise HTTPException(422, "Alleen bestanden hebben versies")
    return node


async def get_version(session: AsyncSession, node: Node, version_id: uuid.UUID) -> Version:
    version = await session.get(Version, version_id, populate_existing=True)
    if version is None or version.node_id != node.id:
        raise HTTPException(404, "Versie niet gevonden")
    return version


@router.get("/files/{node_id}/versions")
async def versions(node_id: uuid.UUID, user: CurrentUser, session: Session) -> list[VersionOut]:
    """Eerdere inhoud van een bestand, nieuwste eerst."""
    node = await get_file(session, user, node_id)
    rows = await session.execute(
        select(Version, User.id, User.display_name)
        .outerjoin(User, User.id == Version.replaced_by)
        .where(Version.node_id == node.id)
        .order_by(Version.replaced_at.desc(), Version.id)
    )
    return [
        VersionOut(
            id=v.id,
            size=v.size,
            created_at=v.created_at,
            replaced_at=v.replaced_at,
            replaced_by=UserRef(id=uid, display_name=name) if uid else None,
        )
        for v, uid, name in rows
    ]


@router.post("/files/{node_id}/versions/{version_id}/restore")
async def restore_version(
    node_id: uuid.UUID,
    version_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    blobs: Store,
) -> NodeOut:
    """Zet een eerdere inhoud terug; wat er nu in staat, wordt zelf een versie."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with blobs.lock:
        node = await get_file(session, user, node_id)
        if await is_trashed(session, node):
            raise HTTPException(422, "Zet het bestand eerst terug uit de prullenbak")
        version = await get_version(session, node, version_id)
        changed = version.blob_id != node.blob_id
        if changed:
            # De verwijzingen ruilen alleen van plaats: ref_count blijft gelijk
            head = await anyio.to_thread.run_sync(read_head, blobs.path(version.blob_id))
            now = datetime.now(UTC)
            session.add(
                Version(
                    node_id=node.id,
                    blob_id=node.blob_id,
                    size=node.size,
                    created_at=node.modified_at,
                    replaced_at=now,
                    replaced_by=user.id,
                )
            )
            node.blob_id, node.size = version.blob_id, version.size
            node.mime = sniff(head, node.name)
            node.modified_at = now
            await session.delete(version)
        await session.commit()
    if changed:
        notify(node, "node.changed")
    return await one_out(session, user, node)


@router.delete("/files/{node_id}/versions/{version_id}", status_code=204)
async def delete_version(
    node_id: uuid.UUID,
    version_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    blobs: Store,
) -> None:
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with blobs.lock:
        node = await get_file(session, user, node_id)
        version = await get_version(session, node, version_id)
        await drop_ref(session, version.blob_id)
        await session.delete(version)
        await session.commit()
    await collect_garbage(session, blobs)


class DownloadLink(BaseModel):
    url: str
    expires_in: int


@router.post("/files/{node_id}/download-link")
async def download_link(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    version: uuid.UUID | None = None,
) -> DownloadLink:
    """Kortlevende link voor de browser (een <a>- of <video>-tag kan geen header meesturen).
    Met version: de link naar die eerdere inhoud; de versie zit in het ondertekende token."""
    node = await get_node(session, user, node_id)
    if node.kind != "file":
        raise HTTPException(422, "Alleen bestanden kunnen worden gedownload")
    if version is not None:
        await get_version(session, node, version)
    token = download_token(settings, user.id, node.id, version)
    return DownloadLink(
        url=f"/api/v1/download/{node.id}?token={token}",
        expires_in=settings.download_token_minutes * 60,
    )


ACTIVE_TYPES = ("html", "xml", "svg", "javascript", "ecmascript")


async def send_file(
    node: Node,
    settings: Settings,
    if_none_match: str | None,
    inline: bool,
    version: Version | None = None,
) -> Response:
    if node.kind != "file" or not node.blob_id:
        raise HTTPException(422, "Geen bestand")
    sha = version.blob_id if version is not None else node.blob_id
    etag = f'"{sha}"'
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    path = BlobStore(settings.blob_dir).path(sha)
    headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
    if version is not None:
        # Het soort van die inhoud zelf, met de naam van nu (zoals bij terugzetten)
        mime = sniff(await anyio.to_thread.run_sync(read_head, path), node.name)
    else:
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
    user_id, token_node, version_id = read_download_token(settings, token)
    if token_node != node_id:
        raise HTTPException(403, "Token hoort bij een ander bestand")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Niet gevonden")
    node = await get_node(session, user, node_id)
    version = await get_version(session, node, version_id) if version_id else None
    return await send_file(node, settings, if_none_match, inline, version)


@router.get("/files/{node_id}/content")
async def content(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    inline: bool = False,
    version: uuid.UUID | None = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Download met een gewone Authorization-header (voor apps); ondersteunt Range en ETag.
    Met version: die eerdere inhoud."""
    node = await get_node(session, user, node_id)
    found = await get_version(session, node, version) if version else None
    return await send_file(node, settings, if_none_match, inline, found)
