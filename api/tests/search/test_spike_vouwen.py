"""Spike S8 (bouwontwerp fase 6, §3.7; Keuze 5 en 21; J1): de twee vormen van een woord.

De tekst-vorm is wat parse() van een woord maakt: NFKC, kleine letters, ß en ẞ naar "ss". De
naam-vorm is fold() (app/text.py), waarmee search_name gemaakt wordt. In de index staat de naam
als search_name en de tekst na het opschonen van Keuze 21 (NFKC), met ß en ẞ als "ss" door de
view. Een woord zoekt in beide vormen: ("a" OR "b"), of één keer als ze gelijk zijn.

Hier: ß, ẞ, ligaturen (ﬁ, ﬂ, ĳ, ǆ), İ en accenten vinden de naam én de tekst, in elke schrijfwijze;
"grosse" en "große" vinden elkaar, "strasse" en "Straße" ook. Op de SQLite van deze python
(3.53.1 met uv, 3.45.1 op de host) en in de job zoek-perf op 3.46.1 (python:3.12-slim).
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
import uuid

import pytest

from app.text import fold
from tests.search import spike

# Elke groep: schrijfwijzen van één woord. Elke schrijfwijze staat als naam en als tekst in de
# database, en elke schrijfwijze als vraag moet ze allemaal vinden (en niets van een andere groep).
GROEPEN = {
    "straat": ("Straße", "STRASSE", "strasse", "STRAẞE"),
    "groot": ("große", "grosse", "GROẞE", "Grosse"),
    "fiets": ("ﬁets", "fiets", "FIETS"),
    "flink": ("ﬂink", "flink"),
    "ijsje": ("ĳsje", "ijsje", "IJsje", "Ĳsje"),
    "dzemal": ("ǆemal", "Ǆemal", "dzemal"),
    "istanbul": ("İstanbul", "istanbul", "ISTANBUL"),
    "een": ("Één", "één", "een"),
    "cafe": ("café", "cafe", "CAFÉ"),
    "oeuvre": ("œuvre", "Œuvre"),
}


def tekstvorm(woord: str) -> str:
    """Wat parse() van een woord maakt (Keuze 5): NFKC, kleine letters, ß en ẞ naar "ss"."""
    return unicodedata.normalize("NFKC", woord).lower().replace("ß", "ss").replace("ẞ", "ss")


def woorden(vraag: str) -> list[str]:
    """Splitsen zoals parse() moet doen: een woord is letters, cijfers en de tekens die erop
    staan (Unicode M). Zonder M valt "İstanbul" in twee woorden (zie de test hieronder)."""
    return re.findall(r"(?:[^\W_]|[̀-ͯ᪰-᫿᷀-᷿⃐-⃿])+",
                      tekstvorm(vraag))  # fmt: skip


def uitdrukking(woord: str) -> str:
    a, b = tekstvorm(woord), fold(woord)
    return f'"{a}"' if a == b else f'("{a}" OR "{b}")'


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """Een database op head (de echte migratie, de echte view en triggers), met per schrijfwijze
    een bestand met die naam, en een bestand waarvan de tekst die schrijfwijze heeft."""
    pad = tmp_path_factory.mktemp("vouwen") / "app.db"
    spike.head_db(pad, fts5=True)
    con = sqlite3.connect(pad, isolation_level=None)
    con.execute("PRAGMA foreign_keys=ON")
    user, root = spike.gebruiker(con, "eva")
    waar: dict[str, str] = {}
    teller = 0
    for groep, vormen in GROEPEN.items():
        for vorm in vormen:
            naam_id = uuid.uuid4().hex
            con.execute(
                "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, "
                "modified_at, search_name) VALUES (?, ?, ?, ?, 'file', 1, ?, ?, ?)",
                (naam_id, root, user, f"{vorm}.txt", spike.NU.isoformat(), spike.NU.isoformat(),
                 fold(f"{vorm}.txt")),
            )  # fmt: skip
            waar[naam_id] = f"{groep}: naam {vorm}"
            sha = uuid.uuid4().hex * 2
            con.execute(
                "INSERT INTO blob (sha256, size, ref_count, created_at) VALUES (?, 1, 1, ?)",
                (sha, spike.NU.isoformat()),
            )
            # De tekst zoals Keuze 21 hem opslaat: NFKC, de ß blijft staan
            body = unicodedata.normalize("NFKC", f"Brief over {vorm}, met groet.")
            con.execute(
                "INSERT INTO blob_text (blob_sha, body, updated_at) VALUES (?, ?, ?)",
                (sha, body, spike.NU.isoformat()),
            )
            # Een naam zonder het woord zelf: dan vindt {name} alleen de namen
            tekst_id, teller = uuid.uuid4().hex, teller + 1
            con.execute(
                "INSERT INTO node (id, parent_id, owner_id, name, kind, size, blob_id, "
                "created_at, modified_at, search_name) VALUES (?, ?, ?, ?, 'file', 1, ?, ?, ?, ?)",
                (tekst_id, root, user, f"brief-{teller:03}.txt", sha, spike.NU.isoformat(),
                 spike.NU.isoformat(), f"brief-{teller:03}.txt"),
            )  # fmt: skip
            waar[tekst_id] = f"{groep}: tekst {vorm}"
    con.close()
    assert spike.herbouw_verschil(pad) == {}
    con = sqlite3.connect(pad)
    yield con, waar
    con.close()


def _vind(con, kolom: str, expr: str) -> set[str]:
    return {
        r[0]
        for r in con.execute(
            "SELECT n.id FROM fts_node AS f CROSS JOIN node AS n ON n.num = f.rowid "
            "WHERE fts_node MATCH ?",
            (f"{{{kolom}}} : {expr}",),
        )
    }


def test_de_versie(db):
    con, _ = db
    print(f"\nS8 op SQLite {sqlite3.sqlite_version}")


@pytest.mark.parametrize("groep", list(GROEPEN))
def test_elke_schrijfwijze_vindt_naam_en_tekst(db, groep):
    con, waar = db
    namen = {i for i, w in waar.items() if w.startswith(f"{groep}: naam")}
    teksten = {i for i, w in waar.items() if w.startswith(f"{groep}: tekst")}
    for vraag in GROEPEN[groep]:
        expr = uitdrukking(vraag)
        assert _vind(con, "name", expr) == namen, (vraag, expr)
        assert _vind(con, "body", expr) == teksten, (vraag, expr)
        # Elke vorm apart vindt ook alles (J1: voor de vormen die we kennen zijn ze gelijk)
        for vorm in {tekstvorm(vraag), fold(vraag)}:
            assert _vind(con, "name", f'"{vorm}"') == namen, (vraag, vorm)
            assert _vind(con, "body", f'"{vorm}"') == teksten, (vraag, vorm)
        # Ronde C (trigram op search_name) met de naam-vorm
        gevonden = {
            r[0]
            for r in con.execute(
                "SELECT n.id FROM fts_name AS f CROSS JOIN node AS n ON n.num = f.rowid "
                "WHERE fts_name MATCH ?",
                (f'"{fold(vraag)}"',),
            )
        }
        assert gevonden == namen, (vraag, fold(vraag))


def test_grosse_en_strasse_vinden_elkaar(db):
    con, waar = db
    for a, b in (("grosse", "große"), ("strasse", "Straße")):
        for vraag in (a, b):
            gevonden = _vind(con, "name", uitdrukking(vraag)) | _vind(
                con, "body", uitdrukking(vraag)
            )
            assert {waar[i] for i in gevonden} >= {
                f"{'groot' if a == 'grosse' else 'straat'}: naam {b}",
                f"{'groot' if a == 'grosse' else 'straat'}: tekst {b}",
                f"{'groot' if a == 'grosse' else 'straat'}: naam {a}",
                f"{'groot' if a == 'grosse' else 'straat'}: tekst {a}",
            }


def test_istanbul_blijft_een_woord(db):
    """NFKC en kleine letters maken van "İ" een "i" met een punt erboven (U+0307). Splitsen op
    "alles wat geen letter of cijfer is" maakt er dan twee woorden van: "i" en "stanbul". De
    regel voor parse(): de tekens op een letter (Unicode M) horen bij het woord."""
    assert tekstvorm("İstanbul") == "i̇stanbul"
    assert re.findall(r"[^\W_]+", tekstvorm("İstanbul")) == ["i", "stanbul"]
    assert woorden("İstanbul in mei") == ["i̇stanbul", "in", "mei"]
    con, waar = db
    namen = {i for i, w in waar.items() if w.startswith("istanbul: naam")}
    assert _vind(con, "name", '"i̇stanbul"') == namen
    assert _vind(con, "name", '"stanbul"') == set()


def test_een_titel_met_ss(tmp_path):
    """De titel van een film (media_item.title, kolom extra) gaat ook door SS() in de view: met
    alleen body, ocr en meta vond "grosse" de titel "Die große Stille" niet (S8, herzien)."""
    pad = tmp_path / "app.db"
    spike.head_db(pad, fts5=True)
    con = sqlite3.connect(pad, isolation_level=None)
    user, root = spike.gebruiker(con, "eva")
    spike.node(con, "a" * 32, root, user, "film.mkv", mime="video/x-matroska")
    # Zonder foreign keys: de bibliotheek zelf doet er hier niet toe
    con.execute(
        "INSERT INTO media_item (node_id, library_id, kind, title, sort_title, year, "
        "added_at, updated_at) VALUES (?, ?, 'movie', ?, ?, 2005, ?, ?)",
        ("a" * 32, "b" * 32, "Die große Stille", "die grosse stille", spike.NU.isoformat(),
         spike.NU.isoformat()),
    )  # fmt: skip
    for vraag in ("grosse", "große", "GROẞE"):
        assert _vind(con, "extra", uitdrukking(vraag)) == {"a" * 32}, vraag
    con.close()
    assert spike.herbouw_verschil(pad) == {}


def test_tekst_zonder_nfkc_vindt_de_ligatuur_niet():
    """Waarom het opschonen van Keuze 21 NFKC moet doen: unicode61 breekt een ligatuur niet op.
    In ruwe tekst blijft "ﬁets" het token "ﬁets", en "fiets" vindt het niet. ß gaat door de view
    (SS), maar ﬁ, ﬂ, ĳ en ǆ niet."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE VIRTUAL TABLE f USING fts5(t, tokenize='unicode61 remove_diacritics 2')")
    for ruw in ("ﬁets", "ĳsje", "ǆemal"):
        con.execute("INSERT INTO f VALUES (?)", (ruw,))
        vraag = f'"{fold(ruw)}"'
        assert con.execute("SELECT count(*) FROM f WHERE f MATCH ?", (vraag,)).fetchone() == (0,)
        # Na NFKC wel
        con.execute("INSERT INTO f VALUES (?)", (unicodedata.normalize("NFKC", ruw),))
        assert con.execute("SELECT count(*) FROM f WHERE f MATCH ?", (vraag,)).fetchone() == (1,)
    con.close()
