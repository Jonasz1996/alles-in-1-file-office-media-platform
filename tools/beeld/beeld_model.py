#!/usr/bin/env python3
"""Het echte beeldmodel en de kwaliteitsset ophalen en nakijken (fase 6, X3a; job beeld-model).

Alleen de standaardbibliotheek; draait op de runner met `python3 -I`, nooit in het image. Alles
wat hij ophaalt, is onvertrouwd: eerst naar een .part, dan grootte en sha256, en pas dan op zijn
plek. De User-Agent is algemeen: geen persoon, geen e-mail, geen repository.

    beeld_model.py bereik   --werk W              BZ1: per host bereikbaar of niet
    beeld_model.py bron     --werk W --lock L     de vijf bronbestanden van SigLIP 2 (BZ2)
    beeld_model.py vergelijk --werk W --lock L    twee omzettingen (en die met --fp32) en
                                                  models.lock (BZ2)
    beeld_model.py set      --werk W --set S      de 120 foto's van Keuze 83 (BZ4)
    beeld_model.py uitslag  --werk W              rood zolang er een voorstel ligt

De map W (in CI $RUNNER_TEMP/beeld) houdt alles bij elkaar: bron/, uit1/, uit2/, uitfp32/, set/,
de voorstellen en kort.txt, de regels voor het blok `=== kort ===` aan het eind van het log.

BZ2 zet twee keer om zoals de dienst analyse-omzetten (zonder --fp32): die twee moeten dezelfde
sha256 geven en elk maxrss ≤ 1,5 GB. Een derde omzetting met --fp32 is er alleen voor BZ4 (de
fp32-grafen, int8 tegen fp32): zijn maxrss staat in het log zonder grens (de fp32-embedding alleen
is al 786 MB), maar zijn vijf bestanden van UITVOER moeten dezelfde zijn als zonder --fp32.

Twee standen, zoals fotos.lock (het patroon van het ontwerp):
- models.lock heeft nog geen regels van SigLIP: `bron` zoekt de huidige commit van
  google/siglip2-base-patch32-256 op via de API van Hugging Face en haalt de vijf bestanden op
  die commit; `vergelijk` schrijft de voorgestelde regels (bron: sha256 bytes naam url op die
  commit; uitkomst: sha256 bytes naam -) naar voorstel-models.lock. Code 3.
- Met de regels: streng. Een andere sha256 of grootte is rood (code 1).
Zo ook de set: zonder api/tests/analyse/sets/beeld.tsv stelt `set` er een voor uit de
gestructureerde gegevens van Wikimedia Commons (voorstel-beeld.tsv) en haalt hij die foto's op,
elk als thumbnail van de vaste breedte DUIM_BREEDTE (1280 px), niet als origineel. Met beeld.tsv
haalt hij alleen wat in set/ ontbreekt of niet klopt (in CI staat set/ in een cache op de sha256
van beeld.tsv). Van upload.wikimedia.org gemiddeld hooguit BANDBREEDTE, en Retry-After altijd
gevolgd (de robotregels van Wikimedia). `uitslag` faalt met "models.lock mist de regels van
SigLIP" en "beeld.tsv ontbreekt" zolang er een voorstel ligt; een mens zet de regels erin, nooit
de job zelf, en die van SigLIP pas samen met X3b (de stap BEELD van install.sh; anders meldt de
container ze als ontbrekend).

Codes: 0 goed, 1 fout, 3 voorstel geschreven (de job gaat door, `uitslag` maakt hem rood).
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import http.client
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "api"
LOCK = API / "app" / "analyse" / "models.lock"
SET = API / "tests" / "analyse" / "sets" / "beeld.tsv"
VRAGEN = API / "tests" / "analyse" / "sets" / "beeld_vragen.tsv"

UA = "beeld-model-meting/1.0 (CI van een zelf-gehost fotoplatform)"
VOORSTEL = 3

REPO = "google/siglip2-base-patch32-256"
HF = "https://huggingface.co"
# Hoe groot een bronbestand hooguit mag zijn (de echte grootte komt daarna in models.lock)
MAX_BRON = {
    "model.safetensors": 2 << 30,  # 1,51 GB
    "config.json": 1 << 20,
    "preprocessor_config.json": 1 << 20,
    "tokenizer.json": 64 << 20,  # 34,4 MB
    "tokenizer_config.json": 16 << 20,
}
MAX_RSS_MIB = 1536  # BZ2: maxrss van één omzetting zoals de dienst (zonder --fp32) ≤ 1,5 GB

COMMONS = "https://commons.wikimedia.org/w/api.php"
UPLOAD = "https://upload.wikimedia.org/"
# BZ1 noemt ook NASA; de set komt alleen van Commons (Vraag 12a), dus alleen de regel in het blok
NASA = "https://images-assets.nasa.gov/"
# Keuze 83: twaalf categorieën met hun item op Wikidata (P180, "beeldt af")
CATEGORIEEN = {
    "strand": "Q40080",
    "hond": "Q144",
    "kat": "Q146",
    "sneeuw": "Q7561",
    "auto": "Q1420",
    "fiets": "Q11442",
    "berg": "Q8502",
    "bos": "Q4421",
    "stad": "Q515",
    "eten": "Q2095",
    "boot": "Q35872",
    "zonsondergang": "Q166564",
}
# De 60 andere foto's: 12 onderwerpen × 5, die geen van de twaalf afbeelden
ANDERE = {
    "bloem": "Q506",
    "vogel": "Q5113",
    "paard": "Q726",
    "trein": "Q870",
    "vliegtuig": "Q197",
    "brug": "Q12280",
    "kerk": "Q16970",
    "boek": "Q571",
    "klok": "Q376",
    "stoel": "Q15026",
    "gitaar": "Q6607",
    "insect": "Q1390",
}
MENS = "Q5"
# Heeft Commons voor een combinatie van beeld_vragen.tsv geen bruikbare foto, dan telt de job deze
# paren ook (alleen de eerste pagina per licentie, niets gedownload), zodat één run laat zien welke
# combinatie er wel is. Een paar dat al in de vragen staat, slaat hij over.
RESERVE_COMBIS = (
    ("berg", "sneeuw"),
    ("bos", "sneeuw"),
    ("zonsondergang", "strand"),
    ("boot", "strand"),
    ("hond", "sneeuw"),
    ("hond", "bos"),
)
LICENTIES = {"CC0": "P275=Q6938433", "publiek domein": "P6216=Q19652"}
PER_CATEGORIE = 5
PER_ANDER = 5
MAX_FOTO = 25 << 20  # elke download van de set, ook een origineel dat als thumbnail terugkomt
MIN_ZIJDE = 640  # de korte kant van het origineel
MAX_PIXELS = 50_000_000  # van het origineel
# De set haalt van elke foto een thumbnail van DUIM_BREEDTE breed, niet het origineel. Op 9ebf298
# gaf upload.wikimedia.org ook met BANDBREEDTE na ±30 minuten HTTP 429 (Retry-After 600), en
# begin 2026 trof zo'n 429 juist originelen voor clients zonder browser, terwijl thumbnails bleven
# laden (phabricator T413570). De richtlijn van Wikimedia voor media: liever thumbnails, en dan
# alleen de vaste maten. Die komen uit de cache van upload; een andere maat weigert Wikimedia of
# remt hij af. De vaste maten ($wgThumbnailSteps; mediawiki.org, "Common thumbnail sizes", en
# phabricator T402792): 20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920 en 3840 px breed.
# DUIM_BREEDTE is de kleinste ≥ 1024: de app geeft het model nooit het origineel maar zijn eigen
# previews (256.webp, of 1024.webp als BZ4 dat zegt: Keuze 77; de gezichten uit 1024.webp: Keuze
# 60). Een thumbnail met een lange kant ≥ 1280 geeft in de kwaliteitstest (app.render.image)
# dezelfde maten van 1024.webp en 256.webp als het origineel in de app.
# Een thumbnail maakt Wikimedia zelf, en kan hem later opnieuw maken met andere bytes (een andere
# versie van de software). beeld.tsv heeft daarom de URL, de grootte en de sha256 van precies het
# bestand dat set ophaalt (de thumbnail); streng is dan rood op de sha256, met de sleutel, nooit
# stil. De cache in CI (op de sha256 van beeld.tsv) houdt de eerste goede kopie. De
# bestandspagina van het origineel blijft in de kolom bron (de naamsvermelding).
DUIM_BREEDTE = 1280
# Hoe groot de thumbnail minstens is. De kwaliteitstest maakt er met app.render.image 1024.webp en
# 256.webp van, zoals de app (lange kant 1024 en 256, nooit vergroot), en het model krijgt die als
# INVOER × INVOER (meta.json: vierkant, zonder bijsnijden). Het model rekent uit 256.webp; BZ4
# zet dat af tegen 1024.webp, en daaruit rekent X3 als 256 te veel verliest (Keuze 77). Dus is de
# korte kant van die 1024.webp ≥ INVOER: anders rekt de voorbewerking ook 1024.webp op die kant
# op, en meet "256 tegen 1024" daar niets. Voor een liggende thumbnail van 1280 breed is dat een
# korte kant van ≥ 320 px; een staande mag tot 4 × 1280 = 5120 px hoog zijn (_genoeg)
PREVIEW = 1024  # de lange kant van 1024.webp (app.render.SIZES)
INVOER = 256  # de zijde van de invoer van het beeldmodel (meta.json; SigLIP 2 B/32 op 256)
KOLOMMEN = ("sleutel", "categorieen", "licentie", "sha256", "bytes", "url", "bron")
_SLEUTEL = re.compile(r"[a-z]+(-[a-z]+)?-[1-9]|[a-z]+\+[a-z]+")
_SHA = re.compile(r"[0-9a-f]{64}")


class Fout(Exception):
    """Een zin voor het log; nooit een traceback."""


def _module(naam: str, pad: Path):
    """Een module van de API zonder het pakket app te importeren (alleen standaardbibliotheek)."""
    spec = importlib.util.spec_from_file_location(naam, pad)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[naam] = mod  # dataclasses zoekt de module op
    spec.loader.exec_module(mod)
    return mod


lock = _module("beeld_lock", API / "app" / "analyse" / "lock.py")
omzetten = _module("beeld_omzetten", API / "app/analyse/worker/omzetten/__init__.py")
BRON_MAP = f"{omzetten.MAP}/bron"


# --- het netwerk -------------------------------------------------------------------------------


def _vraag(url: str, *, methode: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, method=methode, headers={"User-Agent": UA})


# Wikimedia vraagt om vragen na elkaar en om Retry-After te volgen (API:Etiquette). Zonder pauze
# gaf commons.wikimedia.org na ±10 s HTTP 429, ook na drie pogingen (CI-run op 26c49ca)
PAUZE_S = 1.0  # tussen twee vragen aan Commons (de API of upload.wikimedia.org)
# Retry-After geldt altijd (de robotregels): na een 429 of 503 vraagt _open nooit eerder opnieuw
# dan hij zegt. Op 9ebf298 zei upload.wikimedia.org "Retry-After 600", en de tool wachtte toen
# hooguit 120 s: vijf keer te vroeg opnieuw. Zegt Retry-After meer dan MAX_WACHT_S, of past het
# wachten niet meer in wat er van WACHT_TOTAAL_S over is, dan stopt _open meteen, zonder nog een
# vraag, met een fout die dat zegt
MAX_WACHT_S = 600.0  # de langste wachttijd na één 429 of 5xx die we aannemen
WACHT_TOTAAL_S = 900.0  # samen hooguit zo lang wachten na 429 en 5xx, per proces (per stap)
# De robotregels van Wikimedia (wikitech, Robot_policy, het deel over media): samen hooguit 2
# vragen tegelijk, samen hooguit 25 Mbps gemeten over stukken van 10 s, alleen originelen of de
# vaste maten van de thumbnails, en na een 429 Retry-After volgen. De job vraagt één ding
# tegelijk, maar een origineel is tot MAX_FOTO groot en kwam zonder grens in een fractie van een
# seconde binnen; na ±9 minuten gaf upload.wikimedia.org HTTP 429 (CI-run op f16ed5a). Dus van
# upload gemiddeld hooguit BANDBREEDTE, onder die 25 Mbps. Ook met die grens kwam er na ±30
# minuten een 429 (CI-run op 9ebf298); sindsdien haalt de set thumbnails (DUIM_BREEDTE)
BANDBREEDTE = 2_500_000  # bytes per seconde van upload.wikimedia.org (20 Mbps)
# Wat het log van een opgegeven 429 of 5xx overneemt: de tekst van <title>, anders de eerste
# regel met tekst zonder de tags van HTML (_antwoord)
MAX_REGEL = 160  # tekens, alleen afdrukbare ASCII
MAX_ANTWOORD = 4096  # zoveel bytes van het antwoord leest hij hooguit om die regel te vinden
_vorige: dict[str, float] = {}
_klaar: dict[str, float] = {}  # per host: wanneer de bytes tot nu toe op BANDBREEDTE binnen zijn
_gewacht = 0.0  # seconden, samen gewacht na 429 en 5xx in dit proces (WACHT_TOTAAL_S)
_opgehaald = 0  # bytes die haal in dit proces binnenkreeg (de voortgang van set)


def _pauze(url: str) -> None:
    """PAUZE_S tussen twee vragen aan dezelfde host van Commons; andere hosts wachten niet."""
    if not url.startswith((COMMONS, UPLOAD)):
        return
    host = _host(url)
    rest = _vorige.get(host, float("-inf")) + PAUZE_S - time.monotonic()
    if rest > 0:
        time.sleep(rest)
    _vorige[host] = time.monotonic()


def _rem(url: str, n: int) -> None:
    """Na n bytes van upload.wikimedia.org: wachten tot alles wat er tot nu toe binnenkwam, op
    BANDBREEDTE binnen had kunnen zijn. De schuld loopt door van de ene download naar de
    volgende; stilte spaart geen tegoed op (na een pauze telt hij vanaf nu). Andere hosts wachten
    niet."""
    if not url.startswith(UPLOAD):
        return
    host = _host(url)
    nu = time.monotonic()
    klaar = max(_klaar.get(host, nu), nu) + n / BANDBREEDTE
    _klaar[host] = klaar
    if klaar > nu:
        time.sleep(klaar - nu)


def _leesbaar(tekst: str, max_tekens: int) -> str:
    """Tekst van buiten voor het log: alleen afdrukbare ASCII (de rest wordt ?), hooguit
    max_tekens."""
    return "".join(c if " " <= c <= "~" else "?" for c in tekst.strip())[:max_tekens].strip()


_TITEL = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.I | re.S)
# script, style en commentaar zijn geen tekst; ook niet als het antwoord na MAX_ANTWOORD ophoudt
_BLOK = re.compile(r"<(script|style)\b.*?(?:</\1\s*>|\Z)|<!--.*?(?:-->|\Z)", re.I | re.S)
_TAG = re.compile(r"<[^>]*>")


def _antwoord(tekst: str) -> str:
    """De tekst van <title> als die er is, anders de eerste regel met tekst zonder de tags van
    HTML. Op 9ebf298 was de eerste regel van een 429 van upload.wikimedia.org "<!DOCTYPE html>":
    die zei niets."""
    titel = _TITEL.search(tekst)
    if titel:
        kaal = " ".join(_TAG.sub(" ", titel.group(1)).split())
        if kaal:
            return kaal
    kaal = _TAG.sub("", _BLOK.sub("", tekst))
    return next((r for r in kaal.split("\n") if r.strip()), "")


def _uitleg(e: urllib.error.HTTPError, retry_after: str | None, stop: str | None = None) -> str:
    """Wat het log zegt als _open het opgeeft na een 429 of 5xx: de laatste Retry-After die hij
    zag, waarom hij meteen stopte (stop, als dat zo was) en de tekst van het antwoord volgens
    _antwoord, alleen afdrukbare ASCII en hooguit MAX_REGEL tekens. Niets anders van het
    antwoord."""
    ra = f"Retry-After {_leesbaar(retry_after, 64)}" if retry_after else "geen Retry-After"
    if stop:
        ra = f"{ra}; {stop}"
    try:
        data = e.read(MAX_ANTWOORD)
    except (OSError, ValueError, http.client.HTTPException):
        data = b""
    finally:
        e.close()
    regel = _leesbaar(_antwoord(data.decode("ascii", errors="replace")), MAX_REGEL)
    return f"{ra}; antwoord: {regel}" if regel else f"{ra}; antwoord zonder tekst"


def _retry_after(e: urllib.error.HTTPError) -> float:
    """Retry-After in seconden (een getal of een HTTP-datum); 0 als hij ontbreekt of niet leest."""
    waarde = (e.headers.get("Retry-After") if e.headers is not None else None) or ""
    waarde = waarde.strip()
    if waarde.isdigit():
        return float(waarde)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(waarde).timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0.0


def _te_lang(na: float, tijd: float) -> str | None:
    """Waarom _open na een 429 of 5xx meteen stopt, of None als hij tijd seconden mag wachten.
    na: wat Retry-After zei (0 zonder)."""
    if na > MAX_WACHT_S:
        return f"langer dan MAX_WACHT_S ({MAX_WACHT_S:.0f} s), dus meteen gestopt"
    if _gewacht + tijd > WACHT_TOTAAL_S:
        over = max(0.0, WACHT_TOTAAL_S - _gewacht)
        return (
            f"{tijd:.0f} s wachten past niet in wat er over is van WACHT_TOTAAL_S "
            f"({over:.0f} van {WACHT_TOTAAL_S:.0f} s), dus meteen gestopt"
        )
    return None


def _open(url: str, *, pogingen: int = 6, timeout: float = 60):
    """urlopen met pogingen bij 429, 5xx of een netwerkfout: wachten 2, 4, 8, 16, 32 s, of zo
    lang als Retry-After zegt als dat langer is. Na een 429 of 5xx nooit eerder dan Retry-After
    zegt: is dat meer dan MAX_WACHT_S, of past het wachten (ook 2 tot 32 s zonder Retry-After)
    niet meer in WACHT_TOTAAL_S, dan stopt hij meteen. Geeft hij het op na een 429 of 5xx, dan
    krijgt de HTTPError een uitleg voor het log mee (_uitleg, _reden): op f16ed5a zei "HTTP 429"
    niet wat Wikimedia vroeg."""
    global _gewacht
    wacht = 2.0
    retry_after: str | None = None  # de laatste die hij zag, zoals hij binnenkwam
    for poging in range(pogingen):
        _pauze(url)
        try:
            return urllib.request.urlopen(_vraag(url), timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            if e.headers is not None and e.headers.get("Retry-After"):
                retry_after = e.headers.get("Retry-After")
            if poging == pogingen - 1:
                e.uitleg = _uitleg(e, retry_after)
                raise
            na = _retry_after(e)
            tijd = max(wacht, na)
            stop = _te_lang(na, tijd)
            if stop:
                e.uitleg = _uitleg(e, retry_after, stop)
                raise
            e.close()
            _gewacht += tijd
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if poging == pogingen - 1:
                raise
            tijd = wacht
        time.sleep(tijd)
        wacht *= 2
    raise AssertionError("onbereikbaar")


def json_van(url: str, max_bytes: int = 32 << 20) -> object:
    try:
        with _open(url) as antwoord:
            data = antwoord.read(max_bytes + 1)
    except (urllib.error.URLError, OSError) as e:
        raise Fout(f"{_host(url)}: {_reden(e)}") from None
    if len(data) > max_bytes:
        raise Fout(f"{_host(url)}: antwoord groter dan {max_bytes} bytes")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Fout(f"{_host(url)}: geen geldige JSON") from None


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or "?"


def _reden(e: BaseException) -> str:
    if isinstance(e, urllib.error.HTTPError):
        # vars(): HTTPError geeft een onbekend attribuut door aan zijn antwoord
        uitleg = vars(e).get("uitleg")
        return f"HTTP {e.code} ({uitleg})" if uitleg else f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return f"niet bereikbaar ({type(e.reason).__name__})"
    return type(e).__name__


def haal(url: str, doel: Path, *, max_bytes: int, grootte=None, sha256=None) -> tuple[str, int]:
    """Eén bestand naar doel via doel.part. Met grootte en sha256: alleen die bytes komen op hun
    plek. Geeft (sha256, bytes)."""
    global _opgehaald
    part = doel.with_name(doel.name + ".part")
    grens = grootte if grootte is not None else max_bytes
    h = hashlib.sha256()
    n = 0
    try:
        try:
            # set/ kan uit een cache komen (CI op f16ed5a): een .part die er al staat, ook een
            # symlink, gaat eerst weg, en "xb" schrijft nooit door een link naar elders
            part.unlink(missing_ok=True)
            with _open(url, timeout=120) as antwoord, part.open("xb") as f:
                while stuk := antwoord.read(1 << 20):
                    n += len(stuk)
                    _opgehaald += len(stuk)
                    if n > grens:
                        raise Fout(f"{doel.name}: groter dan {grens} bytes")
                    h.update(stuk)
                    f.write(stuk)
                    _rem(url, len(stuk))
        except (urllib.error.URLError, OSError) as e:
            raise Fout(f"{doel.name}: {_reden(e)}") from None
        if grootte is not None and n != grootte:
            raise Fout(f"{doel.name}: {n} bytes, models.lock of de set zegt {grootte}")
        if sha256 is not None and h.hexdigest() != sha256:
            raise Fout(f"{doel.name}: sha256 {h.hexdigest()}, verwacht {sha256}")
        if n == 0:
            raise Fout(f"{doel.name}: leeg")
        os.replace(part, doel)
    finally:
        part.unlink(missing_ok=True)
    return h.hexdigest(), n


def sha256_van(pad: Path) -> str:
    h = hashlib.sha256()
    with pad.open("rb") as f:
        for stuk in iter(lambda: f.read(1 << 20), b""):
            h.update(stuk)
    return h.hexdigest()


def _klopt(pad: Path, grootte: int, sha256: str) -> bool:
    """Staat pad er al als gewoon bestand (geen symlink) met deze grootte en sha256?"""
    if not pad.is_file() or pad.is_symlink():
        return False
    return pad.stat().st_size == grootte and sha256_van(pad) == sha256


# --- de werkmap --------------------------------------------------------------------------------


class Werk:
    def __init__(self, map_: Path) -> None:
        self.map = map_
        map_.mkdir(parents=True, exist_ok=True)
        self.kort = map_ / "kort.txt"
        self.bron = map_ / "bron"
        self.set = map_ / "set"
        self.voorstel_lock = map_ / "voorstel-models.lock"
        self.voorstel_bron = map_ / "voorstel-bron.lock"
        self.voorstel_set = map_ / "voorstel-beeld.tsv"

    def meld(self, regel: str) -> None:
        """Naar het log en naar kort.txt (het blok aan het eind)."""
        print(regel, flush=True)
        with self.kort.open("a", encoding="utf-8") as f:
            f.write(regel + "\n")


# --- BZ1 ---------------------------------------------------------------------------------------


def bereik(werk: Werk) -> int:
    hosts = (
        ("huggingface.co", f"{HF}/api/models/{REPO}"),
        ("commons.wikimedia.org", f"{COMMONS}?action=query&meta=siteinfo&format=json"),
        ("upload.wikimedia.org", UPLOAD),
        ("images-assets.nasa.gov", NASA),
    )
    for naam, url in hosts:
        try:
            with _open(url, pogingen=2, timeout=30) as antwoord:
                werk.meld(f"BZ1 {naam}: bereikbaar (HTTP {antwoord.status})")
        except urllib.error.HTTPError as e:
            werk.meld(f"BZ1 {naam}: bereikbaar (HTTP {e.code})")
        except urllib.error.URLError as e:
            werk.meld(f"BZ1 {naam}: niet bereikbaar ({type(e.reason).__name__})")
        except OSError as e:
            werk.meld(f"BZ1 {naam}: niet bereikbaar ({type(e).__name__})")
    return 0


# --- de bron (BZ2) -----------------------------------------------------------------------------


def siglip_regels(regels: list) -> tuple[list, list]:
    """(bronnen, uitkomsten) van SigLIP in models.lock; Fout als het maar een deel is."""
    bronnen = [r for r in regels if r.name.startswith(BRON_MAP + "/")]
    uit = [r for r in regels if r.name.startswith(omzetten.MAP + "/") and r not in bronnen]
    if not bronnen and not uit:
        return [], []
    namen_b = sorted(r.name.removeprefix(BRON_MAP + "/") for r in bronnen)
    namen_u = sorted(r.name.removeprefix(omzetten.MAP + "/") for r in uit)
    if namen_b != sorted(omzetten.BRONNEN) or namen_u != sorted(omzetten.UITVOER):
        raise Fout(
            "models.lock heeft maar een deel van de regels van SigLIP: verwacht "
            f"{len(omzetten.BRONNEN)} bronnen en {len(omzetten.UITVOER)} uitkomsten"
        )
    for r in bronnen:
        if r.url is None:
            raise Fout(f"models.lock: {r.name} heeft geen url")
    for r in uit:
        if r.url is not None:
            raise Fout(f"models.lock: {r.name} maakt de host zelf (url -)")
    return bronnen, uit


def commit_van() -> str:
    """De huidige commit van REPO, via de API van Hugging Face."""
    api_url = f"{HF}/api/models/{REPO}/revision/main"
    doc = json_van(api_url)
    sha = doc.get("sha") if isinstance(doc, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Fout(f"{_host(api_url)}: geen commit in het antwoord")
    namen = {s.get("rfilename") for s in doc.get("siblings") or [] if isinstance(s, dict)}
    mist = [n for n in omzetten.BRONNEN if namen and n not in namen]
    if mist:
        raise Fout(f"{REPO} op {sha}: {', '.join(mist)} ontbreekt")
    return sha


def bron_url(commit: str, naam: str) -> str:
    return f"{HF}/{REPO}/resolve/{commit}/{naam}"


def bron(werk: Werk, lock_pad: Path) -> int:
    werk.bron.mkdir(exist_ok=True)
    bronnen, _ = siglip_regels(lock.load(lock_pad))
    if bronnen:
        for r in bronnen:
            naam = r.name.rsplit("/", 1)[1]
            haal(r.url, werk.bron / naam, max_bytes=r.size, grootte=r.size, sha256=r.sha256)
        werk.meld(f"BZ2 bron: de vijf bestanden kloppen met models.lock ({bronnen[0].url})")
        return 0
    commit = commit_van()
    regels = []
    for naam in omzetten.BRONNEN:
        url = bron_url(commit, naam)
        sha, n = haal(url, werk.bron / naam, max_bytes=MAX_BRON[naam])
        regels.append(lock.ModelFile(sha, n, f"{BRON_MAP}/{naam}", url).line())
    werk.voorstel_bron.write_text("".join(r + "\n" for r in regels), encoding="utf-8")
    werk.meld(f"BZ2 bron: {REPO} op commit {commit}; nog geen regels in models.lock (voorstel)")
    return VOORSTEL


# --- twee omzettingen (BZ2) --------------------------------------------------------------------

_KLAAR = re.compile(r"omzetten: klaar in ([0-9.]+) s, maxrss ([0-9]+) MiB")


def klaar_regel(log: Path) -> tuple[float, int]:
    """(seconden, maxrss in MiB) uit de laatste regel van de omzetter op stderr."""
    try:
        tekst = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise Fout(f"{log.name}: geen log van de omzetter") from None
    gevonden = _KLAAR.findall(tekst)
    if not gevonden:
        raise Fout(f"{log.name}: de omzetter meldde niet dat hij klaar was")
    s, rss = gevonden[-1]
    return float(s), int(rss)


def _met_fp32(werk: Werk, fp32: Path, log: Path, uitkomst: dict) -> list[str]:
    """De omzetting met --fp32, alleen voor BZ4: maxrss zonder grens (de dienst zet nooit om met
    --fp32), wel dezelfde vijf bestanden van UITVOER als zonder, en de twee fp32-grafen erbij."""
    s, rss = klaar_regel(log)
    werk.meld(f"BZ2 omzetting met --fp32 (voor BZ4): {s:.1f} s, maxrss {rss} MiB (geen grens)")
    for naam in (*omzetten.UITVOER, omzetten.BEELD_FP32, omzetten.TEKST_FP32):
        pad = fp32 / naam
        if not pad.is_file() or pad.is_symlink():
            raise Fout(f"{naam}: ontbreekt in de omzetting met --fp32")
    fout = []
    for naam, (sha, _) in uitkomst.items():
        sha32 = sha256_van(fp32 / naam)
        if sha32 != sha:
            fout.append(f"{naam}: met --fp32 {sha32[:12]}…, zonder {sha[:12]}…")
    if not fout:
        werk.meld(f"BZ2 met --fp32: dezelfde sha256 voor alle {len(uitkomst)} bestanden")
    return fout


def vergelijk(
    werk: Werk,
    lock_pad: Path,
    uit: list[Path],
    logs: list[Path],
    fp32: Path | None = None,
    log_fp32: Path | None = None,
) -> int:
    if len(uit) != 2 or len(logs) != 2:
        raise Fout("vergelijk wil twee uitvoermappen en twee logs")
    if (fp32 is None) != (log_fp32 is None):
        raise Fout("vergelijk wil --uit-fp32 en --log-fp32 samen")
    fout = []
    for i, log in enumerate(logs, start=1):
        s, rss = klaar_regel(log)
        werk.meld(f"BZ2 omzetting {i}: {s:.1f} s, maxrss {rss} MiB (grens {MAX_RSS_MIB})")
        if rss > MAX_RSS_MIB:
            fout.append(f"omzetting {i}: maxrss {rss} MiB > {MAX_RSS_MIB}")
    uitkomst = {}
    for naam in omzetten.UITVOER:
        paden = [u / naam for u in uit]
        if not all(p.is_file() and not p.is_symlink() for p in paden):
            raise Fout(f"{naam}: ontbreekt in een van de uitvoermappen")
        sha = [sha256_van(p) for p in paden]
        if sha[0] != sha[1]:
            fout.append(f"{naam}: twee omzettingen geven {sha[0][:12]}… en {sha[1][:12]}…")
        uitkomst[naam] = (sha[0], paden[0].stat().st_size)
    if not fout:
        werk.meld(f"BZ2 twee keer omzetten: dezelfde sha256 voor alle {len(uitkomst)} bestanden")
    if fp32 is not None and log_fp32 is not None:
        fout += _met_fp32(werk, fp32, log_fp32, uitkomst)
    _, verwacht = siglip_regels(lock.load(lock_pad))
    if verwacht:
        for r in verwacht:
            sha, n = uitkomst[r.name.removeprefix(omzetten.MAP + "/")]
            if (sha, n) != (r.sha256, r.size):
                fout.append(f"{r.name}: sha256 {sha} ({n} bytes), models.lock zegt {r.sha256}")
        if not fout:
            werk.meld("BZ2 models.lock: de uitkomst klopt")
    for f in fout:
        werk.meld(f"BZ2 FOUT {f}")
    if fout:
        return 1
    if verwacht:
        return 0
    regels = [
        lock.ModelFile(sha, n, f"{omzetten.MAP}/{naam}", None).line()
        for naam, (sha, n) in uitkomst.items()
    ]
    bronregels = []
    if werk.voorstel_bron.is_file():
        bronregels = werk.voorstel_bron.read_text(encoding="utf-8").splitlines()
    tekst = "".join(r + "\n" for r in [*bronregels, *regels])
    lock.parse(tekst)  # wat we voorstellen, moet lock.py zelf kunnen lezen
    werk.voorstel_lock.write_text(tekst, encoding="utf-8")
    werk.meld("BZ2 voorstel voor models.lock (sha256 bytes naam url):")
    for r in tekst.splitlines():
        werk.meld(f"  {r}")
    return VOORSTEL


# --- de set (Keuze 83, BZ4) --------------------------------------------------------------------


@dataclass(frozen=True)
class Foto:
    sleutel: str
    categorieen: tuple[str, ...]  # de eerste is de hoofdcategorie; leeg voor een andere foto
    licentie: str
    sha256: str
    bytes: int
    url: str
    bron: str

    def regel(self) -> str:
        cats = ",".join(self.categorieen) or "-"
        velden = (self.sleutel, cats, self.licentie, self.sha256, str(self.bytes), self.url)
        return "\t".join((*velden, self.bron))


def combinaties(pad: Path = VRAGEN) -> list[tuple[str, ...]]:
    """De vaste foto's van de combinaties in beeld_vragen.tsv, als (a, b)."""
    uit = []
    for regel in pad.read_text(encoding="utf-8").splitlines():
        velden = regel.split("\t")
        if velden[0] == "combinatie":
            uit.append(tuple(velden[3].split("+")))
    return uit


def lees_set(tekst: str, combis: list[tuple[str, ...]] | None = None) -> list[Foto]:
    """beeld.tsv, streng nagekeken. Fout met het regelnummer."""
    regels = [r for r in tekst.splitlines() if r and not r.startswith("#")]
    if not regels or tuple(regels[0].split("\t")) != KOLOMMEN:
        raise Fout(f"beeld.tsv: de eerste regel hoort {' '.join(KOLOMMEN)}")
    fotos: list[Foto] = []
    gezien: set[str] = set()
    for nr, regel in enumerate(regels[1:], start=2):
        v = regel.split("\t")
        if len(v) != len(KOLOMMEN):
            raise Fout(f"beeld.tsv regel {nr}: {len(v)} velden, verwacht {len(KOLOMMEN)}")
        sleutel, cats, lic, sha, n, url, bron_ = v
        categorieen = () if cats == "-" else tuple(cats.split(","))
        if not _SLEUTEL.fullmatch(sleutel) or sleutel in gezien:
            raise Fout(f"beeld.tsv regel {nr}: sleutel {sleutel!r} klopt niet of staat er al")
        if any(c not in CATEGORIEEN for c in categorieen) or len(set(categorieen)) != len(
            categorieen
        ):
            raise Fout(f"beeld.tsv regel {nr}: categorieën {cats!r} kloppen niet")
        if lic not in LICENTIES:
            raise Fout(f"beeld.tsv regel {nr}: licentie {lic!r}; alleen {', '.join(LICENTIES)}")
        if not _SHA.fullmatch(sha) or not re.fullmatch(r"[1-9][0-9]{0,9}", n):
            raise Fout(f"beeld.tsv regel {nr}: sha256 of bytes klopt niet")
        if int(n) > MAX_FOTO:
            raise Fout(f"beeld.tsv regel {nr}: groter dan {MAX_FOTO} bytes")
        if not url.startswith(UPLOAD) or not re.fullmatch(r"[!-~]+", url):
            raise Fout(f"beeld.tsv regel {nr}: url hoort bij {UPLOAD}")
        # Een thumbnail alleen van de vaste maat: een andere maat weigert Wikimedia of remt hij af
        if "thumb" in urllib.parse.urlsplit(url).path.split("/") and not _is_duim(url):
            raise Fout(f"beeld.tsv regel {nr}: url is een thumbnail, maar niet {DUIM_BREEDTE} px")
        if not bron_.startswith("https://commons.wikimedia.org/wiki/File:"):
            raise Fout(f"beeld.tsv regel {nr}: bron hoort een bestandspagina van Commons")
        _past(sleutel, categorieen, nr)
        gezien.add(sleutel)
        fotos.append(Foto(sleutel, categorieen, lic, sha, int(n), url, bron_))
    _telling(fotos, combis if combis is not None else combinaties())
    return fotos


def _past(sleutel: str, categorieen: tuple[str, ...], nr: int) -> None:
    if "+" in sleutel:
        a, b = sleutel.split("+")
        goed = categorieen[:1] == (a,) and b in categorieen
    elif sleutel.startswith("anders-"):
        goed = not categorieen and sleutel.split("-")[1] in ANDERE
    else:
        goed = categorieen[:1] == (sleutel.split("-")[0],)
    if not goed:
        raise Fout(f"beeld.tsv regel {nr}: sleutel {sleutel} past niet bij de categorieën")


def _telling(fotos: list[Foto], combis: list[tuple[str, ...]]) -> None:
    for naam in CATEGORIEEN:
        n = sum(1 for f in fotos if f.categorieen[:1] == (naam,))
        if n != PER_CATEGORIE:
            raise Fout(f"beeld.tsv: {n} foto's met hoofdcategorie {naam}; verwacht {PER_CATEGORIE}")
    anders = sum(1 for f in fotos if not f.categorieen)
    if anders != len(ANDERE) * PER_ANDER:
        raise Fout(f"beeld.tsv: {anders} andere foto's; verwacht {len(ANDERE) * PER_ANDER}")
    sleutels = {f.sleutel for f in fotos}
    for a, b in combis:
        if f"{a}+{b}" not in sleutels:
            raise Fout(f"beeld.tsv: de vaste foto {a}+{b} van beeld_vragen.tsv ontbreekt")


def zoek(srsearch: str, offset: int) -> tuple[list[int], int | None]:
    """Eén pagina van de zoekmachine van Commons (namespace File); (pageids, volgende offset)."""
    q = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "list": "search",
        "srnamespace": "6",
        "srlimit": "50",
        "sroffset": str(offset),
        "srsearch": srsearch,
    }
    doc = json_van(f"{COMMONS}?{urllib.parse.urlencode(q)}")
    try:
        ids = [int(r["pageid"]) for r in doc["query"]["search"]]
    except (KeyError, TypeError, ValueError):
        raise Fout("commons.wikimedia.org: het zoekantwoord heeft een andere vorm") from None
    verder = (doc.get("continue") or {}).get("sroffset")
    return ids, int(verder) if isinstance(verder, int) else None


def details(pageids: list[int]) -> dict[int, dict]:
    """Per pageid: url, bytes, maten, mime en de bestandspagina van het origineel, de url en de
    maten van de thumbnail van DUIM_BREEDTE breed (iiurlwidth), plus wat de gestructureerde
    gegevens zeggen (P180, P275, P6216)."""
    if not pageids:
        return {}
    q = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "iiurlwidth": str(DUIM_BREEDTE),
        "pageids": "|".join(map(str, pageids)),
    }
    doc = json_van(f"{COMMONS}?{urllib.parse.urlencode(q)}")
    uit: dict[int, dict] = {}
    for p in (doc.get("query") or {}).get("pages") or []:
        info = (p.get("imageinfo") or [None])[0]
        if isinstance(info, dict):
            uit[int(p["pageid"])] = {
                "url": info.get("url"),
                "bytes": info.get("size"),
                "breedte": info.get("width"),
                "hoogte": info.get("height"),
                "mime": info.get("mime"),
                "bron": info.get("descriptionurl"),
                "duim": info.get("thumburl"),
                "duim_breedte": info.get("thumbwidth"),
                "duim_hoogte": info.get("thumbheight"),
            }
    q = {"action": "wbgetentities", "format": "json", "ids": "|".join(f"M{i}" for i in uit)}
    ent = json_van(f"{COMMONS}?{urllib.parse.urlencode(q)}").get("entities") or {}
    for i, d in uit.items():
        e = ent.get(f"M{i}") or {}
        uitspraken = e.get("statements") or e.get("claims") or {}
        if not isinstance(uitspraken, dict):
            uitspraken = {}
        for eigenschap in ("P180", "P275", "P6216"):
            d[eigenschap] = _waarden(uitspraken.get(eigenschap))
    return uit


def _waarden(lijst: object) -> set[str]:
    uit = set()
    for s in lijst if isinstance(lijst, list) else []:
        try:
            if s["mainsnak"]["snaktype"] == "value":
                uit.add(s["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    return uit


def _licentie(d: dict) -> str | None:
    if "Q6938433" in d["P275"]:
        return "CC0"
    if "Q19652" in d["P6216"]:
        return "publiek domein"
    return None


def _is_duim(url: str) -> bool:
    """Een thumbnail van DUIM_BREEDTE breed op upload: .../thumb/<pad>/<naam>/1280px-<naam>."""
    deel = urllib.parse.urlsplit(url).path.split("/")
    return "thumb" in deel[:-2] and deel[-1].startswith(f"{DUIM_BREEDTE}px-")


def _genoeg(breedte: int, hoogte: int) -> bool:
    """Is de korte kant van de 1024.webp die app.render.image van een foto van deze maat maakt
    (lange kant hooguit PREVIEW, nooit vergroot), minstens INVOER? Zie INVOER."""
    return min(breedte, hoogte) * PREVIEW >= INVOER * max(breedte, hoogte, PREVIEW)


def _duim(d: dict) -> tuple[str, int | None] | None:
    """Wat set van een foto ophaalt: (url, bytes als die vooraf bekend zijn), of None als dat niet
    kan. Meestal de thumbnail van DUIM_BREEDTE breed (bytes onbekend; haal stopt bij MAX_FOTO).
    Is het origineel niet breder, dan geeft MediaWiki het origineel zelf als thumburl: ook goed
    als het niet groter is dan MAX_FOTO (de grens van elke download; de bytes zijn dan vooraf
    bekend). Hoe breed MediaWiki dan in thumbwidth zegt, telt niet: de maten van het origineel
    gelden. Een groot origineel met een thumbnail mag: set haalt alleen die thumbnail."""
    url = d.get("duim")
    if not isinstance(url, str) or not url.startswith(UPLOAD) or not re.fullmatch(r"[!-~]+", url):
        return None
    if url == d["url"]:
        breedte, hoogte, n = int(d["breedte"]), int(d["hoogte"]), int(d["bytes"])
        if breedte > DUIM_BREEDTE or not 0 < n <= MAX_FOTO:
            return None
    else:
        if not _is_duim(url) or int(d.get("duim_breedte")) != DUIM_BREEDTE:
            return None
        breedte, hoogte, n = DUIM_BREEDTE, int(d.get("duim_hoogte")), None
    return (url, n) if _genoeg(breedte, hoogte) else None


def _bruikbaar(d: dict) -> bool:
    try:
        return (
            d["mime"] == "image/jpeg"
            and isinstance(d["url"], str)
            and d["url"].startswith(UPLOAD)
            and isinstance(d["bron"], str)
            and d["bron"].startswith("https://commons.wikimedia.org/wiki/File:")
            and int(d["bytes"]) > 0
            and min(int(d["breedte"]), int(d["hoogte"])) >= MIN_ZIJDE
            and int(d["breedte"]) * int(d["hoogte"]) <= MAX_PIXELS
            and MENS not in d["P180"]
            and _licentie(d) is not None
            and _duim(d) is not None
        )
    except (TypeError, ValueError):
        return False


def _kandidaten(qids: tuple[str, ...], *, max_pagina: int = 4):
    """Bestanden die alle qids afbeelden, met CC0 of publiek domein, zonder mens; per licentie
    tot max_pagina pagina's van 50."""
    for lic in LICENTIES.values():
        zoekterm = " ".join(
            [*(f"haswbstatement:P180={q}" for q in qids), f"haswbstatement:{lic}"]
            + [f"-haswbstatement:P180={MENS}", "filemime:image/jpeg"]
        )
        offset: int | None = 0
        for _ in range(max_pagina):
            if offset is None:
                break
            ids, offset = zoek(zoekterm, offset)
            yield from sorted(details(ids).items())


class Voortgang:
    """Hoe ver set kwam: in de fout ("bij foto 37 van 120, 12,3 MB opgehaald"; CI op 9ebf298 zei
    niet hoe ver de stap was) en, als het goed ging, in de regel van het blok (bytes en tijd)."""

    def __init__(self, totaal: int) -> None:
        self.totaal = totaal
        self.klaar = 0  # foto's die al goed op hun plek staan
        self.gekozen = False  # het voorstel koos alles wat Commons had (en kwam te kort)
        self._bytes = _opgehaald
        self._begin = time.monotonic()

    def bytes(self) -> int:
        return _opgehaald - self._bytes

    def waar(self) -> str:
        if self.gekozen:
            return f"{self.klaar} van {self.totaal} foto's opgehaald, {_mb(self.bytes())}"
        n = min(self.klaar + 1, self.totaal)
        return f"bij foto {n} van {self.totaal}, {_mb(self.bytes())} opgehaald"

    def samen(self) -> str:
        return f"{_mb(self.bytes())} in {time.monotonic() - self._begin:.0f} s"


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB".replace(".", ",")


def stel_voor(werk: Werk, *, combis=None, voortgang: Voortgang | None = None) -> list[Foto]:
    """120 foto's volgens Keuze 83, en ze meteen ophalen (de sha256 komt van de download): van
    elke foto de thumbnail van DUIM_BREEDTE breed (_duim), nooit een groot origineel.

    Een tekort stopt de keuze niet: elk tekort komt met het aantal bekeken kandidaten in het
    blok, en pas daarna volgt de fout. Zo laat één run alles zien wat Commons niet heeft."""
    combis = combis if combis is not None else combinaties()
    voortgang = voortgang or Voortgang(len(CATEGORIEEN) * PER_CATEGORIE + len(ANDERE) * PER_ANDER)
    werk.set.mkdir(exist_ok=True)
    gekozen: list[Foto] = []
    gebruikt: set[int] = set()
    tekort: list[str] = []
    combi_tekort = False

    def neem(pageid: int, d: dict, sleutel: str, cats: tuple[str, ...]) -> None:
        doel = werk.set / f"{sleutel}.jpg"
        url, grootte = _duim(d)
        sha, n = haal(url, doel, max_bytes=MAX_FOTO, grootte=grootte)
        gekozen.append(Foto(sleutel, cats, _licentie(d), sha, n, url, d["bron"]))
        gebruikt.add(pageid)
        voortgang.klaar = len(gekozen)

    def cats_van(d: dict, hoofd: tuple[str, ...]) -> tuple[str, ...]:
        rest = [c for c, q in CATEGORIEEN.items() if q in d["P180"] and c not in hoofd]
        return (*hoofd, *rest)

    hoofd_telt = dict.fromkeys(CATEGORIEEN, 0)
    for a, b in combis:
        gezien = 0
        for pageid, d in _kandidaten((CATEGORIEEN[a], CATEGORIEEN[b])):
            gezien += 1
            if pageid not in gebruikt and _bruikbaar(d):
                neem(pageid, d, f"{a}+{b}", cats_van(d, (a, b)))
                hoofd_telt[a] += 1
                break
        else:
            combi_tekort = True
            tekort.append(f"geen bruikbare foto voor {a}+{b} ({gezien} kandidaten bekeken)")
    for naam, qid in CATEGORIEEN.items():
        nr = gezien = 0
        for pageid, d in _kandidaten((qid,)):
            if hoofd_telt[naam] >= PER_CATEGORIE:
                break
            gezien += 1
            if pageid not in gebruikt and _bruikbaar(d):
                nr += 1
                neem(pageid, d, f"{naam}-{nr}", cats_van(d, (naam,)))
                hoofd_telt[naam] += 1
        if hoofd_telt[naam] < PER_CATEGORIE:
            n = hoofd_telt[naam]
            tekort.append(f"maar {n} bruikbare foto's voor {naam} ({gezien} kandidaten bekeken)")
    alle_qids = set(CATEGORIEEN.values())
    for naam, qid in ANDERE.items():
        nr = gezien = 0
        for pageid, d in _kandidaten((qid,)):
            if nr >= PER_ANDER:
                break
            gezien += 1
            if pageid not in gebruikt and _bruikbaar(d) and not d["P180"] & alle_qids:
                nr += 1
                neem(pageid, d, f"anders-{naam}-{nr}", ())
        if nr < PER_ANDER:
            tekort.append(f"maar {nr} bruikbare foto's voor {naam} (anders; {gezien} bekeken)")
    if not tekort:
        return gekozen
    for t in tekort:
        werk.meld(f"BZ4 set: Commons: {t}")
    if combi_tekort:
        al = {tuple(c) for c in combis}
        for a, b in RESERVE_COMBIS:
            if (a, b) in al:
                continue
            n = len(
                {
                    pageid
                    for pageid, d in _kandidaten((CATEGORIEEN[a], CATEGORIEEN[b]), max_pagina=1)
                    if _bruikbaar(d)
                }
            )
            werk.meld(f"BZ4 reserve {a}+{b}: {n} bruikbare foto's (eerste pagina per licentie)")
    voortgang.gekozen = True  # de fout zegt dan hoeveel er zijn, niet "bij foto ..."
    raise Fout(f"Commons: {len(tekort)} tekort(en) voor de set; zie de regels BZ4 set hierboven")


def set_tsv(fotos: list[Foto]) -> str:
    kop = (
        "# De kwaliteitsset van zoeken op beeld (fase 6, Keuze 83; voorgesteld door de job\n"
        "# beeld-model uit de gestructureerde gegevens van Wikimedia Commons). Alleen CC0 of\n"
        "# publiek domein, zonder P180 = mens. De foto's staan nooit in de repository.\n"
        f"# Per foto de thumbnail van {DUIM_BREEDTE} px breed: url, bytes en sha256 van precies\n"
        "# dat bestand (het origineel als dat niet breder is). bron is de pagina van het\n"
        "# origineel.\n"
    )
    return kop + "\t".join(KOLOMMEN) + "\n" + "".join(f.regel() + "\n" for f in fotos)


def set_(werk: Werk, set_pad: Path, *, combis=None) -> int:
    """Gaat het ophalen mis, dan zegt de fout ook hoe ver set kwam (Voortgang); in een voorstel
    is het totaal het doel van Keuze 83 (120)."""
    combis = combis if combis is not None else combinaties()
    if set_pad.is_file():
        fotos = lees_set(set_pad.read_text(encoding="utf-8"), combis)
        werk.set.mkdir(exist_ok=True)
        voortgang = Voortgang(len(fotos))
        uit_cache = 0
        try:
            for f in fotos:
                doel = werk.set / f"{f.sleutel}.jpg"
                # Wat er al staat (in CI uit de cache op de sha256 van beeld.tsv) en klopt, haalt
                # hij niet opnieuw: de robotregels van Wikimedia vragen zo min mogelijk downloads.
                # Een bestand dat niet klopt, haalt hij opnieuw, via .part eroverheen. Heeft
                # Wikimedia de thumbnail intussen opnieuw gemaakt met andere bytes, dan is dit rood
                # op de sha256 (zie DUIM_BREEDTE), nooit stil
                if _klopt(doel, f.bytes, f.sha256):
                    uit_cache += 1
                else:
                    haal(f.url, doel, max_bytes=f.bytes, grootte=f.bytes, sha256=f.sha256)
                voortgang.klaar += 1
        except Fout as e:
            raise Fout(f"{e} ({voortgang.waar()})") from None
        gehaald = len(fotos) - uit_cache
        werk.meld(
            f"BZ4 set: {len(fotos)} foto's kloppen met beeld.tsv "
            f"({uit_cache} uit de cache, {gehaald} van Commons; {voortgang.samen()})"
        )
        return 0
    voortgang = Voortgang(len(CATEGORIEEN) * PER_CATEGORIE + len(ANDERE) * PER_ANDER)
    try:
        fotos = stel_voor(werk, combis=combis, voortgang=voortgang)
    except Fout as e:
        raise Fout(f"{e} ({voortgang.waar()})") from None
    tekst = set_tsv(fotos)
    lees_set(tekst, combis)  # het voorstel moet zelf door de controle komen
    werk.voorstel_set.write_text(tekst, encoding="utf-8")
    werk.meld(
        f"BZ4 set: beeld.tsv ontbreekt; voorstel met {len(fotos)} foto's in het verslag "
        f"({voortgang.samen()})"
    )
    # Ook in het log (het blok aan het eind): het verslag van upload-artifact is niet overal te
    # halen, en een mens zet de set er pas na het lezen in (zoals de regels van models.lock)
    werk.meld("BZ4 voorstel voor beeld.tsv:")
    for r in tekst.splitlines():
        werk.meld(f"  {r}")
    return VOORSTEL


def uitslag(werk: Werk) -> int:
    rood = []
    if werk.voorstel_lock.is_file():
        rood.append("models.lock mist de regels van SigLIP (voorstel: voorstel-models.lock)")
        # Review golf 5, uitrol F3: caps meldt elke regel van models.lock; zonder de stap BEELD
        # (X3b) staat er geen omzetting en geen bron, dus models_missing
        rood.append(
            "zet die regels pas samen met X3b in models.lock: zonder de stap BEELD van install.sh "
            "maakt niemand de omzetting, en meldt de container ze als ontbrekend (models_missing)"
        )
    if werk.voorstel_set.is_file():
        rood.append("beeld.tsv ontbreekt (voorstel: voorstel-beeld.tsv)")
    for r in rood:
        werk.meld(f"UITSLAG {r}")
    if not rood:
        werk.meld("UITSLAG models.lock en beeld.tsv waren er al; geen voorstel")
    return 1 if rood else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("stap", choices=("bereik", "bron", "vergelijk", "set", "uitslag"))
    p.add_argument("--werk", type=Path, required=True, help="de werkmap ($RUNNER_TEMP/beeld)")
    p.add_argument("--lock", type=Path, default=LOCK, help="models.lock")
    p.add_argument("--set", type=Path, default=SET, help="beeld.tsv")
    p.add_argument("--uit", type=Path, action="append", default=[], help="een uitvoermap")
    p.add_argument("--log", type=Path, action="append", default=[], help="stderr van de omzetter")
    p.add_argument("--uit-fp32", type=Path, help="de uitvoermap van de omzetting met --fp32 (BZ4)")
    p.add_argument("--log-fp32", type=Path, help="stderr van de omzetting met --fp32")
    args = p.parse_args(argv)
    werk = Werk(args.werk)
    try:
        if args.stap == "bereik":
            return bereik(werk)
        if args.stap == "bron":
            return bron(werk, args.lock)
        if args.stap == "vergelijk":
            return vergelijk(werk, args.lock, args.uit, args.log, args.uit_fp32, args.log_fp32)
        if args.stap == "set":
            return set_(werk, args.set)
        return uitslag(werk)
    except (Fout, lock.LockError) as e:
        werk.meld(f"FOUT {args.stap}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
