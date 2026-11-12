"""numpy en onnxruntime alleen in de container analyse (fase 6, A0; Keuze 57, K2-09, K2-10).

- De app importeert ze nooit: geen import buiten app/analyse/worker/ (AST), en `python -I` met
  app.main en app.cli laadt ze niet.
- De job api draait zonder de extra: `uv sync --frozen` installeert ze niet, en uv.lock voegt met
  de extra analyse precies vijf pakketten toe (numpy, onnxruntime, protobuf, flatbuffers,
  packaging), met de versies van §3.11.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

API = Path(__file__).resolve().parents[2]
APP = API / "app"
WORKER = APP / "analyse" / "worker"  # X2a: daar mogen ze wel (in de container analyse)
FORBIDDEN = ("numpy", "onnxruntime")
EXTRA = {
    "flatbuffers": "25.12.19",
    "numpy": "2.5.4",
    "onnxruntime": "1.31.1",
    "packaging": "26.3",
    "protobuf": "7.36.2",
}


def _imports(source: str) -> list[tuple[int, str]]:
    """(regel, module) van elke import, ook importlib.import_module("…") en __import__("…")."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append((node.lineno, node.module))
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            first = node.args[0]
            if name in ("import_module", "__import__") and isinstance(first, ast.Constant):
                found.append((node.lineno, str(first.value)))
    return found


def _forbidden(module: str) -> bool:
    return module.split(".")[0] in FORBIDDEN


def test_de_scan_ziet_elke_vorm():
    source = (
        "import numpy as np\n"
        "from onnxruntime import InferenceSession\n"
        "import os, numpy.linalg\n"
        "importlib.import_module('numpy')\n"
        "__import__('onnxruntime.capi')\n"
        "from . import numpy\n"  # relatief: een eigen module, geen pakket
        "import numpyish\n"
    )
    bad = [(line, m) for line, m in _imports(source) if _forbidden(m)]
    assert bad == [
        (1, "numpy"),
        (2, "onnxruntime"),
        (3, "numpy.linalg"),
        (4, "numpy"),
        (5, "onnxruntime.capi"),
    ]


def test_geen_import_buiten_de_worker():
    bad = []
    for path in sorted(APP.rglob("*.py")):
        if path.is_relative_to(WORKER):
            continue
        for line, module in _imports(path.read_text(encoding="utf-8")):
            if _forbidden(module):
                bad.append(f"{path.relative_to(API)}:{line} {module}")
    assert bad == []


def _loaded(code: str) -> list[str]:
    """In een eigen proces met -I (geen PYTHONPATH, geen user-site): de geladen modules van
    numpy en onnxruntime na `code`."""
    script = (
        f"import json, sys\nsys.path.insert(0, {str(API)!r})\n{code}\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        f"if m.split('.')[0] in {FORBIDDEN!r})))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", script], cwd=API, capture_output=True, text=True, timeout=300
    )
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_de_app_laadt_numpy_niet():
    assert _loaded("import app.main, app.cli") == []


def test_geen_module_van_de_app_laadt_numpy():
    """Elke module onder app/ (behalve de worker van de analyse en een __main__)."""
    code = (
        "import importlib, pkgutil, app\n"
        "for m in pkgutil.walk_packages(app.__path__, 'app.'):\n"
        "    if m.name.startswith('app.analyse.worker') or m.name.endswith('__main__'):\n"
        "        continue\n"
        "    importlib.import_module(m.name)\n"
    )
    assert _loaded(code) == []


def test_de_job_api_heeft_ze_niet():
    """uv sync --frozen zonder de extra: numpy en onnxruntime zijn er niet (ook protobuf en
    flatbuffers niet)."""
    for name in (*FORBIDDEN, "flatbuffers", "google.protobuf"):
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError:  # google bestaat niet
            spec = None
        assert spec is None, name


def _closure(packages: dict, deps: list[dict]) -> set[str]:
    """De pakketten die uv export zou noemen: de afhankelijkheden, met hun extra's."""
    seen: set[tuple[str, tuple]] = set()
    todo = list(deps)
    while todo:
        dep = todo.pop()
        key = (dep["name"], tuple(dep.get("extra", ())))
        if key in seen:
            continue
        seen.add(key)
        package = packages[dep["name"]]
        todo += package.get("dependencies", [])
        for extra in dep.get("extra", ()):
            todo += package.get("optional-dependencies", {}).get(extra, [])
    return {name for name, _ in seen}


def test_de_extra_analyse_is_precies_vijf_pakketten():
    lock = tomllib.loads((API / "uv.lock").read_text())
    packages = {p["name"]: p for p in lock["package"]}
    root = packages["platform-api"]
    assert root["optional-dependencies"] == {
        "analyse": [{"name": "numpy"}, {"name": "onnxruntime"}]
    }
    base = _closure(packages, root["dependencies"])
    full = _closure(packages, root["dependencies"] + root["optional-dependencies"]["analyse"])
    assert {name: packages[name]["version"] for name in full - base} == EXTRA
    assert not base & set(EXTRA)
    project = tomllib.loads((API / "pyproject.toml").read_text())["project"]
    assert project["optional-dependencies"] == {"analyse": ["numpy>=2.5.4", "onnxruntime>=1.31.1"]}
