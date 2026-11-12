"""Elke testmodule met async tests zet zelf pytest.mark.anyio: als pytestmark, op de klasse of op
de test. Dan draait anyio de test en zijn async fixtures (settings, client) ook als geen andere
fixture anyio_backend vraagt.

Waarom: test_log_hiding.py had geen markering en leunde op de autouse-fixture _databases_dicht
van tests/search/conftest.py, die anyio_backend vraagt. Onder pytest 9.1 hangen de fixtures van
een conftest aan de map-node die het eerst verzameld werd. Staat op de commandoregel een bestand
uit tests/ tussen twee bestanden uit tests/search (zoals "tests/search/test_own_live.py
tests/test_photometa.py tests/search/test_log_hiding.py"), dan verzamelt pytest tests/ opnieuw.
tests/search kreeg dan een nieuwe node zonder die fixtures, en de async tests van
test_log_hiding.py gaven bij de setup "requested an async fixture 'settings', with no plugin or
hook that handled it" (I1, C2).

De oorzaak is daarmee niet weg: hetzelfde gold voor elke submap met een conftest.py (tests/geo,
tests/ocr) en voor elke fixture daarvan, ook voor synchrone tests die een async fixture vragen
(review R1). Dat lost de hook pytest_make_collect_report in tests/conftest.py op; daar waakt
test_conftest_volgorde.py over. Deze test houdt alleen elke module met async tests los van een
autouse-fixture voor anyio.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _mark(body: list[ast.stmt]) -> bool:
    """Een pytestmark met anyio erin, in deze body (van de module of van een klasse)."""
    for node in body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        named = any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets)
        if named and "anyio" in ast.dump(value):
            return True
    return False


def _decorated(node: ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    return any("anyio" in ast.dump(d) for d in node.decorator_list)


def _async_tests(body: list[ast.stmt]) -> list[ast.AsyncFunctionDef]:
    return [n for n in body if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("test")]


def test_elke_module_met_async_tests_zet_anyio_zelf():
    zonder: list[str] = []
    for pad in sorted(TESTS.rglob("test_*.py")):
        boom = ast.parse(pad.read_text(), str(pad))
        module = _mark(boom.body)
        naam = pad.relative_to(TESTS)
        for test in _async_tests(boom.body):
            if not (module or _decorated(test)):
                zonder.append(f"{naam}::{test.name}")
        for klasse in (n for n in boom.body if isinstance(n, ast.ClassDef)):
            gemarkeerd = module or _decorated(klasse) or _mark(klasse.body)
            for test in _async_tests(klasse.body):
                if not (gemarkeerd or _decorated(test)):
                    zonder.append(f"{naam}::{klasse.name}::{test.name}")
    assert zonder == [], "zonder pytest.mark.anyio: " + ", ".join(zonder)
