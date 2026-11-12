"""LikeFallback: zoeken op naam met LIKE, zoals in 0.7.0 (Keuze 10). Z0 zet de zoekfunctie van
0.7.0 hier achter het protocol van engine.py; Z1 is de eigenaar en maakt hem af (filters uit
het register, suggesties).

Zoeken gaat met een recursieve CTE vanaf de gekozen map omlaag (zonder map: vanaf de eigen
hoofdmap en alles wat met je gedeeld is), en stopt bij wat in de prullenbak ligt: zo komt niets
uit (of onder) de prullenbak in de resultaten. Werkt op SQLite en PostgreSQL, en met een SQLite
zonder FTS5.

Sinds Z1 is hij de terugval (Keuze 10): als FTS5 ontbreekt, met PLATFORM_SEARCH_INDEX=uit en
tijdens een herbouw. De vraag van 0.7.0 kwam hier tot Z4 ook ("De overgang" in §3.4); die is weg,
en type is sindsdien de soort van §3.4 (node.ftype: een archief is "other", een rekenblad of
presentatie geen "document"), net als in FtsEngine. Hij kent q, type, from/to, tag, library
en in, zonder de kolommen die alleen de triggers van 0010 bijhouden (ftype en mpath: op
PostgreSQL zijn er geen triggers). from/to gaat zoals filters._dates (de opnamedag van een foto
of video, anders modified_at), met de soort uit mime; library en in zoeken vanaf die map omlaag
(review golf 1, contract S3). In en library zijn zelf geen treffer, net als de eigen hoofdmap
(contract N1 en S2). De filters van Z3 en Z6 geven 501 not_built in de router (contract S1)
tot ze in het register staan; daarna past deze motor ze toe zoals FtsEngine (op de tabel node).
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import access, errors, mime
from app.media.library import get_seen
from app.models import Node, NodeTag, Tag, User
from app.routers.files import describe, get_dir, is_trashed, tags_by_node, tree
from app.search.filters import BUILTIN, FILTERS, date_where
from app.search.types import Hit, PathPart, SearchPage, SearchParams, Suggestions
from app.text import fold

MAX_WORDS = 10


def of_category(category: str):
    """SQL-voorwaarde die hetzelfde zegt als node.ftype (mime.ftype, de soorten van §3.4), zonder
    die kolom: deze motor leest geen kolom van 0010 (zie boven). Dezelfde volgorde als FTYPE_SQL
    in schema.py, dus dezelfde soort als in FtsEngine (Keuze 43): een rekenblad of presentatie is
    geen document, een archief is other (Z4 en Z6, tests/search/test_search_filters.py)."""
    m = Node.mime
    ftype = case(
        (Node.kind == "dir", "folder"),
        (or_(Node.kind != "file", m.is_(None), m == ""), "other"),
        (m.in_(mime.SPREADSHEET_TYPES), "spreadsheet"),
        (m.in_(mime.PRESENTATION_TYPES), "presentation"),
        (m.in_(mime.ARCHIVE_TYPES), "other"),
        (m == "application/pdf", "pdf"),
        (m.like("image/%"), "image"),
        (m.like("video/%"), "video"),
        (m.like("audio/%"), "audio"),
        (or_(m.like("text/%"), m.in_(mime.DOCUMENT_TYPES)), "document"),
        else_="other",
    )
    return ftype == category


async def own_tag(session: AsyncSession, user: User, tag_id: uuid.UUID) -> Tag:
    tag = await session.get(Tag, tag_id, populate_existing=True)
    if tag is None or tag.owner_id != user.id:
        raise errors.fail(404, "tag_not_found", "Tag niet gevonden")
    return tag


async def check_library(session: AsyncSession, user: User, library_id: uuid.UUID) -> uuid.UUID:
    """De hoofdmap van een bibliotheek die deze gebruiker ziet (wie de map mag lezen, zoals in
    /media); anders 404, ook als hij niet bestaat (review golf 1, veilig F3)."""
    return (await get_seen(session, user, library_id)).library.root_id


def _dated():
    """Een foto of video met een opnamedag: daarop filtert from/to (filters.DATED, uit mime)."""
    return and_(
        Node.kind == "file",
        or_(Node.mime.like("image/%"), Node.mime.like("video/%")),
        Node.taken_day.is_not(None),
    )


async def _deepest(session: AsyncSession, a: Node, b: Node) -> Node | None:
    """De diepste van twee mappen als de een in de ander ligt; anders None (niets ligt in
    allebei)."""
    if a.id == b.id:
        return a
    where = await paths(session, [a, b])
    if b.id in {part.id for part in where.get(a.id, [])}:
        return a
    if a.id in {part.id for part in where.get(b.id, [])}:
        return b
    return None


async def paths(session: AsyncSession, nodes: list[Node]) -> dict[uuid.UUID, list[PathPart]]:
    """Het pad naar elk van deze nodes, met één recursieve query omhoog."""
    parents = {n.parent_id for n in nodes if n.parent_id}
    if not parents:
        return {}
    up = (
        select(Node.id, Node.parent_id, Node.name)
        .where(Node.id.in_(parents))
        .cte("up", recursive=True)
    )
    above = aliased(Node)
    up = up.union(select(above.id, above.parent_id, above.name).where(above.id == up.c.parent_id))
    known = {row.id: row for row in await session.execute(select(up))}
    result = {}
    for node in nodes:
        chain, current = [], node.parent_id
        while current in known and len(chain) <= len(known):
            row = known[current]
            chain.append(PathPart(id=row.id, name=row.name))
            current = row.parent_id
        result[node.id] = list(reversed(chain))
    return result


async def search_roots(session: AsyncSession, user: User) -> list[uuid.UUID]:
    """Waar zoeken zonder map begint: de eigen hoofdmap (niet voor gasten) en de bovenste
    gedeelde mappen van anderen."""
    roots = [r.node_id for r in await access.shared_roots(session, user.id)]
    if user.role != "guest":
        roots.append(user.root_id)
    return roots


class LikeFallback:
    name = "like"

    async def search(self, db: AsyncSession, user: User, p: SearchParams) -> SearchPage:
        """Zoekt onder map in_ (en onder de hoofdmap van library), of anders in je eigen
        bestanden en alles wat met je gedeeld is. Elk woord van q moet in de naam staan
        (hoofdletters en accenten tellen niet). Mappen eerst, dan op naam. tags: alleen je eigen
        tags."""
        session = db
        anchor = None
        if p.in_ is not None:
            found = await get_dir(session, user, p.in_, "read")
            if found.owner and await is_trashed(session, found.node):
                raise errors.fail(404, "folder_not_found", "Map niet gevonden")
            anchor = found.node
        if p.library is not None:
            top = await session.get(Node, await check_library(session, user, p.library))
            anchor = top if anchor is None or top is None else await _deepest(session, anchor, top)
            if anchor is None:
                return SearchPage(results=[], more=False, offset=p.offset, names_only=True,
                                  engine="like")  # fmt: skip
        if anchor is not None:
            anchors, skip = [anchor.id], [anchor.id]
        else:
            anchors, skip = await search_roots(session, user), [user.root_id]
        found = tree(anchors, live=True)
        # Een album (met je gedeeld) staat buiten de boom: geen zoekresultaat
        query = (
            select(Node)
            .join(found, Node.id == found.c.id)
            .where(Node.id.not_in(skip), Node.kind != "album")
        )
        for word in fold(p.q).split()[:MAX_WORDS]:
            query = query.where(Node.search_name.contains(word, autoescape=True))
        if p.types:
            query = query.where(or_(*(of_category(t) for t in p.types)))
        dates = date_where(p, _dated(), Node.taken_day, Node.modified_at)
        if dates:
            query = query.where(*dates)
        for tag in p.tags:
            await own_tag(session, user, tag)
            query = query.where(
                select(NodeTag.node_id)
                .where(NodeTag.node_id == Node.id, NodeTag.tag_id == tag)
                .exists()
            )
        # De filters die een ander pakket aanmeldt (person van Z6; place, country en camera van
        # Z3): dezelfde functie als in FtsEngine, op de tabel node
        for f in FILTERS.values():
            if f.name not in BUILTIN:
                query = f.apply(query, Node.__table__, p, user)
        limit = p.limit
        rows = list(
            (
                await session.scalars(
                    query.order_by(Node.kind, Node.search_name, Node.id)
                    .offset(p.offset)
                    .limit(limit + 1)
                )
            ).all()
        )
        more, rows = len(rows) > limit, rows[:limit]
        tags = await tags_by_node(session, user, [n.id for n in rows])
        mine = [n for n in rows if n.owner_id == user.id]
        where = await paths(session, mine)
        # Van anderen: recht en pad in één query, het pad vanaf de gedeelde map
        grants = await access.resolve(
            session, user.id, [n.id for n in rows if n.owner_id != user.id]
        )
        permissions = {n.id: "owner" for n in mine}
        roots = set()
        for node_id, grant in grants.items():
            permissions[node_id] = grant.permission
            where[node_id] = [PathPart(id=s.id, name=s.name) for s in grant.chain[:-1]]
            if grant.root_id == node_id:
                roots.add(node_id)
        rows = [n for n in rows if n.id in permissions]  # wat intussen onzichtbaar werd
        described = await describe(session, user, rows, permissions, tags=tags, roots=roots)
        return SearchPage(
            results=[Hit(node=out, path=where.get(out.id, [])) for out in described],
            more=more,
            offset=p.offset,
            names_only=True,
            engine="like",
        )

    async def suggest(self, db: AsyncSession, user: User, q: str) -> Suggestions:
        """Suggesties zijn van Z1; tot dan geen."""
        return Suggestions()
