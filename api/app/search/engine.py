"""De zoekmachine kiezen (bouwontwerp fase 6, §3.3; Keuze 10). Z0 legt het protocol en
select_engine vast; Z1 is de eigenaar en zet hier zijn FTS-motor neer.

select_engine geeft LikeFallback als de index uit staat (PLATFORM_SEARCH_INDEX=uit), als de
database geen fts_node heeft (een SQLite zonder FTS5, of PostgreSQL) of zolang er geen
FTS-motor is (tot Z1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.config import Settings
from app.models import User
from app.search.like import LikeFallback
from app.search.types import SearchPage, SearchParams, Suggestions


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


LIKE = LikeFallback()
# De FTS-motor (Z1); None tot hij er is
FTS: SearchEngine | None = None

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
