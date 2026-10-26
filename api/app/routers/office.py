"""/api/v1/office: de editor openen en opslaan (Keuze 7, 14 en 22). O0: status is af, de rest
zijn stubs die 503 office_off geven; O2 maakt ze af.

status mag altijd: de web-app vraagt hem om te weten of Bewerken en Nieuw document er zijn.
Zonder office-instellingen zegt hij enabled false, state off, en is de app precies fase 3.
"""

import uuid

from fastapi import APIRouter, Request

from app import errors
from app.db import Session
from app.office import runtime
from app.office.state import office_policy, office_state
from app.routers.office_contracts import (
    OfficeFile,
    OfficeStatus,
    SessionIn,
    SessionOut,
)
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/office", tags=["office"])

OFF = "Bewerken van documenten staat op deze server niet aan"


def off() -> Exception:
    return errors.fail(503, "office_off", OFF)


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


@router.get("/files/{node_id}")
async def office_file(node_id: uuid.UUID, user: CurrentUser) -> OfficeFile:
    raise off()


@router.post("/files/{node_id}/session")
async def open_session(node_id: uuid.UUID, body: SessionIn, user: CurrentUser) -> SessionOut:
    raise off()


@router.post("/files/{node_id}/save", status_code=202)
async def save_now(node_id: uuid.UUID, user: CurrentUser) -> dict:
    raise off()


@router.post("/gate", status_code=204)
async def renew_gate(user: CurrentUser) -> None:
    raise off()
