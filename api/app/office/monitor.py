"""Bewaking van office voor /beheer (Keuze 27 en 28). O0-stub; O5 maakt het af.

notices(state, session) -> list[Notice]: de meldingen office_down, office_misconfigured,
office_version, office_limit, office_unsaved en office_convert_failed voor de haak in
admin.system_info. O5 meet zelf niets: hij leest OfficeState en de database. De stub meldt
niets.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.cluster import Notice
from app.office.state import OfficeState


async def notices(state: OfficeState, session: AsyncSession) -> list[Notice]:
    return []
