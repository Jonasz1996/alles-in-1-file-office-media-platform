"""Mijn delingen: alles wat de gebruiker zelf deelt (met mensen, groepen en als link), op één
plek (/gedeeld/door-mij). Wat in de prullenbak ligt (of onder een weggegooide map) telt niet:
die delingen werken nu toch niet. Verlopen delingen staan er wel, met hun vervaldatum.
"""

import uuid
from collections import defaultdict

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app import access, errors
from app.db import Session
from app.models import Node, Share
from app.routers.files import GUEST, NodeOut, describe
from app.routers.shares import ShareOut, shares_out
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["delen"])


class PathPart(BaseModel):
    id: uuid.UUID
    name: str


class MyShare(BaseModel):
    share: ShareOut
    node: NodeOut
    # De mappen boven de node, vanaf de hoofdmap (die telt niet mee); [] = in de hoofdmap
    path: list[PathPart]


@router.get("/shares")
async def my_shares(user: CurrentUser, session: Session) -> list[MyShare]:
    """Al je delingen en links, het nieuwste eerst. Een vast aantal query's. Gasten delen
    niet (403)."""
    if user.role == "guest":
        raise errors.fail(403, **GUEST)
    shares = list(
        (
            await session.scalars(
                select(Share)
                .where(Share.owner_id == user.id)
                .order_by(Share.created_at.desc(), Share.id)
            )
        ).all()
    )
    if not shares:
        return []
    node_ids = list({s.node_id for s in shares})
    up = access.ancestry(Node.id.in_(node_ids))
    rows = await session.execute(
        select(
            up.c.origin, up.c.id, up.c.parent_id, up.c.name, up.c.deleted_at, up.c.depth
        ).order_by(up.c.origin, up.c.depth.desc())
    )
    chains: dict[uuid.UUID, list] = defaultdict(list)
    for row in rows:
        chains[row.origin].append(row)
    trashed = {
        origin for origin, chain in chains.items() if any(r.deleted_at is not None for r in chain)
    }
    live = [i for i in node_ids if i not in trashed]
    nodes = (await session.scalars(select(Node).where(Node.id.in_(live)))).all()
    described = {out.id: out for out in await describe(session, user, nodes, "owner")}
    result = []
    for share, out in zip(shares, await shares_out(session, shares), strict=True):
        node = described.get(share.node_id)
        if node is None:
            continue
        path = [
            PathPart(id=r.id, name=r.name)
            for r in chains.get(share.node_id, [])
            if r.depth > 0 and r.parent_id is not None
        ]
        result.append(MyShare(share=out, node=node, path=path))
    return result
