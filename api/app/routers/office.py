"""/api/v1/office: de editor openen en opslaan (Keuze 7, 14, 18, 22 en 23).

status mag altijd: de web-app vraagt hem om te weten of Bewerken en Nieuw document er zijn.
Zonder office-instellingen zegt hij enabled false, state off, en is de app precies fase 3; de
andere routes geven dan 503 office_off.

files/{id}: mag deze gebruiker dit bestand nu bewerken, en zo niet, waarom niet.
files/{id}/session: de rechten van Keuze 22, het formaat en de invoercontrole van Keuze 23,
dan de key van Keuze 14 (sessions.open_session), de getekende config en het poort-cookie.
files/{id}/save: de knop "Nu opslaan" (forcesave, Keuze 18).
gate: het poort-cookie vernieuwen (de editorpagina doet dat elke 30 minuten).
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.config import Settings
from app.db import Session
from app.models import Blob, Node, OfficeSession, User
from app.office import engine, gate, inspect, runtime, sessions, urls
from app.office.state import OfficeConfig, OfficeState, office_policy, office_state
from app.routers.files import ALBUM, get_access, is_trashed
from app.routers.office_contracts import (
    EDIT_FORMATS,
    MAX_EDIT_BYTES,
    OfficeFile,
    OfficeOpen,
    OfficeStatus,
    SessionIn,
    SessionOut,
)
from app.security import CurrentUser, RateLimiter, TokenFamily
from app.storage import BlobStore

log = logging.getLogger("platform.office")

router = APIRouter(prefix="/api/v1/office", tags=["office"])

OFF = "Bewerken van documenten staat op deze server niet aan"
UNAVAILABLE = "De editor is nu niet bereikbaar; probeer het zo opnieuw"
MISCONFIGURED = "De editor is niet goed ingesteld; vraag het je beheerder"
FORMAT = "Dit soort bestand kun je hier niet bewerken"
TOO_LARGE = "Dit bestand is te groot voor de editor"
UNSAFE = "Dit bestand kwam niet door de controle en kan niet in de editor open"
TRASHED = {"message": "Dit bestand ligt in de prullenbak", "code": "trashed"}
NOT_A_FILE = {"message": "Dit is geen bestand", "code": "not_a_file"}
NOT_FOUND = "Niet gevonden"
# Een eindige licentie van DS; daarboven telt hij als onbeperkt
UNLIMITED = 2_147_483_647

# Per gebruiker: zoveel keer een editor openen per minuut (ook bij office_busy elke 3 s)
session_limiter = RateLimiter(
    30,
    60,
    {"message": "Te vaak de editor geopend; probeer het zo opnieuw", "code": "rate_limited"},
)


def off() -> Exception:
    return errors.fail(503, "office_off", OFF)


def _configured() -> Settings:
    settings = runtime.settings()
    if not settings.office_configured or engine.current() is None:
        raise off()
    return settings


@router.get("/status")
async def status(user: CurrentUser, request: Request, session: Session) -> OfficeStatus:
    """Staat office aan, en kan er nu bewerkt worden?"""
    settings = runtime.settings()
    state = office_state(request.app)
    if not settings.office_url:
        return OfficeStatus(enabled=False, editing=False, state="off")
    if not settings.office_configured:
        return OfficeStatus(enabled=False, editing=False, state="misconfigured")
    config = await office_policy(request.app).get(session)
    health = state.health
    return OfficeStatus(
        enabled=config.enabled, editing=config.enabled and health == "ok", state=health
    )


# --- wat mag deze gebruiker met dit bestand ---


def _ext(name: str) -> str:
    return name.rpartition(".")[2].lower() if "." in name else ""


async def _blocker(session: AsyncSession, user: User, node: Node, permission: str) -> str | None:
    """De eerste reden van Keuze 22 en 23 waarom dit bestand niet in de editor mag (zonder
    de invoercontrole), of None."""
    if node.kind == "album":
        return "album"
    if node.kind != "file":
        return "format"
    if permission not in ("owner", "write"):
        return "read_only"
    if node.deleted_at is not None or await is_trashed(session, node):
        return "trashed"
    if _ext(node.name) not in EDIT_FORMATS or not node.blob_id:
        return "format"
    if (node.size or 0) > MAX_EDIT_BYTES:
        return "too_large"
    return None


async def _safe(settings: Settings, node: Node) -> bool:
    """De invoercontrole (Keuze 23): geen bestand naar DS zonder."""
    path = BlobStore(settings.blob_dir).path(node.blob_id)
    verdict = await inspect.check(settings, path, _ext(node.name), node.blob_id)
    if not verdict.ok:
        log.info("office: invoercontrole weigert een bestand (%s)", verdict.reason)
    return verdict.ok


def _too_many(row: OfficeSession | None, state: OfficeState, user: User, cfg: OfficeConfig):
    if row is None:
        return False
    joined = sessions.editor_users(row, state)
    return user.id not in joined and len(joined) >= cfg.max_editors


async def _over_license(
    session: AsyncSession, row: OfficeSession | None, state: OfficeState, user: User
) -> int | None:
    """De licentie van DS (Keuze 27): het aantal als de editor vol zit, anders None. Wie al
    meedoet, telt niet opnieuw."""
    limit = state.license_connections
    if not limit or limit >= UNLIMITED:
        return None
    if row is not None and user.id in sessions.editor_users(row, state):
        return None
    if await sessions.connections(session, state) >= limit:
        return limit
    return None


@router.get("/files/{node_id}")
async def office_file(
    node_id: uuid.UUID, user: CurrentUser, request: Request, session: Session
) -> OfficeFile:
    """Kan deze gebruiker dit bestand nu bewerken? Zo niet: de reden. Plus hoeveel mensen er
    nu in zitten, en de stand van het PDF-voorbeeld (O3)."""
    settings = _configured()
    found = await get_access(session, user, node_id, "read")
    node = found.node
    state = office_state(request.app)
    cfg = await office_policy(request.app).get(session)
    pdf_state = None
    if node.blob_id:
        pdf_state = await session.scalar(select(Blob.pdf_state).where(Blob.sha256 == node.blob_id))
    row = await sessions.live(session, node.id) if node.kind == "file" else None
    opened = OfficeOpen(editors=len(sessions.editor_users(row, state))) if row else None
    reason = await _blocker(session, user, node, found.permission)
    if reason is None and not cfg.enabled:
        reason = "office_off"
    if reason is None and state.health != "ok":
        reason = "office_down"
    if reason is None and _too_many(row, state, user, cfg):
        reason = "too_many"
    if reason is None and not await _safe(settings, node):
        reason = "unsafe"
    return OfficeFile(can_edit=reason is None, reason=reason, open=opened, pdf_state=pdf_state)


# --- de editor openen ---

REFUSALS = {
    "album": (422, ALBUM),
    "format": (422, {"message": FORMAT, "code": "office_format"}),
    "read_only": (403, {"message": "Je mag dit bestand alleen bekijken", "code": "read_only"}),
    "trashed": (422, TRASHED),
    "too_large": (422, {"message": TOO_LARGE, "code": "office_too_large"}),
}


def _lang(user: User, request: Request) -> str:
    locale = (user.locale or "").lower()
    if not locale:
        accept = request.headers.get("accept-language", "").lower()
        locale = "en" if accept.startswith("en") else "nl"
    return "en" if locale.startswith("en") else "nl"


@router.post("/files/{node_id}/session")
async def open_session(
    node_id: uuid.UUID,
    body: SessionIn,
    user: CurrentUser,
    family: TokenFamily,
    request: Request,
    response: Response,
    session: Session,
) -> SessionOut:
    """De editor openen: wie mag (Keuze 22), welk bestand (Keuze 23), welke key (Keuze 14).
    Daarna de getekende config en het poort-cookie van /office/ (Keuze 7)."""
    settings = _configured()
    session_limiter.check(str(user.id))
    app = request.app
    state = office_state(app)
    cfg = await office_policy(app).get(session)
    if not cfg.enabled:
        raise off()
    if state.health == "misconfigured":
        raise errors.fail(503, "office_misconfigured", MISCONFIGURED)
    if state.health != "ok":
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    found = await get_access(session, user, node_id, "read")
    node = found.node
    if node.kind not in ("file", "album"):
        raise errors.fail(422, **NOT_A_FILE)
    reason = await _blocker(session, user, node, found.permission)
    if reason is not None:
        code, detail = REFUSALS[reason]
        raise errors.fail(code, **detail)
    if not await _safe(settings, node):
        raise errors.fail(422, "office_unsafe", UNSAFE)
    limit = await _over_license(session, await sessions.live(session, node.id), state, user)
    if limit is not None:
        raise sessions.too_many(limit)
    file_type = _ext(node.name)
    row, stale = await sessions.open_session(
        session,
        user,
        node,
        settings=settings,
        state=state,
        file_type=file_type,
        max_editors=cfg.max_editors,
    )
    eng = engine.current()
    if eng is None:
        raise off()
    lang = _lang(user, request)
    ctx = engine.OpenContext(
        key=row.key,
        ds_uid=sessions.ds_uid(settings, user.id),
        user_name=user.display_name or "",
        title=node.name,
        file_type=file_type,
        lang=lang,
        region="en-GB" if lang == "en" else "nl-NL",
        theme=body.theme or "light",
        mobile=body.type == "mobile",
        doc_url=urls.internal_url(settings, f"/internal/office/doc/{row.key}"),
        callback_url=urls.internal_url(settings, "/internal/office/callback"),
        close_text=None,  # A15: DS kent zelf een tekst per lang
    )
    config = eng.editor_config(ctx)
    https = settings.secure_cookies or request.url.scheme == "https"
    gate.issue(response, settings, user, family, https)
    return SessionOut(config=config, key=row.key, stale=stale)


# --- nu opslaan en het cookie ---


async def _my_session(
    session: AsyncSession, user: User, node: Node, states: tuple[str, ...]
) -> OfficeSession | None:
    """De sessie van dit bestand waar deze gebruiker lid van is, niet banned, met schrijfrecht
    van nu."""
    row = await sessions.live(session, node.id)
    if row is None or row.state not in states:
        return None
    uid = sessions.ds_uid(runtime.settings(), user.id)
    if uid not in (row.members or {}) or uid in (row.banned or []):
        return None
    if not await sessions.can_write(session, user.id, node):
        return None
    return row


@router.post("/files/{node_id}/save", status_code=202)
async def save_now(node_id: uuid.UUID, user: CurrentUser, session: Session) -> dict:
    """De knop "Nu opslaan": forcesave met userdata "button" (Keuze 18). 404 zonder open
    sessie waar deze gebruiker met schrijfrecht in zit."""
    _configured()
    found = await get_access(session, user, node_id, "read")
    row = await _my_session(session, user, found.node, ("open",))
    if row is None:
        raise errors.fail(404, "not_found", NOT_FOUND)
    key = row.key
    await session.commit()  # niets openhouden tijdens de aanroep naar DS
    eng = engine.current()
    if eng is None:
        raise off()
    try:
        await eng.checkpoint(key, "button")
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - DS onbereikbaar of een vreemd antwoord
        log.warning("office: forcesave voor de knop mislukt")
        raise errors.fail(503, "office_unavailable", UNAVAILABLE) from None
    return {}


@router.post("/gate", status_code=204)
async def renew_gate(
    user: CurrentUser, family: TokenFamily, request: Request, session: Session
) -> Response:
    """Het poort-cookie opnieuw, alleen voor wie nu lid is van een sessie die niet dicht is,
    met schrijfrecht van nu. Anders 404."""
    settings = _configured()
    uid = sessions.ds_uid(settings, user.id)
    rows = (
        await session.scalars(select(OfficeSession.key).where(OfficeSession.state != "closed"))
    ).all()
    allowed = False
    for key in rows:
        row = await sessions.find_by_key(session, key)
        if row is None or uid not in (row.members or {}) or uid in (row.banned or []):
            continue
        node = await session.get(Node, row.node_id) if row.node_id else None
        if node is not None and await sessions.can_write(session, user.id, node):
            allowed = True
            break
    if not allowed:
        raise errors.fail(404, "not_found", NOT_FOUND)
    response = Response(status_code=204)
    https = settings.secure_cookies or request.url.scheme == "https"
    gate.issue(response, settings, user, family, https)
    return response
