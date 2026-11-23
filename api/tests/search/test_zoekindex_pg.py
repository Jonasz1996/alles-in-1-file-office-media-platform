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

import asyncio
import logging
import uuid

import pytest
import sqlalchemy as sa
from alembic import command

from app import analysis
from app.db import get_engine
from app.models import Node
from app.search import engine as engines
from app.search import rebuild, weekcheck
from app.search.query import parse
from app.text import fold
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
    # check() schrijft niet (F13): de fout staat er nog
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


# Review van PR #6 (F2): de letters van deze blokken (Latijn, Grieks, Cyrillisch), elk midden in
# een woord. SQLite vindt ze (unicode61 met remove_diacritics 2 en de naam-vorm van fold()).
BLOKKEN = ((0x41, 0x24F), (0x370, 0x3FF), (0x400, 0x52F), (0x1E00, 0x1EFF), (0x1F00, 0x1FFF))


def _woorden() -> list[str]:
    uit = []
    for lo, hi in BLOKKEN:
        for cp in range(lo, hi + 1):
            tekst = "ka" + chr(cp) + "pa"
            p = parse(tekst)
            if chr(cp).isalpha() and p is not None and len(p.terms) == 1:
                uit.append(tekst)
    return uit


async def test_tekens_buiten_het_latijn_vindt_de_index(settings):
    """cf_fold vouwt zoals fold(): NFKD, zonder tekens op een letter, casefold. Elke letter
    vindt zichzelf, en zonder accent of met hoofdletters vindt hij de tekst ook (zoals FTS5)."""
    woorden = _woorden()
    assert len(woorden) > 1000
    vragen = [parse(w).tsquery() for w in woorden]
    async with get_engine(settings).connect() as conn:
        mis = await conn.execute(
            sa.text(
                "SELECT t FROM unnest(CAST(:t AS text[]), CAST(:q AS text[])) AS x(t, q) "
                "WHERE NOT cf_tsv(t, 'C') @@ to_tsquery('simple', q)"
            ),
            {"t": woorden, "q": vragen},
        )
        assert mis.scalars().all() == []
        for tekst, vraag in (
            ("λόγος", "λογος"),
            ("Αθήνα", "αθηνα"),
            ("ёлка", "елка"),
            ("Йошкар-Ола", "йошкар"),
            ("ΛΌΓΟΣ", "λόγος"),
            ("Ǣsir", "æsir"),
            ("Straße", "STRASSE"),
        ):
            gevonden = await conn.execute(
                sa.text("SELECT cf_tsv(:t, 'C') @@ to_tsquery('simple', :q)"),
                {"t": tekst, "q": parse(vraag).tsquery()},
            )
            assert gevonden.scalar() is True, (tekst, vraag)


# --- Te grote teksten (review van PR #6, F3, F10 en F14) --------------------------------------

CYRILLISCH = "абвгдежзиклмнопрстуфхцчшщъыьэюяі"  # 32 letters zonder teken, 2 bytes elk


def _cyrillisch(n: int) -> list[str]:
    """n verschillende woorden van 7 letters (2 bytes per letter: veel bytes per teken)."""
    uit = []
    for i in range(n):
        cijfers = []
        for _ in range(6):
            i, r = divmod(i, 32)
            cijfers.append(CYRILLISCH[r])
        uit.append("ж" + "".join(cijfers))
    return uit


def _latijn(n: int) -> list[str]:
    """n verschillende woorden van 8 letters."""
    uit = []
    for i in range(n):
        letters = []
        for _ in range(8):
            i, r = divmod(i, 26)
            letters.append(chr(ord("a") + r))
        uit.append("".join(letters))
    return uit


async def _past(conn, sql: str, **params) -> bool:
    """Geeft sql een tsvector die past (True), of de fout van de grens van 1 MB (False)?"""
    try:
        async with conn.begin_nested():
            await conn.execute(sa.text(sql), params)
        return True
    except sa.exc.DBAPIError as exc:
        assert "too long for tsvector" in str(exc.orig), exc
        return False


async def _grootste(conn, woorden: list[str], sql: str) -> int:
    """Het grootste k waarvoor sql met :t = de eerste k woorden nog past (0 past altijd)."""
    lo, hi = 0, len(woorden)
    assert not await _past(conn, sql, t=" ".join(woorden))
    while hi - lo > 1:
        m = (lo + hi) // 2
        if await _past(conn, sql, t=" ".join(woorden[:m])):
            lo = m
        else:
            hi = m
    return lo


async def _brief(settings, w) -> tuple[int, str]:
    async with get_engine(settings).connect() as conn:
        found = await conn.execute(
            sa.text("SELECT num, blob_id FROM node WHERE id = :i"), {"i": w.ids["brief"]}
        )
        num, blob = found.one()
    return num, blob


async def _naam(settings, w, naam: str) -> None:
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text("UPDATE node SET name = :n, search_name = :s WHERE id = :i"),
            {"n": naam, "s": fold(naam), "i": w.ids["brief"]},
        )


async def _tekst(settings, blob: str, body: str) -> None:
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text("UPDATE blob_text SET body = :b, ocr = '', meta = '' WHERE blob_sha = :s"),
            {"b": body, "s": blob},
        )


async def test_de_naam_en_een_tekst_net_onder_de_grens_samen(client, settings):
    """De tekst alleen past net in een tsvector, met de naam erbij niet: het schrijven van de
    tekst lukt toch (de rij is ingekort, capped), en een andere naam ook (F3). Een lange naam:
    zo is hij zeker groter dan wat er onder de grens over is (minder dan één woord)."""
    w = await W.bouw_async(settings)
    num, blob = await _brief(settings, w)
    await _naam(settings, w, "Lijst met alle woorden van het boek deel een.txt")
    woorden = _cyrillisch(60_000)
    async with get_engine(settings).connect() as conn:
        k = await _grootste(conn, woorden, "SELECT to_tsvector('simple', cf_fold(:t))")
    body = " ".join(woorden[:k])
    assert len(body) < 1_048_576  # onder search_max_text
    await _tekst(settings, blob, body)
    assert await een(settings, "SELECT capped FROM fts_node WHERE rowid = :n", n=num) is True
    eva = await kop(client, "eva")
    assert "Lijst met alle woorden van het boek deel een.txt" in await namen(
        client, eva, woorden[0]
    )
    await _naam(settings, w, "Lijst met alle woorden van het boek deel twee.txt")
    assert await namen(client, eva, "twee") == {"Lijst met alle woorden van het boek deel twee.txt"}
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(check_fts)
    r = await client.get("/api/v1/admin/search", headers=await kop(client, "ada"))
    assert r.status_code == 200, r.text
    assert r.json()["index"]["capped"] == 1


async def test_een_te_grote_tekst_houdt_zoveel_woorden_als_past(client, settings):
    """Van een te grote tekst blijft de langste reeks woorden vooraan die past: het woord erna
    past niet meer (F10; eerst halveerde cf_tsv, en viel tot de helft weg)."""
    w = await W.bouw_async(settings)
    num, blob = await _brief(settings, w)
    woorden = _latijn(116_508)
    body = " ".join(woorden)[:1_048_571]
    woorden = body.split(" ")
    await _tekst(settings, blob, body)
    async with get_engine(settings).connect() as conn:
        found = await conn.execute(
            sa.text(
                "SELECT capped, (SELECT count(*) FROM unnest(tsv) AS u WHERE u.weights = '{C}') "
                "FROM fts_node WHERE rowid = :n"
            ),
            {"n": num},
        )
        capped, k = found.one()
        assert capped is True
        assert 80_000 < k < len(woorden)  # halveren hield er 58.254
        naam = "cf_tsv('brief.txt', 'A')"
        assert await _past(conn, f"SELECT {naam} || cf_tsv(:t, 'C')", t=" ".join(woorden[:k]))
        assert not await _past(
            conn, f"SELECT {naam} || cf_tsv(:t, 'C')", t=" ".join(woorden[: k + 1])
        )
        for i, verwacht in ((0, True), (60_000, True), (k - 1, True), (k, False)):
            gevonden = await conn.execute(
                sa.text("SELECT tsv @@ to_tsquery('simple', :w) FROM fts_node WHERE rowid = :n"),
                {"w": woorden[i], "n": num},
            )
            assert gevonden.scalar() is verwacht, i


async def test_de_functies_van_de_index_in_een_parallel_plan(settings):
    """Een functie met een EXCEPTION-blok start een subtransactie; dat mag nergens in een
    parallel plan, ook niet in de leider (dus ook niet PARALLEL RESTRICTED). Zo'n functie is
    PARALLEL UNSAFE, en cf_tsv heeft geen blok meer (F14)."""
    await W.bouw_async(settings)
    async with get_engine(settings).connect() as conn:
        for zet in (
            "max_parallel_workers_per_gather = 2",
            "parallel_setup_cost = 0",
            "parallel_tuple_cost = 0",
            "min_parallel_table_scan_size = 0",
        ):
            await conn.exec_driver_sql(f"SET LOCAL {zet}")
        plan = await conn.exec_driver_sql(
            "EXPLAIN SELECT count(*) FROM node WHERE search_name LIKE '%x%'"
        )
        assert "Gather" in "\n".join(r[0] for r in plan.all())  # er komt een parallel plan
        for sql in (
            "SELECT count(*) FROM node WHERE cf_tsv(search_name, 'C') @@ to_tsquery('simple', 'x')",
            "SELECT count(*) FROM node p JOIN node q ON p.num = q.num "
            "WHERE cf_tsv(p.search_name, 'C') @@ to_tsquery('simple', 'x')",
            "SELECT count(*) FROM node "
            "WHERE (fts_doc(search_name, '', '', '', NULL)).tsv @@ to_tsquery('simple', 'x')",
        ):
            assert (await conn.exec_driver_sql(sql)).scalar() is not None
        found = await conn.execute(
            sa.text(
                "SELECT p.proname, p.proparallel::text FROM pg_proc p "
                "WHERE p.proname IN ('cf_fold', 'cf_tsv', 'cf_add', 'fts_doc', 'fts_rows', "
                "'fts_refresh') AND p.prosrc ILIKE '%exception%'"
            )
        )
        blokken = dict(found.all())
        assert set(blokken) == {"cf_add", "fts_doc"}
        assert set(blokken.values()) == {"u"}


async def test_een_herbouw_wacht_op_een_schrijver_die_al_bezig_is(settings):
    """Een schrijver begint terwijl de herbouw nog niets deed (cursor 0): zijn trigger slaat
    de rij over, want die komt in een stuk. Het stuk mag dan niet klaar zijn met de oude naam
    voor de schrijver commit: dan bleef fts_node oud (review van PR #6, F4). fts_refresh leest
    de cursor met FOR SHARE; het stuk wacht op de commit van de schrijver en ziet de nieuwe
    naam."""
    w = await W.bouw_async(settings)
    engine = get_engine(settings)
    async with engine.begin() as conn:
        await conn.execute(sa.text("UPDATE search_state SET cursor = 0 WHERE id = 1"))

    async def stuk() -> tuple[int, bool]:
        async with engine.begin() as conn:
            return await rebuild._chunk(conn, 100_000)

    async with engine.connect() as schrijver:
        await schrijver.execute(
            sa.text("UPDATE node SET search_name = 'okapiplan.txt' WHERE id = :i"),
            {"i": w.ids["brief"]},
        )
        taak = asyncio.create_task(stuk())
        await asyncio.sleep(1.0)
        wachtte = not taak.done()
        await schrijver.commit()
        _, klaar = await taak
    assert klaar
    assert await een(settings, "SELECT cursor FROM search_state WHERE id = 1") is None
    async with engine.connect() as conn:
        await conn.run_sync(check_fts)
    assert wachtte


async def test_check_schrijft_niet(settings):
    """check() (search-check en de weekcontrole) vergelijkt fts_node met fts_rows, zonder te
    schrijven: geen slot op rijen van fts_node, geen WAL naar de replica's. Hij werkt dus ook in
    een transactie die alleen leest (review van PR #6, F13)."""
    w = await W.bouw_async(settings)
    num = await een(settings, "SELECT num FROM node WHERE id = :i", i=w.ids["brief"])
    engine = get_engine(settings)
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("UPDATE fts_node SET tsv = ''::tsvector WHERE rowid = :n"), {"n": num}
        )
        await conn.execute(
            sa.text(
                "UPDATE fts_node SET capped = true WHERE rowid = (SELECT min(rowid) FROM fts_node)"
            )
        )
    eerste = await een(settings, "SELECT min(rowid) FROM fts_node")

    def alleen_lezen(conn) -> None:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")

    await engine.dispose()
    sa.event.listen(engine.sync_engine, "begin", alleen_lezen)
    try:
        found = await rebuild.check(engine, full=True)
    finally:
        sa.event.remove(engine.sync_engine, "begin", alleen_lezen)
        await engine.dispose()
    assert sorted(found.bad) == sorted([(num, "fts_node"), (eerste, "fts_node")])
    assert found.sampled == await een(settings, "SELECT count(*) FROM node")


async def test_een_database_met_lc_ctype_c_geeft_een_waarschuwing(tmp_path, caplog):
    """cf_fold hangt af van LC_CTYPE van de database: met C kent lower() en [[:alnum:]] alleen
    ASCII, en valt elke andere letter uit de index. De app zegt dat bij de start, in het log
    (review van PR #6, F12). ClusterForge maakt de database met C.UTF-8: daar niets."""
    import asyncpg

    from app.config import Settings
    from app.migrate import upgrade
    from tests.conftest import PG_URL, _pg_url

    naam = "ctype_c_" + uuid.uuid4().hex[:12]
    beheer = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await beheer.execute(
            f"CREATE DATABASE \"{naam}\" TEMPLATE template0 ENCODING 'UTF8' "
            "LC_COLLATE 'C' LC_CTYPE 'C'"
        )
        settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8,
                            database_url=_pg_url(naam))  # fmt: skip
        caplog.set_level(logging.WARNING, logger="platform.search")
        try:
            await upgrade(settings)
        finally:
            await get_engine(settings).dispose()
            engines.forget(settings.db_url)
        meldingen = [r.getMessage() for r in caplog.records if r.name == "platform.search"]
        assert any("LC_CTYPE" in m and "C.UTF-8" in m for m in meldingen), meldingen
    finally:
        await beheer.execute(f'DROP DATABASE IF EXISTS "{naam}" WITH (FORCE)')
        await beheer.close()


async def test_een_database_met_c_utf8_geeft_geen_waarschuwing(settings, caplog):
    caplog.set_level(logging.WARNING, logger="platform.search")
    engines.forget(settings.db_url)
    async with get_engine(settings).connect() as conn:
        assert (await engines.read_conn_info(conn)).fts is True
    assert not [r for r in caplog.records if r.name == "platform.search"]
