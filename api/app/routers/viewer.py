"""De viewer: alles over één bestand dat een lijst niet nodig heeft, en de pagina's van een PDF
als afbeelding, gerenderd op de server (B20).

- GET /files/{id}/preview?version= -> ViewerDetail: de volledige metadata (tekst, archief,
  codecs, EXIF; nooit een locatie), plus display (2048 px) en taken_at;
- GET /files/{id}/document?version= -> de pagina's van een PDF (groottes, inhoudsopgave,
  labels) of de inhoud van een kantoordocument (OfficeDoc uit doc.json), met een adres per
  pagina dat een download-token bevat;
- GET /files/{id}/pages/{n}.webp?w=&token= -> pagina n, w pixels breed. Bij elk verzoek
  opnieuw: het token (security.active_user: een geblokkeerde gebruiker krijgt 404) en de
  toegang (files.get_node), zodat een ingetrokken deling meteen stopt. Gerenderd wordt in het
  renderproces (app/render.py, mode "page"), n, n+1 en n+2 tegelijk, met hooguit
  settings.page_render_workers processen tegelijk; de pagina's blijven in
  previews/<sha>/pages/<w>/<n>.webp tot de cache boven settings.page_cache_mb komt (de
  langst niet gebruikte gaan eerst).
- dezelfde drie onder /public/ voor een publieke leeslink (met de sleutel van de link);
- een kantoordocument met een omgezette PDF van office (fase 4, Keuze 25): /document geeft
  kind "pdf" met source "office", en de pagina's komen uit die PDF
  (data_dir/office/pdf/<sha[:2]>/<sha>.pdf, app/office/convert.py). De paginacache blijft
  previews/<sha>/pages/, maar de ETag en info.json dragen de pdf_version: een nieuwe PDF telt
  de pagina's opnieuw. Zonder PDF blijft het de inhoud uit doc.json (B21). /document en
  /preview noemen ook pdf_state (/preview als meta.pdf_state), alleen met office aan of met
  een PDF die er is: zonder office is alles precies fase 3. Een eerdere versie zonder PDF
  wordt nooit vanzelf omgezet; dan zegt pdf_state "ask";
- GET /public/listing?folder= -> een map binnen de link met de previews van zijn bestanden in
  één antwoord (GET /public/children plus POST /public/previews): op een mobiel netwerk kost
  elke rondgang meer dan een halve seconde.
"""

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import time
import uuid
import weakref
from pathlib import Path
from typing import Annotated, Any

import anyio
import anyio.to_thread
import jwt
from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import errors, jobs, previews, render
from app.config import Settings, get_settings
from app.db import Session, get_engine
from app.models import Blob, Node
from app.office import convert as office_convert
from app.routers import files, public
from app.routers.files import Utc
from app.routers.previews import MAX_NODES, PreviewOut, PreviewRequest
from app.routers.public import CurrentLink, PublicListing, inside, readable
from app.security import CurrentUser, active_user, download_token, read_download_token
from app.storage import BlobStore

router = APIRouter(prefix="/api/v1", tags=["viewer"])
_bearer = HTTPBearer(auto_error=False)

WIDTHS = render.PAGE_WIDTHS
AHEAD = 2  # na pagina n ook n+1 en n+2 in hetzelfde proces
PAGE_SECONDS = 60
PAGE_CACHE = "private, max-age=600"
INFO = "info.json"
NOT_A_DOCUMENT = {
    "message": "Dit bestand heeft geen pagina's om te tonen",
    "code": "not_a_document",
}


class ViewerDetail(BaseModel):
    # pending, ready, none (geen preview mogelijk) of failed
    state: str
    thumb: str | None
    large: str | None
    # 2048 px, alleen voor wat de browser zelf niet kan tonen (HEIC, HEIF, TIFF)
    display: str | None
    meta: dict[str, Any]
    # De opnamedatum als kloktijd (zonder tijdzone bedoeld, opgeslagen als UTC)
    taken_at: Utc | None


class TocEntry(BaseModel):
    title: str
    page: int
    level: int


class DocumentOut(BaseModel):
    kind: str  # pdf of office
    # Waar de pagina's vandaan komen: "pdf" (het bestand zelf), "office" (de omgezette PDF van
    # een kantoordocument) of None (geen pagina's; kind office toont doc.json)
    source: str | None = None
    # De omgezette PDF (Keuze 25): pending, ready, failed, none, unsafe, ask, of None (geen
    # office, of een soort die de editor niet omzet)
    pdf_state: str | None = None
    pages: int | None
    # Per pagina [breedte, hoogte] in punten
    page_sizes: list[list[float]] | None
    toc: list[TocEntry]
    labels: list[str] | None
    widths: list[int]
    # Met {n} en {w} in te vullen
    page_url: str | None
    # Zo lang (seconden) werkt page_url; daarna opnieuw /document vragen
    token_expires_in: int
    office: dict[str, Any] | None


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def _blobs(settings: Settings) -> BlobStore:
    return BlobStore(settings.blob_dir)


# --- welke inhoud ---------------------------------------------------------------------------


async def _content(
    session: AsyncSession, node: Node, version_id: uuid.UUID | None
) -> tuple[str, Any]:
    """(sha, taken_at) van het bestand zelf of van een eerdere versie ervan."""
    if node.kind != "file" or not node.blob_id:
        raise errors.fail(422, "not_a_file", "Alleen bestanden kunnen worden bekeken")
    if version_id is None:
        return node.blob_id, node.taken_at
    version = await files.get_version(session, node, version_id)
    blob = await session.get(Blob, version.blob_id)
    return version.blob_id, blob.taken_at if blob is not None else None


async def _kind(settings: Settings, blob: Blob | None, sha: str, name: str) -> str:
    """Wat de viewer als document toont: "pdf", "office" of "" (zoals render.kind_of)."""
    path = _blobs(settings).path(sha)

    def head() -> bytes | None:
        try:
            with open(path, "rb") as f:
                return f.read(render.HEAD)
        except FileNotFoundError:
            return None

    data = await anyio.to_thread.run_sync(head)
    if data is None:
        return ""  # de inhoud is (net) opgeruimd
    kind = render.kind_of(blob.mime if blob else None, data, name)
    return kind if kind in ("pdf", "office") else ""


# --- het detail van één bestand -------------------------------------------------------------


async def _detail(
    session: AsyncSession, settings: Settings, sha: str, taken_at: Any, version: bool = False
) -> ViewerDetail:
    blob = await session.get(Blob, sha)
    data = previews.info(previews.url_key(settings), blob, previews.expiry(), full=True)
    ready = await office_convert.office_pdf(settings, blob) is not None
    state = office_convert.shown_state(blob, ready, version)
    if state is not None:  # alleen met office (of een PDF die er is): anders precies fase 3
        data["meta"] = {**data["meta"], "pdf_state": state}
    return ViewerDetail(**data, taken_at=taken_at)


@router.get("/files/{node_id}/preview")
async def preview_detail(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    version: uuid.UUID | None = None,
) -> ViewerDetail:
    """Alles wat de viewer over dit bestand (of een eerdere versie ervan) wil weten. Een
    lijst krijgt via POST /previews alleen het lichte deel."""
    node = await files.get_node(session, user, node_id, "read")
    sha, taken_at = await _content(session, node, version)
    return await _detail(session, settings, sha, taken_at, version is not None)


# --- PDF: gegevens van het document ---------------------------------------------------------


def _pages_dir(blobs: BlobStore, sha: str) -> Path:
    return previews.directory(blobs, sha) / "pages"


def page_path(blobs: BlobStore, sha: str, width: int, n: int) -> Path:
    return _pages_dir(blobs, sha) / str(width) / f"{n}.webp"


async def _read_json(path: Path) -> dict[str, Any] | None:
    def load() -> dict[str, Any] | None:
        try:
            data = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return None
        try:
            value = json.loads(data)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    return await anyio.to_thread.run_sync(load)


# Per event loop (de tests starten er telkens een nieuwe) en per aantal
_limiters: weakref.WeakKeyDictionary[Any, dict[int, anyio.CapacityLimiter]] = (
    weakref.WeakKeyDictionary()
)


def limiter(settings: Settings) -> anyio.CapacityLimiter:
    """Hooguit settings.page_render_workers renderprocessen tegelijk voor de viewer."""
    workers = max(1, settings.page_render_workers)
    per_loop = _limiters.setdefault(asyncio.get_running_loop(), {})
    if workers not in per_loop:
        per_loop[workers] = anyio.CapacityLimiter(workers)
    return per_loop[workers]


async def _render(settings: Settings, request: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    """Het renderproces in een eigen werkmap (onder previews/.tmp); geeft (antwoord, werkmap).
    De aanroeper ruimt de werkmap op."""
    blobs = _blobs(settings)
    tmp = previews.root(blobs) / ".tmp"
    await anyio.Path(tmp).mkdir(parents=True, exist_ok=True)
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=tmp)))
    try:
        out, scratch = work / "out", work / "work"
        await anyio.Path(out).mkdir()
        await anyio.Path(scratch).mkdir()
        request = {
            **request,
            "out": str(out),
            "work": str(scratch),
            "max_pixels": settings.preview_max_pixels,
            "memory_mb": settings.preview_memory_mb,
        }
        try:
            with anyio.fail_after(PAGE_SECONDS):
                result = await previews.render(settings, request, scratch)
        except (jobs.JobError, TimeoutError) as e:
            raise errors.fail(
                503, "render_failed", "Deze pagina kon nu niet worden gemaakt; probeer het opnieuw"
            ) from e
        return result, work
    except BaseException:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
        raise


async def _place(settings: Settings, sha: str, moves: list[tuple[Path, Path]], v: int = 0) -> bool:
    """Zet gerenderde bestanden op hun plek (tijdelijk bestand, dan hernoemen), onder het slot
    van de blobs en alleen als de inhoud er nog is: anders bleef er voor altijd iets staan.
    v: gerenderd uit de omgezette PDF met deze pdf_version. Kwam er intussen een andere PDF
    (die leegde pages/), dan niets neerzetten."""
    blobs = _blobs(settings)
    async with blobs.lock:
        async with _maker(settings)() as session:
            blob = await session.get(Blob, sha)
            if blob is None:
                return False
            if v and previews.pdf_generation(blob) != v:
                return False

        def move() -> None:
            for source, target in moves:
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)

        await anyio.to_thread.run_sync(move)
    return True


def _fresh(cached: dict[str, Any] | None, v: int) -> dict[str, Any] | None:
    """info.json alleen als hij bij deze PDF hoort (v: de pdf_version, 0 voor het bestand)."""
    if cached is None or int(cached.get("v") or 0) != v:
        return None
    return cached


async def docinfo(
    settings: Settings, sha: str, source: Path | None = None, v: int = 0
) -> dict[str, Any] | None:
    """Pagina's, groottes, inhoudsopgave en labels van een PDF; de eerste keer uit het
    renderproces, daarna uit previews/<sha>/pages/info.json. None als het geen leesbare PDF
    is. source en v: de omgezette PDF van office en zijn pdf_version (anders de blob zelf)."""
    blobs = _blobs(settings)
    path = _pages_dir(blobs, sha) / INFO
    cached = _fresh(await _read_json(path), v)
    if cached is not None:
        return cached if cached.get("state") == "ready" else None
    async with limiter(settings):
        cached = _fresh(await _read_json(path), v)  # intussen door een ander verzoek gemaakt
        if cached is None:
            pdf = source or blobs.path(sha)
            result, work = await _render(settings, {"mode": "docinfo", "source": str(pdf)})
            if v:
                result["v"] = v
            try:
                tmp = work / INFO
                await anyio.Path(tmp).write_text(json.dumps(result))
                await _place(settings, sha, [(tmp, path)], v)
            finally:
                await anyio.to_thread.run_sync(shutil.rmtree, work, True)
            cached = result
    return cached if cached.get("state") == "ready" else None


async def _office(settings: Settings, blob: Blob | None, sha: str) -> dict[str, Any] | None:
    if blob is None or blob.preview != "ready":
        return None
    return await _read_json(previews.doc_path(_blobs(settings), sha))


async def _source(
    settings: Settings, blob: Blob | None, sha: str, name: str
) -> tuple[str, Path | None, int]:
    """Wat de viewer toont: ("pdf", pad, v) voor pagina's (de omgezette PDF van office met
    zijn pdf_version; pad None en v 0 voor het bestand zelf), ("office", None, 0) voor
    doc.json, of ("", None, 0)."""
    converted = await office_convert.office_pdf(settings, blob)
    if converted is not None:
        return "pdf", converted[0], converted[1]
    return await _kind(settings, blob, sha, name), None, 0


async def _document(
    session: AsyncSession,
    settings: Settings,
    sha: str,
    name: str,
    page_url: str,
    expires_in: int,
    version: bool = False,
) -> DocumentOut:
    blob = await session.get(Blob, sha)
    kind, source, v = await _source(settings, blob, sha, name)
    state = office_convert.shown_state(blob, source is not None, version)
    if not kind and state is not None:
        kind = "office"  # een doc, xls, ppt of rtf zonder PDF: (nog) niets te tonen
    if not kind:
        raise errors.fail(422, **NOT_A_DOCUMENT)
    await session.commit()  # niet met een databaseverbinding op het renderproces wachten
    if kind == "office":
        return DocumentOut(
            kind="office",
            pdf_state=state,
            pages=None,
            page_sizes=None,
            toc=[],
            labels=None,
            widths=list(WIDTHS),
            page_url=None,
            token_expires_in=expires_in,
            office=await _office(settings, blob, sha),
        )
    found = await docinfo(settings, sha, source, v)
    if found is None:
        raise errors.fail(422, **NOT_A_DOCUMENT)
    return DocumentOut(
        kind="pdf",
        source="office" if source is not None else "pdf",
        pdf_state=state,
        pages=found["pages"],
        page_sizes=found["page_sizes"],
        toc=[TocEntry(**entry) for entry in found.get("toc") or []],
        labels=found.get("labels"),
        widths=list(WIDTHS),
        page_url=page_url,
        token_expires_in=expires_in,
        office=None,
    )


@router.get("/files/{node_id}/document")
async def document(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    version: uuid.UUID | None = None,
) -> DocumentOut:
    """De pagina's van een PDF of de inhoud van een kantoordocument. page_url bevat een
    download-token dat token_expires_in seconden werkt; de viewer vraagt dit daarna opnieuw."""
    node = await files.get_node(session, user, node_id, "read")
    sha, _ = await _content(session, node, version)
    token = download_token(settings, user.id, node.id, version)
    url = f"/api/v1/files/{node.id}/pages/{{n}}.webp?w={{w}}&token={token}"
    expires = max(0, settings.download_token_minutes * 60)
    return await _document(session, settings, sha, node.name, url, expires, version is not None)


# --- PDF: één pagina ------------------------------------------------------------------------


def _etag(sha: str, n: int, width: int, v: int = 0) -> str:
    """v: de pdf_version van de omgezette PDF (0: het bestand zelf, de ETag van fase 3)."""
    return f'"{sha}-{n}-{width}-p{v}"' if v else f'"{sha}-{n}-{width}"'


def _matches(if_none_match: str | None, etag: str) -> bool:
    return bool(if_none_match) and etag in [t.strip() for t in (if_none_match or "").split(",")]


def _check_width(width: int) -> None:
    if width not in WIDTHS:
        raise errors.fail(
            422,
            "invalid_width",
            "Deze breedte bestaat niet; kies 640, 1280, 1920 of 2560",
            widths=list(WIDTHS),
        )


async def _render_pages(
    settings: Settings,
    sha: str,
    n: int,
    width: int,
    count: int,
    source: Path | None = None,
    v: int = 0,
) -> None:
    blobs = _blobs(settings)
    target = page_path(blobs, sha, width, n)
    async with limiter(settings):
        if await anyio.Path(target).is_file():
            return  # een ander verzoek renderde hem net
        wanted = []
        for k in range(n, min(count, n + AHEAD) + 1):
            if k == n or not await anyio.Path(page_path(blobs, sha, width, k)).is_file():
                wanted.append(k)
        pdf = source or blobs.path(sha)
        result, work = await _render(
            settings,
            {"mode": "page", "source": str(pdf), "pages": wanted, "width": width},
        )
        try:
            done = [int(k) for k in result.get("pages") or [] if int(k) in wanted]
            moves = [(work / "out" / f"{k}.webp", page_path(blobs, sha, width, k)) for k in done]
            await _place(settings, sha, moves, v)
        finally:
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
    await evict(settings, keep={target})


def _scan(root: Path) -> list[tuple[float, int, Path]]:
    found = []
    for path in root.glob("*/*/pages/*/*.webp"):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        found.append((stat.st_mtime, stat.st_size, path))
    return found


async def evict(settings: Settings, keep: frozenset[Path] | set[Path] = frozenset()) -> int:
    """Ruimt gerenderde pagina's op tot de cache onder settings.page_cache_mb zit, de langst
    niet gebruikte eerst (een pagina wordt bij elk verzoek "aangeraakt"). Geeft het aantal
    weggegooide pagina's."""
    root = previews.root(_blobs(settings))
    limit = max(0, settings.page_cache_mb) * 1024 * 1024

    def run() -> int:
        found = sorted(_scan(root))
        total = sum(size for _, size, _ in found)
        removed = 0
        for _, size, path in found:
            if total <= limit:
                break
            if path in keep:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            total -= size
            removed += 1
        return removed

    return await anyio.to_thread.run_sync(run)


async def _page(
    session: AsyncSession,
    settings: Settings,
    sha: str,
    name: str,
    n: int,
    width: int,
    if_none_match: str | None,
) -> Response:
    _check_width(width)
    blob = await session.get(Blob, sha)
    kind, source, v = await _source(settings, blob, sha, name)
    if kind != "pdf":
        raise errors.fail(422, **NOT_A_DOCUMENT)
    await session.commit()  # niet met een databaseverbinding op het renderproces wachten
    found = await docinfo(settings, sha, source, v)
    if found is None:
        raise errors.fail(422, **NOT_A_DOCUMENT)
    if not 1 <= n <= found["pages"]:
        raise errors.fail(404, "page_not_found", "Deze pagina bestaat niet", pages=found["pages"])
    etag = _etag(sha, n, width, v)
    headers = {"ETag": etag, "Cache-Control": PAGE_CACHE}
    if _matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    path = page_path(_blobs(settings), sha, width, n)
    if not await anyio.Path(path).is_file():
        await _render_pages(settings, sha, n, width, found["pages"], source, v)

    def load() -> bytes | None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        with contextlib.suppress(FileNotFoundError):
            os.utime(path)  # net gebruikt: als laatste weg bij het opruimen
        return data

    data = await anyio.to_thread.run_sync(load)
    if data is None:
        raise errors.fail(404, "no_preview", "Geen preview")
    return Response(content=data, media_type="image/webp", headers=headers)


@router.get("/files/{node_id}/pages/{n}.webp")
async def page(
    node_id: uuid.UUID,
    n: int,
    token: str,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    w: int = 1280,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Pagina n (vanaf 1) van een PDF als WebP van w pixels breed. Het token komt uit
    GET /document; de toegang wordt bij elk verzoek opnieuw gecontroleerd."""
    user_id, token_node, version_id = read_download_token(settings, token)
    if token_node != node_id:
        raise errors.fail(403, "wrong_token", "Token hoort bij een ander bestand")
    # Weg of geblokkeerd: 404, ook met een token dat al uit was
    user = await active_user(session, user_id)
    # Opnieuw: een deling die intussen is ingetrokken, stopt ook een token dat al uit was
    node = await files.get_node(session, user, node_id, "read")
    sha, _ = await _content(session, node, version_id)
    return await _page(session, settings, sha, node.name, n, w, if_none_match)


# --- hetzelfde via een publieke link --------------------------------------------------------


async def _public_file(session: AsyncSession, link: Any, node_id: uuid.UUID) -> Node:
    readable(link)
    if node_id not in await inside(session, link, [node_id]):
        raise errors.fail(404, "not_found", "Niet gevonden")
    node = await session.get(Node, node_id)
    if node is None or node.kind != "file" or not node.blob_id:
        raise errors.fail(404, "not_found", "Niet gevonden")
    return node


def _raw_key(creds: HTTPAuthorizationCredentials | None, key: str | None) -> str:
    return creds.credentials if creds is not None else key or ""


def _key_left(raw: str) -> int:
    """Hoe lang (seconden) de sleutel van de link nog werkt. Hij is al gecontroleerd
    (current_link); hier alleen de vervaltijd."""
    try:
        claims = jwt.decode(raw, options={"verify_signature": False})
        return max(0, int(claims.get("exp", 0) - time.time()))
    except (jwt.PyJWTError, TypeError, ValueError):
        return 0


@router.get("/public/preview/{node_id}")
async def public_preview(
    node_id: uuid.UUID,
    link: CurrentLink,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ViewerDetail:
    """Zoals GET /files/{id}/preview, voor een bestand binnen een publieke leeslink."""
    node = await _public_file(session, link, node_id)
    return await _detail(session, settings, node.blob_id or "", node.taken_at)


@router.get("/public/document/{node_id}")
async def public_document(
    node_id: uuid.UUID,
    link: CurrentLink,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    key: Annotated[str | None, Query(max_length=2000)] = None,
) -> DocumentOut:
    """Zoals GET /files/{id}/document; page_url bevat de sleutel van de link, en
    token_expires_in is hoe lang die nog werkt."""
    node = await _public_file(session, link, node_id)
    raw = _raw_key(creds, key)
    url = f"/api/v1/public/pages/{node.id}/{{n}}.webp?w={{w}}&key={raw}"
    return await _document(session, settings, node.blob_id or "", node.name, url, _key_left(raw))


@router.get("/public/pages/{node_id}/{n}.webp")
async def public_page(
    node_id: uuid.UUID,
    n: int,
    link: CurrentLink,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    w: int = 1280,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Een pagina via een publieke link (?key=); bij elk verzoek opnieuw: de link, de
    prullenbak en of het bestand binnen de gedeelde map ligt."""
    node = await _public_file(session, link, node_id)
    return await _page(session, settings, node.blob_id or "", node.name, n, w, if_none_match)


class PublicFolder(PublicListing):
    # Zoals POST /public/previews, voor de bestanden onder de eerste `previews` items van
    # children (wat de pagina meteen toont); de rest vraagt de pagina los
    previews: dict[str, PreviewOut]


@router.get("/public/listing")
async def public_listing(
    link: CurrentLink,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    folder: uuid.UUID | None = None,
    previews: Annotated[int, Query(ge=0, le=MAX_NODES)] = MAX_NODES,
) -> PublicFolder:
    """GET /public/children en POST /public/previews in één rondgang: de pagina van de link
    toont de lijst meteen met thumbnails. Dezelfde controles en antwoorden als die twee.
    previews: voor hoeveel items van boven (mappen tellen mee) de previews meekomen; de
    adressen erin zijn ondertekend, dus dit antwoord wordt niet gecomprimeerd en blijft
    zo klein als de pagina het nodig heeft."""
    listing = await public.children(link, session, folder)
    ids = [n.id for n in listing.children[:previews] if n.kind == "file"]
    shown = (
        await public.preview_info(PreviewRequest(node_ids=ids), link, session, settings)
        if ids
        else {}
    )
    return PublicFolder(
        folder=listing.folder, path=listing.path, children=listing.children, previews=shown
    )
