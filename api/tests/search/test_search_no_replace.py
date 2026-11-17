"""De wachter van Keuze 3 (bouwontwerp fase 6, §4.1): de triggers van 0010 houden de index
gelijk met node, blob_text, media_item en photo_ocr, en een REPLACE (of een upsert) zou daar
langs gaan: SQLite wist de rij dan zonder dat een DELETE-trigger afgaat (zonder
recursive_triggers), en de index houdt een spook.

Een AST- en tekstscan van app/ op die vier tabellen:

- geen INSERT OR REPLACE, INSERT OR IGNORE, REPLACE INTO of INSERT … ON CONFLICT in SQL;
- geen on_conflict_do_update, on_conflict_do_nothing of prefix_with("OR …") op een insert van
  die tabellen (of van een tabel die de scan niet kan zien);
- geen .merge( van een sessie (alleen een functie uit een module, zoals media_items.merge);
- blob_text alleen via analysis.write_text(): elders geen insert, update of BlobText(…).

app/search/bench.py maakt testdata (de boom van §3.6) met een gewone INSERT in blob_text, vóór de
nodes (zie zijn docstring); hij valt alleen buiten de regel van write_text, niet buiten de rest.
De scanner zelf wordt eerst getest, op goede en foute stukjes code.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from app import models

APP = Path(__file__).resolve().parents[2] / "app"
TABELLEN = ("node", "blob_text", "media_item", "photo_ocr")
# Wie blob_text mag schrijven (bestand, functie)
SCHRIJVER = ("analysis.py", "write_text")
# Testdata, geen app-code (zie de docstring)
TESTDATA = {"search/bench.py"}

_T = "|".join(TABELLEN)
_DOEL = rf"""(?:main\.)?["'`\[]?(?:{_T})\b|\{{"""
SQL_FOUT = [
    re.compile(rf"\bINSERT\s+OR\s+(?:REPLACE|IGNORE)\s+INTO\s+(?:{_DOEL})", re.I),
    re.compile(rf"\bREPLACE\s+INTO\s+(?:{_DOEL})", re.I),
    re.compile(rf"\bINSERT\s+INTO\s+(?:{_DOEL})[^;]*?\bON\s+CONFLICT\b", re.I | re.S),
]
SQL_BLOB_TEXT = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE(?:\s+OR\s+\w+)?|REPLACE\s+INTO)\s+"
    r"""(?:main\.)?["'`\[]?blob_text\b""",
    re.I,
)
UPSERT = {"on_conflict_do_update", "on_conflict_do_nothing"}
INSERTS = {"insert", "sqlite_insert", "pg_insert"}


def _naam(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _tabel(arg: ast.expr) -> str | None:
    """De tabel van insert(arg): een model (Node), Node.__table__, table("node") of "node";
    een andere naam: "?naam" (de scan weet niet welke tabel)."""
    if isinstance(arg, ast.Attribute) and arg.attr == "__table__":
        arg = arg.value
    if isinstance(arg, ast.Name):
        model = getattr(models, arg.id, None)
        if isinstance(model, type) and hasattr(model, "__tablename__"):
            return model.__tablename__
        return f"?{arg.id}"
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Call) and _naam(arg.func) == "table" and arg.args:
        return _tabel(arg.args[0])
    return None


def _insert_van(node: ast.expr) -> tuple[bool, str | None]:
    """Loop de keten a.b(…).c(…) af naar binnen: (een insert gevonden, zijn tabel)."""
    while True:
        if isinstance(node, ast.Call):
            if _naam(node.func) in INSERTS and isinstance(node.func, ast.Name | ast.Attribute):
                inner = node.func.value if isinstance(node.func, ast.Attribute) else None
                if inner is None or not isinstance(inner, ast.Call):
                    return True, _tabel(node.args[0]) if node.args else None
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return False, None


def _modules(tree: ast.AST, pad: Path) -> set[str]:
    """De namen die in dit bestand voor een module staan (import x, from a import b as c als
    a.b een module is)."""
    uit: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                uit.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                try:
                    spec = importlib.util.find_spec(f"{n.module}.{a.name}")
                except (ImportError, ValueError):
                    spec = None
                if spec is not None:
                    uit.add(a.asname or a.name)
    return uit


def _functies(tree: ast.AST) -> dict[ast.AST, str]:
    """Per knoop de naam van de functie waar hij in staat."""
    uit: dict[ast.AST, str] = {}
    for f in ast.walk(tree):
        if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef):
            for n in ast.walk(f):
                uit.setdefault(n, f.name)
    return uit


def scan(bron: str, pad: str) -> list[str]:
    """Wat er mis is in deze bron (pad: relatief aan app/), als regels "pad:regel: wat"."""
    fouten: list[str] = []
    for regel in SQL_FOUT:
        for m in regel.finditer(bron):
            nr = bron.count("\n", 0, m.start()) + 1
            fouten.append(f"{pad}:{nr}: {m.group(0)[:40]!r} (tekst)")
    tree = ast.parse(bron)
    modules = _modules(tree, Path(pad))
    in_functie = _functies(tree)
    schrijver = Path(pad).name == SCHRIJVER[0]
    testdata = pad in TESTDATA

    def mag_blob_text(n: ast.AST) -> bool:
        return testdata or (schrijver and in_functie.get(n) == SCHRIJVER[1])

    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            for regel in SQL_FOUT:
                if regel.search(n.value):
                    fouten.append(f"{pad}:{n.lineno}: SQL met REPLACE of upsert")
            if SQL_BLOB_TEXT.search(n.value) and not mag_blob_text(n):
                fouten.append(f"{pad}:{n.lineno}: blob_text buiten analysis.write_text (SQL)")
        if not isinstance(n, ast.Call):
            continue
        naam = _naam(n.func)
        if naam in UPSERT or (
            naam == "prefix_with"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and str(n.args[0].value).strip().upper().startswith("OR")
        ):
            gevonden, tabel = _insert_van(n.func)
            if not gevonden or tabel is None or tabel.startswith("?") or tabel in TABELLEN:
                fouten.append(f"{pad}:{n.lineno}: {naam} op {tabel or 'een onbekende tabel'}")
        if naam == "merge" and isinstance(n.func, ast.Attribute):
            ontvanger = n.func.value
            if not (isinstance(ontvanger, ast.Name) and ontvanger.id in modules):
                fouten.append(f"{pad}:{n.lineno}: .merge( van een sessie")
        blob_text = (
            (naam in {"insert", "update", "sqlite_insert", "pg_insert"} and n.args
             and _tabel(n.args[0]) == "blob_text")
            or naam == "BlobText"
        )  # fmt: skip
        if blob_text and not mag_blob_text(n):
            fouten.append(f"{pad}:{n.lineno}: blob_text buiten analysis.write_text")
    return sorted(set(fouten))


# --- De scanner zelf ------------------------------------------------------------------------

FOUT = [
    'db.execute(text("INSERT OR REPLACE INTO node (id) VALUES (:id)"))',
    'db.execute(text("insert or ignore into blob_text (blob_sha) values (?)"))',
    'db.execute(text("REPLACE INTO media_item (id) VALUES (1)"))',
    'conn.exec_driver_sql("INSERT INTO photo_ocr (node_id) VALUES (?) ON CONFLICT DO NOTHING")',
    'sql = f"INSERT OR REPLACE INTO {tabel} VALUES (?)"',
    "db.execute(insert(Node).values(x=1).on_conflict_do_nothing())",
    "db.execute(sqlite_insert(MediaItem).values(v).on_conflict_do_update(index_elements=['id']))",
    "db.execute(sqlite.insert(PhotoOcr.__table__).values(v).on_conflict_do_nothing())",
    'db.execute(insert(Node).prefix_with("OR REPLACE").values(x=1))',
    "db.execute(insert(tabel).values(v).on_conflict_do_nothing())",
    "await db.merge(Node(id=1))",
    "session.merge(x)",
    "db.execute(insert(BlobText).values(blob_sha=sha))",
    "db.execute(update(BlobText).where(BlobText.blob_sha == sha).values(body=''))",
    "db.add(BlobText(blob_sha=sha, body=''))",
    'db.execute(text("UPDATE blob_text SET body = :b"))',
]
GOED = [
    'db.execute(text("INSERT INTO node (id) VALUES (:id)"))',
    'db.execute(text("UPDATE node SET name = :n WHERE id = :id"))',
    '"""Nooit INSERT OR REPLACE: de triggers houden FTS gelijk."""',
    "db.execute(insert(Node).values(x=1))",
    "db.execute(sqlite_insert(PlaybackState).values(v).on_conflict_do_update(index_elements=['id']))",
    'db.execute(text("INSERT INTO progress (id) VALUES (1) ON CONFLICT DO NOTHING"))',
    "from app.media import items as media_items\nmedia_items.merge(item, fields, 'name')",
    "from app.media import profile as profiles\nprofiles.merge(body.profile)",
    "db.execute(select(BlobText.body).where(BlobText.blob_sha == sha))",
]


@pytest.mark.parametrize("code", FOUT)
def test_de_scanner_ziet_het(code):
    assert scan(code, "x.py"), code


@pytest.mark.parametrize("code", GOED)
def test_de_scanner_laat_dit_staan(code):
    assert scan(code, "x.py") == [], code


def test_alleen_write_text_schrijft_blob_text():
    code = (
        "async def write_text(db, sha):\n"
        "    await db.execute(insert(BlobText).values(blob_sha=sha))\n"
        "async def iets_anders(db, sha):\n"
        "    await db.execute(update(BlobText).values(body=''))\n"
    )
    assert [f.split(": ", 1)[1] for f in scan(code, "analysis.py")] == [
        "blob_text buiten analysis.write_text"
    ]
    assert scan(code, "search/bench.py") == []


# --- app/ -----------------------------------------------------------------------------------


def test_niets_in_app():
    bestanden = sorted(APP.rglob("*.py"))
    assert len(bestanden) > 100
    fouten = []
    for pad in bestanden:
        fouten += scan(pad.read_text(encoding="utf-8"), str(pad.relative_to(APP)))
    assert fouten == []


def test_write_text_bestaat_en_schrijft():
    """De regel heeft zin: write_text staat er, en schrijft blob_text echt."""
    bron = (APP / SCHRIJVER[0]).read_text(encoding="utf-8")
    tree = ast.parse(bron)
    functies = [
        f
        for f in ast.walk(tree)
        if isinstance(f, ast.AsyncFunctionDef | ast.FunctionDef) and f.name == SCHRIJVER[1]
    ]
    assert len(functies) == 1
    namen = {_naam(n.func) for n in ast.walk(functies[0]) if isinstance(n, ast.Call)}
    assert {"insert", "update"} <= namen
