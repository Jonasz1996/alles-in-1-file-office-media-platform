"""De ene plek waar nodes definitief weggaan (bouwontwerp fase 6, Keuze 11 en §3.5). Z0: stub.

Definitief wissen uit de prullenbak (`routers/trash.py`, ook `purge_expired`) en het wissen van
een gebruiker (`userdelete.py`) halen hun nodes weg via `delete_nodes`. Nu doet hij alles in de
transactie van de aanroeper, net als in 0.7.1. Z1 maakt er stukken van `CHUNK` met een commit
ertussen: een boom van 100.000 in één transactie houdt de schrijver minuten vast, en
`busy_timeout` is 5 seconden, dus een upload ernaast zou falen. De triggers van 0010 werken dan
per stuk mee.

De aanroeper geeft de ids met de kinderen vóór hun ouders. De FK `node.parent_id` staat op
CASCADE, dus een ouder neemt zijn kinderen mee, maar dan ziet de trigger de kinderen niet en
blijft hun rij in de zoekindex staan. Daarom wist de helper van onder naar boven.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from itertools import batched

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Node

# Keuze 11: zoveel nodes per stuk, met een commit ertussen (Z1)
CHUNK = 2000
# Zoveel ids per IN-lijst: SQLite heeft een grens op het aantal parameters
IN_LIMIT = 500


async def delete_nodes(session: AsyncSession, ids: Sequence[uuid.UUID]) -> int:
    """Wist deze nodes (kinderen vóór hun ouders) en geeft het aantal. In de transactie van de
    aanroeper, die commit; Z1 maakt er stukken van met een commit per stuk."""
    for part in batched(ids, IN_LIMIT):
        await session.execute(
            delete(Node).where(Node.id.in_(part)).execution_options(synchronize_session=False)
        )
    return len(ids)
