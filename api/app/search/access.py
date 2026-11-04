"""Wat zoeken mag laten zien (bouwontwerp fase 6, Keuze 14 en 15).

**De wortels en hun bereiken, zonder staat** (Keuze 14). Bij elk verzoek opnieuw: de eigen
hoofdmap (niet voor een gast) plus de gedeelde mappen van anderen (app.access.shared_roots:
direct, via een groep, als gast; niet verlopen, niets in de prullenbak). Een gedeeld album geeft
niets om te zoeken. Een wortel binnen een andere valt weg. Elke wortel wordt een bereik op het
pad van nummers: [pad, eind), met eind het pad met de laatste / als 0 (het teken erna).

    roots = await search_ranges(session, user)
    sql, params = ranges_sql(roots.ranges)  # tot en met 64 een OR, daarboven json_each

Nooit een tijdelijke tabel en niets op de verbinding: verbindingen gaan via de pool van verzoek
naar verzoek. Een deling die in hetzelfde verzoek verloopt of wordt ingetrokken, telt dus al
niet meer.

**De tweede controle** (Keuze 15). Elke treffer gaat nog door app.access (resolve voor wat van
een ander is; de keten omhoog voor wat van jezelf is) voordat hij de pagina op gaat. Wijkt dat af
van het filter, dan valt de treffer weg en komt er één logregel search_access_mismatch met het
node-id: geen vraag, geen naam.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access
from app.models import Node, User

log = logging.getLogger("platform.search")

# Tot en met zoveel wortels een OR van bereiken; daarboven één JSON-parameter (J11, S18)
MAX_OR = 64
_NODE = Node.__table__


def end_of(path: str) -> str:
    """Het einde van het bereik van een pad: de laatste / wordt 0 (het teken erna)."""
    return path[:-1] + "0"


@dataclass(frozen=True)
class Ranges:
    """De bereiken [pad, eind), gesorteerd. shared: er is iets van een ander bij (een gedeelde
    map); zonder dat is elke treffer van de gebruiker zelf (een node hoort bij de eigenaar van
    zijn boom)."""

    ranges: list[tuple[str, str]]
    shared: bool


async def search_ranges(session: AsyncSession, user: User) -> Ranges:
    """De bereiken waarin zoeken voor deze gebruiker mag kijken; een wortel binnen een andere
    valt weg."""
    ids = [r.node_id for r in await access.shared_roots(session, user.id)]
    if user.role != "guest" and user.root_id is not None:
        ids.append(user.root_id)
    if not ids:
        return Ranges([], False)
    rows = await session.execute(
        select(_NODE.c.id, _NODE.c.mpath, _NODE.c.kind).where(_NODE.c.id.in_(ids))
    )
    found = [(path, node_id) for node_id, path, kind in rows if path and kind != "album"]
    shared = any(node_id != user.root_id for _, node_id in found)
    out: list[tuple[str, str]] = []
    for path, _ in sorted(found):
        # Gesorteerd staat alles onder een pad direct achter dat pad
        if out and path.startswith(out[-1][0]):
            continue
        out.append((path, end_of(path)))
    return Ranges(out, shared)


def ranges_sql(ranges: Sequence[tuple[str, str]], alias: str = "n") -> tuple[str, dict]:
    """De voorwaarde op {alias}.mpath voor deze bereiken, met haar parameters. Geen bereiken:
    een voorwaarde die nooit waar is."""
    if not ranges:
        return "0", {}
    if len(ranges) <= MAX_OR:
        parts, params = [], {}
        for i, (start, end) in enumerate(ranges):
            parts.append(f"({alias}.mpath >= :rp{i} AND {alias}.mpath < :re{i})")
            params[f"rp{i}"], params[f"re{i}"] = start, end
        return "(" + " OR ".join(parts) + ")", params
    sql = (
        f"EXISTS (SELECT 1 FROM json_each(:ranges) AS r "
        f"WHERE {alias}.mpath >= r.value ->> '$[0]' AND {alias}.mpath < r.value ->> '$[1]')"
    )
    return sql, {"ranges": json.dumps([list(r) for r in ranges])}


@dataclass
class Checked:
    """Wat de tweede controle doorliet: het recht per node, het pad vanaf de gedeelde map voor
    wat van een ander is, en welke nodes zelf een gedeelde map zijn."""

    permissions: dict[uuid.UUID, str]
    paths: dict[uuid.UUID, list[access.Step]]
    roots: set[uuid.UUID]


async def second_check(
    session: AsyncSession, user: User, hits: Sequence[tuple[uuid.UUID, uuid.UUID]]
) -> Checked:
    """hits: (node-id, eigenaar) per treffer. Eigen nodes: niets in de keten in de prullenbak
    (en geen gast). Van een ander: access.resolve. Wat niet doorkomt, ontbreekt in het
    antwoord, met een logregel per node."""
    own = [node_id for node_id, owner in hits if owner == user.id]
    other = [node_id for node_id, owner in hits if owner != user.id]
    permissions: dict[uuid.UUID, str] = {}
    paths: dict[uuid.UUID, list[access.Step]] = {}
    roots: set[uuid.UUID] = set()
    if own and user.role != "guest":
        up = access.ancestry(Node.id.in_(own))
        dead = {
            origin
            for origin, deleted_at in await session.execute(select(up.c.origin, up.c.deleted_at))
            if deleted_at is not None
        }
        for node_id in own:
            if node_id not in dead:
                permissions[node_id] = "owner"
    if other:
        grants = await access.resolve(session, user.id, other)
        for node_id, grant in grants.items():
            permissions[node_id] = grant.permission
            paths[node_id] = grant.chain[:-1]
            if grant.root_id == node_id:
                roots.add(node_id)
    for node_id, _ in hits:
        if node_id not in permissions:
            log.warning("search_access_mismatch %s", node_id)
    return Checked(permissions, paths, roots)
