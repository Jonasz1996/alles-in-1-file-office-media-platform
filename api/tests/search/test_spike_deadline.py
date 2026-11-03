"""Spike S1 (bouwontwerp fase 6, §3.7; Keuze 9): de tijdsgrens van een zoekvraag via
interrupt() uit een timer in de event loop, door aiosqlite en de pool van SQLAlchemy heen, met
de rondes A, B en C.

grens() hieronder is de vorm die de spike bewijst. Z1 bouwt app.search.deadline.deadline() met
dezelfde handtekening en zet die hier in de plaats; deze tests blijven.

Wat de spike vond (docs/fase-6/spike.md, S1):
- interrupt() moet in de callback van de timer zelf gebeuren, op de sqlite3-verbinding die
  aiosqlite in zijn thread gebruikt. aiosqlite.Connection.interrupt() is een coroutine: als
  taak gestart, kan hij pas lopen als de vraag al klaar is en de volgende al draait. Zo raakt
  een late interrupt toch de volgende vraag;
- de adapter van SQLAlchemy voor aiosqlite leest elke uitkomst meteen helemaal (fetchall in de
  thread). Na een await loopt er op die verbinding dus geen statement meer, en een interrupt()
  dan doet niets (SQLite negeert hem zonder lopend statement);
- na "interrupted" is de verbinding gewoon bruikbaar, ook in dezelfde transactie, en de pool
  geeft hem heel terug;
- ronde A stopt bij een voorvoegsel 2–22 ms na de grens (FTS5 voegt de lijsten samen tussen
  twee kijkjes naar de interrupt). Daarom krijgt ronde B de rest van 120 + 40 ms, niet vast
  40 ms: zo bleef "ver"* in 30 keer onder 172 ms (met vast 40 ms: tot 191 ms).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import statistics
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy import exc, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from tests.search import spike

pytestmark = pytest.mark.anyio

# Een vraag die zonder grens ruim 2 s duurt (op deze sandbox ±20 s): tellen in een recursieve CTE
TRAAG = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 100000000) "
    "SELECT count(*) FROM c"
)
# Een vraag van ±0,1–0,5 s, en een van een paar ms
KORT = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 20000) "
    "SELECT count(*) FROM c"
)
HALF = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 1500000) "
    "SELECT count(*) FROM c"
)


@dataclass
class Grens:
    ms: int
    hit: bool = False  # de vraag is gestopt


def _sqlite3(raw):
    """De sqlite3-verbinding achter een verbinding uit de pool (aiosqlite houdt hem in
    _connection; interrupt() mag vanuit elke thread)."""
    return raw.driver_connection._conn


@asynccontextmanager
async def grens(conn: AsyncConnection, ms: int) -> AsyncIterator[Grens]:
    """De vorm van app.search.deadline.deadline (§3.3): de timer roept interrupt() zelf aan, en
    is altijd uit vóór de verbinding verder gaat. Een vraag die stopt, geeft OperationalError
    "interrupted"; die vangt grens() op en zet .hit."""
    raw = await conn.get_raw_connection()
    db = _sqlite3(raw)
    d = Grens(ms)
    handle = asyncio.get_running_loop().call_later(ms / 1000, db.interrupt)
    try:
        yield d
    except exc.OperationalError as e:
        if "interrupted" not in str(e.orig):
            raise
        d.hit = True
    finally:
        handle.cancel()


# --- de database ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """Een database op head met Eva (zichtbaar) en Bram (niet zichtbaar voor Eva): 2.000
    documenten van ±400 woorden bij Eva, een paar bij Bram, en namen met "vergadering" en
    "verslag" bij allebei. Gevuld zoals de migratie het doet (triggers voor num en pad, dan een
    herbouw van FTS)."""
    path = tmp_path_factory.mktemp("s1") / "s1.db"
    url = spike.head_db(path)
    conn = sqlite3.connect(path)

    def doe(c) -> None:
        eva, eva_root = spike.gebruiker(c, "eva")
        bram, bram_root = spike.gebruiker(c, "bram")
        for i, body in enumerate(spike.teksten(2000, 400)):
            owner, root = (bram, bram_root) if i % 50 == 0 else (eva, eva_root)
            sha = hashlib.sha256(f"s1-{i}".encode()).hexdigest()
            c.execute(
                "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES (?, 1, 1, ?)",
                (sha, spike.NU.isoformat()),
            )
            spike.node(c, uuid.uuid4().hex, root, owner, f"stuk {i}.txt", mime="text/plain",
                       blob=sha)  # fmt: skip
            c.execute(
                "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
                "VALUES (?, ?, '', '', 0, ?)",
                (sha, body, spike.NU.isoformat()),
            )
        spike.node(c, uuid.uuid4().hex, eva_root, eva, "Vergadering bestuur.pdf")
        spike.node(c, uuid.uuid4().hex, bram_root, bram, "Verslag van Bram.pdf")
        spike.node(c, uuid.uuid4().hex, bram_root, bram, "Geheim zebrakwarts.pdf")

    spike.vul_snel(conn, doe)
    eva_path = conn.execute(
        "SELECT n.mpath FROM node n JOIN user u ON u.root_id = n.id WHERE u.display_name = 'eva'"
    ).fetchone()[0]
    conn.close()
    return url, eva_path


@pytest.fixture
async def engine(corpus):
    url, _ = corpus
    eng = create_async_engine(url)
    yield eng
    await eng.dispose()


# --- de rondes van Keuze 9 --------------------------------------------------------------------

BEREIK = "(n.mpath >= :p1 AND n.mpath < :e1)"
RONDE_A = text(
    "SELECT n.id, bm25(fts_node, 10.0, 6.0, 1.0, 0.8, 0.5) AS s "
    "FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid "
    f"WHERE fts_node MATCH :expr AND n.kind <> 'album' AND n.dead = 0 AND {BEREIK} "
    "ORDER BY s, n.num LIMIT :n"
)
RONDE_B = text(
    "SELECT n.id FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid "
    f"WHERE fts_node MATCH :expr AND n.kind <> 'album' AND n.dead = 0 AND {BEREIK} "
    "LIMIT :n"
)
RONDE_C = text(
    "SELECT n.id FROM fts_name CROSS JOIN node AS n ON n.num = fts_name.rowid "
    f"WHERE fts_name MATCH :names AND n.kind <> 'album' AND n.dead = 0 AND {BEREIK} "
    "LIMIT :n"
)


@dataclass
class Uitkomst:
    rows: list
    ronde: str
    partial: bool
    ranked: bool
    names_only: bool
    ms: float


async def zoek(conn, root: str, expr: str, names: str | None, *, ms_a=120, ms_b=40, n=30):
    """De volgorde van Keuze 9: A gerangschikt; stopt die, dan B zonder volgorde; stopt ook die,
    dan C alleen op namen (trigram).

    B krijgt wat er over is van ms_a + ms_b, niet vast ms_b: A stopt pas 2–22 ms na zijn grens
    (op 3.46.1; FTS5 kijkt bij een voorvoegsel niet bij elke stap naar de interrupt), en met een
    vaste 40 ms kwam het antwoord zo tot 191 ms in plaats van 165 (S1). Minstens 1 ms."""
    args = {"expr": expr, "p1": root, "e1": root[:-1] + "0", "n": n + 1}
    start = time.perf_counter()
    rows: list = []
    async with grens(conn, ms_a) as a:
        rows = (await conn.execute(RONDE_A, args)).all()
    if not a.hit:
        return Uitkomst(rows, "A", False, True, False, (time.perf_counter() - start) * 1000)
    rest = ms_a + ms_b - (time.perf_counter() - start) * 1000
    async with grens(conn, max(1, round(rest))) as b:
        rows = (await conn.execute(RONDE_B, args)).all()
    if not b.hit:
        return Uitkomst(rows, "B", True, False, False, (time.perf_counter() - start) * 1000)
    rows = []
    if names:
        rows = (await conn.execute(RONDE_C, {**args, "names": names})).all()
    return Uitkomst(rows, "C", True, False, True, (time.perf_counter() - start) * 1000)


async def duur(conn, sql, args) -> float:
    start = time.perf_counter()
    await conn.execute(sql, args)
    return (time.perf_counter() - start) * 1000


# --- de tests ---------------------------------------------------------------------------------


async def test_een_vraag_van_2_s_stopt_na_120_ms(engine):
    """Klaar als (S1): een vraag van 2 s stopt na 120 ± 30 ms, door aiosqlite en de pool heen."""
    tijden = []
    async with engine.connect() as conn:
        for _ in range(5):
            start = time.perf_counter()
            async with grens(conn, 120) as d:
                await conn.exec_driver_sql(TRAAG)
            tijden.append((time.perf_counter() - start) * 1000)
            assert d.hit
    assert 90 <= statistics.median(tijden) <= 150, tijden
    assert min(tijden) >= 115, tijden  # nooit vóór de grens


async def test_de_verbinding_werkt_daarna_gewoon(engine):
    """Na "interrupted": dezelfde verbinding, dezelfde transactie en de pool werken door."""
    async with engine.connect() as conn:
        raw_before = (await conn.get_raw_connection()).driver_connection
        async with grens(conn, 20) as d:
            await conn.exec_driver_sql(TRAAG)
        assert d.hit
        assert (await conn.exec_driver_sql("SELECT 41 + 1")).scalar() == 42
        # In dezelfde transactie: schrijven en committen lukt
        await conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS s1_proef (x INTEGER)")
        await conn.exec_driver_sql("INSERT INTO s1_proef VALUES (1)")
        async with grens(conn, 20) as d:
            await conn.exec_driver_sql(TRAAG)
        assert d.hit
        await conn.exec_driver_sql("INSERT INTO s1_proef VALUES (2)")
        await conn.commit()
    # Terug in de pool en weer gebruikt: dezelfde verbinding, en hij werkt
    async with engine.connect() as conn:
        assert (await conn.get_raw_connection()).driver_connection is raw_before
        assert (await conn.exec_driver_sql("SELECT count(*) FROM s1_proef")).scalar() == 2
        await conn.exec_driver_sql("DROP TABLE s1_proef")
        await conn.commit()


async def test_een_late_interrupt_raakt_de_volgende_vraag_niet(engine):
    """De timer is uit vóór de volgende vraag; en een interrupt() zonder lopende vraag doet
    niets. Ook als de grens precies valt waar de vraag klaar is (de race), 200 keer."""
    async with engine.connect() as conn:
        db = _sqlite3(await conn.get_raw_connection())
        # Klaar ruim voor de grens; na de grens nog een vraag
        async with grens(conn, 50) as d:
            await conn.exec_driver_sql("SELECT 1")
        assert not d.hit
        await asyncio.sleep(0.08)
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000
        # Een interrupt() als er niets loopt
        db.interrupt()
        assert (await conn.exec_driver_sql(HALF)).scalar() == 1_500_000
        # De race: de grens op de mediane duur van de vraag, dan meteen de volgende
        tijden = [await duur(conn, text(KORT), {}) for _ in range(20)]
        ms = max(1, round(statistics.median(tijden)))
        gestopt = 0
        for _ in range(200):
            async with grens(conn, ms) as d:
                await conn.exec_driver_sql(KORT)
            gestopt += d.hit
            # De volgende vraag stopt nooit
            assert (await conn.exec_driver_sql("SELECT count(*) FROM node")).scalar() > 0
    # Beide kanten van de race kwamen voor (anders bewijst de lus weinig); alleen gemeld
    print(f"S1 race: grens {ms} ms, {gestopt} van 200 gestopt")


async def test_de_grens_raakt_alleen_de_eigen_verbinding(engine):
    """Twee verbindingen uit de pool tegelijk: de grens van de één stopt de ander niet."""
    async with engine.connect() as een, engine.connect() as twee:

        async def met_grens():
            async with grens(een, 60) as d:
                await een.exec_driver_sql(TRAAG)
            return d.hit

        async def zonder():
            return (await twee.exec_driver_sql(HALF)).scalar()

        hit, telling = await asyncio.gather(met_grens(), zonder())
    assert hit
    assert telling == 1_500_000


async def test_rondes_a_b_en_c(engine, corpus):
    """Keuze 9: een gerangschikte vraag die stopt, geeft 0 rijen; ronde B stroomt; stopt ook
    die, dan ronde C op namen. Een vraag met een zichtbare treffer geeft ≥ 1 rij. De grenzen hier
    zijn een derde van wat de vraag zonder grens duurt (op elke machine raak); de echte grenzen
    (120 en 40 ms) staan in test_rondes_met_de_echte_grenzen (zoek_perf)."""
    _, root = corpus
    args = {"p1": root, "e1": root[:-1] + "0", "n": 31}
    async with engine.connect() as conn:
        # Zonder grens: hoe lang A en B duren voor het dure voorvoegsel
        a_ms = min([await duur(conn, RONDE_A, {**args, "expr": '"ver"*'}) for _ in range(2)])
        b_ms = min([await duur(conn, RONDE_B, {**args, "expr": '"ver"*'}) for _ in range(2)])
        assert a_ms > 3 and b_ms > 3, (a_ms, b_ms)

        # 1. Een gewoon woord: ronde A haalt het, gerangschikt
        r = await zoek(conn, root, '"factuur"', None, ms_a=max(120, int(a_ms * 3)))
        assert r.ronde == "A" and r.ranked and len(r.rows) == 31

        # 2. A stopt (0 rijen), B stroomt: "de" staat in bijna elk document
        de_a = await duur(conn, RONDE_A, {**args, "expr": '"de"'})
        r = await zoek(conn, root, '"de"', None, ms_a=max(1, int(de_a / 3)), ms_b=10_000)
        assert r.ronde == "B" and r.partial and not r.ranked and len(r.rows) == 31

        # 3. A en B stoppen allebei: alleen de namen (trigram), binnen de toegang. Eva vindt
        # haar "Vergadering bestuur.pdf", niet "Verslag van Bram.pdf"
        r = await zoek(conn, root, '"ver"*', '"ver"', ms_a=int(a_ms / 3), ms_b=int(b_ms / 3))
        assert r.ronde == "C" and r.names_only and r.partial
        ids = {row[0] for row in r.rows}
        namen = set(
            (await conn.execute(
                text("SELECT name FROM node WHERE id IN (SELECT value FROM json_each(:ids))"),
                {"ids": json.dumps(sorted(ids))},
            )).scalars()
        )  # fmt: skip
        assert namen == {"Vergadering bestuur.pdf"}

        # 4. Niets zichtbaars in de namen: een eerlijke lege pagina, met partial
        r = await zoek(conn, root, '"ver"*', '"zebrakwarts"', ms_a=int(a_ms / 3),
                       ms_b=int(b_ms / 3))  # fmt: skip
        assert r.ronde == "C" and r.rows == [] and r.partial

        # De verbinding werkt daarna gewoon, met volgorde
        r = await zoek(conn, root, '"vergadering"', None, ms_a=10_000)
        assert r.ronde == "A" and r.rows


@pytest.mark.zoek_perf
async def test_rondes_met_de_echte_grenzen(tmp_path):
    """De rondes met PLATFORM_SEARCH_DEADLINE_MS = 120 en 40 ms voor B, op 20.000 documenten
    van 150 woorden (zoals V3). Meldt per ronde de tijd; eist dat het antwoord binnen
    120 + 40 + 25 ms komt en dat "ver"* via C toch de zichtbare naam geeft."""
    path = tmp_path / "groot.db"
    url = spike.head_db(path)
    conn = sqlite3.connect(path)

    def doe(c) -> None:
        eva, root = spike.gebruiker(c, "eva")
        now = spike.NU.isoformat()
        tx = spike.teksten(20_000, 150)
        shas = [hashlib.sha256(f"groot-{i}".encode()).hexdigest() for i in range(len(tx))]
        c.executemany(
            "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES (?, 1, 1, ?)",
            [(s, now) for s in shas],
        )
        c.executemany(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
            "created_at, modified_at, search_name) VALUES (?, ?, ?, ?, 'file', 'text/plain', "
            "1, ?, ?, ?, ?)",
            [(uuid.uuid4().hex, root, eva, f"stuk {i}.txt", shas[i], now, now, f"stuk {i}.txt")
             for i in range(len(tx))],
        )  # fmt: skip
        c.executemany(
            "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
            "VALUES (?, ?, '', '', 0, ?)",
            [(shas[i], tx[i], now) for i in range(len(tx))],
        )
        spike.node(c, uuid.uuid4().hex, root, eva, "Vergadering bestuur.pdf")

    spike.vul_snel(conn, doe)
    root = conn.execute("SELECT mpath FROM node WHERE parent_id IS NULL").fetchone()[0]
    conn.close()
    eng = create_async_engine(url)
    try:
        async with eng.connect() as aconn:
            for expr, names in (('"factuur"', None), ('"de"', None), ('"ver"*', '"ver"')):
                rondes, tijden = [], []
                for _ in range(5):
                    r = await zoek(aconn, root, expr, names)
                    rondes.append(r.ronde)
                    tijden.append(r.ms)
                    assert r.ms < 120 + 40 + 25, (expr, r.ronde, r.ms)
                    if expr == '"ver"*' and r.ronde == "C":
                        assert len(r.rows) == 1
                print(
                    f"S1 echt: {expr}: rondes {rondes}, mediaan {statistics.median(tijden):.1f} ms"
                )
    finally:
        await eng.dispose()
