"""De zoekvragen gaan weer door de cache van SQLAlchemy, zoals in 0.8.1 (0.8.2, P5).

De stukken per dialect van PR #6 (FtsMatch, FtsRank, NotFromPocr, AllWordsIn, NumNotIn,
InRanges) hadden inherit_cache = False: elke zoekvraag werd dan bij elke uitvoering opnieuw
gecompileerd, ook op SQLite (0,3 tot 0,9 ms per statement in de event loop). Nu is hun sleutel de
vorm en gaan de waarden als parameters mee. Hier:

- dezelfde vorm met andere woorden, bereiken of nums: één sleutel, en de waarden in de sleutel
  zijn die van de vraag zelf;
- een andere vorm (fts_node of fts_name, een woord meer, een eigenaar of niet, gedeeld of niet,
  meer bereiken, groeperen of niet): een andere sleutel;
- tegen een echte database (SQLite of PostgreSQL) met een eigen cache: een vraag met dezelfde
  vorm voegt niets toe en geeft haar eigen treffers (geen waarde van de vorige vraag), een
  andere vorm voegt een vorm toe en geeft ook haar eigen treffers.

De tekst van de SQL op SQLite houdt test_sql_sqlite.py vast.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.sql import operators

from app.db import get_engine
from app.models import User
from app.search import engine as E
from app.search.access import MAX_OR, Ranges, ranges_sql, search_ranges
from app.search.query import parse
from app.search.types import SearchParams
from app.text import fold
from tests.factories import create_file, create_user, maker

pytestmark = pytest.mark.anyio

U = User(id=uuid.UUID(int=1), root_id=uuid.UUID(int=2))
EEN = Ranges([("a/", "a0")], shared=False)
ANDER = Ranges([("b/", "b0")], shared=False)
TWEE = Ranges([("a/", "a0"), ("b/", "b0")], shared=True)
VEEL = Ranges([(f"{i:03d}/", f"{i:03d}0") for i in range(MAX_OR + 1)], shared=True)


def _scope(roots: Ranges, q: str) -> E.Scope:
    return E.Scope(user=U, roots=roots, p=SearchParams(q=q))


def _key(sel):
    key = sel._generate_cache_key()
    assert key is not None, "geen sleutel: deze vraag gaat niet door de cache"
    return key


def _waarden(sel) -> dict:
    return {b.key: b.value for b in _key(sel).bindparams}


def test_dezelfde_vorm_deelt_een_sleutel_met_eigen_waarden():
    a = E.ranked_select(parse("rode fiets"), _scope(TWEE, "rode fiets"), 30)
    b = E.ranked_select(parse("blauwe auto"), _scope(TWEE, "blauwe auto"), 30)
    assert _key(a) == _key(b)
    assert _waarden(a)["fts_node_expr"] != _waarden(b)["fts_node_expr"]
    assert _waarden(b)["fts_node_q"] == parse("blauwe auto").tsquery()
    # Andere bereiken van dezelfde vorm, andere nums om over te slaan
    c = E.names_select(parse("brief"), _scope(EEN, "brief"), 30, 0, [1, 2])
    d = E.names_select(parse("kaart"), _scope(ANDER, "kaart"), 30, 0, [7, 8, 9])
    assert _key(c) == _key(d)
    assert _waarden(d)["rp0"] == "b/" and _waarden(d)["without_nums"] == [7, 8, 9]
    assert _waarden(d)["without"] == "[7, 8, 9]" and _waarden(d)["fts_name_w0"] == "%kaart%"


@pytest.mark.parametrize(
    "een, ander",
    [
        # fts_node (hele woorden) of fts_name (stukken van namen)
        (lambda: E.ranked_select(parse("brief"), _scope(EEN, "brief"), 30),
         lambda: E.names_select(parse("brief"), _scope(EEN, "brief"), 30, 0)),
        # een lang woord meer voor fts_name (op PostgreSQL een LIKE meer)
        (lambda: E.names_select(parse("brief"), _scope(EEN, "brief"), 30, 0),
         lambda: E.names_select(parse("brief kaart"), _scope(EEN, "brief kaart"), 30, 0)),
        # eigen bereik of gedeeld (CASE in de rang, NotFromPocr)
        (lambda: E.ranked_select(parse("brief"), _scope(EEN, "brief"), 30),
         lambda: E.ranked_select(parse("brief"), _scope(TWEE, "brief"), 30)),
        # tot MAX_OR bereiken elk twee parameters, daarboven één lijst
        (lambda: E.stream_select(parse("brief"), _scope(TWEE, "brief"), 30, None, 0),
         lambda: E.stream_select(parse("brief"), _scope(VEEL, "brief"), 30, None, 0)),
        # met of zonder nums om over te slaan
        (lambda: E.names_select(parse("brief"), _scope(EEN, "brief"), 30, 0),
         lambda: E.names_select(parse("brief"), _scope(EEN, "brief"), 30, 0, [1])),
        # met of zonder eigenaar (otsv alleen voor de eigen nodes)
        (lambda: select(E.FTS_NODE.c.rowid).where(E.FtsMatch("fts_node", parse("brief"))),
         lambda: select(E.FTS_NODE.c.rowid).where(E.FtsMatch("fts_node", parse("brief"), U.id))),
        # de rang met of zonder de OCR van een eigen foto
        (lambda: select(E.FtsRank(parse("brief"), True)),
         lambda: select(E.FtsRank(parse("brief"), False))),
    ],
)  # fmt: skip
def test_een_andere_vorm_heeft_een_andere_sleutel(een, ander):
    assert _key(een()) != _key(ander())
    assert _key(een()) == _key(een())


def test_groeperen_hoort_bij_de_vorm():
    """Op SQLite krijgt de MATCH haakjes als hij gegroepeerd is (F17): dat is een andere SQL."""
    m = E.FtsMatch("fts_node", parse("brief"), U.id)
    assert _key(select(m)) != _key(select(m.self_group(against=operators.and_)))
    # Ook als m zijn eigen sleutel al onthield: de gegroepeerde kloon neemt die niet over
    assert _key(m) != _key(m.self_group(against=operators.and_))


def test_de_parameters_van_de_bereiken_zijn_op_elke_dialect_gelijk():
    """InRanges maakt zijn parameters zonder dialect; de SQL per dialect gebruikt dezelfde."""
    for roots in (EEN, TWEE, VEEL, Ranges([], shared=False)):
        assert ranges_sql(roots.ranges)[1] == ranges_sql(roots.ranges, dialect="postgresql")[1]


async def _wereld(settings) -> dict:
    """Eva en Bram, elk met een paar bestanden in hun hoofdmap; de index vullen de triggers."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        for owner, name in (
            (eva, "alfa rapport.txt"),
            (eva, "bravo rapport.txt"),
            (eva, "alfa memo.txt"),
            (eva, "charlie.txt"),
            (bram, "alfa bram.txt"),
        ):
            await create_file(session, owner, None, name, data=name.encode())
        await session.commit()
        return {
            "eva": (eva, await search_ranges(session, eva)),
            "bram": (bram, await search_ranges(session, bram)),
        }


async def test_tegen_de_database_een_vorm_een_keer_gecompileerd(settings):
    wereld = await _wereld(settings)
    cache: dict = {}
    async with get_engine(settings).connect() as conn:
        conn = await conn.execution_options(compiled_cache=cache)

        async def namen(sel) -> set[str]:
            return {r.search_name for r in (await conn.execute(sel)).all()}

        def scope(wie: str, q: str) -> E.Scope:
            user, roots = wereld[wie]
            return E.Scope(user=user, roots=roots, p=SearchParams(q=q))

        def ranked(wie: str, q: str):
            return E.ranked_select(parse(q), scope(wie, q), 30)

        def names(wie: str, q: str, without=()):
            return E.names_select(parse(q), scope(wie, q), 30, 0, without)

        alfa = {fold("alfa rapport.txt"), fold("alfa memo.txt")}
        assert await namen(ranked("eva", "alfa")) == alfa
        assert len(cache) == 1
        # Dezelfde vorm: geen nieuwe vorm, en de woorden en het bereik van deze vraag
        assert await namen(ranked("eva", "bravo")) == {fold("bravo rapport.txt")}
        assert await namen(ranked("bram", "alfa")) == {fold("alfa bram.txt")}
        assert len(cache) == 1
        # fts_name met één lang woord, dan met twee: een vorm meer, met elk zijn treffers
        assert await namen(names("eva", "alf")) == alfa
        assert len(cache) == 2
        assert await namen(names("eva", "alf rap")) == {fold("alfa rapport.txt")}
        assert len(cache) == 3
        assert await namen(names("eva", "bra rap")) == {fold("bravo rapport.txt")}
        assert len(cache) == 3
        # Andere nums om over te slaan, dezelfde vorm
        nums = {r.search_name: r.num for r in (await conn.execute(names("eva", "alf"))).all()}
        memo = await namen(names("eva", "alf", [nums[fold("alfa rapport.txt")]]))
        assert memo == {fold("alfa memo.txt")}
        assert len(cache) == 4
        rapport = await namen(names("eva", "alf", [nums[fold("alfa memo.txt")]]))
        assert rapport == {fold("alfa rapport.txt")}
        assert await namen(names("eva", "alf", list(nums.values()))) == set()
        assert len(cache) == 4
