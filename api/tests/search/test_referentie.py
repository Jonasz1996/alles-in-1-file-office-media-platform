"""De referentie (tests/search/referentie.py, bouwontwerp §4.3): de regels van de vraag, en dat
hij op de boom van 2.000 nodes precies vindt wat de index van 0010 voor die woorden heeft (de
woorden zelf: zonder de regels voor wie wat ziet, die de app in Z1 toevoegt). Z1 legt de app
naast de referentie; hier wordt de referentie zelf vastgezet."""

from __future__ import annotations

import sqlite3
import uuid
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import access
from app.config import Settings
from app.db import get_engine
from app.search import bench
from tests.search import referentie as R
from tests.search.migratie import MADE


@pytest.fixture(scope="module")
def boom(tmp_path_factory) -> tuple[Path, bench.Uitkomst, R.Referentie]:
    data = tmp_path_factory.mktemp("referentie")
    uit = bench.maak(data, R.BOOM)
    con = sqlite3.connect(data / "platform.db")
    try:
        ref = R.Referentie.lees(_Verbinding(con))
    finally:
        con.close()
    return data, uit, ref


class _Verbinding:
    """sqlite3 met exec_driver_sql, zoals Referentie.lees verwacht."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def exec_driver_sql(self, sql: str, args=()):
        return _Rijen(self.con.execute(sql, args))


class _Rijen:
    def __init__(self, cur):
        self.cur = cur

    def __iter__(self):
        return iter(self.cur)

    def all(self):
        return self.cur.fetchall()


# --- De vraag -------------------------------------------------------------------------------


def test_de_vragenlijst_is_gemaakt_en_niet_met_de_hand():
    vragen = R.lees_vragen()
    assert vragen == R.maak_vragen()
    soorten = Counter(soort for soort, _ in vragen)
    assert len(vragen) == 200
    assert soorten["operator"] == 20 and soorten["ss"] == 12 and soorten["frase"] == 16
    # ß en "ss" in beide richtingen (§4.3)
    teksten = {v for _, v in vragen}
    assert {"grosse", "große", "GROẞE", "strasse", "Straße"} <= teksten


@pytest.mark.parametrize(
    ("q", "termen", "prefix"),
    [
        ("factuur 2024", (("factuur",), ("2024",)), True),
        ("factuur ", (("factuur",),), False),
        ("de", (("de",),), False),  # korte woorden alleen heel
        ("Fac", (("fac",),), True),
        ('"de factuur"', (("de", "factuur"),), False),  # eindigt op een aanhalingsteken
        ('"de fact', (("de", "fact"),), True),  # zonder sluiter: tot het eind
        ('rapport "van de" klant', (("rapport",), ("van", "de"), ("klant",)), True),
        ("Straße", (("strasse",),), True),
        ("GROẞE", (("grosse",),), True),
        ("café", (("cafe",),), True),
        ("ﬁle", (("file",),), True),  # de ligatuur fi (NFKC)
        ("IMG_2019.jpg", (("img",), ("2019",), ("jpg",)), True),
        # Operators zijn gewone tekst
        ("factuur AND offerte", (("factuur",), ("and",), ("offerte",)), True),
        ("NEAR(factuur offerte)", (("near",), ("factuur",), ("offerte",)), False),
        ("name:factuur", (("name",), ("factuur",)), True),
        ("fact*", (("fact",),), False),
        ("-factuur", (("factuur",),), True),
    ],
)
def test_ontleed(q, termen, prefix):
    vraag = R.ontleed(q)
    assert vraag is not None
    assert (vraag.termen, vraag.prefix) == (termen, prefix)


def test_niets_te_zoeken_en_te_lang():
    for leeg in ("", "   ", "*", '""', "()", "-:"):
        assert R.ontleed(leeg) is None, leeg
    assert R.ontleed("a" * 200) is not None
    with pytest.raises(R.TeLang):
        R.ontleed("a" * 201)
    assert R.ontleed(" ".join(["woord"] * 10)) is not None
    with pytest.raises(R.TeLang):
        R.ontleed(" ".join(["woord"] * 11))
    assert R.ontleed("de q3").kort and not R.ontleed("de factuur").kort


# --- Tegen de index van 0010 ----------------------------------------------------------------


def _fts(vraag: R.Vraag) -> str:
    """De vraag als FTS5-uitdrukking: elke term een frase, het voorvoegsel met *. Alleen voor
    deze vergelijking; de app maakt hem in Z1 met parse()."""
    delen = ['"' + " ".join(term) + '"' for term in vraag.termen]
    if vraag.prefix:
        delen[-1] += "*"
    return " ".join(delen)


def test_de_referentie_vindt_wat_de_index_heeft(boom):
    """Elke vraag van vragen.tsv: de hele woorden zoals fts_node ze vindt (levend, geen album),
    en de stukken van namen zoals search_name ze heeft. 0 verschillen."""
    data, _, ref = boom
    con = sqlite3.connect(data / "platform.db")
    num = {uuid.UUID(hex=i): n for i, n in con.execute("SELECT id, num FROM node")}
    treffers = 0
    try:
        for soort, q in R.lees_vragen():
            vraag = R.ontleed(q)
            uit = ref.zoek(None, q)
            if vraag is None:
                assert uit.alles == set(), q
                continue
            index = {
                r[0]
                for r in con.execute(
                    "SELECT n.num FROM fts_node f JOIN node n ON n.num = f.rowid "
                    "WHERE fts_node MATCH ? AND n.dead = 0 AND n.kind <> 'album'",
                    (_fts(vraag),),
                )
            }
            assert {num[n] for n in uit.woorden} == index, (soort, q)
            voorwaarde = " AND ".join("instr(search_name, ?) > 0" for _ in vraag.alle)
            namen = {
                r[0]
                for r in con.execute(
                    f"SELECT num FROM node WHERE dead = 0 AND kind <> 'album' AND {voorwaarde}",
                    vraag.alle,
                )
            }
            assert {num[n] for n in uit.alles} == index | namen, (soort, q)
            treffers += len(index)
    finally:
        con.close()
    assert treffers > 1_000  # de vragen vinden echt iets


def test_ss_in_beide_richtingen(boom):
    _, _, ref = boom
    for a, b in (("grosse", "große"), ("GROẞE", "grosse"), ("strasse", "Straße")):
        assert ref.zoek(None, a).woorden == ref.zoek(None, b).woorden, (a, b)


def test_een_frase_staat_in_een_kolom():
    ref = R.Referentie()
    een, twee = uuid.uuid4(), uuid.uuid4()
    for node_id, kolommen in (
        (een, {"name": ["rapport"], "body": ["de", "factuur", "voor"]}),
        (twee, {"name": ["de"], "body": ["factuur"]}),
    ):
        volledig = {k: kolommen.get(k, []) for k in R.KOLOMMEN}
        ref.nodes[node_id] = R._Node(node_id, None, node_id, "file", "x", False, volledig, [])
        for woorden in volledig.values():
            for w in woorden:
                ref.index[w].add(node_id)
    ref.gesorteerd = sorted(ref.index)
    assert ref.zoek(None, '"de factuur"').woorden == {een}
    assert ref.zoek(None, "de factuur").woorden == {een, twee}
    assert ref.zoek(None, '"de fact').woorden == {een}


# --- Wie wat ziet: tegen access.py ----------------------------------------------------------


@pytest.mark.anyio
async def test_wat_iedereen_ziet_volgt_access(boom):
    """Voor elke gebruiker van de boom: de nodes onder zijn eigen map (niet voor een gast) en
    onder access.shared_roots (zonder album) zijn precies ref.zichtbaar()."""
    data, uit, ref = boom
    settings = Settings(data_dir=data, jwt_secret="test" * 8)
    MADE.append(settings)
    levend = ref.zichtbaar(None)
    async with AsyncSession(get_engine(settings)) as session:
        for sleutel, gebruiker in uit.gebruikers.items():
            user_id = uuid.UUID(gebruiker["id"])
            wortels = {r.node_id for r in await access.shared_roots(session, user_id)}
            if ref.rollen[user_id] != "guest":
                wortels.add(ref.wortels[user_id])
            verwacht = {n for n in levend if wortels.intersection(ref.keten(n))}
            assert ref.zichtbaar(user_id) == verwacht, sleutel
