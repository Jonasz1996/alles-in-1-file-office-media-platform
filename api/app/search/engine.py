"""De zoekmachine (bouwontwerp fase 6, §3.3; Keuzes 4, 4a, 6, 7, 9 en 10). Z0 legde het
protocol en select_engine vast; Z1 bouwt FtsEngine.

select_engine geeft LikeFallback als de index uit staat (PLATFORM_SEARCH_INDEX=uit) en als de
database geen fts_node heeft (een SQLite zonder FTS5, of PostgreSQL); anders FtsEngine.

**FtsEngine** draait de vraag van Keuze 4: fts_node eerst (CROSS JOIN), dan node, binnen de
bereiken van de wortels (access.py, Keuze 14) en de filters uit het register (filters.py), in
rondes onder een tijdsgrens (deadline.py, Keuze 9):

- **ronde A**: gerangschikt met bm25 (naam 10, titel 6, tekst 1, OCR 0,8, meta 0,5, OCR van een
  foto 0,8), bij gelijke score op num; grens settings.search_deadline_ms (120). Geeft A minder
  dan een pagina, dan komen daarna de stukken van namen (fts_name, trigram; Keuze 6), op num;
- **ronde B**, als A de grens raakt: dezelfde vraag zonder bm25 en zonder ORDER BY, in de
  volgorde van rowid, met de rest van A + 40 ms (minstens 1 ms). Een volgende pagina gaat
  verder na `after` (de laatste rowid);
- **ronde C**, als ook B de grens raakt: alleen de namen via fts_name.

Stopt een nieuwere vraag van dezelfde gebruiker deze (limits.admit, Keuze 13), dan komt er geen
volgende ronde: meteen een lege, gedeeltelijke pagina.

Zonder vraag maar met filters de vormen van Keuze 4a: langs de tijd (INDEXED BY
ix_node_live_mod, of ix_node_ftype_mod bij één soort), of vanaf de paden als de wortels samen
≤ 20.000 nodes hebben (en het er ≤ 64 zijn).

Een treffer van een ander mag nooit uit pocr komen (de OCR van een foto is alleen van de
eigenaar, Keuze 16 en 25a): voor een node van een ander met OCR-tekst moet de vraag ook zonder
pocr passen, en zijn score telt pocr niet mee. Elke treffer gaat daarna door de tweede controle
(access.second_check, Keuze 15). Geen totaal en geen score in het antwoord (Keuze 7).
"""

from __future__ import annotations

import json
import time as clock
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy import (
    URL,
    Select,
    bindparam,
    case,
    column,
    exists,
    literal_column,
    make_url,
    or_,
    select,
    table,
    text,
)
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.selectable import Alias, Join

from app import errors
from app.config import Settings, get_settings
from app.models import Node, PhotoOcr, User
from app.routers.files import describe, get_dir, is_trashed, tags_by_node
from app.search import snippets
from app.search.access import MAX_OR, Ranges, ranges_sql, search_ranges, second_check
from app.search.deadline import STOP, deadline
from app.search.filters import apply_all
from app.search.like import LikeFallback, check_library, own_tag, paths
from app.search.query import Parsed, parse
from app.search.types import Hit, PathPart, SearchPage, SearchParams, Suggestions


class SearchEngine(Protocol):
    name: Literal["fts", "like"]

    async def search(self, db: AsyncSession, user: User, p: SearchParams) -> SearchPage: ...

    async def suggest(self, db: AsyncSession, user: User, q: str) -> Suggestions: ...


@dataclass(frozen=True)
class ConnInfo:
    """Wat de database kan: het dialect, de versie van SQLite, en of de migratie de
    FTS-tabellen maakte (alleen met FTS5)."""

    dialect: str
    fts: bool
    sqlite_version: str | None = None


# De instellingen van dit verzoek (de router zet ze); anders die van get_settings()
SETTINGS: ContextVar[Settings | None] = ContextVar("zoek_settings", default=None)


def settings_now() -> Settings:
    return SETTINGS.get() or get_settings()


# De tijd per stap van dit verzoek, voor Server-Timing (alleen met PLATFORM_SEARCH_TIMING=1,
# Keuze 13): roots (de bereiken), fts (ronde A, of alleen filters), round (ronde B), names (de
# stukken van namen, ook ronde C), check (de tweede controle en de nodes laden), snip (de
# snippets). De router zet een lege dict; zonder dict wordt er niets gemeten.
TIMING: ContextVar[dict[str, float] | None] = ContextVar("zoek_timing", default=None)
STEPS = ("roots", "fts", "round", "names", "check", "snip")


@contextmanager
def timed(step: str) -> Iterator[None]:
    found = TIMING.get()
    if found is None:
        yield
        return
    start = clock.perf_counter()
    try:
        yield
    finally:
        found[step] = found.get(step, 0.0) + (clock.perf_counter() - start) * 1000


def server_timing(found: dict[str, float]) -> str:
    """De kop Server-Timing: elke stap die liep, in milliseconden."""
    return ", ".join(f"{step};dur={found[step]:.1f}" for step in STEPS if step in found)


# --- De vorm van de SQL: CROSS JOIN en INDEXED BY (SQLAlchemy kent ze niet voor SQLite) -------


class CrossJoin(Join):
    """`links CROSS JOIN rechts ON …`: SQLite begint dan altijd bij links (Keuze 4)."""

    inherit_cache = True


@compiles(CrossJoin)
def _cross_join(element: CrossJoin, compiler: Any, **kw: Any) -> str:
    # De gewone JOIN van SQLAlchemy (met zijn controle op een kruisproduct), met CROSS ervoor;
    # links staat altijd een gewone tabel (fts_node of fts_name)
    left, _, rest = compiler.visit_join(element, **kw).partition(" JOIN ")
    return f"{left} CROSS JOIN {rest}"


class _Indexed(Alias):
    """node AS n INDEXED BY <index> (Keuze 4a): de code kiest het plan, niet de planner."""

    inherit_cache = True
    index = ""


class _ByTime(_Indexed):
    inherit_cache = True
    index = "ix_node_live_mod"


class _ByType(_Indexed):
    inherit_cache = True
    index = "ix_node_ftype_mod"


class _ByPath(_Indexed):
    """Vanaf de paden: zonder INDEXED BY koos de planner bij een filter op soort of tijd zelf
    ix_node_ftype_mod of ix_node_live_mod, ook voor een klein bereik (test_search_plans.py)."""

    inherit_cache = True
    index = "ix_node_mpath"


class _ByNum(_Indexed):
    """Op num, zonder sorteren: de rijen komen meteen, en de LIMIT stopt hem."""

    inherit_cache = True
    index = "ix_node_num"


class _ByParent(_Indexed):
    """Albums staan buiten de boom (parent_id NULL; routers/files.py weigert ze): langs
    (parent_id, kind). Met owner_id erbij koos de planner ix_node_owner_taken en liep hij alle
    nodes van de eigenaar langs: 10-17 ms bij 5.000-6.500 nodes (suggest.albums)."""

    inherit_cache = True
    index = "ix_node_parent_order"


def top_level() -> Alias:
    """node AS n INDEXED BY ix_node_parent_order, voor wat op het hoogste niveau staat."""
    return _ByParent._construct(_NODE, name="n")


@compiles(_Indexed)
@compiles(_ByTime)
@compiles(_ByType)
@compiles(_ByPath)
@compiles(_ByNum)
@compiles(_ByParent)
def _indexed(element: _Indexed, compiler: Any, **kw: Any) -> str:
    out = compiler.visit_alias(element, **kw)
    if kw.get("asfrom"):
        out += f" INDEXED BY {element.index}"
    return out


_NODE = Node.__table__
FTS_NODE = table("fts_node", column("rowid"))
FTS_NAME = table("fts_name", column("rowid"))
# bm25 per kolom: name, extra, body, ocr, meta, pocr (Keuze 6). Voor een node van een ander telt
# pocr niet (0.0): ook de volgorde mag niets zeggen over de OCR van zijn foto's
_WEIGHTS = "10.0, 6.0, 1.0, 0.8, 0.5"
RANK_OWN = f"bm25(fts_node, {_WEIGHTS}, 0.8)"
RANK_OTHER = f"bm25(fts_node, {_WEIGHTS}, 0.0)"

# Ronde B krijgt de rest van A + 40 ms, minstens 1 ms; ronde C hooguit 20 ms (Keuze 9)
B_MS = 40
C_MS = 20
# Keuze 4a: vanaf de paden als de wortels samen hooguit zoveel nodes hebben
SMALL = 20_000


def alias() -> Any:
    return _NODE.alias("n")


def _match(table_name: str, expr: str) -> Any:
    return literal_column(table_name).op("MATCH")(bindparam(f"{table_name}_expr", expr))


@dataclass(frozen=True)
class Scope:
    """Waar een vraag mag kijken: de bereiken van de wortels, de filters en wie vraagt."""

    user: User
    roots: Ranges
    p: SearchParams


def scoped(sel: Select, n: Any, scope: Scope) -> Select:
    """Levend, geen album, binnen de bereiken, niet de eigen hoofdmap (zoals in LikeFallback;
    review golf 1, contract S2), en de filters uit het register. dead = 0 staat er letterlijk:
    alleen dan mag SQLite de partiële indexen gebruiken."""
    sql, params = ranges_sql(scope.roots.ranges, n.name)
    sel = sel.where(
        n.c.dead == literal_column("0"),
        n.c.kind != literal_column("'album'"),
        text(sql).bindparams(**params),
    )
    if scope.user.root_id is not None:
        sel = sel.where(n.c.id != scope.user.root_id)
    return apply_all(sel, n, scope.p, scope.user)


def _not_from_pocr(n: Any, scope: Scope, parsed: Parsed) -> Any:
    """Een node van een ander met OCR-tekst uit een foto: de vraag moet ook zonder pocr passen."""
    return or_(
        n.c.owner_id == scope.user.id,
        ~exists().where(PhotoOcr.node_id == n.c.id),
        text(
            "EXISTS (SELECT 1 FROM fts_node AS f2 "
            "WHERE f2.rowid = n.num AND f2.fts_node MATCH :not_pocr)"
        ).bindparams(not_pocr=parsed.match_not_pocr()),
    )


def _columns(n: Any) -> list[Any]:
    return [n.c.id, n.c.num, n.c.owner_id, n.c.search_name]


def ranked_select(parsed: Parsed, scope: Scope, k: int) -> Select:
    """Ronde A (Keuze 4): gerangschikt, de eerste k."""
    n = alias()
    if scope.roots.shared:
        score = case(
            (n.c.owner_id == scope.user.id, literal_column(RANK_OWN)),
            else_=literal_column(RANK_OTHER),
        )
    else:
        score = literal_column(RANK_OWN)
    sel = (
        select(*_columns(n), score.label("s"))
        .select_from(CrossJoin(FTS_NODE, n, n.c.num == FTS_NODE.c.rowid))
        .where(_match("fts_node", parsed.match))
    )
    sel = scoped(sel, n, scope)
    if scope.roots.shared:
        sel = sel.where(_not_from_pocr(n, scope, parsed))
    return sel.order_by(literal_column("s"), n.c.num).limit(k)


def stream_select(parsed: Parsed, scope: Scope, k: int, after: int | None, skip: int) -> Select:
    """Ronde B: zonder bm25 en zonder ORDER BY, in de volgorde van rowid; na `after`."""
    n = alias()
    sel = (
        select(*_columns(n))
        .select_from(CrossJoin(FTS_NODE, n, n.c.num == FTS_NODE.c.rowid))
        .where(_match("fts_node", parsed.match))
    )
    if after is not None:
        sel = sel.where(FTS_NODE.c.rowid > after)
    sel = scoped(sel, n, scope)
    if scope.roots.shared:
        sel = sel.where(_not_from_pocr(n, scope, parsed))
    return sel.limit(k).offset(skip)


def names_select(
    parsed: Parsed,
    scope: Scope,
    k: int,
    skip: int,
    without: Sequence[int] = (),
    *,
    small: bool = False,
) -> Select:
    """Stukken van namen (Keuze 6, en ronde C): elk woord staat in de gevouwen naam. Via
    fts_name (trigram) als er een woord van ≥ 3 tekens is. Anders zonder index op de naam, en
    dan kiest de code het plan zoals bij alleen filters (Keuze 4a): vanaf de paden als het bereik
    klein is (small, zie is_small), en anders op num, zonder sorteren, zodat er ook bij de grens
    rijen zijn. without: de nums die al als treffer op hele woorden kwamen."""
    if parsed.names is not None:
        n = alias()
        sel = (
            select(*_columns(n))
            .select_from(CrossJoin(FTS_NAME, n, n.c.num == FTS_NAME.c.rowid))
            .where(_match("fts_name", parsed.names))
        )
        order = FTS_NAME.c.rowid
    else:
        n = (_ByPath if small else _ByNum)._construct(_NODE, name="n")
        sel = select(*_columns(n)).select_from(n)
        order = n.c.num
    sel = sel.where(
        text(
            "NOT EXISTS (SELECT 1 FROM json_each(:words) AS w "
            "WHERE instr(n.search_name, w.value) = 0)"
        ).bindparams(words=json.dumps(list(parsed.words)))
    )
    if without:
        sel = sel.where(
            text("n.num NOT IN (SELECT value FROM json_each(:without))").bindparams(
                without=json.dumps(list(without))
            )
        )
    return scoped(sel, n, scope).order_by(order).limit(k).offset(skip)


def filters_select(scope: Scope, k: int, small: bool) -> Select:
    """Alleen filters, zonder vraag (Keuze 4a), op de nieuwste eerst."""
    types = set(scope.p.types)
    if small:
        n = _ByPath._construct(_NODE, name="n")
    elif len(types) == 1:
        n = _ByType._construct(_NODE, name="n")
    else:
        n = _ByTime._construct(_NODE, name="n")
    sel = select(*_columns(n)).select_from(n)
    sel = scoped(sel, n, scope)
    return sel.order_by(n.c.modified_at.desc(), n.c.num.desc()).limit(k)


async def is_small(conn: AsyncConnection, roots: Ranges) -> bool:
    """Keuze 4a: ≤ 64 wortels met samen ≤ 20.000 nodes, geteld via ix_node_mpath. Boven 64 is
    vanaf de paden een SCAN over heel ix_node_mpath (S10): dan altijd langs de tijd."""
    if len(roots.ranges) > MAX_OR:
        return False
    sql, params = ranges_sql(roots.ranges)
    count = text(f"SELECT count(*) FROM (SELECT 1 FROM node AS n WHERE {sql} LIMIT {SMALL + 1})")
    return (await conn.execute(count.bindparams(**params))).scalar_one() <= SMALL


# --- FtsEngine -------------------------------------------------------------------------------


@dataclass
class Round:
    rows: list[Row]
    hit: bool


def stopped() -> bool:
    """Heeft een nieuwere vraag van dezelfde gebruiker deze gestopt (Keuze 13)? Dan geen
    volgende ronde meer: de plek is voor de nieuwe vraag."""
    stop = STOP.get()
    return stop is not None and stop.stopped


def stopped_page(p: SearchParams) -> SearchPage:
    return SearchPage(
        results=[], more=False, offset=p.offset, partial=True, ranked=False, engine="fts"
    )


async def run(conn: AsyncConnection, sel: Select, ms: float, step: str = "fts") -> Round:
    """sel onder een grens van ms milliseconden; bij de grens geen rijen (de vraag stopte)."""
    with timed(step):
        async with deadline(conn, max(1, int(ms))) as d:
            rows = list((await conn.execute(sel)).all())
    return Round([] if d.hit else rows, d.hit)


async def check_in(db: AsyncSession, user: User, folder: uuid.UUID) -> None:
    """Zoals LikeFallback: in moet een map zijn die je mag zien, niet in de prullenbak."""
    found = await get_dir(db, user, folder, "read")
    if found.owner and await is_trashed(db, found.node):
        raise errors.fail(404, "folder_not_found", "Map niet gevonden")


async def hits(
    db: AsyncSession, user: User, rows: Sequence[Row], parsed: Parsed | None
) -> list[Hit]:
    """De treffers van een pagina, na de tweede controle (Keuze 15), in dezelfde volgorde, met
    match en snippet (snippets.py, Keuze 8; zonder vraag alleen filters: match name)."""
    if not rows:
        return []
    with timed("check"):
        checked = await second_check(db, user, [(r.id, r.owner_id) for r in rows])
        keep = [r for r in rows if r.id in checked.permissions]
        if not keep:
            return []
        loaded = await db.scalars(select(Node).where(Node.id.in_([r.id for r in keep])))
        by_id = {node.id: node for node in loaded}
        nodes = [by_id[r.id] for r in keep if r.id in by_id]
        tags = await tags_by_node(db, user, [node.id for node in nodes])
        where = await paths(db, [node for node in nodes if node.owner_id == user.id])
        for node_id, chain in checked.paths.items():
            where[node_id] = [PathPart(id=s.id, name=s.name) for s in chain]
        described = await describe(
            db, user, nodes, checked.permissions, tags=tags, roots=checked.roots
        )
    snips: dict[uuid.UUID, snippets.Snip] = {}
    if parsed is not None:
        with timed("snip"):
            snips = await snippets.make(db, user, [node.id for node in nodes], parsed)
    none = snippets.Snip("name", None)
    return [
        Hit(
            node=out,
            path=where.get(out.id, []),
            match=snips.get(out.id, none).match,
            snippet=snips.get(out.id, none).snippet,
        )
        for out in described
    ]


class FtsEngine:
    name = "fts"

    async def search(self, db: AsyncSession, user: User, p: SearchParams) -> SearchPage:
        settings = settings_now()
        parsed = parse(p.q)  # QueryTooLong: de router geeft 400 search_query_too_long
        if p.in_ is not None:
            await check_in(db, user, p.in_)
        if p.library is not None:
            await check_library(db, user, p.library)
        for tag_id in dict.fromkeys(p.tags):
            await own_tag(db, user, tag_id)
        if stopped():
            return stopped_page(p)
        with timed("roots"):
            roots = await search_ranges(db, user)
        if not roots.ranges or (parsed is None and not p.has_filter()):
            return SearchPage(results=[], more=False, offset=p.offset, ranked=True, engine="fts")
        scope = Scope(user, roots, p)
        conn = await db.connection()
        if parsed is None:
            return await self._filters_only(db, conn, scope, settings)
        return await self._rounds(db, conn, scope, parsed, settings)

    async def _filters_only(
        self, db: AsyncSession, conn: AsyncConnection, scope: Scope, settings: Settings
    ) -> SearchPage:
        p = scope.p
        k = p.offset + p.limit + 1
        sel = filters_select(scope, k, await is_small(conn, scope.roots))
        found = await run(conn, sel, settings.search_deadline_ms)
        page = found.rows[p.offset : p.offset + p.limit]
        return SearchPage(
            results=await hits(db, scope.user, page, None),
            more=len(found.rows) == k,
            offset=p.offset,
            partial=found.hit,
            ranked=not found.hit,
            engine="fts",
        )

    async def _rounds(
        self,
        db: AsyncSession,
        conn: AsyncConnection,
        scope: Scope,
        parsed: Parsed,
        settings: Settings,
    ) -> SearchPage:
        p = scope.p
        start = clock.perf_counter()
        a_ms = settings.search_deadline_ms

        def rest() -> float:
            return max(1.0, a_ms + B_MS - (clock.perf_counter() - start) * 1000)

        if p.after is None:
            # Ronde A, en daarna de stukken van namen als A minder dan deze pagina gaf
            k = p.offset + p.limit + 1
            a = await run(conn, ranked_select(parsed, scope, k), a_ms)
            if a.hit and stopped():
                return stopped_page(p)
            if not a.hit:
                page = a.rows[p.offset : p.offset + p.limit]
                if len(a.rows) == k:
                    return await self._page(db, scope, page, parsed, more=True)
                need = k - max(len(a.rows), p.offset)
                skip = max(0, p.offset - len(a.rows))
                without = [r.num for r in a.rows]
                small = await self._small(conn, scope, parsed)
                sel = names_select(parsed, scope, need, skip, without, small=small)
                names = await run(conn, sel, rest(), "names")
                rows = page + names.rows[: p.limit - len(page)]
                more = len(page) + len(names.rows) > p.limit
                return await self._page(db, scope, rows, parsed, more=more, partial=names.hit)
        # Ronde B: in de volgorde van rowid, verder na after
        k = p.limit + 1
        skip = p.offset if p.after is None else 0
        b = await run(conn, stream_select(parsed, scope, k, p.after, skip), rest(), "round")
        if b.hit and stopped():
            return stopped_page(p)
        if not b.hit:
            page = b.rows[: p.limit]
            more = len(b.rows) > p.limit
            after = page[-1].num if more and page else None
            return await self._page(
                db, scope, page, parsed, more=more, partial=True, ranked=False, after=after
            )
        if p.after is not None:
            # Een volgende pagina van ronde B: geen namen ertussen, die waren er al
            return await self._page(db, scope, [], parsed, partial=True, ranked=False)
        # Ronde C: alleen de namen
        small = await self._small(conn, scope, parsed)
        c = await run(conn, names_select(parsed, scope, k, p.offset, small=small), C_MS, "names")
        return await self._page(
            db,
            scope,
            c.rows[: p.limit],
            parsed,
            more=len(c.rows) > p.limit,
            partial=True,
            ranked=False,
            names_only=True,
        )

    @staticmethod
    async def _small(conn: AsyncConnection, scope: Scope, parsed: Parsed) -> bool:
        """Alleen nodig voor de namen zonder trigram (woorden van 1–2 tekens)."""
        return parsed.names is None and await is_small(conn, scope.roots)

    async def _page(
        self,
        db: AsyncSession,
        scope: Scope,
        rows: Sequence[Row],
        parsed: Parsed,
        *,
        more: bool = False,
        partial: bool = False,
        ranked: bool = True,
        names_only: bool = False,
        after: int | None = None,
    ) -> SearchPage:
        return SearchPage(
            results=await hits(db, scope.user, rows, parsed),
            more=more,
            offset=scope.p.offset,
            partial=partial,
            ranked=ranked,
            names_only=names_only,
            after=after,
            engine="fts",
        )

    async def suggest(self, db: AsyncSession, user: User, q: str) -> Suggestions:
        """suggest.py; die gebruikt names_select en run van hier (vandaar pas hier geïmporteerd)."""
        from app.search.suggest import suggest

        return await suggest(db, user, q)


LIKE = LikeFallback()
FTS: SearchEngine | None = FtsEngine()

_info: dict[str, ConnInfo] = {}


def _key(url: str | URL) -> str:
    # str(url) verbergt het wachtwoord; dan zou forget(settings.db_url) de sleutel missen
    return make_url(url).render_as_string(hide_password=False)


async def read_conn_info(conn: AsyncConnection) -> ConnInfo:
    """Leest het één keer per database (de url); daarna uit het geheugen."""
    key = _key(conn.engine.url)
    if key in _info:
        return _info[key]
    dialect = conn.dialect.name
    fts, version = False, None
    if dialect == "sqlite":
        version = (await conn.exec_driver_sql("SELECT sqlite_version()")).scalar()
        found = await conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fts_node'"
        )
        fts = found.scalar() is not None
    _info[key] = ConnInfo(dialect=dialect, fts=fts, sqlite_version=version)
    return _info[key]


def forget(url: str | None = None) -> None:
    """Na een migratie (app.migrate.upgrade) en in tests: read_conn_info leest opnieuw, voor
    deze database of (zonder url) voor alle."""
    if url is None:
        _info.clear()
    else:
        _info.pop(_key(url), None)


def select_engine(settings: Settings, conn_info: ConnInfo) -> SearchEngine:
    if settings.search_index == "uit" or not conn_info.fts or FTS is None:
        return LIKE
    return FTS


async def rebuilding(conn: AsyncConnection) -> bool:
    """Loopt er een herbouw in stukken (search_state.cursor gezet, Keuze 12)? Dan is de index
    nog niet heel."""
    found = await conn.exec_driver_sql("SELECT cursor FROM search_state WHERE id = 1")
    return found.scalar() is not None


async def engine_for(settings: Settings, conn: AsyncConnection) -> SearchEngine:
    """De motor voor dit verzoek: select_engine, en LikeFallback zolang een herbouw loopt
    (Keuze 10 en 12)."""
    chosen = select_engine(settings, await read_conn_info(conn))
    if chosen is not LIKE and await rebuilding(conn):
        return LIKE
    return chosen
