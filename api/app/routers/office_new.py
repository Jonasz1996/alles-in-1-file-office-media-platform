"""POST /api/v1/office/new: een leeg docx, xlsx of pptx in een map (Keuze 24). O0-stub die
503 office_off geeft; O4 maakt hem af (201 NodeOut; 403 read_only, 404 folder_not_found,
413 no_room, 422 album).
"""

from fastapi import APIRouter

from app import errors
from app.routers.files import NodeOut
from app.routers.office_contracts import NewDocumentIn
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1/office", tags=["office"])


@router.post("/new", status_code=201)
async def new_document(body: NewDocumentIn, user: CurrentUser) -> NodeOut:
    raise errors.fail(503, "office_off", "Bewerken van documenten staat op deze server niet aan")
