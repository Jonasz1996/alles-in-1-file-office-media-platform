"""GET /search?mode=image (bouwontwerp fase 6, X3; Keuze 78, 79; X3b), met de nep van beeld.

Alleen eigen, levende foto's op de volgorde van rank; de filters; like stuurt de num (nooit een
vector, K1-08); pagina's uit de cache van 60 s; 400 search_bad_filter; 409 beeld_no_vector; een
nieuwe vraag stopt de oude; 12 tegelijk geeft 429 search_busy; de plan-test van stap 5.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from app.db import get_engine
from app.models import Node
from app.security import login_limiter
from tests.beeld import nep
from tests.beeld.nep import foto
from tests.search.plans import assert_plan
from tests.test_files import mkdir, root
from tests.test_shares import person

pytestmark = pytest.mark.anyio


async def _wereld(client, settings, eva):
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    oranje = await foto(client, eva, settings, "oranje.jpg", "oranje", seed=1)
    blauw = await foto(client, eva, settings, "blauw.jpg", "blauw", seed=2)
    paars = await foto(client, eva, settings, "paars.jpg", "paars", seed=3)
    await nep.embed_all(settings)
    return rood, oranje, blauw, paars


async def test_de_volgorde_van_rank_en_alleen_eigen_levende_fotos(client, mensen, beeld):
    (eva, _), (bram, _) = mensen
    fake, settings = beeld
    rood, oranje, blauw, paars = await _wereld(client, settings, eva)
    assert await nep.ids(client, eva, "rood") == [r["id"] for r in (rood, oranje, paars, blauw)]
    assert await nep.ids(client, eva, "blauw") == [r["id"] for r in (blauw, paars, rood, oranje)]
    body = (await nep.zoek(client, eva, "rood")).json()
    assert {h["match"] for h in body["results"]} == {"image"}
    assert {h["snippet"] for h in body["results"]} == {None}
    assert (body["mode"], body["ranked"], body["fallback"]) == ("image", True, None)
    # een document of een map met dezelfde naam doet niet mee
    eva_root = (await root(client, eva))["folder"]["id"]
    await mkdir(client, eva, eva_root, "rood")
    await nep.upload(client, eva, "rood.txt", b"rood rood rood")
    assert await nep.ids(client, eva, "rood") == [r["id"] for r in (rood, oranje, paars, blauw)]
    # Bram heeft beeld aan maar geen foto's
    await nep.beeld_aan(client, bram)
    await nep.run_jobs(settings)
    await nep.bestanden(settings)
    r = await nep.zoek(client, bram, "rood")
    assert r.status_code == 200, r.text
    assert r.json()["results"] == []


async def test_pagina_s_uit_de_cache(client, mensen, beeld):
    """offset en limit lezen de cache: één rank voor drie pagina's; een andere vraag is een
    nieuwe rank, en een nieuw bestand (een hogere gen) ook. Opwarmen (k = 0) telt niet mee."""
    (eva, _), _ = mensen
    fake, settings = beeld
    rood, oranje, blauw, paars = await _wereld(client, settings, eva)

    def vragen() -> int:
        return sum(1 for args in fake.ranks if args.k > 0)

    voor = vragen()
    pagina = []
    for offset in (0, 2, 4):
        pagina += await nep.ids(client, eva, "rood", offset=offset, limit=2)
    assert pagina == [r["id"] for r in (rood, oranje, paars, blauw)]
    r = await nep.zoek(client, eva, "rood", offset=0, limit=2)
    assert r.json()["more"] is True
    assert vragen() == voor + 1
    await nep.ids(client, eva, "blauw")
    assert vragen() == voor + 2
    await foto(client, eva, settings, "groen.jpg", "groen", seed=4)
    await nep.embed_all(settings)
    await nep.ids(client, eva, "rood")
    assert vragen() == voor + 3


async def test_de_filters(client, mensen, beeld):
    """in (een map), type image, van en tot: dezelfde volgorde, alleen wat het filter houdt."""
    (eva, _), _ = mensen
    fake, settings = beeld
    rood, oranje, blauw, paars = await _wereld(client, settings, eva)
    eva_root = (await root(client, eva))["folder"]["id"]
    album = await mkdir(client, eva, eva_root, "Album")
    for node in (oranje, blauw):
        r = await client.patch(
            f"/api/v1/files/{node['id']}", json={"parent_id": album}, headers=eva
        )
        assert r.status_code == 200, r.text
    assert await nep.ids(client, eva, "rood", **{"in": album}) == [oranje["id"], blauw["id"]]
    assert await nep.ids(client, eva, "rood", type="image") == [
        r["id"] for r in (rood, oranje, paars, blauw)
    ]
    assert await nep.ids(client, eva, "rood", to="2000-01-01") == []


async def test_like_stuurt_de_num_en_nooit_een_vector(client, mensen, beeld):
    """Meer zoals deze: rank krijgt de num van de eigen foto (geen tekst, geen vector); de foto
    zelf staat bovenaan. Zonder vector: 409 beeld_no_vector; een foto van een ander: 404."""
    (eva, _), (bram, _) = mensen
    fake, settings = beeld
    rood, oranje, blauw, paars = await _wereld(client, settings, eva)
    assert await nep.ids(client, eva, like=rood["id"]) == [
        r["id"] for r in (rood, oranje, paars, blauw)
    ]
    args = fake.ranks[-1]
    async with nep.maker(settings)() as db:
        num = await db.scalar(sa.select(Node.num).where(Node.id == uuid.UUID(rood["id"])))
    assert (args.num, args.text) == (num, None)
    assert set(args.model_dump()) == {"owner", "gen", "text", "num", "k"}
    zonder = await nep.upload(client, eva, "nog niet.jpg", nep.photo((9, 9, 9), seed=5))
    r = await nep.zoek(client, eva, like=zonder["id"])
    assert (r.status_code, nep.code(r)) == (409, "beeld_no_vector")
    await nep.beeld_aan(client, bram)
    await nep.run_jobs(settings)
    r = await nep.zoek(client, bram, like=rood["id"])
    assert (r.status_code, nep.code(r)) == (404, "not_found")
    r = await nep.zoek(client, eva, like=str(uuid.uuid4()))
    assert (r.status_code, nep.code(r)) == (404, "not_found")


async def test_400_bij_wat_niet_kan(client, mensen, beeld):
    """after, een ander type, of zonder q en zonder like: 400 search_bad_filter; q en like
    samen ook. Niets daarvan bereikt de container."""
    (eva, _), _ = mensen
    fake, settings = beeld
    rood, *_ = await _wereld(client, settings, eva)
    voor = len(fake.ranks)
    for params in (
        {"q": "rood", "after": 5},
        {"q": "rood", "type": "document"},
        {},
        {"q": "rood", "like": rood["id"]},
    ):
        r = await client.get("/api/v1/search", params={"mode": "image", **params}, headers=eva)
        assert (r.status_code, nep.code(r)) == (400, "search_bad_filter"), (params, r.text)
    assert len(fake.ranks) == voor


async def test_een_nieuwe_vraag_stopt_de_oude(client, mensen, beeld):
    """Dezelfde gebruiker vraagt opnieuw terwijl rank nog loopt: de eerste krijgt een lege,
    gedeeltelijke pagina (partial), de tweede het antwoord."""
    (eva, _), _ = mensen
    fake, settings = beeld
    rood, *_ = await _wereld(client, settings, eva)
    fake.rank_delay = 0.4
    eerste = asyncio.create_task(nep.zoek(client, eva, "blauw"))
    await asyncio.sleep(0.15)
    tweede = await nep.zoek(client, eva, "rood")
    r = await eerste
    fake.rank_delay = 0.0
    assert r.status_code == 200, r.text
    assert (r.json()["results"], r.json()["partial"]) == ([], True)
    assert tweede.json()["results"][0]["node"]["id"] == rood["id"]


async def test_12_tegelijk_geeft_429(client, admin, beeld):
    """1 bij de container en 10 in de rij; de twaalfde krijgt meteen 429 search_busy met
    Retry-After."""
    jonas, _ = admin
    fake, settings = beeld
    mensen = []
    for i in range(12):
        login_limiter.reset()  # twaalf keer inloggen vanaf hetzelfde adres
        mensen.append(await person(client, jonas, f"p{i}@example.nl"))
    for headers, _ in mensen:
        await nep.beeld_aan(client, headers)
        await foto(client, headers, settings, "rood.jpg", "rood")
    await nep.run_jobs(settings)
    await nep.embed_all(settings)
    fake.rank_delay = 0.3
    antwoorden = await asyncio.gather(*(nep.zoek(client, h, "rood") for h, _ in mensen))
    fake.rank_delay = 0.0
    codes = sorted(r.status_code for r in antwoorden)
    assert codes == [200] * 11 + [429], [r.text for r in antwoorden]
    druk = next(r for r in antwoorden if r.status_code == 429)
    assert nep.code(druk) == "search_busy" and druk.headers["retry-after"] == "1"


@pytest.mark.sqlite_only("het plan van SQLite (json_each, ix_node_num); PostgreSQL gebruikt unnest")
async def test_het_plan_van_stap_5(client, mensen, beeld):
    """De SQL van stap 5 begint bij json_each en zoekt de node op met ix_node_num (Keuze 14);
    geen AUTOMATIC index."""
    (eva, _), _ = mensen
    fake, settings = beeld
    await _wereld(client, settings, eva)
    engine = get_engine(settings)
    gezien: list[tuple[str, object]] = []

    def kijk(conn, cursor, statement, parameters, context, executemany):
        if "json_each" in statement:
            gezien.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", kijk)
    try:
        await nep.ids(client, eva, "paars")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", kijk)
    assert gezien
    sql, args = gezien[-1]
    async with engine.connect() as conn:
        for analyze in (False, True):
            if analyze:
                await conn.exec_driver_sql("ANALYZE")
            plan = await conn.run_sync(
                lambda c: assert_plan(c, sql, args, require=("ix_node_num",))
            )
            assert "json_each" in plan.eerste or plan.eerste.startswith("SCAN r"), plan.tekst()


async def test_de_voortgang_telt_alleen_als_er_iets_open_staat(client, mensen, beeld):
    """indexing bij zoeken op beeld (admin.progress): staat er niets meer open, dan None zonder
    te tellen, met één zoekstap in ix_analysis_todo (de GROUP BY over alle foto's van de eigenaar
    kostte bij elke vraag ±9 ms bij 100.000 foto's, test_beeld_perf). Staat er wel iets open, dan
    het deel dat klaar is, zoals eerst."""
    (eva, _), _ = mensen
    fake, settings = beeld
    rood, *_ = await _wereld(client, settings, eva)
    engine = get_engine(settings)
    gezien: list[tuple[str, object]] = []

    def kijk(conn, cursor, statement, parameters, context, executemany):
        if "analysis.step" in statement and "analysis.owner_id" in statement:
            gezien.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", kijk)
    try:
        body = (await nep.zoek(client, eva, "rood")).json()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", kijk)
    assert (body["mode"], body["indexing"]) == ("image", None), body
    assert len(gezien) == 1, [sql for sql, _ in gezien]
    sql, args = gezien[0]
    assert "GROUP BY" not in sql and "LIMIT" in sql, sql
    if not settings.database_url:  # het plan van SQLite; PostgreSQL kiest zelf
        async with engine.connect() as conn:
            await conn.run_sync(
                lambda c: assert_plan(c, sql, args, require=("ix_analysis_todo",))
            )
    # één van de vier weer open: dan telt hij wel, en 3 van de 4 zijn klaar
    sha = await nep.sha_of(settings, rood["id"])
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("UPDATE analysis SET state = 'wait' WHERE blob_sha = :sha AND step = 'embed'"),
            {"sha": sha},
        )
    body = (await nep.zoek(client, eva, "blauw")).json()
    assert (body["mode"], body["indexing"]) == ("image", {"done": 0.75}), body
