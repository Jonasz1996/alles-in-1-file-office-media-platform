"""De job e2e haalt Playwright op de versie waarmee e2e/test_uit080.py zijn bomen opnam (review
golf 6, G6-6).

test_uit080.py vergelijkt de boom van aria_snapshot met uit080_web.json; een andere Playwright
kan die vorm veranderen. Elke `--with playwright` van de job draagt daarom de versie uit de
docstring van de test ("opgenomen met X.Y.Z"), ook de installatie van Chromium en de stap met
de marker office, anders past de browser niet bij de Playwright van de tests.
"""

from __future__ import annotations

import re

from tests.test_media_ci import ROOT, job


def test_de_job_e2e_haalt_de_playwright_van_de_opname():
    tekst = (ROOT / "e2e" / "test_uit080.py").read_text(encoding="utf-8")
    opname = re.findall(r"opgenomen met (\d+\.\d+\.\d+)", tekst)
    assert opname == ["1.65.2"]
    regels = "\n".join(job("e2e"))
    withs = re.findall(r"--with (\S+)", regels)
    assert withs == [f"playwright=={opname[0]}"] * 3
