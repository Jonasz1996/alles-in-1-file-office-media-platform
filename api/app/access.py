"""Wie mag wat met een map of bestand.

De eigenaar mag alles. Een ander mag iets via een deling met hemzelf of met een groep waar hij
in zit, op de node zelf of op een map erboven, zolang die deling niet verlopen is en er in de
keten niets in de prullenbak ligt. Het hoogste recht telt: write gaat voor read. Het wordt bij
elk verzoek opnieuw uitgerekend, zodat een ingetrokken deling of een gewijzigde groep meteen
telt.

Eén recursieve query omhoog (vanaf één of veel nodes tegelijk), samen met de delingen: geen
query per map erboven. De bovenste map met een deling voor die gebruiker is zijn gedeelde map
(share root): wat daarboven ligt, ziet hij niet, ook de naam en het id niet.

Links (token gezet) geven hier nooit toegang: die gaan via app/routers/public.py.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Integer, and_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import GroupMember, Node, Share

RANK = {"read": 1, "write": 2, "owner": 3}


def better(a: str | None, b: str | None) -> str | None:
    """Het hoogste van twee rechten (None = geen)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if RANK[a] >= RANK[b] else b


@dataclass
class Step:
    """Een map of bestand op de weg vanaf de gedeelde map, met het recht dat daar geldt."""

    id: uuid.UUID
    name: str
    permission: str


@dataclass
class Grant:
    """Toegang via delingen tot één node."""

    permission: str
    # De gedeelde map: de bovenste node in de keten met een deling voor deze gebruiker
    root_id: uuid.UUID
    # Van de gedeelde map tot en met de node zelf
    chain: list[Step] = field(default_factory=list)
    # De node zelf draagt een deling voor deze gebruiker
    direct: bool = False


def live_share(now: datetime | None = None) -> ColumnElement[bool]:
    """Een deling met een gebruiker of groep (geen link) die nog niet verlopen is."""
    now = now or datetime.now(UTC)
    return and_(Share.token.is_(None), or_(Share.expires_at.is_(None), Share.expires_at > now))


def for_user(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Met deze gebruiker gedeeld, of met een groep waar hij in zit."""
    groups = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    return or_(Share.grantee_id == user_id, Share.group_id.in_(groups))


def ancestry(start: ColumnElement[bool]):
    """Recursieve CTE (origin, id, name, deleted_at, depth): van elke node die aan `start`
    voldoet omhoog tot de hoofdmap. depth 0 is de node zelf."""
    top = select(
        Node.id.label("origin"),
        Node.id.label("id"),
        Node.parent_id.label("parent_id"),
        Node.name.label("name"),
        Node.deleted_at.label("deleted_at"),
        literal(0, Integer).label("depth"),
    ).where(start)
    up = top.cte("up", recursive=True)
    parent = aliased(Node)
    step = select(
        up.c.origin, parent.id, parent.parent_id, parent.name, parent.deleted_at, up.c.depth + 1
    ).where(parent.id == up.c.parent_id)
    return up.union_all(step)


async def resolve(
    session: AsyncSession, user_id: uuid.UUID, node_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Grant]:
    """Toegang van deze gebruiker via delingen tot elk van deze nodes, in één query. Wat hij
    niet mag zien (geen deling, verlopen, of iets in de keten in de prullenbak) ontbreekt.
    Over eigenaarschap gaat dit niet: dat controleert de aanroeper."""
    ids = list(dict.fromkeys(node_ids))
    if not ids:
        return {}
    up = ancestry(Node.id.in_(ids))
    rows = await session.execute(
        select(up.c.origin, up.c.id, up.c.name, up.c.deleted_at, up.c.depth, Share.permission)
        .select_from(up)
        .outerjoin(Share, and_(Share.node_id == up.c.id, live_share(), for_user(user_id)))
    )
    # per node: depth -> [id, naam, in de prullenbak, hoogste recht daar]
    levels: dict[uuid.UUID, dict[int, list]] = defaultdict(dict)
    for origin, node_id, name, deleted_at, depth, permission in rows:
        level = levels[origin].get(depth)
        if level is None:
            levels[origin][depth] = [node_id, name, deleted_at is not None, permission]
        else:
            level[3] = better(level[3], permission)
    result = {}
    for origin, chain in levels.items():
        if any(level[2] for level in chain.values()):
            continue  # iets in de keten ligt in de prullenbak
        shared = [depth for depth, level in chain.items() if level[3]]
        if not shared:
            continue
        top = max(shared)
        steps, best = [], None
        for depth in range(top, -1, -1):
            node_id, name, _, permission = chain[depth]
            best = better(best, permission)
            steps.append(Step(node_id, name, best))
        result[origin] = Grant(best, chain[top][0], steps, chain[0][3] is not None)
    return result


async def trashed(session: AsyncSession, node_id: uuid.UUID) -> bool:
    """Ligt deze node, of een map erboven, in de prullenbak (of bestaat hij niet meer)? Eén
    query, ook voor een diepe map."""
    up = ancestry(Node.id == node_id)
    rows = (await session.execute(select(up.c.deleted_at))).all()
    return not rows or any(deleted_at is not None for (deleted_at,) in rows)


async def watchers(
    session: AsyncSession, node_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, dict[uuid.UUID, int]]:
    """Wie deze nodes via een deling met een gebruiker of groep kan zien, ook als er iets in
    de prullenbak ligt: per node {gebruiker: hoogste depth van zo'n deling}. depth 0 = de
    deling staat op de node zelf (dan ziet hij de map erboven niet). Eén query."""
    ids = [i for i in dict.fromkeys(node_ids) if i is not None]
    if not ids:
        return {}
    up = ancestry(Node.id.in_(ids))
    rows = await session.execute(
        select(up.c.origin, up.c.depth, Share.grantee_id, GroupMember.user_id)
        .select_from(up)
        .join(Share, and_(Share.node_id == up.c.id, live_share()))
        .outerjoin(GroupMember, GroupMember.group_id == Share.group_id)
    )
    result: dict[uuid.UUID, dict[uuid.UUID, int]] = defaultdict(dict)
    for origin, depth, grantee_id, member_id in rows:
        user_id = grantee_id or member_id
        if user_id is not None:
            result[origin][user_id] = max(depth, result[origin].get(user_id, -1))
    return result


async def viewers_of(
    session: AsyncSession, start: ColumnElement[bool]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Per gebruiker welke van deze nodes (die aan `start` voldoen) hij nu via een deling kan
    zien: niet verlopen en niets in de keten in de prullenbak. Eén query."""
    up = ancestry(start)
    rows = await session.execute(
        select(up.c.origin, up.c.deleted_at, Share.grantee_id, GroupMember.user_id)
        .select_from(up)
        .outerjoin(Share, and_(Share.node_id == up.c.id, live_share()))
        .outerjoin(GroupMember, GroupMember.group_id == Share.group_id)
    )
    gone: set[uuid.UUID] = set()
    seen: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for origin, deleted_at, grantee_id, member_id in rows:
        if deleted_at is not None:
            gone.add(origin)
        user_id = grantee_id or member_id
        if user_id is not None:
            seen[user_id].add(origin)
    return {user_id: nodes - gone for user_id, nodes in seen.items() if nodes - gone}


@dataclass
class Root:
    """Een gedeelde map (of bestand) van iemand anders: bovenaan, levend, niet verlopen."""

    node_id: uuid.UUID
    permission: str
    # De deling die het hoogste recht geeft (bij gelijk: liever met de gebruiker zelf)
    share: Share


async def shared_roots(session: AsyncSession, user_id: uuid.UUID) -> list[Root]:
    """Alles wat met deze gebruiker (of een groep van hem) gedeeld is en nu zichtbaar is,
    alleen de bovenste: een gedeelde map binnen een andere gedeelde map telt niet apart. Twee
    queries, hoeveel delingen er ook zijn."""
    shares = (
        await session.scalars(
            select(Share)
            .join(Node, Node.id == Share.node_id)
            .where(live_share(), for_user(user_id), Node.owner_id != user_id)
        )
    ).all()
    if not shares:
        return []
    grants = await resolve(session, user_id, [s.node_id for s in shares])
    best: dict[uuid.UUID, Share] = {}
    for share in shares:
        grant = grants.get(share.node_id)
        if grant is None or grant.root_id != share.node_id:
            continue  # in de prullenbak, of binnen een andere gedeelde map
        current = best.get(share.node_id)
        if current is None or _preferred(share, current):
            best[share.node_id] = share
    return [Root(node_id, grants[node_id].permission, s) for node_id, s in best.items()]


def _preferred(share: Share, other: Share) -> bool:
    if RANK[share.permission] != RANK[other.permission]:
        return RANK[share.permission] > RANK[other.permission]
    return share.grantee_id is not None and other.grantee_id is None
