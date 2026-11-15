"""tools/beeld/beeld_model.py tegen een eigen nep-server (fase 6, X3a; de job beeld-model).

De echte bronnen (huggingface.co, Wikimedia Commons) zijn vanuit de sandbox dicht; de job draait
alleen in CI. Hier staat vast wat de stappen doen, met een HTTP-server op 127.0.0.1 die doet
alsof hij Hugging Face, de API van Commons en upload.wikimedia.org is:

- bron zonder regels in models.lock: de commit via de API, de vijf bestanden op die commit, een
  voorstel en code 3; met regels: streng (een andere sha256 of grootte is rood, en er blijft
  niets half staan);
- vergelijk: twee omzettingen met dezelfde sha256, maxrss ≤ 1,5 GB, het voorstel voor
  models.lock (dat lock.py zelf weer leest), of streng tegen de regels; de omzetting met --fp32
  (voor BZ4) zonder grens op maxrss, maar met dezelfde vijf bestanden;
- set: 120 foto's volgens Keuze 83 uit de gestructureerde gegevens, zonder mens, alleen CC0 of
  publiek domein, alleen JPEG; beeld.tsv streng nagekeken;
- elke vraag heeft een algemene User-Agent, zonder persoon, e-mail of repository.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import random
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.analyse import lock as app_lock
from app.analyse.worker import omzetten

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tools" / "beeld" / "beeld_model.py"


def _laad():
    spec = importlib.util.spec_from_file_location("beeld_model_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["beeld_model_test"] = mod
    spec.loader.exec_module(mod)
    return mod


bm = _laad()


class Nep:
    """Een server met vaste antwoorden per pad (zonder query), plus een eigen handler voor de API
    van Commons. vragen: (pad met query, User-Agent) van elke vraag."""

    def __init__(self) -> None:
        # (status, body) of (status, body, extra kopregels)
        self.paden: dict[str, list[tuple]] = {}
        self.vragen: list[tuple[str, str]] = []
        self.commons = None
        nep = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                nep.vragen.append((self.path, self.headers.get("User-Agent", "")))
                pad = urllib.parse.urlsplit(self.path).path
                extra: list[tuple[str, str]] = []
                if pad == "/w/api.php" and nep.commons is not None:
                    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                    status, body = 200, json.dumps(nep.commons.antwoord(q)).encode()
                else:
                    rij = nep.paden.get(pad)
                    if not rij:
                        status, body = 404, b"niet hier"
                    else:
                        status, body, *kop = rij[0] if len(rij) == 1 else rij.pop(0)
                        for naam, waarde in kop[0].items() if kop else ():
                            extra.append((naam, waarde))
                self.send_response(status)
                for naam, waarde in extra:
                    self.send_header(naam, waarde)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def zet(self, pad: str, body: bytes, status: int = 200) -> None:
        self.paden[pad] = [(status, body)]

    def sluit(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def nep(monkeypatch):
    n = Nep()
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(bm, "HF", n.url)
    monkeypatch.setattr(bm, "COMMONS", f"{n.url}/w/api.php")
    monkeypatch.setattr(bm, "UPLOAD", f"{n.url}/upload/")
    monkeypatch.setattr(bm, "NASA", f"{n.url}/nasa/")
    monkeypatch.setattr(bm.time, "sleep", lambda s: None)
    yield n
    n.sluit()


@pytest.fixture
def werk(tmp_path):
    return bm.Werk(tmp_path / "werk")


def _kort(werk) -> list[str]:
    return werk.kort.read_text().splitlines()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _ua_algemeen(nep: Nep) -> None:
    assert nep.vragen
    for _, ua in nep.vragen:
        assert ua == bm.UA
    assert "@" not in bm.UA and "github" not in bm.UA.lower() and "/" in bm.UA


# --- de tool zelf ------------------------------------------------------------------------------


def test_de_tool_gebruikt_alleen_de_standaardbibliotheek():
    boom = ast.parse(TOOL.read_text())
    namen = set()
    for knoop in ast.walk(boom):
        if isinstance(knoop, ast.Import):
            namen |= {a.name.split(".")[0] for a in knoop.names}
        elif isinstance(knoop, ast.ImportFrom) and knoop.level == 0:
            namen.add(knoop.module.split(".")[0])
    assert namen <= set(sys.stdlib_module_names) | {"__future__"}, namen
    r = subprocess.run(
        [sys.executable, "-I", str(TOOL), "--help"], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0 and "bereik" in r.stdout


def test_de_namen_komen_uit_de_omzetter():
    assert bm.omzetten.BRONNEN == omzetten.BRONNEN
    assert bm.omzetten.UITVOER == omzetten.UITVOER
    assert bm.BRON_MAP == f"{omzetten.MAP}/bron"
    assert sorted(bm.MAX_BRON) == sorted(omzetten.BRONNEN)


def test_de_categorieen_zijn_die_van_de_vragen():
    cats = set()
    for regel in bm.VRAGEN.read_text().splitlines():
        v = regel.split("\t")
        if v[0] in ("nl", "combinatie", "en"):
            cats |= set(v[2].split(","))
    assert cats == set(bm.CATEGORIEEN)
    assert bm.combinaties() == [
        ("auto", "sneeuw"),
        ("hond", "strand"),
        ("boot", "zonsondergang"),
        ("fiets", "stad"),
        ("kat", "sneeuw"),
        ("bos", "berg"),
    ]
    alle = list(bm.CATEGORIEEN.values()) + list(bm.ANDERE.values()) + [bm.MENS]
    assert len(set(alle)) == len(alle) == 25


# --- BZ1 ---------------------------------------------------------------------------------------


def test_bereik_meldt_elke_host(nep, werk, monkeypatch):
    nep.zet(f"/api/models/{bm.REPO}", b"{}")
    monkeypatch.setattr(bm, "COMMONS", "http://127.0.0.1:1/w/api.php")  # dicht
    assert bm.bereik(werk) == 0
    assert _kort(werk) == [
        "BZ1 huggingface.co: bereikbaar (HTTP 200)",
        "BZ1 commons.wikimedia.org: niet bereikbaar (ConnectionRefusedError)",
        "BZ1 upload.wikimedia.org: bereikbaar (HTTP 404)",
        "BZ1 images-assets.nasa.gov: bereikbaar (HTTP 404)",
    ]
    _ua_algemeen(nep)


# --- de bron -----------------------------------------------------------------------------------

COMMIT = "0123456789abcdef0123456789abcdef01234567"
BESTANDEN = {n: f"inhoud van {n}\n".encode() * 3 for n in omzetten.BRONNEN}


def _hf(nep: Nep, *, sha=COMMIT, siblings=None) -> None:
    sib = siblings if siblings is not None else list(BESTANDEN)
    doc = {"sha": sha, "siblings": [{"rfilename": n} for n in sib]}
    nep.zet(f"/api/models/{bm.REPO}/revision/main", json.dumps(doc).encode())
    for n, b in BESTANDEN.items():
        nep.zet(f"/{bm.REPO}/resolve/{sha}/{n}", b)


def test_bron_zonder_regels_stelt_ze_voor(nep, werk, tmp_path):
    _hf(nep)
    leeg = tmp_path / "models.lock"
    leeg.write_text("# geen regels van SigLIP\n")
    assert bm.bron(werk, leeg) == bm.VOORSTEL
    for n, b in BESTANDEN.items():
        assert (werk.bron / n).read_bytes() == b
    # de nep-server is http; in CI is het https://huggingface.co, en dat leest lock.py
    regels = werk.voorstel_bron.read_text().splitlines()
    assert regels == [
        f"{_sha(BESTANDEN[n])} {len(BESTANDEN[n])} siglip2-b32-256/bron/{n} "
        f"{nep.url}/{bm.REPO}/resolve/{COMMIT}/{n}"
        for n in omzetten.BRONNEN
    ]
    echt = [r.replace(nep.url, "https://huggingface.co") for r in regels]
    assert [m.url for m in app_lock.parse("\n".join(echt))] == [
        f"https://huggingface.co/{bm.REPO}/resolve/{COMMIT}/{n}" for n in omzetten.BRONNEN
    ]
    assert _kort(werk) == [
        f"BZ2 bron: {bm.REPO} op commit {COMMIT}; nog geen regels in models.lock (voorstel)"
    ]
    assert sorted(p.name for p in werk.bron.iterdir()) == sorted(BESTANDEN)
    _ua_algemeen(nep)


@pytest.mark.parametrize(
    "geval, fout",
    [
        ("geen commit", "geen commit in het antwoord"),
        ("korte commit", "geen commit in het antwoord"),
        ("bestand ontbreekt", "tokenizer.json ontbreekt"),
    ],
)
def test_bron_met_een_commit_die_niet_klopt(nep, werk, tmp_path, geval, fout):
    if geval == "geen commit":
        nep.zet(f"/api/models/{bm.REPO}/revision/main", b'{"siblings": []}')
    elif geval == "korte commit":
        _hf(nep, sha="abc123")
    else:
        _hf(nep, siblings=[n for n in BESTANDEN if n != "tokenizer.json"])
    leeg = tmp_path / "models.lock"
    leeg.write_text("")
    with pytest.raises(bm.Fout, match=fout):
        bm.bron(werk, leeg)
    assert not werk.voorstel_bron.exists()


def _regels(nep: Nep, *, anders: str | None = None) -> list:
    """De tien regels van SigLIP zoals ze in models.lock zouden staan, met http-urls naar nep."""
    uit = []
    for n, b in BESTANDEN.items():
        sha = "0" * 64 if n == anders else _sha(b)
        url = f"{nep.url}/{bm.REPO}/resolve/{COMMIT}/{n}"
        uit.append(app_lock.ModelFile(sha, len(b), f"siglip2-b32-256/bron/{n}", url))
    for n in omzetten.UITVOER:
        uit.append(app_lock.ModelFile(_sha(n.encode()), len(n), f"siglip2-b32-256/{n}", None))
    return uit


def test_bron_met_regels_is_streng(nep, werk, monkeypatch):
    _hf(nep)
    monkeypatch.setattr(bm.lock, "load", lambda p: _regels(nep))
    assert bm.bron(werk, Path("models.lock")) == 0
    assert not werk.voorstel_bron.exists()
    assert _kort(werk)[0].startswith("BZ2 bron: de vijf bestanden kloppen met models.lock")
    assert f"/api/models/{bm.REPO}/revision/main" not in [p for p, _ in nep.vragen]


def test_bron_met_een_andere_sha256_is_rood_en_laat_niets_achter(nep, werk, monkeypatch):
    _hf(nep)
    monkeypatch.setattr(bm.lock, "load", lambda p: _regels(nep, anders="config.json"))
    with pytest.raises(bm.Fout, match="config.json: sha256 .*verwacht 0000"):
        bm.bron(werk, Path("models.lock"))
    assert not (werk.bron / "config.json").exists()
    assert not list(werk.bron.glob("*.part"))


def test_een_deel_van_de_regels_is_een_fout(nep):
    regels = _regels(nep)
    with pytest.raises(bm.Fout, match="maar een deel"):
        bm.siglip_regels(regels[:-1])
    with pytest.raises(bm.Fout, match="maar een deel"):
        bm.siglip_regels(regels[1:])
    zonder_url = [*regels]
    zonder_url[0] = app_lock.ModelFile(regels[0].sha256, 1, regels[0].name, None)
    with pytest.raises(bm.Fout, match="heeft geen url"):
        bm.siglip_regels(zonder_url)
    met_url = [*regels]
    met_url[-1] = app_lock.ModelFile(regels[-1].sha256, 1, regels[-1].name, "https://x/y")
    with pytest.raises(bm.Fout, match="maakt de host zelf"):
        bm.siglip_regels(met_url)
    assert bm.siglip_regels(app_lock.load()) == ([], [])  # nu: alleen X2


# --- haal --------------------------------------------------------------------------------------


def test_haal_controleert_grootte_en_sha256(nep, tmp_path):
    nep.zet("/f", b"abcdef")
    doel = tmp_path / "f"
    assert bm.haal(f"{nep.url}/f", doel, max_bytes=6) == (_sha(b"abcdef"), 6)
    doel.unlink()
    with pytest.raises(bm.Fout, match="groter dan 5 bytes"):
        bm.haal(f"{nep.url}/f", doel, max_bytes=5)
    with pytest.raises(bm.Fout, match="7"):
        bm.haal(f"{nep.url}/f", doel, max_bytes=9, grootte=7)
    with pytest.raises(bm.Fout, match="groter dan 4"):
        bm.haal(f"{nep.url}/f", doel, max_bytes=9, grootte=4)
    with pytest.raises(bm.Fout, match="sha256"):
        bm.haal(f"{nep.url}/f", doel, max_bytes=9, grootte=6, sha256="1" * 64)
    with pytest.raises(bm.Fout, match="HTTP 404"):
        bm.haal(f"{nep.url}/weg", doel, max_bytes=9)
    nep.zet("/leeg", b"")
    with pytest.raises(bm.Fout, match="leeg"):
        bm.haal(f"{nep.url}/leeg", doel, max_bytes=9)
    assert list(tmp_path.iterdir()) == []


def test_haal_probeert_opnieuw_bij_503_maar_niet_bij_404(nep, tmp_path):
    nep.paden["/druk"] = [(503, b""), (429, b""), (200, b"eindelijk")]
    assert bm.haal(f"{nep.url}/druk", tmp_path / "d", max_bytes=99)[1] == 9
    assert [p for p, _ in nep.vragen].count("/druk") == 3
    nep.paden["/kapot"] = [(500, b"")] * 9
    with pytest.raises(bm.Fout, match="HTTP 500"):
        bm.haal(f"{nep.url}/kapot", tmp_path / "k", max_bytes=99)
    assert [p for p, _ in nep.vragen].count("/kapot") == 6
    with pytest.raises(bm.Fout, match="HTTP 404"):
        bm.haal(f"{nep.url}/niet", tmp_path / "n", max_bytes=99)
    assert [p for p, _ in nep.vragen].count("/niet") == 1


def test_na_een_429_wacht_hij_zo_lang_als_retry_after_zegt(nep, tmp_path, monkeypatch):
    """CI-run op 26c49ca: Commons gaf na ±10 s HTTP 429 en drie snelle pogingen hielpen niet.
    Retry-After (seconden of een HTTP-datum) telt als hij langer is dan de eigen wachttijd, met
    MAX_WACHT_S als grens."""
    slapen: list[float] = []
    monkeypatch.setattr(bm.time, "sleep", slapen.append)
    nep.paden["/vol"] = [
        (429, b"", {"Retry-After": "30"}),
        (429, b"", {"Retry-After": "1"}),
        (503, b"", {"Retry-After": "9999"}),
        (429, b"", {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),  # voorbij: telt als 0
        (200, b"klaar"),
    ]
    assert bm.haal(f"{nep.url}/vol", tmp_path / "v", max_bytes=99)[1] == 5
    assert slapen == [30.0, 4.0, bm.MAX_WACHT_S, 16.0]
    assert bm.MAX_WACHT_S <= 300
    nep.paden["/dicht"] = [(429, b"")] * 9
    with pytest.raises(bm.Fout, match="HTTP 429"):
        bm.haal(f"{nep.url}/dicht", tmp_path / "d", max_bytes=99)
    assert [p for p, _ in nep.vragen].count("/dicht") == 6


def test_tussen_twee_vragen_aan_commons_zit_een_pauze(nep, monkeypatch):
    """Naar de API en upload van Commons nooit twee vragen binnen PAUZE_S; andere hosts (Hugging
    Face) wachten niet."""
    klok = [1000.0]
    slapen: list[float] = []

    def slaap(s: float) -> None:
        slapen.append(s)
        klok[0] += s

    monkeypatch.setattr(bm.time, "monotonic", lambda: klok[0])
    monkeypatch.setattr(bm.time, "sleep", slaap)
    monkeypatch.setattr(bm, "_vorige", {})
    monkeypatch.setattr(bm, "COMMONS", "https://commons.example/w/api.php")
    monkeypatch.setattr(bm, "UPLOAD", "https://upload.example/")
    bm._pauze("https://commons.example/w/api.php?a=1")
    bm._pauze("https://commons.example/w/api.php?a=2")
    klok[0] += 0.25
    bm._pauze("https://commons.example/w/api.php?a=3")
    bm._pauze("https://upload.example/x.jpg")  # een andere host: eigen beurt
    bm._pauze("https://huggingface.co/x")
    assert slapen == [bm.PAUZE_S, bm.PAUZE_S - 0.25]
    assert bm.PAUZE_S >= 1.0


# --- vergelijk ---------------------------------------------------------------------------------


def _twee_omzettingen(tmp_path: Path, *, anders=None, rss=(812, 790)) -> tuple[list, list]:
    uit, logs = [], []
    for i in (1, 2):
        u = tmp_path / f"uit{i}"
        u.mkdir()
        for n in omzetten.UITVOER:
            data = n.encode() + (b"!" if (i == 2 and n == anders) else b"")
            (u / n).write_bytes(data)
        (u / omzetten.BEELD_FP32).write_bytes(b"alleen in een van de twee" * i)
        log = tmp_path / f"omzetten{i}.txt"
        log.write_text(
            "omzetten: bronnen gelezen (0.1 s)\n"
            f"omzetten: klaar in {100 + i}.5 s, maxrss {rss[i - 1]} MiB\n"
        )
        uit.append(u)
        logs.append(log)
    return uit, logs


def test_vergelijk_zonder_regels_stelt_models_lock_voor(werk, tmp_path):
    uit, logs = _twee_omzettingen(tmp_path)
    werk.voorstel_bron.write_text("a" * 64 + " 5 siglip2-b32-256/bron/config.json https://h/c\n")
    leeg = tmp_path / "models.lock"
    leeg.write_text("")
    assert bm.vergelijk(werk, leeg, uit, logs) == bm.VOORSTEL
    regels = app_lock.parse(werk.voorstel_lock.read_text())
    assert [r.name for r in regels] == ["siglip2-b32-256/bron/config.json"] + [
        f"siglip2-b32-256/{n}" for n in omzetten.UITVOER
    ]
    assert regels[1:] == [
        app_lock.ModelFile(_sha(n.encode()), len(n), f"siglip2-b32-256/{n}", None)
        for n in omzetten.UITVOER
    ]
    kort = _kort(werk)
    assert kort[:3] == [
        "BZ2 omzetting 1: 101.5 s, maxrss 812 MiB (grens 1536)",
        "BZ2 omzetting 2: 102.5 s, maxrss 790 MiB (grens 1536)",
        "BZ2 twee keer omzetten: dezelfde sha256 voor alle 5 bestanden",
    ]
    assert kort[3] == "BZ2 voorstel voor models.lock (sha256 bytes naam url):"
    assert kort[4:] == [f"  {r.line()}" for r in regels]


def test_vergelijk_twee_verschillende_omzettingen_is_rood(werk, tmp_path):
    uit, logs = _twee_omzettingen(tmp_path, anders=omzetten.TEKST)
    leeg = tmp_path / "models.lock"
    leeg.write_text("")
    assert bm.vergelijk(werk, leeg, uit, logs) == 1
    assert any(r.startswith("BZ2 FOUT tekst.int8.onnx: twee omzettingen") for r in _kort(werk))
    assert not werk.voorstel_lock.exists()


def test_vergelijk_te_veel_geheugen_is_rood(werk, tmp_path):
    uit, logs = _twee_omzettingen(tmp_path, rss=(1536, 1537))
    leeg = tmp_path / "models.lock"
    leeg.write_text("")
    assert bm.vergelijk(werk, leeg, uit, logs) == 1
    assert "BZ2 FOUT omzetting 2: maxrss 1537 MiB > 1536" in _kort(werk)


def test_vergelijk_zonder_klaar_regel_is_een_fout(werk, tmp_path):
    uit, logs = _twee_omzettingen(tmp_path)
    logs[1].write_text("omzetten: model.safetensors: tensor 'x' ontbreekt\n")
    with pytest.raises(bm.Fout, match="meldde niet dat hij klaar was"):
        bm.vergelijk(werk, tmp_path / "x", uit, logs)
    (uit[0] / omzetten.META).unlink()
    logs[1].write_text("omzetten: klaar in 1.0 s, maxrss 9 MiB\n")
    with pytest.raises(bm.Fout, match="meta.json: ontbreekt"):
        bm.vergelijk(werk, tmp_path / "x", uit, logs)


def test_vergelijk_met_regels_is_streng(nep, werk, tmp_path, monkeypatch):
    uit, logs = _twee_omzettingen(tmp_path)
    monkeypatch.setattr(bm.lock, "load", lambda p: _regels(nep))
    assert bm.vergelijk(werk, Path("models.lock"), uit, logs) == 0
    assert "BZ2 models.lock: de uitkomst klopt" in _kort(werk)
    for n in (omzetten.META, omzetten.BEELD):
        (uit[0] / n).write_bytes(b"anders")
        (uit[1] / n).write_bytes(b"anders")
    assert bm.vergelijk(werk, Path("models.lock"), uit, logs) == 1
    fouten = [r for r in _kort(werk) if r.startswith("BZ2 FOUT")]
    assert [f.split(":")[0] for f in fouten] == [
        "BZ2 FOUT siglip2-b32-256/beeld.int8.onnx",
        "BZ2 FOUT siglip2-b32-256/meta.json",
    ]


def _met_fp32(tmp_path: Path, uit1: Path, *, anders=None, rss=1582) -> tuple[Path, Path]:
    """De derde omzetting, met --fp32 (alleen voor BZ4): de vijf bestanden van uit1, en de twee
    fp32-grafen erbij. 1582 MiB is wat de omzetting met --fp32 op ware grootte mat."""
    u = tmp_path / "uitfp32"
    u.mkdir()
    for n in omzetten.UITVOER:
        (u / n).write_bytes((uit1 / n).read_bytes() + (b"!" if n == anders else b""))
    for n in (omzetten.BEELD_FP32, omzetten.TEKST_FP32):
        (u / n).write_bytes(b"fp32")
    log = tmp_path / "omzettenfp32.log"
    log.write_text(f"omzetten: klaar in 18.7 s, maxrss {rss} MiB\n")
    return u, log


def test_vergelijk_met_fp32_heeft_geen_grens_op_maxrss(werk, tmp_path):
    """Review golf 5, uitrol F2: de grens van 1,5 GB geldt voor de omzetting zoals de dienst; die
    met --fp32 (alleen voor BZ4) staat in het log, maar maakt BZ2 niet rood."""
    uit, logs = _twee_omzettingen(tmp_path)
    fp32, log32 = _met_fp32(tmp_path, uit[0])
    leeg = tmp_path / "models.lock"
    leeg.write_text("")
    argv = ["vergelijk", "--werk", str(werk.map), "--lock", str(leeg)]
    argv += ["--uit", str(uit[0]), "--uit", str(uit[1]), "--log", str(logs[0])]
    argv += ["--log", str(logs[1]), "--uit-fp32", str(fp32), "--log-fp32", str(log32)]
    assert bm.main(argv) == bm.VOORSTEL
    assert _kort(werk)[:5] == [
        "BZ2 omzetting 1: 101.5 s, maxrss 812 MiB (grens 1536)",
        "BZ2 omzetting 2: 102.5 s, maxrss 790 MiB (grens 1536)",
        "BZ2 twee keer omzetten: dezelfde sha256 voor alle 5 bestanden",
        "BZ2 omzetting met --fp32 (voor BZ4): 18.7 s, maxrss 1582 MiB (geen grens)",
        "BZ2 met --fp32: dezelfde sha256 voor alle 5 bestanden",
    ]
    assert werk.voorstel_lock.is_file()


def test_vergelijk_met_fp32_andere_uitvoer_is_rood(werk, tmp_path):
    """Review golf 5, uitrol F1: geeft --fp32 een ander meta.json (of een ander bestand van
    UITVOER), dan rekent BZ4 met een ander model dan de dienst: rood, en geen voorstel."""
    uit, logs = _twee_omzettingen(tmp_path)
    fp32, log32 = _met_fp32(tmp_path, uit[0], anders=omzetten.META)
    leeg = tmp_path / "models.lock"
    leeg.write_text("")
    assert bm.vergelijk(werk, leeg, uit, logs, fp32, log32) == 1
    fouten = [r for r in _kort(werk) if r.startswith("BZ2 FOUT")]
    assert len(fouten) == 1 and fouten[0].startswith("BZ2 FOUT meta.json: met --fp32 ")
    assert not werk.voorstel_lock.exists()


def test_vergelijk_met_fp32_wil_de_fp32_grafen_en_beide_opties(werk, tmp_path):
    uit, logs = _twee_omzettingen(tmp_path)
    fp32, log32 = _met_fp32(tmp_path, uit[0])
    (fp32 / omzetten.TEKST_FP32).unlink()
    with pytest.raises(bm.Fout, match="tekst.fp32.onnx: ontbreekt in de omzetting met --fp32"):
        bm.vergelijk(werk, tmp_path / "x", uit, logs, fp32, log32)
    with pytest.raises(bm.Fout, match="--uit-fp32 en --log-fp32 samen"):
        bm.vergelijk(werk, tmp_path / "x", uit, logs, fp32, None)


# --- de set ------------------------------------------------------------------------------------


class NepCommons:
    """De API van Commons met een eigen verzameling bestanden. De zoekmachine kijkt alleen naar
    de positieve P180's: wat er verder niet mag (een mens, geen JPEG, een andere licentie, te
    klein), moet de tool zelf weggooien."""

    def __init__(self, url: str, seed: int = 83) -> None:
        self.url = url
        self.bestanden: dict[int, dict] = {}
        self.inhoud: dict[int, bytes] = {}
        rnd = random.Random(seed)
        pid = 1000

        def maak(p180: set[str], **anders) -> None:
            nonlocal pid
            pid += 1
            data = rnd.randbytes(rnd.randint(200, 900))
            d = {
                "P180": set(p180),
                "P275": {"Q6938433"},
                "P6216": set(),
                "mime": "image/jpeg",
                "breedte": 1600,
                "hoogte": 1200,
                "bytes": len(data),
            }
            d.update(anders)
            self.bestanden[pid] = d
            self.inhoud[pid] = data

        cat = bm.CATEGORIEEN
        for a, b in bm.combinaties():
            maak({cat[a], cat[b], bm.MENS})  # met een mens: nooit
            maak({cat[a], cat[b]}, P275=set(), P6216={"Q19652"})
        for naam, q in cat.items():
            maak({q}, mime="image/png")
            maak({q}, P275={"Q20007257"})  # CC BY 4.0: niet vrij genoeg
            maak({q}, breedte=500)
            maak({q}, bytes=bm.MAX_FOTO + 1)
            if naam == "strand":
                maak({q, cat["zonsondergang"]})
            for _ in range(6):
                maak({q})
        for q in bm.ANDERE.values():
            maak({q, cat["hond"]})  # beeldt ook een hond af: geen andere foto
            maak({q, bm.MENS})
            for _ in range(6):
                maak({q}, P275=set(), P6216={"Q19652"})

    def _zoek(self, q: dict) -> dict:
        wil = set()
        for deel in q["srsearch"].split():
            if deel.startswith("haswbstatement:P180="):
                wil.add(deel.split("=")[1])
        ids = sorted(i for i, d in self.bestanden.items() if wil <= d["P180"])
        offset = int(q.get("sroffset", 0))
        pagina = ids[offset : offset + 50]
        doc = {"query": {"search": [{"pageid": i, "ns": 6} for i in pagina]}}
        if offset + 50 < len(ids):
            doc["continue"] = {"sroffset": offset + 50, "continue": "-||"}
        return doc

    def antwoord(self, q: dict) -> dict:
        if q.get("list") == "search":
            return self._zoek(q)
        if q.get("prop") == "imageinfo":
            pages = []
            for i in map(int, q["pageids"].split("|")):
                d = self.bestanden[i]
                info = {
                    "url": f"{self.url}/upload/{i}.jpg",
                    "descriptionurl": f"https://commons.wikimedia.org/wiki/File:Nep_{i}.jpg",
                    "size": d["bytes"],
                    "width": d["breedte"],
                    "height": d["hoogte"],
                    "mime": d["mime"],
                }
                pages.append({"pageid": i, "imageinfo": [info]})
            return {"query": {"pages": pages}}
        if q.get("action") == "wbgetentities":
            ent = {}
            for m in q["ids"].split("|"):
                d = self.bestanden[int(m[1:])]
                st = {}
                for p in ("P180", "P275", "P6216"):
                    st[p] = [
                        {"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": v}}}}
                        for v in sorted(d[p])
                    ]
                ent[m] = {"id": m, "statements": st}
            return {"entities": ent}
        return {"error": "onbekend"}


@pytest.fixture
def commons(nep):
    c = NepCommons(nep.url)
    nep.commons = c
    for i, data in c.inhoud.items():
        nep.zet(f"/upload/{i}.jpg", data)
    return c


def test_set_zonder_beeld_tsv_stelt_120_fotos_voor(nep, commons, werk, tmp_path):
    assert bm.set_(werk, tmp_path / "beeld.tsv") == bm.VOORSTEL
    fotos = bm.lees_set(werk.voorstel_set.read_text())
    assert len(fotos) == 120
    per_url = {f"{nep.url}/upload/{i}.jpg": i for i in commons.bestanden}
    for f in fotos:
        d = commons.bestanden[per_url[f.url]]
        assert bm.MENS not in d["P180"]
        assert d["mime"] == "image/jpeg" and d["breedte"] >= bm.MIN_ZIJDE
        assert d["P275"] == {"Q6938433"} or d["P6216"] == {"Q19652"}
        assert f.licentie == ("CC0" if d["P275"] else "publiek domein")
        cats = {c for c, q in bm.CATEGORIEEN.items() if q in d["P180"]}
        assert set(f.categorieen) == cats
        assert (werk.set / f"{f.sleutel}.jpg").read_bytes() == commons.inhoud[per_url[f.url]]
        assert f.sha256 == _sha(commons.inhoud[per_url[f.url]])
    sleutels = [f.sleutel for f in fotos]
    assert sleutels[:6] == [f"{a}+{b}" for a, b in bm.combinaties()]
    assert [f.categorieen for f in fotos[:6]] == [(a, b) for a, b in bm.combinaties()]
    assert sleutels.count("hond-1") == 1 and "hond-5" not in sleutels  # hond+strand telt mee
    assert "strand-5" in sleutels
    assert sum(1 for f in fotos if "zonsondergang" in f.categorieen) == 7  # 5 + 1 + boot
    assert len({f.url for f in fotos}) == 120
    # het voorstel staat ook in het log, regel voor regel, zodat een mens het kan overnemen
    assert _kort(werk) == [
        "BZ4 set: beeld.tsv ontbreekt; voorstel met 120 foto's in het verslag",
        "BZ4 voorstel voor beeld.tsv:",
        *(f"  {r}" for r in werk.voorstel_set.read_text().splitlines()),
    ]
    _ua_algemeen(nep)


def test_set_met_beeld_tsv_is_streng(nep, commons, werk, tmp_path):
    assert bm.set_(werk, tmp_path / "geen.tsv") == bm.VOORSTEL
    set_pad = tmp_path / "beeld.tsv"
    set_pad.write_text(werk.voorstel_set.read_text())
    werk2 = bm.Werk(tmp_path / "werk2")
    assert bm.set_(werk2, set_pad) == 0
    assert len(list(werk2.set.iterdir())) == 120
    assert _kort(werk2) == ["BZ4 set: 120 foto's kloppen met beeld.tsv"]
    regels = set_pad.read_text().splitlines()
    i = next(n for n, r in enumerate(regels) if r.startswith("kat-2\t"))
    v = regels[i].split("\t")
    v[3] = "f" * 64
    regels[i] = "\t".join(v)
    set_pad.write_text("\n".join(regels) + "\n")
    with pytest.raises(bm.Fout, match="kat-2.jpg: sha256"):
        bm.set_(bm.Werk(tmp_path / "werk3"), set_pad)


def _geldige_set() -> list[str]:
    regels = ["\t".join(bm.KOLOMMEN)]
    n = 0

    def rij(sleutel, cats, lic="CC0"):
        nonlocal n
        n += 1
        url = f"{bm.UPLOAD}wikipedia/commons/a/ab/F{n}.jpg"
        bron_ = f"https://commons.wikimedia.org/wiki/File:F{n}.jpg"
        regels.append("\t".join((sleutel, cats, lic, f"{n:064x}", str(1000 + n), url, bron_)))

    for a, b in bm.combinaties():
        rij(f"{a}+{b}", f"{a},{b}", "publiek domein")
    for naam in bm.CATEGORIEEN:
        eerst = sum(1 for a, _ in bm.combinaties() if a == naam)
        for i in range(1, bm.PER_CATEGORIE - eerst + 1):
            rij(f"{naam}-{i}", naam)
    for naam in bm.ANDERE:
        for i in range(1, bm.PER_ANDER + 1):
            rij(f"anders-{naam}-{i}", "-")
    return regels


def _vervang(regels, sleutel, kolom, waarde):
    uit = list(regels)
    i = next(n for n, r in enumerate(uit) if r.split("\t")[0] == sleutel)
    v = uit[i].split("\t")
    v[bm.KOLOMMEN.index(kolom)] = waarde
    uit[i] = "\t".join(v)
    return uit


KAPOTTE_SET = {
    "andere kop": (lambda r: ["sleutel\turl", *r[1:]], "eerste regel"),
    "veld te weinig": (lambda r: [*r[:5], r[5].rsplit("\t", 1)[0], *r[6:]], "6 velden"),
    "dubbele sleutel": (lambda r: [*r, r[-1]], "staat er al"),
    "sleutel met hoofdletter": (lambda r: _vervang(r, "kat-1", "sleutel", "Kat-1"), "sleutel"),
    "onbekende categorie": (lambda r: _vervang(r, "kat-1", "categorieen", "kat,vis"), "categ"),
    "categorie twee keer": (lambda r: _vervang(r, "kat-1", "categorieen", "kat,kat"), "categ"),
    "licentie CC BY": (lambda r: _vervang(r, "kat-1", "licentie", "CC-BY-4.0"), "licentie"),
    "sha256 kort": (lambda r: _vervang(r, "kat-1", "sha256", "abc"), "sha256"),
    "bytes 0": (lambda r: _vervang(r, "kat-1", "bytes", "0"), "bytes"),
    "te groot": (lambda r: _vervang(r, "kat-1", "bytes", str(bm.MAX_FOTO + 1)), "groter"),
    "url elders": (lambda r: _vervang(r, "kat-1", "url", "https://elders.example/x.jpg"), "url"),
    "bron elders": (lambda r: _vervang(r, "kat-1", "bron", "https://elders.example/"), "bron"),
    "sleutel past niet": (lambda r: _vervang(r, "kat-1", "categorieen", "hond"), "past niet"),
    "combinatie zonder b": (
        lambda r: _vervang(r, "auto+sneeuw", "categorieen", "auto"),
        "past niet",
    ),
    "andere met categorie": (
        lambda r: _vervang(r, "anders-bloem-1", "categorieen", "bos"),
        "past niet",
    ),
    "onbekend ander onderwerp": (
        lambda r: _vervang(r, "anders-bloem-1", "sleutel", "anders-vis-1"),
        "past niet",
    ),
    "een kat te weinig": (lambda r: [x for x in r if not x.startswith("kat-4\t")], "kat"),
    "een andere te weinig": (lambda r: r[:-1], "andere foto's"),
    "combinatie ontbreekt": (
        lambda r: [
            x.replace("bos+berg\tbos,berg", "bos-5\tbos,berg") if x.startswith("bos+berg") else x
            for x in r
        ],
        "bos\\+berg",
    ),
}


def test_een_geldige_set_komt_door_de_controle():
    fotos = bm.lees_set("\n".join(_geldige_set()) + "\n")
    assert len(fotos) == 120
    assert fotos[0] == bm.Foto(
        "auto+sneeuw",
        ("auto", "sneeuw"),
        "publiek domein",
        f"{1:064x}",
        1001,
        f"{bm.UPLOAD}wikipedia/commons/a/ab/F1.jpg",
        "https://commons.wikimedia.org/wiki/File:F1.jpg",
    )
    assert bm.lees_set("# opmerking\n" + "\n".join(_geldige_set())) == fotos


@pytest.mark.parametrize("geval", sorted(KAPOTTE_SET))
def test_een_set_die_niet_klopt(geval):
    wijzig, fout = KAPOTTE_SET[geval]
    with pytest.raises(bm.Fout, match=fout):
        bm.lees_set("\n".join(wijzig(_geldige_set())) + "\n")


# --- uitslag -----------------------------------------------------------------------------------


def test_uitslag_is_rood_zolang_er_een_voorstel_ligt(werk):
    assert bm.uitslag(werk) == 0
    assert _kort(werk) == ["UITSLAG models.lock en beeld.tsv waren er al; geen voorstel"]
    werk.voorstel_lock.write_text("x")
    werk.voorstel_set.write_text("x")
    assert bm.main(["uitslag", "--werk", str(werk.map)]) == 1
    assert _kort(werk)[1:] == [
        "UITSLAG models.lock mist de regels van SigLIP (voorstel: voorstel-models.lock)",
        "UITSLAG zet die regels pas samen met X3b in models.lock: zonder de stap BEELD van "
        "install.sh maakt niemand de omzetting, en meldt de container ze als ontbrekend "
        "(models_missing)",
        "UITSLAG beeld.tsv ontbreekt (voorstel: voorstel-beeld.tsv)",
    ]


def test_main_zet_een_fout_om_in_een_zin_en_code_1(werk, tmp_path):
    kapot = tmp_path / "models.lock"
    kapot.write_text("twee velden\n")
    assert bm.main(["bron", "--werk", str(werk.map), "--lock", str(kapot)]) == 1
    assert _kort(werk) == [
        "FOUT bron: models.lock regel 1: 2 velden, verwacht sha256 bytes naam url"
    ]
