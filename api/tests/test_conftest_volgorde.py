"""De volgorde van de bestanden op de commandoregel verandert de fixtures van een test niet.

Onder pytest 9.1 hangen de fixtures van een conftest.py aan de eerste map-node van die map. Staat
een bestand uit tests/ tussen twee bestanden uit één submap met een eigen conftest.py (zoals
"tests/search/test_own_live.py tests/test_photometa.py tests/search/test_log_hiding.py"), dan
verzamelt pytest tests/ opnieuw en krijgt die submap een nieuwe node. Zonder de hook
pytest_make_collect_report in tests/conftest.py had die node geen fixtures van zijn conftest:
autouse-fixtures als _databases_dicht (tests/search) en _aangemeld (tests/geo) vielen stil weg,
en plaatsen (tests/geo) of stappen (tests/ocr) bestonden niet (I1, C2 en review R1).

De test verzamelt dezelfde bestanden twee keer in een eigen proces: eerst per map bij elkaar,
dan met telkens dit bestand ertussen. Per test moeten de fixtures, en de module waar elke
fixture vandaan komt, gelijk zijn, en mag er geen ontbreken.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
API = TESTS.parent

# Per submap met een conftest.py een paar bestanden die er fixtures van gebruiken
BESTANDEN = {
    # test_benchlos: synchrone tests die de async fixture settings vragen (R1); test_log_hiding:
    # de async tests van C2; test_triggers_dead: alleen het autouse _databases_dicht (R1)
    "search": ["test_benchlos.py", "test_log_hiding.py", "test_triggers_dead.py"],
    # plaatsen en het autouse _aangemeld
    "geo": ["test_places.py", "test_location_off.py", "test_places_later.py"],
    # stappen, aan en mensen
    "ocr": ["test_ocr_step.py", "test_ocr_access.py", "test_ocr_limits.py"],
    # de nep-worker fake, settings_fake en het autouse _stand_terug (fase 6, A0)
    "analyse": ["test_analyse_wire.py", "test_analyse_monitor.py", "test_fake_analyse_worker.py"],
}

# In het eigen proces: verzamelen (niets draaien) en per test de fixtures teruggeven
_VERZAMEL = """
import json, sys
import pytest

class Fixtures:
    def __init__(self):
        self.uit = {}

    def pytest_collection_finish(self, session):
        for item in session.items:
            info = item._fixtureinfo
            self.uit[item.nodeid] = {
                "namen": sorted(item.fixturenames),
                "van": {n: d[-1].func.__module__ for n, d in info.name2fixturedefs.items() if d},
            }

f = Fixtures()
code = pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider", *sys.argv[1:]], plugins=[f])
print("FIXTURES " + json.dumps(f.uit))
sys.exit(code)
"""


def _verzamel(args: list[str]) -> dict[str, dict]:
    uit = subprocess.run(
        [sys.executable, "-c", _VERZAMEL, *args],
        cwd=API,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert uit.returncode == 0, uit.stdout[-3000:] + uit.stderr[-3000:]
    regel = next(r for r in uit.stdout.splitlines() if r.startswith("FIXTURES "))
    return json.loads(regel.removeprefix("FIXTURES "))


def test_elke_submap_met_een_conftest_staat_in_de_lijst():
    mappen = {p.parent.name for p in TESTS.glob("*/conftest.py")}
    assert mappen == set(BESTANDEN)


def test_een_bestand_uit_tests_ertussen_verandert_de_fixtures_niet():
    hier = str(Path(__file__).relative_to(API))
    bij_elkaar = [hier]
    ertussen: list[str] = []
    for map_, namen in BESTANDEN.items():
        paden = [f"tests/{map_}/{naam}" for naam in namen]
        bij_elkaar += paden
        ertussen += [paden[0], hier, *paden[1:]]
    verwacht = _verzamel(bij_elkaar)
    gekregen = _verzamel(ertussen)

    # Niet leeg, en de fixtures van de conftests zitten er echt in
    assert any("_databases_dicht" in v["namen"] for v in verwacht.values())
    assert any("_aangemeld" in v["namen"] for v in verwacht.values())
    assert any("plaatsen" in v["van"] for v in verwacht.values())
    assert any("stappen" in v["van"] for v in verwacht.values())
    assert any(v["van"].get("fake") == "tests.analyse.conftest" for v in verwacht.values())
    assert all("_stand_terug" in v["namen"] for n, v in verwacht.items() if "/analyse/" in n)

    assert sorted(gekregen) == sorted(verwacht)
    verschil = [n for n in verwacht if gekregen[n] != verwacht[n]]
    assert verschil == [], f"{len(verschil)} tests met andere fixtures, o.a. {verschil[:5]}"
    for uitkomst in (verwacht, gekregen):
        zonder = {
            n: sorted(set(v["namen"]) - set(v["van"]) - {"request"})
            for n, v in uitkomst.items()
            if set(v["namen"]) - set(v["van"]) - {"request"}
        }
        assert zonder == {}
