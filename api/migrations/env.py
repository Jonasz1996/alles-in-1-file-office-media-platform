import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401 - registreert de tabellen
from app.db import Base

config = context.config
target_metadata = Base.metadata


def run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        run(connection)
        return
    engine = create_async_engine(config.get_main_option("sqlalchemy.url"))
    async with engine.connect() as conn:
        await conn.run_sync(run)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migraties worden niet gebruikt")
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    asyncio.run(main())
