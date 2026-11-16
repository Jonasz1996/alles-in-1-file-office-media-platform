"""De regels van SigLIP 2 in models.lock, na golf 5 (X2a en X3a samen; fase 6, Keuze 58 en 73).

De job beeld-model (X3a) stelt tien regels voor: vijf bronnen van huggingface.co op een vaste
commit (url https://…) en vijf uitkomsten van de omzetter (url -). Hier maakt de tool zelf dat
voorstel (beeld_model.vergelijk, met de bronregels zoals beeld_model.bron ze schrijft), en dan
staat vast dat de code van X2a die regels leest zoals bedoeld, achter de regels van de
gezichten uit de echte models.lock (alleen die twee: na de pins staan de regels van SigLIP er
ook in, en een naam mag er maar één keer in staan):

- lock.parse leest ze, line() geeft precies de regel terug, en bash leest ze zoals install.sh
  (met model_name_ok uit install.sh zelf);
- de vingerafdruk van de gezichten (lock.digest over YuNet en SFace) blijft dezelfde, waar de
  regels van SigLIP ook staan;
- caps van pid 1 zegt per regel ok of ontbreekt, en de monitor zegt models_missing met precies
  de vijf uitkomsten, terwijl gezichten (ready met YuNet en SFace) door kunnen. Sinds X3b telt
  caps de bronnen niet mee (die haalt install.sh alleen om om te zetten, en het haalt ze daarna
  weg), en een uitkomst in een map pas als die map er is: zonder siglip2-b32-256/ (BEELD=uit)
  is de stand ok, met caps.beeld leeg;
- Models.entry van het kind vindt een bestand van SigLIP op zijn naam.

De commit, de bytes en dus de sha256 van het voorstel hieronder zijn een voorbeeld. Sinds X3b
staan de echte tien regels in models.lock (de pins van de job beeld-model, samen met de stap
BEELD van install.sh): test_de_pins_in_models_lock legt ze vast, en
test_caps_met_de_echte_lock laat zien dat gezichten er niets van merken zolang de map
siglip2-b32-256/ er niet is.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
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


def _echt() -> str:
    """De regels van YuNet en SFace uit de echte models.lock, in hun volgorde."""
    regels = [r for r in lock.load() if r.name in (YUNET, SFACE)]
    assert [r.name for r in regels] == [YUNET, SFACE]
    return "".join(r.line() + "\n" for r in regels)


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
    tekst = _echt() + voorstel
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
    echt = _echt()

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
    pad.write_text(_echt() + voorstel)
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
    # over de draad: zeven namen met mappen passen in wire (MODEL_NAME, CAPS_MODELS_MAX)
    data = wire.CapsData.model_validate_json(data.model_dump_json())
    # de bronnen tellen niet mee (X3b); de map siglip2-b32-256 is er, dus de uitkomsten wel
    assert [(m.name, m.state) for m in data.models] == [
        (YUNET, "ok"),
        (SFACE, "ok"),
        *[(n, "ontbreekt") for n in SIGLIP_UIT],
    ]
    assert data.beeld is None

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
    assert len([r for r in regels if r.startswith("  model ")]) == 7
    assert status.exit_code(found, 0) == 1 and status.exit_code(found, 20) == 0


@pytest.mark.anyio
async def test_zonder_de_map_van_siglip_is_de_stand_ok(voorstel, tmp_path, monkeypatch):
    """BEELD=uit of nog niet omgezet: install.sh zet siglip2-b32-256/ pas in één keer neer als
    alles klopt (Keuze 84). Zonder die map telt caps alleen de gezichten; beeld is niet klaar
    (caps.beeld leeg), gezichten merken er niets van."""
    root, pad = _modellen(tmp_path, voorstel)
    for naam in BRON:
        (root / omzetten.MAP / "bron" / naam).unlink()
    (root / omzetten.MAP / "bron").rmdir()
    (root / omzetten.MAP).rmdir()
    data = caps.gather(caps.ModelStates(root, pad), frozenset({Op.FACES}), tmp_path / "geen")
    data = wire.CapsData.model_validate_json(data.model_dump_json())
    assert [(m.name, m.state) for m in data.models] == [(YUNET, "ok"), (SFACE, "ok")]
    assert data.beeld is None

    async def nep_caps(settings):
        return data

    monkeypatch.setattr(client, "caps", nep_caps)
    settings = Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8, analyse_enabled=True
    )
    found = await monitor.check(settings)
    assert found.state == "ok" and found.missing == ()
    assert found.ready([YUNET, SFACE])
    # een symlink op de plaats van de map telt niet als map (lstat)
    elders = tmp_path / "elders"
    elders.mkdir()
    (root / omzetten.MAP).symlink_to(elders)
    data = caps.gather(caps.ModelStates(root, pad), frozenset({Op.FACES}), tmp_path / "geen")
    assert [m.name for m in data.models] == [YUNET, SFACE]


def test_het_kind_vindt_een_bestand_van_siglip(voorstel, tmp_path):
    root, pad = _modellen(tmp_path, voorstel)
    modellen = Models(root, pad)
    naam = f"{omzetten.MAP}/{omzetten.BEELD}"
    assert modellen.entry(naam) == lock.ModelFile(
        _sha(UIT[omzetten.BEELD]), len(UIT[omzetten.BEELD]), naam, None
    )
    assert modellen.entry(YUNET).name == YUNET


# --- de echte pins (X3b) -------------------------------------------------------------------------

PIN_COMMIT = "94dffa8cb1179de3e03f091dbc3917e5d5a9ae84"
PINS = [
    (
        "7d241bb3becad218f211f480487f491df4f8c0a472ecf7afdec5615815a301f1",
        1507473288,
        "bron/model.safetensors",
    ),
    ("52deee7a2d6fb2e73c45720d9c4056f0e4dc582ef84524aac229c6d5aa348dee", 298, "bron/config.json"),
    (
        "d14ba2ee3fd816f3de8abaddc31953565128eaf37c73ad4bed32101a98465aff",
        394,
        "bron/preprocessor_config.json",
    ),
    (
        "cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322",
        34363039,
        "bron/tokenizer.json",
    ),
    (
        "14afe629fe4959b9e0d51e1852b8d9f7ad074f90a1a7125a4fcdd17f06e78fc8",
        47164,
        "bron/tokenizer_config.json",
    ),
    (
        "ddc76fc08a1c30bd0d0b8fa997f5f8fa44c0cbe25b9f04d81859fa41cb877a5b",
        102609072,
        "beeld.int8.onnx",
    ),
    (
        "258337dbc659cfebc72850ca0ce8fdec4c50c726da35790893569eb8a10bfd1a",
        284248004,
        "tekst.int8.onnx",
    ),
    ("1516dde1739c181c24fcaef51d49e49ae26bea112490ff57799f945e7666b8ec", 12414446, "tokenizer.bin"),
    ("dfb3d153f941cd0ea549922f34c584f81730a43708539fff879e6d85a6d49af1", 2376, "meta.json"),
    (
        "0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594",
        10174,
        "siglip2-Apache-2.0.txt",
    ),
]

# De VERSIE van de omzetter (bouw.py) in de run waaruit de drie uitkomsten hierboven komen
# (beeld.int8.onnx, tekst.int8.onnx en meta.json; BZ2 op 669f03c, x3a.md "CI op 669f03c").
PINS_OMZETTER = 3
BOUW = ROOT / "api" / "app" / "analyse" / "worker" / "omzetten" / "bouw.py"


def test_de_uitkomst_in_models_lock_is_van_de_versie_van_de_omzetter():
    """meta.json noemt "omzetter": VERSIE, en een andere VERSIE geeft andere int8-bytes. Gaat
    VERSIE in bouw.py omhoog, dan kloppen de drie uitkomsten in models.lock niet meer: beeld-model
    wordt rood, en install.sh met BEELD=aan haalt elke run 1,5 GB, zet om en gooit de uitkomst
    weg. Dan horen er de regels van een run met die VERSIE in (runbook analyse, stap 5), en hier
    PINS_OMZETTER mee. Een samenvoeging hield de regels van VERSIE 1 stil vast terwijl de omzetter
    al op 3 stond (review golf 6, G6-2). bouw.py importeert numpy, dus de versie uit de bron."""
    versie = re.findall(r"^VERSIE = (\d+)$", BOUW.read_text(encoding="utf-8"), re.MULTILINE)
    assert versie == [str(PINS_OMZETTER)]


def test_de_pins_in_models_lock():
    """De tien regels van SigLIP 2, precies: na YuNet en SFace, de vijf bronnen op één commit van
    huggingface.co (zoals beeld_model.bron_url ze maakt) en de vijf uitkomsten (url -), in de
    volgorde van omzetten.BRONNEN en UITVOER. De licentie in de uitkomst is byte voor byte die
    van licenties/ (dezelfde sha256)."""
    regels = lock.load()
    assert [r.name for r in regels[:2]] == [YUNET, SFACE]
    assert [(r.sha256, r.size, r.name) for r in regels[2:]] == [
        (sha, size, f"{omzetten.MAP}/{naam}") for sha, size, naam in PINS
    ]
    assert [r.name for r in regels[2:]] == SIGLIP_BRON + SIGLIP_UIT
    assert [r.url for r in regels[2:7]] == [bm.bron_url(PIN_COMMIT, n) for n in omzetten.BRONNEN]
    assert [r.url for r in regels[7:]] == [None] * 5
    bronnen, uitkomsten = bm.siglip_regels(regels)
    assert [r.name for r in bronnen] == SIGLIP_BRON and [r.name for r in uitkomsten] == SIGLIP_UIT
    licentie = (lock.LICENSES / omzetten.LICENTIE).read_bytes()
    assert (_sha(licentie), len(licentie)) == (regels[-1].sha256, regels[-1].size)
    # de vingerafdruk van gezichten blijft die van de twee regels (X2)
    assert lock.digest(regels[:2]) == GEZICHTEN


@pytest.mark.anyio
async def test_caps_met_de_echte_lock(tmp_path, monkeypatch):
    """De echte models.lock: zonder siglip2-b32-256/ (BEELD=uit, of nog niet omgezet) telt caps
    alleen YuNet en SFace en is de stand ok; met een map waar een uitkomst ontbreekt zegt de
    stand models_missing met precies die namen, en kunnen gezichten door. De bronnen telt caps
    nooit (install.sh wist ze na het omzetten)."""
    root = tmp_path / "modellen"
    root.mkdir()
    for naam in (YUNET, SFACE):
        (root / naam).write_bytes(b"nep")
    staat = caps.ModelStates(root)
    data = caps.gather(staat, frozenset({Op.FACES}), tmp_path / "geen")
    assert [m.name for m in data.models] == [YUNET, SFACE]  # anders: de bytes zijn nep
    assert data.beeld is None

    async def nep_caps(settings):
        return data

    monkeypatch.setattr(client, "caps", nep_caps)
    settings = Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8, analyse_enabled=True
    )
    # met nepbytes zijn ze "anders"; zet ze op ok zoals een echte installatie
    data = data.model_copy(
        update={"models": [wire.ModelCaps(name=m.name, state="ok") for m in data.models]}
    )
    found = await monitor.check(settings)
    assert found.state == "ok" and found.missing == ()
    (root / omzetten.MAP).mkdir()
    (root / omzetten.MAP / omzetten.META).write_bytes(b"{}")
    data = caps.gather(staat, frozenset({Op.FACES}), tmp_path / "geen")
    assert [m.name for m in data.models] == [YUNET, SFACE, *SIGLIP_UIT]
    assert {m.name: m.state for m in data.models if m.name in SIGLIP_UIT} == {
        **{n: "ontbreekt" for n in SIGLIP_UIT},
        f"{omzetten.MAP}/{omzetten.META}": "anders",  # 2 bytes, geen 2135
    }
    data = data.model_copy(
        update={
            "models": [
                m if m.name in SIGLIP_UIT else wire.ModelCaps(name=m.name, state="ok")
                for m in data.models
            ]
        }
    )
    found = await monitor.check(settings)
    assert found.state == "models_missing" and found.missing == tuple(SIGLIP_UIT)
    assert found.ready([YUNET, SFACE])
    assert not found.ready([f"{omzetten.MAP}/{omzetten.BEELD}"])
