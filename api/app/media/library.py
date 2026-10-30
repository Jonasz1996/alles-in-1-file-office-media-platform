"""Bibliotheken: een map in de boom met een soort erop (Keuze 4). Meer niet.

- Maken: alleen de eigenaar van de map, en alleen een map die niet in de prullenbak ligt (zelf of
  een map erboven). Geen nesting: een bibliotheek boven, op of onder een andere geeft 409
  media_library_nested. Hooguit MAX_LIBRARIES per gebruiker.
- Wie hem ziet: de eigenaar (zolang de map niet in de prullenbak ligt), en iedereen die de map
  zelf mag lezen via een deling (access.resolve op de map, B18). Een deling van alleen een
  submap toont de bibliotheek niet; de bestanden spelen dan wel vanuit Bestanden. Wie mag
  schrijven (de eigenaar, of een deling met schrijven), mag ook opnieuw scannen en metadata
  verbeteren (Keuze 43).
- Weghalen haalt alleen de rijen van media weg (CASCADE naar media_item en media_show); de
  bestanden blijven.

Een gast is een gebruiker met minder rechten: hij ziet een gedeelde bibliotheek, maar maakt er
zelf geen (zijn eigen map mag hij niet eens openen: files.get_access geeft 403 guest).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import Integer, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, errors
from app.media import errors as media_errors
from app.media.types import Library, LibraryIn, LibraryPatch
from app.models import MediaItem, MediaLibrary, Node, User

MAX_LIBRARIES = 20
COUNTED = ("movie", "episode", "video", "track")  # wat item_count telt (geen extra's)
FALLBACK_NAME = "Bibliotheek"

# Maken gaat in dit proces na elkaar: twee bibliotheken tegelijk in elkaar (een map en zijn
# submap) zouden anders allebei door de controle op nesting komen. Het slot wordt genomen
# voordat de sessie iets schrijft (commit vóór het wachten op een slot).
_create_lock = asyncio.Lock()


@dataclass
class Seen:
    """Een bibliotheek en wat de vrager ermee mag: owner, write of read."""

    library: MediaLibrary
    permission: str

    @property
    def mine(self) -> bool:
        return self.permission == "owner"

    @property
    def can_edit(self) -> bool:
        return self.permission in ("owner", "write")


def not_found():
    return errors.fail(404, "not_found", "Niet gevonden")


async def trashed_roots(session: AsyncSession, root_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Welke van deze mappen in de prullenbak liggen (zelf of een map erboven). Eén query."""
    if not root_ids:
        return set()
    up = access.ancestry(Node.id.in_(root_ids))
    rows = await session.scalars(select(up.c.origin).where(up.c.deleted_at.is_not(None)).distinct())
    return set(rows.all())


async def seen(session: AsyncSession, user: User) -> list[Seen]:
    """De bibliotheken die deze gebruiker ziet, met zijn recht, op naam. Twee queries voor de
    eigen bibliotheken en één access.resolve voor die van anderen."""
    own: list[MediaLibrary] = []
    if user.role != "guest":
        rows = await session.scalars(select(MediaLibrary).where(MediaLibrary.owner_id == user.id))
        own = list(rows.all())
    gone = await trashed_roots(session, [lib.root_id for lib in own])
    out = [Seen(lib, "owner") for lib in own if lib.root_id not in gone]
    rows = await session.scalars(select(MediaLibrary).where(MediaLibrary.owner_id != user.id))
    others = list(rows.all())
    if others:
        # Een map komt nooit via een gedeeld album binnen (albums bevatten alleen bestanden),
        # dus elke Grant hier is een deling van de map zelf of van een map erboven
        grants = await access.resolve(session, user.id, [lib.root_id for lib in others])
        for lib in others:
            grant = grants.get(lib.root_id)
            if grant is not None:
                out.append(Seen(lib, "write" if grant.permission == "write" else "read"))
    out.sort(key=lambda s: (s.library.name.casefold(), str(s.library.id)))
    return out


async def visible(session: AsyncSession, user_id: uuid.UUID) -> list[MediaLibrary]:
    """De bibliotheken die deze gebruiker ziet (wie de map mag lezen)."""
    user = await session.get(User, user_id)
    if user is None:
        return []
    return [s.library for s in await seen(session, user)]


async def get_seen(
    session: AsyncSession, user: User, library_id: uuid.UUID, need: str = "read"
) -> Seen:
    """Een bibliotheek die deze gebruiker ziet, anders 404 (ook als hij niet bestaat). need
    write of owner: 403 media_not_owner als hij hem wel ziet maar te weinig mag."""
    library = await session.get(MediaLibrary, library_id)
    if library is None:
        raise not_found()
    if library.owner_id == user.id and user.role != "guest":
        if await trashed_roots(session, [library.root_id]):
            raise not_found()
        return Seen(library, "owner")
    grant = (await access.resolve(session, user.id, [library.root_id])).get(library.root_id)
    if grant is None:
        raise not_found()
    found = Seen(library, "write" if grant.permission == "write" else "read")
    if need == "owner" or (need == "write" and not found.can_edit):
        raise media_errors.not_owner()
    return found


async def counts(session: AsyncSession, library_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Het aantal items per bibliotheek (aanwezig, geen extra's), in één query."""
    if not library_ids:
        return {}
    rows = await session.execute(
        select(MediaItem.library_id, func.count())
        .where(
            MediaItem.library_id.in_(library_ids),
            MediaItem.present.is_(True),
            MediaItem.kind.in_(COUNTED),
        )
        .group_by(MediaItem.library_id)
    )
    return {library_id: n for library_id, n in rows}


def view(found: Seen, count: int | None = None) -> Library:
    lib = found.library
    return Library(
        id=lib.id,
        name=lib.name,
        kind=lib.kind,  # type: ignore[arg-type]
        root_id=lib.root_id,
        mine=found.mine,
        item_count=lib.item_count if count is None else count,
        scan_state=lib.scan_state,  # type: ignore[arg-type]
        scanned_at=lib.scanned_at,
        online=lib.online,
    )


async def nested(session: AsyncSession, root_id: uuid.UUID) -> uuid.UUID | None:
    """Een bibliotheek boven, op of onder deze map: zijn id, anders None. Alles in één boom
    is van dezelfde eigenaar, dus dit id is nooit dat van een ander."""
    up = access.ancestry(Node.id == root_id)
    above = await session.scalar(
        select(MediaLibrary.id).join(up, MediaLibrary.root_id == up.c.id).limit(1)
    )
    if above is not None:
        return above
    top = select(Node.id, literal(0, Integer).label("depth")).where(Node.id == root_id)
    down = top.cte("below", recursive=True)
    child = select(Node.id, down.c.depth + 1).where(Node.parent_id == down.c.id, Node.kind == "dir")
    down = down.union_all(child)
    return await session.scalar(
        select(MediaLibrary.id).join(down, MediaLibrary.root_id == down.c.id).limit(1)
    )


async def create(session: AsyncSession, user: User, body: LibraryIn) -> Seen:
    """Een bibliotheek maken en de eerste scan klaarzetten (commit hier). 404 als de map niet te
    zien is, 403 als hij van een ander is (of de vrager een gast is), 422 als het geen map is
    of hij in de prullenbak ligt, 422 boven de grens en 409 bij nesting."""
    from app.media import scanner

    async with _create_lock:
        node = await session.get(Node, body.root_id)
        if node is None:
            raise not_found()
        if node.owner_id != user.id or user.role == "guest":
            if node.owner_id != user.id:
                grant = (await access.resolve(session, user.id, [node.id])).get(node.id)
                if grant is None:
                    raise not_found()
            raise media_errors.not_owner()
        if node.kind != "dir" or await trashed_roots(session, [node.id]):
            raise media_errors.library_not_folder()
        mine = await session.scalar(
            select(func.count()).select_from(MediaLibrary).where(MediaLibrary.owner_id == user.id)
        )
        if (mine or 0) >= MAX_LIBRARIES:
            raise media_errors.library_limit(MAX_LIBRARIES)
        other = await nested(session, node.id)
        if other is not None:
            raise media_errors.library_nested(str(other))
        library = MediaLibrary(
            id=uuid.uuid4(),
            owner_id=user.id,
            root_id=node.id,
            kind=body.kind,
            name=(body.name or node.name or FALLBACK_NAME)[:255],
            online=bool(body.online),
            scan_state="queued",
        )
        session.add(library)
        try:
            await session.flush()
        except IntegrityError:  # dezelfde map in een ander proces (uq_media_library_root)
            await session.rollback()
            other = await session.scalar(
                select(MediaLibrary.id).where(MediaLibrary.root_id == body.root_id)
            )
            raise media_errors.library_nested(str(other or "")) from None
        await scanner.queue_full(session, library.id)
        await session.commit()
    scanner.activate()
    return Seen(library, "owner")


async def update(
    session: AsyncSession, user: User, library_id: uuid.UUID, body: LibraryPatch
) -> Seen:
    """Naam, taal of online opzoeken wijzigen: alleen de eigenaar."""
    found = await get_seen(session, user, library_id, "owner")
    lib = found.library
    if body.name is not None:
        lib.name = body.name[:255]
    if body.online is not None:
        lib.online = body.online
    if body.language is not None:
        lib.language = body.language
    await session.commit()
    return found


async def delete(session: AsyncSession, user: User, library_id: uuid.UUID) -> None:
    """Alleen de rijen van media weg (CASCADE); de bestanden blijven."""
    found = await get_seen(session, user, library_id, "owner")
    await session.delete(found.library)
    await session.commit()


async def watchers(session: AsyncSession, library: MediaLibrary) -> set[uuid.UUID]:
    """Wie de bibliotheek ziet, voor de events: de eigenaar en wie de map via een deling ziet
    (ook met iets in de prullenbak: dan krijgt hij een event voor iets wat hij niet meer ziet,
    zoals bij files.notify)."""
    seen_by = await access.watchers(session, [library.root_id])
    return {library.owner_id, *seen_by.get(library.root_id, {}).keys()}
