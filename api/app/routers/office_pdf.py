"""PDF van kantoordocumenten (Keuze 25 en 26).

POST /api/v1/office/files/{id}/convert?version= -> 202 {pdf_state: "pending" | "ready"}
    Een voorbeeld met opmaak op verzoek (ingelogd, leesrecht genoeg; nooit via een publieke
    link). Fouten: 404; 422 not_a_file | office_format | office_unsafe; 413 office_too_large;
    429 rate_limited; 502 office_convert_failed {code}; 503 office_off | office_unavailable.
POST /api/v1/office/files/{id}/pdf?version=     -> {url, expires_in}
    Downloaden als PDF. Is de PDF er nog niet, dan eerst een conversie op verzoek (met dezelfde
    grens per gebruiker) en hooguit 60 s wachten; anders 409 pdf_pending.
GET  /api/v1/office/pdf/{id}?token=             -> application/pdf, als bijlage "<stam>.pdf"
    Het download-token van /pdf, met security.active_user en de toegang bij elk verzoek
    opnieuw, net als /download. Werkt ook als DS even weg is: de PDF staat bij de app.
Intern (internal_router, poort 8081):
GET  /internal/office/src/{grant}                -> de bron van een conversie
    Alleen met het download-JWT van DS voor precies dit adres (401 office_token), en alleen
    zolang de grant bestaat (404).

Zonder office-instellingen geven ze 503 office_off, precies zoals de stubs van O0.
"""

import base64
import uuid
from pathlib import PurePosixPath
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.config import Settings, get_settings
from app.db import Session
from app.models import Blob, Node, User
from app.office import convert, engine, grants, runtime, urls
from app.routers import files
from app.routers.office_contracts import ConvertOut, PdfLink
from app.security import CurrentUser, active_user, download_token, read_download_token
from app.storage import BlobStore

router = APIRouter(prefix="/api/v1/office", tags=["office"])
internal_router = APIRouter(prefix="/internal/office", tags=["office intern"])

OFF = convert.OFF
NO_PDF = "Van dit bestand is er (nog) geen PDF"


def _office_on() -> None:
    if engine.current() is None:
        raise errors.fail(503, "office_off", OFF)


async def _target(
    session: AsyncSession, user: User, node_id: uuid.UUID, version: uuid.UUID | None
) -> tuple[Node, str]:
    """Het bestand (leesrecht) en de inhoud: van het bestand zelf of van een eerdere versie."""
    node = await files.get_node(session, user, node_id, "read")
    if node.kind != "file" or not node.blob_id:
        raise errors.fail(422, "not_a_file", "Alleen bestanden kunnen worden omgezet")
    if version is None:
        return node, node.blob_id
    found = await files.get_version(session, node, version)
    return node, found.blob_id


@router.post("/files/{node_id}/convert", status_code=202)
async def convert_now(
    node_id: uuid.UUID, user: CurrentUser, session: Session, version: uuid.UUID | None = None
) -> ConvertOut:
    """Een voorbeeld met opmaak maken (bij pdf_state ask, of voor een eerdere versie)."""
    _office_on()
    node, sha = await _target(session, user, node_id, version)
    return ConvertOut(pdf_state=await convert.request(session, user, node, sha))


@router.post("/files/{node_id}/pdf")
async def pdf_link(
    node_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    version: uuid.UUID | None = None,
) -> PdfLink:
    """Een downloadlink naar de omgezette PDF; zo nodig eerst omzetten (hooguit 60 s)."""
    _office_on()
    node, sha = await _target(session, user, node_id, version)
    state = await convert.request(session, user, node, sha)
    if state != "ready" and await convert.wait_ready(session, sha) != "ready":
        raise errors.fail(409, "pdf_pending", "De PDF wordt nog gemaakt; probeer het zo opnieuw")
    token = download_token(settings, user.id, node.id, version)
    return PdfLink(
        url=f"/api/v1/office/pdf/{node.id}?token={token}",
        expires_in=max(0, settings.download_token_minutes * 60),
    )


def pdf_name(name: str) -> str:
    """ "Rapport 2024.docx" -> "Rapport 2024.pdf"."""
    stem = PurePosixPath(name).stem if "." in name.strip(".") else name
    return f"{stem or 'document'}.pdf"


@router.get("/pdf/{node_id}")
async def pdf_download(
    node_id: uuid.UUID,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    token: str = "",
) -> FileResponse:
    """De omgezette PDF als bijlage. Bij elk verzoek opnieuw: het token, de gebruiker
    (geblokkeerd of weg: 404) en de toegang, zodat een ingetrokken deling meteen stopt."""
    _office_on()
    user_id, token_node, version_id = read_download_token(settings, token)
    if token_node != node_id:
        raise errors.fail(403, "wrong_token", "Token hoort bij een ander bestand")
    user = await active_user(session, user_id)
    node, sha = await _target(session, user, node_id, version_id)
    blob = await session.get(Blob, sha)
    found = await convert.office_pdf(settings, blob)
    if found is None:
        raise errors.fail(404, "not_found", NO_PDF)
    return FileResponse(
        found[0],
        media_type="application/pdf",
        filename=pdf_name(node.name),
        content_disposition_type="attachment",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )


@internal_router.get("/src/{grant}")
async def source(grant: str, authorization: Annotated[str | None, Header()] = None) -> FileResponse:
    """De bron van een conversie voor DS: alleen met zijn download-JWT voor precies dit adres,
    en alleen zolang de grant bestaat (tien minuten, in het geheugen)."""
    office = engine.current()
    if office is None:
        raise errors.fail(503, "office_off", OFF)
    settings = runtime.settings()
    url = urls.internal_url(settings, f"/internal/office/src/{grant}")
    try:
        office.check_download(authorization, url)
    except engine.TokenError as e:
        raise errors.fail(401, "office_token", "Ongeldig of verlopen token van de editor") from e
    sha = grants.resolve(grant)
    if sha is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    path = BlobStore(settings.blob_dir).path(sha)
    if not await anyio.Path(path).is_file():
        raise errors.fail(404, "not_found", "Niet gevonden")
    digest = base64.b64encode(bytes.fromhex(sha)).decode()
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={"Repr-Digest": f"sha-256=:{digest}:", "Cache-Control": "no-store"},
    )
