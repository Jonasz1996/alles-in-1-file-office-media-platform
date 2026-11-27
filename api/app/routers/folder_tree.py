"""Mappen voor een mapupload, en vooraf kijken welke namen al bezet zijn (P3).

POST /api/v1/files/{id}/folders/tree {paths}
    Maakt de mappen van een mapupload aan, of hergebruikt ze: "Vakantie" en "Vakantie/2024"
    onder map id. Een bestaande map met dezelfde naam (zonder op hoofdletters te letten, zoals
    sibling()) wordt hergebruikt: uploaden naar een map met dezelfde naam voegt samen. Staat
    er een *bestand* met die naam, dan krijgt dat pad errors[pad] = "name_taken", en elk pad
    eronder ook; de andere paden gaan gewoon door. Alles in één commit, onder het slot van de
    eigenaar van de doelmap: twee gelijktijdige verzoeken maken zo geen dubbele mappen.

POST /api/v1/files/{id}/names/check {names}
    Welke van deze namen staan al in map id (zonder hoofdletters, niet in de prullenbak)? Eén
    query; maakt niets aan en reserveert niets. Zo vraagt de app één keer per batch wat er met
    dubbele bestanden moet gebeuren, voordat er een upload bestaat.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import access, errors
from app.db import Session
from app.models import Node
from app.routers.files import Gone, clean_name, get_live_dir, notify_many, sibling, user_lock
from app.security import CurrentUser
from app.text import fold

router = APIRouter(prefix="/api/v1", tags=["bestanden"])

MAX_PATHS = 1000
MAX_NAMES = 1000
MAX_DEPTH = 32

PathError = Literal["name_taken", "invalid_name", "too_deep"]


class TreeIn(BaseModel):
    paths: list[str]


class TreeOut(BaseModel):
    # Per pad (zoals de client het stuurde) het id van de map
    folders: dict[str, uuid.UUID]
    # De paden (zoals gestuurd) waarvan de map nieuw is
    created: list[str]
    errors: dict[str, PathError]


class NamesIn(BaseModel):
    names: list[str]


class Taken(BaseModel):
    # De naam zoals de client hem vroeg; id en kind zijn van wat er al staat
    name: str
    id: uuid.UUID
    kind: str


class NamesOut(BaseModel):
    taken: list[Taken]


def split_path(path: str) -> list[str] | PathError:
    """'Vakantie/2024' -> ['Vakantie', '2024']; elk deel door clean_name. Een lege tussenstap
    ('a//b') of een ongeldig deel is invalid_name, meer dan MAX_DEPTH delen too_deep."""
    parts = path.strip().strip("/").split("/")
    if len(parts) > MAX_DEPTH:
        return "too_deep"
    try:
        return [clean_name(part) for part in parts]
    except HTTPException:
        return "invalid_name"


def too_many(count: int, limit: int) -> None:
    if count > limit:
        raise errors.fail(422, "too_many", f"Te veel tegelijk (meer dan {limit})", max=limit)


@router.post("/files/{node_id}/folders/tree")
async def folders_tree(
    node_id: uuid.UUID, body: TreeIn, user: CurrentUser, session: Session
) -> TreeOut:
    too_many(len(body.paths), MAX_PATHS)
    found = await get_live_dir(session, user, node_id, "write")
    target = found.node
    owner_id = target.owner_id

    result_errors: dict[str, PathError] = {}
    wanted: dict[str, list[str]] = {}
    for path in dict.fromkeys(body.paths):
        parts = split_path(path)
        if isinstance(parts, str):
            result_errors[path] = parts
        else:
            wanted[path] = parts

    # Elk voorvoegsel één keer, van ondiep naar diep: de ouder is er dan altijd al. Sleutel is
    # het pad in kleine letters (zoals sibling() vergelijkt): "A/b" en "a/B" zijn dezelfde map.
    prefixes: dict[tuple[str, ...], tuple[str, ...]] = {}
    for parts in wanted.values():
        for depth in range(1, len(parts) + 1):
            key = tuple(p.lower() for p in parts[:depth])
            prefixes.setdefault(key, tuple(parts[:depth]))
    order = sorted(prefixes, key=len)

    # Niet met een databaseverbinding op het slot wachten
    await session.commit()
    async with user_lock(owner_id):
        # Onder het slot opnieuw: de map kan intussen in de prullenbak liggen, of de deling
        # ingetrokken zijn (op het slot kan gewacht zijn)
        if found.owner:
            if await access.trashed(session, target.id):
                raise Gone
        else:
            grant = (await access.resolve(session, user.id, [target.id])).get(target.id)
            if grant is None or grant.permission != "write":
                raise Gone

        ids: dict[tuple[str, ...], uuid.UUID] = {}
        failed: dict[tuple[str, ...], PathError] = {}
        new: dict[tuple[str, ...], Node] = {}
        try:
            for key in order:
                parent_key = key[:-1]
                if parent_key in failed:
                    failed[key] = failed[parent_key]
                    continue
                parent_id = ids[parent_key] if parent_key else target.id
                name = prefixes[key][-1]
                # Onder een map die net nieuw is, staat nog niets: dan niet zoeken
                existing = None if parent_key in new else await sibling(session, parent_id, name)
                if existing is None:
                    folder = Node(
                        id=uuid.uuid4(),
                        parent_id=parent_id,
                        owner_id=owner_id,
                        name=name,
                        kind="dir",
                    )
                    session.add(folder)
                    new[key] = folder
                    ids[key] = folder.id
                elif existing.kind == "dir":
                    ids[key] = existing.id
                else:
                    failed[key] = "name_taken"
            await session.commit()
        except IntegrityError:
            await session.rollback()  # de doelmap is intussen definitief verwijderd
            raise Gone from None

    folders: dict[str, uuid.UUID] = {}
    created: list[str] = []
    for path, parts in wanted.items():
        key = tuple(p.lower() for p in parts)
        if key in failed:
            result_errors[path] = failed[key]
            continue
        folders[path] = ids[key]
        if key in new:
            created.append(path)
    # Alleen de bovenste nieuwe mappen: wat daaronder nieuw is, zien de anderen bij het openen
    tops = [node for key, node in new.items() if key[:-1] not in new]
    await notify_many(session, tops, "node.created")
    return TreeOut(folders=folders, created=created, errors=result_errors)


@router.post("/files/{node_id}/names/check")
async def names_check(
    node_id: uuid.UUID, body: NamesIn, user: CurrentUser, session: Session
) -> NamesOut:
    too_many(len(body.names), MAX_NAMES)
    found = await get_live_dir(session, user, node_id, "write")
    names = [n.strip() for n in body.names if n.strip()]
    if not names:
        return NamesOut(taken=[])
    # Dezelfde vergelijking als sibling(): kandidaten op search_name, dan lower() in Python
    # (lower() in SQLite kent alleen ASCII)
    rows = (
        await session.execute(
            select(Node.name, Node.id, Node.kind).where(
                Node.parent_id == found.node.id,
                Node.search_name.in_({fold(n) for n in names}),
                Node.deleted_at.is_(None),
            )
        )
    ).all()
    by_lower: dict[str, tuple[uuid.UUID, str]] = {}
    for name, node_id, kind in rows:
        by_lower.setdefault(name.lower(), (node_id, kind))
    taken = [
        Taken(name=name, id=hit[0], kind=hit[1])
        for name in dict.fromkeys(names)
        if (hit := by_lower.get(name.lower())) is not None
    ]
    return NamesOut(taken=taken)
