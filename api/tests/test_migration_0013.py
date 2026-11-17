"""Migratie 0013 (release 0.8.2, P2; docs/fase-6/i3/pg-0013.md).

Op PostgreSQL: van 0012 met een boom zonder num, mpath en dead (0010 vulde ze daar niet) en
photo_geo zonder dead naar 0013 geeft wat de Python-referentie check_tree wil; fase6_fill twee
keer na elkaar geeft nullen; wat niet klopt telt hij, en herstelt hij; terug naar 0012 geeft
hetzelfde schema als een nieuwe 0012 (behalve COLLATE "C" op node.mpath), en daarna weer heen.
Op SQLite doet 0013 niets, heen en terug.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command

from app import db as app_db
from app import jobs, photo_rows
from app.config import Settings
from app.db import get_engine
from app.geo import load, places
from tests.geo import bronnen
from tests.geo.hulp import AMSTERDAM
from tests.pgboom import ZERO, boom, fill, nalopen, rows, run
from tests.search.migratie import MADE, fill_0009, migrate, settings_at

pytestmark = pytest.mark.anyio

INT32 = 2**31 - 1


@pytest.fixture
async def at_0012(tmp_path, database_url):
    """Een eigen database (PostgreSQL met PLATFORM_TEST_DATABASE_URL) op revisie 0012."""
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8,
                        database_url=database_url)  # fmt: skip
    await migrate(settings, command.upgrade, "0012")
    yield settings
    await app_db.get_engine(settings).dispose()
    app_db._engine.cache_clear()


@pytest.fixture(autouse=True)
async def _dicht(anyio_backend):
    """De engines van settings_at dicht na de test (zoals tests/search/conftest.py)."""
    yield
    while MADE:
        await app_db.get_engine(MADE.pop()).dispose()
    app_db._engine.cache_clear()


@pytest.mark.pg
async def test_van_0012_met_een_kapotte_boom(at_0012):
    settings = at_0012
    ids = await boom(settings)
    # Zoals op een cluster op 0012: num, mpath en dead nergens gezet (0010 vulde ze alleen op
    # SQLite), photo_geo met dead 0 en zonder taken_at. Eén node heeft al een num (een halve
    # vulling); de rest komt daarachter, op volgorde van created_at en id. search_seq en
    # search_state zoals db-copy ze van SQLite meenam, maar oud
    await run(settings, "UPDATE node SET taken_at = now() WHERE kind = 'file'")
    await run(settings, "INSERT INTO search_seq (id, next) VALUES (1, 5)")
    await run(settings, "INSERT INTO search_state (id, cursor) VALUES (1, 2)")
    for key in ("strand", "schelp", "notities"):
        await run(
            settings,
            "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source, dead) "
            "VALUES (:n, :o, 52, 4, 1, 2, 'exif', 0)",
            n=ids[key],
            o=ids["eva"],
        )
    await run(settings, "UPDATE node SET num = 7, dead = 1 WHERE id = :n", n=ids["notities"])
    assert await rows(settings, "SELECT count(*) FROM node WHERE mpath IS NOT NULL") == [(0,)]
    zonder = await rows(settings, "SELECT id, created_at FROM node WHERE num IS NULL")

    await migrate(settings, command.upgrade, "0013")
    count = await nalopen(settings)
    assert count == len(zonder) + 1
    order = [i for i, _t in sorted(zonder, key=lambda r: (r[1], r[0]))]
    nums = dict(await rows(settings, "SELECT id, num FROM node"))
    assert [nums[i] for i in order] == list(range(8, 8 + len(order)))
    assert nums[ids["notities"]] == 7
    assert await rows(settings, "SELECT next FROM search_seq") == [(8 + len(order),)]
    assert await rows(settings, "SELECT cursor FROM search_state") == [(None,)]
    assert await rows(settings, "SELECT last_value, is_called FROM node_num_seq") == [
        (7 + len(order), True)
    ]
    # De foto's in vakantie (in de prullenbak) zijn dead, ook in photo_geo
    geo = dict(await rows(settings, "SELECT node_id, dead FROM photo_geo"))
    assert geo == {ids["strand"]: 1, ids["schelp"]: 1, ids["notities"]: 0}
    # Twee keer na elkaar: niets meer te doen
    assert await fill(settings) == ZERO
    assert await fill(settings, repair=False) == ZERO

    # Wat niet klopt: tellen zonder te herstellen, dan herstellen, dan weer nullen
    await run(settings, "UPDATE node SET dead = 1 WHERE id = :n", n=ids["werk"])
    await run(settings, "UPDATE node SET mpath = '/kapot/' WHERE id = :n", n=ids["verslag"])
    await run(settings, "UPDATE node SET num = NULL WHERE id = :n", n=ids["los"])
    await run(settings, "ALTER TABLE photo_geo DISABLE TRIGGER geo_biu")
    await run(settings, "UPDATE photo_geo SET dead = 0, taken_at = NULL WHERE node_id = :n",
              n=ids["strand"])  # fmt: skip
    await run(settings, "ALTER TABLE photo_geo ENABLE TRIGGER geo_biu")
    await run(settings, "UPDATE search_state SET cursor = 3")
    # los had het hoogste num: zonder is search_seq.next één te hoog, na het herstel één te laag
    want = {"num": 1, "tree": 3, "photo_geo": 1, "search_seq": 1, "search_state": 1}
    assert await fill(settings, repair=False) == want
    assert await fill(settings, repair=False) == want  # nog steeds: niets hersteld
    assert await fill(settings) == want
    assert await fill(settings) == ZERO
    assert await nalopen(settings) == count
    [(los,)] = await rows(settings, "SELECT num FROM node WHERE id = :n", n=ids["los"])
    assert los == 8 + len(order)
    assert await rows(settings, "SELECT next FROM search_seq") == [(9 + len(order),)]
    assert await rows(settings, "SELECT cursor FROM search_state") == [(None,)]


async def _schema(settings: Settings) -> dict[str, set]:
    """Wat een migratie op PostgreSQL maakt: kolommen (met type, standaard, collatie en
    identity), indexen, constraints, triggers, functies en reeksen."""
    queries = {
        "columns": "SELECT table_name, column_name, data_type, is_nullable, column_default, "
        "collation_name, is_identity FROM information_schema.columns "
        "WHERE table_schema = 'public'",
        "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
        "constraints": "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace",
        "triggers": "SELECT tgname, tgrelid::regclass::text FROM pg_trigger WHERE NOT tgisinternal",
        "functions": "SELECT routine_name FROM information_schema.routines "
        "WHERE routine_schema = 'public'",
        "sequences": "SELECT sequence_name, data_type FROM information_schema.sequences "
        "WHERE sequence_schema = 'public'",
    }
    return {key: set(await rows(settings, sql)) for key, sql in queries.items()}


@pytest.mark.pg
async def test_terug_naar_0012_en_weer_heen(at_0012):
    settings = at_0012
    fresh = await _schema(settings)
    ids = await boom(settings)
    await migrate(settings, command.upgrade, "0013")
    # Waarden die alleen in BIGINT passen
    await run(
        settings,
        "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source, place_id) "
        "VALUES (:n, :o, 52, 4, 1, 2, 'exif', :p)",
        n=ids["notities"],
        o=ids["eva"],
        p=3_000_000_000,
    )
    await run(
        settings,
        "INSERT INTO playback_state (user_id, profile, node_id, position_ms, duration_ms, "
        "watched, play_count, seq, updated_at) VALUES (:u, '', :n, :pos, :dur, false, 0, "
        ":seq, now())",
        u=ids["eva"],
        n=ids["notities"],
        pos=5_000_000_000,
        dur=6_000_000_000,
        seq=-3_000_000_000,
    )
    nums = await rows(settings, "SELECT id, num, mpath, dead FROM node ORDER BY id")

    await migrate(settings, command.downgrade, "0012")
    back = await _schema(settings)
    # Alleen COLLATE "C" op node.mpath blijft (ook 0.8.1 heeft daar het goede bereik aan)
    mpath = {c for c in back["columns"] if c[:2] == ("node", "mpath")}
    assert [c[5] for c in mpath] == ["C"]
    back["columns"] = {c[:5] + (None,) + c[6:] if c[:2] == ("node", "mpath") else c
                       for c in back["columns"]}  # fmt: skip
    assert back == fresh
    assert await rows(settings, "SELECT place_id FROM photo_geo") == [(None,)]
    assert await rows(settings, "SELECT position_ms, duration_ms, seq FROM playback_state") == [
        (INT32, INT32, -INT32 - 1)
    ]
    # De data van de boom blijft zoals hij was
    assert await rows(settings, "SELECT id, num, mpath, dead FROM node ORDER BY id") == nums

    await migrate(settings, command.upgrade, "0013")
    await nalopen(settings)
    assert await fill(settings) == ZERO
    # Een database zonder de rijen van search_seq en search_state krijgt ze niet (db-copy vraagt
    # een leeg doel)
    assert await rows(settings, "SELECT count(*) FROM search_seq") == [(0,)]
    assert await rows(settings, "SELECT count(*) FROM search_state") == [(0,)]
    [(found,)] = await rows(settings, "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal")
    assert found == 10


@pytest.mark.pg
async def test_plaatsen_komen_terug_na_0012_en_weer_heen(at_0012, monkeypatch):
    """Review PG13-2. Terug naar 0012 maakt place_id boven int32 NULL, en dat is elke plaats
    (48 bits). Het land blijft, en de job geo.places vult alleen rijen zonder plaats én land, één
    keer per bouw (Setting "geo.places"). Na weer heen naar 0013 kwam de plaats zo nooit terug.
    Nu maakt 0013 land en provincie van zulke rijen leeg en haalt hij de Setting weg: de start
    van 0.8.2 zet de keten opnieuw klaar, en die vult de plaats weer in."""
    settings = at_0012
    ids = await boom(settings)
    await migrate(settings, command.upgrade, "0013")
    bronnen.gebruik(monkeypatch, bronnen.maak(settings.geo_src))
    places.forget()
    try:
        assert load.ensure(settings) == "gebouwd"
        built = load.bouw_id(Path(settings.places_db))
        await run(
            settings,
            "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) "
            "VALUES (:n, :o, 52.37403, 4.88969, 1, 2, 'exif')",
            n=ids["notities"],
            o=ids["eva"],
        )
        sql = "SELECT place_id, country, admin1 FROM photo_geo"
        amsterdam = [(AMSTERDAM, "NL", "North Holland")]
        assert AMSTERDAM > INT32
        assert await photo_rows.enqueue_places(settings, built)
        await jobs.run_pending(settings)
        assert await rows(settings, sql) == amsterdam

        # Na elke migratie nieuwe verbindingen: een oude houdt een plan met het oude type
        await migrate(settings, command.downgrade, "0012")
        await app_db.get_engine(settings).dispose()
        assert await rows(settings, sql) == [(None, "NL", "North Holland")]

        await migrate(settings, command.upgrade, "0013")
        await app_db.get_engine(settings).dispose()
        assert await rows(settings, sql) == [(None, None, None)]
        # Zoals de start van 0.8.2: places.sqlite is er al ("klaar"), dezelfde bouw
        assert await photo_rows.enqueue_places(settings, built)
        await jobs.run_pending(settings)
        assert await rows(settings, sql) == amsterdam
        # En één keer per bouw, zoals altijd
        assert not await photo_rows.enqueue_places(settings, built)
    finally:
        places.forget()


async def _sqlite_state(settings: Settings) -> dict[str, list]:
    """Het hele schema (sqlite_master) en de inhoud van elke tabel, zonder alembic_version."""
    async with get_engine(settings).connect() as conn:
        master = await conn.exec_driver_sql(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
        out: dict[str, list] = {"sqlite_master": [tuple(r) for r in master.all()]}
        tables = [r[1] for r in out["sqlite_master"] if r[0] == "table"]
        for table in tables:
            if table == "alembic_version" or table.startswith("sqlite_"):
                continue
            found = await conn.exec_driver_sql(f'SELECT * FROM "{table}"')
            out[table] = sorted((tuple(r) for r in found.all()), key=repr)
    return out


async def _version(settings: Settings) -> list:
    async with get_engine(settings).connect() as conn:
        found = await conn.exec_driver_sql("SELECT version_num FROM alembic_version")
        return [r[0] for r in found.all()]


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_op_sqlite_doet_0013_niets(tmp_path, fts5):
    """Niet pg: altijd een eigen SQLite-bestand. Heen en terug blijven schema en data gelijk;
    alleen alembic_version verandert."""
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    await fill_0009(settings)
    await migrate(settings, command.upgrade, "0012", fts5=fts5)
    before = await _sqlite_state(settings)
    await migrate(settings, command.upgrade, "0013", fts5=fts5)
    assert await _version(settings) == ["0013"]
    assert await _sqlite_state(settings) == before
    await migrate(settings, command.downgrade, "0012", fts5=fts5)
    assert await _version(settings) == ["0012"]
    assert await _sqlite_state(settings) == before
