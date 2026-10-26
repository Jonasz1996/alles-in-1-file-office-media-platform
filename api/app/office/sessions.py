"""Bewerksessies (Keuze 14 en 22). O0-stub; O2 maakt het af.

open_session(session, user, node) -> (OfficeSession, stale): wie welke key krijgt, onder
state.node_lock, na inspect. find_by_key(session, key): de rij van een key. busy(session):
loopt er een sessie die opent of opslaat (pending of saving)? Een conversie wacht dan (O3).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OfficeSession


async def find_by_key(session: AsyncSession, key: str) -> OfficeSession | None:
    return await session.get(OfficeSession, key)


async def busy(session: AsyncSession) -> bool:
    found = await session.scalar(
        select(OfficeSession.key).where(OfficeSession.state.in_(("pending", "saving"))).limit(1)
    )
    return found is not None
