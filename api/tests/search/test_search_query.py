"""De zoekvraag naar een FTS5-uitdrukking (bouwontwerp fase 6, Keuze 5; app/search/query.py).

- een tabel met invoer en uitdrukking, met Één, café, Straße, GROẞE, grosse, İstanbul en ﬁets,
  operators die gewone tekst blijven, frases, korte woorden en de grenzen;
- fuzz met een vaste seed: parse() geeft nooit een fout van FTS5 (op een echte database op head,
  met fts_node, de kolommen zonder pocr en fts_name), en ontleedt elke vraag zoals de referentie
  (tests/search/referentie.py: dezelfde woorden, frases, het voorvoegsel en de grenzen).
"""

from __future__ import annotations

import random
import sqlite3
import uuid

import pytest

from app.search.query import MAX_CHARS, Parsed, QueryTooLong, parse
from app.text import fold
from tests.search import referentie, spike

TABEL = [
    # (invoer, match, names, words)
    ("Één", '("één"* OR "een"*)', '"een"', ("een",)),
    ("café", '("café"* OR "cafe"*)', '"cafe"', ("cafe",)),
    ("Straße", '"strasse"*', '"strasse"', ("strasse",)),
    ("GROẞE", '"grosse"*', '"grosse"', ("grosse",)),
    ("grosse", '"grosse"*', '"grosse"', ("grosse",)),
    ("große ", '"grosse"', '"grosse"', ("grosse",)),
    ("İstanbul", '("i̇stanbul"* OR "istanbul"*)', '"istanbul"', ("istanbul",)),
    ("ﬁets", '"fiets"*', '"fiets"', ("fiets",)),
    ("rapport 2019", '"rapport" AND "2019"*', '"rapport" AND "2019"', ("rapport", "2019")),
    ("rapport ", '"rapport"', '"rapport"', ("rapport",)),
    ("rap", '"rap"*', '"rap"', ("rap",)),
    # Korte woorden: als heel woord, nooit een voorvoegsel; niet in fts_name (trigram)
    ("de", '"de"', None, ("de",)),
    ("de q3", '"de" AND "q3"', None, ("de", "q3")),
    # Operators zijn gewone woorden of tekens
    ("AND OR", '"and" AND "or"', '"and"', ("and", "or")),
    ("NOT zebra", '"not" AND "zebra"*', '"not" AND "zebra"', ("not", "zebra")),
    ("NEAR(a b)", '"near" AND "a" AND "b"', '"near"', ("near", "a", "b")),
    ("name:foto*", '"name" AND "foto"', '"name" AND "foto"', ("name", "foto")),
    ("{body}: x", '"body" AND "x"', '"body"', ("body", "x")),
    (
        "^start -min +plus",
        '"start" AND "min" AND "plus"*',
        '"start" AND "min" AND "plus"',
        ("start", "min", "plus"),
    ),
    ("a_b", '"a" AND "b"', None, ("a", "b")),
    # Frases; een aanhalingsteken zonder sluiter loopt tot het eind
    ('"jaar verslag"', '"jaar verslag"', '"jaar" AND "verslag"', ("jaar", "verslag")),
    ('"jaar verslag', '"jaar verslag"*', '"jaar" AND "verslag"', ("jaar", "verslag")),
    (
        '2025 "Café Noir"',
        '"2025" AND ("café noir" OR "cafe noir")',
        '"2025" AND "cafe" AND "noir"',
        ("2025", "cafe", "noir"),
    ),
    ('""', None, None, None),
    ("", None, None, None),
    ("   \t", None, None, None),
    ("!?*:()", None, None, None),
]


@pytest.mark.parametrize("q,match,names,words", TABEL, ids=[repr(r[0]) for r in TABEL])
def test_invoer_en_uitdrukking(q, match, names, words):
    p = parse(q)
    if match is None:
        assert p is None
        return
    assert isinstance(p, Parsed)
    assert (p.match, p.names, p.words) == (match, names, words)
    assert p.match_not_pocr() == "{name extra body ocr meta} : (" + match + ")"


def test_de_grenzen():
    assert parse("a" * MAX_CHARS) is not None
    with pytest.raises(QueryTooLong):
        parse("a" * (MAX_CHARS + 1))
    tien = " ".join(f"w{i}" for i in range(10))
    assert len(parse(tien).words) == 10
    with pytest.raises(QueryTooLong):
        parse(tien + " w10")
    # Woorden in een frase tellen ook
    with pytest.raises(QueryTooLong):
        parse('"' + tien + ' w10"')


# --- fuzz -------------------------------------------------------------------------------------

STUKKEN = [
    "a", "Z", "é", "É", "ß", "ẞ", "ﬁ", "ĳ", "ǆ", "İ", "ı", "œ", "ø", "å", "ç", "ñ", "ü",
    "́", "̇", "̀", "ि", "ं", "क", "ह", "日", "本", "語", "한", "ا", "ل", "Ж",
    "ω", "ς", "𞤀", "😀", "👍🏽", "​", "‍", "‮", "\x00", "\x07", "\t", "\n", " ",
    "  ", '"', "'", "*", ":", "(", ")", "{", "}", "[", "]", "^", "-", "+", "_", ".", ",", ";",
    "/", "\\", "%", "&", "|", "<", ">", "=", "!", "?", "#", "@", "$", "€", "½", "²", "Ⅻ",
    "①", "0", "7", "42", "AND", "OR", "NOT", "NEAR", "near", "name", "body", "pocr", "rapport",
    "factuur", "verslag", "grosse", "Straße", "café", "de", "x",
]  # fmt: skip


def vraag(rng: random.Random) -> str:
    n = rng.choice([1, 2, 3, 5, 8, 12, 20, 40])
    return "".join(rng.choice(STUKKEN) for _ in range(n))


@pytest.fixture(scope="module")
def fts(tmp_path_factory):
    """Een database op head met FTS5, en een paar nodes zodat een vraag ook iets vindt."""
    pad = tmp_path_factory.mktemp("vraag") / "app.db"
    spike.head_db(pad, fts5=True)
    con = sqlite3.connect(pad, isolation_level=None)
    eva, root = spike.gebruiker(con, "eva")
    for naam in ("rapport 2019.pdf", "Café Noir.txt", "große Straße.txt", "日本語.txt"):
        node_id = uuid.uuid4().hex
        spike.node(con, node_id, root, eva, naam)
        # Zoals de app: search_name is fold(name) (spike.node zet lower())
        con.execute("UPDATE node SET search_name = ? WHERE id = ?", (fold(naam), node_id))
    yield con
    con.close()


def _draai(con: sqlite3.Connection, p: Parsed) -> None:
    con.execute("SELECT count(*) FROM fts_node WHERE fts_node MATCH ?", (p.match,)).fetchone()
    con.execute(
        "SELECT count(*) FROM fts_node WHERE fts_node MATCH ?", (p.match_not_pocr(),)
    ).fetchone()
    if p.names is not None:
        con.execute("SELECT count(*) FROM fts_name WHERE fts_name MATCH ?", (p.names,)).fetchone()


def test_fuzz_nooit_een_fout_van_fts5_en_zoals_de_referentie(fts):
    """5.000 vragen met een vaste seed, uit stukken die FTS5 en Unicode moeilijk maken. Elke
    vraag: parse() en de referentie zijn het eens (te lang, niets, of dezelfde termen en
    hetzelfde voorvoegsel), en de uitdrukkingen draaien in FTS5 zonder fout."""
    rng = random.Random("zoekvraag:6")
    gedraaid = 0
    for _ in range(5000):
        q = vraag(rng)
        try:
            ref = referentie.ontleed(q)
        except referentie.TeLang:
            with pytest.raises(QueryTooLong):
                parse(q)
            continue
        p = parse(q)
        if ref is None:
            assert p is None, q
            continue
        assert p is not None, q
        assert p.terms == ref.termen, q
        assert p.words == ref.alle, q
        assert p.prefix == ref.prefix, q
        _draai(fts, p)
        gedraaid += 1
    assert gedraaid > 3000


def test_fuzz_vindt_wat_hij_moet_vinden(fts):
    """Een paar vragen die iets moeten vinden in de namen, ook via trigram."""

    def namen(p: Parsed) -> set[str]:
        return {
            r[0]
            for r in fts.execute(
                "SELECT n.name FROM fts_node AS f CROSS JOIN node AS n ON n.num = f.rowid "
                "WHERE fts_node MATCH ?",
                (p.match,),
            )
        }

    assert namen(parse("rapp")) == {"rapport 2019.pdf"}
    assert namen(parse("CAFE noir")) == {"Café Noir.txt"}
    assert namen(parse('"café noir"')) == {"Café Noir.txt"}
    assert namen(parse("grosse strasse")) == {"große Straße.txt"}
    assert namen(parse("GROẞE")) == {"große Straße.txt"}
    assert namen(parse("AND")) == set()
    trigram = {
        r[0]
        for r in fts.execute(
            "SELECT n.name FROM fts_name AS f CROSS JOIN node AS n ON n.num = f.rowid "
            "WHERE fts_name MATCH ?",
            (parse("port").names,),
        )
    }
    assert trigram == {"rapport 2019.pdf"}
