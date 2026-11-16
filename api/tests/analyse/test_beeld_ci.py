"""De vorm van de job beeld-model in ci.yml (fase 6, X3a; K2-03, Keuze 83).

Leest ci.yml als tekst, zoals test_media_ci.py en test_zoek_ci.py:

- de job draait alleen als git diff een wijziging ziet in de omzetter, tokenizer.py, onnxw.py,
  models.lock of wat de job zelf meet (tools/beeld, de tests beeld_echt en hun lijsten), of als de
  PR een regel "beeld-model: ja" heeft; geen workflow_dispatch; de bron nooit in een cache, de
  foto's van beeld.tsv wel (sinds CI op f16ed5a: actions/cache/restore@v4 vóór BZ4 set en
  actions/cache/save@v4 direct erna, op de sha256 van beeld.tsv, alleen als die er is; bewaren
  ook als de job daarna rood eindigt, maar alleen na een goede set);
- de tekst van de PR komt via env binnen, nooit als expressie in een script;
- de omzetter start zoals de dienst analyse-omzetten (compose van X2a);
- BZ1, BZ2, BZ3, BZ4, BZ6 en BZ7 hebben elk een stap; de tests met marker beeld_echt draaien
  alleen hier, BZ6 onder taskset -c 0;
- het rapport van int8 (x3a.md, "CI op 3a9b07d") heeft een eigen stap na BZ6, die mag falen en
  geen poort is: continue-on-error, hooguit 15 min, || true, en Uitslag leest het niet;
- timeout-minutes van de job draagt het uiterste uit de opmerking erboven (de vragen aan de API
  zonder contact, het wachten na 429 en de rest) plus de timeout-minutes van die stap (de review,
  N1);
- het verslag via upload-artifact, en als laatste stap het blok `=== kort ===`;
- BEELD_CONTACT (de link naar het repository als contact in de User-Agent naar Wikimedia, met
  toestemming van de eigenaar, 2026-11-15) staat alleen op de twee stappen die Commons vragen.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from app.analyse import runtime
from tests.search.test_zoek_ci import _runs
from tests.test_media_ci import CI, job

# De opdrachtregel van de dienst analyse-omzetten: python -I -c <boot> /srv --bron /bron --uit /uit
BOOT = (
    "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"
)
API = Path(__file__).resolve().parents[2]
PADEN = [
    "app/analyse/worker/omzetten",
    "app/analyse/tokenizer.py",
    "app/analyse/onnxw.py",
    "app/analyse/models.lock",
    # wat de job zelf meet: een andere lijst of meting draait de job ook (CI-run op 26c49ca)
    "../tools/beeld",
    "tests_analyse/beeld_echt.py",
    "tests_analyse/test_beeld_tokenizer_echt.py",
    "tests_analyse/test_beeld_kwaliteit.py",
    "tests_analyse/test_beeld_echt_perf.py",
    "tests/analyse/sets/beeld_tokens.tsv",
    "tests/analyse/sets/beeld_vragen.tsv",
    "tests/analyse/sets/beeld.tsv",
    "tests_analyse/test_beeld_int8_lagen.py",
    # de lijst van wat een voorstel nooit neemt (x3a.md, "De set strenger"): een push die alleen
    # die lijst verandert, draait de job ook
    "tests/analyse/sets/beeld_niet.tsv",
    # wat BZ7 in test_beeld_tokenizer_echt.py meet: het geheugen van het kind (piek.laad, I2)
    "tests/beeld/piek.py",
]
# Paden die nog niet hoeven te bestaan. Leeg sinds beeld.tsv er staat (de set van de run op
# 502f4c3, Keuze 85): elk pad bestaat
LATER: tuple[str, ...] = ()


def _stappen() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["beeld-model"]["steps"]


def _blokken() -> list[str]:
    return [re.sub(r"\\\n\s*", "", tekst) for _, tekst in _runs(job("beeld-model"))]


TSV_HASH = "hashFiles('api/tests/analyse/sets/beeld.tsv')"
# alleen set/ (nooit bron/ of uit*/), op de sha256 van beeld.tsv, zonder restore-keys
SET_CACHE = {"path": "${{ runner.temp }}/beeld/set", "key": f"beeld-set-${{{{ {TSV_HASH} }}}}"}


def _cache_stappen() -> tuple[dict, dict]:
    """De twee stappen van actions/cache in beeld-model: ophalen en bewaren, in die volgorde."""
    cache = [s for s in _stappen() if "cache" in str(s.get("uses", ""))]
    assert [s["uses"] for s in cache] == ["actions/cache/restore@v4", "actions/cache/save@v4"]
    return cache[0], cache[1]


def test_alleen_push_en_pull_request_en_alleen_de_set_in_de_cache():
    doc = yaml.safe_load(CI.read_text())
    # PyYAML leest de sleutel on als True; dus ook geen workflow_dispatch
    assert set(doc[True]) == {"push", "pull_request"}
    regels = "\n".join(job("beeld-model"))
    # De bron komt nooit uit een cache (geen cache van uv, geen derde stap); alleen de foto's van
    # beeld.tsv (CI op f16ed5a: de robotregels van Wikimedia, zo weinig mogelijk downloads)
    assert len(re.findall(r"^\s+(?:- )?uses: actions/cache", regels, re.M)) == 2
    assert "cache:" not in regels and "enable-cache" not in regels
    stappen = _stappen()
    ophalen, _ = _cache_stappen()
    # Dezelfde action en versie als elders in ci.yml (actions/cache, v4). Hier ophalen en bewaren
    # apart (de review van de reparatie, R1); elke andere job houdt actions/cache@v4
    for naam, j in doc["jobs"].items():
        gebruikt = {s["uses"] for s in j.get("steps", []) if "actions/cache" in s.get("uses", "")}
        if naam == "beeld-model":
            assert gebruikt == {"actions/cache/restore@v4", "actions/cache/save@v4"}
        else:
            assert gebruikt <= {"actions/cache@v4"}, naam
    assert set(re.findall(r"uses: (actions/cache\S*)", CI.read_text())) == {
        "actions/cache@v4",
        "actions/cache/restore@v4",
        "actions/cache/save@v4",
    }
    assert ophalen["with"] == SET_CACHE
    # alleen als beeld.tsv er is (een voorstel krijgt geen cache), en als de job draait
    assert ophalen["if"] == (
        "${{ !cancelled() && steps.poort.outputs.draaien == 'ja' && " + TSV_HASH + " != '' }}"
    )
    # direct vóór de stap set, die de foto's nakijkt
    i = stappen.index(ophalen)
    assert ophalen["id"] == "set-cache"
    assert stappen[i + 1].get("id") == "set"
    assert "beeld_model.py set " in stappen[i + 1]["run"]


def test_de_foto_s_gaan_direct_na_set_in_de_cache_ook_als_de_job_rood_eindigt():
    """De review van de reparatie op f16ed5a (R1): actions/cache@v4 bewaart in zijn post-stap,
    en alleen na een groene job (post-if: success()). De job is rood zolang models.lock de regels
    van SigLIP mist (tot X3b), ook met beeld.tsv en een goede set; dan haalde elke strenge run de
    120 originelen opnieuw. Nu bewaart een eigen stap direct na set: alleen als set goed was
    (nooit een halve set, want een sleutel bewaart maar één keer) en de cache ze nog niet had."""
    stappen = _stappen()
    ophalen, bewaren = _cache_stappen()
    i = stappen.index(bewaren)
    assert stappen[i - 1].get("id") == "set" and stappen.index(ophalen) == i - 2
    assert bewaren["with"] == ophalen["with"] == SET_CACHE
    assert bewaren["if"] == (
        "${{ !cancelled() && steps.poort.outputs.draaien == 'ja' && steps.set.outcome == 'success'"
        " && steps.set-cache.outputs.cache-hit != 'true' && " + TSV_HASH + " != '' }}"
    )
    # geen post-stap die op een groene job wacht, in deze job
    assert not any(s.get("uses") == "actions/cache@v4" for s in stappen)


def test_de_poort_kijkt_naar_git_diff_en_de_tekst_van_de_pr():
    stappen = _stappen()
    assert stappen[0]["uses"] == "actions/checkout@v4" and stappen[0]["with"]["fetch-depth"] == 0
    poort = stappen[1]
    assert poort["id"] == "poort"
    assert poort["env"]["PR_TEKST"] == "${{ github.event.pull_request.body }}"
    script = poort["run"]
    assert "${{" not in script  # alles via env: geen expressie in het script
    m = re.findall(r"^paden\+?=\((.*)\)$", script, re.M)
    assert re.search(r"^paden=\(", script, re.M) and [p for g in m for p in g.split()] == PADEN
    # elk pad bestaat (een tikfout zou de job stil overslaan), behalve die van LATER
    for pad in PADEN:
        assert pad.endswith(LATER) or (API / pad).exists(), pad
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
    run = omzetten["run"]
    assert 'python -I -c "$BOOT" "$PWD" --bron "$WERK/bron" --uit "$WERK/uit$n"' in run
    # Twee keer precies zoals de dienst (zonder --fp32), dan een keer met --fp32 voor BZ4 (review
    # golf 5, uitrol F1 en F2)
    assert re.search(r"^for n in 1 2 fp32; do$", run, re.M)
    assert 'if [ "$n" = fp32 ]; then extra=(--fp32); fi' in run
    assert run.count("--fp32") == 1
    # Sinds de samenvoeging met X2a: dezelfde regel als app/analyse/runtime.py (de dienst
    # analyse-omzetten in compose.analyse.yaml start OMZET_BOOT, test_compose_analyse.py)
    assert runtime.OMZET_BOOT == BOOT


def test_bz2_begrenst_alleen_de_omzettingen_zoals_de_dienst():
    """vergelijk krijgt uit1 en uit2 (zonder --fp32: dezelfde sha256, maxrss ≤ 1,5 GB) en de
    omzetting met --fp32 apart (dezelfde vijf bestanden, maxrss zonder grens). BZ4 rekent met de
    fp32-grafen uit uitfp32, BZ3 en BZ6 met uit1 (review golf 5, uitrol F1 en F2)."""
    blokken = _blokken()
    regel = next(b for b in blokken if "beeld_model.py vergelijk " in b)
    assert (
        '--uit "$WERK/uit1" --uit "$WERK/uit2" '
        '--log "$WERK/omzetten1.log" --log "$WERK/omzetten2.log" '
        '--uit-fp32 "$WERK/uitfp32" --log-fp32 "$WERK/omzettenfp32.log" || [ $? -eq 3 ]'
    ) in regel
    model = {}
    for b in blokken:
        if m := re.search(r'BEELD_MODEL="([^"]+)".*tests_analyse/(\S+)', b, re.S):
            model[m.group(2)] = m.group(1)
    assert model == {
        "test_beeld_tokenizer_echt.py": "$WERK/uit1",
        "test_beeld_kwaliteit.py": "$WERK/uitfp32",
        "test_beeld_echt_perf.py": "$WERK/uit1",
        "test_beeld_int8_lagen.py": "$WERK/uitfp32",
    }


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
    # het rapport van int8: dezelfde omgeving als BZ4, plus de bron
    assert (
        'BEELD_BRON="$WERK/bron" BEELD_MODEL="$WERK/uitfp32" BEELD_SET="$WERK/set" '
        'BEELD_SET_TSV="$tsv" BEELD_RAPPORT="$WERK" uv run --no-sync pytest -q -m beeld_echt -rs '
        'tests_analyse/test_beeld_int8_lagen.py | tee "$WERK/bz4r.txt" || true'
    ) in blokken


RAPPORT = "BZ4 rapport int8 per laag en varianten (geen poort)"


def test_het_rapport_van_int8_mag_falen_en_is_geen_poort():
    """x3a.md, "CI op 3a9b07d": een eigen stap na BZ6 en vóór Uitslag, die de job niet rood kan
    maken (continue-on-error en || true), hooguit 15 min duurt, alleen draait als de omzetting en
    de set goed waren, en geen netwerk vraagt (geen BEELD_CONTACT). Uitslag leest alleen de
    voorstellen, en de samenvatting toont de laatste regel van bz4r.txt."""
    stappen = _stappen()
    namen = [s.get("name", "") for s in stappen]
    i = namen.index(RAPPORT)
    stap = stappen[i]
    assert namen[i - 1].startswith("BZ6 ") and namen[i + 1].startswith("Uitslag")
    assert stap["continue-on-error"] is True and stap["timeout-minutes"] == 15
    assert stap["if"] == (
        "${{ !cancelled() && steps.omzetten.outcome == 'success' "
        "&& steps.set.outcome == 'success' }}"
    )
    assert "env" not in stap and "BEELD_CONTACT" not in stap["run"]
    assert stap["run"].rstrip().endswith('| tee "$WERK/bz4r.txt" || true')
    assert "bz4r" not in stappen[i + 1]["run"]
    assert "for stap in bz3 bz4 bz4r bz6; do" in stappen[-1]["run"]


TOOL = API.parent / "tools" / "beeld" / "beeld_model.py"


def _constante(naam: str) -> float:
    """Een getal uit tools/beeld/beeld_model.py, zonder de tool te laden."""
    for knoop in ast.parse(TOOL.read_text(encoding="utf-8")).body:
        doelen = [getattr(d, "id", "") for d in getattr(knoop, "targets", [])]
        if isinstance(knoop, ast.Assign) and doelen == [naam]:
            return float(ast.literal_eval(knoop.value))
    raise AssertionError(f"{naam} staat niet in {TOOL.name}")


def test_de_tijd_van_de_job_draagt_ook_het_rapport_van_int8():
    """De review, N1: de opmerking boven timeout-minutes rekent het uiterste zonder contact (±V
    vragen aan de API, elk PAUZE_API_S, plus WACHT_TOTAAL_S en R min voor de rest); daar komt de
    timeout-minutes van elke stap bij die er een heeft (het rapport van int8). Het getal van de
    opmerking is dat van de job, en het budget aan vragen is niet kleiner dan op 3a9b07d."""
    tekst = re.sub(r"\n\s*#\s*", " ", CI.read_text())
    draagt = re.findall(r"(\d+) min draagt ±(\d+) vragen", tekst)
    rest = re.findall(r"(\d+) min voor de rest", tekst)
    assert len(draagt) == 1 and len(rest) == 1, (draagt, rest)
    (minuten, vragen), rest_min = (int(x) for x in draagt[0]), int(rest[0])
    j = yaml.safe_load(CI.read_text())["jobs"]["beeld-model"]
    assert minuten == j["timeout-minutes"]
    assert vragen >= 450
    stappen = sum(s.get("timeout-minutes", 0) for s in j["steps"])
    assert stappen >= 15  # het rapport van int8
    nodig = (
        vragen * _constante("PAUZE_API_S") / 60
        + _constante("WACHT_TOTAAL_S") / 60
        + rest_min
        + stappen
    )
    assert j["timeout-minutes"] >= nodig, (j["timeout-minutes"], nodig)


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


# Wat Wikimedia als contact krijgt: de pagina van dit repository, nooit een e-mail of een naam
REPO_LINK = "${{ github.server_url }}/${{ github.repository }}"
COMMONS_STAPPEN = [
    "BZ1 bereik per host",
    "BZ4 de 120 foto's (beeld.tsv, of een voorstel uit Commons)",
]


def test_alleen_bz1_en_bz4_set_geven_wikimedia_de_link_naar_het_repository():
    """De eigenaar vond op 2026-11-15 goed dat de CI de link naar zijn (openbare) repository als
    contact in de User-Agent naar Wikimedia stuurt, en alleen dat. Dus: BEELD_CONTACT precies
    REPO_LINK, alleen in de eigen env: van de twee stappen die Commons vragen (de stappen die
    beeld_model.py bereik en set draaien), nergens anders: niet op een job of workflow (dan staat
    het bovenaan elke stap), niet in een andere stap, niet uit een secret, niet in run of with."""
    gevonden = []
    for workflow in sorted(CI.parent.iterdir()):
        tekst = workflow.read_text(encoding="utf-8")
        assert "secrets.BEELD_CONTACT" not in tekst, workflow.name
        doc = yaml.safe_load(tekst)
        assert "BEELD_CONTACT" not in (doc.get("env") or {}), workflow.name
        for naam, job_ in doc["jobs"].items():
            assert "BEELD_CONTACT" not in (job_.get("env") or {}), naam
            for stap in job_.get("steps", []):
                assert "BEELD_CONTACT" not in str(stap.get("run", "")), stap.get("name")
                assert "BEELD_CONTACT" not in str(stap.get("with", "")), stap.get("name")
                env = stap.get("env") or {}
                if "BEELD_CONTACT" in env:
                    assert env["BEELD_CONTACT"] == REPO_LINK, stap["name"]
                    gevonden.append((workflow.name, naam, stap["name"]))
    assert gevonden == [("ci.yml", "beeld-model", naam) for naam in COMMONS_STAPPEN]
    commons = [
        s["name"]
        for s in _stappen()
        if re.search(r"beeld_model\.py (bereik|set) ", str(s.get("run", "")))
    ]
    assert commons == COMMONS_STAPPEN
