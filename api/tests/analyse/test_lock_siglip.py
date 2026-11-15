"""De regels van SigLIP 2 in models.lock, na golf 5 (X2a en X3a samen; fase 6, Keuze 58 en 73).

De job beeld-model (X3a) stelt tien regels voor: vijf bronnen van huggingface.co op een vaste
commit (url https://…) en vijf uitkomsten van de omzetter (url -). Hier maakt de tool zelf dat
voorstel (beeld_model.vergelijk, met de bronregels zoals beeld_model.bron ze schrijft), en dan
staat vast dat de code van X2a die regels leest zoals bedoeld, achter de echte models.lock:

- lock.parse leest ze, line() geeft precies de regel terug, en bash leest ze zoals install.sh
  (met model_name_ok uit install.sh zelf);
- de vingerafdruk van de gezichten (lock.digest over YuNet en SFace) blijft dezelfde, waar de
  regels van SigLIP ook staan;
- caps van pid 1 zegt per regel ok of ontbreekt, en de monitor zegt models_missing met precies
  de vijf uitkomsten, terwijl gezichten (ready met YuNet en SFace) door kunnen;
- Models.entry van het kind vindt een bestand van SigLIP op zijn naam.

De commit, de bytes en dus de sha256 hieronder zijn een voorbeeld: de echte zet de job in zijn
voorstel. Zolang X3b de omzetter niet in install.sh zet, maakt niemand de vijf uitkomsten: met
deze regels in models.lock zegt de stand dan models_missing (x3a.md, De samenvoeging).
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from app.analyse import client, lock, monitor, status, wire
from app.analyse.wire import Op
from app.analyse.worker import caps, omzetten
from app.analyse.worker.models import SFACE, YUNET, Models
from app.config import Settings

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "beeld" / "beeld_model.py"
INSTALL = ROOT / "infra" / "single" / "install.sh"
COMMIT = "0123456789abcdef0123456789abcdef01234567"  # een voorbeeld, geen echte commit
BRON = {n: f"bron {n}\n".encode() * 3 for n in omzetten.BRONNEN}
UIT = {n: f"uitkomst {n}\n".encode() * 2 for n in omzetten.UITVOER}
# lock.digest over de twee regels van gezichten (test_models_lock.py)
GEZICHTEN = "c34829c2524b2d619166d71855cfcb18d74cda83e0fa0441983f4b7a61a73d60"
SIGLIP_BRON = [f"{omzetten.MAP}/bron/{n}" for n in omzetten.BRONNEN]
SIGLIP_UIT = [f"{omzetten.MAP}/{n}" for n in omzetten.UITVOER]


def _laad():
    spec = importlib.util.spec_from_file_location("beeld_model_lock_siglip", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["beeld_model_lock_siglip"] = mod
    spec.loader.exec_module(mod)
    return mod


bm = _laad()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def voorstel(tmp_path) -> str:
    """voorstel-models.lock zoals de job beeld-model hem schrijft (BZ2): de bronregels van
    `bron` (met bron_url op COMMIT) en de uitkomst van twee gelijke omzettingen."""
    werk = bm.Werk(tmp_path / "werk")
    bronregels = [
        lock.ModelFile(_sha(b), len(b), f"{bm.BRON_MAP}/{n}", bm.bron_url(COMMIT, n)).line()
        for n, b in BRON.items()
    ]
    werk.voorstel_bron.write_text("".join(r + "\n" for r in bronregels))
    uit, logs = [], []
    for i in (1, 2):
        u = tmp_path / f"uit{i}"
        u.mkdir()
        for n, b in UIT.items():
            (u / n).write_bytes(b)
        log = tmp_path / f"omzetten{i}.log"
        log.write_text(f"omzetten: klaar in 1.{i} s, maxrss 39 MiB\n")
        uit.append(u)
        logs.append(log)
    leeg = tmp_path / "models.lock"
    leeg.write_text("")
    assert bm.vergelijk(werk, leeg, uit, logs) == bm.VOORSTEL
    return werk.voorstel_lock.read_text()


def test_het_voorstel_achter_de_echte_lock(voorstel):
    tekst = lock.LOCK_PATH.read_text() + voorstel
    regels = lock.parse(tekst)
    assert [r.name for r in regels] == [YUNET, SFACE, *SIGLIP_BRON, *SIGLIP_UIT]
    hf = f"https://huggingface.co/google/siglip2-base-patch32-256/resolve/{COMMIT}/"
    assert [r.url for r in regels[2:7]] == [hf + n for n in omzetten.BRONNEN]
    assert [(r.sha256, r.size) for r in regels[2:7]] == [(_sha(b), len(b)) for b in BRON.values()]
    assert [r.url for r in regels[7:]] == [None] * 5
    assert [(r.sha256, r.size) for r in regels[7:]] == [(_sha(b), len(b)) for b in UIT.values()]
    assert [r.line() for r in regels[2:]] == voorstel.splitlines()
    # de tool zelf leest ze weer als de tien regels van SigLIP
    bronnen, uitkomsten = bm.siglip_regels(regels)
    assert (len(bronnen), len(uitkomsten)) == (5, 5)


def test_de_vingerafdruk_van_gezichten_blijft(voorstel):
    echt = lock.LOCK_PATH.read_text()

    def gezichten(tekst: str) -> str:
        return lock.digest([r for r in lock.parse(tekst) if r.name in (YUNET, SFACE)])

    assert gezichten(echt) == GEZICHTEN
    assert gezichten(echt + voorstel) == GEZICHTEN
    assert gezichten(voorstel + echt) == GEZICHTEN  # ook als ze ervoor staan
    assert lock.digest(lock.parse(echt + voorstel)) != GEZICHTEN


def test_bash_leest_ze_zoals_install_sh(voorstel, tmp_path):
    """De lus van modellen_ophalen: read -r, de vormen van sha256 en bytes, model_name_ok uit
    install.sh zelf, url niet leeg."""
    pad = tmp_path / "models.lock"
    pad.write_text(lock.LOCK_PATH.read_text() + voorstel)
    script = r"""
eval "$(sed -n '/^model_name_ok() {/,/^}/p' "$1")"
declare -F model_name_ok >/dev/null || { echo "model_name_ok niet gevonden"; exit 1; }
while read -r sha size name url rest; do
  case "$sha" in "" | "#"*) continue ;; esac
  if ! [[ $sha =~ ^[0123456789abcdef]{64}$ && $size =~ ^[123456789][0123456789]{0,11}$ ]] ||
    ! model_name_ok "$name" || [ -z "$url" ] ||
    { [ -n "$rest" ] && [ "${rest#\#}" = "$rest" ]; }; then
    echo "FOUT $name"
    exit 1
  fi
  printf '%s %s %s %s\n' "$sha" "$size" "$name" "$url"
done < "$2"
"""
    out = subprocess.run(
        ["bash", "-c", script, "bash", str(INSTALL), str(pad)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.splitlines() == [r.line() for r in lock.parse(pad.read_text())]
    assert len(out.stdout.splitlines()) == 12


def _modellen(tmp_path: Path, voorstel: str) -> tuple[Path, Path]:
    """Een MODELLEN_ROOT met twee nepmodellen onder de namen van YuNet en SFace en de vijf
    bronnen van SigLIP, zonder de uitkomsten (die maakt de omzetter, X3b)."""
    root = tmp_path / "modellen"
    (root / omzetten.MAP / "bron").mkdir(parents=True)
    gezichten = {YUNET: b"yunet" * 50, SFACE: b"sface" * 70}
    for naam, b in gezichten.items():
        (root / naam).write_bytes(b)
    for naam, b in BRON.items():
        (root / omzetten.MAP / "bron" / naam).write_bytes(b)
    pad = tmp_path / "models.lock"
    pad.write_text(
        "".join(
            f"{_sha(b)} {len(b)} {n} https://example.invalid/{n}\n" for n, b in gezichten.items()
        )
        + voorstel
    )
    return root, pad


@pytest.mark.anyio
async def test_caps_en_de_monitor(voorstel, tmp_path, monkeypatch):
    root, pad = _modellen(tmp_path, voorstel)
    data = caps.gather(caps.ModelStates(root, pad), frozenset({Op.FACES}), tmp_path / "geen")
    # over de draad: twaalf namen met mappen passen in wire (MODEL_NAME, CAPS_MODELS_MAX)
    data = wire.CapsData.model_validate_json(data.model_dump_json())
    assert [(m.name, m.state) for m in data.models] == [
        (YUNET, "ok"),
        (SFACE, "ok"),
        *[(n, "ok") for n in SIGLIP_BRON],
        *[(n, "ontbreekt") for n in SIGLIP_UIT],
    ]

    async def nep_caps(settings):
        return data

    monkeypatch.setattr(client, "caps", nep_caps)
    settings = Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8, analyse_enabled=True
    )
    found = await monitor.check(settings)
    assert found.state == "models_missing"
    assert found.missing == tuple(SIGLIP_UIT)
    assert found.ready([YUNET, SFACE])  # gezichten kunnen door
    assert not found.ready([f"{omzetten.MAP}/{omzetten.BEELD}"])
    assert monitor.wait([YUNET, SFACE], found) is None
    assert monitor.wait([f"{omzetten.MAP}/{omzetten.TEKST}"], found).seconds == monitor.DEFER_MODEL
    regels = status.lines(found)
    assert (
        regels[0]
        == "Analyse: models_missing (een model ontbreekt of klopt niet: "
        + ", ".join(SIGLIP_UIT)
        + ")"
    )
    assert len([r for r in regels if r.startswith("  model ")]) == 12
    assert status.exit_code(found, 0) == 1 and status.exit_code(found, 20) == 0


def test_het_kind_vindt_een_bestand_van_siglip(voorstel, tmp_path):
    root, pad = _modellen(tmp_path, voorstel)
    modellen = Models(root, pad)
    naam = f"{omzetten.MAP}/{omzetten.BEELD}"
    assert modellen.entry(naam) == lock.ModelFile(
        _sha(UIT[omzetten.BEELD]), len(UIT[omzetten.BEELD]), naam, None
    )
    assert modellen.entry(YUNET).name == YUNET
