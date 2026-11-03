"""De tekst van een bestand voor de viewer (bouwontwerp fase 6, §3.4; Z2): de woorden per pagina
met hun plek (de tekstlaag, B20) en de stand van de analyse. Stubs van Z0: 501 not_built.

De woorden gaan met het download-token van de viewer, zoals de pagina's zelf (viewer.page):
het token hoort bij dit bestand, en de toegang wordt bij elk verzoek opnieuw gecontroleerd.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app import errors
from app.config import Settings, get_settings
from app.routers.stubs import not_built
from app.search.types import AnalysisOut, PageWords
from app.security import CurrentUser, read_download_token

router = APIRouter(prefix="/api/v1", tags=["zoeken"])


@router.get("/files/{node_id}/pages/{n}/words")
async def page_words(
    node_id: uuid.UUID,
    n: Annotated[int, Path(ge=1)],
    token: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> PageWords:
    """{"page", "words": [{t, x, y, w, h}], "source": "pdf" | "ocr"}; ETag, Cache-Control:
    private; 404 not_found als er geen is; 20 per seconde per gebruiker."""
    _user, token_node, _version = read_download_token(settings, token)
    if token_node != node_id:
        raise errors.fail(403, "wrong_token", "Token hoort bij een ander bestand")
    raise not_built("Z2")


@router.get("/files/{node_id}/analysis")
async def analysis(node_id: uuid.UUID, user: CurrentUser) -> AnalysisOut:
    """De stand van text, hash en ocr voor wie het bestand mag lezen."""
    raise not_built("Z2")
