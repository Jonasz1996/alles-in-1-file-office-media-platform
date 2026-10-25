"""Publieke links, zonder inloggen. De web-pagina /s/<token> opent de link met
POST /api/v1/public/open (het token komt zo nooit in een API-adres) en krijgt een sleutel
terug: een JWT met eigen typ ("link") voor die ene link, die hooguit 12 uur geldt (of korter
als de link eerder verloopt). Die sleutel gaat mee als Authorization: Bearer <sleutel>, of als
?key= waar de browser geen header kan sturen (<a>, <img>, <video>).

Bij elk verzoek opnieuw: bestaat de link nog, is hij niet verlopen, ligt de map of het bestand
niet in de prullenbak, en is het wachtwoord niet veranderd sinds de sleutel werd uitgegeven.

Een leeslink toont de map (of het bestand) met alles eronder, niets erboven. Een uploadlink
laat alleen uploaden (tus, zoals /api/v1/tus) in die ene map, en toont niets van wat erin
staat: er komen ook geen ids van bestanden in de antwoorden.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import access, errors, photodate, previews
from app.config import Settings, get_settings
from app.db import Session
from app.models import AlbumItem, Blob, Node, Share, User
from app.routers import albums as albums_router
from app.routers import photos, uploads
from app.routers.files import Gone, Store, Utc, clean_name, send_file
from app.routers.photos import PublicPhotoItem
from app.routers.previews import PreviewOut, PreviewRequest
from app.routers.uploads import Tus, Uploader
from app.security import (
    KEY_EXPIRED,
    RateLimiter,
    check_password,
    client_ip,
    link_key,
    password_print,
    read_link_key,
)

router = APIRouter(prefix="/api/v1/public", tags=["publiek"])
log = logging.getLogger("platform.public")
_bearer = HTTPBearer(auto_error=False)

LINK_NOT_FOUND = {"message": "Deze link bestaat niet (meer)", "code": "link_not_found"}
LINK_EXPIRED = {"message": "Deze link is verlopen", "code": "link_expired"}
PASSWORD_REQUIRED = {
    "message": "Voor deze link is een wachtwoord nodig",
    "code": "password_required",
}
WRONG_PASSWORD = {"message": "Wachtwoord klopt niet", "code": "wrong_password"}
UPLOAD_ONLY = {"message": "Met deze link kun je alleen uploaden", "code": "upload_only"}
READ_ONLY = {"message": "Met deze link kun je alleen kijken", "code": "read_only"}
NOT_FOUND = {"message": "Niet gevonden", "code": "not_found"}
RATE_LIMITED = {"message": "Te veel pogingen; probeer het straks opnieuw", "code": "rate_limited"}

# Per IP: zoveel keer een link openen per minuut, en zoveel uploads beginnen per uur
open_limiter = RateLimiter(20, 60, RATE_LIMITED)
upload_limiter = RateLimiter(100, 3600, RATE_LIMITED)
# Per link: na zoveel foute wachtwoorden in 10 minuten gaat hij even dicht, voor iedereen
password_limiter = RateLimiter(
    10,
    600,
    {
        "message": "Te vaak een fout wachtwoord voor deze link; probeer het over een paar minuten "
        "opnieuw",
        "code": "rate_limited",
    },
)
# Per link één wachtwoordcontrole tegelijk: zo telt de limiet alleen echte fouten, ook als
# veel mensen de link tegelijk openen (een poging die nog loopt, telt dan niet als fout)
_password_locks: dict[uuid.UUID, asyncio.Lock] = {}


class OpenLink(BaseModel):
    token: str = Field(max_length=200)
    password: str | None = Field(default=None, max_length=200)


class PublicNode(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    mime: str | None
    size: int
    etag: str
    modified_at: Utc

    model_config = {"from_attributes": True}


class PublicShare(BaseModel):
    # dir, file of album (een album: de foto's via GET /public/album/items)
    kind: str
    name: str
    permission: str
    owner_name: str
    expires_at: Utc | None
    root: PublicNode


class Opened(BaseModel):
    key: str
    expires_in: int
    share: PublicShare


class PublicListing(BaseModel):
    folder: PublicNode
    # Van de gedeelde map tot en met folder
    path: list[PublicNode]
    children: list[PublicNode]


@dataclass
class Link:
    share: Share
    node: Node


def expired(share: Share) -> bool:
    if share.expires_at is None:
        return False
    until = share.expires_at.replace(tzinfo=share.expires_at.tzinfo or UTC)
    return until <= datetime.now(UTC)


async def live_node(session: Session, share: Share) -> Node:
    """De gedeelde map of het bestand, als het er nog is en niet in de prullenbak ligt, en de
    eigenaar niet geblokkeerd is (beslissing 16). Een geblokkeerde eigenaar geeft dezelfde 404
    als een link die er niet is: de blokkade is zo niet af te lezen."""
    owner = await session.get(User, share.owner_id, populate_existing=True)
    if owner is None or owner.disabled_at is not None:
        raise errors.fail(404, **LINK_NOT_FOUND)
    node = await session.get(Node, share.node_id)
    if node is None or await access.trashed(session, node.id):
        raise errors.fail(404, **LINK_NOT_FOUND)
    return node


async def current_link(
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    key: Annotated[str | None, Query(max_length=2000)] = None,
) -> Link:
    raw = creds.credentials if creds is not None else key
    if not raw:
        raise errors.fail(401, **KEY_EXPIRED)
    share_id, fingerprint = read_link_key(settings, raw)
    share = await session.get(Share, share_id, populate_existing=True)
    if share is None or share.token is None:
        raise errors.fail(404, **LINK_NOT_FOUND)
    if expired(share):
        raise errors.fail(410, **LINK_EXPIRED)
    if fingerprint != password_print(share.password_hash):
        raise errors.fail(401, **KEY_EXPIRED)  # wachtwoord veranderd: opnieuw openen
    return Link(share, await live_node(session, share))


CurrentLink = Annotated[Link, Depends(current_link)]


def readable(link: Link) -> None:
    if link.share.permission != "read":
        raise errors.fail(403, **UPLOAD_ONLY)


def uploadable(link: Link) -> None:
    if link.share.permission != "upload" or link.node.kind != "dir":
        raise errors.fail(403, **READ_ONLY)


async def inside(session: Session, link: Link, node_ids: list[uuid.UUID]) -> dict[uuid.UUID, list]:
    """Per node die binnen de gedeelde map ligt (en niet in de prullenbak): de ids van de
    gedeelde map tot en met die node. Wat erbuiten ligt, ontbreekt. Eén query.

    Bij een album: het album zelf ([album_id]) en elk bestand dat erin staat, van de eigenaar
    van het album is en waarvan niets in de keten in de prullenbak ligt ([album_id, id]). Twee
    queries."""
    ids = list(dict.fromkeys(node_ids))
    if not ids:
        return {}
    if link.node.kind == "album":
        return await _in_album(session, link.node, ids)
    up = access.ancestry(Node.id.in_(ids))
    rows = await session.execute(select(up.c.origin, up.c.id, up.c.deleted_at, up.c.depth))
    chains: dict[uuid.UUID, dict[int, tuple]] = {}
    for origin, node_id, deleted_at, depth in rows:
        chains.setdefault(origin, {})[depth] = (node_id, deleted_at)
    result = {}
    for origin, chain in chains.items():
        path = []
        for depth in range(len(chain)):
            node_id, deleted_at = chain[depth]
            if deleted_at is not None:
                break
            path.append(node_id)
            if node_id == link.node.id:
                result[origin] = list(reversed(path))
                break
    return result


async def _in_album(session: Session, album: Node, ids: list[uuid.UUID]) -> dict[uuid.UUID, list]:
    result: dict[uuid.UUID, list] = {album.id: [album.id]} if album.id in ids else {}
    members = (
        await session.scalars(
            select(AlbumItem.node_id)
            .join(Node, Node.id == AlbumItem.node_id)
            .where(
                AlbumItem.album_id == album.id,
                AlbumItem.node_id.in_(ids),
                Node.owner_id == album.owner_id,
                Node.kind == "file",
            )
        )
    ).all()
    if not members:
        return result
    up = access.ancestry(Node.id.in_(members))
    gone = set(await session.scalars(select(up.c.origin).where(up.c.deleted_at.is_not(None))))
    for node_id in members:
        if node_id not in gone:
            result[node_id] = [album.id, node_id]
    return result


def public_node(node: Node) -> PublicNode:
    return PublicNode.model_validate(node)


@router.post("/open")
async def open_link(
    body: OpenLink,
    request: Request,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Opened:
    """Een link openen met zijn token (en wachtwoord als hij er een heeft). Geeft de sleutel
    voor de verzoeken hierna."""
    ip = client_ip(request)
    open_limiter.check(ip)
    share = await session.scalar(select(Share).where(Share.token == body.token))
    if share is None or not body.token:
        raise errors.fail(404, **LINK_NOT_FOUND)
    node = await live_node(session, share)
    if expired(share):
        raise errors.fail(410, **LINK_EXPIRED)
    if share.password_hash is not None:
        # Eerst de blokkade: daarna wordt er voor deze link niets meer geprobeerd
        password_limiter.refuse(str(share.id))
        if not body.password:
            raise errors.fail(401, **PASSWORD_REQUIRED)
        await session.commit()  # niet met een databaseverbinding op het slot wachten
        async with _password_locks.setdefault(share.id, asyncio.Lock()):
            password_limiter.refuse(str(share.id))  # intussen kan de limiet bereikt zijn
            if not await check_password(share.password_hash, body.password):
                password_limiter.add(str(share.id))
                log.warning("Fout wachtwoord voor link %s vanaf %s", share.id, ip)
                raise errors.fail(401, **WRONG_PASSWORD)
    owner = await session.get(User, share.owner_id)
    key, expires_in = link_key(settings, share.id, share.password_hash, share.expires_at)
    return Opened(
        key=key,
        expires_in=expires_in,
        share=PublicShare(
            kind=node.kind,
            name=node.name,
            permission=share.permission,
            owner_name=owner.display_name if owner else "",
            expires_at=share.expires_at,
            root=public_node(node),
        ),
    )


@router.get("/children")
async def children(
    link: CurrentLink, session: Session, folder: uuid.UUID | None = None
) -> PublicListing:
    """Wat er in een map binnen de gedeelde map staat (standaard de gedeelde map zelf)."""
    readable(link)
    if link.node.kind == "album":
        raise errors.fail(422, "album", "Deze link is voor een album, niet voor een map")
    if link.node.kind != "dir":
        raise errors.fail(422, "file", "Deze link is voor één bestand")
    target = folder or link.node.id
    chain = (await inside(session, link, [target])).get(target)
    if chain is None:
        raise errors.fail(404, **NOT_FOUND)
    nodes = {n.id: n for n in (await session.scalars(select(Node).where(Node.id.in_(chain))))}
    current = nodes.get(target)
    if current is None or current.kind != "dir":
        raise errors.fail(404, **NOT_FOUND)
    rows = (
        await session.scalars(
            select(Node)
            .where(Node.parent_id == current.id, Node.deleted_at.is_(None))
            .order_by(Node.kind, Node.search_name, Node.id)
        )
    ).all()
    return PublicListing(
        folder=public_node(current),
        path=[public_node(nodes[i]) for i in chain if i in nodes],
        children=[public_node(n) for n in rows],
    )


@router.get("/download/{node_id}")
async def download(
    node_id: uuid.UUID,
    link: CurrentLink,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    inline: bool = False,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Een bestand binnen de gedeelde map (of het gedeelde bestand zelf), met Range en
    ETag; geüploade HTML en SVG in een sandbox, zoals elke download."""
    readable(link)
    if node_id not in await inside(session, link, [node_id]):
        raise errors.fail(404, **NOT_FOUND)
    node = await session.get(Node, node_id)
    if node is None or node.kind != "file":
        raise errors.fail(404, **NOT_FOUND)
    return await send_file(node, settings, if_none_match, inline)


@router.post("/previews")
async def preview_info(
    body: PreviewRequest,
    link: CurrentLink,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, PreviewOut]:
    """Zoals POST /api/v1/previews, voor bestanden binnen de gedeelde map."""
    readable(link)
    ids = list(dict.fromkeys(body.node_ids))
    allowed = await inside(session, link, ids)
    loaded = (
        (await session.scalars(select(Node).where(Node.id.in_(list(allowed))))).all()
        if allowed
        else []
    )
    nodes = [n for n in loaded if n.kind == "file" and n.blob_id]
    blobs: dict[str, Any] = {}
    if nodes:
        rows = await session.scalars(
            select(Blob).where(Blob.sha256.in_({n.blob_id for n in nodes}))
        )
        blobs = {b.sha256: b for b in rows.all()}
    secret, exp = previews.url_key(settings), previews.expiry()
    return {
        str(n.id): PreviewOut(**previews.info(secret, blobs.get(n.blob_id), exp)) for n in nodes
    }


class PublicPhotoPage(BaseModel):
    items: list[PublicPhotoItem]
    next: str | None


@router.get("/album/items")
async def album_items(
    link: CurrentLink,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    after: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=photos.MAX_PAGE)] = photos.MAX_PAGE,
) -> PublicPhotoPage:
    """De foto's en video's van een album achter een leeslink, nieuwste eerst, in pagina's
    (zoals GET /api/v1/albums/{id}/items, zonder shared). Alleen wat nu levend in het album
    staat; downloaden en previews gaan via /download en /previews van deze link."""
    readable(link)
    album = link.node
    if album.kind != "album":
        raise errors.fail(422, "not_an_album", "Deze link is niet voor een album")
    root = await session.scalar(select(User.root_id).where(User.id == album.owner_id))
    where = [*albums_router.live_items(album, root), *photos.page_where(after)]
    rows = (await session.execute(photos.photo_query(*where).limit(limit))).all()
    secret, exp = previews.url_key(settings), previews.expiry()
    return PublicPhotoPage(
        items=[PublicPhotoItem.model_construct(**photos.item_dict(r, secret, exp)) for r in rows],
        next=photos.next_cursor(rows, limit),
    )


# --- uploaden via een link (tus) ------------------------------------------------------


@router.options("/tus")
async def tus_options(settings: Annotated[Settings, Depends(get_settings)]) -> Response:
    return uploads.capabilities(settings)


@router.options("/tus/{upload_id}")
async def tus_options_upload(
    upload_id: uuid.UUID, settings: Annotated[Settings, Depends(get_settings)]
) -> Response:
    return uploads.capabilities(settings)


@router.post("/tus", status_code=201)
async def tus_create(
    request: Request,
    _: Tus,
    link: CurrentLink,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
    upload_length: Annotated[int, Header()],
    upload_metadata: Annotated[str | None, Header()] = None,
) -> Response:
    """Een upload naar de gedeelde map. Alleen filename telt (en lastmodified, voor de
    opnamedatum zoals in de app); bestaat de naam al, dan krijgt het bestand een vrije naam.
    Het kost het quotum van wie de map deelt."""
    uploadable(link)
    upload_limiter.check(client_ip(request))
    meta = uploads.parse_metadata(upload_metadata)
    name = clean_name(meta.get("filename", ""))
    uploads.check_length(settings, upload_length)
    owner = await session.get(User, link.share.owner_id)
    if owner is None:
        raise errors.fail(404, **LINK_NOT_FOUND)
    try:
        upload = await uploads.start(
            session,
            settings,
            owner,
            link.node,
            name,
            "rename",
            upload_length,
            created_by=None,
            share_id=link.share.id,
            guard=uploads.link_open(link.share.id, link.node.id),
            client_mtime=photodate.from_ms(meta.get("lastmodified")),
        )
    except Gone:
        # Terwijl dit wachtte, is de link weggehaald of verlopen, of de map weg
        raise errors.fail(404, **LINK_NOT_FOUND, headers=uploads._headers()) from None
    headers = uploads._headers(upload, Location=f"/api/v1/public/tus/{upload.id}")
    if upload_length == 0:
        await uploads.finish(session, settings, blobs, Uploader(link=link.share), upload)
    return Response(status_code=201, headers=headers)


@router.head("/tus/{upload_id}")
async def tus_head(
    upload_id: uuid.UUID,
    _: Tus,
    link: CurrentLink,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    uploadable(link)
    return await uploads.status(session, settings, blobs, Uploader(link=link.share), upload_id)


@router.patch("/tus/{upload_id}")
async def tus_patch(
    upload_id: uuid.UUID,
    request: Request,
    _: Tus,
    link: CurrentLink,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
    upload_offset: Annotated[int, Header()],
    content_type: Annotated[str | None, Header()] = None,
) -> Response:
    uploadable(link)
    who = Uploader(link=link.share)
    return await uploads.receive(
        session, settings, blobs, who, upload_id, request, upload_offset, content_type
    )


@router.delete("/tus/{upload_id}", status_code=204)
async def tus_delete(
    upload_id: uuid.UUID,
    _: Tus,
    link: CurrentLink,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    uploadable(link)
    return await uploads.cancel(session, settings, Uploader(link=link.share), upload_id)
