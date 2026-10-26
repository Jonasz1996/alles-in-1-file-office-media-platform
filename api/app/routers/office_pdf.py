"""PDF van kantoordocumenten (Keuze 25 en 26). O0-stubs die 503 office_off geven; O3 maakt
ze af.

POST /api/v1/office/files/{id}/convert -> 202 {pdf_state}
POST /api/v1/office/files/{id}/pdf     -> {url, expires_in}
GET  /api/v1/office/pdf/{id}?token=    -> application/pdf
Intern (internal_router, poort 8081): GET /internal/office/src/{grant} (download-JWT van DS)
"""

import uuid

from fastapi import APIRouter

from app import errors
from app.routers.office_contracts import ConvertOut, PdfLink
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/office", tags=["office"])
internal_router = APIRouter(prefix="/internal/office", tags=["office intern"])

OFF = "Bewerken van documenten staat op deze server niet aan"


@router.post("/files/{node_id}/convert", status_code=202)
async def convert(node_id: uuid.UUID, user: CurrentUser) -> ConvertOut:
    raise errors.fail(503, "office_off", OFF)


@router.post("/files/{node_id}/pdf")
async def pdf_link(node_id: uuid.UUID, user: CurrentUser) -> PdfLink:
    raise errors.fail(503, "office_off", OFF)


@router.get("/pdf/{node_id}")
async def pdf_download(node_id: uuid.UUID, token: str = "") -> None:
    raise errors.fail(503, "office_off", OFF)


@internal_router.get("/src/{grant}")
async def source(grant: str) -> None:
    raise errors.fail(503, "office_off", OFF)
