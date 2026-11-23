"""De jobs van PostgreSQL in ci.yml (release 0.8.2; review van golf 2, F2 en F5).

Deze test leest ci.yml als tekst, zoals test_media_ci.py:

- F2: een -m op de commandoregel vervangt de -m van addopts (api/pyproject.toml). Een pytest met
  een eigen -m in api-postgres of api-postgres-15 houdt dus zelf elke uitsluiting van addopts
  (not media, not zoek_perf, ...). Anders draaien bijvoorbeeld de tijdtests van zoek_perf (100.000
  nodes op SQLite, grens 10 s) in een job die blokkeert, waar ze niet voor gemaakt zijn. Behalve
  beeld_echt: die tests staan niet onder api/tests (zie NIET_ONDER_TESTS).
- F5: de rol eigenaar van api-postgres-15 is zoals op ClusterForge: NOSUPERUSER CREATEDB, zonder
  CREATEROLE. De rol die test_dbcopy.py nodig heeft, maakt de superuser vooraf en geeft hem aan
  eigenaar (PLATFORM_TEST_PG_ROLE).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from tests.test_media_ci import ROOT, job

JOBS = ("api-postgres", "api-postgres-15")
# beeld_echt staat alleen in api/tests_analyse/, niet onder testpaths, en ci.yml noemt hem alleen
# in api-analyse en beeld-model (tests/analyse/test_beeld_ci.py). Hier hoeft een -m hem dus niet
# uit te sluiten; test_geen_beeld_echt_onder_tests houdt vast dat dat zo blijft.
NIET_ONDER_TESTS = ("not beeld_echt",)
TESTS = ROOT / "api" / "tests"
MARK = re.compile(r"""\bpytest\b[^\n]*?\s-m\s+("[^"]*"|'[^']*'|[^\s"']+)""")


def _addopts_not() -> list[str]:
    """De uitsluitingen van addopts: ["not media", "not media_perf", ...]."""
    data = tomllib.loads((ROOT / "api" / "pyproject.toml").read_text())
    addopts = data["tool"]["pytest"]["ini_options"]["addopts"]
    found = re.search(r"-m\s+'([^']*)'", addopts)
    assert found is not None, addopts
    terms = [t.strip() for t in found.group(1).split(" and ")]
    assert terms and all(t.startswith("not ") for t in terms), terms
    return terms


def _marks(name: str) -> list[str]:
    """Elke -m van een pytest in deze job (een gevouwen run: >- telt als één regel)."""
    text = " ".join(line.strip() for line in job(name))
    return [m.group(1).strip("\"'") for m in MARK.finditer(text)]


def test_een_eigen_m_houdt_de_uitsluitingen_van_addopts():
    terms = [t for t in _addopts_not() if t not in NIET_ONDER_TESTS]
    assert "not zoek_perf" in terms and "not media" in terms
    seen = 0
    for name in JOBS:
        for expr in _marks(name):
            seen += 1
            parts = [p.strip() for p in expr.split(" and ")]
            missing = [t for t in terms if t not in parts]
            assert not missing, (name, expr, missing)
    assert seen >= 2  # api-postgres-15 heeft er twee (pg, en de migraties zonder pg)


def test_de_rol_eigenaar_zoals_op_clusterforge():
    lines = job("api-postgres-15")
    roles = [line for line in lines if "CREATE ROLE eigenaar" in line]
    assert len(roles) == 1, roles
    role = roles[0]
    assert "NOSUPERUSER" in role and "CREATEDB" in role, role
    assert "CREATEROLE" not in role, role
    # De rol van test_copy_without_superuser: vooraf gemaakt en aan eigenaar gegeven
    text = "\n".join(lines)
    assert "PLATFORM_TEST_PG_ROLE:" in text
    assert re.search(r"GRANT \w+ TO eigenaar", text), text


def test_geen_beeld_echt_onder_tests():
    """Waarom NIET_ONDER_TESTS mag: geen test onder api/tests heeft de marker beeld_echt."""
    eigen = {Path(__file__).resolve(), (TESTS / "analyse" / "test_beeld_ci.py").resolve()}
    for path in TESTS.rglob("*.py"):
        if path.resolve() in eigen:
            continue
        assert "beeld_echt" not in path.read_text(), path
