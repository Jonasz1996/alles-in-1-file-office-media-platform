"""De ene plek waar nodes definitief weggaan (bouwontwerp fase 6, Keuze 11 en §3.5).

Definitief wissen uit de prullenbak (`routers/trash.py`, ook `purge_expired`) en het wissen van
een gebruiker (`userdelete.py`) halen hun nodes weg via `delete_nodes`. Die doet het in de
transactie van de aanroeper en commit nooit zelf. De stukken maken de aanroepers: per `CHUNK`
nodes (userdelete per 500) een eigen transactie onder BlobStore.lock, met een commit ertussen
(`trash.purge_chunks`, `userdelete._drop_nodes`; review Z0, haalbaar F3). Een boom van 100.000
in één transactie hield de schrijver minuten vast, en `busy_timeout` is 5 seconden, dus een
upload ernaast zou falen. De triggers van 0010 werken per stuk mee.

De aanroeper geeft de ids met de kinderen vóór hun ouders. De FK `node.parent_id` staat op
CASCADE, dus een ouder neemt zijn kinderen mee, en de cascade start node_bd ook voor elk kind en
kleinkind (spike S2, test_wissen_met_de_cascade_raakt_ook_de_kleinkinderen): de index blijft
dan heel. Van onder naar boven wissen is er voor de stukken: een ouder vóór zijn kinderen wissen
neemt de hele boom in één opdracht mee, en dan is een stuk niet meer `CHUNK` nodes.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from itertools import batched

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Node

# Keuze 11: zoveel nodes per stuk, met een commit ertussen (de aanroeper)
CHUNK = 2000
# Zoveel ids per IN-lijst: SQLite heeft een grens op het aantal parameters
IN_LIMIT = 500


async def delete_nodes(session: AsyncSession, ids: Sequence[uuid.UUID]) -> int:
    """Wist deze nodes (kinderen vóór hun ouders) en geeft het aantal. In de transactie van de
    aanroeper, die commit: hooguit CHUNK nodes per transactie (zie boven)."""
    for part in batched(ids, IN_LIMIT):
        await session.execute(
            delete(Node).where(Node.id.in_(part)).execution_options(synchronize_session=False)
        )
    return len(ids)
