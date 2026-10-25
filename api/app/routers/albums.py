"""Albums: een node met kind "album" buiten de boom (parent_id NULL) plus de tabellen album
(beschrijving, omslag) en album_item (welke eigen foto's en video's erin staan; er wordt niets
gekopieerd).

Delen gaat zoals bij een map: POST /files/{album_id}/shares en /links (routers/shares.py),
maar alleen om te bekijken. Wie een album via een deling ziet, mag de bestanden erin lezen
(app/access.py); wijzigen kan alleen de eigenaar (403 owner_only voor de rest).

Wat in een album "erin staat", is altijd levend: een bestand in de prullenbak (of onder een map
in de prullenbak) telt niet mee en verschijnt niet, maar blijft wel lid; zet je het terug, dan
staat het er weer. Definitief verwijderen haalt het lid weg (ON DELETE CASCADE).

Gebeurtenissen (na de commit): album.changed {id} en album.deleted {id} naar de eigenaar en
wie het album via een deling ziet (access.watchers); bij verwijderen eerst share.deleted naar
wie het gedeeld kreeg.
"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, errors, events, photodate, previews
from app.config import Settings, get_settings
from app.db import Session
from app.errors import ItemResult
from app.models import Album, AlbumItem, Blob, Node, Share, User
from app.routers.files import GUEST, OwnerRef, Utc, clean_name, get_access, owner_refs, shared_ids
from app.routers.photos import (
    MAX_PAGE,
    IdRange,
    Limit,
    PhotoPage,
    id_range,
    live_dirs,
    next_cursor,
    page_where,
    photo_items,
    photo_query,
)
from app.routers.shares import announce, grantees
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])
MAX_ADD = 1000
MOVED_TEXT = "Het album is intussen veranderd; probeer het opnieuw"
MAX_DESCRIPTION = 2000


class Cover(BaseModel):
    thumb: str | None
    large: str | None


class AlbumOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    owner: OwnerRef
    permission: Literal["owner", "read"]
    # Levende foto's en video's erin
    count: int
    cover: Cover | None
    first_taken_at: Utc | None
    last_taken_at: Utc | None
    # Er staat een deling of link op (alleen voor de eigenaar)
    shared: bool
    created_at: Utc
    modified_at: Utc


class Albums(BaseModel):
    albums: list[AlbumOut]


class NewAlbum(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)


class AlbumChange(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    # null: geen vaste omslag (dan de nieuwste foto)
    cover_id: uuid.UUID | None = None


class NodeIds(BaseModel):
    node_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_ADD)


class Added(BaseModel):
    added: int
    skipped: list[ItemResult]


class Removed(BaseModel):
    removed: int


def now() -> datetime:
    return datetime.now(UTC)


async def get_album(
    session: AsyncSession, user: User, album_id: uuid.UUID, need: Literal["read", "owner"]
) -> Node:
    """Het album als user het mag zien (en bij need="owner" ook wijzigen). Geen album, of niet
    zichtbaar: 404, zoals bij een map."""
    found = await get_access(session, user, album_id, need)
    if found.node.kind != "album":
        raise errors.fail(404, "not_found", "Niet gevonden")
    return found.node


def live_items(album: Node, root_id: uuid.UUID | None):
    """Voorwaarden voor de levende leden van dit album (join AlbumItem op Node)."""
    dirs = live_dirs(root_id)
    return [
        AlbumItem.album_id == album.id,
        Node.id == AlbumItem.node_id,
        Node.owner_id == album.owner_id,
        Node.kind == "file",
        Node.taken_day.is_not(None),
        Node.deleted_at.is_(None),
        Node.parent_id.in_(select(dirs.c.id)),
    ]


async def roots_of(session: AsyncSession, owner_ids: set[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    rows = await session.execute(select(User.id, User.root_id).where(User.id.in_(owner_ids)))
    return {uid: root for uid, root in rows}


async def albums_out(
    session: AsyncSession,
    user: User,
    albums: list[Node],
    permission: dict[uuid.UUID, str],
    settings: Settings,
) -> list[AlbumOut]:
    """AlbumOut voor deze albums: per eigenaar één query voor aantal en datums en één voor de
    omslagen, plus de beschrijvingen, namen en delingen."""
    if not albums:
        return []
    ids = [a.id for a in albums]
    extra = {row.id: row for row in (await session.scalars(select(Album).where(Album.id.in_(ids))))}
    roots = await roots_of(session, {a.owner_id for a in albums})
    owners = await owner_refs(session, user, {a.owner_id for a in albums})
    shared = await shared_ids(session, [a.id for a in albums if a.owner_id == user.id])
    stats: dict[uuid.UUID, tuple[int, datetime | None, datetime | None]] = {}
    covers: dict[uuid.UUID, str | None] = {}
    by_owner: dict[uuid.UUID, list[Node]] = defaultdict(list)
    for album in albums:
        by_owner[album.owner_id].append(album)
    for owner_id, group in by_owner.items():
        dirs = live_dirs(roots.get(owner_id))
        alive = [
            AlbumItem.album_id.in_([a.id for a in group]),
            Node.owner_id == owner_id,
            Node.kind == "file",
            Node.taken_day.is_not(None),
            Node.deleted_at.is_(None),
            Node.parent_id.in_(select(dirs.c.id)),
        ]
        rows = await session.execute(
            select(
                AlbumItem.album_id, func.count(), func.min(Node.taken_at), func.max(Node.taken_at)
            )
            .join(Node, Node.id == AlbumItem.node_id)
            .where(*alive)
            .group_by(AlbumItem.album_id)
        )
        stats.update({a: (n, first, last) for a, n, first, last in rows})
        # Omslag: de gekozen foto als die nog leeft, anders de nieuwste
        newest = (
            func.row_number()
            .over(
                partition_by=AlbumItem.album_id,
                order_by=(Node.taken_day.desc(), Node.taken_at.desc(), Node.id.desc()),
            )
            .label("rank")
        )
        ranked = (
            select(AlbumItem.album_id, Node.id.label("node_id"), Node.blob_id, newest)
            .join(Node, Node.id == AlbumItem.node_id)
            .where(*alive)
            .subquery()
        )
        chosen = {extra[a.id].cover_id for a in group if a.id in extra and extra[a.id].cover_id}
        rows = await session.execute(
            select(ranked.c.album_id, ranked.c.node_id, ranked.c.blob_id, ranked.c.rank).where(
                (ranked.c.rank == 1) | ranked.c.node_id.in_(list(chosen))
            )
        )
        fixed = set()
        for album_id, node_id, blob_id, rank in rows:
            cover_id = extra[album_id].cover_id if album_id in extra else None
            if node_id == cover_id:
                covers[album_id] = blob_id
                fixed.add(album_id)
            elif rank == 1 and album_id not in fixed:
                covers[album_id] = blob_id
    shas = {sha for sha in covers.values() if sha}
    blobs = {}
    if shas:
        blobs = {
            b.sha256: b for b in await session.scalars(select(Blob).where(Blob.sha256.in_(shas)))
        }
    secret, exp = previews.url_key(settings), previews.expiry()
    result = []
    for album in albums:
        count, first, last = stats.get(album.id, (0, None, None))
        sha = covers.get(album.id)
        cover = None
        if sha:
            info = previews.info(secret, blobs.get(sha), exp)
            cover = Cover(thumb=info["thumb"], large=info["large"])
        result.append(
            AlbumOut(
                id=album.id,
                name=album.name,
                description=extra[album.id].description if album.id in extra else "",
                owner=owners.get(album.owner_id) or OwnerRef(id=album.owner_id, display_name=""),
                permission="owner" if permission[album.id] == "owner" else "read",
                count=count,
                cover=cover,
                first_taken_at=first,
                last_taken_at=last,
                shared=album.owner_id == user.id and album.id in shared,
                created_at=album.created_at,
                modified_at=album.modified_at,
            )
        )
    return result


async def one_album(session: AsyncSession, user: User, album: Node, settings: Settings) -> AlbumOut:
    permission = "owner" if album.owner_id == user.id else "read"
    [out] = await albums_out(session, user, [album], {album.id: permission}, settings)
    return out


async def changed(session: AsyncSession, album: Node, type: str = "album.changed") -> None:
    """Na de commit: naar de eigenaar en wie het album via een deling ziet."""
    seen = await access.watchers(session, [album.id])
    events.publish({album.owner_id, *seen.get(album.id, {})}, type, {"id": str(album.id)})


@router.post("/albums", status_code=201)
async def create_album(
    body: NewAlbum,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AlbumOut:
    """Een nieuw, leeg album van jou."""
    if user.role == "guest":
        raise errors.fail(403, **GUEST)
    album = Node(
        id=uuid.uuid4(), parent_id=None, owner_id=user.id, name=clean_name(body.name), kind="album"
    )
    session.add(album)
    await session.flush()
    session.add(Album(id=album.id, description=body.description.strip()))
    await session.commit()
    await changed(session, album)
    return await one_album(session, user, album, settings)


@router.get("/albums")
async def list_albums(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Albums:
    """Je eigen albums en de albums die met jou (of een groep van jou) gedeeld zijn en nu
    zichtbaar zijn; het laatst gewijzigd eerst."""
    own = []
    if user.role != "guest":
        own = list(
            (
                await session.scalars(
                    select(Node).where(Node.owner_id == user.id, Node.kind == "album")
                )
            ).all()
        )
    roots = await access.shared_roots(session, user.id)
    shared_ids_ = [r.node_id for r in roots]
    others = []
    if shared_ids_:
        others = list(
            (
                await session.scalars(
                    select(Node).where(Node.id.in_(shared_ids_), Node.kind == "album")
                )
            ).all()
        )
    permission = {a.id: "owner" for a in own} | {a.id: "read" for a in others}
    albums = sorted(own + others, key=lambda a: (a.modified_at, a.id), reverse=True)
    return Albums(albums=await albums_out(session, user, albums, permission, settings))


@router.get("/albums/{album_id}")
async def album_info(
    album_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AlbumOut:
    album = await get_album(session, user, album_id, "read")
    return await one_album(session, user, album, settings)


@router.patch("/albums/{album_id}")
async def change_album(
    album_id: uuid.UUID,
    body: AlbumChange,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AlbumOut:
    """Naam, beschrijving of omslag (een foto in het album; null = de nieuwste). Alleen de
    eigenaar."""
    album = await get_album(session, user, album_id, "owner")
    extra = await session.get(Album, album.id)
    if extra is None:  # een album zonder eigen rij (zou niet moeten): dan nu
        extra = Album(id=album.id)
        session.add(extra)
    given = body.model_fields_set
    if body.name is not None:
        album.name = clean_name(body.name)
    if body.description is not None:
        extra.description = body.description.strip()
    if "cover_id" in given:
        if body.cover_id is not None:
            member = await session.scalar(
                select(AlbumItem.node_id).where(
                    AlbumItem.album_id == album.id, AlbumItem.node_id == body.cover_id
                )
            )
            if member is None:
                raise errors.fail(
                    422, "cover_not_in_album", "De omslag moet een foto uit dit album zijn"
                )
        extra.cover_id = body.cover_id
    album.modified_at = now()
    await session.commit()
    await changed(session, album)
    return await one_album(session, user, album, settings)


@router.delete("/albums/{album_id}", status_code=204)
async def delete_album(album_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """Het album weg (de foto's zelf blijven staan). Eerst horen wie het gedeeld kregen dat
    hun deling weg is, dan pas gaat de node weg (met zijn delingen en links)."""
    album = await get_album(session, user, album_id, "owner")
    seen = await access.watchers(session, [album.id])
    shares = (await session.scalars(select(Share).where(Share.node_id == album.id))).all()
    told = []
    for share in shares:
        told.append((await grantees(session, share), share.id))
    for receivers, share_id in told:
        announce(receivers, "share.deleted", share_id, album.id)
    await session.execute(delete(Node).where(Node.id == album.id, Node.kind == "album"))
    await session.commit()
    events.publish(
        {album.owner_id, *seen.get(album.id, {})}, "album.deleted", {"id": str(album.id)}
    )


@router.get("/albums/{album_id}/items")
async def album_items(
    album_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    after: Annotated[str | None, Query(max_length=300)] = None,
    limit: Limit = MAX_PAGE,
) -> PhotoPage:
    """De foto's en video's in het album, nieuwste eerst, in pagina's (zoals /photos)."""
    album = await get_album(session, user, album_id, "read")
    root = (await roots_of(session, {album.owner_id})).get(album.owner_id)
    query = photo_query(*live_items(album, root), *page_where(after))
    rows = (await session.execute(query.limit(limit))).all()
    return PhotoPage(
        items=await photo_items(session, user, rows, settings), next=next_cursor(rows, limit)
    )


@router.get("/albums/{album_id}/ids")
async def album_ids(album_id: uuid.UUID, user: CurrentUser, session: Session) -> IdRange:
    """De ids van alle levende foto's en video's in het album (hooguit 10.000), voor
    downloaden als zip en selecteren."""
    album = await get_album(session, user, album_id, "read")
    root = (await roots_of(session, {album.owner_id})).get(album.owner_id)
    return await id_range(session, live_items(album, root))


@router.post("/albums/{album_id}/items")
async def add_items(
    album_id: uuid.UUID, body: NodeIds, user: CurrentUser, session: Session
) -> Added:
    """Eigen foto's en video's toevoegen (hooguit 1000). Wat niet kan, staat in skipped met een
    code: not_found, not_own (van een ander), not_media (geen foto of video, of een map),
    trashed (in de prullenbak) of already (staat er al in)."""
    return await _add(session, user, album_id, body.node_ids)


async def _add(
    session: AsyncSession, user: User, album_id: uuid.UUID, node_ids: list[uuid.UUID], retry=True
) -> Added:
    album = await get_album(session, user, album_id, "owner")
    ids = list(dict.fromkeys(node_ids))
    nodes = {n.id: n for n in await session.scalars(select(Node).where(Node.id.in_(ids)))}
    present = set(
        (
            await session.scalars(
                select(AlbumItem.node_id).where(
                    AlbumItem.album_id == album.id, AlbumItem.node_id.in_(ids)
                )
            )
        ).all()
    )
    up = access.ancestry(Node.id.in_([i for i in ids if i in nodes]))
    gone = set(
        (await session.scalars(select(up.c.origin).where(up.c.deleted_at.is_not(None)))).all()
    )
    skipped, adding = [], []
    for node_id in ids:
        node = nodes.get(node_id)
        if node is None:
            skipped.append(errors.item_fail(node_id, "not_found", "Niet gevonden"))
        elif node.owner_id != user.id:
            skipped.append(
                errors.item_fail(node_id, "not_own", "Je kunt alleen je eigen bestanden toevoegen")
            )
        elif node.kind != "file" or not photodate.is_media(node.mime) or node.taken_day is None:
            skipped.append(
                errors.item_fail(
                    node_id, "not_media", "Alleen foto's en video's kunnen in een album"
                )
            )
        elif node_id in gone:
            skipped.append(errors.item_fail(node_id, "trashed", "Dit ligt in de prullenbak"))
        elif node_id in present:
            skipped.append(errors.item_fail(node_id, "already", "Staat al in dit album"))
        else:
            adding.append(node_id)
    if adding:
        at = now()
        await session.execute(
            insert(AlbumItem),
            [
                {"album_id": album.id, "node_id": i, "added_by": user.id, "added_at": at}
                for i in adding
            ],
        )
        album.modified_at = at
        try:
            await session.commit()
        except IntegrityError:
            # Tegelijk toegevoegd (een ander tabblad), of intussen definitief weg: één keer
            # opnieuw, met wat er nu is
            await session.rollback()
            if not retry:
                raise errors.fail(409, "moved", MOVED_TEXT) from None
            return await _add(session, user, album_id, node_ids, retry=False)
        await changed(session, album)
    return Added(added=len(adding), skipped=skipped)


@router.post("/albums/{album_id}/items/remove")
async def remove_items(
    album_id: uuid.UUID, body: NodeIds, user: CurrentUser, session: Session
) -> Removed:
    """Uit het album halen (de bestanden zelf blijven staan). Alleen de eigenaar."""
    album = await get_album(session, user, album_id, "owner")
    ids = list(dict.fromkeys(body.node_ids))
    result = await session.execute(
        delete(AlbumItem).where(AlbumItem.album_id == album.id, AlbumItem.node_id.in_(ids))
    )
    removed = result.rowcount or 0
    if removed:
        extra = await session.get(Album, album.id)
        if extra is not None and extra.cover_id in ids:
            extra.cover_id = None
        album.modified_at = now()
        await session.commit()
        await changed(session, album)
    return Removed(removed=removed)
