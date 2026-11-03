"""Drie regels voor ci.yml en e2e/slim.sh (review Z0, haalbaar F6, F7 en F9).

Deze test leest ci.yml als tekst, zoals test_media_ci.py:

- F6: in een job met `container:` geeft de expressie runner.temp het pad op de host, niet dat in
  de container. Een actie als upload-artifact vindt daar niets; zo'n job gebruikt $RUNNER_TEMP
  in de shell (via GITHUB_ENV, zoals ZP in zoek-perf);
- F7: een `run:` die door `tee` pijpt, faalt alleen als het commando ervoor faalt met pipefail:
  `set -o pipefail` in het blok, of `shell: bash` in de defaults van de job (dan zet GitHub
  zelf -o pipefail);
- F9: de e2e draait met `uv run --with playwright python -m pytest`, zodat pytest zeker uit de
  omgeving met playwright komt.
"""

from __future__ import annotations

import re

from tests.test_media_ci import CI, ROOT, job

RUNNER_TEMP = re.compile(r"\$\{\{\s*runner\.temp\s*\}\}")
RUN = re.compile(r"^(\s*)(?:- )?run:\s*(.*)$")


def _jobs() -> list[str]:
    """De namen onder jobs: (twee spaties in)."""
    lines = CI.read_text().splitlines()
    start = lines.index("jobs:")
    return [m.group(1) for line in lines[start:] if (m := re.match(r"^  ([\w-]+):$", line))]


def _runs(lines: list[str]) -> list[tuple[int, str]]:
    """Elk `run:`-blok van een job: (regel in de job, de tekst)."""
    uit = []
    for i, line in enumerate(lines):
        m = RUN.match(line)
        if not m:
            continue
        if m.group(2) not in ("|", "|-", ">", ">-"):
            uit.append((i, m.group(2)))
            continue
        diep = len(line) - len(line.lstrip()) + (2 if line.lstrip().startswith("- ") else 0)
        blok = []
        for volgende in lines[i + 1 :]:
            if volgende.strip() and len(volgende) - len(volgende.lstrip()) <= diep:
                break
            blok.append(volgende)
        uit.append((i, "\n".join(blok)))
    return uit


def test_de_lezer_vindt_de_jobs_en_hun_blokken():
    assert {"zoek-perf", "media-image", "e2e", "acme"} <= set(_jobs())
    blokken = [tekst for _, tekst in _runs(job("media-image"))]
    assert any("tesseract.txt" in tekst for tekst in blokken)


def test_een_job_in_een_container_gebruikt_runner_temp_niet():
    fout = {
        naam: [line.strip() for line in job(naam) if RUNNER_TEMP.search(line)]
        for naam in _jobs()
        if any(re.match(r"^    container:", line) for line in job(naam))
    }
    assert "zoek-perf" in fout
    assert not any(fout.values()), fout


def test_tee_alleen_met_pipefail():
    fout = []
    for naam in _jobs():
        lines = job(naam)
        bash = any(re.match(r"^\s+shell: bash\s*$", line) for line in lines)
        for i, tekst in _runs(lines):
            if re.search(r"\|\s*tee\b", tekst) and not bash and "set -o pipefail" not in tekst:
                fout.append(f"{naam}, regel {i}: {tekst.strip().splitlines()[0]}")
    assert not fout, fout


def test_de_e2e_draait_pytest_met_python_m():
    for pad in (CI, ROOT / "e2e" / "slim.sh"):
        tekst = pad.read_text()
        assert "--with playwright pytest" not in tekst, pad
    assert "--with playwright python -m pytest ../e2e" in (ROOT / "e2e" / "slim.sh").read_text()
