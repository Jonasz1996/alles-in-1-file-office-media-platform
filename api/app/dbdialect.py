"""SQL die per database anders is (0.8.2: fase 6 ook op PostgreSQL; P1 pg-sql).

Op SQLite blijft de tekst van elke vraag precies wat hij was: de vormen hier geven daar dezelfde
tekst als ervoor, zodat de plannen van SQLite niet veranderen (tests/test_pg_dialect.py
vergelijkt ze byte voor byte). PostgreSQL krijgt dezelfde betekenis in zijn eigen vorm.

- Een upsert: `dialect = dbdialect.of(db)` en dan `dialect.insert(Tabel)` in één keten met
  on_conflict_do_update(index_elements=, index_where=, set_=, where=) of on_conflict_do_nothing;
  `.excluded` van dezelfde insert. De insert van SQLite compileert niet op PostgreSQL (review A0,
  contract F2). Zo in één keten ziet de wachter van Keuze 3 (test_search_no_replace) de tabel.
  insert_for(naam, tabel) geeft de insert zelf, voor wie alleen de naam van de database heeft.
- NoIndex(kolom): `+kolom` op SQLite (dan gebruikt SQLite voor die term geen index), elders de
  kolom zelf (PostgreSQL kent geen unaire + op uuid of tekst).
- FaceSeq(): de volgorde van invoegen van face: face.rowid op SQLite, face.seq op PostgreSQL
  (een identity-kolom die migratie 0013 alleen op PostgreSQL maakt; niet in het model).
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from sqlalchemy import BigInteger
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression
from sqlalchemy.sql.visitors import InternalTraversal

SQLITE = "sqlite"
POSTGRESQL = "postgresql"


def name_of(db: Any) -> str:
    """De naam van de database achter een AsyncSession of (async) verbinding."""
    found = getattr(db, "dialect", None)
    if found is None:
        found = db.get_bind().dialect
    return found.name


def is_pg(db: Any) -> bool:
    return name_of(db) == POSTGRESQL


def module_for(dialect_name: str) -> ModuleType:
    """sqlalchemy.dialects.postgresql of .sqlite (zoals origin.placed_stmt het al deed)."""
    return postgresql if dialect_name == POSTGRESQL else sqlite


def of(db: Any) -> ModuleType:
    """module_for() voor de database achter deze sessie of verbinding."""
    return module_for(name_of(db))


def insert_for(dialect_name: str, table: Any) -> Any:
    """De insert van deze database voor table: beide kennen on_conflict_do_update,
    on_conflict_do_nothing en .excluded."""
    return module_for(dialect_name).insert(table)


class NoIndex(UnaryExpression):
    """`+kolom` op SQLite, de kolom zelf elders. tight: zonder spatie na de + (zoals de oude
    literal_column("+blob_hash.blob_sha") van photo_hash); anders als de oude
    UnaryExpression(custom_op("+")) van face_rows.no_index, met spatie en tussen haakjes naast
    een vergelijking. Zo blijft de tekst op SQLite byte voor byte gelijk."""

    inherit_cache = True
    _traverse_internals = [
        *UnaryExpression._traverse_internals,
        ("tight", InternalTraversal.dp_boolean),
    ]

    def __init__(self, col: Any, *, tight: bool = False) -> None:
        super().__init__(col, operator=operators.custom_op("+"), type_=col.type)
        self.tight = tight


@compiles(NoIndex)
def _no_index(element: NoIndex, compiler: Any, **kw: Any) -> str:
    return compiler.process(element.element, **kw)


@compiles(NoIndex, SQLITE)
def _no_index_sqlite(element: NoIndex, compiler: Any, **kw: Any) -> str:
    if element.tight:
        return "+" + compiler.process(element.element, **kw)
    return compiler.visit_unary(element, **kw)


class FaceSeq(ColumnElement[int]):
    """De volgorde van invoegen van een gezicht (alias: de naam van de tabel in de vraag)."""

    inherit_cache = True
    type = BigInteger()
    _traverse_internals = [("alias", InternalTraversal.dp_string)]

    def __init__(self, alias: str = "face") -> None:
        self.alias = alias


@compiles(FaceSeq)
def _face_seq(element: FaceSeq, compiler: Any, **kw: Any) -> str:
    return f"{element.alias}.seq"


@compiles(FaceSeq, SQLITE)
def _face_seq_sqlite(element: FaceSeq, compiler: Any, **kw: Any) -> str:
    return f"{element.alias}.rowid"


def face_seq_sql(dialect_name: str, alias: str = "face") -> str:
    """Hetzelfde als tekst, voor de vragen die als tekst gebouwd worden (people/faces/read.py)."""
    return f"{alias}.rowid" if dialect_name == SQLITE else f"{alias}.seq"
