"""De zoekindex op PostgreSQL (0.8.2, P5, migratie 0014).

Wat hier vastligt, alleen op PostgreSQL (marker pg):
- Na de migratie staat elke node in fts_node, gelijk aan wat fts_refresh opnieuw schrijft, en de
  app zoekt met de index (FtsEngine), niet met LIKE.
- De triggers houden fts_node bij: een andere naam, nieuwe tekst, een node weg.
- check() vindt een rij die afwijkt (alleen nums), search-triggers herstelt dat met een herbouw,
  en tijdens een herbouw controleert check() niet.
- De wekelijkse controle zet een afwijkende rij meteen recht.
- De downgrade naar 0013 haalt de index weg; zoeken valt dan terug op LIKE, zoals in 0.8.1.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command

from app import analysis
from app.db import get_engine
from app.models import Node
from app.search import engine as engines
from app.search import rebuild, weekcheck
from tests.search import wereld as W
from tests.search.migratie import check_fts, migrate
from tests.search.test_search_access import kop
from tests.test_cli_db import run_cli

pytestmark = [pytest.mark.anyio, pytest.mark.pg]


async def een(settings, sql: str, **params):
    async with get_engine(settings).connect() as conn:
        return (await conn.execute(sa.text(sql), params)).scalar()


async def namen(client, headers, q: str) -> set[str]:
    r = await client.get("/api/v1/search", params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    return {hit["node"]["name"] for hit in r.json()["results"]}


async def test_de_index_staat_er_en_de_app_gebruikt_hem(client, settings):
    await W.bouw_async(settings)
    async with get_engine(settings).connect() as conn:
        assert (await engines.read_conn_info(conn)).fts is True
        chosen = await engines.engine_for(settings, conn)
        assert chosen.name == "fts"
        await conn.run_sync(check_fts)
    assert await een(settings, "SELECT count(*) FROM fts_node") == await een(
        settings, "SELECT count(*) FROM node"
    )
    eva = await kop(client, "eva")
    # Het woord staat alleen in de tekst van documenten, niet in hun naam
    gevonden = await namen(client, eva, W.TEKST_WOORD)
    assert "Verslag kwartaal.docx" in gevonden


async def test_de_triggers_houden_de_index_bij(client, settings):
    w = await W.bouw_async(settings)
    eva = await kop(client, "eva")
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.update(Node)
            .where(Node.id == w.ids["brief"])
            .values(name="Okapiplan.txt", search_name="okapiplan.txt")
        )
    assert "Okapiplan.txt" in await namen(client, eva, "okapiplan")
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(check_fts)
    num = await een(settings, "SELECT num FROM node WHERE id = :i", i=w.ids["brief"])
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text("DELETE FROM node WHERE id = :i"), {"i": w.ids["brief"]})
    assert await een(settings, "SELECT count(*) FROM fts_node WHERE rowid = :n", n=num) == 0


async def test_check_vindt_een_verschil_en_triggers_herstelt(client, settings):
    w = await W.bouw_async(settings)
    goed = await run_cli(settings, "search-check", "--volledig")
    assert goed.returncode == 0, goed.stdout.decode() + goed.stderr.decode()
    num = await een(settings, "SELECT num FROM node WHERE id = :i", i=w.ids["brief"])
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text("UPDATE fts_node SET tsv = ''::tsvector WHERE rowid = :n"), {"n": num}
        )
    fout = await run_cli(settings, "search-check", "--volledig")
    assert fout.returncode == 1
    uit = fout.stdout.decode()
    assert f"num {num}: fts_node" in uit
    assert "brief" not in uit.lower()
    assert "Herstel: python -m app.cli search-triggers" in uit
    # check() rolt zijn eigen fts_refresh terug: de fout staat er nog
    assert await een(settings, "SELECT tsv = ''::tsvector FROM fts_node WHERE rowid = :n", n=num)
    terug = await run_cli(settings, "search-triggers")
    assert terug.returncode == 0, terug.stderr.decode()
    assert "De index is herbouwd in " in terug.stdout.decode()
    assert await een(settings, "SELECT cursor FROM search_state") is None
    weer = await run_cli(settings, "search-check", "--volledig")
    assert weer.returncode == 0, weer.stdout.decode()


async def test_niet_controleren_tijdens_een_herbouw(client, settings):
    await W.bouw_async(settings)
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.text("UPDATE search_state SET cursor = 0 WHERE id = 1"))
    found = await rebuild.check(get_engine(settings))
    assert found.rebuilding and not found.ok
    # Tijdens de herbouw zoekt de app met LIKE (Keuze 12)
    async with get_engine(settings).connect() as conn:
        assert (await engines.engine_for(settings, conn)).name == "like"
    tijden = await rebuild.rebuild(get_engine(settings), chunk=7, pause=0)
    assert len(tijden) >= 2
    assert (await rebuild.check(get_engine(settings), full=True)).ok
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(check_fts)


async def test_de_weekcontrole_zet_een_verschil_recht(client, settings):
    w = await W.bouw_async(settings)
    analysis.PAUSED.clear()
    num = await een(settings, "SELECT num FROM node WHERE id = :i", i=w.ids["brief"])
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text("UPDATE fts_node SET tsv = ''::tsvector WHERE rowid = :n"), {"n": num}
        )
    found = await weekcheck.run(settings)
    assert found is not None and found.bad >= 1
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(check_fts)


async def test_downgrade_naar_0013_haalt_de_index_weg(settings):
    await W.bouw_async(settings)
    await migrate(settings, command.downgrade, "0013")
    engines.forget(settings.db_url)  # de app leest het één keer per database, bij het starten
    assert await een(settings, "SELECT to_regclass('fts_node') IS NULL")
    async with get_engine(settings).connect() as conn:
        assert (await engines.read_conn_info(conn)).fts is False
    await migrate(settings, command.upgrade, "0014")
    engines.forget(settings.db_url)
    async with get_engine(settings).connect() as conn:
        assert (await engines.read_conn_info(conn)).fts is True
        await conn.run_sync(check_fts)


async def test_terug_naar_0_8_1_met_db_downgrade_houdt_de_rijen(client, settings):
    """De route MET_DATABASE=downgrade van Platform terug: db-downgrade 0012 met 0.8.2 (0.8.1 kent
    tot 0012). Geen rij gaat verloren; de index van 0014 en de stukken van 0013 gaan weg."""
    await W.bouw_async(settings)
    voor = {
        t: await een(settings, f'SELECT count(*) FROM "{t}"')
        for t in ("node", "user", "blob", "share", "photo_geo")
    }
    uit = await run_cli(settings, "db-downgrade", "0012", "--ja")
    assert uit.returncode == 0, uit.stdout.decode() + uit.stderr.decode()
    assert "De database staat nu op revisie 0012." in uit.stdout.decode()
    assert await een(settings, "SELECT version_num FROM alembic_version") == "0012"
    assert await een(settings, "SELECT to_regclass('fts_node') IS NULL")
    na = {t: await een(settings, f'SELECT count(*) FROM "{t}"') for t in voor}
    assert na == voor
