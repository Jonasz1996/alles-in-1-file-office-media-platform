"""secure_delete en temp_store op elke verbinding met platform.db (bouwontwerp fase 6, §3.1a,
"Het wissen" en K1-03; A0).

Een pool die de haak voor een deel van zijn verbindingen overslaat, is een gat: dan blijven
gewiste gezichten als bytes in de database staan, of schrijft een sortering vectoren naar een
tijdelijk bestand. Daarom per soort verbinding:

- elke verbinding uit de pool (acht tegelijk open, en elke checkout tijdens echte verzoeken);
- de verbinding van een migratie (app.migrate.upgrade) en van db-downgrade (app.cli);
- alembic zelf, zonder verbinding van de app (migrations/env.py);
- het checkpoint van het uur en na een migratie (app.db.wal_truncate), het checkpoint van elke
  seconde (app.dbgate) en de controle van de week (app.search.weekcheck).
"""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import event

from app import cli, db, dbgate, jobs
from app import migrate as migrate_mod
from app.config import Settings
from app.db import get_engine, sqlite_path
from app.migrate import alembic_config
from app.search import weekcheck
from tests import test_files

pytestmark = pytest.mark.anyio

WANT = (1, 2)  # secure_delete aan, temp_store MEMORY


def _read(dbapi_conn) -> tuple[int, int]:
    """Zoals privacy() hem gebruikt: een cursor, voor sqlite3 en voor de adapter van aiosqlite."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA secure_delete")
        secure = cur.fetchone()[0]
        cur.execute("PRAGMA temp_store")
        temp = cur.fetchone()[0]
    finally:
        cur.close()
    return secure, temp


@pytest.fixture
def seen(monkeypatch) -> list[tuple[int, int]]:
    """Elke verbinding die app.db.privacy krijgt, met wat hij daarna zegt. Wie privacy bij naam
    uit app.db haalt (dbgate, env.py) of via de haak van de pool, komt hier langs."""
    found: list[tuple[int, int]] = []
    real = db.privacy

    def spy(conn) -> None:
        real(conn)
        found.append(_read(conn))

    monkeypatch.setattr(db, "privacy", spy)
    monkeypatch.setattr(weekcheck, "privacy", spy)
    return found


async def test_elke_verbinding_uit_de_pool(settings):
    engine = get_engine(settings)
    await engine.dispose()  # alles nieuw, ook de verbindingen van de migratie bij het starten
    conns = [await engine.connect() for _ in range(8)]
    try:
        raw = {id((await c.get_raw_connection()).driver_connection) for c in conns}
        assert len(raw) == 8  # echt acht verbindingen
        for conn in conns:
            secure = (await conn.exec_driver_sql("PRAGMA secure_delete")).scalar()
            temp = (await conn.exec_driver_sql("PRAGMA temp_store")).scalar()
            assert (secure, temp) == WANT
    finally:
        for conn in conns:
            await conn.close()


async def test_elke_checkout_tijdens_echte_verzoeken(client, admin, settings):
    """Elke keer dat de app een verbinding uit de pool haalt (inloggen, een map, uploads, een
    lijst), staan ze aan."""
    headers, _ = admin
    engine = get_engine(settings)
    await engine.dispose()
    found: list[tuple[int, int]] = []

    def checkout(dbapi_conn, _record, _proxy) -> None:
        found.append(_read(dbapi_conn))

    event.listen(engine.sync_engine, "checkout", checkout)
    try:
        root = (await test_files.root(client, headers))["folder"]["id"]
        for i in range(5):
            r = await test_files.put(client, headers, root, f"{i}.txt", b"tekst %d" % i)
            assert r.status_code == 201, r.text
        r = await client.get(f"/api/v1/files/{root}", headers=headers)
        assert r.status_code == 200
    finally:
        event.remove(engine.sync_engine, "checkout", checkout)
    assert len(found) >= 7
    assert set(found) == {WANT}


async def test_de_verbinding_van_een_migratie_en_van_db_downgrade(settings, monkeypatch):
    found: dict[str, tuple[int, int]] = {}
    up, down = command.upgrade, command.downgrade

    def read(name: str, cfg) -> None:
        conn = cfg.attributes["connection"]
        secure = conn.exec_driver_sql("PRAGMA secure_delete").scalar()
        temp = conn.exec_driver_sql("PRAGMA temp_store").scalar()
        found[name] = (secure, temp)

    def spy_up(cfg, revision, **kw):
        read("upgrade", cfg)
        return up(cfg, revision, **kw)

    def spy_down(cfg, revision, **kw):
        read("downgrade", cfg)
        return down(cfg, revision, **kw)

    monkeypatch.setattr(command, "upgrade", spy_up)
    monkeypatch.setattr(command, "downgrade", spy_down)
    await get_engine(settings).dispose()
    await cli.db_downgrade(settings, "0011")
    await migrate_mod.upgrade(settings)
    assert found == {"upgrade": WANT, "downgrade": WANT}


async def test_alembic_zelf(tmp_path, seen):
    """alembic zonder verbinding van de app (alembic upgrade head met de hand): env.py maakt dan
    een eigen engine, met dezelfde haak."""
    settings = Settings(data_dir=tmp_path, jwt_secret="test" * 8)

    def run() -> None:
        command.upgrade(alembic_config(settings.db_url), "head")

    await asyncio.to_thread(run)
    assert seen and set(seen) == {WANT}


async def test_de_checkpoints_en_de_controle_van_de_week(settings, seen):
    path = sqlite_path(settings.db_url)
    assert path is not None
    # Het checkpoint van het uur (en na een migratie: dezelfde wal_truncate)
    await jobs._checkpoint(settings)
    assert seen == [WANT]
    # Het checkpoint van elke seconde (app.dbgate)
    await asyncio.to_thread(dbgate._checkpoint, str(path))
    assert seen == [WANT, WANT]
    # De controle van de week: een eigen engine, alleen lezen
    await asyncio.to_thread(weekcheck._check, settings.db_url)
    assert len(seen) >= 3 and set(seen) == {WANT}


async def test_ook_na_een_herstart_van_de_pool(settings):
    """dispose() en weer verbinden: de nieuwe verbindingen hebben ze ook."""
    engine = get_engine(settings)
    for _ in range(3):
        await engine.dispose()
        async with engine.connect() as conn:
            row = (await conn.execute(sa.text("PRAGMA secure_delete"))).scalar()
            temp = (await conn.execute(sa.text("PRAGMA temp_store"))).scalar()
        assert (row, temp) == WANT
