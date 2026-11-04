"""python -m app.cli search-check, search-rebuild, search-triggers en search-bench (bouwontwerp
fase 6, §3.5 en Keuze 12; de glue in app/search/bench.py, het werk in app/search/rebuild.py).

Wat hier vastligt: elk commando draait als eigen proces naast de database van de app; check
zegt bij een verschil alleen nums (nooit een naam of tekst) en exitcode 1, met de weg terug;
triggers zet terug en herbouwt; rebuild meldt per stuk zijn voortgang; bench schrijft nooit in
een map die al een database heeft. Van de stubs van zoeken blijft alleen geo-load (Z3).
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import update

from app import cli
from app.db import get_engine
from app.models import Node
from app.search import bench, schema
from tests.search import wereld as W
from tests.search.test_spike_triggers import klopt, pad
from tests.test_cli_db import run_cli

pytestmark = pytest.mark.anyio


def test_van_de_stubs_blijft_alleen_geo_load():
    assert sorted(cli.SEARCH_STUBS) == ["geo-load"]
    assert sorted(bench.COMMANDS) == [
        "search-bench",
        "search-check",
        "search-rebuild",
        "search-triggers",
    ]


async def test_check_dan_triggers_dan_weer_goed(client, settings):
    w = await W.bouw_async(settings)
    goed = await run_cli(settings, "search-check", "--n", "5000", "--seed", "1", "--volledig")
    assert goed.returncode == 0, goed.stderr.decode()
    assert " 0 verschil(len)." in goed.stdout.decode()
    async with get_engine(settings).begin() as conn:
        await conn.exec_driver_sql("DROP TRIGGER node_au_text")
        await conn.execute(
            update(Node)
            .where(Node.id == w.ids["brief"])
            .values(name="Geheim plan.txt", search_name="geheim plan.txt")
        )
    fout = await run_cli(settings, "search-check", "--n", "5000", "--seed", "1")
    assert fout.returncode == 1
    uit = fout.stdout.decode()
    assert "fts_name" in uit and "fts_node" in uit
    assert "Herstel: python -m app.cli search-triggers" in uit
    # Alleen nums: geen naam, oud of nieuw
    assert "geheim" not in uit.lower() and "brief" not in uit.lower()
    terug = await run_cli(settings, "search-triggers")
    assert terug.returncode == 0, terug.stderr.decode()
    uit = terug.stdout.decode()
    assert "triggers weg: 0, nieuw: 1;" in uit
    assert "De index is herbouwd in " in uit
    with sqlite3.connect(pad(settings)) as con:
        triggers = dict(con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"))
        cursor = con.execute("SELECT cursor FROM search_state").fetchone()[0]
    assert triggers == schema.TRIGGERS
    assert cursor is None
    await klopt(settings)
    weer = await run_cli(settings, "search-check", "--n", "5000", "--volledig")
    assert weer.returncode == 0, weer.stdout.decode()


async def test_rebuild_in_stukken_met_voortgang(client, settings):
    await W.bouw_async(settings)
    result = await run_cli(settings, "search-rebuild", "--stuk", "10")
    assert result.returncode == 0, result.stderr.decode()
    regels = result.stdout.decode().splitlines()
    assert len([r for r in regels if r.startswith("tot num ")]) >= 2
    assert regels[-1].startswith("De index is herbouwd in ")
    await klopt(settings)


async def test_check_tijdens_een_herbouw(client, settings):
    await W.bouw_async(settings)
    with sqlite3.connect(pad(settings)) as con:
        con.execute("UPDATE search_state SET cursor = 0 WHERE id = 1")
    result = await run_cli(settings, "search-check")
    assert result.returncode == 1
    assert "Er loopt een herbouw" in result.stderr.decode()
    # search-rebuild maakt hem af
    assert (await run_cli(settings, "search-rebuild")).returncode == 0
    assert (await run_cli(settings, "search-check")).returncode == 0


async def test_bench_nooit_in_een_map_met_een_database(client, settings, tmp_path):
    await W.bouw_async(settings)
    for data in (settings.data_dir, tmp_path / "anders"):
        if data != settings.data_dir:
            data.mkdir()
            sqlite3.connect(data / "platform.db").close()
        result = await run_cli(settings, "search-bench", "--data", str(data), "--n", "300")
        assert result.returncode == 1
        assert "heeft al een database" in result.stderr.decode()
    with sqlite3.connect(pad(settings)) as con:
        assert not con.execute(
            "SELECT 1 FROM user WHERE email LIKE ?", (f"%@{bench.DOMEIN}",)
        ).fetchall()


async def test_bench_maakt_de_boom(settings, tmp_path):
    data = tmp_path / "boom"
    uitkomst = tmp_path / "uitkomst.json"
    result = await run_cli(
        settings, "search-bench", "--data", str(data), "--n", "300", "--uitkomst", str(uitkomst)
    )
    assert result.returncode == 0, result.stderr.decode()
    assert "=== kort ===" in result.stdout.decode()
    assert "boom: n=300 seed=6 profiel=basis" in result.stdout.decode()
    assert uitkomst.exists()
    with sqlite3.connect(data / "platform.db") as con:
        rollen = {r[0] for r in con.execute("SELECT email FROM user")}
    assert {f"{naam}@{bench.DOMEIN}" for naam in bench.ROLLEN.values()} <= rollen
