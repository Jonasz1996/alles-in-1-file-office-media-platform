"""Herinneringen: foto's van deze dag in eerdere jaren (bouwontwerp fase 6, §3.4; Z6). Stubs van
Z0: 501 not_built. Alleen eigen, levende foto's (own_live).
"""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path

from app.people.types import Memories, MemoryHide, MemoryHides
from app.routers.stubs import not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])


@router.get("/photos/memories")
async def memories(user: CurrentUser, day: datetime.date | None = None) -> Memories:
    raise not_built("Z6")


@router.get("/photos/memories/hide")
async def hidden(user: CurrentUser) -> MemoryHides:
    raise not_built("Z6")


@router.post("/photos/memories/hide", status_code=204)
async def hide(body: MemoryHide, user: CurrentUser) -> None:
    raise not_built("Z6")


@router.delete("/photos/memories/hide/{kind}/{ref}", status_code=204)
async def unhide(
    kind: Literal["day", "folder", "person"],
    ref: Annotated[str, Path(max_length=255)],
    user: CurrentUser,
) -> None:
    raise not_built("Z6")
