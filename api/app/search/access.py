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

De keten omhoog van wat van jezelf is, geeft meteen ook het pad van die treffers (de namen vanaf
de hoofdmap): één recursieve query voor de controle en de paden samen, en die query wordt één
keer gebouwd (de ids als één lijst-parameter). Eerst waren het er twee, elke keer opnieuw
gebouwd: ±9 ms CPU in de event loop per pagina van de ±25 (review golf 1, H2: bij 4 clients
tegelijk is die CPU de rij).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

from sqlalchemy import Select, String, bindparam, select, type_coerce
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


@cache
def _wortels() -> Select:
    """(id, pad, soort) van de wortels; één keer gebouwd, de ids als lijst-parameter."""
    return select(_NODE.c.id, _NODE.c.mpath, _NODE.c.kind).where(
        _NODE.c.id.in_(bindparam("ids", expanding=True))
    )


async def search_ranges(
    session: AsyncSession, user: User, shared: Sequence[uuid.UUID] | None = None
) -> Ranges:
    """De bereiken waarin zoeken voor deze gebruiker mag kijken; een wortel binnen een andere
    valt weg. shared: de ids van access.shared_roots als de aanroeper die al heeft (suggest
    gebruikt ze ook voor de albums), anders leest hij ze zelf."""
    if shared is None:
        shared = [r.node_id for r in await access.shared_roots(session, user.id)]
    ids = list(shared)
    if user.role != "guest" and user.root_id is not None:
        ids.append(user.root_id)
    if not ids:
        return Ranges([], False)
    rows = await session.execute(_wortels(), {"ids": ids})
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
    """Wat de tweede controle doorliet: het recht per node, het pad naar elke node (vanaf de
    gedeelde map voor wat van een ander is, vanaf de hoofdmap voor wat van jezelf is; zonder de
    node zelf), en welke nodes zelf een gedeelde map zijn."""

    permissions: dict[uuid.UUID, str]
    paths: dict[uuid.UUID, list[access.Step]]
    roots: set[uuid.UUID]


@cache
def own_chains() -> Select:
    """(origin, id, naam, deleted_at, diepte) van elke eigen treffer omhoog tot de hoofdmap
    (access.ancestry), met de ids als één lijst-parameter "own". Eén keer gebouwd: SQLAlchemy
    onthoudt dan ook de cache-sleutel. origin en id komen zoals de database ze geeft (_uuid):
    een map staat in de keten van elke treffer eronder, en elke rij twee keer uuid.UUID() was
    ±1,5 ms per pagina."""
    up = access.ancestry(Node.id.in_(bindparam("own", expanding=True)))
    raw = (type_coerce(up.c.origin, String), type_coerce(up.c.id, String))
    return select(*raw, up.c.name, up.c.deleted_at, up.c.depth)


def _uuid(value: object, made: dict[object, uuid.UUID]) -> uuid.UUID:
    """Een id uit own_chains als uuid.UUID, één keer per waarde (in made): SQLite geeft de 32
    hextekens van de kolom Uuid, een andere database misschien al een UUID."""
    found = made[value] = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    return found


async def second_check(
    session: AsyncSession, user: User, hits: Sequence[tuple[uuid.UUID, uuid.UUID]]
) -> Checked:
    """hits: (node-id, eigenaar) per treffer. Eigen nodes: niets in de keten in de prullenbak
    (en geen gast), en uit dezelfde keten het pad. Van een ander: access.resolve. Wat niet
    doorkomt, ontbreekt in het antwoord, met een logregel per node."""
    own = [node_id for node_id, owner in hits if owner == user.id]
    other = [node_id for node_id, owner in hits if owner != user.id]
    permissions: dict[uuid.UUID, str] = {}
    paths: dict[uuid.UUID, list[access.Step]] = {}
    roots: set[uuid.UUID] = set()
    if own and user.role != "guest":
        # per node: diepte -> (id, naam); dead: iets in de keten ligt in de prullenbak
        chains: dict[uuid.UUID, dict[int, tuple[uuid.UUID, str]]] = defaultdict(dict)
        dead: set[uuid.UUID] = set()
        made: dict[object, uuid.UUID] = {}
        rows = await session.execute(own_chains(), {"own": own})
        for raw_origin, raw_id, name, deleted_at, depth in rows:
            origin = made.get(raw_origin) or _uuid(raw_origin, made)
            chains[origin][depth] = (made.get(raw_id) or _uuid(raw_id, made), name)
            if deleted_at is not None:
                dead.add(origin)
        for node_id in own:
            if node_id in dead:
                continue
            permissions[node_id] = "owner"
            chain = chains.get(node_id, {})
            paths[node_id] = [
                access.Step(chain[depth][0], chain[depth][1], "owner")
                for depth in sorted(chain, reverse=True)
                if depth > 0
            ]
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
