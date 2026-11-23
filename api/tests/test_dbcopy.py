"""db-copy: SQLite van één node naar PostgreSQL (B12, B15).

Alleen met PLATFORM_TEST_DATABASE_URL: de marker pg (zonder slaat tests/conftest.py ze over; de
job api-postgres-15 kiest ze met -m pg)."""

import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app import db
from app.config import Settings
from app.dbcopy import CopyError, db_copy
from app.migrate import upgrade
from app.models import Blob, Node, User

PG = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")
pytestmark = [pytest.mark.anyio, pytest.mark.pg]


async def _database(name: str, drop: bool = False) -> str:
    import asyncpg

    conn = await asyncpg.connect(PG.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        sql = (
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
            if drop
            else f'CREATE DATABASE "{name}"'
        )
        await conn.execute(sql)
    finally:
        await conn.close()
    return PG.rsplit("/", 1)[0] + "/" + name


@pytest.fixture
async def sqlite_with_data(tmp_path):
    """Een node met SQLite, een gebruiker, een map en een bestand, via de echte API."""
    from httpx import ASGITransport, AsyncClient

    from app.certs import CertManager
    from app.config import get_settings
    from app.main import app
    from app.security import SecurityConfig, SecurityPolicy, login_limiter

    s = Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8, database_url=""
    )
    await upgrade(s)
    app.dependency_overrides[get_settings] = lambda: s
    app.state.certs = CertManager(s)
    app.state.security = SecurityPolicy(SecurityConfig(require_2fa=False))
    login_limiter.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        from tests.conftest import ADMIN

        r = await c.post("/api/v1/auth/setup", json=ADMIN)
        assert r.status_code < 300, r.text
    from datetime import UTC, datetime

    from tests.factories import create_file, create_folder, create_user, maker

    async with maker(s)() as session:
        owner = await create_user(session, "foto@example.nl", disabled=True)
        late = await create_file(
            session,
            owner,
            name="foto.jpg",
            data=b"jpg",
            mime="image/jpeg",
            taken_at=datetime(2026, 7, 1, 12, 30, tzinfo=UTC),
        )
        # Een map die later komt dan wat erin staat (verplaatst): foreign keys op volgorde
        # van id volstaan dan niet
        folder = await create_folder(session, owner, name="Later", deleted=True)
        late.parent_id = folder.id
        await session.commit()
    app.dependency_overrides.clear()
    del app.state.certs
    del app.state.security
    yield s
    await db.get_engine(s).dispose()
    db._engine.cache_clear()


async def _count(settings: Settings, model) -> int:
    async with db.get_engine(settings).connect() as conn:
        return (await conn.execute(select(func.count()).select_from(model))).scalar_one()


async def test_copy_to_postgres(sqlite_with_data):
    s = sqlite_with_data
    name = "c_" + uuid.uuid4().hex
    url = await _database(name)
    try:
        said = []
        res = await db_copy(s, url, say=said.append)
        assert res.rows["user"] == await _count(s, User) == 2
        assert res.snapshot is not None and res.snapshot.exists()

        pg = Settings(
            data_dir=s.data_dir, web_dist=s.web_dist, jwt_secret="test" * 8, database_url=url
        )
        assert await _count(pg, User) == 2
        assert await _count(pg, Node) == await _count(s, Node)
        assert await _count(pg, Blob) == await _count(s, Blob)
        async with db.get_engine(pg).connect() as conn:
            row = (
                await conn.execute(
                    select(Node.taken_at, Node.parent_id).where(Node.name == "foto.jpg")
                )
            ).one()
            folder = (
                await conn.execute(select(Node.id, Node.deleted_at).where(Node.name == "Later"))
            ).one()
            user = (
                await conn.execute(select(User.disabled_at).where(User.email == "foto@example.nl"))
            ).one()
        assert row.taken_at.year == 2026 and row.parent_id == folder.id
        assert folder.deleted_at is not None and user.disabled_at is not None
        # De app start op het doel: de migraties zien dezelfde head en doen niets
        await upgrade(pg)

        with pytest.raises(CopyError, match="al rijen"):
            await db_copy(s, url, say=said.append)
        again = await db_copy(s, url, empty_first=True, say=said.append)
        assert again.rows["user"] == 2
        await db.get_engine(pg).dispose()
    finally:
        db._engine.cache_clear()
        await _database(name, drop=True)


async def test_copy_without_superuser(sqlite_with_data):
    """Zoals op het cluster: de gebruiker is eigenaar van de database, geen superuser. Een keten
    van mappen die elk in de vorige staan, komt dan toch over, ongeacht de volgorde van de id's."""
    import asyncpg

    s = sqlite_with_data
    from tests.factories import create_folder, maker

    async with maker(s)() as session:
        owner = await session.scalar(select(User).where(User.email == "foto@example.nl"))
        parent = None
        for i in range(12):
            parent = (await create_folder(session, owner, parent, name=f"diep{i}")).id

    name = "c_" + uuid.uuid4().hex
    # Op ClusterForge (en in de job api-postgres-15) maakt de beheerder geen rollen (geen
    # CREATEROLE): daar bestaat de rol al en is de beheerder er lid van (PLATFORM_TEST_PG_ROLE,
    # naam:wachtwoord). Anders maakt de test zelf een rol en haalt hem daarna weg.
    given = os.environ.get("PLATFORM_TEST_PG_ROLE", "")
    role, pw = given.split(":", 1) if given else ("r_" + uuid.uuid4().hex[:12], "pw")
    admin = await asyncpg.connect(PG.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        if not given:
            await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{pw}' NOSUPERUSER")
            # Zo mag ook een beheerder zonder superuser de database aan die rol geven en hem
            # daarna weghalen
            await admin.execute(f"GRANT {role} TO CURRENT_USER")
        await admin.execute(f'CREATE DATABASE "{name}" OWNER {role}')
        base = make_url(PG).set(username=role, password=pw, database=name)
        url = base.render_as_string(hide_password=False)
        res = await db_copy(s, url, say=lambda _: None)
        assert res.rows["node"] == await _count(s, Node)
        conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            deferrable = await conn.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND condeferrable"
            )
        finally:
            await conn.close()
        assert deferrable == 0
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if not given:
            await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()


async def test_refuses_sqlite_target(sqlite_with_data):
    with pytest.raises(CopyError, match="PostgreSQL"):
        await db_copy(sqlite_with_data, "sqlite+aiosqlite:///x.db")


async def _tree(settings: Settings) -> set[tuple]:
    async with db.get_engine(settings).connect() as conn:
        found = await conn.execute(select(Node.id, Node.num, Node.mpath, Node.dead))
        return {tuple(r) for r in found.all()}


@pytest.mark.pg
async def test_copy_de_boom_en_de_gezichten(sqlite_with_data):
    """Vanaf 0013 (P2; docs/fase-6/i3/pg-0013.md): num, mpath en dead komen mee zoals SQLite ze
    heeft, ook na verplaatsen en de prullenbak; photo_geo ook; face.seq in de volgorde van rowid.
    fase6_fill() heeft daarna niets te herstellen, de triggers staan weer aan, en een nieuwe map
    krijgt een nieuw num en het pad van zijn ouder."""
    from app.models import Face, PhotoGeo
    from tests.factories import create_folder, maker
    from tests.pgboom import boom, nalopen

    s = sqlite_with_data
    ids = await boom(s)
    async with maker(s)() as session:
        # werk naar de prullenbak (onder vakantie), diep eruit (naar de hoofdmap), los terug
        (await session.get(Node, ids["werk"])).parent_id = ids["vakantie"]
        (await session.get(Node, ids["diep"])).parent_id = ids["root"]
        (await session.get(Node, ids["los"])).deleted_at = None
        await session.flush()
        for key in ("strand", "schelp"):
            session.add(PhotoGeo(node_id=ids[key], owner_id=ids["eva"], lat=52.0, lon=4.0,
                                 x16=1, y16=2, source="exif"))  # fmt: skip
        # Gezichten met een id die aflopen: de volgorde van rowid is niet die van de id's
        faces = sorted((uuid.uuid4() for _ in range(6)), reverse=True)
        for face_id in faces:
            session.add(Face(id=face_id, node_id=ids["schelp"], owner_id=ids["eva"], x=0.1,
                             y=0.1, w=0.2, h=0.2, score=0.9, px=64, version=1))  # fmt: skip
            await session.flush()
        await session.commit()
    async with db.get_engine(s).connect() as conn:
        found = await conn.exec_driver_sql("SELECT id FROM face ORDER BY rowid")
        assert [uuid.UUID(str(r[0])) for r in found.all()] == faces
        src_geo = set((await conn.execute(select(PhotoGeo.node_id, PhotoGeo.dead))).all())
    assert src_geo == {(ids["strand"], 1), (ids["schelp"], 0)}
    src_tree = await _tree(s)

    name = "c_" + uuid.uuid4().hex
    url = await _database(name)
    pg = Settings(data_dir=s.data_dir, web_dist=s.web_dist, jwt_secret="test" * 8,
                  database_url=url)  # fmt: skip
    try:
        said: list[str] = []
        await db_copy(s, url, say=said.append)
        [line] = [m for m in said if m.startswith("Nagelopen met fase6_fill")]
        for part in ("num 0", "tree 0", "photo_geo 0"):
            assert part in line, line
        assert await _tree(pg) == src_tree
        assert await nalopen(pg) == len(src_tree)
        async with db.get_engine(pg).connect() as conn:
            got = await conn.execute(select(PhotoGeo.node_id, PhotoGeo.dead))
            assert set(got.all()) == src_geo
            got = await conn.exec_driver_sql("SELECT id FROM face ORDER BY seq")
            assert [r[0] for r in got.all()] == faces
            got = await conn.exec_driver_sql(
                "SELECT count(*), count(*) FILTER (WHERE tgenabled = 'D') FROM pg_trigger "
                "WHERE NOT tgisinternal"
            )
            # 10 van 0013 en 6 van de zoekindex (0014), allemaal weer aan
            assert tuple(got.one()) == (16, 0)
            # De zoekindex: leeg, de app bouwt hem op bij het starten (cursor 0)
            got = await conn.exec_driver_sql("SELECT cursor FROM search_state WHERE id = 1")
            assert got.scalar_one() == 0
            top = (await conn.execute(select(func.max(Node.num)))).scalar_one()
            werk = (await conn.execute(select(Node.mpath).where(Node.id == ids["werk"]))).one()
        # Een nieuwe map in werk (in de prullenbak, via vakantie): het volgende num, het pad
        # van werk erachter, en dead
        async with maker(pg)() as session:
            eva = await session.get(User, ids["eva"])
            new = await create_folder(session, eva, ids["werk"], "nieuw")
            await session.commit()
            new_id = new.id
        [row] = [r for r in await _tree(pg) if r[0] == new_id]
        assert row[1:] == (top + 1, f"{werk.mpath}{top + 1}/", 1)
        assert await nalopen(pg) == len(src_tree) + 1
    finally:
        await db.get_engine(pg).dispose()
        db._engine.cache_clear()
        await _database(name, drop=True)
