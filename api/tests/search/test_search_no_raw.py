"""Geen MATCH naast een f-string of % (bouwontwerp fase 6, §4.1; Keuze 5). Alleen parse()
(app/search/query.py) maakt de uitdrukking voor FTS5; ze gaat altijd als parameter mee.

De wachter leest elke module in app/ als AST en weigert:
- een f-string waarvan de letterlijke tekst MATCH bevat;
- een tekst met MATCH in een `%`, een `+` met iets dat geen tekst is, of `.format(...)`;
- SQL-tekst waarin na MATCH geen parameter (`:naam` of `?`) staat;
- `.op("MATCH")(x)` waarbij x geen bindparam(...) is.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
WORD = re.compile(r"\bMATCH\b")
BOUND = re.compile(r"\bMATCH\s+(:\w+|\?)")


def _text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def raw(source: str) -> list[tuple[int, str]]:
    """(regel, wat) voor elke overtreding in deze broncode."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and _text(node.body[0].value) is not None
    }
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            literal = "".join(_text(v) or "" for v in node.values)
            if WORD.search(literal):
                found.append((node.lineno, "f-string met MATCH"))
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "op"
            and node.args
            and _text(node.args[0]) == "MATCH"
        ):
            outer = parents.get(node)
            if isinstance(outer, ast.Call) and outer.func is node:
                arg = outer.args[0] if outer.args else None
                name = getattr(getattr(arg, "func", None), "id", None) or getattr(
                    getattr(arg, "func", None), "attr", None
                )
                if name != "bindparam" or any(
                    isinstance(a, ast.JoinedStr | ast.BinOp) for a in arg.args[1:]
                ):
                    found.append((node.lineno, '.op("MATCH") zonder bindparam'))
            continue
        text = _text(node)
        if text is None or not WORD.search(text) or id(node) in docstrings:
            continue
        if parents.get(node) is not None and isinstance(parents[node], ast.JoinedStr):
            continue  # al gezien als f-string
        if text.strip() == "MATCH":
            continue  # de naam van de operator in .op("MATCH")
        parent = parents.get(node)
        if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mod):
            found.append((node.lineno, "% naast MATCH"))
        elif isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add):
            other = parent.right if parent.left is node else parent.left
            if _text(other) is None:
                found.append((node.lineno, "+ naast MATCH"))
        elif isinstance(parent, ast.Attribute) and parent.attr == "format":
            found.append((node.lineno, ".format() naast MATCH"))
        elif _looks_like_sql(text) and len(WORD.findall(text)) != len(BOUND.findall(text)):
            found.append((node.lineno, "MATCH zonder parameter"))
    return found


def _looks_like_sql(text: str) -> bool:
    return bool(re.search(r"\b(SELECT|WHERE|FROM)\b", text))


def test_nergens_match_naast_een_f_string_of_procent():
    files = sorted(APP.rglob("*.py"))
    found = {
        str(f.relative_to(APP)): raw(f.read_text(encoding="utf-8"))
        for f in files
        if "MATCH" in f.read_text(encoding="utf-8")
    }
    assert {k: v for k, v in found.items() if v} == {}
    # De wachter keek ook echt: de plekken die MATCH gebruiken
    assert {"search/engine.py", "search/query.py", "search/rebuild.py"} <= set(found)


def test_de_wachter_ziet_ze():
    slecht = {
        "sql = f\"SELECT 1 FROM fts_node WHERE fts_node MATCH '{q}'\"": "f-string met MATCH",
        "sql = \"SELECT 1 FROM f WHERE f MATCH '%s'\" % q": "% naast MATCH",
        'sql = "SELECT 1 FROM f WHERE f MATCH " + q': "+ naast MATCH",
        'sql = "SELECT 1 FROM f WHERE f MATCH {}".format(q)': ".format() naast MATCH",
        "sql = \"SELECT 1 FROM f WHERE f MATCH 'woord'\"": "MATCH zonder parameter",
        'x = col.op("MATCH")(q)': '.op("MATCH") zonder bindparam',
        'x = col.op("MATCH")(bindparam("e", f"{q}*"))': '.op("MATCH") zonder bindparam',
    }
    for source, wat in slecht.items():
        assert [w for _, w in raw(source)] == [wat], source
    goed = [
        'sql = "SELECT 1 FROM f WHERE f MATCH ? AND rowid = ?"',
        'sql = "SELECT 1 FROM f WHERE f MATCH :expr"',
        'x = col.op("MATCH")(bindparam(f"{name}_expr", expr))',
        '"""Alleen parse() maakt een uitdrukking voor MATCH."""',
        'MATCH_STATES = ("local", "matched")',
    ]
    for source in goed:
        assert raw(source) == [], source
