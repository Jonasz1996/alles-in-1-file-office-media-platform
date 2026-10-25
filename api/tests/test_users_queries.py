"""GET /users zonder N+1: het aantal SQL-statements hangt niet af van het aantal gebruikers
(geteld met before_cursor_execute op de engine van de test)."""

from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.db import get_engine
from tests.factories import create_file, create_folder, create_user, maker


@contextmanager
def counted(settings):
    statements: list[str] = []
    engine = get_engine(settings).sync_engine

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", count)


async def add_users(settings, start: int, count: int) -> None:
    async with maker(settings)() as session:
        for i in range(start, start + count):
            user = await create_user(session, f"gebruiker{i}@example.nl", quota_bytes=1000)
            folder = await create_folder(session, user, name="Map", deleted=i % 2 == 0)
            await create_file(session, user, folder.id, name="a.txt", data=f"a{i}".encode())
            await create_file(session, user, name="b.txt", data=b"b")
        await session.commit()


async def statements_for_users(client, settings, headers) -> tuple[int, int]:
    with counted(settings) as statements:
        r = await client.get("/api/v1/users", headers=headers)
    assert r.status_code == 200, r.text
    return len(statements), len(r.json())


@pytest.mark.anyio
async def test_vast_aantal_queries(client, admin, settings):
    headers, _ = admin
    await add_users(settings, 0, 1)
    few, n_few = await statements_for_users(client, settings, headers)
    await add_users(settings, 1, 49)
    many, n_many = await statements_for_users(client, settings, headers)
    assert (n_few, n_many) == (2, 51)
    assert many == few, f"{few} statements bij 2 gebruikers, {many} bij 51"


@pytest.mark.anyio
async def test_opslag_per_gebruiker_ook_vast(client, admin, settings):
    headers, _ = admin
    await add_users(settings, 0, 1)
    await client.get("/api/v1/admin/storage", headers=headers)  # de eerste meting: cache
    with counted(settings) as few:
        assert (await client.get("/api/v1/admin/storage", headers=headers)).status_code == 200
    await add_users(settings, 1, 30)
    with counted(settings) as many:
        assert (await client.get("/api/v1/admin/storage", headers=headers)).status_code == 200
    assert len(many) == len(few)
