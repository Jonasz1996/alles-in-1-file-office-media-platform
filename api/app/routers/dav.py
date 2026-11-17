"""WebDAV voor de Nextcloud-clients, rclone en andere WebDAV-clients (klasse 1, zonder LOCK).

Adressen zoals bij Nextcloud: /remote.php/dav/files/<login>/<pad> en /remote.php/webdav/<pad>,
plus /remote.php/dav/uploads/<login>/<id>/ voor uploads in stukken (chunking v2). Inloggen met
een app-wachtwoord (app/nc/auth.py).

Alles gaat via dezelfde functies als /api/v1/files (new_folder, place_file, change, copy,
trash): quotum, versies, sloten, previews, zoeken en live updates werken dus precies zo.
Verwijderen gaat naar de prullenbak; vervangen houdt de vorige inhoud als versie.

Voor nu alleen de eigen bestanden van de gebruiker (zijn hoofdmap); wat met hem gedeeld is,
staat niet in de WebDAV-boom.
"""

import hashlib
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, unquote, urlparse
from xml.sax.saxutils import escape

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import Session
from app.models import Node, User
from app.nc import etags
from app.nc.auth import DavUser, app_password_user, matches
from app.routers import files
from app.routers.auth import used_bytes
from app.storage import CHUNK, BlobStore

router = APIRouter(include_in_schema=False)

METHODS = [
    "OPTIONS",
    "PROPFIND",
    "PROPPATCH",
    "GET",
    "HEAD",
    "PUT",
    "MKCOL",
    "DELETE",
    "MOVE",
    "COPY",
]
DAV_HEADERS = {
    "DAV": "1, 3",
    "MS-Author-Via": "DAV",
    "Allow": ", ".join(METHODS),
}
NS = (
    'xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns" '
    'xmlns:s="http://sabredav.org/ns"'
)
# Een upload in stukken die zo lang stilligt, wordt opgeruimd
STALE_UPLOAD = 24 * 3600

Settings_ = Annotated[Settings, Depends(get_settings)]


class DavError(Exception):
    def __init__(self, status: int, message: str = ""):
        self.status, self.message = status, message


def error_response(status: int, message: str) -> Response:
    body = (
        f'<?xml version="1.0" encoding="utf-8"?>\n<d:error {NS}>'
        f"<s:exception>DavError</s:exception><s:message>{escape(message)}</s:message></d:error>"
    )
    return Response(body, status_code=status, media_type="application/xml; charset=utf-8")


def from_http(e: HTTPException, *, exists: int = 412, missing: int = 404) -> DavError:
    detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail), "code": ""}
    code, message = detail.get("code", ""), detail.get("message", "")
    if e.status_code == 401:
        raise e
    if code in ("no_room", "no_room_owner", "too_large_or_quota"):
        return DavError(507, message)
    if code == "name_taken":
        return DavError(exists, message)
    if code in ("not_found", "folder_not_found"):
        return DavError(missing, message)
    if code == "invalid_name":
        return DavError(400, message)
    if code in ("root", "into_itself", "cross_owner", "album"):
        return DavError(403, message)
    return DavError(e.status_code, message)


# --- Paden ------------------------------------------------------------------------------------


def segments(path: str) -> list[str]:
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise DavError(400, "Ongeldig pad")
    return parts


async def walk(session: AsyncSession, user: User, parts: list[str]) -> Node | None:
    """De node op dit pad in de eigen boom (niets in de prullenbak), of None."""
    node = await session.get(Node, user.root_id) if user.root_id else None
    for name in parts:
        if node is None or node.kind != "dir":
            return None
        node = await files.sibling(session, node.id, name)
    return node


async def parent_and_name(session: AsyncSession, user: User, parts: list[str]) -> tuple[Node, str]:
    """De (bestaande) map waar het laatste deel in hoort, en die naam; 409 als die map er
    niet is (zo zegt WebDAV dat)."""
    if not parts:
        raise DavError(403, "De hoofdmap zelf kan dat niet")
    parent = await walk(session, user, parts[:-1])
    if parent is None or parent.kind != "dir":
        raise DavError(409, "De map erboven bestaat niet")
    return parent, parts[-1]


def destination_parts(request: Request, user: User) -> list[str]:
    raw = request.headers.get("destination")
    if not raw:
        raise DavError(400, "Destination ontbreekt")
    path = unquote(urlparse(raw).path)
    for prefix in (f"/remote.php/dav/files/{user.email}/", f"/remote.php/dav/files/{user.id}/"):
        if path.lower().startswith(prefix.lower()) or path.lower() + "/" == prefix.lower():
            return segments(path[len(prefix) :])
    if path.startswith("/remote.php/webdav"):
        return segments(path[len("/remote.php/webdav") :])
    raise DavError(502, "Destination ligt buiten deze server of deze gebruiker")


# --- PROPFIND ---------------------------------------------------------------------------------


def file_id(node_id: uuid.UUID) -> int:
    """Een vast getal per node (Nextcloud-clients verwachten een geheel getal)."""
    return int(node_id.hex[:15], 16)


def permissions(node: Node, root: bool) -> str:
    if root:
        return "GCK"
    return "RGDNVCK" if node.kind == "dir" else "RGDNVW"


def prop_xml(node: Node, href: str, user: User, root: bool, quota: tuple[int, int] | None) -> str:
    fid = file_id(node.id)
    modified = format_datetime(node.modified_at.astimezone(UTC), usegmt=True)
    if node.kind == "dir":
        etag = etags.dir_etag(node.id)
        kind = "<d:resourcetype><d:collection/></d:resourcetype>"
    else:
        etag = node.etag
        kind = (
            "<d:resourcetype/>"
            f"<d:getcontentlength>{node.size}</d:getcontentlength>"
            f"<d:getcontenttype>{escape(node.mime or 'application/octet-stream')}"
            "</d:getcontenttype>"
            f"<oc:size>{node.size}</oc:size>"
        )
    extra = ""
    if quota is not None:
        used, free = quota
        extra = (
            f"<d:quota-used-bytes>{used}</d:quota-used-bytes>"
            f"<d:quota-available-bytes>{free}</d:quota-available-bytes>"
        )
        if node.kind == "dir" and root:
            extra += f"<oc:size>{used}</oc:size>"
    return (
        f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>"
        f"<d:getlastmodified>{modified}</d:getlastmodified>"
        f"<d:getetag>{escape(etag)}</d:getetag>{kind}"
        f"<oc:id>{fid:08d}ocplatform</oc:id><oc:fileid>{fid}</oc:fileid>"
        f"<oc:permissions>{permissions(node, root)}</oc:permissions>"
        f"<oc:owner-id>{escape(user.email)}</oc:owner-id>"
        f"<oc:owner-display-name>{escape(user.display_name)}</oc:owner-display-name>"
        f"<oc:share-types/><nc:has-preview>false</nc:has-preview>{extra}"
        f"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


def multistatus(parts: list[str]) -> Response:
    body = (
        f'<?xml version="1.0" encoding="utf-8"?>\n<d:multistatus {NS}>{"".join(parts)}'
        "</d:multistatus>"
    )
    return Response(body, status_code=207, media_type="application/xml; charset=utf-8")


async def quota_of(session: AsyncSession, user: User) -> tuple[int, int]:
    used = await used_bytes(session, user.id)
    free = max(0, user.quota_bytes - used) if user.quota_bytes else -3  # -3: onbeperkt
    return used, free


async def propfind(
    request: Request, session: AsyncSession, user: User, base: str, parts: list[str]
) -> Response:
    depth = request.headers.get("depth", "1").strip().lower()
    if depth not in ("0", "1"):
        raise DavError(403, "Depth infinity kan niet; gebruik Depth 1")
    node = await walk(session, user, parts)
    if node is None:
        raise DavError(404, "Niet gevonden")
    await etags.flush(session)
    root = not parts
    href = base + "".join(quote(p) + "/" for p in parts)
    if node.kind != "dir":
        href = href.rstrip("/")
    out = [
        prop_xml(
            node, href, user, root, await quota_of(session, user) if node.kind == "dir" else None
        )
    ]
    if depth == "1" and node.kind == "dir":
        children = await session.scalars(
            select(Node)
            .where(
                Node.parent_id == node.id,
                Node.deleted_at.is_(None),
                Node.kind.in_(("dir", "file")),
            )
            .order_by(Node.kind, Node.name)
        )
        for child in children:
            child_href = href + quote(child.name) + ("/" if child.kind == "dir" else "")
            out.append(prop_xml(child, child_href, user, False, None))
    return multistatus(out)


# --- Schrijven --------------------------------------------------------------------------------


def client_mtime(request: Request) -> datetime | None:
    raw = request.headers.get("x-oc-mtime")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(float(raw), UTC)
    except (ValueError, OverflowError, OSError):
        return None


async def set_mtime(session: AsyncSession, node_id: uuid.UUID, mtime: datetime | None) -> Node:
    node = await session.get(Node, node_id, populate_existing=True)
    if mtime is not None and node is not None:
        node.modified_at = mtime
        await session.commit()
        node = await session.get(Node, node_id, populate_existing=True)
    return node


def written(node: Node, created: bool, mtime: datetime | None) -> Response:
    headers = {
        "ETag": node.etag,
        "OC-ETag": node.etag,
        "OC-FileId": f"{file_id(node.id):08d}ocplatform",
    }
    if mtime is not None:
        headers["X-OC-MTime"] = "accepted"
    return Response(status_code=201 if created else 204, headers=headers)


async def place(
    session: AsyncSession,
    settings: Settings,
    user: User,
    parts: list[str],
    tmp: Path,
    sha: str,
    size: int,
    mtime: datetime | None,
) -> Response:
    blobs = BlobStore(settings.blob_dir)
    parent, name = await parent_and_name(session, user, parts)
    existing = await files.sibling(session, parent.id, name)
    if existing is not None and existing.kind == "dir":
        raise DavError(405, "Daar staat een map")
    node = await files.place_file(
        session,
        blobs,
        user,
        parent,
        files.clean_name(name),
        "replace",
        sha,
        size,
        tmp,
        actor=user,
        client_mtime=mtime,
        tz=settings.timezone,
    )
    node = await set_mtime(session, node.id, mtime)
    return written(node, existing is None, mtime)


async def put(
    request: Request, session: AsyncSession, settings: Settings, user: User, parts: list[str]
) -> Response:
    parent, name = await parent_and_name(session, user, parts)
    files.clean_name(name)
    existing = await files.sibling(session, parent.id, name)
    if existing is not None and existing.kind == "dir":
        raise DavError(405, "Daar staat een map")
    length = request.headers.get("content-length")
    blobs = BlobStore(settings.blob_dir)
    tmp, sha, size = await files.receive_body(
        session, settings, blobs, request, user, user, int(length) if length else None
    )
    try:
        return await place(session, settings, user, parts, tmp, sha, size, client_mtime(request))
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)


async def trash_node(session: AsyncSession, user: User, node: Node) -> None:
    await files.trash(node.id, user, session)


async def move_or_copy(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    user: User,
    parts: list[str],
    copy: bool,
) -> Response:
    source = await walk(session, user, parts)
    if source is None:
        raise DavError(404, "Niet gevonden")
    if not parts:
        raise DavError(403, "De hoofdmap kan niet")
    target = destination_parts(request, user)
    parent, name = await parent_and_name(session, user, target)
    files.clean_name(name)
    overwrite = request.headers.get("overwrite", "T").strip().upper() != "F"
    existing = await files.sibling(session, parent.id, name)
    replaced = existing is not None and existing.id != source.id
    if replaced:
        if not overwrite:
            raise DavError(412, "Bestaat al")
        await trash_node(session, user, existing)
    blobs = BlobStore(settings.blob_dir)
    if copy:
        await files.copy(
            source.id, files.CopyTo(parent_id=parent.id, name=name), user, session, blobs
        )
    else:
        change = files.Change(
            name=name if name != source.name else None,
            parent_id=parent.id if parent.id != source.parent_id else None,
        )
        if change.name is not None or change.parent_id is not None:
            await files.change(source.id, change, user, session, blobs)
    return Response(status_code=204 if replaced else 201)


async def handle(
    request: Request, session: AsyncSession, settings: Settings, user: User, base: str, path: str
) -> Response:
    method = request.method.upper()
    parts = segments(path)
    if method == "PROPFIND":
        return await propfind(request, session, user, base, parts)
    if method == "PROPPATCH":
        node = await walk(session, user, parts)
        if node is None:
            raise DavError(404, "Niet gevonden")
        href = base + "/".join(quote(p) for p in parts)
        return multistatus(
            [
                f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop/>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            ]
        )
    if method in ("GET", "HEAD"):
        node = await walk(session, user, parts)
        if node is None:
            raise DavError(404, "Niet gevonden")
        if node.kind == "dir":
            return Response("Dit is een WebDAV-map.\n", media_type="text/plain")
        await session.close()
        return await files.send_file(node, settings, request.headers.get("if-none-match"), False)
    if method == "PUT":
        return await put(request, session, settings, user, parts)
    if method == "MKCOL":
        parent, name = await parent_and_name(session, user, parts)
        if await files.sibling(session, parent.id, name) is not None:
            raise DavError(405, "Bestaat al")
        await files.new_folder(parent.id, files.NewFolder(name=name), user, session)
        return Response(status_code=201)
    if method == "DELETE":
        node = await walk(session, user, parts)
        if node is None:
            raise DavError(404, "Niet gevonden")
        if not parts:
            raise DavError(403, "De hoofdmap kan niet weg")
        await trash_node(session, user, node)
        return Response(status_code=204)
    if method in ("MOVE", "COPY"):
        return await move_or_copy(request, session, settings, user, parts, method == "COPY")
    raise DavError(405, "Kan niet")


async def run(coro_factory) -> Response:
    try:
        return await coro_factory()
    except DavError as e:
        return error_response(e.status, e.message)
    except HTTPException as e:
        if e.status_code == 401:
            raise
        err = from_http(e)
        return error_response(err.status, err.message)


def options() -> Response:
    return Response(status_code=200, headers=DAV_HEADERS)


@router.api_route("/remote.php/dav/files/{login}", methods=METHODS)
@router.api_route("/remote.php/dav/files/{login}/{path:path}", methods=METHODS)
async def dav_files(
    request: Request, login: str, session: Session, settings: Settings_, path: str = ""
) -> Response:
    if request.method == "OPTIONS":
        return options()

    user = await app_password_user(request, session)
    if not matches(user, login):
        return error_response(403, "Dit is niet jouw map")
    base = f"/remote.php/dav/files/{quote(login)}/"
    return await run(lambda: handle(request, session, settings, user, base, path))


@router.api_route("/remote.php/webdav", methods=METHODS)
@router.api_route("/remote.php/webdav/{path:path}", methods=METHODS)
async def dav_legacy(
    request: Request, session: Session, settings: Settings_, path: str = ""
) -> Response:
    if request.method == "OPTIONS":
        return options()

    user = await app_password_user(request, session)
    return await run(lambda: handle(request, session, settings, user, "/remote.php/webdav/", path))


@router.api_route("/remote.php/dav", methods=METHODS)
@router.api_route("/remote.php/dav/", methods=METHODS)
async def dav_root(request: Request, user: DavUser) -> Response:
    if request.method == "OPTIONS":
        return options()
    if request.method != "PROPFIND":
        return error_response(405, "Kan niet")
    return multistatus(
        [
            "<d:response><d:href>/remote.php/dav/</d:href><d:propstat><d:prop>"
            "<d:resourcetype><d:collection/></d:resourcetype></d:prop>"
            "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        ]
    )


# --- Uploads in stukken (Nextcloud chunking v2) -----------------------------------------------


def upload_root(settings: Settings, user: User) -> Path:
    return settings.data_dir / "dav-uploads" / user.id.hex


def clean_part(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name or len(name) > 120:
        raise DavError(400, "Ongeldige naam")
    return name


def sweep(root: Path) -> None:
    """Uploads in stukken die een dag stilliggen, weg."""
    if not root.parent.is_dir():
        return
    limit = time.time() - STALE_UPLOAD
    for user_dir in root.parent.iterdir():
        for transfer in user_dir.iterdir() if user_dir.is_dir() else ():
            try:
                if transfer.stat().st_mtime < limit:
                    shutil.rmtree(transfer, ignore_errors=True)
            except FileNotFoundError:
                pass


def chunk_order(path: Path) -> tuple[int, int | str]:
    return (0, int(path.name)) if path.name.isdigit() else (1, path.name)


def assemble(transfer: Path, tmp_dir: Path) -> tuple[Path, str, int]:
    """Alle stukken achter elkaar in één tijdelijk bestand naast de blob-opslag (zodat adopt
    het alleen hoeft te hernoemen), met de SHA-256 erbij."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=tmp_dir)
    digest, size = hashlib.sha256(), 0
    try:
        with open(fd, "wb") as out:
            for chunk in sorted((p for p in transfer.iterdir() if p.is_file()), key=chunk_order):
                with open(chunk, "rb") as f:
                    while data := f.read(CHUNK):
                        digest.update(data)
                        size += len(data)
                        out.write(data)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name), digest.hexdigest(), size


async def uploads(
    request: Request, session: AsyncSession, settings: Settings, user: User, path: str
) -> Response:
    method = request.method.upper()
    parts = [p for p in path.split("/") if p]
    root = upload_root(settings, user)
    if not parts:
        if method == "PROPFIND":
            href = f"/remote.php/dav/uploads/{quote(user.email)}/"
            return multistatus(
                [
                    f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>"
                    "<d:resourcetype><d:collection/></d:resourcetype></d:prop>"
                    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                ]
            )
        raise DavError(405, "Kan niet")
    transfer = root / clean_part(parts[0])
    if len(parts) == 1:
        if method == "MKCOL":
            await anyio.to_thread.run_sync(sweep, root)
            if transfer.exists():
                raise DavError(405, "Bestaat al")
            transfer.mkdir(parents=True)
            return Response(status_code=201)
        if method == "DELETE":
            await anyio.to_thread.run_sync(lambda: shutil.rmtree(transfer, ignore_errors=True))
            return Response(status_code=204)
        if method == "PROPFIND":
            if not transfer.is_dir():
                raise DavError(404, "Niet gevonden")
            href = f"/remote.php/dav/uploads/{quote(user.email)}/{quote(parts[0])}/"
            out = [
                f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>"
                "<d:resourcetype><d:collection/></d:resourcetype></d:prop>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            ]
            if request.headers.get("depth", "1") != "0":
                for chunk in sorted(transfer.iterdir(), key=chunk_order):
                    out.append(
                        f"<d:response><d:href>{escape(href + quote(chunk.name))}</d:href>"
                        f"<d:propstat><d:prop><d:resourcetype/><d:getcontentlength>"
                        f"{chunk.stat().st_size}</d:getcontentlength></d:prop>"
                        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                    )
            return multistatus(out)
        raise DavError(405, "Kan niet")
    if len(parts) != 2:
        raise DavError(404, "Niet gevonden")
    if not transfer.is_dir():
        raise DavError(404, "Upload niet gevonden; begin opnieuw")
    name = clean_part(parts[1])
    if method == "PUT":
        room = await files.room_left(session, user)
        await session.commit()
        so_far = sum(p.stat().st_size for p in transfer.iterdir() if p.is_file())
        limit = None if room is None else room - so_far
        target = transfer / name
        part = transfer / f".{name}.part"
        size = 0
        async with await anyio.open_file(part, "wb") as f:
            async for data in request.stream():
                size += len(data)
                if limit is not None and size > limit:
                    await anyio.Path(part).unlink(missing_ok=True)
                    raise DavError(507, "Onvoldoende quotum")
                await f.write(data)
        part.rename(target)
        transfer.touch()
        return Response(status_code=201)
    if method == "MOVE" and name == ".file":
        target = destination_parts(request, user)
        total = request.headers.get("oc-total-length")
        tmp, sha, size = await anyio.to_thread.run_sync(
            assemble, transfer, BlobStore(settings.blob_dir).tmp
        )
        try:
            if total and total.isdigit() and int(total) != size:
                raise DavError(400, "De stukken samen zijn niet zo groot als OC-Total-Length")
            room = await files.room_left(session, user)
            if room is not None and size > room:
                raise DavError(507, "Onvoldoende quotum")
            response = await place(
                session, settings, user, target, tmp, sha, size, client_mtime(request)
            )
        finally:
            await anyio.Path(tmp).unlink(missing_ok=True)
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(transfer, ignore_errors=True))
        return response
    if method == "DELETE":
        (transfer / name).unlink(missing_ok=True)
        return Response(status_code=204)
    raise DavError(405, "Kan niet")


@router.api_route("/remote.php/dav/uploads/{login}", methods=METHODS)
@router.api_route("/remote.php/dav/uploads/{login}/{path:path}", methods=METHODS)
async def dav_uploads(
    request: Request, login: str, session: Session, settings: Settings_, path: str = ""
) -> Response:
    if request.method == "OPTIONS":
        return options()

    user = await app_password_user(request, session)
    if not matches(user, login):
        return error_response(403, "Dit is niet jouw map")
    return await run(lambda: uploads(request, session, settings, user, path))
