"""POST /api/v1/office/new: een leeg docx, xlsx of pptx in een map (Keuze 24, §2 O4).

Het nieuwe bestand is gewoon een bestand: place_file met conflict "rename", dus met de rechten,
het quotum en de gebeurtenissen van een upload. In een gedeelde map met schrijfrecht wordt het
van de eigenaar van die map, en kost het zijn quotum (B18). Daarom werkt het ook als DS weg is
(Keuze 28: "Nieuw document werkt"); er gaat niets naar DS. Alleen zonder office-instellingen, of
als de beheerder office uitzette, geeft het 503 office_off: dan is de app precies fase 3 (en de
web-app toont het menu dan niet).

De naam: zonder naam "Nieuw document.docx", "Nieuw werkblad.xlsx" of "Nieuwe presentatie.pptx",
in de taal van de gebruiker (user.locale; zonder keuze de eerste taal van de browser, zoals de
web-app hem kiest). Een naam die niet op de extensie van de soort eindigt, krijgt die erbij,
zodat de naam altijd past bij de inhoud. Bestaat hij al: "Nieuw document (1).docx", dan (2),
enzovoort: place_file kiest de vrije naam onder het slot van de eigenaar, net als bij elke
upload, kopie of terugzetten met rename (free_name).

Fouten: 403 read_only (alleen leesrecht) en guest; 404 folder_not_found (geen map, weg, in de
prullenbak of niet zichtbaar); 413 no_room of no_room_owner (quotum vol); 422 album, invalid_name
of invalid_input (een andere soort); 503 office_off.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request

from app import errors
from app.config import Settings, get_settings
from app.db import Session
from app.models import User
from app.office import blank, runtime
from app.office.state import office_policy
from app.routers.files import (
    ALBUM,
    MAX_NAME,
    READ_ONLY,
    Gone,
    NodeOut,
    Store,
    clean_name,
    get_access,
    is_trashed,
    one_out,
    place_file,
    writable,
)
from app.routers.office_contracts import NewDocumentIn
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/office", tags=["office"])

OFF = "Bewerken van documenten staat op deze server niet aan"


def locale_of(user: User, accept_language: str | None) -> str:
    """De taal van de gebruiker: zijn keuze in het account, anders de eerste taal van de
    browser zoals de web-app hem kiest (nl* is Nederlands, een andere taal Engels), anders
    Nederlands."""
    if user.locale:
        return user.locale
    first = (accept_language or "").split(",", 1)[0].split(";", 1)[0].strip().lower()
    if not first or first == "*":
        return "nl"
    return "nl" if first.startswith("nl") else "en"


def document_name(kind: blank.Kind, name: str | None, locale: str) -> str:
    """De naam van het nieuwe bestand: de standaardnaam, of de gegeven naam met de extensie van
    de soort erbij als hij daar nog niet op eindigt (te lang: de stam wordt korter)."""
    if name is None or not name.strip():
        return blank.default_name(kind, locale)
    name = clean_name(name)
    ext = f".{kind}"
    if name.lower().endswith(ext) and len(name) > len(ext):
        return name
    stem = name[: MAX_NAME - len(ext)].rstrip()
    return clean_name(stem + ext)


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data


@router.post("/new", status_code=201)
async def new_document(
    body: NewDocumentIn,
    user: CurrentUser,
    request: Request,
    session: Session,
    blobs: Store,
    settings: Annotated[Settings, Depends(get_settings)],
) -> NodeOut:
    """Een leeg document in map parent_id; daarna opent de web-app het in de editor."""
    if not runtime.settings().office_configured:
        raise errors.fail(503, "office_off", OFF)
    if not (await office_policy(request.app).get(session)).enabled:
        raise errors.fail(503, "office_off", OFF)

    # De map: zoals bij uploaden (get_live_dir), maar een album geeft 422 album en wat er niet
    # is (of niet zichtbaar) 404 folder_not_found
    try:
        found = await get_access(session, user, body.parent_id, "read")
    except HTTPException as e:
        if e.status_code == 404:
            raise Gone from None
        raise
    parent = found.node
    if parent.kind == "album":
        raise errors.fail(422, **ALBUM)
    if parent.kind != "dir" or parent.deleted_at is not None:
        raise Gone
    if not found.owner and found.permission != "write":
        raise errors.fail(403, **READ_ONLY)
    # Via een deling is de keten omhoog al gecontroleerd; de eigenaar zelf nog niet
    if found.owner and await is_trashed(session, parent):
        raise Gone

    locale = locale_of(user, request.headers.get("accept-language"))
    name = document_name(body.kind, body.name, locale)
    owner = user if found.owner else await session.get(User, parent.owner_id)
    if owner is None:
        raise Gone
    # Eerst als tijdelijk bestand met zijn sha (zoals een upload); place_file zet het op zijn
    # plek, of gooit het weg als dezelfde lege inhoud er al is (één blob per soort en taal)
    tmp, sha, size = await blobs.receive(_once(blank.blank(body.kind, locale)), 0)
    guard = None if found.owner else writable(user.id, parent.id)
    try:
        node = await place_file(
            session,
            blobs,
            owner,
            parent,
            name,
            "rename",
            sha,
            size,
            tmp,
            actor=user,
            guard=guard,
            tz=settings.timezone,
        )
    finally:
        await anyio.Path(tmp).unlink(missing_ok=True)  # alleen nog over als het misging
    return await one_out(session, user, node)
