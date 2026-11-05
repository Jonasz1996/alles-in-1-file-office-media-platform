"""Duplicaten (bouwontwerp fase 6, §3.4; Z3). Stubs van Z0: 501 not_built. Alleen eigen,
levende bestanden; opruimen gaat via de bestaande batchroute van de prullenbak.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.geo.types import DupeIgnoreIn, DupePage
from app.routers.stubs import not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])


@router.get("/duplicates")
async def duplicates(
    user: CurrentUser,
    kind: Literal["exact", "near"] = "exact",
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> DupePage:
    raise not_built("Z3")


@router.post("/duplicates/ignore", status_code=204)
async def ignore(body: DupeIgnoreIn, user: CurrentUser) -> None:
    raise not_built("Z3")
