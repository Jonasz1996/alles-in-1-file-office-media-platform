"""De wekelijkse search-check (bouwontwerp fase 6, Keuze 12 en R4; I1): app/search/weekcheck.py,
het opruimwerk dat eens per week 200 nodes van de index met de bron vergelijkt, en de uitkomst in
GET /admin/search (AdminSearch.check).

Wat hier vastligt:
- de eerste keer meteen, daarna pas na een week; de tijd staat in Setting "search_check", dus
  ook over een herstart heen; de uitkomst staat in GET /admin/search;
- een verschil komt op /beheer, met in het log alleen aantallen (geen num, naam of tekst);
- niet tijdens de pauze van /beheer, niet tijdens een herbouw, niet zonder FTS5 of op
  PostgreSQL, en zonder nodes geen uitkomst;
- de controle loopt in een eigen draad, met 200 nodes, en kan niet schrijven (query_only): hij
  houdt de schrijver nooit vast.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app import analysis, jobs
from app.db import get_engine
from app.models import Node, now
from app.search import rebuild, weekcheck
from tests.search import wereld as W
from tests.search.test_search_access import kop
from tests.search.test_spike_triggers import sql

pytestmark = pytest.mark.anyio

ADMIN = "/api/v1/admin/search"


@pytest.fixture(autouse=True)
def _zonder_pauze():
    analysis.PAUSED.clear()
    yield
    analysis.PAUSED.clear()


async def opgeslagen(settings) -> dict | None:
    rows = await sql(settings, "SELECT value FROM setting WHERE key = :k", k=weekcheck.KEY)
    if not rows:
        return None
    value = rows[0][0]
    return value if isinstance(value, dict) else json.loads(value)


async def tel(settings) -> int:
    return (await sql(settings, "SELECT count(*) FROM node WHERE num IS NOT NULL"))[0][0]


async def zet_tijd(settings, check: dict, at: datetime) -> None:
    value = json.dumps({**check, "at": at.isoformat()})
    await sql(settings, "UPDATE setting SET value = :v WHERE key = :k", v=value, k=weekcheck.KEY)


def bespied(monkeypatch) -> list[str]:
    """Telt hoe vaak de controle zelf liep (weekcheck._check, in zijn draad)."""
    gelopen: list[str] = []
    echt = weekcheck._check

    def check(url: str) -> rebuild.Checked:
        gelopen.append(url)
        return echt(url)

    monkeypatch.setattr(weekcheck, "_check", check)
    return gelopen


async def test_de_eerste_keer_meteen_en_daarna_pas_na_een_week(client, settings):
    assert jobs._housekeeping[weekcheck.NAME].every == weekcheck.TICK
    await W.bouw_async(settings)
    ada = await kop(client, "ada")
    r = await client.get(ADMIN, headers=ada)
    assert r.status_code == 200 and r.json()["check"] is None
    voor = now()
    # Via het opruimwerk van de Runner, zoals in de app
    assert weekcheck.NAME in await jobs.run_housekeeping(settings)
    r = await client.get(ADMIN, headers=ada)
    check = r.json()["check"]
    assert check["bad"] == 0
    assert check["sampled"] == min(weekcheck.SAMPLE, await tel(settings)) > 20
    eerst = datetime.fromisoformat(check["at"])
    assert voor <= eerst <= now()
    assert await opgeslagen(settings) == check

    # Binnen de week niet opnieuw, ook niet na een herstart (de tijd staat in de database)
    assert await weekcheck.run(settings) is None
    await zet_tijd(settings, check, now() - weekcheck.EVERY + timedelta(minutes=5))
    assert await weekcheck.run(settings) is None
    # Na een week wel
    await zet_tijd(settings, check, now() - weekcheck.EVERY - timedelta(minutes=1))
    nieuw = await weekcheck.run(settings)
    assert nieuw is not None and nieuw.bad == 0
    assert datetime.fromisoformat(nieuw.at) > eerst
    assert (await client.get(ADMIN, headers=ada)).json()["check"] == nieuw.model_dump()


async def test_een_kapotte_uitkomst_is_geen_uitkomst(client, settings):
    for kapot in ({"at": 1, "sampled": 2, "bad": 0}, {"at": "x", "sampled": -1, "bad": 0},
                  {"at": "x", "sampled": 2, "bad": True}, ["x"], None):  # fmt: skip
        assert weekcheck._stored(kapot) is None
    # Een tijd die niet te lezen is: dan is het tijd
    assert weekcheck._due(weekcheck._stored({"at": "gisteren", "sampled": 2, "bad": 0}), now())


async def test_een_verschil_komt_op_beheer_met_alleen_aantallen(client, settings, caplog):
    w = await W.bouw_async(settings)
    async with get_engine(settings).begin() as conn:
        # Een naam langs de index heen (zoals in test_search_rebuild.py)
        await conn.exec_driver_sql("DROP TRIGGER node_au_text")
        await conn.execute(
            update(Node)
            .where(Node.id == w.ids["brief"])
            .values(name="Ander.txt", search_name="ander.txt")
        )
    assert await tel(settings) <= weekcheck.SAMPLE  # dus brief zit erbij
    with caplog.at_level(logging.INFO, logger="platform.search"):
        found = await weekcheck.run(settings)
    assert found is not None
    assert found.bad >= 2  # fts_node en fts_name van brief
    ada = await kop(client, "ada")
    assert (await client.get(ADMIN, headers=ada)).json()["check"] == found.model_dump()
    regels = [r for r in caplog.records if "wekelijkse controle" in r.getMessage()]
    assert [r.levelno for r in regels] == [logging.WARNING]
    tekst = regels[0].getMessage().lower()
    assert "brief" not in tekst and "ander" not in tekst and str(found.bad) in tekst


async def test_niet_tijdens_de_pauze(client, settings, monkeypatch):
    await W.bouw_async(settings)
    gelopen = bespied(monkeypatch)
    ada = await kop(client, "ada")
    r = await client.patch(ADMIN, json={"paused": True}, headers=ada)
    assert r.status_code in (200, 204), r.text
    assert weekcheck.NAME in await jobs.run_housekeeping(settings)
    assert gelopen == [] and await opgeslagen(settings) is None
    r = await client.patch(ADMIN, json={"paused": False}, headers=ada)
    assert r.status_code in (200, 204), r.text
    assert await weekcheck.run(settings) is not None
    assert len(gelopen) == 1


async def test_niet_tijdens_een_herbouw_zonder_fts5_of_zonder_nodes(client, settings, monkeypatch):
    gelopen = bespied(monkeypatch)
    # Zonder nodes: gekeken, niets vergeleken, dus geen uitkomst (anders telde die week niet)
    assert await tel(settings) == 0
    assert await weekcheck.run(settings) is None
    assert len(gelopen) == 1 and await opgeslagen(settings) is None
    await W.bouw_async(settings)
    # Tijdens een herbouw zoekt de app met LIKE: niet controleren
    await sql(settings, "UPDATE search_state SET cursor = 0 WHERE id = 1")
    assert await weekcheck.run(settings) is None
    await sql(settings, "UPDATE search_state SET cursor = NULL WHERE id = 1")
    # Zonder FTS5 (PLATFORM_SEARCH_INDEX=uit) en op PostgreSQL: de motor is like
    assert await weekcheck.run(settings.model_copy(update={"search_index": "uit"})) is None
    pg = settings.model_copy(update={"database_url": "postgresql+asyncpg://niemand@127.0.0.1:1/x"})
    assert await weekcheck.run(pg) is None
    assert len(gelopen) == 1 and await opgeslagen(settings) is None

    # Begint er een herbouw tussen de keuze en de controle, dan ook geen uitkomst
    def herbouw(url: str) -> rebuild.Checked:
        gelopen.append(url)
        return rebuild.Checked(rebuilding=True)

    monkeypatch.setattr(weekcheck, "_check", herbouw)
    assert await weekcheck.run(settings) is None
    assert len(gelopen) == 2 and await opgeslagen(settings) is None


async def test_de_controle_leest_alleen_in_een_eigen_draad(client, settings, monkeypatch):
    await W.bouw_async(settings)
    gezien: dict = {}
    echt = rebuild.check

    async def check(engine, **opties):
        gezien["draad"] = threading.get_ident()
        gezien["opties"] = opties
        async with engine.connect() as conn:
            with pytest.raises(OperationalError, match="readonly"):
                await conn.exec_driver_sql("UPDATE search_state SET cursor = cursor WHERE id = 1")
        return await echt(engine, **opties)

    monkeypatch.setattr(rebuild, "check", check)
    found = await weekcheck.run(settings)
    assert found is not None and found.bad == 0
    assert gezien["opties"] == {"sample": weekcheck.SAMPLE}
    # Niet in de event loop van de app: de FTS5 in het geheugen houdt die niet bezig
    assert gezien["draad"] != threading.get_ident()
    assert (await sql(settings, "SELECT cursor FROM search_state WHERE id = 1")) == [(None,)]
    assert await opgeslagen(settings) == found.model_dump()
