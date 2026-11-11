"""Wie een bestand neerzette: node_origin (bouwontwerp fase 6, Keuze 69, K1-02, Vraag 9; A0).

Een bestand dat een ander in jouw map zet (een upload in een gedeelde map, via een uploadlink,
een kopie naar jouw map), wordt van jou. Zonder deze rij zou het gescand worden onder jouw
toestemming. faces.plan (X2) en de beeldindex (X3) slaan een bestand over als created_by niet
de eigenaar is, tenzij die zelf gezichten (of beeld) aan heeft; via een link nooit.

- **Neerzetten** (files.place_file: uploads, office, het importeren): wie het deed, of NULL
  met via_link 1 zonder actor (een uploadlink; zo noemt place_file dat ook). Nieuwe inhoud
  voor een bestaand bestand, ook een versie terugzetten: de rij wordt die van wie dat deed.
  Dezelfde inhoud nog eens: niets.
- **Kopiëren** (files.copy en batch.copy_many): wie kopieert, tenzij het origineel van een
  ander kwam (created_by niet de eigenaar of weg, of via een link): dan gaat die herkomst
  mee. Zo maakt een kopie de upload van een ander nooit tot die van jezelf.
- **Geen rij** (een bestand van vóór 0011): telt als door zijn eigenaar geüpload. Wie het echt
  neerzette, weet het platform niet (Vraag 9).

Alleen bestanden; een map krijgt geen rij. Alles in de transactie van de aanroeper."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from itertools import batched

from sqlalchemy import insert, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Node, NodeOrigin

CHUNK = 500  # rijen per INSERT of IN (...), zoals de kopie zelf

_TABLE = NodeOrigin.__table__


async def placed(session: AsyncSession, node_id: uuid.UUID, actor_id: uuid.UUID | None) -> None:
    """De rij van een bestand met nieuwe inhoud (nieuw, of vervangen): actor_id is wie het
    neerzette, None via een uploadlink. Een bestaande rij wordt vervangen."""
    stmt = sqlite_insert(_TABLE).values(
        node_id=node_id, created_by=actor_id, via_link=int(actor_id is None)
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[_TABLE.c.node_id],
            set_={"created_by": stmt.excluded.created_by, "via_link": stmt.excluded.via_link},
        )
    )


async def copied(
    session: AsyncSession, pairs: Sequence[tuple[uuid.UUID, uuid.UUID]], actor_id: uuid.UUID
) -> None:
    """De rijen van kopieën van bestanden: pairs is (origineel, kopie), de kopieën staan al in
    node. actor_id: wie kopieert."""
    if not pairs:
        return
    foreign: dict[uuid.UUID, tuple[uuid.UUID | None, int]] = {}
    for chunk in batched(pairs, CHUNK):
        rows = await session.execute(
            select(NodeOrigin.node_id, NodeOrigin.created_by, NodeOrigin.via_link)
            .join(Node, Node.id == NodeOrigin.node_id)
            .where(
                NodeOrigin.node_id.in_([old for old, _ in chunk]),
                or_(
                    NodeOrigin.via_link != 0,
                    NodeOrigin.created_by.is_(None),
                    NodeOrigin.created_by != Node.owner_id,
                ),
            )
        )
        foreign.update({r.node_id: (r.created_by, r.via_link) for r in rows})
    own = (actor_id, 0)
    for chunk in batched(pairs, CHUNK):
        values = []
        for old, new in chunk:
            created_by, via_link = foreign.get(old, own)
            values.append({"node_id": new, "created_by": created_by, "via_link": via_link})
        await session.execute(insert(_TABLE), values)
