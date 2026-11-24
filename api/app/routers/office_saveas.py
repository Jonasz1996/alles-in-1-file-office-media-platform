"""Een kopie in een ander formaat: "Opslaan als" in de editor en "Omzetten" bij hernoemen.

POST /api/v1/office/files/{id}/save-as {key, url, file_type, title} -> 201 NodeOut
    "Kopie opslaan als" in de editor (het event onRequestSaveAs van DS). DS zette het document
    al om naar file_type en geeft een adres op de host van de browser; de app neemt daarvan
    alleen pad en query, en het pad moet bij de key passen (urls.rewrite, soort saveas: na de
    key nog een eigen stuk van DS en als laatste de titel, met veilige procentcodes). key moet
    de open sessie van dit bestand zijn waar deze gebruiker nu met schrijfrecht in zit
    (office._my_session, pending of open); anders 404. De kopie komt naast het origineel, in
    dezelfde map, met de naam van title (de extensie van file_type erbij als die ontbreekt) en
    conflict rename. Schrijfrecht op die map is nodig (403 read_only); de grens is die van een
    opslag (saving.limit_for: office_max_save_bytes of het quotum van de eigenaar van de map).

POST /api/v1/office/files/{id}/convert-to {format, name?} -> 201 NodeOut
    Een omgezette kopie in dezelfde map (de web-app vraagt het bij hernoemen naar een andere
    extensie). Het origineel blijft zoals het is. Wat kan, per groep (de bron volgens het
    mime-type van de inhoud; tekst/csv als txt of csv):
      tekst:    docx, doc, odt, rtf, docm, txt  -> docx, odt, rtf, txt, pdf
      werkblad: xlsx, xls, ods, xlsm, csv      -> xlsx, ods, csv, pdf
      dia:      pptx, ppt, odp, pptm           -> pptx, odp, pdf
    Een kantoorbestand gaat eerst door de invoercontrole (Keuze 23); dan een grant, zoals bij de
    PDF (Keuze 25), en engine.convert met een eigen key per verzoek (cv-<hex>: DS bewaart het
    antwoord onder de key, en elke vraag is nieuw). Hooguit 10 per 10 minuten per gebruiker,
    samen met de voorbeelden op verzoek (convert.ASKS).

Fouten: 404 not_found (geen bestand, of geen sessie van jou met die key); 403 read_only; 404
folder_not_found; 413 office_too_large, no_room of no_room_owner; 422 office_format (dit
formaat niet, of niet vanuit deze bron), office_unsafe, invalid_input (een adres dat niet bij
de key past); 429 rate_limited; 502 office_convert_to_failed {code}; 503 office_off of
office_unavailable.
"""

import hashlib
import logging
import secrets
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Annotated

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.config import Settings, get_settings
from app.db import Session
from app.models import Blob, Node, User
from app.office import convert, engine, grants, inspect, monitor, runtime, saving, urls
from app.office.state import office_policy, office_state
from app.routers import office
from app.routers.files import NodeOut, Store, clean_name, get_access, no_room, one_out, place_file
from app.routers.office_contracts import SAVE_AS_FORMATS, ConvertToIn, SaveAsIn
from app.routers.office_new import target_folder, with_extension
from app.security import CurrentUser
from app.storage import BlobStore

log = logging.getLogger("platform.office")

router = APIRouter(prefix="/api/v1/office", tags=["office"])

NOT_FOUND = "Niet gevonden"
FORMAT = "Naar dit formaat kan dit bestand niet worden omgezet"
BAD_URL = "Dit adres hoort niet bij deze editor"
TOO_LARGE = "Het omgezette bestand is te groot"
UNAVAILABLE = office.UNAVAILABLE
# De code van de adapter als de uitvoer groter is dan de grens (oo/convert.py TOO_LARGE)
OUT_TOO_LARGE = 3

# Welke bronnen naar welke formaten kunnen (wat DS kan maken; een groep blijft een groep)
TEXT_SOURCES = frozenset({"docx", "doc", "odt", "rtf", "docm", "txt"})
SHEET_SOURCES = frozenset({"xlsx", "xls", "ods", "xlsm", "csv"})
SLIDE_SOURCES = frozenset({"pptx", "ppt", "odp", "pptm"})
GROUPS: list[tuple[frozenset[str], tuple[str, ...]]] = [
    (TEXT_SOURCES, ("docx", "odt", "rtf", "txt", "pdf")),
    (SHEET_SOURCES, ("xlsx", "ods", "csv", "pdf")),
    (SLIDE_SOURCES, ("pptx", "odp", "pdf")),
]
# Tekst gaat niet door de invoercontrole (die kent alleen kantoorformaten)
PLAIN = frozenset({"txt", "csv"})


def targets(source: str) -> tuple[str, ...]:
    """De formaten waar bron source heen kan (leeg: geen)."""
    for sources, outputs in GROUPS:
        if source in sources:
            return outputs
    return ()


def source_ext(blob: Blob, name: str) -> str | None:
    """De soort van de inhoud voor DS: een kantoorformaat volgens het mime-type (mime.py keek
    naar de bytes), of txt/csv voor tekst (csv volgens het mime-type of de naam)."""
    ext = convert.ext_of(blob.mime)
    if ext is not None:
        return ext
    mime = (blob.mime or "").lower()
    if mime == "text/csv" or (mime.startswith("text/") and name.lower().endswith(".csv")):
        return "csv"
    if mime.startswith("text/") or (blob.size == 0 and name.lower().endswith(".txt")):
        return "txt"
    return None


def copy_name(node: Node, name: str | None, ext: str) -> str:
    """De naam van de kopie: name, of de stam van het origineel; altijd met extensie ext."""
    if name is None or not name.strip():
        stem = PurePosixPath(node.name).stem or node.name
        name = stem
    return with_extension(clean_name(name), ext)


def _office_on() -> Settings:
    settings = runtime.settings()
    if not settings.office_configured or engine.current() is None:
        raise office.off()
    return settings


async def _digest(path: Path) -> tuple[str, int]:
    def run() -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    return await anyio.to_thread.run_sync(run)


async def _file(session: AsyncSession, user: User, node_id: uuid.UUID) -> Node:
    node = (await get_access(session, user, node_id, "read")).node
    if node.kind != "file" or not node.blob_id:
        raise errors.fail(422, "not_a_file", "Alleen bestanden kunnen worden omgezet")
    return node


def convert_to_failed(code: int) -> Exception:
    error = errors.fail(
        502, "office_convert_to_failed", f"Dit bestand kon niet worden omgezet (code {code})"
    )
    error.detail["params"] = {"code": code}  # zoals convert.convert_failed
    return error


# --- Opslaan als (de editor) ---


@router.post("/files/{node_id}/save-as", status_code=201)
async def save_as(
    node_id: uuid.UUID,
    body: SaveAsIn,
    user: CurrentUser,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
) -> NodeOut:
    """De kopie die DS maakte bij "Kopie opslaan als", naast het origineel."""
    office_settings = _office_on()
    file_type = body.file_type.lower().lstrip(".")
    if file_type not in SAVE_AS_FORMATS:
        raise errors.fail(422, "office_format", FORMAT)
    node = await _file(session, user, node_id)
    row = await office._my_session(session, user, node, ("pending", "open"))
    if row is None or row.key != body.key:
        raise errors.fail(404, "not_found", NOT_FOUND)
    target = await target_folder(session, user, node.parent_id)
    name = copy_name(node, body.title, file_type)
    limit, by_room = await saving.limit_for(session, office_settings, target.owner)
    key = row.key
    await session.commit()  # niets openhouden tijdens het ophalen bij DS
    try:
        tmp, sha, size = await saving.download(office_settings, key, body.url, limit, kind="saveas")
    except ValueError:
        raise errors.fail(422, "invalid_input", BAD_URL) from None
    except saving.DownloadTooLarge:
        if by_room:
            raise no_room(target.owner, user.id) from None
        raise errors.fail(413, "office_too_large", TOO_LARGE) from None
    except saving.DownloadError:
        log.warning("office: ophalen van een kopie (opslaan als) mislukt")
        raise errors.fail(503, "office_unavailable", UNAVAILABLE) from None
    try:
        placed = await place_file(
            session,
            blobs,
            target.owner,
            target.parent,
            name,
            "rename",
            sha,
            size,
            tmp,
            actor=user,
            guard=target.guard,
            tz=settings.timezone,
        )
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)  # alleen nog over als het misging
    return await one_out(session, user, placed)


# --- Omzetten (hernoemen naar een andere extensie) ---


@router.post("/files/{node_id}/convert-to", status_code=201)
async def convert_to(
    node_id: uuid.UUID,
    body: ConvertToIn,
    user: CurrentUser,
    request: Request,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
) -> NodeOut:
    """Een omgezette kopie in de map van het bestand; het origineel blijft."""
    office_settings = _office_on()
    if not (await office_policy(request.app).get(session)).enabled:
        raise office.off()
    state = office_state(request.app)
    if state.health != "ok" or monitor.too_old(state.version):
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    node = await _file(session, user, node_id)
    blob = await session.get(Blob, node.blob_id)
    if blob is None:
        raise errors.fail(404, "not_found", NOT_FOUND)
    output = body.format.lower().lstrip(".")
    ext = source_ext(blob, node.name)
    if ext is None or output == ext or output not in targets(ext):
        raise errors.fail(422, "office_format", FORMAT)
    limit = convert.limit_for(office_settings, ext)
    if blob.size > limit:
        raise errors.fail(
            413,
            "office_too_large",
            f"Dit bestand is te groot om om te zetten (hooguit {limit // 1024**2} MB)",
        )
    target = await target_folder(session, user, node.parent_id)
    name = copy_name(node, body.name, output)
    out_limit, by_room = await saving.limit_for(session, office_settings, target.owner)
    sha = blob.sha256
    await session.commit()  # geen databaseverbinding vasthouden tijdens de controle en DS
    if ext not in PLAIN:
        verdict = await inspect.check(office_settings, blobs.path(sha), ext, sha)
        if verdict.reason in inspect.TRANSIENT:  # de machine, niet het bestand: zo opnieuw
            raise errors.fail(503, "office_unavailable", UNAVAILABLE)
        if not verdict.ok:
            raise convert.unsafe()
    convert.ASKS.check(str(user.id))
    eng = engine.current()
    if eng is None:
        raise office.off()
    src = urls.internal_url(office_settings, f"/internal/office/src/{grants.grant(sha)}")
    work_root = BlobStore(office_settings.blob_dir).tmp
    await anyio.Path(work_root).mkdir(parents=True, exist_ok=True)
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=work_root)))
    out = work / f"out.{output}"
    try:
        try:
            await eng.convert(
                src,
                ext,
                f"cv-{secrets.token_hex(16)}",
                f"{convert.TITLE}.{ext}",
                out,
                out_limit,
                output=output,
            )
        except engine.ConvertError as e:
            if e.code == OUT_TOO_LARGE:
                if by_room:
                    raise no_room(target.owner, user.id) from None
                raise errors.fail(413, "office_too_large", TOO_LARGE) from None
            if e.kind in ("retry", "new_key", "misconfigured"):
                raise errors.fail(503, "office_unavailable", UNAVAILABLE) from None
            raise convert_to_failed(e.code) from None
        made, size = await _digest(out)
        placed = await place_file(
            session,
            blobs,
            target.owner,
            target.parent,
            name,
            "rename",
            made,
            size,
            out,
            actor=user,
            guard=target.guard,
            tz=settings.timezone,
        )
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
    return await one_out(session, user, placed)
