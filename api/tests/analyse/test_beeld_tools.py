"""tools/beeld/beeld_model.py tegen een eigen nep-server (fase 6, X3a; de job beeld-model).

De echte bronnen (huggingface.co, Wikimedia Commons) zijn vanuit de sandbox dicht; de job draait
alleen in CI. Hier staat vast wat de stappen doen, met een HTTP-server op 127.0.0.1 die doet
alsof hij Hugging Face, de API van Commons, thumb.wikimedia.org en upload.wikimedia.org is:

- bron zonder regels in models.lock: de commit via de API, de vijf bestanden op die commit, een
  voorstel en code 3; met regels: streng (een andere sha256 of grootte is rood, en er blijft
  niets half staan);
- vergelijk: twee omzettingen met dezelfde sha256, maxrss ≤ 1,5 GB, het voorstel voor
  models.lock (dat lock.py zelf weer leest), of streng tegen de regels; de omzetting met --fp32
  (voor BZ4) zonder grens op maxrss, maar met dezelfde vijf bestanden;
- set: 120 foto's volgens Keuze 83 uit de gestructureerde gegevens, zonder mens, alleen CC0 of
  publiek domein, alleen JPEG, elk als thumbnail van DUIM_BREEDTE (1280 px) die groot genoeg is
  voor de kwaliteitstest, op thumb.wikimedia.org (zoals de API hem sinds T427465 geeft; CI op
  45bb926) of nog op de oude plek op upload; elke foto gevraagd als JPEG (Accept) en
  nagekeken op de eerste bytes; beeld.tsv streng nagekeken, en met beeld.tsv alleen halen wat in
  set/ ontbreekt of niet klopt; een fout zegt hoe ver set kwam en of hij een thumbnail of een
  origineel ophaalde, en een tekort waarom de kandidaten afvielen;
- het netwerk: een pauze per dienst van Commons (de API PAUZE_API_S, 8 s, ook voor elke nieuwe
  poging; thumb en upload elk PAUZE_S), Retry-After altijd gevolgd (te lang, niet te lezen of
  buiten het budget: meteen stoppen), van thumb en upload gemiddeld hooguit BANDBREEDTE, en bij
  een opgegeven 429 of 5xx de laatste Retry-After en de reden uit het antwoord in de fout (de
  titel, de eerste <p> of de eerste regel tekst; nooit "Wikimedia Error"); de fout en de regel
  van set zeggen ook hoeveel vragen de API, thumb en upload kregen en hoe vaak het antwoord 429
  was; een omleiding van Commons alleen naar een host van Commons;
- elke vraag heeft een algemene User-Agent, zonder persoon, e-mail of repository; alleen met
  BEELD_CONTACT (standaard uit, streng nagekeken) een contact, alleen naar Commons (ook na een
  omleiding), en dat staat nooit in het log.
"""

from __future__ import annotations

import ast
import email.utils
import hashlib
import html
import importlib.util
import itertools
import json
import random
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import render
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
    van Commons. vragen: (pad met query, User-Agent) van elke vraag; accept: de kopregel Accept
    van elke vraag (None zonder); tijden: time.monotonic() bij elke vraag (de nepklok van een test,
    als die er is); codes: de status van elk antwoord. webp: per pad de bytes die de nep op een
    200 geeft als de Accept om image/webp vraagt, zoals thumb.wikimedia.org (zie
    bm.DUIM_BREEDTE: dezelfde URL, het formaat naar de Accept, geen Vary); anders die van
    paden."""

    def __init__(self) -> None:
        # (status, body) of (status, body, extra kopregels)
        self.paden: dict[str, list[tuple]] = {}
        self.webp: dict[str, bytes] = {}
        self.vragen: list[tuple[str, str]] = []
        self.accept: list[str | None] = []
        self.tijden: list[float] = []
        self.codes: list[int] = []
        self.commons = None
        nep = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                nep.tijden.append(time.monotonic())
                nep.vragen.append((self.path, self.headers.get("User-Agent", "")))
                nep.accept.append(self.headers.get("Accept"))
                pad = urllib.parse.urlsplit(self.path).path
                extra: list[tuple[str, str]] = []
                if pad == "/w/api.php" and nep.commons is not None:
                    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                    druk = nep.commons.druk(q)
                    if druk is not None:  # de API weigert deze vraag (429 of 5xx)
                        status, body, kop = druk
                        extra += list(kop.items())
                    else:
                        doc = nep.commons.antwoord(q)
                        status, body = (
                            (404, b"weg") if doc is None else (200, json.dumps(doc).encode())
                        )
                else:
                    rij = nep.paden.get(pad)
                    if not rij:
                        status, body = 404, b"niet hier"
                    else:
                        status, body, *kop = rij[0] if len(rij) == 1 else rij.pop(0)
                        for naam, waarde in kop[0].items() if kop else ():
                            extra.append((naam, waarde))
                        webp = "image/webp" in (self.headers.get("Accept") or "")
                        if status == 200 and webp and pad in nep.webp:
                            body = nep.webp[pad]
                nep.codes.append(status)
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
    # thumb.wikimedia.org (bm.THUMB, sinds 45bb926): raising=False, zodat ook de tool van vóór
    # THUMB tegen deze tests draait (de terugdraaitest in x3a.md)
    monkeypatch.setattr(bm, "THUMB", f"{n.url}/duim/", raising=False)
    monkeypatch.setattr(bm, "NASA", f"{n.url}/nasa/")
    monkeypatch.setattr(bm.time, "sleep", lambda s: None)
    # Elke test begint zonder pauze en zonder rem van een vorige test: hier slaapt niets, dus
    # _klaar loopt anders steeds verder voor op de klok (de review van de reparatie, R4)
    monkeypatch.setattr(bm, "_vorige", {})
    monkeypatch.setattr(bm, "_klaar", {})
    # en met het hele budget voor wachten na 429 en 5xx, en de teller van de voortgang op 0
    monkeypatch.setattr(bm, "_gewacht", 0.0, raising=False)
    monkeypatch.setattr(bm, "_opgehaald", 0, raising=False)
    # en met de tellers van het verkeer met Commons op 0
    monkeypatch.setattr(bm, "_tellers", {}, raising=False)
    yield n
    n.sluit()


@pytest.fixture
def elders(nep):
    """Een tweede server: een host buiten Commons (localhost; de nep van Commons is 127.0.0.1).
    Wat hij krijgt, staat in zijn vragen."""
    n = Nep()
    n.url = n.url.replace("127.0.0.1", "localhost")
    yield n
    n.sluit()


# Een contact zoals ci.yml het meestuurt (de link naar het repository)
CONTACT = "https://github.com/Eigenaar-1/een_repo.met-punten"
# Het begin van elke JPEG van de nep: bm.JPEG_BEGIN en de rest van een APP0-marker
JPEG = b"\xff\xd8\xff\xe0"


def _webp(n: int) -> bytes:
    """n bytes WebP: RIFF, de lengte en WEBP, zoals thumb.wikimedia.org op Accept: image/webp."""
    return b"RIFF" + (n - 8).to_bytes(4, "little") + b"WEBPVP8 " + bytes(n - 16)


@pytest.fixture(autouse=True)
def _zonder_contact(monkeypatch):
    """Elke test begint zonder BEELD_CONTACT: niet uit de omgeving, niet van een vorige test."""
    monkeypatch.delenv("BEELD_CONTACT", raising=False)
    monkeypatch.setattr(bm, "_contact", None, raising=False)


@pytest.fixture(autouse=True)
def _zonder_lijst(monkeypatch, tmp_path):
    """Elke test begint zonder beeld_niet.tsv: niet die van de repository (die heeft echte bronnen
    van Commons, geen van de nep). Een test die een lijst wil, schrijft hem naar bm.NIET.
    raising=False: ook de tool van vóór de lijst draait tegen deze tests (de terugdraaitest)."""
    monkeypatch.setattr(bm, "NIET", tmp_path / "lijst" / "beeld_niet.tsv", raising=False)


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


def _verkeer(nep: Nep, *, van: int = 0, begin: float = 0.0, contact: str = "nee") -> str:
    """Wat de fout en de regel van set over het verkeer met Commons horen te zeggen
    (Voortgang.verkeer), uit wat de nep zelf zag vanaf nep.vragen[van]: per dienst (de API, thumb
    en upload, in die volgorde) elke vraag (ook een die 429 kreeg), hoe vaak het antwoord 429 of
    5xx was, en na hoeveel seconden (vanaf begin, op de klok van de test) de eerste 429 kwam."""
    delen = []
    for dienst, voor in (("API", "/w/api.php"), ("thumb", "/duim/"), ("upload", "/upload/")):
        rij = [
            (t, c)
            for (p, _), t, c in zip(
                nep.vragen[van:], nep.tijden[van:], nep.codes[van:], strict=True
            )
            if p.startswith(voor)
        ]
        if not rij:
            delen.append(f"{dienst}: 0 vragen")
            continue
        n429 = [t for t, c in rij if c == 429]
        n5xx = sum(1 for _, c in rij if c >= 500)
        deel = (
            f"{dienst}: {len(rij)} {'vraag' if len(rij) == 1 else 'vragen'}, {len(n429)} keer 429"
        )
        if n5xx:
            deel += f", {n5xx} keer 5xx"
        if n429:
            deel += f", eerste 429 na {n429[0] - begin:.0f} s"
        delen.append(deel)
    return "; ".join([*delen, f"contact: {contact}"])


def _api_tijden(nep: Nep) -> list[float]:
    """Wanneer (op de klok van de test) elke vraag aan de API van Commons binnenkwam."""
    return [
        t for (p, _), t in zip(nep.vragen, nep.tijden, strict=True) if p.startswith("/w/api.php")
    ]


def _kortste(tijden: list[float]) -> float:
    """De kortste tijd tussen twee vragen na elkaar, op een microseconde (de nepklok is float)."""
    return round(min(b - a for a, b in itertools.pairwise(tijden)), 6)


def _per_minuut(tijden: list[float]) -> int:
    """Het meeste aantal vragen in een venster van 60 s (begin en eind erbij)."""
    return max((sum(1 for u in tijden if t <= u <= t + 60.0) for t in tijden), default=0)


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
        ("berg", "sneeuw"),
        ("kat", "sneeuw"),
        ("bos", "berg"),
    ]
    # de reserveparen: bestaande categorieën, twee verschillende, en geen paar twee keer
    assert all({a, b} <= set(bm.CATEGORIEEN) and a != b for a, b in bm.RESERVE_COMBIS)
    assert len({frozenset(c) for c in bm.RESERVE_COMBIS}) == len(bm.RESERVE_COMBIS)
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
        "BZ1 thumb.wikimedia.org: bereikbaar (HTTP 404)",
        "BZ1 upload.wikimedia.org: bereikbaar (HTTP 404)",
        "BZ1 images-assets.nasa.gov: bereikbaar (HTTP 404)",
    ]
    _ua_algemeen(nep)


def test_bereik_meldt_thumb_ook_als_de_wortel_omleidt(nep, elders, werk):
    """CI op 45bb926: de thumbnails staan op thumb.wikimedia.org (zie bm.THUMB), en BZ1 noemde
    die host niet. Nu wel, met een regel die iets zegt, ook als de wortel omleidt: naar een host
    van Commons volgt hij, en de regel zegt waarheen en wat daar het antwoord was; naar een andere
    host volgt hij niet (_Omleiding), en dat zegt de regel ook, zonder vraag daarheen."""
    nep.zet(f"/api/models/{bm.REPO}", b"{}")
    nep.zet("/w/api.php", b"{}")
    nep.paden["/duim/"] = [(301, b"", {"Location": f"{nep.url}/"})]
    nep.zet("/", b"<title>Wikimedia Commons</title>")
    assert bm.bereik(werk) == 0
    assert (
        _kort(werk)[2]
        == "BZ1 thumb.wikimedia.org: bereikbaar (HTTP 301 naar 127.0.0.1, daar HTTP 200)"
    )
    nep.paden["/duim/"] = [(302, b"", {"Location": f"{elders.url}/"})]
    werk2 = bm.Werk(werk.map.parent / "werk2")
    assert bm.bereik(werk2) == 0
    assert _kort(werk2) == [
        "BZ1 huggingface.co: bereikbaar (HTTP 200)",
        "BZ1 commons.wikimedia.org: bereikbaar (HTTP 200)",
        "BZ1 thumb.wikimedia.org: bereikbaar (HTTP 302 naar localhost, niet gevolgd (van Commons "
        "alleen naar https op commons.wikimedia.org, thumb.wikimedia.org of "
        "upload.wikimedia.org))",
        "BZ1 upload.wikimedia.org: bereikbaar (HTTP 404)",
        "BZ1 images-assets.nasa.gov: bereikbaar (HTTP 404)",
    ]
    assert elders.vragen == []
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


def test_haal_schrijft_nooit_door_een_part_die_er_al_staat(nep, tmp_path):
    """De review van de reparatie op f16ed5a (R3): set/ kan uit een cache komen, en tar houdt
    symlinks. Een <sleutel>.jpg.part die er al staat (een symlink naar elders, of een oud stuk)
    gaat eerst weg; haal schrijft nooit door de link, en op de plek komt een gewoon bestand."""
    data = b"de echte foto"
    nep.zet("/upload/kat-1.jpg", data)
    set_ = tmp_path / "set"
    set_.mkdir()
    elders = tmp_path / "elders.txt"
    elders.write_bytes(b"niet van haal")
    doel = set_ / "kat-1.jpg"
    (set_ / "kat-1.jpg.part").symlink_to(elders)
    assert bm.haal(f"{bm.UPLOAD}kat-1.jpg", doel, max_bytes=99, sha256=_sha(data))[1] == len(data)
    assert elders.read_bytes() == b"niet van haal"
    assert not doel.is_symlink() and doel.read_bytes() == data
    # een oud stuk als gewoon bestand: weg, en niet vooraan in de nieuwe download
    doel.unlink()
    (set_ / "kat-1.jpg.part").write_bytes(b"half")
    bm.haal(f"{bm.UPLOAD}kat-1.jpg", doel, max_bytes=99, sha256=_sha(data))
    assert doel.read_bytes() == data
    assert sorted(p.name for p in set_.iterdir()) == ["kat-1.jpg"]


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
    Retry-After (seconden of een HTTP-datum) telt als hij langer is dan de eigen wachttijd, tot en
    met MAX_WACHT_S helemaal (CI op 9ebf298: "Retry-After 600", en de tool wachtte 120 s)."""
    slapen: list[float] = []
    monkeypatch.setattr(bm.time, "sleep", slapen.append)
    nep.paden["/vol"] = [
        (429, b"", {"Retry-After": "30"}),
        (429, b"", {"Retry-After": "1"}),
        (503, b"", {"Retry-After": "600"}),
        (429, b"", {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),  # voorbij: telt als 0
        (200, b"klaar"),
    ]
    assert bm.haal(f"{nep.url}/vol", tmp_path / "v", max_bytes=99)[1] == 5
    assert slapen == [30.0, 4.0, 600.0, 16.0]
    assert bm.MAX_WACHT_S == 600.0 and bm.WACHT_TOTAAL_S == 900.0
    assert bm._gewacht == 650.0
    nep.paden["/dicht"] = [(429, b"")] * 9
    with pytest.raises(bm.Fout, match="HTTP 429"):
        bm.haal(f"{nep.url}/dicht", tmp_path / "d", max_bytes=99)
    assert [p for p, _ in nep.vragen].count("/dicht") == 6
    assert slapen[4:] == [2.0, 4.0, 8.0, 16.0, 32.0] and bm._gewacht == 712.0


def test_retry_after_600_wacht_hij_helemaal_nooit_eerder(nep, tmp_path, monkeypatch):
    """CI op 9ebf298: upload.wikimedia.org zei "Retry-After 600", en _open vroeg na 120 s
    opnieuw (hooguit MAX_WACHT_S, toen 120), vijf keer. Nu: de volgende vraag komt op de klok
    nooit eerder dan Retry-After zegt, ook als HTTP-datum, en ook met de pauze van _pauze ertussen
    (een nepklok die alleen door slapen verder loopt)."""
    klok = [1000.0]
    slapen: list[float] = []

    def slaap(s: float) -> None:
        slapen.append(s)
        klok[0] += s

    nu = 1_700_000_000.0  # time.time() als de klok op 0 stond
    monkeypatch.setattr(bm.time, "monotonic", lambda: klok[0])
    monkeypatch.setattr(bm.time, "sleep", slaap)
    monkeypatch.setattr(bm.time, "time", lambda: nu + klok[0])
    # de tweede vraag komt op 1000 + 600 s; de datum ligt 70 s daarna (meer dan de eigen 4 s)
    datum = email.utils.formatdate(nu + 1000.0 + 600.0 + 70.0, usegmt=True)
    nep.paden["/upload/kat-1.jpg"] = [
        (429, b"<title>Too many requests</title>", {"Retry-After": "600"}),
        (429, b"", {"Retry-After": datum}),
        (200, b"de foto"),
    ]
    assert bm.haal(f"{bm.UPLOAD}kat-1.jpg", tmp_path / "k", max_bytes=99)[1] == 7
    assert len(nep.tijden) == 3
    assert nep.tijden[1] - nep.tijden[0] >= 600.0
    assert nep.tijden[2] - nep.tijden[1] >= 70.0
    assert slapen[:2] == [600.0, 70.0] and bm._gewacht == 670.0


def test_retry_after_met_decimalen_telt_en_onleesbaar_stopt_meteen(nep, tmp_path, monkeypatch):
    """Phabricator T414173: Wikimedia stuurde in januari 2026 ook "Retry-After: 11.000". Dat las
    _retry_after als 0, en dan vroeg _open na zijn eigen 2 s opnieuw: eerder dan gevraagd. Nu
    telt een getal met decimalen helemaal (op de nepklok: de volgende vraag ≥ 11 s later), en
    stopt een Retry-After die er is maar niet te lezen is, _open meteen: geen tweede vraag, en
    de fout zegt waarom."""
    klok = [1000.0]
    slapen: list[float] = []

    def slaap(s: float) -> None:
        slapen.append(s)
        klok[0] += s

    monkeypatch.setattr(bm.time, "monotonic", lambda: klok[0])
    monkeypatch.setattr(bm.time, "sleep", slaap)
    nep.paden["/upload/kat-1.jpg"] = [
        (429, b"", {"Retry-After": "11.000"}),
        (503, b"", {"Retry-After": "12.5"}),  # meer dan de eigen 4 s
        (200, b"de foto"),
    ]
    assert bm.haal(f"{bm.UPLOAD}kat-1.jpg", tmp_path / "k", max_bytes=99)[1] == 7
    assert len(nep.tijden) == 3
    assert nep.tijden[1] - nep.tijden[0] >= 11.0
    assert nep.tijden[2] - nep.tijden[1] >= 12.5
    assert slapen[:2] == [11.0, 12.5] and bm._gewacht == 23.5
    # niet te lezen: meteen stoppen, niets gewacht
    for waarde in ("straks", "-5", "11,5"):
        nep.paden["/upload/hond-1.jpg"] = [
            (429, b"<title>Too many requests</title>", {"Retry-After": waarde})
        ] * 9
        voor = len(nep.tijden)
        with pytest.raises(bm.Fout) as fout:
            bm.haal(f"{bm.UPLOAD}hond-1.jpg", tmp_path / "hond-1.jpg", max_bytes=99)
        assert str(fout.value) == (
            f"hond-1.jpg: HTTP 429 (Retry-After {waarde}; niet te lezen, dus meteen gestopt; "
            "antwoord: Too many requests)"
        )
        assert len(nep.tijden) == voor + 1
    assert bm._gewacht == 23.5


def test_te_lang_of_buiten_het_budget_stopt_meteen(nep, tmp_path, monkeypatch):
    """Zegt Retry-After meer dan MAX_WACHT_S, of past het wachten niet meer in WACHT_TOTAAL_S
    (ook 2 tot 32 s zonder Retry-After tellen mee), dan stopt _open meteen: geen tweede vraag,
    niets gewacht, en de fout zegt de code, Retry-After en waarom."""
    slapen: list[float] = []
    monkeypatch.setattr(bm.time, "sleep", slapen.append)
    titel = b"<!DOCTYPE html>\n<html><head><title>Too many requests</title></head>"
    nep.paden["/upload/kat-1.jpg"] = [(429, titel, {"Retry-After": "601"})] * 9
    with pytest.raises(bm.Fout) as fout:
        bm.haal(f"{bm.UPLOAD}kat-1.jpg", tmp_path / "kat-1.jpg", max_bytes=99)
    assert str(fout.value) == (
        "kat-1.jpg: HTTP 429 (Retry-After 601; langer dan MAX_WACHT_S (600 s), dus meteen "
        "gestopt; antwoord: Too many requests)"
    )
    assert [p for p, _ in nep.vragen].count("/upload/kat-1.jpg") == 1
    assert [s for s in slapen if s > bm.PAUZE_S] == [] and bm._gewacht == 0.0
    # het budget: 400 s al gewacht, dan past 600 s niet meer in 900 s
    monkeypatch.setattr(bm, "_gewacht", 400.0)
    nep.paden["/upload/hond-1.jpg"] = [(503, b"", {"Retry-After": "600"})] * 9
    with pytest.raises(bm.Fout) as fout:
        bm.haal(f"{bm.UPLOAD}hond-1.jpg", tmp_path / "hond-1.jpg", max_bytes=99)
    assert str(fout.value) == (
        "hond-1.jpg: HTTP 503 (Retry-After 600; 600 s wachten past niet in wat er over is van "
        "WACHT_TOTAAL_S (500 van 900 s), dus meteen gestopt; antwoord zonder tekst)"
    )
    assert [p for p, _ in nep.vragen].count("/upload/hond-1.jpg") == 1
    assert bm._gewacht == 400.0
    # zonder Retry-After telt 2, 4, 8 s ook: met een budget van 10 s komt de vierde vraag niet.
    # Op een nepklok: aan de API zit tussen twee pogingen ook PAUZE_API_S, en het wachten loopt
    # daarin mee (na 2 s wachten nog 6 s pauze, na 4 s nog 4); alleen het wachten telt in het
    # budget
    klok = [1000.0]

    def slaap(s: float) -> None:
        slapen.append(s)
        klok[0] += s

    monkeypatch.setattr(bm.time, "monotonic", lambda: klok[0])
    monkeypatch.setattr(bm.time, "sleep", slaap)
    monkeypatch.setattr(bm, "_vorige", {})
    monkeypatch.setattr(bm, "_gewacht", 0.0)
    monkeypatch.setattr(bm, "WACHT_TOTAAL_S", 10.0, raising=False)
    slapen.clear()
    nep.paden["/w/api.php"] = [(502, b"")] * 9
    with pytest.raises(bm.Fout) as fout:
        bm.json_van(f"{nep.url}/w/api.php?action=query")
    assert str(fout.value) == (
        "127.0.0.1: HTTP 502 (geen Retry-After; 8 s wachten past niet in wat er over is van "
        "WACHT_TOTAAL_S (4 van 10 s), dus meteen gestopt; antwoord zonder tekst)"
    )
    assert _api_tijden(nep) == [1000.0, 1008.0, 1016.0]
    assert slapen == [2.0, 6.0, 4.0, 4.0] and bm._gewacht == 6.0


def test_tussen_twee_vragen_aan_commons_zit_een_pauze(nep, monkeypatch):
    """CI op 3148c2f: met één vraag per seconde gaf de API van Commons al vóór de eerste foto
    steeds 429 (Retry-After 50). Nu nooit twee vragen aan de API binnen PAUZE_API_S (8 s), aan
    thumb en aan upload binnen PAUZE_S (thumb sinds 45bb926: zie bm.THUMB); elk op zijn eigen
    klok, en elke vraag telt. Andere hosts (Hugging Face) wachten niet en tellen niet."""
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
    monkeypatch.setattr(bm, "THUMB", "https://thumb.example/", raising=False)
    bm._pauze("https://commons.example/w/api.php?a=1")
    bm._pauze("https://commons.example/w/api.php?a=2")
    klok[0] += 0.25
    bm._pauze("https://commons.example/w/api.php?a=3")
    bm._pauze("https://upload.example/x.jpg")  # upload: een eigen beurt, geen 8 s
    bm._pauze("https://upload.example/y.jpg")
    bm._pauze("https://thumb.example/thumb/a/1280px-a.jpg")  # thumb: ook een eigen beurt
    bm._pauze("https://thumb.example/thumb/b/1280px-b.jpg")
    bm._pauze("https://huggingface.co/x")
    assert slapen == [8.0, 7.75, 1.0, 1.0]
    assert bm.PAUZE_API_S == 8.0 and bm.PAUZE_S == 1.0
    assert bm._tellers["API"].vragen == 3 and bm._tellers["upload"].vragen == 2
    assert bm._tellers["thumb"].vragen == 2
    assert set(bm._tellers) == {"API", "thumb", "upload"}
    assert bm.DIENSTEN == ("API", "thumb", "upload")


def test_elke_poging_aan_de_api_wacht_pauze_api_s(nep, monkeypatch):
    """Ook een nieuwe poging na een 429 gaat nooit eerder dan PAUZE_API_S na de vorige vraag aan
    de API, en nooit eerder dan Retry-After zegt: het wachten en de pauze lopen samen (3 s
    Retry-After en dan nog 5 s pauze; 20 s Retry-After en dan geen pauze meer). Elke poging telt,
    en van elke 429 het moment."""
    klok = [1000.0]
    slapen: list[float] = []

    def slaap(s: float) -> None:
        slapen.append(s)
        klok[0] += s

    monkeypatch.setattr(bm.time, "monotonic", lambda: klok[0])
    monkeypatch.setattr(bm.time, "sleep", slaap)
    nep.paden["/w/api.php"] = [
        (429, b"", {"Retry-After": "3"}),
        (200, b"{}"),
        (429, b"", {"Retry-After": "20"}),
        (200, b"{}"),
        (200, b"{}"),
    ]
    for _ in range(3):
        assert bm.json_van(f"{bm.COMMONS}?action=query") == {}
    assert _api_tijden(nep) == [1000.0, 1008.0, 1016.0, 1036.0, 1044.0]
    assert slapen == [3.0, 5.0, 8.0, 20.0, 8.0] and bm._gewacht == 23.0
    api = bm._tellers["API"]
    assert (api.vragen, api.n429, api.n5xx, api.momenten_429) == (5, 2, 0, [1000.0, 1016.0])
    assert "upload" not in bm._tellers


ROBOT_BPS = 25_000_000 // 8  # de robotregels van Wikimedia (media): samen hooguit 25 Mbps


def test_van_upload_hooguit_bandbreedte_andere_hosts_niet(nep, tmp_path, monkeypatch):
    """CI op f16ed5a: upload.wikimedia.org gaf na ±9 minuten HTTP 429. De robotregels (wikitech,
    Robot_policy) staan van media samen hooguit 25 Mbps toe, gemeten over stukken van 10 s; een
    origineel kwam zonder grens in een fractie van een seconde binnen. Van upload nu gemiddeld
    hooguit BANDBREEDTE, per download en over downloads samen, en een pauze spaart geen tegoed
    op; van thumb.wikimedia.org (de thumbnails sinds 45bb926) net zo. Hugging Face en andere
    hosts wachten niet."""
    klok = [1000.0]
    slapen: list[float] = []

    def slaap(s: float) -> None:
        slapen.append(s)
        klok[0] += s

    monkeypatch.setattr(bm.time, "monotonic", lambda: klok[0])
    monkeypatch.setattr(bm.time, "sleep", slaap)
    monkeypatch.setattr(bm, "_vorige", {})
    monkeypatch.setattr(bm, "_klaar", {}, raising=False)
    rnd = random.Random(16)
    groot = rnd.randbytes(3 * (1 << 20) + 12345)  # meer dan drie stukken van 1 MiB
    klein = rnd.randbytes(500_000)
    nep.zet("/upload/a.jpg", groot)
    nep.zet("/upload/b.jpg", klein)
    begin = klok[0]
    assert bm.haal(f"{bm.UPLOAD}a.jpg", tmp_path / "a", max_bytes=len(groot))[1] == len(groot)
    tussen = klok[0]
    assert bm.haal(f"{bm.UPLOAD}b.jpg", tmp_path / "b", max_bytes=len(klein))[1] == len(klein)
    # samen onder de 25 Mbps van de robotregels, en elke download apart niet boven BANDBREEDTE
    # (1e-9: afronding van de klok in float)
    assert len(groot) + len(klein) <= ROBOT_BPS * (klok[0] - begin)
    assert len(groot) <= bm.BANDBREEDTE * (tussen - begin) * (1 + 1e-9)
    assert len(klein) <= bm.BANDBREEDTE * (klok[0] - tussen) * (1 + 1e-9)
    assert bm.BANDBREEDTE <= ROBOT_BPS
    # 100 s niets: dat geeft geen tegoed, de volgende download gaat weer op BANDBREEDTE
    klok[0] += 100.0
    na_pauze = klok[0]
    bm.haal(f"{bm.UPLOAD}a.jpg", tmp_path / "a2", max_bytes=len(groot))
    assert len(groot) <= bm.BANDBREEDTE * (klok[0] - na_pauze) * (1 + 1e-9)
    # thumb.wikimedia.org net zo
    nep.zet("/duim/thumb/c.jpg/1280px-c.jpg", groot)
    van_thumb = klok[0]
    url = f"{bm.THUMB}thumb/c.jpg/1280px-c.jpg"
    assert bm.haal(url, tmp_path / "c", max_bytes=len(groot))[1] == len(groot)
    assert len(groot) <= bm.BANDBREEDTE * (klok[0] - van_thumb) * (1 + 1e-9)
    # Hugging Face (en elke andere host): geen pauze en geen rem
    nep.zet(f"/{bm.REPO}/resolve/{COMMIT}/model.safetensors", groot)
    slapen.clear()
    hf = klok[0]
    url = f"{bm.HF}/{bm.REPO}/resolve/{COMMIT}/model.safetensors"
    assert bm.haal(url, tmp_path / "m", max_bytes=len(groot))[1] == len(groot)
    assert slapen == [] and klok[0] == hf


def test_een_opgegeven_429_zegt_wat_wikimedia_zei(nep, tmp_path):
    """CI op f16ed5a: "FOUT set: strand-1.jpg: HTTP 429" zei niet wat Wikimedia vroeg. Nu staan
    de laatste Retry-After die hij zag en de eerste regel met tekst van het antwoord erbij: alleen
    afdrukbare ASCII, hooguit MAX_REGEL tekens, en niets anders van het antwoord."""
    body = (
        b"\r\n\x1b[2J\x1b[31mToo many requests\x07 \xc3\xa9\x00 see https://w.wiki/4wJS\r\n"
        b"X-Client-IP: 10.0.0.1\n"
    )
    nep.paden["/dicht"] = [(429, body, {"Retry-After": "7"})] * 9
    with pytest.raises(bm.Fout) as fout:
        bm.haal(f"{nep.url}/dicht", tmp_path / "strand-1.jpg", max_bytes=99)
    zin = str(fout.value)
    assert zin == (
        "strand-1.jpg: HTTP 429 (Retry-After 7; antwoord: "
        "?[2J?[31mToo many requests? ??? see https://w.wiki/4wJS)"
    )
    assert [p for p, _ in nep.vragen].count("/dicht") == 6
    # de laatste Retry-After die hij zag, ook als het laatste antwoord er geen heeft; een leeg
    # antwoord
    nep.paden["/druk"] = [
        (429, b"", {"Retry-After": "3"}),
        (503, b"", {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),
        *([(503, b"\n \n")] * 9),
    ]
    with pytest.raises(bm.Fout) as fout:
        bm.haal(f"{nep.url}/druk", tmp_path / "d.jpg", max_bytes=99)
    assert str(fout.value) == (
        "d.jpg: HTTP 503 (Retry-After Wed, 21 Oct 2015 07:28:00 GMT; antwoord zonder tekst)"
    )
    # zonder Retry-After, via json_van, en hooguit MAX_REGEL tekens van de eerste regel
    nep.paden["/w/api.php"] = [(502, b"x" * 500 + b"\ntweede regel")] * 9
    with pytest.raises(bm.Fout) as fout:
        bm.json_van(f"{nep.url}/w/api.php?action=query")
    assert bm.MAX_REGEL == 160
    assert str(fout.value) == f"127.0.0.1: HTTP 502 (geen Retry-After; antwoord: {'x' * 160})"
    # wat niet opnieuw geprobeerd wordt, houdt de korte vorm
    with pytest.raises(bm.Fout) as fout:
        bm.haal(f"{nep.url}/weg", tmp_path / "w.jpg", max_bytes=99)
    assert str(fout.value) == "w.jpg: HTTP 404"
    assert list(tmp_path.iterdir()) == []


# De foutpagina van Wikimedia bij een 429, in de vorm van wikimedia/operations-puppet
# (modules/mediawiki/templates/errorpage.html.erb met de content "<p>%error_body_content%</p>" van
# modules/varnish/manifests/common/errorpage.pp, de footer van
# modules/varnish/templates/errorpage.footer.html.erb en de details van errorpage.inc.vcl.erb) en
# van phabricator T413570: de titel is altijd "Wikimedia Error" en de kop "Error"; de reden staat
# in de eerste <p>, het IP-adres in <details> in de footer. Ook de maten zijn die van de echte
# pagina (de template ingevuld: 2.845 bytes, de <p> op byte 2.226): hier 2.862 bytes, de <p> op
# byte 2.236, dus ruim binnen MAX_ANTWOORD
WIKIMEDIA_REDEN = (
    "Too many requests - please contact noc@wikimedia.org to discuss a less disruptive approach "
    "(3bfa1aa)"
)


def _wikimedia_fout(code: int, reden: str, inhoud: str) -> bytes:
    css = "".join(f".r{i} {{ margin: 0; padding: 0; color: #202122; }}\n" for i in range(34))
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<meta charset="utf-8">\n'
        "<title>Wikimedia Error</title>\n"
        f"<style>\n{css}</style>\n"
        '<meta name="color-scheme" content="light dark">\n<div class="content" role="main">\n'
        '<a href="https://www.wikimedia.org"><img id="logo" '
        'src="https://www.wikimedia.org/static/images/footer/wikimedia.svg" alt="Wikimedia" '
        'width="120" height="120">\n</a>\n'
        "<script>\nif ( /(^|\\.)wikipedia\\.org$/.test( location.hostname ) ) {\n"
        "\tdocument.getElementById( 'logo' ).outerHTML = '<picture>' +\n"
        '\t\t\'<img id="logo" alt="Wikipedia" width="120" height="120">\' +\n'
        "\t\t'</picture>';\n}\n</script>\n"
        '<div class="content-text">\n<h1>Error</h1>\n\n'
        f"<p>{inhoud}</p>\n</div>\n</div>\n"
        '<div class="footer"><p>If you report this error to the Wikimedia System Administrators, '
        'please include the details below.</p><p class="text-muted"><code>Request served via '
        "cp3067 cp3067, Varnish XID 123456789<br>Upstream caches: cp3067 int<br>"
        f"Error: {code}, {reden} at Mon, 05 Jan 2026 10:00:00 GMT<br>"
        "<details><summary>Sensitive client information</summary>IP address: 10.0.0.1"
        "</details></code></p></div>\n</html>\n"
    ).encode()


WIKIMEDIA_429 = _wikimedia_fout(429, WIKIMEDIA_REDEN, WIKIMEDIA_REDEN)
# bij een 5xx zet Wikimedia een vaste zin in de <p> (errorpage.inc.vcl.erb)
WIKIMEDIA_503 = _wikimedia_fout(
    503,
    "Service Unavailable",
    "Our servers are currently under maintenance or experiencing a technical issue",
)


@pytest.mark.parametrize(
    "body, regel",
    [
        # CI op 9ebf298: de eerste regel was "<!DOCTYPE html>". De titel van Wikimedia zegt ook
        # niets ("Wikimedia Error"), de kop ook niet ("Error"): de reden staat in de eerste <p>
        (WIKIMEDIA_429, WIKIMEDIA_REDEN),
        (
            WIKIMEDIA_503,
            "Our servers are currently under maintenance or experiencing a technical issue",
        ),
        # een titel die wel iets zegt, gaat voor de <p>
        (b"<title>429 Too Many Requests</title><p>Te veel</p>", "429 Too Many Requests"),
        # zonder <p>: de eerste regel die meer zegt dan "Error"; nooit iets uit <details>
        (
            b"<title>Wikimedia Error</title>\n<h1>Error</h1>\n<details>IP 10.0.0.1</details>\n"
            b"<div>Too many requests</div>\n",
            "Too many requests",
        ),
        # een titel met tags, regels, stuurtekens en niet-ASCII: alleen afdrukbare ASCII
        (
            b"<TITLE lang=x>\n Te <b>veel</b>\x1b[31m\n vragen \xc3\xa9 </TITLE>",
            "Te veel ?[31m vragen ??",
        ),
        # een lege titel telt niet, en zonder <p> de eerste regel met tekst zonder tags, zonder
        # style en commentaar
        (
            b"<!DOCTYPE html>\n<title> </title><style>\nbody { x: 1 }\n</style>\n<!-- a\nb -->\n"
            b"<div>\n  <h1>Fout</h1> <span>Te veel vragen</span>\n</div>\n",
            "Fout Te veel vragen",
        ),
        # een style die niet sluit (het antwoord houdt op na MAX_ANTWOORD): geen css
        (b"<html>\n<style>\nbody { x: 1 }\n", ""),
        # een titel langer dan MAX_REGEL: hooguit 160 tekens
        (b"<title>" + b"y" * 300 + b"</title>", "y" * 160),
    ],
)
def test_de_uitleg_is_de_titel_of_de_eerste_regel_met_tekst(nep, tmp_path, body, regel):
    """Wat het log van een opgegeven 429 of 5xx overneemt: de tekst van <title>, anders van de
    eerste <p>, anders de eerste regel met tekst, zonder de tags van HTML; wat alleen "Error" of
    "Wikimedia Error" zegt, telt niet (de foutpagina van Wikimedia). Alleen afdrukbare ASCII (de
    rest wordt ?), hooguit MAX_REGEL tekens, en niets anders van het antwoord (CI op 9ebf298:
    "antwoord: <!DOCTYPE html>"; de review van de reparatie, R1: "antwoord: Wikimedia
    Error")."""
    nep.paden["/upload/kat-1.jpg"] = [(429, body, {"Retry-After": "1"})] * 9
    with pytest.raises(bm.Fout) as fout:
        bm.haal(f"{bm.UPLOAD}kat-1.jpg", tmp_path / "kat-1.jpg", max_bytes=99)
    antwoord = f"antwoord: {regel}" if regel else "antwoord zonder tekst"
    assert str(fout.value) == f"kat-1.jpg: HTTP 429 (Retry-After 1; {antwoord})"
    assert "10.0.0.1" not in str(fout.value) and "{" not in str(fout.value)
    assert "Wikimedia Error" not in str(fout.value)


# --- omleidingen --------------------------------------------------------------------------------

ALLEEN_COMMONS = (
    "van Commons alleen naar https op commons.wikimedia.org, thumb.wikimedia.org of "
    "upload.wikimedia.org"
)


def test_een_omleiding_van_commons_naar_elders_volgt_hij_nooit(nep, elders, tmp_path):
    """urllib volgt elke omleiding, ook naar een andere host, en neemt de kopregels mee: met
    BEELD_CONTACT ging het contact dan mee naar die host, en de eigenaar gaf alleen toestemming
    voor Wikimedia. Nu volgt _Omleiding een omleiding van een vraag aan Commons (de API, thumb of
    upload) alleen naar https op een host van Commons; elke andere is een fout met de code en de
    host (nooit de hele URL), zonder een vraag daarheen, zonder nieuwe poging, en zonder iets op
    schijf."""
    bm._zet_contact(CONTACT)
    met = bm.UA_MET_CONTACT.format(contact=CONTACT)
    elders.zet("/x.jpg", JPEG + b"van elders")
    elders.zet("/w/api.php", b"{}")
    nep.paden["/duim/thumb/1.jpg/1280px-1.jpg"] = [
        (302, b"", {"Location": f"{elders.url}/x.jpg?wie=ci"})
    ]
    with pytest.raises(bm.Fout) as fout:
        bm.haal(
            f"{bm.THUMB}thumb/1.jpg/1280px-1.jpg", tmp_path / "kat-1.jpg", max_bytes=99, jpeg=True
        )
    assert str(fout.value) == f"kat-1.jpg: HTTP 302 naar localhost, niet gevolgd ({ALLEEN_COMMONS})"
    # de API net zo
    nep.paden["/w/api.php"] = [(301, b"", {"Location": f"{elders.url}/w/api.php?action=query"})]
    with pytest.raises(bm.Fout) as fout:
        bm.json_van(f"{bm.COMMONS}?action=query")
    assert str(fout.value) == f"127.0.0.1: HTTP 301 naar localhost, niet gevolgd ({ALLEEN_COMMONS})"
    # upload, en een host die geen gewone hostnaam is: "een onbekende host", nooit de URL
    nep.paden["/upload/2.jpg"] = [(307, b"", {"Location": "https://x_y.example/2.jpg?wie=ci"})]
    with pytest.raises(bm.Fout) as fout:
        bm.haal(f"{bm.UPLOAD}2.jpg", tmp_path / "kat-2.jpg", max_bytes=99, jpeg=True)
    assert str(fout.value) == (
        f"kat-2.jpg: HTTP 307 naar een onbekende host, niet gevolgd ({ALLEEN_COMMONS})"
    )
    # dezelfde host en poort als Commons, maar een ander schema: ook niet
    https = nep.url.replace("http://", "https://")
    nep.paden["/upload/3.jpg"] = [(301, b"", {"Location": f"{https}/upload/3.jpg"})]
    with pytest.raises(bm.Fout, match=r"^kat-3\.jpg: HTTP 301 naar 127\.0\.0\.1, niet gevolgd"):
        bm.haal(f"{bm.UPLOAD}3.jpg", tmp_path / "kat-3.jpg", max_bytes=99, jpeg=True)
    # nooit een vraag elders, elke vraag aan Commons één keer (geen nieuwe poging) en met het
    # contact, en niets op schijf
    assert elders.vragen == []
    assert [p for p, _ in nep.vragen] == [
        "/duim/thumb/1.jpg/1280px-1.jpg",
        "/w/api.php?action=query",
        "/upload/2.jpg",
        "/upload/3.jpg",
    ]
    assert all(ua == met for _, ua in nep.vragen)
    assert list(tmp_path.iterdir()) == []


def test_omleidingen_alleen_naar_https_op_de_hosts_van_commons():
    """Met de echte URL's (zonder de nep): de drie hosts van Commons, alle drie over https op
    poort 443; http naar dezelfde host, een andere poort of een andere host valt erbuiten."""
    assert bm.COMMONS.startswith("https://commons.wikimedia.org/")
    assert bm.THUMB == "https://thumb.wikimedia.org/"
    assert bm.UPLOAD == "https://upload.wikimedia.org/"
    assert bm._commons_hosts() == {
        ("https", "commons.wikimedia.org", 443),
        ("https", "thumb.wikimedia.org", 443),
        ("https", "upload.wikimedia.org", 443),
    }
    for url in (
        "http://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/F.jpg/1280px-F.jpg",
        "https://thumb.wikimedia.org:8443/x",
        "https://wikimedia.org.example/x",
        "https://maps.wikimedia.org/x",
        "https://[::1/x",
    ):
        assert bm._oorsprong(url) not in bm._commons_hosts(), url


def test_een_omleiding_binnen_commons_volgt_hij_met_pauze_en_telling(nep, tmp_path, monkeypatch):
    """De oude URL's van thumbnails op upload werken nog; Wikimedia wil ze "eventually" met een
    301 naar thumb.wikimedia.org sturen (zie bm.THUMB). Zo'n omleiding volgt de tool: de nieuwe
    vraag krijgt de User-Agent voor de nieuwe URL (met contact: naar thumb wel), Accept gaat mee,
    hij wacht de pauze van thumb (PAUZE_S na de vorige vraag daar) en telt bij thumb. Een
    omleiding naar commons.wikimedia.org buiten de API (zo antwoordt thumb.wikimedia.org op het pad
    van een origineel) volgt hij ook, maar zonder contact en zonder pauze of telling; daar komt
    geen JPEG, dus een fout, en niets op schijf."""
    klok, _ = _nepklok(monkeypatch)
    bm._zet_contact(CONTACT)
    met = bm.UA_MET_CONTACT.format(contact=CONTACT)
    duim = JPEG + b"de thumbnail"
    nep.zet("/duim/thumb/a/ab/F.jpg/1280px-F.jpg", duim)
    nep.zet("/duim/thumb/a/ab/G.jpg/1280px-G.jpg", duim)
    nep.paden["/upload/thumb/a/ab/G.jpg/1280px-G.jpg"] = [
        (301, b"", {"Location": f"{bm.THUMB}thumb/a/ab/G.jpg/1280px-G.jpg"})
    ]
    f = bm.haal(
        f"{bm.THUMB}thumb/a/ab/F.jpg/1280px-F.jpg", tmp_path / "boot-1.jpg", max_bytes=99, jpeg=True
    )
    g = bm.haal(
        f"{bm.UPLOAD}thumb/a/ab/G.jpg/1280px-G.jpg",
        tmp_path / "boot-2.jpg",
        max_bytes=99,
        jpeg=True,
    )
    assert f == g == (_sha(duim), len(duim))
    assert [p for p, _ in nep.vragen] == [
        "/duim/thumb/a/ab/F.jpg/1280px-F.jpg",
        "/upload/thumb/a/ab/G.jpg/1280px-G.jpg",
        "/duim/thumb/a/ab/G.jpg/1280px-G.jpg",
    ]
    assert [ua for _, ua in nep.vragen] == [met] * 3
    assert nep.accept == ["image/jpeg"] * 3
    # upload had een eigen beurt; de omleiding naar thumb wachtte PAUZE_S na F
    assert nep.tijden[1] < nep.tijden[0] + bm.PAUZE_S
    assert round(nep.tijden[2] - nep.tijden[0], 6) >= bm.PAUZE_S
    assert (bm._tellers["thumb"].vragen, bm._tellers["upload"].vragen) == (2, 1)
    # het pad van een origineel op thumb: een 301 naar commons.wikimedia.org
    nep.paden["/duim/wikipedia/commons/a/ab/H.jpg"] = [(301, b"", {"Location": f"{nep.url}/"})]
    nep.zet("/", b"<!DOCTYPE html><title>Wikimedia Commons</title>")
    voor = klok[0]
    with pytest.raises(bm.Fout) as fout:
        bm.haal(
            f"{bm.THUMB}wikipedia/commons/a/ab/H.jpg",
            tmp_path / "boot-3.jpg",
            max_bytes=999,
            jpeg=True,
        )
    assert str(fout.value) == "boot-3.jpg: geen JPEG"
    assert nep.vragen[-1] == ("/", bm.UA)  # buiten de API: geen dienst, dus zonder contact
    assert nep.tijden[-1] == nep.tijden[-2] >= voor
    assert bm._tellers["thumb"].vragen == 3 and "API" not in bm._tellers
    assert sorted(p.name for p in tmp_path.iterdir()) == ["boot-1.jpg", "boot-2.jpg"]


def test_hugging_face_naar_zijn_cdn_volgt_hij_zonder_contact(nep, elders, tmp_path):
    """Hugging Face stuurt een download door naar zijn CDN (een andere host): dat blijft werken,
    zonder contact (dat gaat alleen naar Commons, ook na een omleiding), zonder Accept, en zonder
    pauze of telling."""
    bm._zet_contact(CONTACT)
    data = b"het model"
    pad = f"/{bm.REPO}/resolve/{COMMIT}/model.safetensors"
    nep.paden[pad] = [(302, b"", {"Location": f"{elders.url}/cdn/model.safetensors?x=1"})]
    elders.zet("/cdn/model.safetensors", data)
    uit = bm.haal(f"{bm.HF}{pad}", tmp_path / "m", max_bytes=99, sha256=_sha(data))
    assert uit == (_sha(data), len(data))
    assert nep.vragen == [(pad, bm.UA)]
    assert elders.vragen == [("/cdn/model.safetensors?x=1", bm.UA)]
    assert nep.accept == elders.accept == [None]
    assert bm._tellers == {}


def test_een_keten_via_commons_buiten_een_dienst_naar_elders_volgt_hij_niet(nep, elders, tmp_path):
    """De bewaker van _Omleiding kijkt naar waar de vraag begon, niet naar de vorige stap. Thumb
    leidt om naar commons.wikimedia.org buiten de API (een host van Commons, maar geen dienst: zo
    antwoordt thumb.wikimedia.org op het pad van een origineel), en die weer naar een andere host:
    die tweede omleiding volgt hij niet. Gaf de nieuwe vraag niet mee waar hij begon, dan leek de
    tweede stap een vraag die buiten Commons begon, en ging er wel een vraag (met de foto erachter)
    naar die host (de review van de reparatie, R1)."""
    bm._zet_contact(CONTACT)
    met = bm.UA_MET_CONTACT.format(contact=CONTACT)
    elders.zet("/x.jpg", JPEG + b"van elders")
    nep.paden["/duim/thumb/K.jpg/1280px-K.jpg"] = [(301, b"", {"Location": f"{nep.url}/omweg"})]
    nep.paden["/omweg"] = [(302, b"", {"Location": f"{elders.url}/x.jpg?wie=ci"})]
    with pytest.raises(bm.Fout) as fout:
        bm.haal(
            f"{bm.THUMB}thumb/K.jpg/1280px-K.jpg", tmp_path / "kat-1.jpg", max_bytes=99, jpeg=True
        )
    assert str(fout.value) == f"kat-1.jpg: HTTP 302 naar localhost, niet gevolgd ({ALLEEN_COMMONS})"
    # de API net zo
    nep.paden["/w/api.php"] = [(301, b"", {"Location": f"{nep.url}/omweg"})]
    with pytest.raises(bm.Fout) as fout:
        bm.json_van(f"{bm.COMMONS}?action=query")
    assert str(fout.value) == f"127.0.0.1: HTTP 302 naar localhost, niet gevolgd ({ALLEEN_COMMONS})"
    assert elders.vragen == []
    # de omweg is geen dienst: zonder contact, en elke vraag één keer (geen nieuwe poging)
    assert nep.vragen == [
        ("/duim/thumb/K.jpg/1280px-K.jpg", met),
        ("/omweg", bm.UA),
        ("/w/api.php?action=query", met),
        ("/omweg", bm.UA),
    ]
    assert list(tmp_path.iterdir()) == []


def test_na_een_omleiding_telt_en_remt_de_host_die_antwoordde(nep, tmp_path, monkeypatch):
    """De 301 van een oude URL op upload naar thumb (zie bm.THUMB), en dan een 429 van thumb. Die
    429 telt bij thumb, niet bij upload: de tellers in de fout zeggen welke host weigert. De bytes
    tellen in de rem (BANDBREEDTE) van thumb, de host die ze gaf, niet in die van upload. Accept
    gaat mee bij elke stap en elke poging. Thumb staat hier op localhost en upload op 127.0.0.1
    (dezelfde nep), zodat de rem per host te zien is (de review van de reparatie, R3 en R4)."""
    _, slapen = _nepklok(monkeypatch)
    poort = nep.url.rsplit(":", 1)[1]
    monkeypatch.setattr(bm, "THUMB", f"http://localhost:{poort}/duim/")
    duim = JPEG + bytes(5000)
    oud, nieuw = "/upload/thumb/a/ab/G.jpg/1280px-G.jpg", "/duim/thumb/a/ab/G.jpg/1280px-G.jpg"
    nep.paden[oud] = [(301, b"", {"Location": f"{bm.THUMB}thumb/a/ab/G.jpg/1280px-G.jpg"})]
    nep.paden[nieuw] = [(429, b"", {"Retry-After": "3"}), (200, duim)]
    uit = bm.haal(
        f"{bm.UPLOAD}thumb/a/ab/G.jpg/1280px-G.jpg",
        tmp_path / "boot-1.jpg",
        max_bytes=9999,
        jpeg=True,
    )
    assert uit == (_sha(duim), len(duim))
    assert [p for p, _ in nep.vragen] == [oud, nieuw, oud, nieuw]
    assert nep.codes == [301, 429, 301, 200]
    assert nep.accept == ["image/jpeg"] * 4
    thumb, upload = bm._tellers["thumb"], bm._tellers["upload"]
    assert (thumb.vragen, thumb.n429, thumb.n5xx) == (2, 1, 0)
    assert (upload.vragen, upload.n429, upload.n5xx) == (2, 0, 0)
    # de rem: alleen die van thumb (localhost), met precies de bytes van de thumbnail
    assert set(bm._klaar) == {"localhost"}
    assert slapen[-1] == pytest.approx(len(duim) / bm.BANDBREEDTE)


def test_de_dienst_leest_de_host_zoals_de_bewaker():
    """_Omleiding laat een omleiding door naar een host van Commons zoals urlsplit hem leest: in
    kleine letters, en met poort 443 ook als die er staat. _dienst las startswith op de hele URL:
    https://THUMB.wikimedia.org/… en https://thumb.wikimedia.org:443/… kwamen door de bewaker,
    maar zonder pauze, telling, rem en contact (de review van de reparatie, R5). Nu leest _dienst
    de oorsprong net zo, met de echte URL's: elke dienst is een host van Commons, en het enige wat
    de bewaker doorlaat zonder dienst te zijn, is commons.wikimedia.org buiten de API."""
    bm._zet_contact(CONTACT)
    met = bm.UA_MET_CONTACT.format(contact=CONTACT)
    for url, dienst in (
        ("https://commons.wikimedia.org/w/api.php?action=query", "API"),
        ("https://COMMONS.Wikimedia.org:443/w/api.php?action=query", "API"),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/F.jpg/1280px-F.jpg", "thumb"),
        ("https://THUMB.wikimedia.org/wikipedia/commons/thumb/a/ab/F.jpg/1280px-F.jpg", "thumb"),
        ("https://thumb.wikimedia.org:443/x", "thumb"),
        ("https://Upload.Wikimedia.Org/wikipedia/commons/a/ab/F.jpg", "upload"),
        ("https://upload.wikimedia.org:443/x", "upload"),
        ("https://commons.wikimedia.org/wiki/Main_Page", None),
        ("https://Commons.wikimedia.org/", None),
        ("http://thumb.wikimedia.org/x", None),
        ("https://thumb.wikimedia.org:8443/x", None),
        ("https://maps.wikimedia.org/x", None),
        ("https://thumb.wikimedia.org.example/x", None),
        ("https://[::1/x", None),
        ("https://huggingface.co/x", None),
    ):
        assert bm._dienst(url) == dienst, url
        assert bm._user_agent(url) == (met if dienst else bm.UA), url
        door = bm._oorsprong(url) in bm._commons_hosts()
        assert door == (dienst is not None or bm._hostnaam(url) == "commons.wikimedia.org"), url


def test_een_omleiding_naar_thumb_in_hoofdletters_wacht_telt_en_remt(nep, tmp_path, monkeypatch):
    """Van de nep: upload leidt om naar thumb met de host in hoofdletters (LOCALHOST; thumb staat
    op localhost). Die stap is thumb: met het contact, PAUZE_S na de vorige vraag aan thumb,
    geteld bij thumb, en de bytes in de rem van thumb (de review van de reparatie, R5)."""
    klok, _ = _nepklok(monkeypatch)
    poort = nep.url.rsplit(":", 1)[1]
    monkeypatch.setattr(bm, "THUMB", f"http://localhost:{poort}/duim/")
    bm._zet_contact(CONTACT)
    met = bm.UA_MET_CONTACT.format(contact=CONTACT)
    duim = JPEG + b"de thumbnail"
    nep.zet("/duim/thumb/F.jpg/1280px-F.jpg", duim)
    nep.zet("/duim/thumb/G.jpg/1280px-G.jpg", duim)
    nep.paden["/upload/thumb/G.jpg/1280px-G.jpg"] = [
        (301, b"", {"Location": f"http://LOCALHOST:{poort}/duim/thumb/G.jpg/1280px-G.jpg"})
    ]
    bm.haal(f"{bm.THUMB}thumb/F.jpg/1280px-F.jpg", tmp_path / "a-1.jpg", max_bytes=99, jpeg=True)
    bm._klaar.clear()
    bm.haal(f"{bm.UPLOAD}thumb/G.jpg/1280px-G.jpg", tmp_path / "a-2.jpg", max_bytes=99, jpeg=True)
    assert [p for p, _ in nep.vragen] == [
        "/duim/thumb/F.jpg/1280px-F.jpg",
        "/upload/thumb/G.jpg/1280px-G.jpg",
        "/duim/thumb/G.jpg/1280px-G.jpg",
    ]
    assert [ua for _, ua in nep.vragen] == [met] * 3
    assert round(nep.tijden[2] - nep.tijden[0], 6) >= bm.PAUZE_S
    assert (bm._tellers["thumb"].vragen, bm._tellers["upload"].vragen) == (2, 1)
    assert set(bm._klaar) == {"localhost"} and bm._klaar["localhost"] == pytest.approx(klok[0])


# --- de JPEG ------------------------------------------------------------------------------------


def test_haal_met_jpeg_vraagt_jpeg_en_kijkt_de_eerste_bytes_na(nep, tmp_path):
    """thumb.wikimedia.org geeft op dezelfde URL WebP of JPEG, naar de Accept (zie
    bm.DUIM_BREEDTE), en een cache die de Vary mist, kan WebP geven op een vraag om JPEG. haal met
    jpeg (de foto's van de set) vraagt Accept: image/jpeg en kijkt de eerste bytes na: anders een
    Fout die zegt wat er kwam, zodra hij 12 bytes heeft (niet de hele download), en niets op
    schijf. Zonder jpeg (Hugging Face, de bron): geen Accept en geen controle."""
    assert bm.ACCEPT == "image/jpeg" and bm.JPEG_BEGIN == b"\xff\xd8\xff"
    goed = JPEG + b"een foto"
    nep.zet("/duim/thumb/goed.jpg/1280px-goed.jpg", goed)
    nep.webp["/duim/thumb/goed.jpg/1280px-goed.jpg"] = _webp(64)
    url = f"{bm.THUMB}thumb/goed.jpg/1280px-goed.jpg"
    assert bm.haal(url, tmp_path / "kat-1.jpg", max_bytes=99, jpeg=True) == (_sha(goed), len(goed))
    assert nep.accept == ["image/jpeg"]
    # de nep geeft wel WebP met de Accept van een browser (zoals thumb.wikimedia.org)
    vraag = urllib.request.Request(url, headers={"Accept": "image/avif,image/webp,*/*"})
    with urllib.request.urlopen(vraag, timeout=30) as antwoord:
        assert antwoord.read()[8:12] == b"WEBP"
    # een cache die de Vary mist: WebP, wat de Accept ook zegt; groot, en toch na één stuk gestopt
    groot = _webp(3 << 20)
    nep.zet("/duim/thumb/webp.jpg/1280px-webp.jpg", groot)
    voor = bm._opgehaald
    with pytest.raises(bm.Fout) as fout:
        bm.haal(
            f"{bm.THUMB}thumb/webp.jpg/1280px-webp.jpg",
            tmp_path / "kat-2.jpg",
            max_bytes=4 << 20,
            jpeg=True,
        )
    assert str(fout.value) == "kat-2.jpg: WebP in plaats van JPEG"
    assert 12 <= bm._opgehaald - voor <= 1 << 20
    # iets anders (een HTML-pagina), en korter dan 12 bytes (na de download nagekeken)
    for naam, body in (("html", b"<!DOCTYPE html><html><body>"), ("gif", b"GIF89a")):
        nep.zet(f"/upload/{naam}.jpg", body)
        with pytest.raises(bm.Fout) as fout:
            bm.haal(f"{bm.UPLOAD}{naam}.jpg", tmp_path / f"{naam}-1.jpg", max_bytes=99, jpeg=True)
        assert str(fout.value) == f"{naam}-1.jpg: geen JPEG"
    # een JPEG van maar vier bytes mag (de controle is alleen het begin)
    nep.zet("/upload/klein.jpg", JPEG)
    assert (
        bm.haal(f"{bm.UPLOAD}klein.jpg", tmp_path / "klein-1.jpg", max_bytes=99, jpeg=True)[1] == 4
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kat-1.jpg", "klein-1.jpg"]
    # zonder jpeg: geen Accept, en de bytes zijn wat ze zijn
    n = len(nep.accept)
    assert bm.haal(f"{bm.UPLOAD}gif.jpg", tmp_path / "g", max_bytes=99)[1] == 6
    assert nep.accept[n:] == [None]


def test_elke_nieuwe_poging_van_een_foto_vraagt_weer_jpeg(nep, tmp_path, monkeypatch):
    """Accept: image/jpeg gaat mee met elke poging van een download van de set, ook met een nieuwe
    poging na een 503 of een 429: anders kon juist de poging die slaagt WebP krijgen (zie
    bm.DUIM_BREEDTE), of de bytes van een andere vorm dan die van de sha256 in beeld.tsv (de
    review van de reparatie, R2)."""
    _nepklok(monkeypatch)
    duim = JPEG + b"de thumbnail"
    pad = "/duim/thumb/L.jpg/1280px-L.jpg"
    nep.paden[pad] = [(503, b""), (429, b"", {"Retry-After": "3"}), (200, duim)]
    url = f"{bm.THUMB}thumb/L.jpg/1280px-L.jpg"
    assert bm.haal(url, tmp_path / "kat-1.jpg", max_bytes=99, jpeg=True) == (_sha(duim), len(duim))
    assert nep.vragen == [(pad, bm.UA)] * 3 and nep.codes == [503, 429, 200]
    assert nep.accept == ["image/jpeg"] * 3
    thumb = bm._tellers["thumb"]
    assert (thumb.vragen, thumb.n429, thumb.n5xx) == (3, 1, 1)


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
    klein), moet de tool zelf weggooien. Met iiurlwidth geeft imageinfo ook een thumbnail, zoals
    MediaWiki: van een breder origineel een eigen bestand op thumb.wikimedia.org (de nep: /duim/,
    bm.THUMB; zo geeft de API hem sinds T427465, CI op 45bb926), behalve van de foto's van alleen
    een boot: die nog op de oude plek, /upload/thumb/ (oud); van een origineel dat niet breder is
    het origineel zelf als thumburl, en dan zijn thumbwidth en thumbheight de maten van het
    origineel (MediaWiki, ApiQueryImageInfo: "T25834 - If the URLs are the same, we haven't
    resized it"). Elk bestand begint als een JPEG; van elke thumbnail op /duim/ geeft de nep WebP
    als de Accept erom vraagt (de fixture commons). weg: vragen aan de API waarop de nep 404 zegt;
    druk: (status, body, kopregels) als de API die vraag weigert (429 of 5xx), anders None."""

    def __init__(self, url: str, seed: int = 83) -> None:
        self.url = url
        self.bestanden: dict[int, dict] = {}
        self.inhoud: dict[int, bytes] = {}  # de originelen
        self.duim: dict[int, bytes] = {}  # de thumbnails van DUIM_BREEDTE breed
        self.weg = lambda q: False
        self.druk = lambda q: None
        self.tl_later: set[int] = set()  # bestanden waarvan de sjablonen pas na tlcontinue komen
        rnd = random.Random(seed)
        pid = 1000

        def maak(p180: set[str], **anders) -> None:
            nonlocal pid
            pid += 1
            data = JPEG + rnd.randbytes(rnd.randint(200, 900))
            d = {
                "P180": set(p180),
                "P275": {"Q6938433"},
                "P6216": set(),
                "P31": set(),
                "P7482": set(),
                "prominent": set(),  # de P180's met rang preferred
                "eigen": set(),  # P6243, P195, P217: alleen of er een uitspraak is
                "sjablonen": set(),
                "mime": "image/jpeg",
                "breedte": 1600,
                "hoogte": 1200,
                "bytes": len(data),
            }
            d.update(anders)
            self.bestanden[pid] = d
            self.inhoud[pid] = data
            if d["breedte"] > bm.DUIM_BREEDTE:
                self.duim[pid] = JPEG + rnd.randbytes(rnd.randint(8_000, 12_000))

        cat = bm.CATEGORIEEN
        for a, b in bm.combinaties():
            maak({cat[a], cat[b], bm.MENS})  # met een mens: nooit
            maak({cat[a], cat[b]}, P275=set(), P6216={"Q19652"})
        for naam, q in cat.items():
            maak({q}, mime="image/png")
            maak({q}, P275={"Q20007257"})  # CC BY 4.0: niet vrij genoeg
            maak({q}, breedte=500)
            maak({q}, breedte=1000, bytes=bm.MAX_FOTO + 1)  # het origineel zelf, te groot
            if naam == "strand":
                maak({q, cat["zonsondergang"]})
            if naam == "fiets":
                maak({q}, breedte=1000, hoogte=800)  # niet breder: het origineel zelf (fiets-1)
                maak({q}, breedte=6000, hoogte=1000)  # thumbnail 1280 x 213: te laag
                maak({q}, bytes=bm.MAX_FOTO + 1)  # groot origineel: alleen de thumbnail (fiets-2)
            for _ in range(6):
                maak({q})
        for q in bm.ANDERE.values():
            maak({q, cat["hond"]})  # beeldt ook een hond af: geen andere foto
            maak({q, bm.MENS})
            for _ in range(6):
                maak({q}, P275=set(), P6216={"Q19652"})
        # de thumbnails van boot nog op upload (de oude URL's werken nog; zie bm.THUMB)
        self.oud = {i for i, d in self.bestanden.items() if d["P180"] == {cat["boot"]}}

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

    def antwoord(self, q: dict) -> dict | None:
        if self.weg(q):
            return None
        if q.get("list") == "search":
            return self._zoek(q)
        props = q.get("prop", "").split("|")
        if "imageinfo" in props:
            pages = []
            doc: dict = {}
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
                if "iiurlwidth" in q:
                    w = int(q["iiurlwidth"])
                    if d["breedte"] > w:
                        info["thumburl"] = self.duim_url(i, w)
                        info["thumbwidth"] = w
                        info["thumbheight"] = round(d["hoogte"] * w / d["breedte"])
                    else:
                        info["thumburl"] = info["url"]
                        info["thumbwidth"], info["thumbheight"] = d["breedte"], d["hoogte"]
                pagina = {"pageid": i, "imageinfo": [info]}
                # prop=templates: alleen de gevraagde (tltemplates), zoals MediaWiki; van een
                # bestand in self.tl_later komen ze pas na tlcontinue
                if "templates" in props:
                    if i in self.tl_later:
                        doc["continue"] = {"tlcontinue": f"{i}|10|Artwork", "continue": "||"}
                    else:
                        wil = set(q.get("tltemplates", "").split("|"))
                        pagina["templates"] = [
                            {"ns": 10, "title": t} for t in sorted(d["sjablonen"] & wil)
                        ]
                pages.append(pagina)
            doc["query"] = {"pages": pages}
            return doc
        if q.get("action") == "wbgetentities":
            ent = {}
            for m in q["ids"].split("|"):
                d = self.bestanden[int(m[1:])]
                st = {}
                for p in ("P180", "P275", "P6216", "P31", "P7482"):
                    st[p] = [
                        {
                            "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": v}}},
                            "rank": "preferred" if v in d["prominent"] else "normal",
                        }
                        for v in sorted(d[p])
                    ]
                for p in sorted(d["eigen"]):
                    waarde = "SK-A-1" if p == "P217" else {"id": "Q190804"}
                    st[p] = [
                        {
                            "mainsnak": {"snaktype": "value", "datavalue": {"value": waarde}},
                            "rank": "normal",
                        }
                    ]
                ent[m] = {"id": m, "statements": st}
            return {"entities": ent}
        return {"error": "onbekend"}

    def duim_url(self, i: int, w: int = 1280) -> str:
        """De thumburl zoals de API hem geeft: op thumb (/duim/), van boot nog op upload."""
        host = "upload" if i in self.oud else "duim"
        return f"{self.url}/{host}/thumb/{i}.jpg/{w}px-{i}.jpg"

    def gehaald(self, i: int) -> tuple[str, bytes]:
        """Wat set van bestand i hoort op te halen: de thumbnail, of het origineel als dat niet
        breder is dan DUIM_BREEDTE."""
        if i in self.duim:
            return self.duim_url(i), self.duim[i]
        return f"{self.url}/upload/{i}.jpg", self.inhoud[i]


@pytest.fixture
def commons(nep):
    c = NepCommons(nep.url)
    nep.commons = c
    for i, data in c.inhoud.items():
        nep.zet(f"/upload/{i}.jpg", data)
    for i, data in c.duim.items():
        pad = urllib.parse.urlsplit(c.duim_url(i)).path
        nep.zet(pad, data)
        if pad.startswith("/duim/"):
            nep.webp[pad] = _webp(len(data))
    return c


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB".replace(".", ",")


def test_set_zonder_beeld_tsv_stelt_120_fotos_voor(nep, commons, werk, tmp_path, monkeypatch):
    """CI op 9ebf298: upload.wikimedia.org gaf ook met BANDBREEDTE HTTP 429 op een origineel (de
    stap stopte na ±30 minuten). Nu vraagt details de thumbnail van DUIM_BREEDTE (iiurlwidth), en
    haalt set van elke foto die thumbnail (url, bytes en sha256 in beeld.tsv), of het origineel
    als dat niet breder is; nooit een groot origineel. De bron blijft de pagina van het
    origineel. De thumbnails komen van thumb.wikimedia.org (CI op 45bb926), die van boot nog van
    de oude plek op upload; elke download als JPEG (Accept)."""
    monkeypatch.setattr(bm.time, "monotonic", lambda: 1000.0)
    assert bm.set_(werk, tmp_path / "beeld.tsv") == bm.VOORSTEL
    fotos = bm.lees_set(werk.voorstel_set.read_text())
    assert len(fotos) == 120
    per_url = {commons.gehaald(i)[0]: i for i in commons.bestanden}
    for f in fotos:
        i = per_url[f.url]
        d = commons.bestanden[i]
        assert bm.MENS not in d["P180"]
        assert d["mime"] == "image/jpeg" and d["breedte"] >= bm.MIN_ZIJDE
        assert d["P275"] == {"Q6938433"} or d["P6216"] == {"Q19652"}
        assert f.licentie == ("CC0" if d["P275"] else "publiek domein")
        cats = {c for c, q in bm.CATEGORIEEN.items() if q in d["P180"]}
        assert set(f.categorieen) == cats
        data = commons.gehaald(i)[1]
        assert (werk.set / f"{f.sleutel}.jpg").read_bytes() == data
        assert f.sha256 == _sha(data) and f.bytes == len(data)
        assert f.bron == f"https://commons.wikimedia.org/wiki/File:Nep_{i}.jpg"
    # elke vraag om imageinfo wil de thumbnail van 1280 breed, de kleinste vaste maat ≥ 1024
    info = [
        dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(p).query))
        for p, _ in nep.vragen
        if "prop=imageinfo" in p
    ]
    assert info and all(q["iiurlwidth"] == "1280" for q in info)
    # alleen thumbnails van 1280 (van thumb, en van boot nog van upload), en van upload het ene
    # origineel dat niet breder is (fiets-1); het grote origineel van fiets-2 (meer dan MAX_FOTO)
    # kwam als thumbnail
    per_sleutel = {f.sleutel: f for f in fotos}
    smal = per_url[per_sleutel["fiets-1"].url]
    assert commons.bestanden[smal]["breedte"] == 1000 and smal not in commons.duim
    assert per_sleutel["fiets-1"].url == f"{nep.url}/upload/{smal}.jpg"
    groot = per_url[per_sleutel["fiets-2"].url]
    assert commons.bestanden[groot]["bytes"] > bm.MAX_FOTO
    assert per_sleutel["fiets-2"].url == commons.duim_url(groot)
    paden = [urllib.parse.urlsplit(p).path for p, _ in nep.vragen]
    duim = [p for p in paden if p.startswith("/duim/")]
    upload = [p for p in paden if p.startswith("/upload/")]
    assert len(duim) == 115 and len(upload) == 5
    assert upload.count(f"/upload/{smal}.jpg") == 1
    assert all(p.startswith("/duim/thumb/") for p in duim)
    assert all(p.startswith("/upload/thumb/") for p in upload if p != f"/upload/{smal}.jpg")
    assert all(p.split("/")[-1].startswith("1280px-") for p in duim + upload if "/thumb/" in p)
    oud = [f.sleutel for f in fotos if f.url.startswith(f"{nep.url}/upload/thumb/")]
    assert oud == ["boot-1", "boot-2", "boot-3", "boot-4"]
    # de panorama (thumbnail 1280 x 213) is te laag voor 1024.webp en 256.webp
    assert not any(commons.bestanden[per_url[f.url]]["breedte"] == 6000 for f in fotos)
    sleutels = [f.sleutel for f in fotos]
    assert sleutels[:6] == [f"{a}+{b}" for a, b in bm.combinaties()]
    assert [f.categorieen for f in fotos[:6]] == [(a, b) for a, b in bm.combinaties()]
    assert sleutels.count("hond-1") == 1 and "hond-5" not in sleutels  # hond+strand telt mee
    assert "strand-5" in sleutels
    assert sum(1 for f in fotos if "zonsondergang" in f.categorieen) == 7  # 5 + 1 + boot
    assert len({f.url for f in fotos}) == 120
    # het voorstel staat ook in het log, regel voor regel, zodat een mens het kan overnemen; de
    # eerste regel zegt hoeveel er van upload kwam en hoe lang dat duurde, en het verkeer met
    # Commons: een voorstel zonder tekort is 30 zoekopdrachten van elk drie vragen aan de API (zie
    # _kandidaten)
    samen = sum(f.bytes for f in fotos)
    assert bm._opgehaald == samen
    verkeer = (
        "API: 90 vragen, 0 keer 429; thumb: 115 vragen, 0 keer 429; upload: 5 vragen, 0 keer 429; "
        "contact: nee"
    )
    assert verkeer == _verkeer(nep)
    assert _kort(werk) == [
        f"BZ4 set: beeld.tsv ontbreekt; voorstel met 120 foto's in het verslag "
        f"(1 als origineel; {_mb(samen)} in 0 s; {verkeer})",
        "BZ4 voorstel voor beeld.tsv:",
        *(f"  {r}" for r in werk.voorstel_set.read_text().splitlines()),
    ]
    _ua_algemeen(nep)


def test_set_met_beeld_tsv_is_streng(nep, commons, werk, tmp_path, monkeypatch):
    monkeypatch.setattr(bm.time, "monotonic", lambda: 1000.0)
    assert bm.set_(werk, tmp_path / "geen.tsv") == bm.VOORSTEL
    set_pad = tmp_path / "beeld.tsv"
    set_pad.write_text(werk.voorstel_set.read_text())
    samen = sum(f.bytes for f in bm.lees_set(set_pad.read_text()))
    werk2 = bm.Werk(tmp_path / "werk2")
    assert bm.set_(werk2, set_pad) == 0
    assert len(list(werk2.set.iterdir())) == 120
    assert _kort(werk2) == [
        "BZ4 set: 120 foto's kloppen met beeld.tsv (0 uit de cache, 120 van Commons, "
        f"1 als origineel; {_mb(samen)} in 0 s; "
        "API: 0 vragen; thumb: 115 vragen, 0 keer 429; upload: 5 vragen, 0 keer 429; contact: nee)"
    ]
    regels = set_pad.read_text().splitlines()
    i = next(n for n, r in enumerate(regels) if r.startswith("kat-2\t"))
    v = regels[i].split("\t")
    v[3] = "f" * 64
    regels[i] = "\t".join(v)
    set_pad.write_text("\n".join(regels) + "\n")
    with pytest.raises(bm.Fout, match="kat-2.jpg: sha256"):
        bm.set_(bm.Werk(tmp_path / "werk3"), set_pad)


def test_ci_op_45bb926_de_thumbnails_staan_op_thumb_wikimedia_org(nep, commons, werk, tmp_path):
    """CI op 45bb926 (run 43787944528): de API gaf 630 antwoorden zonder 429, maar het voorstel
    nam niets: "maar 0 bruikbare foto's voor <x> (400 kandidaten bekeken)" voor alle 24 en "FOUT
    set: Commons: 30 tekort(en) ... (0 van 120 foto's opgehaald, 0,0 MB; ...)". De thumburl van
    imageinfo staat sinds T427465 op thumb.wikimedia.org, en _duim nam alleen een thumbnail op
    upload. De nep geeft de thumburl nu zo (op /duim/, bm.THUMB); de tool neemt hem, met de URL
    precies zoals de API hem gaf, en de oude URL's op upload (boot) ook nog."""
    assert bm.set_(werk, tmp_path / "beeld.tsv") == bm.VOORSTEL
    fotos = bm.lees_set(werk.voorstel_set.read_text())
    gaf = {commons.gehaald(i)[0] for i in commons.bestanden}
    assert len(fotos) == 120 and all(f.url in gaf for f in fotos)
    op = [("thumb" if f.url.startswith(bm.THUMB) else "upload") for f in fotos]
    assert op.count("thumb") == 115 and op.count("upload") == 5
    assert not any("tekort" in r or "bruikbare" in r for r in _kort(werk))


def test_de_set_vraagt_elke_foto_als_jpeg(nep, commons, werk, tmp_path, monkeypatch):
    """thumb.wikimedia.org kiest het formaat op dezelfde URL naar de Accept (WebP met image/webp;
    zie bm.DUIM_BREEDTE), en de sha256 in beeld.tsv hoort bij vaste bytes. Elke download van de set
    vraagt daarom Accept: image/jpeg, in een voorstel en met beeld.tsv; de API niet. Een cache die
    de Vary mist en toch WebP geeft, is een fout met de sleutel, en die foto staat niet in set/."""
    monkeypatch.setattr(bm.time, "monotonic", lambda: 1000.0)
    assert bm.set_(werk, tmp_path / "geen.tsv") == bm.VOORSTEL
    set_pad = tmp_path / "beeld.tsv"
    set_pad.write_text(werk.voorstel_set.read_text())
    assert bm.set_(bm.Werk(tmp_path / "werk2"), set_pad) == 0
    downloads = 0
    for (pad, _), accept in zip(nep.vragen, nep.accept, strict=True):
        if pad.startswith(("/duim/", "/upload/")):
            downloads += 1
            assert accept == "image/jpeg", pad
        else:
            assert accept is None, pad
    assert downloads == 240
    assert all(p.read_bytes().startswith(JPEG) for p in werk.set.iterdir())
    # een cache die WebP geeft op de vraag om JPEG
    fotos = bm.lees_set(set_pad.read_text())
    pad = urllib.parse.urlsplit(fotos[0].url).path
    assert pad.startswith("/duim/")
    nep.paden[pad] = [(200, nep.webp[pad])]
    werk3 = bm.Werk(tmp_path / "werk3")
    with pytest.raises(bm.Fout) as fout:
        bm.set_(werk3, set_pad)
    assert str(fout.value).startswith(
        "auto+sneeuw.jpg: WebP in plaats van JPEG (bij foto 1 van 120, een thumbnail van 1280 px, "
    )
    assert list(werk3.set.iterdir()) == []


def test_streng_haalt_alleen_wat_ontbreekt_of_niet_klopt(nep, commons, werk, tmp_path, monkeypatch):
    """CI op f16ed5a: in CI staat set/ nu in een cache op de sha256 van beeld.tsv (de robotregels
    van Wikimedia: zo min mogelijk downloads). Een foto die er al staat met de juiste grootte en
    sha256, haalt set niet opnieuw; een die niet klopt, ontbreekt of een symlink is wel, via .part
    eroverheen. Het blok zegt hoeveel er uit de cache kwamen, en hoeveel bytes er van Commons
    kwamen."""
    monkeypatch.setattr(bm.time, "monotonic", lambda: 1000.0)
    assert bm.set_(werk, tmp_path / "geen.tsv") == bm.VOORSTEL
    set_pad = tmp_path / "beeld.tsv"
    set_pad.write_text(werk.voorstel_set.read_text())
    fotos = {f.sleutel: f for f in bm.lees_set(set_pad.read_text())}
    werk2 = bm.Werk(tmp_path / "werk2")
    werk2.set.mkdir()
    elders = tmp_path / "elders"
    elders.mkdir()
    for s in fotos:
        data = (werk.set / f"{s}.jpg").read_bytes()
        doel = werk2.set / f"{s}.jpg"
        if s == "kat-2":  # dezelfde grootte, een andere sha256
            doel.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        elif s == "hond-1":  # te kort
            doel.write_bytes(data[:-1])
        elif s == "boot-1":  # de juiste bytes, maar via een symlink
            (elders / "boot-1.jpg").write_bytes(data)
            doel.symlink_to(elders / "boot-1.jpg")
        elif s != "strand-3":  # strand-3 ontbreekt
            doel.write_bytes(data)
    opnieuw = ("kat-2", "hond-1", "boot-1", "strand-3")
    nep.vragen.clear()
    assert bm.set_(werk2, set_pad) == 0
    assert sorted(p for p, _ in nep.vragen) == sorted(
        urllib.parse.urlsplit(fotos[s].url).path for s in opnieuw
    )
    for s, f in fotos.items():
        doel = werk2.set / f"{s}.jpg"
        assert not doel.is_symlink() and bm.sha256_van(doel) == f.sha256, s
    assert (elders / "boot-1.jpg").read_bytes() == (werk.set / "boot-1.jpg").read_bytes()
    assert sorted(p.name for p in werk2.set.iterdir()) == sorted(f"{s}.jpg" for s in fotos)
    samen = sum(fotos[s].bytes for s in opnieuw)
    assert _kort(werk2) == [
        "BZ4 set: 120 foto's kloppen met beeld.tsv (116 uit de cache, 4 van Commons, "
        f"0 als origineel; {_mb(samen)} in 0 s; "
        "API: 0 vragen; thumb: 3 vragen, 0 keer 429; upload: 1 vraag, 0 keer 429; contact: nee)"
    ]


def _tekort(commons) -> None:
    """Haal uit de nep: alle foto's van kat+sneeuw, 4 van eten en 2 van gitaar (3 tekorten)."""
    cat = bm.CATEGORIEEN

    def goed(d, q, lic):
        return (
            d["P180"] == q
            and d["mime"] == "image/jpeg"
            and d["breedte"] == 1600
            and d["bytes"] <= bm.MAX_FOTO
            and (d["P275"] if lic == "CC0" else d["P6216"])
            == {"Q6938433" if lic == "CC0" else "Q19652"}
        )

    weg = [i for i, d in commons.bestanden.items() if goed(d, {cat["kat"], cat["sneeuw"]}, "PD")]
    weg += [i for i, d in commons.bestanden.items() if goed(d, {cat["eten"]}, "CC0")][:4]
    weg += [i for i, d in commons.bestanden.items() if goed(d, {bm.ANDERE["gitaar"]}, "PD")][:2]
    assert len(weg) == 7
    for i in weg:
        del commons.bestanden[i]


def test_een_tekort_stopt_de_keuze_niet_en_de_reserves_worden_geteld(nep, commons, werk, tmp_path):
    """CI op 625e946 stopte op de eerste combinatie zonder foto; nu staat elk tekort in het blok,
    met wat er bekeken is, en de reserveparen met hun aantal, en pas dan de fout."""
    _tekort(commons)
    with pytest.raises(bm.Fout, match=r"^Commons: 3 tekort\(en\) voor de set") as fout:
        bm.set_(werk, tmp_path / "beeld.tsv")
    assert not werk.voorstel_set.exists()
    # hoeveel er wel kwamen, niet "bij foto ...": de keuze was klaar
    gehaald = sorted(werk.set.iterdir())
    mb = _mb(sum(p.stat().st_size for p in gehaald))
    assert str(fout.value).endswith(
        f" ({len(gehaald)} van 120 foto's opgehaald, {mb}; {_verkeer(nep)})"
    )
    regels = _kort(werk)
    # met waarom de kandidaten afvielen (CI op 45bb926 zei dat niet): de nep geeft elk bestand
    # voor beide licenties, dus elk twee keer
    assert regels[:3] == [
        "BZ4 set: Commons: geen bruikbare foto voor kat+sneeuw (2 kandidaten bekeken; afgevallen: "
        "2 mens erop)",
        "BZ4 set: Commons: maar 2 bruikbare foto's voor eten (12 kandidaten bekeken; afgevallen: "
        "2 al gekozen, 2 geen image/jpeg, 2 licentie, 2 te groot, 2 om een andere reden)",
        "BZ4 set: Commons: maar 4 bruikbare foto's voor gitaar (anders; 12 bekeken; afgevallen: "
        "4 al gekozen, 2 beeldt een categorie af, 2 mens erop)",
    ]
    # berg+sneeuw staat al in de vragen; van de rest heeft de nep alleen zonsondergang+strand
    # (één bestand, ook al geeft de nep het voor beide licenties)
    assert regels[3:] == [
        "BZ4 reserve bos+sneeuw: 0 bruikbare foto's (eerste pagina per licentie)",
        "BZ4 reserve zonsondergang+strand: 1 bruikbare foto's (eerste pagina per licentie)",
        "BZ4 reserve boot+strand: 0 bruikbare foto's (eerste pagina per licentie)",
        "BZ4 reserve hond+sneeuw: 0 bruikbare foto's (eerste pagina per licentie)",
        "BZ4 reserve hond+bos: 0 bruikbare foto's (eerste pagina per licentie)",
    ]


def test_een_tekort_zegt_waarom_de_kandidaten_afvielen(nep, commons, werk, tmp_path, monkeypatch):
    """CI op 45bb926: "maar 0 bruikbare foto's voor strand (400 kandidaten bekeken)" zei niet
    waarom. Nu staan de redenen erbij (_afgevallen: de vier vaakste, de rest samen), ook bij de
    reserves. Hier de toestand van 45bb926 van de andere kant: de tool verwacht de thumbnails op
    https://thumb.wikimedia.org/, de API (de nep) geeft ze elders (127.0.0.1): elke thumbnail
    valt af als "thumbnail op een andere host", met de host maar nooit de URL. De oude URL's op
    upload (boot) en het smalle origineel (fiets-1) komen nog door."""
    monkeypatch.setattr(bm, "THUMB", "https://thumb.wikimedia.org/", raising=False)
    with pytest.raises(bm.Fout, match=r"^Commons: 29 tekort\(en\) voor de set"):
        bm.set_(werk, tmp_path / "beeld.tsv")
    regels = _kort(werk)
    elders = "thumbnail op een andere host (127.0.0.1)"
    assert regels[0] == (
        "BZ4 set: Commons: geen bruikbare foto voor auto+sneeuw (4 kandidaten bekeken; afgevallen: "
        f"2 mens erop, 2 {elders})"
    )
    assert (
        "BZ4 set: Commons: maar 0 bruikbare foto's voor auto (24 kandidaten bekeken; afgevallen: "
        f"14 {elders}, 2 geen image/jpeg, 2 licentie, 2 mens erop, 4 om een andere reden)"
    ) in regels
    assert (
        "BZ4 set: Commons: maar 1 bruikbare foto's voor fiets (26 kandidaten bekeken; afgevallen: "
        f"16 {elders}, 2 geen image/jpeg, 2 licentie, 2 te groot, 3 om een andere reden)"
    ) in regels
    assert (
        "BZ4 set: Commons: maar 0 bruikbare foto's voor bloem (anders; 16 bekeken; afgevallen: "
        f"14 {elders}, 2 mens erop)"
    ) in regels
    assert (
        "BZ4 reserve zonsondergang+strand: 0 bruikbare foto's (eerste pagina per licentie; "
        f"afgevallen: 1 {elders})"
    ) in regels
    tekorten = [r for r in regels if r.startswith("BZ4 set: Commons: ")]
    assert len(tekorten) == 29 and not any(" voor boot (" in r for r in tekorten)
    assert all("; afgevallen: " in r for r in tekorten)
    assert not any(nep.url in r or "/duim/" in r for r in regels)
    assert sorted(p.name for p in werk.set.iterdir()) == [
        *(f"boot-{i}.jpg" for i in range(1, 6)),
        "fiets-1.jpg",
    ]


def test_een_fout_bij_het_tellen_van_de_reserves_zegt_hoeveel_er_kwamen(
    nep, commons, werk, tmp_path
):
    """De review van de reparatie, R6: een fout van de API van Commons terwijl set de reserves
    telt (de keuze is dan klaar, er wordt niets meer opgehaald), zei "bij foto 117 van 120". Nu
    zegt hij hoeveel foto's er kwamen."""
    _tekort(commons)
    cat = bm.CATEGORIEEN
    # bos+sneeuw is het eerste reservepaar (berg+sneeuw staat al in de vragen); het zoeken naar
    # dat paar gebeurt alleen bij de reserves
    paar = (f"haswbstatement:P180={cat['bos']}", f"haswbstatement:P180={cat['sneeuw']}")
    commons.weg = lambda q: q.get("list") == "search" and all(p in q["srsearch"] for p in paar)
    with pytest.raises(bm.Fout) as fout:
        bm.set_(werk, tmp_path / "beeld.tsv")
    gehaald = sorted(werk.set.iterdir())
    mb = _mb(sum(p.stat().st_size for p in gehaald))
    assert len(gehaald) == 116  # 120, min 3 van eten en 1 van gitaar
    assert str(fout.value) == (
        f"127.0.0.1: HTTP 404 ({len(gehaald)} van 120 foto's opgehaald, {mb}; {_verkeer(nep)})"
    )
    assert [r.split(" (")[0] for r in _kort(werk)] == [
        "BZ4 set: Commons: geen bruikbare foto voor kat+sneeuw",
        "BZ4 set: Commons: maar 2 bruikbare foto's voor eten",
        "BZ4 set: Commons: maar 4 bruikbare foto's voor gitaar",
    ]


def test_een_set_die_misgaat_zegt_hoe_ver_hij_kwam(nep, commons, werk, tmp_path, monkeypatch):
    """CI op 9ebf298: "FOUT set: kat-1.jpg: HTTP 429" zei niet hoe ver de stap was toen hij na
    ±30 minuten stopte. Nu zegt de fout bij welke foto set was, of hij een thumbnail of een
    origineel ophaalde (de review van de reparatie, R3: zo zegt de volgende run of thumbnails de
    429 voorkomen) en hoeveel bytes er al binnen waren (in een voorstel van het doel van 120), en
    de regel van een goede set hoeveel er als origineel kwamen, de bytes en de tijd."""
    klok = [1000.0]
    slapen: list[float] = []

    def slaap(s: float) -> None:
        slapen.append(s)
        klok[0] += s

    monkeypatch.setattr(bm.time, "monotonic", lambda: klok[0])
    monkeypatch.setattr(bm.time, "sleep", slaap)
    # een voorstel dat lukt: de bytes en de tijd (de nepklok loopt alleen door de pauzes en de rem)
    assert bm.set_(werk, tmp_path / "geen.tsv") == bm.VOORSTEL
    fotos = bm.lees_set(werk.voorstel_set.read_text())
    samen = sum(f.bytes for f in fotos)
    assert bm._opgehaald == samen
    assert _kort(werk)[0] == (
        "BZ4 set: beeld.tsv ontbreekt; voorstel met 120 foto's in het verslag "
        f"(1 als origineel; {_mb(samen)} in {sum(slapen):.0f} s; "
        "API: 90 vragen, 0 keer 429; thumb: 115 vragen, 0 keer 429; upload: 5 vragen, 0 keer 429; "
        "contact: nee)"
    )
    assert sum(slapen) >= samen / bm.BANDBREEDTE
    assert sum(slapen) >= 89 * bm.PAUZE_API_S  # 90 vragen aan de API, 8 s ertussen
    # een voorstel dat bij de 37e foto (een thumbnail) een 404 krijgt (de 36 ervoor staan er al)
    assert bm._is_duim(fotos[36].url) and bm._is_duim(fotos[49].url)
    pad = urllib.parse.urlsplit(fotos[36].url).path
    nep.paden[pad] = [(404, b"weg")]
    werk2 = bm.Werk(tmp_path / "werk2")
    van = len(nep.vragen)
    with pytest.raises(bm.Fout) as fout:
        bm.set_(werk2, tmp_path / "geen.tsv")
    voor = sum(f.bytes for f in fotos[:36])
    assert str(fout.value) == (
        f"{fotos[36].sleutel}.jpg: HTTP 404 "
        f"(bij foto 37 van 120, een thumbnail van 1280 px, {_mb(voor)} opgehaald; "
        f"{_verkeer(nep, van=van)})"
    )
    assert "; thumb: 36 vragen, 0 keer 429; upload: 1 vraag, 0 keer 429; contact: nee)" in str(
        fout.value
    )
    assert len(list(werk2.set.iterdir())) == 36
    nep.zet(pad, (werk.set / f"{fotos[36].sleutel}.jpg").read_bytes())  # de 37e is er weer
    # een voorstel dat op het ene smalle origineel (fiets-1) de 429 van Wikimedia krijgt, met een
    # Retry-After boven MAX_WACHT_S: zo ziet het log het, ook in een echte run
    k = next(i for i, f in enumerate(fotos) if not bm._is_duim(f.url))
    assert fotos[k].sleutel == "fiets-1"
    origineel = urllib.parse.urlsplit(fotos[k].url).path
    nep.paden[origineel] = [(429, WIKIMEDIA_429, {"Retry-After": "1000"})]
    van, begin = len(nep.vragen), klok[0]
    with pytest.raises(bm.Fout) as fout:
        bm.set_(bm.Werk(tmp_path / "werk4"), tmp_path / "geen.tsv")
    voor = sum(f.bytes for f in fotos[:k])
    assert str(fout.value) == (
        "fiets-1.jpg: HTTP 429 (Retry-After 1000; langer dan MAX_WACHT_S (600 s), dus meteen "
        f"gestopt; antwoord: {WIKIMEDIA_REDEN}) "
        f"(bij foto {k + 1} van 120, een origineel, {_mb(voor)} opgehaald; "
        f"{_verkeer(nep, van=van, begin=begin)})"
    )
    assert f"; thumb: {k} vragen, 0 keer 429; upload: 1 vraag, 1 keer 429, eerste 429 na " in str(
        fout.value
    )
    nep.zet(origineel, (werk.set / "fiets-1.jpg").read_bytes())
    # streng: Wikimedia maakte de 50e thumbnail opnieuw, met andere bytes van dezelfde lengte
    # (nog steeds een JPEG: de eerste vier bytes blijven)
    set_pad = tmp_path / "beeld.tsv"
    set_pad.write_text(werk.voorstel_set.read_text())
    pad = urllib.parse.urlsplit(fotos[49].url).path
    oud = (werk.set / f"{fotos[49].sleutel}.jpg").read_bytes()
    nep.paden[pad] = [(200, oud[:4] + bytes(b ^ 0xFF for b in oud[4:]))]
    with pytest.raises(bm.Fout) as fout:
        bm.set_(bm.Werk(tmp_path / "werk3"), set_pad)
    voor = sum(f.bytes for f in fotos[:50])
    assert str(fout.value).startswith(f"{fotos[49].sleutel}.jpg: sha256 ")
    assert str(fout.value).endswith(
        f" (bij foto 50 van 120, een thumbnail van 1280 px, {_mb(voor)} opgehaald; "
        "API: 0 vragen; thumb: 49 vragen, 0 keer 429; upload: 1 vraag, 0 keer 429; contact: nee)"
    )
    assert fotos[49].sha256 in str(fout.value)


def _nepklok(monkeypatch) -> tuple[list[float], list[float]]:
    """Een nepklok die alleen door slapen verder loopt: (klok, slapen)."""
    klok = [1000.0]
    slapen: list[float] = []

    def slaap(s: float) -> None:
        slapen.append(s)
        klok[0] += s

    monkeypatch.setattr(bm.time, "monotonic", lambda: klok[0])
    monkeypatch.setattr(bm.time, "sleep", slaap)
    return klok, slapen


def test_een_voorstel_vraagt_de_api_hooguit_8_keer_per_minuut(
    nep, commons, werk, tmp_path, monkeypatch
):
    """CI op 3148c2f: zonder contact in de User-Agent staat Wikimedia van één IP 10 vragen per
    minuut aan de API toe (zie PAUZE_API_S), en set vroeg er één per seconde. Een heel voorstel op
    de nepklok, met af en toe een 429 (Retry-After 5, korter dan de pauze): tussen twee vragen aan
    de API, ook een nieuwe poging, minstens 8 s, en dus in geen enkel venster van 60 s meer dan
    8 (onder de 9, en onder de 10 van Wikimedia). De regel van set zegt hoeveel vragen en 429's
    er waren, en na hoeveel seconden de eerste 429 kwam."""
    _, slapen = _nepklok(monkeypatch)
    n = [0]

    def druk(q):
        n[0] += 1
        if n[0] % 10 == 3:  # de 3e, 13e, ..., 93e vraag aan de API
            return 429, b"<title>Too many requests</title>", {"Retry-After": "5"}
        return None

    commons.druk = druk
    assert bm.set_(werk, tmp_path / "beeld.tsv") == bm.VOORSTEL
    assert len(bm.lees_set(werk.voorstel_set.read_text())) == 120
    tijden = _api_tijden(nep)
    assert len(tijden) == 100  # 90 vragen en 10 nieuwe pogingen
    assert _kortste(tijden) >= bm.PAUZE_API_S
    assert _per_minuut(tijden) <= 8
    assert bm._gewacht == 10 * 5.0
    verkeer = (
        "API: 100 vragen, 10 keer 429, eerste 429 na 16 s; thumb: 115 vragen, 0 keer 429; "
        "upload: 5 vragen, 0 keer 429; contact: nee"
    )
    assert verkeer == _verkeer(nep, begin=1000.0)
    assert _kort(werk)[0].endswith(f" in {sum(slapen):.0f} s; {verkeer})")


def test_een_api_die_429_blijft_zeggen_staat_met_de_tellers_in_de_fout(
    nep, commons, werk, tmp_path, monkeypatch
):
    """CI op 3148c2f: "FOUT set: commons.wikimedia.org: HTTP 429 (Retry-After 50; 50 s wachten
    past niet in wat er over is van WACHT_TOTAAL_S (32 van 900 s), dus meteen gestopt; antwoord:
    You are making too many requests to the API.) (bij foto 1 van 120, 0,0 MB opgehaald)" zei niet
    hoeveel vragen de API had gekregen, hoeveel daarvan 429 waren, en sinds wanneer. Hier zegt de
    API op elke tweede vraag 429 met Retry-After 50, tot WACHT_TOTAAL_S op is: de fout zegt nu ook
    het verkeer, en ook hier zit tussen twee vragen aan de API minstens PAUZE_API_S."""
    _nepklok(monkeypatch)
    n = [0]
    weigering = (429, b"You are making too many requests to the API.\n", {"Retry-After": "50"})

    def druk(q):
        n[0] += 1
        return weigering if n[0] % 2 == 0 else None

    commons.druk = druk
    with pytest.raises(bm.Fout) as fout:
        bm.set_(werk, tmp_path / "beeld.tsv")
    mb = _mb(sum(p.stat().st_size for p in werk.set.iterdir()))
    # 19 vragen gingen goed: 6 combinaties van 3 vragen, en de eerste van de zevende foto
    assert str(fout.value) == (
        "127.0.0.1: HTTP 429 (Retry-After 50; 50 s wachten past niet in wat er over is van "
        "WACHT_TOTAAL_S (0 van 900 s), dus meteen gestopt; antwoord: You are making too many "
        f"requests to the API.) (bij foto 7 van 120, {mb} opgehaald; API: 38 vragen, 19 keer "
        "429, eerste 429 na 8 s; thumb: 6 vragen, 0 keer 429; upload: 0 vragen; contact: nee)"
    )
    assert str(fout.value).endswith(f"; {_verkeer(nep, begin=1000.0)})")
    tijden = _api_tijden(nep)
    assert _kortste(tijden) >= bm.PAUZE_API_S
    assert bm._gewacht == bm.WACHT_TOTAAL_S == 900.0


def test_een_5xx_telt_apart_en_een_tweede_set_telt_vanaf_zijn_eigen_begin(
    nep, commons, werk, tmp_path, monkeypatch
):
    """De tellers per dienst tellen ook de 5xx ("… keer 5xx", alleen als die er waren), en ook na
    een 5xx zit tussen twee vragen aan de API minstens PAUZE_API_S. Een tweede set in hetzelfde
    proces zegt alleen zijn eigen verkeer: geen 5xx van de eerste, en de eerste 429 gemeten vanaf
    zijn eigen begin, op zijn eigen eerste 429 (de review van de reparatie, R1)."""
    klok, slapen = _nepklok(monkeypatch)
    n = [0]
    weigeringen = {
        3: (503, b"", {"Retry-After": "3"}),
        5: (502, b"", {}),
        9: (429, b"", {"Retry-After": "5"}),
    }

    def druk(q):
        n[0] += 1
        return weigeringen.get(n[0])

    commons.druk = druk
    # de eerste foto (auto+sneeuw, de thumbnail van bestand 1002) geeft eerst een 503
    eerste = urllib.parse.urlsplit(commons.duim_url(1002)).path
    nep.paden[eerste].insert(0, (503, b"", {"Retry-After": "1"}))
    assert bm.set_(werk, tmp_path / "beeld.tsv") == bm.VOORSTEL
    assert bm.lees_set(werk.voorstel_set.read_text())[0].url == commons.duim_url(1002)
    assert _kortste(_api_tijden(nep)) >= bm.PAUZE_API_S
    verkeer = (
        "API: 93 vragen, 1 keer 429, 2 keer 5xx, eerste 429 na 64 s; "
        "thumb: 116 vragen, 0 keer 429, 1 keer 5xx; upload: 5 vragen, 0 keer 429; contact: nee"
    )
    assert verkeer == _verkeer(nep, begin=1000.0)
    assert _kort(werk)[0].endswith(f" in {sum(slapen):.0f} s; {verkeer})")
    # een tweede set in hetzelfde proces (de tellers lopen door): een 429 op zijn vierde vraag
    n[0] = 0
    weigeringen.clear()
    weigeringen[4] = (429, b"", {"Retry-After": "5"})
    van, begin, voor = len(nep.vragen), klok[0], sum(slapen)
    laatste = _api_tijden(nep)[-1]
    werk2 = bm.Werk(tmp_path / "werk2")
    assert bm.set_(werk2, tmp_path / "beeld.tsv") == bm.VOORSTEL
    tweede = _verkeer(nep, van=van, begin=begin)
    # de eerste vraag aan de API wacht nog de rest van de pauze na de laatste van de eerste set;
    # de 429 is de vierde, 24 s later
    tijden = [t for t in _api_tijden(nep) if t >= begin]
    assert begin < tijden[0] == laatste + bm.PAUZE_API_S and tijden[3] == tijden[0] + 24.0
    assert round(tijden[3] - begin) == 28
    assert tweede == (
        "API: 91 vragen, 1 keer 429, eerste 429 na 28 s; thumb: 115 vragen, 0 keer 429; "
        "upload: 5 vragen, 0 keer 429; contact: nee"
    )
    assert _kort(werk2)[0].endswith(f" in {sum(slapen) - voor:.0f} s; {tweede})")
    api = bm._tellers["API"]
    assert (api.vragen, api.n429, api.n5xx) == (184, 2, 2)


def _stop_bij_de_zevende_vraag(commons) -> None:
    """Vanaf de zevende vraag aan de API zegt de nep 404: set stopt dan snel, bij foto 3."""
    n = [0]

    def weg(q):
        n[0] += 1
        return n[0] >= 7

    commons.weg = weg


def _set(werk, tmp_path) -> int:
    return bm.main(["set", "--werk", str(werk.map), "--set", str(tmp_path / "geen.tsv")])


@pytest.mark.parametrize("waarde", [None, ""], ids=["niet gezet", "leeg"])
def test_zonder_beeld_contact_gaat_er_geen_contact_mee(
    nep, commons, werk, tmp_path, monkeypatch, capsys, waarde
):
    """Zonder BEELD_CONTACT (niet gezet of leeg) is de User-Agent precies UA, zonder persoon,
    e-mail of repository; de API krijgt hooguit één vraag per PAUZE_API_S; het log zegt "contact:
    nee". Waar ci.yml BEELD_CONTACT wel zet (alleen BZ1 en BZ4 set, met toestemming van de
    eigenaar), kijkt test_beeld_ci.py na."""
    if waarde is not None:
        monkeypatch.setenv("BEELD_CONTACT", waarde)
    _nepklok(monkeypatch)
    _stop_bij_de_zevende_vraag(commons)
    assert _set(werk, tmp_path) == 1
    assert bm.UA == "beeld-model-meting/1.0 (CI van een zelf-gehost fotoplatform)"
    _ua_algemeen(nep)
    uit = capsys.readouterr().out
    assert (
        "BZ4 set: de API van Commons hooguit één vraag per 8 s, thumb één per 1 s, upload één per "
        "1 s; contact: nee\n"
    ) in uit
    mb = _mb(sum(p.stat().st_size for p in werk.set.iterdir()))
    assert _kort(werk) == [
        f"FOUT set: 127.0.0.1: HTTP 404 (bij foto 3 van 120, {mb} opgehaald; "
        "API: 7 vragen, 0 keer 429; thumb: 2 vragen, 0 keer 429; upload: 0 vragen; contact: nee)"
    ]
    tijden = _api_tijden(nep)
    assert _kortste(tijden) == bm.PAUZE_API_S


GELDIG = [
    "https://example.org/beeld-model",
    # de vorm die ci.yml meestuurt: ${{ github.server_url }}/${{ github.repository }}
    "https://github.com/Eigenaar-1/een_repo.met-punten",
    "https://beeld.example.org:8443/over?wie=ci#contact",
    "beeld-model+ci@example.org",
    "https://example.org/" + "a" * 180,  # precies MAX_CONTACT tekens
]


@pytest.mark.parametrize(
    "contact", GELDIG, ids=["URL", "repository op GitHub", "URL met poort", "e-mail", "200 tekens"]
)
def test_met_beeld_contact_alleen_naar_commons_en_nooit_in_het_log(
    nep, commons, werk, tmp_path, monkeypatch, capsys, contact
):
    """Met een BEELD_CONTACT die klopt: de User-Agent in de vorm van de richtlijn van Wikimedia
    ("naam/versie (contact) ..."), maar alleen naar Commons (de API, thumb en upload), nooit naar
    Hugging Face of een andere host; de API dan op PAUZE_S (de grens met contact is 200 per
    minuut). Het
    contact staat nergens in het log, niet in kort.txt en niet in een ander bestand van de
    werkmap: alleen "contact: ja"."""
    assert len(GELDIG[-1]) == bm.MAX_CONTACT == 200
    monkeypatch.setenv("BEELD_CONTACT", contact)
    _nepklok(monkeypatch)
    assert bm.main(["bereik", "--werk", str(werk.map)]) == 0
    _stop_bij_de_zevende_vraag(commons)
    assert _set(werk, tmp_path) == 1
    met = f"beeld-model-meting/1.0 ({contact}; CI van een zelf-gehost fotoplatform)"
    assert met == bm.UA_MET_CONTACT.format(contact=contact)
    anders = 0
    for pad, ua in nep.vragen:
        if pad.startswith(("/w/api.php", "/duim/", "/upload/")):
            assert ua == met, pad
        else:
            assert ua == bm.UA, pad
            anders += 1
    assert anders == 2  # Hugging Face en NASA in bereik
    tijden = _api_tijden(nep)
    assert _kortste(tijden) == bm.PAUZE_S
    uit, fout = capsys.readouterr()
    assert (
        "BZ4 set: de API van Commons hooguit één vraag per 1 s, thumb één per 1 s, upload één per "
        "1 s; contact: ja\n"
    ) in uit
    assert _kort(werk)[-1].endswith(
        "; API: 7 vragen, 0 keer 429; thumb: 2 vragen, 0 keer 429; upload: 0 vragen; contact: ja)"
    )
    teksten = [uit, fout] + [
        p.read_text(encoding="utf-8", errors="replace")
        for p in werk.map.rglob("*")
        if p.is_file() and p.suffix != ".jpg"
    ]
    assert len(teksten) >= 3
    for tekst in teksten:
        assert contact not in tekst
        assert "example.org" not in tekst
        assert "Eigenaar-1" not in tekst and "een_repo" not in tekst


HERHAALD = (
    "Too many requests from beeld-model-meting/1.0 ([contact]; CI van een zelf-gehost fotoplatform)"
)


def _herhaalt(contact: str, vorm: str) -> tuple[bytes, str]:
    """Een 429 van de API die de User-Agent herhaalt (met BEELD_CONTACT erin), en wat de fout dan
    als antwoord hoort te zeggen."""
    ua = bm.UA_MET_CONTACT.format(contact=contact)
    if vorm == "titel":
        body = f"<html><head><title>Too many requests from {ua}</title></head></html>"
        return body.encode(), HERHAALD
    if vorm == "html":  # de foutpagina van Wikimedia, met de User-Agent HTML-gecodeerd
        body = (
            "<!DOCTYPE html><html><head><title>Wikimedia Error</title></head><body><h1>Error</h1>"
            f"<p>Too many requests from {html.escape(ua)}</p><details>{ua}</details></body></html>"
        )
        return body.encode(), HERHAALD
    # afgekapt: na MAX_ANTWOORD bytes staan er nog 12 tekens van het contact
    kop, voor = "<style>", "</style><p>Too many requests from beeld-model-meting/1.0 ("
    vulling = "x" * (bm.MAX_ANTWOORD - 12 - len(kop) - len(voor))
    body = f"{kop}{vulling}{voor}{contact}; CI van een zelf-gehost fotoplatform)</p>".encode()
    assert body[: bm.MAX_ANTWOORD].endswith(contact[:12].encode())
    return body, "Too many requests from beeld-model-meting/1.0 ([contact]"


@pytest.mark.parametrize(
    ("contact", "vorm"),
    [
        ("beeld-model+ci@example.org", "titel"),
        ("https://example.org/over?wie=ci&van=beeld", "html"),
        ("https://example.org/beeld-model", "afgekapt"),
    ],
    ids=["in de titel", "HTML-gecodeerd in de eerste p", "afgekapt na MAX_ANTWOORD"],
)
def test_een_antwoord_dat_de_user_agent_herhaalt_zet_het_contact_niet_in_het_log(
    nep, commons, werk, tmp_path, monkeypatch, capsys, contact, vorm
):
    """De uitleg van een opgegeven 429 neemt tekst van de server over (_uitleg). Een foutpagina
    die de User-Agent herhaalt, zette zo BEELD_CONTACT in de fout en in kort.txt. Nu staat er
    "[contact]": ook als de pagina het HTML-gecodeerd herhaalt ("&amp;"), en ook als het antwoord
    na MAX_ANTWOORD ophoudt midden in het contact (de review van de reparatie, R5)."""
    monkeypatch.setenv("BEELD_CONTACT", contact)
    _nepklok(monkeypatch)
    body, regel = _herhaalt(contact, vorm)
    commons.druk = lambda q: (429, body, {"Retry-After": "1000"})
    assert _set(werk, tmp_path) == 1
    # de nep kreeg precies de User-Agent die zijn antwoord herhaalt
    assert [ua for _, ua in nep.vragen] == [bm.UA_MET_CONTACT.format(contact=contact)]
    assert _kort(werk) == [
        "FOUT set: 127.0.0.1: HTTP 429 (Retry-After 1000; langer dan MAX_WACHT_S (600 s), dus "
        f"meteen gestopt; antwoord: {regel}) (bij foto 1 van 120, 0,0 MB opgehaald; API: 1 vraag, "
        "1 keer 429, eerste 429 na 0 s; thumb: 0 vragen; upload: 0 vragen; contact: ja)"
    ]
    uit, fout = capsys.readouterr()
    teksten = [uit, fout] + [
        p.read_text(encoding="utf-8", errors="replace") for p in werk.map.rglob("*") if p.is_file()
    ]
    for tekst in teksten:
        assert contact not in tekst and "exam" not in tekst


@pytest.mark.parametrize(
    ("waarde", "reden"),
    [
        ("https://example.org/" + "a" * 181, "langer dan 200 tekens"),
        ("beeld@example.org; ja", "alleen afdrukbare ASCII, zonder ; ( of )"),
        ("https://example.org/(beeld)", "alleen afdrukbare ASCII, zonder ; ( of )"),
        ("beeld@example.org) X-Mee: (ja", "alleen afdrukbare ASCII, zonder ; ( of )"),
        ("beeld@example.org\r\nX-Mee: ja", "alleen afdrukbare ASCII, zonder ; ( of )"),
        ("beeld@example.org\t", "alleen afdrukbare ASCII, zonder ; ( of )"),
        ("beeld@exämple.org", "alleen afdrukbare ASCII, zonder ; ( of )"),
        ("http://example.org/beeld", "geen https://-URL en geen e-mailadres"),
        ("https://example.org/ beeld", "geen https://-URL en geen e-mailadres"),
        ("https://localhost/beeld", "geen https://-URL en geen e-mailadres"),
        ("beeld@localhost", "geen https://-URL en geen e-mailadres"),
        ("Beeld Model", "geen https://-URL en geen e-mailadres"),
        ("ftp://example.org", "geen https://-URL en geen e-mailadres"),
    ],
)
def test_een_beeld_contact_dat_niet_klopt_gaat_nooit_weg(
    nep, commons, werk, tmp_path, monkeypatch, capsys, waarde, reden
):
    """BEELD_CONTACT wordt nagekeken vóór de eerste vraag: te lang, iets anders dan afdrukbare
    ASCII, ";", "(" of ")" (die breken de vorm van de User-Agent, en een regeleinde zou er een
    kopregel bij maken), of geen https://-URL en geen e-mailadres. Dan een fout, geen enkele
    vraag, ook niet in bereik, en de waarde staat niet in het log."""
    monkeypatch.setenv("BEELD_CONTACT", waarde)
    _stop_bij_de_zevende_vraag(commons)
    for stap in ("bereik", "set"):
        assert bm.main([stap, "--werk", str(werk.map), "--set", str(tmp_path / "geen.tsv")]) == 1
    assert nep.vragen == []
    assert _kort(werk) == [
        f"FOUT bereik: BEELD_CONTACT: {reden}",
        f"FOUT set: BEELD_CONTACT: {reden}",
    ]
    uit, fout = capsys.readouterr()
    for tekst in (uit, fout, werk.kort.read_text()):
        assert waarde not in tekst and "example.org" not in tekst
    assert bm._contact is None


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


THUMB_ECHT = "https://thumb.wikimedia.org/"
UPLOAD_ECHT = "https://upload.wikimedia.org/"
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
    "url elders": (
        lambda r: _vervang(r, "kat-1", "url", "https://elders.example/x.jpg"),
        rf"regel \d+: url hoort bij {THUMB_ECHT} of {UPLOAD_ECHT}",
    ),
    "thumbnail over http": (
        lambda r: _vervang(
            r,
            "kat-1",
            "url",
            "http://thumb.wikimedia.org/wikipedia/commons/thumb/a/F1.jpg/1280px-F1.jpg",
        ),
        r"regel \d+: url hoort bij",
    ),
    "thumbnail van 1024": (
        lambda r: _vervang(
            r, "kat-1", "url", f"{bm.UPLOAD}wikipedia/commons/thumb/a/ab/F1.jpg/1024px-F1.jpg"
        ),
        r"regel \d+: url is een thumbnail, maar niet 1280 px",
    ),
    "thumbnail van 1024 op thumb": (
        lambda r: _vervang(
            r, "kat-1", "url", f"{THUMB_ECHT}wikipedia/commons/thumb/a/ab/F1.jpg/1024px-F1.jpg"
        ),
        r"regel \d+: url is een thumbnail, maar niet 1280 px",
    ),
    # het pad van een origineel op thumb.wikimedia.org geeft een 301 naar commons.wikimedia.org
    "origineel op thumb": (
        lambda r: _vervang(r, "kat-1", "url", f"{THUMB_ECHT}wikipedia/commons/a/ab/F1.jpg"),
        rf"regel \d+: url op {THUMB_ECHT} is geen thumbnail; een origineel hoort bij {UPLOAD_ECHT}",
    ),
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
    # een thumbnail van 1280 breed mag (zoals set hem voorstelt): op thumb.wikimedia.org (zoals
    # de API hem sinds T427465 geeft), en de oude vorm op upload
    for duim in (
        f"{THUMB_ECHT}wikipedia/commons/thumb/a/ab/F7.jpg/1280px-F7.jpg",
        f"{bm.UPLOAD}wikipedia/commons/thumb/a/ab/F7.jpg/1280px-F7.jpg",
    ):
        fotos = bm.lees_set("\n".join(_vervang(_geldige_set(), "kat-1", "url", duim)) + "\n")
        assert next(f for f in fotos if f.sleutel == "kat-1").url == duim


@pytest.mark.parametrize("geval", sorted(KAPOTTE_SET))
def test_een_set_die_niet_klopt(geval):
    wijzig, fout = KAPOTTE_SET[geval]
    with pytest.raises(bm.Fout, match=fout):
        bm.lees_set("\n".join(wijzig(_geldige_set())) + "\n")


# De vaste maten van de thumbnails van Wikimedia ($wgThumbnailSteps; mediawiki.org, "Common
# thumbnail sizes", en phabricator T402792)
VASTE_MATEN = (20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840)
DUIM = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/F.jpg/1280px-F.jpg"


def _foto(**anders) -> dict:
    """Wat details van één bestand geeft: een origineel van 1600 x 1200 met zijn thumbnail."""
    d = {
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/F.jpg",
        "bytes": 2_000_000,
        "breedte": 1600,
        "hoogte": 1200,
        "mime": "image/jpeg",
        "bron": "https://commons.wikimedia.org/wiki/File:F.jpg",
        "duim": DUIM,
        "duim_breedte": 1280,
        "duim_hoogte": 960,
        "P180": {bm.CATEGORIEEN["kat"]},
        "P275": {"Q6938433"},
        "P6216": set(),
    }
    d.update(anders)
    return d


SMAL = "https://upload.wikimedia.org/wikipedia/commons/a/ab/F.jpg"  # het origineel zelf
# zo geeft de API de thumbnail sinds T427465 (zie bm.THUMB)
DUIM_THUMB = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/F.jpg/1280px-F.jpg"
GOEDE_FOTOS = {
    "gewoon": ({}, (DUIM, None)),
    "op thumb.wikimedia.org": ({"duim": DUIM_THUMB}, (DUIM_THUMB, None)),
    "liggend, korte kant 320": (
        {"breedte": 4000, "hoogte": 1000, "duim_hoogte": 320},
        (DUIM, None),
    ),
    "staand, 1280 x 5120": ({"breedte": 1600, "hoogte": 6400, "duim_hoogte": 5120}, (DUIM, None)),
    "groot origineel, de thumbnail": ({"bytes": 300 << 20}, (DUIM, None)),
    "niet breder: het origineel": (
        {"breedte": 1280, "hoogte": 853, "bytes": 900_000, "duim": SMAL, "duim_hoogte": 853},
        (SMAL, 900_000),
    ),
    # MediaWiki geeft bij een origineel dat niet breder is de maten van het origineel als
    # thumbwidth en thumbheight (ApiQueryImageInfo, T25834); _duim neemt die van het origineel
    # zelf, dus ook een thumbwidth die iets anders zegt, valt niet verkeerd uit (verdediging)
    "smal, thumbwidth van de vraag": (
        {"breedte": 1000, "hoogte": 800, "duim": SMAL, "duim_breedte": 1280, "duim_hoogte": 1024},
        (SMAL, 2_000_000),
    ),
}
SLECHTE_FOTOS = {
    "liggend, korte kant 319": {"breedte": 4013, "hoogte": 1000, "duim_hoogte": 319},
    "origineel op thumb als thumburl": {
        "breedte": 1000,
        "hoogte": 800,
        "duim": "https://thumb.wikimedia.org/wikipedia/commons/a/ab/F.jpg",
    },
    "origineel zelf op thumb": {
        "url": "https://thumb.wikimedia.org/wikipedia/commons/a/ab/F.jpg",
        "duim": "https://thumb.wikimedia.org/wikipedia/commons/a/ab/F.jpg",
        "breedte": 1000,
        "hoogte": 800,
    },
    "thumbnail op een vreemde host": {
        "duim": "https://ci:geheim@vreemd.example:8443/thumb/a/ab/F.jpg/1280px-F.jpg?x=1"
    },
    "thumbnail op een host met een liggend streepje": {
        "duim": "https://x_y.example/thumb/a/ab/F.jpg/1280px-F.jpg"
    },
    "thumbnail op een te lange host": {
        "duim": f"https://{'a' * 60}.example/thumb/a/ab/F.jpg/1280px-F.jpg"
    },
    "geen bestandspagina": {"bron": "https://elders.example/wiki/File:F.jpg"},
    "staand, 1280 x 5121": {"breedte": 1600, "hoogte": 6402, "duim_hoogte": 5121},
    "smal origineel groter dan MAX_FOTO": {
        "breedte": 1000,
        "hoogte": 800,
        "bytes": bm.MAX_FOTO + 1,
        "duim": SMAL,
    },
    "breed origineel als thumburl": {"duim": SMAL},
    "thumbnail elders": {"duim": "https://elders.example/thumb/a/ab/F.jpg/1280px-F.jpg"},
    "thumbnail 1024 breed": {"duim": DUIM.replace("1280px", "1024px"), "duim_breedte": 1024},
    "thumbwidth 1024": {"duim_breedte": 1024},
    "url van 1280 buiten /thumb/": {"duim": DUIM.replace("/thumb/", "/")},
    "geen thumbnail": {"duim": None},
    "spatie in de url": {"duim": DUIM.replace("F.jpg/", "F 1.jpg/")},
    "geen thumbheight": {"duim_hoogte": None},
    "korte kant origineel onder MIN_ZIJDE": {"hoogte": 639},
    "meer dan MAX_PIXELS": {"breedte": 10_000, "hoogte": 5_001},
    "geen JPEG": {"mime": "image/png"},
    "met een mens": {"P180": {bm.CATEGORIEEN["kat"], bm.MENS}},
    "CC BY": {"P275": {"Q20007257"}},
}


@pytest.mark.parametrize("geval", sorted(GOEDE_FOTOS))
def test_de_thumbnail_van_een_goede_foto(geval):
    """Van een bruikbare foto haalt set de thumbnail van 1280 breed (bytes vooraf onbekend), of
    het origineel als dat niet breder is (met zijn bytes). Een groot origineel is geen bezwaar
    meer: set haalt het nooit."""
    anders, uit = GOEDE_FOTOS[geval]
    d = _foto(**anders)
    assert bm._bruikbaar(d)
    assert bm._duim(d) == uit


@pytest.mark.parametrize("geval", sorted(SLECHTE_FOTOS))
def test_een_foto_zonder_goede_thumbnail_valt_af(geval):
    """Wat de set niet neemt: de controles op het origineel (licentie, JPEG, geen mens,
    MIN_ZIJDE, MAX_PIXELS), en een thumbnail die niet van thumb of upload komt, niet de vaste maat
    heeft, te klein is voor 1024.webp en 256.webp, of een origineel groter dan MAX_FOTO; een
    origineel alleen op upload."""
    assert not bm._bruikbaar(_foto(**SLECHTE_FOTOS[geval]))


ELDERS = "thumbnail op een andere host"
REDEN = {
    "liggend, korte kant 319": "te langgerekt",
    "staand, 1280 x 5121": "te langgerekt",
    "smal origineel groter dan MAX_FOTO": "te groot",
    "breed origineel als thumburl": "geen thumbnail van 1280 px",
    "thumbnail elders": f"{ELDERS} (elders.example)",
    "thumbnail op een vreemde host": f"{ELDERS} (vreemd.example)",
    "thumbnail op een host met een liggend streepje": f"{ELDERS} (een onbekende host)",
    "thumbnail op een te lange host": f"{ELDERS} (een onbekende host)",
    "thumbnail 1024 breed": "geen thumbnail van 1280 px",
    "thumbwidth 1024": "geen thumbnail van 1280 px",
    "url van 1280 buiten /thumb/": "geen thumbnail van 1280 px",
    "origineel op thumb als thumburl": "geen thumbnail van 1280 px",
    "origineel zelf op thumb": "origineel niet op upload",
    "geen thumbnail": "geen thumbnail van 1280 px",
    "spatie in de url": "onleesbare gegevens",
    "geen thumbheight": "onleesbare gegevens",
    "geen bestandspagina": "geen bestandspagina",
    "korte kant origineel onder MIN_ZIJDE": "te klein",
    "meer dan MAX_PIXELS": "te groot",
    "geen JPEG": "geen image/jpeg",
    "met een mens": "mens erop",
    "CC BY": "licentie",
}


@pytest.mark.parametrize("geval", sorted(SLECHTE_FOTOS))
def test_waarom_een_foto_afvalt(geval):
    """De reden (_waarom) is er een uit een vaste reeks (REDENEN); bij een thumbnail op een andere
    host met de host erbij, als het een gewone hostnaam is van hooguit 64 tekens (anders "een
    onbekende host"), nooit de URL (die kan van alles bevatten). Een goede foto heeft geen reden."""
    reden = bm._waarom(_foto(**SLECHTE_FOTOS[geval]))
    assert reden == REDEN[geval]
    assert reden.split(" (")[0] in bm.REDENEN
    assert "://" not in reden and "geheim" not in reden and "8443" not in reden
    assert sorted(REDEN) == sorted(SLECHTE_FOTOS)
    assert all(bm._waarom(_foto(**anders)) is None for anders, _ in GOEDE_FOTOS.values())


def test_afgevallen_noemt_de_vier_vaakste_redenen():
    """De regel van een tekort noemt hooguit MAX_REDENEN (4) redenen, de vaakste eerst en bij
    een gelijk aantal op de tekst (dus elke run in dezelfde volgorde), en de rest samen."""
    assert bm.MAX_REDENEN == 4
    assert bm._afgevallen({}) == ""
    assert bm._afgevallen({"te klein": 1}) == "; afgevallen: 1 te klein"
    redenen = {
        "te klein": 2,
        "licentie": 2,
        "mens erop": 5,
        "geen image/jpeg": 2,
        "te groot": 1,
        "al gekozen": 2,
    }
    verwacht = (
        "; afgevallen: 5 mens erop, 2 al gekozen, 2 geen image/jpeg, 2 licentie, "
        "3 om een andere reden"
    )
    assert bm._afgevallen(redenen) == verwacht
    assert bm._afgevallen(dict(reversed(list(redenen.items())))) == verwacht
    assert bm._afgevallen({"te klein": 3, "licentie": 3, "te groot": 3, "mens erop": 3}) == (
        "; afgevallen: 3 licentie, 3 mens erop, 3 te groot, 3 te klein"
    )


def test_de_maat_van_de_thumbnail():
    """DUIM_BREEDTE is de kleinste vaste maat ≥ 1024 (de lange kant van 1024.webp in de app);
    de grens op de korte kant volgt uit 1024.webp en de invoer van het model (256 × 256)."""
    assert bm.DUIM_BREEDTE == min(m for m in VASTE_MATEN if m >= 1024) == 1280
    assert bm.PREVIEW == render.SIZES[0] == 1024 and bm.INVOER == render.SIZES[1] == 256
    assert bm.REPO.endswith(f"-{bm.INVOER}")
    assert bm._genoeg(1280, 320) and not bm._genoeg(1280, 319)
    assert bm._genoeg(1280, 5120) and not bm._genoeg(1280, 5121)
    assert bm._genoeg(640, 640) and bm._genoeg(1000, 800)


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
