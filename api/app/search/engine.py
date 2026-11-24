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

import copy
import json
import logging
import time as clock
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, Protocol

from sqlalchemy import (
    URL,
    BigInteger,
    Float,
    Select,
    Uuid,
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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Alias, Join
from sqlalchemy.sql.visitors import InternalTraversal
from sqlalchemy.types import NullType

from app import errors
from app.access import Step
from app.config import Settings, get_settings
from app.models import Node, PhotoOcr, User
from app.routers.files import describe, get_dir, is_trashed, tags_by_node
from app.search import perhour, snippets
from app.search.access import MAX_OR, Ranges, ranges_sql, search_ranges, second_check
from app.search.deadline import STOP, deadline
from app.search.filters import apply_all
from app.search.like import LikeFallback, check_library, own_tag
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
    if compiler.dialect.name != "sqlite":
        # PostgreSQL (0.8.2, P5): een gewone JOIN; de planner kiest zelf
        return compiler.visit_join(element, **kw)
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


@cache
def indexed(kind: type[_Indexed]) -> Alias:
    """node AS n INDEXED BY <index van kind>; één object per soort, zoals alias()."""
    return kind._construct(_NODE, name="n")


def top_level() -> Alias:
    """node AS n INDEXED BY ix_node_parent_order, voor wat op het hoogste niveau staat."""
    return indexed(_ByParent)


@compiles(_Indexed)
@compiles(_ByTime)
@compiles(_ByType)
@compiles(_ByPath)
@compiles(_ByNum)
@compiles(_ByParent)
def _indexed(element: _Indexed, compiler: Any, **kw: Any) -> str:
    out = compiler.visit_alias(element, **kw)
    if kw.get("asfrom") and compiler.dialect.name == "sqlite":
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


@cache
def alias() -> Any:
    """node AS n, één object voor elke vraag: een alias maakt bij zijn eerste .c een kopie van
    elke kolom van node, en dat kostte bij elke vraag opnieuw CPU in de event loop (review golf
    1, H2). Een alias verandert nooit; een select maakt er een nieuwe vraag mee."""
    return _NODE.alias("n")


# --- Per dialect: FTS5 op SQLite, tsvector en LIKE op PostgreSQL (0.8.2, P5) ------------------
#
# Op PostgreSQL is fts_node een tabel (rowid, tsv, otsv) en fts_name een view (rowid,
# search_name) op node (migratie 0014). Dezelfde vragen, met deze stukken per dialect.

TS_WEIGHTS = "'{0.05, 0.1, 0.6, 1.0}'"  # D (meta), C (tekst, OCR), B (titel), A (naam)

# De cache van SQLAlchemy (zoals in 0.8.1 voor elke zoekvraag): de stukken hieronder maken hun
# parameters zelf, bij het maken, en noemen in _traverse_internals alleen hun vorm: de tabel,
# of er een eigenaar is, waartegen ze groeperen, en de parameters zelf. Van een parameter telt in
# de sleutel alleen de naam en het type, nooit de waarde. Twee vragen met dezelfde vorm delen zo
# één gecompileerde SQL, en een andere vorm (een woord meer bij fts_name, een eigenaar of niet,
# meer bereiken) heeft een andere sleutel. De compileerfuncties gebruiken alleen die parameters:
# een parameter die pas bij het compileren ontstaat, zou bij een treffer in de cache de waarde van
# de eerste vraag houden. Per dialect een eigen naam als de waarde anders is (without en
# without_nums): SQLAlchemy koppelt de parameters bij een treffer op hun naam.
_T = InternalTraversal


class FtsMatch(ColumnElement):
    """De treffer op hele woorden (fts_node) of op stukken van namen (fts_name).

    SQLite: `fts_node MATCH :fts_node_expr` (of fts_name). PostgreSQL: fts_node.tsv @@ de
    tsquery, en voor de eigen nodes van owner ook otsv (met de OCR van een eigen foto; Keuze 16
    en 25a); fts_name: elk woord van ≥ 3 tekens in search_name (LIKE)."""

    _traverse_internals = [
        ("table_name", _T.dp_string),
        ("against", _T.dp_plain_obj),
        ("sqlite_match", _T.dp_clauseelement),
        ("q", _T.dp_clauseelement),
        ("owner", _T.dp_clauseelement),
        ("words", _T.dp_clauseelement_list),
    ]
    # geen Boolean: dan zet SQLAlchemy op SQLite "= 1" erachter, en MATCH mag dat niet
    type = NullType()

    def __init__(self, table_name: str, parsed: Parsed, owner: uuid.UUID | None = None) -> None:
        self.table_name = table_name
        self.against: Any = None
        self.q: Any = None
        self.owner: Any = None
        self.words: list[Any] = []
        # SQLite: de MATCH zelf, al bij het maken (de parameter gaat zo mee in de sleutel)
        expr = parsed.match if table_name == "fts_node" else parsed.names
        name = literal_column(table_name)
        self.sqlite_match = name.op("MATCH")(bindparam(f"{table_name}_expr", expr))
        if table_name == "fts_node":
            # Dezelfde naam als in FtsRank, met dezelfde waarde (dezelfde parsed)
            self.q = bindparam("fts_node_q", parsed.tsquery())
            if owner is not None:
                self.owner = bindparam("fts_node_owner", owner, type_=Uuid())
        else:
            long_words = [w for w in parsed.words if len(w) >= 3]
            self.words = [
                bindparam(f"fts_name_w{i}", "%" + _like_escape(w) + "%")
                for i, w in enumerate(long_words)
            ]

    def self_group(self, against: Any = None) -> Any:
        """Onthoudt waartegen SQLAlchemy groepeert (de AND van de WHERE): op SQLite krijgt de
        MATCH dan dezelfde haakjes als in 0.8.1 (review van PR #6, F17)."""
        found = copy.copy(self)
        found.against = against
        return found


@compiles(FtsMatch)
def _fts_match(element: FtsMatch, compiler: Any, **kw: Any) -> str:
    t = element.table_name
    if compiler.dialect.name == "sqlite":
        found = element.sqlite_match
        if element.against is not None:
            found = found.self_group(against=element.against)
        return compiler.process(found, **kw)
    if t == "fts_name":
        parts = [
            compiler.process(literal_column("fts_name.search_name").like(w, escape="\\"), **kw)
            for w in element.words
        ]
        return "(" + " AND ".join(parts or ["true"]) + ")"
    q = compiler.process(element.q, **kw)
    found = f"fts_node.tsv @@ to_tsquery('simple', {q})"
    if element.owner is None:
        return f"({found})"
    owner = compiler.process(element.owner, **kw)
    own = f"(fts_node.otsv @@ to_tsquery('simple', {q}) AND n.owner_id = {owner})"
    return f"({found} OR {own})"


def _like_escape(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FtsRank(ColumnElement):
    """De rang (lager is beter, zoals bm25). own: de OCR van een eigen foto telt mee."""

    _traverse_internals = [("own", _T.dp_boolean), ("q", _T.dp_clauseelement)]
    type = Float()

    def __init__(self, parsed: Parsed, own: bool) -> None:
        self.own = own
        self.q = bindparam("fts_node_q", parsed.tsquery())


@compiles(FtsRank)
def _fts_rank(element: FtsRank, compiler: Any, **kw: Any) -> str:
    if compiler.dialect.name == "sqlite":
        return RANK_OWN if element.own else RANK_OTHER
    q = compiler.process(element.q, **kw)
    vec = "coalesce(fts_node.otsv, fts_node.tsv)" if element.own else "fts_node.tsv"
    # normalisatie 1: gedeeld door 1 + log(lengte), zodat een korte naam voorgaat (zoals bm25)
    return f"(-ts_rank({TS_WEIGHTS}, {vec}, to_tsquery('simple', {q}), 1))"


class NotFromPocr(ColumnElement):
    """Een node van een ander met OCR-tekst uit een foto: de vraag moet ook zonder pocr passen
    (SQLite). Op PostgreSQL zit dat al in FtsMatch (otsv alleen voor de eigen nodes)."""

    _traverse_internals = [("check", _T.dp_clauseelement)]
    # geen Boolean: dan zet SQLAlchemy op SQLite "= 1" erachter, en MATCH mag dat niet
    type = NullType()

    def __init__(self, n: Any, user_id: uuid.UUID, parsed: Parsed) -> None:
        self.check = or_(
            n.c.owner_id == user_id,
            ~exists().where(PhotoOcr.node_id == n.c.id),
            text(
                "EXISTS (SELECT 1 FROM fts_node AS f2 "
                "WHERE f2.rowid = n.num AND f2.fts_node MATCH :not_pocr)"
            ).bindparams(not_pocr=parsed.match_not_pocr()),
        )


@compiles(NotFromPocr)
def _not_from_pocr_sql(element: NotFromPocr, compiler: Any, **kw: Any) -> str:
    if compiler.dialect.name != "sqlite":
        return "true"
    # tussen haakjes: de vorm is NullType, dus SQLAlchemy zet ze er zelf niet omheen
    return f"({compiler.process(element.check, **kw)})"


class AllWordsIn(ColumnElement):
    """Elk woord staat in n.search_name (SQLite: json_each en instr; PostgreSQL: strpos)."""

    _traverse_internals = [
        ("column_sql", _T.dp_string),
        ("as_json", _T.dp_clauseelement),
        ("each", _T.dp_clauseelement_list),
    ]
    # geen Boolean: dan zet SQLAlchemy op SQLite "= 1" erachter, en MATCH mag dat niet
    type = NullType()

    def __init__(self, words: Sequence[str], column_sql: str = "n.search_name") -> None:
        self.words = tuple(words)
        self.column_sql = column_sql
        self.as_json = bindparam("words", json.dumps(list(self.words)))
        self.each = [bindparam(f"word{i}", w) for i, w in enumerate(self.words)]


@compiles(AllWordsIn)
def _all_words_in(element: AllWordsIn, compiler: Any, **kw: Any) -> str:
    col = element.column_sql
    if compiler.dialect.name == "sqlite":
        words = compiler.process(element.as_json, **kw)
        return f"NOT EXISTS (SELECT 1 FROM json_each({words}) AS w WHERE instr({col}, w.value) = 0)"
    parts = [f"strpos({col}, {compiler.process(w, **kw)}) > 0" for w in element.each]
    return "(" + " AND ".join(parts or ["true"]) + ")"


class NumNotIn(ColumnElement):
    """n.num niet in deze lijst (SQLite: json_each; PostgreSQL: <> ALL)."""

    _traverse_internals = [("as_json", _T.dp_clauseelement), ("as_array", _T.dp_clauseelement)]
    # geen Boolean: dan zet SQLAlchemy op SQLite "= 1" erachter, en MATCH mag dat niet
    type = NullType()

    def __init__(self, nums: Sequence[int]) -> None:
        self.nums = [int(x) for x in nums]
        self.as_json = bindparam("without", json.dumps(self.nums))
        self.as_array = bindparam("without_nums", self.nums, type_=ARRAY(BigInteger()))


class InRanges(ColumnElement):
    """{alias}.mpath binnen de bereiken (access.ranges_sql, voor de dialect van de compiler).
    De parameters zijn op elke dialect dezelfde; de vorm is het aantal bereiken (tot MAX_OR elk
    twee parameters, daarboven één lijst)."""

    _traverse_internals = [("alias", _T.dp_string), ("binds", _T.dp_clauseelement_list)]
    # geen Boolean: dan zet SQLAlchemy op SQLite "= 1" erachter, en MATCH mag dat niet
    type = NullType()

    def __init__(self, ranges: Sequence[tuple[str, str]], alias: str = "n") -> None:
        self.ranges = list(ranges)
        self.alias = alias
        _, params = ranges_sql(self.ranges, alias)
        self.binds = [bindparam(name, value) for name, value in params.items()]


@compiles(InRanges)
def _in_ranges(element: InRanges, compiler: Any, **kw: Any) -> str:
    sql, _ = ranges_sql(element.ranges, element.alias, compiler.dialect.name)
    found = compiler.process(text(sql).bindparams(*element.binds), **kw)
    # SQLite: zonder extra haakjes, zoals in 0.8.1 (F17)
    return found if compiler.dialect.name == "sqlite" else f"({found})"


@compiles(NumNotIn)
def _num_not_in(element: NumNotIn, compiler: Any, **kw: Any) -> str:
    if compiler.dialect.name == "sqlite":
        nums = compiler.process(element.as_json, **kw)
        return f"n.num NOT IN (SELECT value FROM json_each({nums}))"
    return f"n.num <> ALL({compiler.process(element.as_array, **kw)})"


def _match(table_name: str, parsed: Parsed, owner: uuid.UUID | None = None) -> Any:
    return FtsMatch(table_name, parsed, owner)


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
    sel = sel.where(
        n.c.dead == literal_column("0"),
        n.c.kind != literal_column("'album'"),
        InRanges(scope.roots.ranges, n.name),
    )
    if scope.user.root_id is not None:
        sel = sel.where(n.c.id != scope.user.root_id)
    return apply_all(sel, n, scope.p, scope.user)


def _not_from_pocr(n: Any, scope: Scope, parsed: Parsed) -> Any:
    """Een node van een ander met OCR-tekst uit een foto: de vraag moet ook zonder pocr passen."""
    return NotFromPocr(n, scope.user.id, parsed)


def _columns(n: Any) -> list[Any]:
    return [n.c.id, n.c.num, n.c.owner_id, n.c.search_name]


def ranked_select(parsed: Parsed, scope: Scope, k: int) -> Select:
    """Ronde A (Keuze 4): gerangschikt, de eerste k."""
    n = alias()
    if scope.roots.shared:
        score = case(
            (n.c.owner_id == scope.user.id, FtsRank(parsed, True)),
            else_=FtsRank(parsed, False),
        )
    else:
        score = FtsRank(parsed, True)
    sel = (
        select(*_columns(n), score.label("s"))
        .select_from(CrossJoin(FTS_NODE, n, n.c.num == FTS_NODE.c.rowid))
        .where(_match("fts_node", parsed, scope.user.id))
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
        .where(_match("fts_node", parsed, scope.user.id))
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
            .where(_match("fts_name", parsed))
        )
        order = FTS_NAME.c.rowid
    else:
        n = indexed(_ByPath if small else _ByNum)
        sel = select(*_columns(n)).select_from(n)
        order = n.c.num
    sel = sel.where(AllWordsIn(parsed.words))
    if without:
        sel = sel.where(NumNotIn(without))
    return scoped(sel, n, scope).order_by(order).limit(k).offset(skip)


def filters_select(scope: Scope, k: int, small: bool) -> Select:
    """Alleen filters, zonder vraag (Keuze 4a), op de nieuwste eerst."""
    types = set(scope.p.types)
    if small:
        n = indexed(_ByPath)
    elif len(types) == 1:
        n = indexed(_ByType)
    else:
        n = indexed(_ByTime)
    sel = select(*_columns(n)).select_from(n)
    sel = scoped(sel, n, scope)
    return sel.order_by(n.c.modified_at.desc(), n.c.num.desc()).limit(k)


async def is_small(conn: AsyncConnection, roots: Ranges) -> bool:
    """Keuze 4a: ≤ 64 wortels met samen ≤ 20.000 nodes, geteld via ix_node_mpath. Boven 64 is
    vanaf de paden een SCAN over heel ix_node_mpath (S10): dan altijd langs de tijd."""
    if len(roots.ranges) > MAX_OR:
        return False
    sql, params = ranges_sql(roots.ranges, dialect=conn.dialect.name)
    # PostgreSQL wil een naam voor de subquery; SQLite houdt de tekst van 0.8.1 (F17)
    name = "" if conn.dialect.name == "sqlite" else " AS small"
    count = text(
        f"SELECT count(*) FROM (SELECT 1 FROM node AS n WHERE {sql} LIMIT {SMALL + 1}){name}"
    )
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


def stopped_page(p: SearchParams, engine: Literal["fts", "like"] = "fts") -> SearchPage:
    return SearchPage(
        results=[],
        more=False,
        offset=p.offset,
        partial=True,
        ranked=False,
        engine=engine,
        names_only=engine == "like",
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


@cache
def page_nodes() -> Select:
    """De kolommen die files.describe leest, van de nodes van een pagina (de ids als één
    lijst-parameter "ids"). Rijen, geen ORM-objecten: die kostten met de identity map ±1 ms
    CPU in de event loop per pagina (review golf 1, H2)."""
    n = _NODE
    return select(
        n.c.id,
        n.c.parent_id,
        n.c.owner_id,
        n.c.name,
        n.c.kind,
        n.c.mime,
        n.c.size,
        n.c.blob_id,
        n.c.created_at,
        n.c.modified_at,
        n.c.deleted_at,
    ).where(n.c.id.in_(bindparam("ids", expanding=True)))


def path_parts(paths: dict[uuid.UUID, list[Step]]) -> dict[uuid.UUID, list[PathPart]]:
    """Het pad per treffer uit de tweede controle (de keten omhoog voor wat van jezelf is, de
    deling voor wat van een ander is). Een map boven meer treffers wordt één PathPart."""
    parts: dict[uuid.UUID, PathPart] = {}
    out: dict[uuid.UUID, list[PathPart]] = {}
    for node_id, chain in paths.items():
        line = []
        for step in chain:
            part = parts.get(step.id)
            if part is None:
                part = parts[step.id] = PathPart(id=step.id, name=step.name)
            line.append(part)
        out[node_id] = line
    return out


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
        loaded = await db.execute(page_nodes(), {"ids": [r.id for r in keep]})
        by_id = {node.id: node for node in loaded}
        nodes = [by_id[r.id] for r in keep if r.id in by_id]
        tags = await tags_by_node(db, user, [node.id for node in nodes])
        where = path_parts(checked.paths)
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
        perhour.note("deadline_b")  # /beheer (Z7): ronde B raakte de grens
        if p.after is not None:
            # Een volgende pagina van ronde B: geen namen ertussen, die waren er al
            return await self._page(db, scope, [], parsed, partial=True, ranked=False)
        # Ronde C: alleen de namen
        small = await self._small(conn, scope, parsed)
        c = await run(conn, names_select(parsed, scope, k, p.offset, small=small), C_MS, "names")
        if c.hit and not stopped():
            perhour.note("deadline_c")  # /beheer (Z7): ook ronde C raakte de grens
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

log = logging.getLogger("platform.search")

# Een letter met accent en een Griekse letter, en wat cf_fold van 0014 daarvan moet maken
# (fold(), zonder accent). Met LC_CTYPE C van de database kennen lower() en [[:alnum:]] alleen
# ASCII en geeft cf_fold " λ" niet terug: dan mist de index elke andere letter.
FOLD_PROBE = ("Éλ", "eλ")

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
    if dialect == "postgresql":
        # 0014 (0.8.2, P5): de tabel fts_node
        found = await conn.exec_driver_sql("SELECT to_regclass('fts_node') IS NOT NULL")
        fts = bool(found.scalar())
        if fts:
            await _check_fold(conn)
    if dialect == "sqlite":
        version = (await conn.exec_driver_sql("SELECT sqlite_version()")).scalar()
        found = await conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fts_node'"
        )
        fts = found.scalar() is not None
    _info[key] = ConnInfo(dialect=dialect, fts=fts, sqlite_version=version)
    return _info[key]


async def _check_fold(conn: AsyncConnection) -> None:
    """cf_fold van 0014 hangt af van LC_CTYPE van de database (review van PR #6, F12): met C
    vallen alle letters buiten ASCII uit de index. Dan een waarschuwing, één keer per database
    (read_conn_info onthoudt het); zoeken met de index blijft aan, voor wat wel ASCII is."""
    probe, want = FOLD_PROBE
    got = (await conn.execute(text("SELECT cf_fold(:p)"), {"p": probe})).scalar()
    if got == want:
        return
    ctype = (
        await conn.exec_driver_sql(
            "SELECT datctype FROM pg_database WHERE datname = current_database()"
        )
    ).scalar()
    log.warning(
        "Zoeken: de database heeft LC_CTYPE %s; daarmee mist de zoekindex elke letter buiten "
        "ASCII (accenten, Grieks, Cyrillisch). Maak de database met LC_CTYPE C.UTF-8 of een "
        "andere UTF-8-locale (runbook cluster, PostgreSQL).",
        ctype,
    )


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
