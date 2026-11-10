"""De database van één node (SQLite) overzetten naar PostgreSQL, voor het cluster (B12, B15).

    docker exec <container> python -m app.cli db-copy <postgresql+asyncpg://…> [--leegmaken]

Wat er gebeurt:

1. Een kopie van SQLite met sqlite3.Connection.backup (zoals db-backup), zodat de app mag blijven
   draaien; voor de echte overstap stop je de app eerst, anders mist het doel wat erna kwam.
2. Het doel krijgt het schema met Alembic tot dezelfde head als de bron. Een doel met rijen
   weigert het, tenzij --leegmaken: dan gaan eerst alle tabellen van het platform weg.
3. Elke tabel van de modellen gaat over in stukken van BATCH rijen, in de volgorde van de
   foreign keys, met de types van de modellen (tijden, ja/nee en JSON worden echte waarden).
   De foreign keys gaan pas aan het eind na (uitgesteld), want een map kan een hoger id hebben
   dan wat erin staat; daarna zijn ze weer zoals Alembic ze maakte.
   Berekende kolommen (node.ftype) rekent PostgreSQL zelf.
4. De reeksen van de primaire sleutels komen achter de hoogste id, en per tabel moet het aantal
   rijen in bron en doel gelijk zijn; anders faalt het commando.

Bestanden (blobs, media, office) gaan niet mee: die staan in de datamap, die het cluster deelt.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app import models  # noqa: F401  (registreert de tabellen in Base.metadata)
from app.config import Settings
from app.db import Base

BATCH = 2000


class CopyError(Exception):
    pass


@dataclass
class CopyResult:
    head: str
    rows: dict[str, int] = field(default_factory=dict)
    snapshot: Path | None = None

    @property
    def total(self) -> int:
        return sum(self.rows.values())


def _snapshot(source: Path, folder: Path, stamp: str) -> tuple[Path, str]:
    """Een kopie van SQLite die klopt, ook als de app schrijft; met de alembic-head."""
    from app.cli import read_head

    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    target = folder / f"dbcopy-{stamp}.db"
    with closing(sqlite3.connect(source)) as src:
        src.execute("PRAGMA busy_timeout=5000")
        head = read_head(src)
        if head is None:
            raise CopyError(f"{source} heeft geen (eenduidige) alembic-revisie")
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    target.chmod(0o600)
    return target, head


def _check_target(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise CopyError("het doel moet PostgreSQL zijn (postgresql+asyncpg://…)")
    if parsed.drivername != "postgresql+asyncpg":
        raise CopyError("gebruik de driver asyncpg: postgresql+asyncpg://…")


async def _target_rows(conn: AsyncConnection) -> dict[str, int]:
    names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    out = {}
    for table in Base.metadata.sorted_tables:
        if table.name in names:
            out[table.name] = (
                await conn.execute(select(func.count()).select_from(table))
            ).scalar_one()
    return out


async def _drop_platform_tables(conn: AsyncConnection) -> None:
    names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in names:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{table.name}" CASCADE'))
    await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


def _copyable(table) -> list:
    return [c for c in table.columns if c.computed is None]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _defer_foreign_keys(conn: AsyncConnection) -> list[tuple[str, str]]:
    """Maakt de foreign keys van het schema even uitstelbaar en stelt ze uit tot het eind."""
    rows = (
        await conn.execute(
            text(
                "SELECT c.conrelid::regclass::text, c.conname FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE c.contype = 'f' AND NOT c.condeferrable AND n.nspname = current_schema()"
            )
        )
    ).all()
    for table, name in rows:
        await conn.execute(text(f"ALTER TABLE {table} ALTER CONSTRAINT {_quote(name)} DEFERRABLE"))
    await conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return [(t, n) for t, n in rows]


async def _restore_foreign_keys(conn: AsyncConnection, deferred: list[tuple[str, str]]) -> None:
    """Controleert de uitgestelde foreign keys nu en zet ze terug zoals Alembic ze maakte."""
    try:
        await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    except Exception as e:  # noqa: BLE001
        raise CopyError(f"foreign keys kloppen niet na het kopiëren: {e.__cause__ or e}") from e
    for table, name in deferred:
        await conn.execute(
            text(f"ALTER TABLE {table} ALTER CONSTRAINT {_quote(name)} NOT DEFERRABLE")
        )


async def _fix_sequences(conn: AsyncConnection, table) -> None:
    for col in table.primary_key.columns:
        seq = (
            await conn.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": col.name}
            )
        ).scalar_one_or_none()
        if seq:
            await conn.execute(
                text(
                    f'SELECT setval(:s, COALESCE((SELECT MAX("{col.name}") '
                    f'FROM "{table.name}"), 0) + 1, false)'
                ),
                {"s": seq},
            )


async def db_copy(
    settings: Settings,
    target_url: str,
    *,
    empty_first: bool = False,
    say: Callable[[str], None] = print,
) -> CopyResult:
    from app.cli import BACKUP_DIR, now_stamp, sqlite_file
    from app.migrate import alembic_config

    _check_target(target_url)
    source = sqlite_file(settings)
    if not source.exists():
        raise CopyError(f"{source} bestaat niet")
    snap, head = _snapshot(source, settings.data_dir / BACKUP_DIR, now_stamp())
    result = CopyResult(head=head, snapshot=snap)
    say(f"Kopie van {source} gemaakt ({snap.name}), revisie {head}.")

    src_engine = create_async_engine(f"sqlite+aiosqlite:///{snap}")
    dst_engine = create_async_engine(target_url, hide_parameters=True)
    try:
        async with dst_engine.begin() as conn:
            rows = await _target_rows(conn)
            filled = {k: v for k, v in rows.items() if v}
            if filled and not empty_first:
                raise CopyError(
                    "het doel heeft al rijen ("
                    + ", ".join(f"{k} {v}" for k, v in sorted(filled.items())[:5])
                    + "); gebruik --leegmaken om ze eerst weg te halen"
                )
            if empty_first:
                await _drop_platform_tables(conn)

        cfg = alembic_config(target_url)

        def _upgrade(connection) -> None:
            from alembic import command

            cfg.attributes["connection"] = connection
            cfg.attributes["timezone"] = settings.timezone
            command.upgrade(cfg, head)

        async with dst_engine.begin() as conn:
            await conn.run_sync(_upgrade)
        say(f"Schema op PostgreSQL tot revisie {head}.")

        async with src_engine.connect() as src, dst_engine.begin() as dst:
            src_names = await src.run_sync(lambda c: set(inspect(c).get_table_names()))
            src_cols = {
                name: {
                    c["name"]
                    for c in await src.run_sync(lambda c, n=name: inspect(c).get_columns(n))
                }
                for name in src_names
            }
            # Foreign keys pas aan het eind controleren: een map kan een hoger id hebben dan wat
            # erin staat (verplaatsen), dus op volgorde kopiëren volstaat niet. Daarvoor is
            # geen superuser nodig, alleen eigenaar van de tabellen (Alembic maakte ze net).
            deferred = await _defer_foreign_keys(dst)
            for table in Base.metadata.sorted_tables:
                if table.name not in src_names:
                    continue
                cols = [c for c in _copyable(table) if c.name in src_cols[table.name]]
                order = list(table.primary_key.columns) or cols[:1]
                stmt = select(*cols).order_by(*order)
                copied = 0
                stream = await src.stream(stmt)
                async for part in stream.partitions(BATCH):
                    await dst.execute(table.insert(), [dict(r._mapping) for r in part])
                    copied += len(part)
                result.rows[table.name] = copied
                await _fix_sequences(dst, table)
            await _restore_foreign_keys(dst, deferred)
            for name, count in result.rows.items():
                table = Base.metadata.tables[name]
                got = (await dst.execute(select(func.count()).select_from(table))).scalar_one()
                if got != count:
                    raise CopyError(f"tabel {name}: {count} rijen in SQLite, {got} in PostgreSQL")
        say(f"{result.total} rijen in {len(result.rows)} tabellen overgezet en nageteld.")
        return result
    finally:
        await src_engine.dispose()
        await dst_engine.dispose()
