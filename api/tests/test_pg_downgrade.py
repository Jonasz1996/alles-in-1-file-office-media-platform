"""Terug van 0.8.2 naar 0.8.1 op PostgreSQL (plan 0.8.2, P7 punt 3; docs/runbooks/cluster.md).

0.8.1 kent de database tot revisie 0012. Platform terug met `MET_DATABASE=downgrade` draait met
het image van 0.8.2 `python -m app.cli db-downgrade 0012 --ja`: dat haalt 0014 (de zoekindex) en
0013 (triggers, reeks, BIGINT, face.seq) weg. Hier met rijen zoals 0.8.2 ze schrijft: een plaats
boven int32 (de plaatsen van foto's van 0013), voortgang boven int32, gezichten met seq en de
zoekindex in fts_node. Daarna:

- is het schema gelijk aan dat van een database die alleen tot 0012 migreerde (relaties,
  kolommen, indexen, constraints, triggers, functies, views, reeksen, types en extensies), op
  COLLATE "C" van node.mpath na: dat blijft (0013, ook goed voor 0.8.1);
- staat elke rij die 0.8.1 ziet (de kolommen van 0012) er nog, op wat db-downgrade en het
  runbook noemen na: de plaats van een foto wordt NULL (land en provincie blijven), en voortgang
  buiten int32 wordt begrensd;
- kan 0.8.2 er weer op: heen naar de nieuwste revisie geeft een boom en een index die kloppen.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from alembic import command

from app import db as app_db
from app import photo_rows
from app.config import Settings
from app.geo.types import Place
from app.models import Face, PlaybackState
from tests.factories import maker
from tests.geo.hulp import AMSTERDAM
from tests.pgboom import ZERO, boom, fill, nalopen, rows, run
from tests.search.migratie import check_fts, migrate
from tests.test_cli_db import run_cli

pytestmark = [pytest.mark.anyio, pytest.mark.pg]

INT32 = (-(2**31), 2**31 - 1)
SPOT = photo_rows.Spot(52.37403, 4.88969, None, "exif")
PLACE = Place(id=AMSTERDAM, name="Amsterdam", admin1="North Holland", cc="NL", km=0.3)

# Wat een migratie op PostgreSQL maakt, uit de catalogus (zonder pg_dump: dat moet dezelfde
# versie hebben als de server, en de jobs van CI draaien PostgreSQL 15 en 17)
CATALOG = {
    "relations": "SELECT relname, relkind::text FROM pg_class "
    "WHERE relnamespace = 'public'::regnamespace",
    "columns": "SELECT table_name, column_name, data_type, udt_name, is_nullable, "
    "column_default, collation_name, is_identity, character_maximum_length "
    "FROM information_schema.columns WHERE table_schema = 'public'",
    "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
    "constraints": "SELECT conname, conrelid::regclass::text, pg_get_constraintdef(oid) "
    "FROM pg_constraint WHERE connamespace = 'public'::regnamespace",
    "triggers": "SELECT tgname, pg_get_triggerdef(oid), tgenabled::text FROM pg_trigger "
    "WHERE NOT tgisinternal",
    "functions": "SELECT proname, pg_get_function_identity_arguments(oid), prosrc FROM pg_proc "
    "WHERE pronamespace = 'public'::regnamespace",
    "views": "SELECT viewname, definition FROM pg_views WHERE schemaname = 'public'",
    "sequences": "SELECT sequence_name, data_type, start_value, minimum_value, maximum_value "
    "FROM information_schema.sequences WHERE sequence_schema = 'public'",
    "types": "SELECT typname, typtype::text FROM pg_type "
    "WHERE typnamespace = 'public'::regnamespace AND typrelid = 0 AND typelem = 0",
    "extensions": "SELECT extname FROM pg_extension",
}


async def _catalog(settings: Settings) -> dict[str, set]:
    return {key: set(await rows(settings, sql)) for key, sql in CATALOG.items()}


def _tables(catalog: dict[str, set]) -> dict[str, list[str]]:
    """De tabellen van een catalogus met hun kolommen (zonder alembic_version)."""
    tables = {name for name, kind in catalog["relations"] if kind == "r"}
    out: dict[str, list[str]] = {}
    for table, column, *_rest in catalog["columns"]:
        if table in tables and table != "alembic_version":
            out.setdefault(table, []).append(column)
    return {table: sorted(columns) for table, columns in out.items()}


async def _seen_by_081(settings: Settings, tables: dict[str, list[str]]) -> dict[str, list]:
    """Per tabel van 0012 de rijen, alleen met de kolommen van 0012 (wat 0.8.1 leest)."""
    out = {}
    for table, columns in tables.items():
        names = ", ".join(f'"{c}"' for c in columns)
        found = await rows(settings, f'SELECT {names} FROM "{table}"')
        out[table] = sorted(found, key=repr)
    return out


def _clamp(value: int) -> int:
    low, high = INT32
    return min(max(value, low), high)


def _after_downgrade(tables: dict[str, list[str]], seen: dict[str, list]) -> dict[str, list]:
    """Wat 0.8.1 na db-downgrade 0012 hoort te zien: dezelfde rijen, behalve place_id buiten int32
    (NULL) en de voortgang buiten int32 (begrensd), zoals 0013 het in zijn downgrade zegt."""
    low, high = INT32
    out = dict(seen)
    geo = tables["photo_geo"].index("place_id")
    play = [tables["playback_state"].index(c) for c in ("position_ms", "duration_ms", "seq")]

    def place(i: int, v: Any) -> Any:
        return None if i == geo and v is not None and not low <= v <= high else v

    def progress(i: int, v: Any) -> Any:
        return _clamp(v) if i in play else v

    out["photo_geo"] = sorted(
        (tuple(place(i, v) for i, v in enumerate(row)) for row in seen["photo_geo"]), key=repr
    )
    out["playback_state"] = sorted(
        (tuple(progress(i, v) for i, v in enumerate(row)) for row in seen["playback_state"]),
        key=repr,
    )
    return out


@pytest.fixture
async def at_0012(tmp_path, database_url):
    """Een tweede, eigen database, alleen gemigreerd tot 0012: het schema van 0.8.1."""
    ref = tmp_path / "0012"
    settings = Settings(data_dir=ref, web_dist=ref / "web", jwt_secret="test" * 8,
                        database_url=database_url)  # fmt: skip
    await migrate(settings, command.upgrade, "0012")
    yield settings
    await app_db.get_engine(settings).dispose()


async def _rows_of_082(settings: Settings, ids: dict[str, uuid.UUID]) -> None:
    """Rijen zoals 0.8.2 ze schrijft, met zijn eigen code en modellen waar dat kan."""
    async with maker(settings)() as session:
        shas = [
            (await rows(settings, "SELECT blob_id FROM node WHERE id = :n", n=ids[name]))[0][0]
            for name in ("strand", "schelp")
        ]
        await photo_rows.write(session, [(sha, SPOT, PLACE) for sha in shas], replace=False)
        session.add_all(
            [
                PlaybackState(user_id=ids["eva"], node_id=ids["notities"],
                              position_ms=5_000_000_000, duration_ms=6_000_000_000,
                              seq=-3_000_000_000),
                PlaybackState(user_id=ids["eva"], node_id=ids["verslag"], position_ms=1234,
                              duration_ms=5678, seq=7),
            ]
        )  # fmt: skip
        for i in range(3):
            session.add(
                Face(node_id=ids["strand"], owner_id=ids["eva"], x=0.1 * i, y=0.2, w=0.3, h=0.4,
                     score=0.9, px=64, vec=bytes([i]) * 48, version=1)
            )  # fmt: skip
        await session.commit()
    await run(
        settings,
        "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
        "SELECT blob_id, 'jaarcijfers en notulen', '', 'auteur eva', 0, now() FROM node "
        "WHERE id = :n",
        n=ids["verslag"],
    )
    await run(
        settings,
        "INSERT INTO photo_ocr (node_id, owner_id, text, words, angle, updated_at) "
        "VALUES (:n, :o, 'strandpaviljoen', '\\x00'::bytea, 0, now())",
        n=ids["strand"],
        o=ids["eva"],
    )


async def test_db_downgrade_naar_0012_geeft_het_schema_en_de_rijen_van_081(settings, at_0012):
    fresh = await _catalog(at_0012)
    tables = _tables(fresh)
    assert {"photo_geo", "playback_state", "face", "node"} <= tables.keys()

    ids = await boom(settings)
    await _rows_of_082(settings, ids)
    # Wat alleen 0.8.2 heeft, staat er echt
    geo = "SELECT place_id, country, admin1 FROM photo_geo ORDER BY node_id"
    assert await rows(settings, geo) == [(AMSTERDAM, "NL", "North Holland")] * 2
    assert AMSTERDAM > INT32[1]
    assert await rows(settings, "SELECT seq FROM face ORDER BY seq") == [(1,), (2,), (3,)]
    [(indexed,)] = await rows(settings, "SELECT count(*) FROM fts_node")
    [(live,)] = await rows(settings, "SELECT count(*) FROM node WHERE num IS NOT NULL")
    assert indexed == live > 0
    hits = await rows(
        settings,
        "SELECT n.id FROM fts_node f JOIN node n ON n.num = f.rowid "
        "WHERE f.tsv @@ to_tsquery('simple', 'jaarcijfers')",
    )
    assert hits == [(ids["verslag"],)]
    before = await _seen_by_081(settings, tables)
    expected = _after_downgrade(tables, before)
    assert expected != before  # de test ziet de plaats en de voortgang veranderen

    # Zoals Platform terug: de app is gestopt, dan db-downgrade met het image van 0.8.2
    await app_db.get_engine(settings).dispose()
    result = await run_cli(settings, "db-downgrade", "0012", "--ja")
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert "De database staat nu op revisie 0012." in result.stdout.decode()
    # Nieuwe verbindingen: een oude houdt een plan met het type van voor de downgrade
    await app_db.get_engine(settings).dispose()
    assert await rows(settings, "SELECT version_num FROM alembic_version") == [("0012",)]

    back = await _catalog(settings)
    mpath = [c for c in back["columns"] if c[:2] == ("node", "mpath")]
    assert [c[6] for c in mpath] == ["C"]
    back["columns"] = {
        c[:6] + (None,) + c[7:] if c[:2] == ("node", "mpath") else c for c in back["columns"]
    }
    for key in CATALOG:
        assert back[key] == fresh[key], key

    after = await _seen_by_081(settings, tables)
    assert after == expected
    assert await rows(settings, geo) == [(None, "NL", "North Holland")] * 2
    play = "SELECT position_ms, duration_ms, seq FROM playback_state ORDER BY position_ms"
    assert await rows(settings, play) == [(1234, 5678, 7), (INT32[1], INT32[1], INT32[0])]
    assert len(after["face"]) == 3

    # En weer naar 0.8.2: de boom en de index kloppen
    await migrate(settings, command.upgrade, "head")
    await app_db.get_engine(settings).dispose()
    await nalopen(settings)
    assert await fill(settings, repair=False) == ZERO
    async with app_db.get_engine(settings).connect() as conn:
        await conn.run_sync(check_fts)
