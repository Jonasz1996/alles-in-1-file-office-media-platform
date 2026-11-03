"""OCR op verzoek (bouwontwerp fase 6, §3.4; X1, pas na ja op Vraag 1). Stub van Z0: 501
not_built.

Een foto: alleen de eigenaar; een PDF: schrijfrecht. 202; 409 ocr_running; 503 ocr_off;
429 ocr_limit; 409 ocr_has_text.
"""

import uuid

from fastapi import APIRouter

from app.routers.stubs import not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["zoeken"])


@router.post("/files/{node_id}/ocr", status_code=202)
async def request_ocr(node_id: uuid.UUID, user: CurrentUser) -> None:
    raise not_built("X1")
