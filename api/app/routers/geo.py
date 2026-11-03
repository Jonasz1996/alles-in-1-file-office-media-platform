"""De plek van een foto en de bronnen van de plaatsnamen (bouwontwerp fase 6, §3.4; Z3). Stubs
van Z0: 501 not_built.

Alleen de eigenaar ziet de plek (Keuze 27): anderen krijgen 404 not_found, net als bij een
bestand zonder plek. Nooit via een publieke link.
"""

import uuid

from fastapi import APIRouter

from app.geo.types import FileLocation, GeoAttribution
from app.routers.stubs import not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])


@router.get("/files/{node_id}/location")
async def location(node_id: uuid.UUID, user: CurrentUser) -> FileLocation:
    raise not_built("Z3")


@router.get("/geo/attribution")
async def attribution(user: CurrentUser) -> GeoAttribution:
    raise not_built("Z3")
