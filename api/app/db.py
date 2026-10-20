from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def _engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(conn, _record):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    return engine


def get_engine(settings: Settings) -> AsyncEngine:
    return _engine(settings.db_url)


async def get_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]
