"""Herinneringen: je eigen foto's van deze dag in eerdere jaren (bouwontwerp fase 6, §3.4,
Keuze 41; Z6). Alleen eigen, levende foto's (own_live, in app/memories.py); een gast heeft geen
eigen foto's (403, zoals /photos).

De dag en de ref van een regel (een dag of een id) staan in de URL; de toegangslog laat ze weg
(QUERY_PATHS en MEMORY_HIDE_PATH in app/main.py).
"""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Path

from app import memories as M
from app.config import Settings, get_settings
from app.db import Session
from app.people.types import Memories, MemoryHide, MemoryHides, MemoryYear
from app.routers.photos import owner_only, photo_items
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])


@router.get("/photos/memories")
async def memories(
    user: CurrentUser,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
    background: BackgroundTasks,
    day: datetime.date | None = None,
) -> Memories:
    """Per eerder jaar je foto's van deze dag (standaard vandaag in PLATFORM_TIMEZONE), het
    recentste jaar eerst; leeg als je herinneringen uit hebt."""
    owner_only(user)
    found = await M.find(session, user, day or M.today(settings))
    # Eén keer photo_items voor alle jaren (één query voor shared), dan weer per jaar
    items = await photo_items(
        session, user, [row for _, _, rows in found for row in rows], settings, background
    )
    years = []
    start = 0
    for years_ago, taken, rows in found:
        years.append(
            MemoryYear(years_ago=years_ago, day=taken, items=items[start : start + len(rows)])
        )
        start += len(rows)
    return Memories(years=years)


@router.get("/photos/memories/hide")
async def hidden(user: CurrentUser, session: Session) -> MemoryHides:
    """Wat je uit je herinneringen hebt gehaald."""
    owner_only(user)
    return MemoryHides(hides=await M.hides(session, user))


@router.post("/photos/memories/hide", status_code=204)
async def hide(body: MemoryHide, user: CurrentUser, session: Session) -> None:
    """Een dag (JJJJ-MM-DD), een eigen map of een eigen persoon niet meer in je herinneringen;
    400 memories_bad_hide als die er niet is of niet van jou is."""
    owner_only(user)
    await M.hide(session, user, body.kind, body.ref)
    await session.commit()


@router.delete("/photos/memories/hide/{kind}/{ref}", status_code=204)
async def unhide(
    kind: Literal["day", "folder", "person"],
    ref: Annotated[str, Path(max_length=255)],
    user: CurrentUser,
    session: Session,
) -> None:
    """Weer in je herinneringen; wat er niet was, is ook goed."""
    owner_only(user)
    await M.unhide(session, user, kind, ref)
    await session.commit()
