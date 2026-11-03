"""De kaart, de plaatsen en de controle vóór het delen (bouwontwerp fase 6, §3.4; Z3). Stubs van
Z0: 501 not_built. Alleen eigen, levende bestanden (own_live); de filters van Keuze 43 komen
erbij met Z3.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.geo.types import MapCells, MapItems, Places, ShareCheck
from app.routers.stubs import not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])

# w,s,e,n in graden
BBOX = Query(max_length=100, pattern=r"^[0-9eE.,+-]+$")


@router.get("/photos/map")
async def photo_map(
    user: CurrentUser,
    bbox: Annotated[str, BBOX],
    z: Annotated[int, Query(ge=0, le=14)],
) -> MapCells:
    raise not_built("Z3")


@router.get("/photos/map/items")
async def photo_map_items(
    user: CurrentUser,
    bbox: Annotated[str, BBOX],
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> MapItems:
    raise not_built("Z3")


@router.get("/photos/places")
async def places(user: CurrentUser) -> Places:
    raise not_built("Z3")


@router.get("/photos/share-check")
async def share_check(user: CurrentUser, node: uuid.UUID) -> ShareCheck:
    raise not_built("Z3")
