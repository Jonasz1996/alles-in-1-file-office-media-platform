"""De vorm van de job beeld-model in ci.yml (fase 6, X3a; K2-03, Keuze 83).

Leest ci.yml als tekst, zoals test_media_ci.py en test_zoek_ci.py:

- de job draait alleen als git diff een wijziging ziet in de omzetter, tokenizer.py, onnxw.py of
  models.lock, of als de PR een regel "beeld-model: ja" heeft; geen workflow_dispatch, geen cache;
- de tekst van de PR komt via env binnen, nooit als expressie in een script;
- de omzetter start zoals de dienst analyse-omzetten (compose van X2a);
- BZ1, BZ2, BZ3, BZ4, BZ6 en BZ7 hebben elk een stap; de tests met marker beeld_echt draaien
  alleen hier, BZ6 onder taskset -c 0;
- het verslag via upload-artifact, en als laatste stap het blok `=== kort ===`.
"""

from __future__ import annotations

import re

import yaml

from app.analyse import runtime
from tests.search.test_zoek_ci import _runs
from tests.test_media_ci import CI, job

# De opdrachtregel van de dienst analyse-omzetten: python -I -c <boot> /srv --bron /bron --uit /uit
BOOT = (
    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"
)
PADEN = [
    "app/analyse/worker/omzetten",
    "app/analyse/tokenizer.py",
    "app/analyse/onnxw.py",
    "app/analyse/models.lock",
]


def _stappen() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["beeld-model"]["steps"]


def _blokken() -> list[str]:
    return [re.sub(r"\\\n\s*", "", tekst) for _, tekst in _runs(job("beeld-model"))]


def test_alleen_push_en_pull_request_zonder_cache():
    doc = yaml.safe_load(CI.read_text())
    # PyYAML leest de sleutel on als True; dus ook geen workflow_dispatch
    assert set(doc[True]) == {"push", "pull_request"}
    regels = "\n".join(job("beeld-model"))
    assert "actions/cache" not in regels and "cache:" not in regels
    assert "enable-cache" not in regels


def test_de_poort_kijkt_naar_git_diff_en_de_tekst_van_de_pr():
    stappen = _stappen()
    assert stappen[0]["uses"] == "actions/checkout@v4" and stappen[0]["with"]["fetch-depth"] == 0
    poort = stappen[1]
    assert poort["id"] == "poort"
    assert poort["env"]["PR_TEKST"] == "${{ github.event.pull_request.body }}"
    script = poort["run"]
    assert "${{" not in script  # alles via env: geen expressie in het script
    m = re.search(r"^paden=\((.*)\)$", script, re.M)
    assert m and m.group(1).split() == PADEN
    assert 'git diff --name-only "$reeks" -- "${paden[@]}"' in script
    assert "beeld-model:[[:space:]]*ja" in script
    assert 'echo "draaien=$draaien" >> "$GITHUB_OUTPUT"' in script
    # Elke stap daarna hangt aan de poort (direct of via een stap die eraan hangt), behalve
    # de samenvatting
    for s in stappen[2:-1]:
        conditie = str(s.get("if", ""))
        assert re.search(r"steps\.(poort|bron|omzetten)\.", conditie), s


def test_de_omzetter_start_zoals_de_dienst_analyse_omzetten():
    omzetten = next(s for s in _stappen() if s.get("id") == "omzetten")
    assert omzetten["env"]["BOOT"] == BOOT
    assert 'python -I -c "$BOOT" "$PWD" --bron "$WERK/bron" --uit "$WERK/uit$n"' in omzetten["run"]
    assert "extra=(--fp32)" in omzetten["run"]
    # Sinds de samenvoeging met X2a: dezelfde regel als app/analyse/runtime.py (de dienst
    # analyse-omzetten in compose.analyse.yaml start OMZET_BOOT, test_compose_analyse.py)
    assert runtime.OMZET_BOOT == BOOT


def test_elke_bz_heeft_een_stap():
    blokken = "\n".join(_blokken())
    for stap in ("bereik", "bron", "vergelijk", "set", "uitslag"):
        assert f'python3 -I ../tools/beeld/beeld_model.py {stap} --werk "$WERK"' in blokken, stap
    for stap in ("bron", "vergelijk", "set"):
        regel = next(b for b in _blokken() if f"beeld_model.py {stap} " in b)
        assert "|| [ $? -eq 3 ]" in regel, stap  # een voorstel is geen fout; de uitslag is rood
    for bestand in ("test_beeld_tokenizer_echt.py", "test_beeld_kwaliteit.py"):
        assert f"uv run --no-sync pytest -q -m beeld_echt -rs tests_analyse/{bestand}" in blokken
    assert (
        "taskset -c 0 uv run --no-sync pytest -q -m beeld_echt -rs "
        "tests_analyse/test_beeld_echt_perf.py" in blokken
    )
    assert blokken.count('grep -q "^SKIPPED"') == 3  # overslaan telt als mislukt


def test_het_verslag_en_als_laatste_het_blok_kort():
    stappen = _stappen()
    upload = [s for s in stappen if s.get("uses") == "actions/upload-artifact@v4"]
    assert len(upload) == 1 and "always()" in upload[0]["if"]
    assert "uit1/meta.json" in upload[0]["with"]["path"]
    laatste = stappen[-1]
    assert laatste["name"] == "Samenvatting" and laatste["if"] == "always()"
    assert laatste["run"].startswith('echo "=== kort ==="')
    assert stappen[-3]["name"].startswith("Uitslag")


def test_beeld_echt_draait_nergens_anders():
    # api-analyse (X2a) sluit hem uit, met precies één -m "not beeld_echt"; elke andere job
    # noemt hem niet
    for naam in re.findall(r"^  ([\w-]+):$", CI.read_text(), re.M):
        if naam == "beeld-model":
            continue
        regels = "\n".join(job(naam))
        if naam == "api-analyse":
            assert regels.count("beeld_echt") == regels.count('-m "not beeld_echt"') == 1
            continue
        assert "beeld_echt" not in regels, naam
