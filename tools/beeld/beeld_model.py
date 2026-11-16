#!/usr/bin/env python3
"""Het echte beeldmodel en de kwaliteitsset ophalen en nakijken (fase 6, X3a; job beeld-model).

Alleen de standaardbibliotheek; draait op de runner met `python3 -I`, nooit in het image. Alles
wat hij ophaalt, is onvertrouwd: eerst naar een .part, dan grootte en sha256, en pas dan op zijn
plek. De User-Agent is algemeen: geen persoon, geen e-mail, geen repository. Alleen als
BEELD_CONTACT gezet is (zie UA_MET_CONTACT; in de CI de link naar het repository) krijgt Wikimedia
een contact mee.

    beeld_model.py bereik   --werk W              BZ1: per host bereikbaar of niet
    beeld_model.py bron     --werk W --lock L     de vijf bronbestanden van SigLIP 2 (BZ2)
    beeld_model.py vergelijk --werk W --lock L    twee omzettingen (en die met --fp32) en
                                                  models.lock (BZ2)
    beeld_model.py set      --werk W --set S      de 120 foto's van Keuze 83 (BZ4); --niet N:
                                                  de foto's die een voorstel nooit neemt
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
van beeld.tsv). Een voorstel neemt eerst foto's zonder teken van een kunstwerk, een collectie of
een persoon (de eerste keus) en vult pas daarna aan met de rest (de tweede keus; _tweede_keus),
en nooit een foto uit api/tests/analyse/sets/beeld_niet.tsv (een mens wees hem af; lees_niet);
met beeld.tsv is een foto uit die lijst een fout. Van thumb.wikimedia.org (de thumbnails) en
upload.wikimedia.org (de originelen, en oude URL's van thumbnails) elk gemiddeld hooguit
BANDBREEDTE, altijd als JPEG (ACCEPT), van de API van Commons hooguit één vraag per PAUZE_API_S
(de grens zonder contact), en Retry-After altijd gevolgd (de robotregels van Wikimedia). Een
omleiding van Commons volgt hij alleen naar een host van Commons (_Omleiding). `uitslag` faalt
met "models.lock mist de regels van SigLIP" en "beeld.tsv ontbreekt" zolang er een voorstel
ligt; een mens zet de regels erin, nooit de job zelf, en die van SigLIP pas samen met X3b (de
stap BEELD van install.sh; anders meldt de container ze als ontbrekend).

Codes: 0 goed, 1 fout, 3 voorstel geschreven (de job gaat door, `uitslag` maakt hem rood).
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
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
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "api"
LOCK = API / "app" / "analyse" / "models.lock"
SET = API / "tests" / "analyse" / "sets" / "beeld.tsv"
VRAGEN = API / "tests" / "analyse" / "sets" / "beeld_vragen.tsv"
NIET = API / "tests" / "analyse" / "sets" / "beeld_niet.tsv"  # lees_niet

UA = "beeld-model-meting/1.0 (CI van een zelf-gehost fotoplatform)"
# Optioneel en standaard uit: een contact in de User-Agent, in de vorm van de richtlijn van
# Wikimedia voor de User-Agent ("naam/versie (contactgegevens) ..."; een e-mailadres of een URL).
# Alleen als de omgevingsvariabele BEELD_CONTACT gezet en niet leeg is (_zet_contact), en alleen
# naar Commons (de API, thumb en upload: _dienst), nooit naar Hugging Face of een andere host,
# ook niet na een omleiding (_Omleiding). Het contact is van de eigenaar en
# Wikimedia ziet het in elke vraag: alleen met zijn toestemming. ci.yml zet sinds 2026-11-15 de
# link naar het repository, alleen op BZ1 en BZ4 set (runbook analyse.md). Het log zegt alleen
# "contact: ja" of "contact: nee", nooit het contact zelf
UA_MET_CONTACT = "beeld-model-meting/1.0 ({contact}; CI van een zelf-gehost fotoplatform)"
MAX_CONTACT = 200  # tekens
_CONTACT_URL = re.compile(r"https://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:[0-9]{1,5})?(/[!-~]*)?")
_CONTACT_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
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
# De originelen. Ook de oude URL's van thumbnails (.../thumb/...) werken hier nog (zie THUMB)
UPLOAD = "https://upload.wikimedia.org/"
# De thumbnails. Wikimedia haalde de cache van de thumbnails uit het upload-cluster naar een eigen
# host (phabricator T427465, "Move thumbnail caching from upload cluster to text"; uitgerold
# vanaf 2026-08-19, "everywhere" sinds 2026-09-07). Alleen de host verandert; het pad blijft
# /wikipedia/commons/thumb/<a>/<ab>/<naam>/<breedte>px-<naam>. MediaWiki noemt nu die host: de
# thumburl van imageinfo in de API staat op thumb.wikimedia.org (fuzheado/Wikipedia-AI-Skills,
# PR #26, 2026-09-03). De oude URL's op upload werken voorlopig nog; een 301 naar
# thumb.wikimedia.org komt "eventually". De originelen blijven op upload: het pad van een origineel
# op thumb.wikimedia.org geeft een 301 naar https://commons.wikimedia.org/. Op 45bb926 nam _duim
# alleen een thumbnail op upload, en viel in CI elke kandidaat af (x3a.md, "CI op 45bb926"). Deze
# bronnen zijn uit de tweede hand: de sandbox komt niet bij Wikimedia.
# THUMB krijgt ook het contact (_dienst, _user_agent) en is een toegestaan doel van omleidingen
# (_commons_hosts): alleen een host van Wikimedia. Een andere host vraagt eerst toestemming van de
# eigenaar (runbook analyse.md)
THUMB = "https://thumb.wikimedia.org/"
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
# De eerste keus van een voorstel (_tweede_keus; x3a.md, "De set strenger"). Het voorstel op
# 3a9b07d had 34 bestanden van het Metropolitan Museum, schilderijen, prenten en foto's van mensen
# zonder P180 = mens (set-review van run 43816803103). Geen nieuwe harde regel: een kandidaat met
# zo'n teken is tweede keus, en vult een zoekopdracht pas aan als de eerste keus op is, zodat er
# nooit een tekort bij komt (een tekort maakt set rood, en dan draait BZ4 niet).
# Alles uit gegevens die de tool al vraagt: de uitspraken uit wbgetentities en de sjablonen uit
# dezelfde vraag als imageinfo (details).
# - Een kunstwerk: een uitspraak op P6243 (digital representation of), een P31 uit KUNSTWERK_P31,
#   of het sjabloon Artwork of Art Photo (de uploads van musea);
# - een collectie: een uitspraak op P195 (collection) of P217 (inventory number);
# - een persoon (naast P180 = mens, dat altijd afvalt): een P180 uit PERSOON_P180, of het sjabloon
#   Personality rights (de uploader zegt zelf dat er herkenbare mensen op staan).
# Binnen de eerste en binnen de tweede keus eerst een kandidaat waarop de categorie van de
# zoekopdracht prominent is (P180 met rang preferred), dan eigen werk (P7482 = EIGEN_WERK), en
# verder de volgorde van Commons (_voorkeur).
KUNSTWERK_EIGENSCHAPPEN = ("P6243",)
COLLECTIE = ("P195", "P217")
# De ids hieronder komen uit het geheugen en zijn niet nagekeken: www.wikidata.org is vanaf hier
# niet te bereiken, en is geen host van de tool (geen toestemming om hem te vragen). Een verkeerd id
# kan de voorkeur alleen zwakker maken: het maakt hooguit een foto met dat id tweede keus, en sluit
# nooit iets uit (de tweede keus vult aan). Een foto (Q125191) staat er bewust niet in
KUNSTWERK_P31 = {
    "Q3305213": "schilderij",
    "Q93184": "tekening",
    "Q11060274": "prent",
    "Q860861": "beeldhouwwerk",
    "Q838948": "kunstwerk",
    "Q4502142": "beeldend kunstwerk",
    "Q192425": "ansichtkaart",
}
PERSOON_P180 = {
    "Q215627": "persoon",
    "Q8441": "man",
    "Q467": "vrouw",
    "Q7569": "kind",
    "Q3010": "jongen",
    "Q3031": "meisje",
    "Q2472587": "mensen",
    "Q16334295": "groep mensen",
    "Q134307": "portret",
    "Q639669": "muzikant",
    "Q855091": "gitarist",
    "Q177220": "zanger",
}
# De sjablonen die details opvraagt, met waar ze een teken van zijn
SJABLONEN = {
    "Template:Artwork": "kunstwerk",
    "Template:Art Photo": "kunstwerk",
    "Template:Personality rights": "persoon",
}
EIGEN_WERK = "Q66458942"  # P7482 (source of file): original creation by uploader
LICENTIES = {"CC0": "P275=Q6938433", "publiek domein": "P6216=Q19652"}
PER_CATEGORIE = 5
PER_ANDER = 5
MAX_FOTO = 25 << 20  # elke download van de set, ook een origineel dat als thumbnail terugkomt
MIN_ZIJDE = 640  # de korte kant van het origineel
MAX_PIXELS = 50_000_000  # van het origineel
# De set haalt van elke foto een thumbnail van DUIM_BREEDTE breed, niet het origineel. Op 9ebf298
# gaf upload.wikimedia.org ook met BANDBREEDTE een HTTP 429 op een origineel (Retry-After 600; de
# stap stopte na ±30 minuten), en begin 2026 trof zo'n 429 juist originelen voor clients zonder
# browser, terwijl thumbnails bleven laden (phabricator T413570). De richtlijn van Wikimedia voor
# media: liever thumbnails, en dan alleen de vaste maten. Die komen uit de cache van upload; een
# andere maat weigert Wikimedia of remt hij af. De vaste maten ($wgThumbnailSteps; mediawiki.org,
# "Common thumbnail sizes", en phabricator T402792): 20, 40, 60, 120, 250, 330, 500, 960, 1280,
# 1920 en 3840 px breed.
# DUIM_BREEDTE is de kleinste ≥ 1024: de app geeft het model nooit het origineel maar zijn eigen
# previews (256.webp, of 1024.webp als BZ4 dat zegt: Keuze 77; de gezichten uit 1024.webp: Keuze
# 60). Een thumbnail met een lange kant ≥ 1280 geeft in de kwaliteitstest (app.render.image)
# dezelfde maten van 1024.webp en 256.webp als het origineel in de app.
# Een thumbnail maakt Wikimedia zelf, en kan hem later opnieuw maken met andere bytes (een andere
# versie van de software). beeld.tsv heeft daarom de URL, de grootte en de sha256 van precies het
# bestand dat set ophaalt (de thumbnail); streng is dan rood op de sha256, met de sleutel, nooit
# stil. De cache in CI (op de sha256 van beeld.tsv) houdt de eerste goede kopie. De
# bestandspagina van het origineel blijft in de kolom bron (de naamsvermelding).
# Sinds de thumbnails van thumb.wikimedia.org komen (zie THUMB): die host geeft alleen de vaste
# maten (120, 250, 330, 500, 960, 1280, 1920 en 3840 px; ook 20, 40 en 60) en een HTTP 400 op een
# andere, en kiest het formaat op dezelfde URL (.jpg) naar de Accept van de vraag: met
# "image/webp,*/*" (en de Accept van Chrome) WebP, zonder Accept, met "image/jpeg" of met
# "image/avif,*/*" JPEG; het antwoord in WebP heeft geen Vary. Met 1 s tussen twee vragen gaf hij
# geen 429 (een verslag op dev.to, "Wikimedia thumbnails now: thumb.wikimedia.org, a fixed list
# of widths, and WebP only for some clients", nagekeken op 2026-11-14; ook uit de tweede hand).
# Een cache die de Vary mist, kan dus WebP geven op een vraag om JPEG, en de sha256 in beeld.tsv
# hoort bij vaste bytes. Daarom vraagt set elke foto met Accept: ACCEPT, en kijkt hij na de
# download na dat de bytes met JPEG_BEGIN beginnen (haal met jpeg); anders een Fout en niets op
# schijf
DUIM_BREEDTE = 1280
ACCEPT = "image/jpeg"  # de Accept van elke download van de set (haal met jpeg)
JPEG_BEGIN = b"\xff\xd8\xff"  # de eerste bytes van elke JPEG (SOI en het begin van een marker)
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
# beeld_niet.tsv (lees_niet): de foto's die een voorstel nooit neemt, met waarom. Alleen om een
# van deze vier redenen, die een mens aan de foto en zijn pagina ziet, nooit om een score van het
# model (runbook analyse, stap 4)
NIET_KOLOMMEN = ("bron", "reden", "gezien")
NIET_REDENEN = ("persoon", "kunstwerk", "niet-zichtbaar", "toont-categorie")
UITGESLOTEN = "uitgesloten (beeld_niet.tsv)"  # de reden in REDENEN
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


_contact: str | None = None  # BEELD_CONTACT als hij klopt (_zet_contact); None: zonder contact


def _zet_contact(waarde: str | None) -> None:
    """BEELD_CONTACT nakijken, vóór de eerste vraag. Niet gezet of leeg: zonder contact. Anders
    streng: hooguit MAX_CONTACT tekens, alleen afdrukbare ASCII zonder ";", "(" of ")" (die
    breken de vorm van de User-Agent), en een https://-URL of een e-mailadres. Alles anders is een
    Fout, en dan gaat er niets weg. De fout noemt nooit de waarde zelf."""
    global _contact
    _contact = None
    if not waarde:
        return
    if len(waarde) > MAX_CONTACT:
        raise Fout(f"BEELD_CONTACT: langer dan {MAX_CONTACT} tekens")
    if any(not " " <= c <= "~" or c in ";()" for c in waarde):
        raise Fout("BEELD_CONTACT: alleen afdrukbare ASCII, zonder ; ( of )")
    if not (_CONTACT_URL.fullmatch(waarde) or _CONTACT_MAIL.fullmatch(waarde)):
        raise Fout("BEELD_CONTACT: geen https://-URL en geen e-mailadres")
    _contact = waarde


def _user_agent(url: str) -> str:
    """UA, of met BEELD_CONTACT UA_MET_CONTACT, maar dan alleen naar Commons (_dienst). Ook na
    een omleiding geldt de nieuwe URL (_Omleiding)."""
    if _contact is not None and _dienst(url) is not None:
        return UA_MET_CONTACT.format(contact=_contact)
    return UA


def _vraag(
    url: str, *, accept: str | None = None, omleidingen: list | None = None
) -> urllib.request.Request:
    """De vraag voor _opener: de User-Agent voor url, en Accept als die er is. begin en
    omleidingen zijn voor _Omleiding: waar de vraag begon, en (code, nieuwe url) van elke
    omleiding die hij volgde."""
    vraag = urllib.request.Request(url, headers={"User-Agent": _user_agent(url)})
    if accept is not None:
        vraag.add_header("Accept", accept)
    vraag.begin = url
    vraag.omleidingen = omleidingen if omleidingen is not None else []
    return vraag


class Geweigerd(Exception):
    """Een omleiding die _Omleiding niet volgt: naar de nieuwe URL gaat nooit een vraag. Nooit
    opnieuw geprobeerd (_open probeert alleen een HTTPError of een netwerkfout opnieuw)."""

    def __init__(self, code: int, naar: str) -> None:
        self.code = code
        self.naar = naar
        super().__init__(f"HTTP {code} naar {naar}, niet gevolgd ({ALLEEN_COMMONS})")


# Waar een omleiding van een vraag aan Commons heen mag. In de URL's van COMMONS, THUMB en UPLOAD
# (_commons_hosts): https, en die drie hosts
ALLEEN_COMMONS = (
    "van Commons alleen naar https op commons.wikimedia.org, thumb.wikimedia.org of "
    "upload.wikimedia.org"
)


def _oorsprong(url: str) -> tuple[str, str, int] | None:
    """(schema, host, poort) van een url, de poort ook als die er niet staat; None als dat niet
    te lezen is."""
    try:
        deel = urllib.parse.urlsplit(url)
        poort = deel.port or {"https": 443, "http": 80}.get(deel.scheme)
    except ValueError:
        return None
    if not deel.hostname or poort is None:
        return None
    return deel.scheme, deel.hostname, poort


def _commons_hosts() -> set[tuple[str, str, int]]:
    """De hosts van de diensten van Commons, met schema en poort: commons.wikimedia.org,
    thumb.wikimedia.org en upload.wikimedia.org, alle drie over https (uit COMMONS, THUMB en
    UPLOAD; de tests zetten die op hun eigen server)."""
    return {o for o in map(_oorsprong, (COMMONS, THUMB, UPLOAD)) if o is not None}


class _Omleiding(urllib.request.HTTPRedirectHandler):
    """Hoe _opener een omleiding (301, 302, 303, 307, 308) volgt. urllib volgt elke omleiding,
    ook naar een andere host, en neemt de kopregels mee, ook de User-Agent met BEELD_CONTACT. Hier:

    - begon de vraag bij een dienst van Commons (de API, thumb of upload: _dienst), dan volgt hij
      alleen een omleiding naar https op een host van Commons (_commons_hosts); elke andere URL
      is een Geweigerd, en daar gaat nooit een vraag heen. Een vraag die elders begon (Hugging
      Face en zijn CDN) volgt hij zoals urllib;
    - de User-Agent komt opnieuw van _user_agent, voor de nieuwe URL: het contact gaat alleen naar
      een dienst van Commons;
    - de nieuwe vraag is een vraag als elke andere: naar een dienst van Commons wacht hij eerst
      _pauze en telt hij bij die dienst (Teller); naar een andere host niet. Accept gaat mee."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        begin = getattr(req, "begin", req.full_url)
        if _dienst(begin) is not None and _oorsprong(newurl) not in _commons_hosts():
            fp.close()
            raise Geweigerd(code, _hostnaam(newurl))
        nieuw = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nieuw is None:
            return None
        nieuw.add_header("User-Agent", _user_agent(newurl))
        nieuw.begin = begin
        nieuw.omleidingen = getattr(req, "omleidingen", [])
        nieuw.omleidingen.append((code, newurl))
        _pauze(newurl)
        return nieuw


# Alle vragen van de tool gaan door deze opener: urlopen met _Omleiding in plaats van de gewone
# HTTPRedirectHandler
_opener = urllib.request.build_opener(_Omleiding)


# Wikimedia vraagt om vragen na elkaar en om Retry-After te volgen (API:Etiquette). Zonder pauze
# gaf commons.wikimedia.org na ±10 s HTTP 429, ook na drie pogingen (CI-run op 26c49ca)
# Tussen twee vragen aan thumb.wikimedia.org, aan upload.wikimedia.org (elk apart) en aan de API
# met BEELD_CONTACT. Op thumb.wikimedia.org gaf 1 s geen 429 (het verslag op dev.to bij
# DUIM_BREEDTE)
PAUZE_S = 1.0
# De API van Commons heeft sinds maart 2026 grenzen per soort client (wikitech-l, 2 maart 2026,
# "New global API rate limits": eerst lage grenzen voor anonieme vragen, vanaf april hogere voor
# wie zich identificeert). De getallen staan op mediawiki.org, "Wikimedia APIs/Rate limits"; die
# pagina was vanaf hier niet te lezen. Wie haar citeert (in september 2026): zonder
# identificatie (alleen een IP-adres) 10 vragen per minuut, per IP; met een User-Agent volgens de
# richtlijn (met contact: e-mail, een URL of een gebruikerspagina) 200; aangemeld meer. Een
# oudere kopie van de pagina, uit de tijd van de invoering, noemde 500 per uur zonder
# identificatie. Onze User-Agent heeft geen contact, dus geldt die laagste grens, en dat budget
# hoort bij het IP van de runner, dat ook ander verkeer kan hebben. Op 3148c2f gaf de API zo al
# vóór de eerste foto steeds een 429 (Retry-After 50); eerder die dag (f16ed5a, 9ebf298) kwam set
# met 1 vraag per seconde wel door de keuze.
# Tussen twee vragen aan de API (elke poging telt, ook een herhaling na een 429) zit daarom
# minstens PAUZE_API_S: met een vaste tussentijd g zitten er in een venster van 60 s hooguit
# floor(60 / g) + 1 vragen, bij 8 s dus 8 (op 0, 8, ..., 56 s), onder de 10 met ruimte voor een
# klok die anders telt; en in een uur hooguit 451, onder de 500. Met BEELD_CONTACT (200 per
# minuut) is het PAUZE_S. Hoeveel vragen een voorstel doet: zie _kandidaten
PAUZE_API_S = 8.0
# Retry-After geldt altijd (de robotregels): na een 429 of 503 vraagt _open nooit eerder opnieuw
# dan hij zegt. Op 9ebf298 zei upload.wikimedia.org "Retry-After 600", en de tool wachtte toen
# hooguit 120 s: vijf keer te vroeg opnieuw. Zegt Retry-After meer dan MAX_WACHT_S, is hij niet
# te lezen, of past het wachten niet meer in wat er van WACHT_TOTAAL_S over is, dan stopt _open
# meteen, zonder nog een vraag, met een fout die dat zegt
MAX_WACHT_S = 600.0  # de langste wachttijd na één 429 of 5xx die we aannemen
WACHT_TOTAAL_S = 900.0  # samen hooguit zo lang wachten na 429 en 5xx, per proces (per stap)
# De robotregels van Wikimedia (wikitech, Robot_policy, het deel over media): samen hooguit 2
# vragen tegelijk, samen hooguit 25 Mbps gemeten over stukken van 10 s, alleen originelen of de
# vaste maten van de thumbnails, en na een 429 Retry-After volgen. De job vraagt één ding
# tegelijk, maar een origineel is tot MAX_FOTO groot en kwam zonder grens in een fractie van een
# seconde binnen; na ±9 minuten gaf upload.wikimedia.org HTTP 429 (CI-run op f16ed5a). Dus van
# upload gemiddeld hooguit BANDBREEDTE, onder die 25 Mbps. Ook met die grens stopte de stap op een
# 429 (CI-run op 9ebf298: na ±30 minuten; de 429 kwam ±10 minuten eerder, daarna vijf keer 120 s
# gewacht); sindsdien haalt de set thumbnails (DUIM_BREEDTE). Die komen nu van thumb.wikimedia.org
# (THUMB): ook daar gemiddeld hooguit BANDBREEDTE, per host (_rem). De job haalt één ding
# tegelijk, dus nooit van beide tegelijk
BANDBREEDTE = 2_500_000  # bytes per seconde van thumb.wikimedia.org en van upload (20 Mbps)
# Wat het log van een opgegeven 429 of 5xx overneemt: de tekst van <title>, anders van de eerste
# <p>, anders de eerste regel met tekst, zonder de tags van HTML; wat alleen "Error" of "Wikimedia
# Error" zegt, telt niet (_antwoord)
MAX_REGEL = 160  # tekens, alleen afdrukbare ASCII
# Zoveel bytes van het antwoord leest hij hooguit om die tekst te vinden. De foutpagina van
# Wikimedia is ±2,8 kB; de <p> met de reden begint na ±2,2 kB, na de css en een script
MAX_ANTWOORD = 4096
_vorige: dict[str, float] = {}  # per dienst van Commons (_dienst): de laatste vraag
_klaar: dict[str, float] = {}  # per host: wanneer de bytes tot nu toe op BANDBREEDTE binnen zijn
_gewacht = 0.0  # seconden, samen gewacht na 429 en 5xx in dit proces (WACHT_TOTAAL_S)
_opgehaald = 0  # bytes die haal in dit proces binnenkreeg (de voortgang van set)
# De diensten van Commons (_dienst), in de volgorde van het log (Voortgang.verkeer)
DIENSTEN = ("API", "thumb", "upload")


@dataclass
class Teller:
    """Per dienst van Commons, per proces: de vragen (elke poging, ook een herhaling na een 429),
    hoe vaak het antwoord 429 of 5xx was, en wanneer elke 429 kwam (time.monotonic). Op 3148c2f
    zei het log niet hoeveel vragen de API weigerde, en ook niet sinds wanneer."""

    vragen: int = 0
    n429: int = 0
    n5xx: int = 0
    momenten_429: list[float] = field(default_factory=list)


_tellers: dict[str, Teller] = {}


def _teller(dienst: str) -> Teller:
    return _tellers.setdefault(dienst, Teller())


def _dienst(url: str) -> str | None:
    """Welke dienst van Commons een url is: "API" (COMMONS; de tool vraagt commons.wikimedia.org
    niets anders), "thumb" (THUMB, thumb.wikimedia.org: de thumbnails), "upload" (UPLOAD,
    upload.wikimedia.org: de originelen, en oude URL's van thumbnails), of None voor elke andere
    URL: een andere host, en ook commons.wikimedia.org buiten de API (alleen na een omleiding).

    Op de oorsprong (_oorsprong: schema, host in kleine letters, poort ook als die er niet staat)
    en het begin van het pad, zoals de bewaker van _Omleiding de host leest. Een omleiding naar
    https://THUMB.wikimedia.org/… of https://thumb.wikimedia.org:443/… laat de bewaker door; met
    startswith op de hele URL was dat geen dienst, en wachtte die stap niet, telde hij niet en
    remde hij niet (de review van de reparatie, R5)."""
    oorsprong = _oorsprong(url)
    if oorsprong is None:
        return None
    pad = urllib.parse.urlsplit(url).path
    for dienst, basis in (("API", COMMONS), ("thumb", THUMB), ("upload", UPLOAD)):
        if oorsprong == _oorsprong(basis) and pad.startswith(urllib.parse.urlsplit(basis).path):
            return dienst
    return None


def _tussen(dienst: str) -> float:
    """De kortste tijd tussen twee vragen aan een dienst van Commons: PAUZE_API_S voor de API
    zonder contact in de User-Agent, anders PAUZE_S."""
    return PAUZE_API_S if dienst == "API" and _contact is None else PAUZE_S


def _pauze(url: str) -> None:
    """Vóór elke vraag aan Commons, ook vóór elke nieuwe poging van _open: minstens _tussen na
    de vorige vraag aan dezelfde dienst (de API, thumb en upload elk apart; een download loopt
    door in de pauze van de API), en de vraag telt (Teller). Ook een vraag na een omleiding
    (_Omleiding). Andere hosts wachten niet en tellen niet."""
    dienst = _dienst(url)
    if dienst is None:
        return
    rest = _vorige.get(dienst, float("-inf")) + _tussen(dienst) - time.monotonic()
    if rest > 0:
        time.sleep(rest)
    _vorige[dienst] = time.monotonic()
    _teller(dienst).vragen += 1


def _tel_weigering(url: str, code: int) -> None:
    """Een 429 of 5xx van Commons telt bij zijn dienst; van een 429 ook het moment."""
    dienst = _dienst(url)
    if dienst is None:
        return
    teller = _teller(dienst)
    if code == 429:
        teller.n429 += 1
        teller.momenten_429.append(time.monotonic())
    else:
        teller.n5xx += 1


def _rem(url: str, n: int) -> None:
    """Na n bytes van thumb.wikimedia.org of upload.wikimedia.org: wachten tot alles wat er tot nu
    toe van die host binnenkwam, op BANDBREEDTE binnen had kunnen zijn (per host). De schuld loopt
    door van de ene download naar de volgende; stilte spaart geen tegoed op (na een pauze telt hij
    vanaf nu). Andere hosts, en de API, wachten niet."""
    if _dienst(url) not in ("thumb", "upload"):
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
_ALINEA = re.compile(r"<p\b[^>]*>(.*?)(?:</p\s*>|(?=<p\b)|\Z)", re.I | re.S)
# script, style, details en commentaar zijn geen tekst; ook niet als het antwoord na MAX_ANTWOORD
# ophoudt. In <details> zet Wikimedia het IP-adres van de client: dat hoort niet in het log
_BLOK = re.compile(r"<(script|style|details)\b.*?(?:</\1\s*>|\Z)|<!--.*?(?:-->|\Z)", re.I | re.S)
_TAG = re.compile(r"<[^>]*>")
# De titel en de kop van elke foutpagina van Wikimedia: die zeggen niets
_ALGEMEEN = re.compile(r"(wikimedia\s+)?error", re.I)


def _zegt_iets(tekst: str) -> bool:
    return bool(tekst) and not _ALGEMEEN.fullmatch(tekst)


def _antwoord(tekst: str) -> str:
    """De tekst van <title>, anders van de eerste <p>, anders de eerste regel met tekst, zonder de
    tags van HTML. Wat alleen "Error" of "Wikimedia Error" zegt, telt niet als tekst.

    Op 9ebf298 was de eerste regel van een 429 van upload.wikimedia.org "<!DOCTYPE html>": die zei
    niets. De titel ook niet: de foutpagina van Wikimedia heet altijd "Wikimedia Error", met de
    kop <h1>Error</h1>, en de reden staat in de eerste <p> eronder, bij een 429 bijvoorbeeld "Too
    many requests - please contact noc@wikimedia.org ..." (wikimedia/operations-puppet:
    modules/mediawiki/templates/errorpage.html.erb, modules/varnish/manifests/common/errorpage.pp
    en modules/varnish/templates/errorpage.inc.vcl.erb; phabricator T413570). Onderaan staan de
    details van de vraag, met het IP-adres in <details>: die komen nooit mee."""
    kaal = _BLOK.sub("", tekst)
    titel = _TITEL.search(kaal)
    stukken = [titel.group(1)] if titel else []
    stukken += [m.group(1) for m in _ALINEA.finditer(kaal)]
    for stuk in stukken:
        zin = " ".join(_TAG.sub(" ", stuk).split())
        if _zegt_iets(zin):
            return zin
    regels = (r.strip() for r in _TAG.sub("", kaal).split("\n"))
    return next((r for r in regels if _zegt_iets(" ".join(r.split()))), "")


def _zonder_contact(tekst: str, *, heel: bool = True) -> str:
    """Tekst van de server zonder BEELD_CONTACT, vóór die in het log komt (_uitleg). Een
    foutpagina die de User-Agent herhaalt, zette het contact anders in de fout en in kort.txt.
    Ook HTML-gecodeerd ("&amp;"), en hield het antwoord na MAX_ANTWOORD op (heel False), ook een
    begin van het contact aan het eind. Op zijn plaats komt "[contact]", zonder < en >: _antwoord
    haalt tags weg."""
    if _contact is None:
        return tekst
    vormen = sorted({_contact, html.escape(_contact, quote=False), html.escape(_contact)}, key=len)
    for vorm in reversed(vormen):
        tekst = tekst.replace(vorm, "[contact]")
    if not heel:  # het langste begin van een van de vormen
        begin = [n for vorm in vormen for n in range(1, len(vorm)) if tekst.endswith(vorm[:n])]
        if begin:
            tekst = tekst[: -max(begin)] + "[contact]"
    return tekst


def _uitleg(e: urllib.error.HTTPError, retry_after: str | None, stop: str | None = None) -> str:
    """Wat het log zegt als _open het opgeeft na een 429 of 5xx: de laatste Retry-After die hij
    zag, waarom hij meteen stopte (stop, als dat zo was) en de tekst van het antwoord volgens
    _antwoord, alleen afdrukbare ASCII en hooguit MAX_REGEL tekens. Niets anders van het
    antwoord, en nooit BEELD_CONTACT (_zonder_contact)."""
    ra = "geen Retry-After"
    if retry_after:
        ra = f"Retry-After {_leesbaar(_zonder_contact(retry_after), 64)}"
    if stop:
        ra = f"{ra}; {stop}"
    try:
        data = e.read(MAX_ANTWOORD)
    except (OSError, ValueError, http.client.HTTPException):
        data = b""
    finally:
        e.close()
    tekst = _zonder_contact(data.decode("ascii", errors="replace"), heel=len(data) < MAX_ANTWOORD)
    regel = _leesbaar(_antwoord(tekst), MAX_REGEL)
    return f"{ra}; antwoord: {regel}" if regel else f"{ra}; antwoord zonder tekst"


# Retry-After in seconden. De norm zegt hele seconden, maar Wikimedia stuurde in januari 2026 ook
# "11.000" (phabricator T414173); dat telt ook
_SECONDEN = re.compile(r"[0-9]+(\.[0-9]+)?")


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    """Retry-After in seconden (een getal, ook met decimalen, of een HTTP-datum); 0 als hij
    ontbreekt, None als hij er is maar niet te lezen is. Dan weet _open niet hoe lang hij moet
    wachten, en stopt hij meteen (_te_lang): eerder vragen dan Wikimedia wil, mag nooit."""
    waarde = (e.headers.get("Retry-After") if e.headers is not None else None) or ""
    waarde = waarde.strip()
    if not waarde:
        return 0.0
    if _SECONDEN.fullmatch(waarde):
        return float(waarde)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(waarde).timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _te_lang(na: float | None, tijd: float) -> str | None:
    """Waarom _open na een 429 of 5xx meteen stopt, of None als hij tijd seconden mag wachten.
    na: wat Retry-After zei (0 zonder, None als hij niet te lezen was)."""
    if na is None:
        return "niet te lezen, dus meteen gestopt"
    if na > MAX_WACHT_S:
        return f"langer dan MAX_WACHT_S ({MAX_WACHT_S:.0f} s), dus meteen gestopt"
    if _gewacht + tijd > WACHT_TOTAAL_S:
        over = max(0.0, WACHT_TOTAAL_S - _gewacht)
        return (
            f"{tijd:.0f} s wachten past niet in wat er over is van WACHT_TOTAAL_S "
            f"({over:.0f} van {WACHT_TOTAAL_S:.0f} s), dus meteen gestopt"
        )
    return None


def _open(
    url: str,
    *,
    pogingen: int = 6,
    timeout: float = 60,
    accept: str | None = None,
    omleidingen: list | None = None,
):
    """_opener.open met pogingen bij 429, 5xx of een netwerkfout: wachten 2, 4, 8, 16, 32 s, of zo
    lang als Retry-After zegt als dat langer is. Na een 429 of 5xx nooit eerder dan Retry-After
    zegt: is dat meer dan MAX_WACHT_S, is hij niet te lezen, of past het wachten (ook 2 tot 32 s
    zonder Retry-After) niet meer in WACHT_TOTAAL_S, dan stopt hij meteen. Geeft hij het op na
    een 429 of 5xx, dan krijgt de HTTPError een uitleg voor het log mee (_uitleg, _reden): op
    f16ed5a zei "HTTP 429" niet wat Wikimedia vroeg. Elke poging gaat door _pauze (en telt), dus
    ook na het wachten nooit eerder dan PAUZE_API_S na de vorige vraag aan de API; het wachten en
    de pauze lopen samen, ze tellen niet op. accept: de kopregel Accept (ACCEPT voor de set).
    omleidingen: een lijst die (code, nieuwe url) krijgt van elke omleiding die de laatste poging
    volgde (_Omleiding; voor bereik). Een omleiding die _Omleiding weigert, is een Geweigerd:
    nooit een nieuwe poging. Een 429 of 5xx na een omleiding telt bij de dienst die hem gaf."""
    global _gewacht
    wacht = 2.0
    retry_after: str | None = None  # de laatste die hij zag, zoals hij binnenkwam
    for poging in range(pogingen):
        _pauze(url)
        if omleidingen is not None:
            omleidingen.clear()
        try:
            vraag = _vraag(url, accept=accept, omleidingen=omleidingen)
            return _opener.open(vraag, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            _tel_weigering(e.geturl() or url, e.code)
            if e.headers is not None and e.headers.get("Retry-After"):
                retry_after = e.headers.get("Retry-After")
            if poging == pogingen - 1:
                e.uitleg = _uitleg(e, retry_after)
                raise
            na = _retry_after(e)
            tijd = max(wacht, na or 0.0)
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
    except (urllib.error.URLError, OSError, Geweigerd) as e:
        raise Fout(f"{_host(url)}: {_reden(e)}") from None
    if len(data) > max_bytes:
        raise Fout(f"{_host(url)}: antwoord groter dan {max_bytes} bytes")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Fout(f"{_host(url)}: geen geldige JSON") from None


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or "?"


# Een gewone hostnaam: letters, cijfers en liggende streepjes, in delen gescheiden door punten
_HOSTNAAM = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")
MAX_HOSTNAAM = 64  # tekens


def _hostnaam(url: str) -> str:
    """De host van een url die van buiten komt (een thumburl, een omleiding), voor het log: alleen
    als het een gewone hostnaam is van hooguit MAX_HOSTNAAM tekens, anders "een onbekende host".
    Nooit de hele URL."""
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    if len(host) <= MAX_HOSTNAAM and _HOSTNAAM.fullmatch(host):
        return host
    return "een onbekende host"


def _reden(e: BaseException) -> str:
    if isinstance(e, Geweigerd):
        return str(e)
    if isinstance(e, urllib.error.HTTPError):
        # vars(): HTTPError geeft een onbekend attribuut door aan zijn antwoord
        uitleg = vars(e).get("uitleg")
        return f"HTTP {e.code} ({uitleg})" if uitleg else f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return f"niet bereikbaar ({type(e.reason).__name__})"
    return type(e).__name__


def _geen_jpeg(naam: str, kop: bytes) -> Fout | None:
    """De Fout als een download van de set niet met JPEG_BEGIN begint, met wat hij wel is:
    "<naam>: WebP in plaats van JPEG" (RIFF....WEBP), anders "<naam>: geen JPEG"; None als het
    een JPEG is. kop: de eerste (hooguit 12) bytes."""
    if kop.startswith(JPEG_BEGIN):
        return None
    if kop[:4] == b"RIFF" and kop[8:12] == b"WEBP":
        return Fout(f"{naam}: WebP in plaats van JPEG")
    return Fout(f"{naam}: geen JPEG")


def haal(
    url: str, doel: Path, *, max_bytes: int, grootte=None, sha256=None, jpeg: bool = False
) -> tuple[str, int]:
    """Eén bestand naar doel via doel.part. Met grootte en sha256: alleen die bytes komen op hun
    plek. Geeft (sha256, bytes).

    jpeg (een foto van de set; Voortgang.haal): de vraag met Accept: ACCEPT, en de bytes moeten
    met JPEG_BEGIN beginnen. Zo niet, dan stopt hij zodra hij de eerste 12 bytes heeft, met een
    Fout die zegt wat er kwam ("WebP in plaats van JPEG" of "geen JPEG"), en blijft er niets
    staan (zie DUIM_BREEDTE: thumb.wikimedia.org kan op dezelfde URL WebP geven). Zonder jpeg
    (Hugging Face, de bron) zoals altijd: geen Accept en geen controle op de bytes."""
    global _opgehaald
    part = doel.with_name(doel.name + ".part")
    grens = grootte if grootte is not None else max_bytes
    h = hashlib.sha256()
    n = 0
    kop = b""  # de eerste 12 bytes, voor jpeg
    try:
        try:
            # set/ kan uit een cache komen (CI op f16ed5a): een .part die er al staat, ook een
            # symlink, gaat eerst weg, en "xb" schrijft nooit door een link naar elders
            part.unlink(missing_ok=True)
            accept = ACCEPT if jpeg else None
            with _open(url, timeout=120, accept=accept) as antwoord, part.open("xb") as f:
                van = antwoord.geturl()  # na een omleiding de host die de bytes geeft (_rem)
                while stuk := antwoord.read(1 << 20):
                    n += len(stuk)
                    _opgehaald += len(stuk)
                    if jpeg and len(kop) < 12:
                        kop += stuk[: 12 - len(kop)]
                        if len(kop) == 12 and (fout := _geen_jpeg(doel.name, kop)):
                            raise fout
                    if n > grens:
                        raise Fout(f"{doel.name}: groter dan {grens} bytes")
                    h.update(stuk)
                    f.write(stuk)
                    _rem(van, len(stuk))
        except (urllib.error.URLError, OSError, Geweigerd) as e:
            raise Fout(f"{doel.name}: {_reden(e)}") from None
        if jpeg and n and (fout := _geen_jpeg(doel.name, kop)):
            raise fout
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


def _via(omleidingen: list, eind: str, *, daar: bool = True) -> str:
    """Wat BZ1 tussen haakjes zet: "HTTP 200", of na een omleiding (_Omleiding) ook waar die heen
    ging en wat daar het antwoord was: "HTTP 301 naar commons.wikimedia.org, daar HTTP 200". Zo
    zegt de regel wat de host zelf antwoordde, ook als zijn wortel omleidt. eind: het laatste
    antwoord ("HTTP 200"), of (daar False) een omleiding die de tool niet volgde (Geweigerd)."""
    stappen = [f"HTTP {c} naar {_hostnaam(naar)}" for c, naar in omleidingen]
    if stappen and daar:
        eind = f"daar {eind}"
    return ", ".join([*stappen, eind])


def bereik(werk: Werk) -> int:
    """BZ1: per host bereikbaar of niet, met het antwoord. thumb.wikimedia.org erbij sinds de
    thumbnails daar staan (THUMB). Een omleiding die _Omleiding weigert, zegt de regel ook: de
    host is bereikbaar, maar de tool volgt hem niet."""
    hosts = (
        ("huggingface.co", f"{HF}/api/models/{REPO}"),
        ("commons.wikimedia.org", f"{COMMONS}?action=query&meta=siteinfo&format=json"),
        ("thumb.wikimedia.org", THUMB),
        ("upload.wikimedia.org", UPLOAD),
        ("images-assets.nasa.gov", NASA),
    )
    for naam, url in hosts:
        omleidingen: list = []
        try:
            with _open(url, pogingen=2, timeout=30, omleidingen=omleidingen) as antwoord:
                via = _via(omleidingen, f"HTTP {antwoord.status}")
                werk.meld(f"BZ1 {naam}: bereikbaar ({via})")
        except urllib.error.HTTPError as e:
            werk.meld(f"BZ1 {naam}: bereikbaar ({_via(omleidingen, f'HTTP {e.code}')})")
        except Geweigerd as e:
            werk.meld(f"BZ1 {naam}: bereikbaar ({_via(omleidingen, str(e), daar=False)})")
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
        # Een thumbnail op thumb.wikimedia.org (zoals de API hem nu geeft) of, de oude vorm, op
        # upload; een origineel alleen op upload (zie THUMB)
        op_thumb = url.startswith(THUMB)
        if not (op_thumb or url.startswith(UPLOAD)) or not re.fullmatch(r"[!-~]+", url):
            raise Fout(f"beeld.tsv regel {nr}: url hoort bij {THUMB} of {UPLOAD}")
        # Een thumbnail alleen van de vaste maat: een andere maat weigert Wikimedia of remt hij af
        duim = "thumb" in urllib.parse.urlsplit(url).path.split("/")
        if duim and not _is_duim(url):
            raise Fout(f"beeld.tsv regel {nr}: url is een thumbnail, maar niet {DUIM_BREEDTE} px")
        # Het pad van een origineel op thumb.wikimedia.org geeft een 301 naar commons.wikimedia.org
        if op_thumb and not duim:
            raise Fout(
                f"beeld.tsv regel {nr}: url op {THUMB} is geen thumbnail; een origineel hoort bij "
                f"{UPLOAD}"
            )
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


@dataclass(frozen=True)
class Niet:
    """Een regel van beeld_niet.tsv: een foto die een voorstel nooit neemt."""

    bron: str
    reden: str  # een van NIET_REDENEN
    gezien: str  # waar een mens hem zag, zoals "stad-5 op 3a9b07d"
    regel: int  # het regelnummer in het bestand


def _bron_sleutel(bron_: str) -> str:
    """Waarop beeld_niet.tsv een bron vergelijkt: zonder procentcodering en met _ voor een spatie.
    Zo is "R%C3%A4v" gelijk aan "R%c3%a4v", en "File:Twee%20woorden" aan "File:Twee_woorden"."""
    return urllib.parse.unquote(bron_).replace(" ", "_")


def lees_niet(tekst: str | None) -> dict[str, Niet]:
    """beeld_niet.tsv, streng nagekeken, op _bron_sleutel. Fout met het regelnummer in het bestand
    (commentaar en lege regels tellen mee, zoals in een editor). None (geen bestand) is een lege
    lijst: dan is een voorstel hetzelfde als zonder de lijst."""
    if tekst is None:
        return {}
    uit: dict[str, Niet] = {}
    kop = False
    for nr, regel in enumerate(tekst.splitlines(), start=1):
        if not regel or regel.startswith("#"):
            continue
        v = regel.split("\t")
        if not kop:
            if tuple(v) != NIET_KOLOMMEN:
                raise Fout(
                    f"beeld_niet.tsv regel {nr}: de eerste regel hoort {' '.join(NIET_KOLOMMEN)}"
                )
            kop = True
            continue
        if len(v) != len(NIET_KOLOMMEN):
            raise Fout(f"beeld_niet.tsv regel {nr}: {len(v)} velden, verwacht {len(NIET_KOLOMMEN)}")
        bron_, reden, gezien = v
        pagina = "https://commons.wikimedia.org/wiki/File:"
        if not bron_.startswith(pagina) or bron_ == pagina or not re.fullmatch(r"[!-~]+", bron_):
            raise Fout(
                f"beeld_niet.tsv regel {nr}: bron hoort een bestandspagina van Commons, zoals in "
                "de kolom bron van het voorstel (zonder spaties)"
            )
        if reden not in NIET_REDENEN:
            raise Fout(
                f"beeld_niet.tsv regel {nr}: reden {reden!r}; alleen {', '.join(NIET_REDENEN)}"
            )
        if not gezien.strip() or not re.fullmatch(r"[^\x00-\x1f\x7f]{1,80}", gezien):
            raise Fout(
                f"beeld_niet.tsv regel {nr}: gezien hoort een korte tekst (hooguit 80 tekens), "
                'zoals "stad-5 op 3a9b07d"'
            )
        sleutel = _bron_sleutel(bron_)
        if sleutel in uit:
            raise Fout(
                f"beeld_niet.tsv regel {nr}: die bron staat er al (regel {uit[sleutel].regel})"
            )
        uit[sleutel] = Niet(bron_, reden, gezien, nr)
    if not kop:
        raise Fout(f"beeld_niet.tsv: de eerste regel hoort {' '.join(NIET_KOLOMMEN)}")
    return uit


def _lees_niet_pad(pad: Path) -> dict[str, Niet] | None:
    """beeld_niet.tsv van pad; None als het bestand er niet is."""
    if not pad.is_file():
        return None
    try:
        return lees_niet(pad.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise Fout("beeld_niet.tsv: geen UTF-8") from None


def _op_lijst(d: dict, niet: dict[str, Niet]) -> bool:
    bron_ = d.get("bron")
    return bool(niet) and isinstance(bron_, str) and _bron_sleutel(bron_) in niet


def _niet_in_set(fotos: list[Foto], niet: dict[str, Niet], naam: str) -> None:
    """Fout als een foto van de set (beeld.tsv, of een voorstel: naam) in beeld_niet.tsv staat,
    met alle zulke foto's: de regel van de set (zoals lees_set telt), de sleutel, en de regel en
    de reden in beeld_niet.tsv."""
    fout = []
    for i, f in enumerate(fotos):
        n = niet.get(_bron_sleutel(f.bron))
        if n is not None:
            fout.append(
                f"regel {i + 2} ({f.sleutel}) staat in beeld_niet.tsv regel {n.regel} ({n.reden})"
            )
    if fout:
        raise Fout(
            f"{naam}: {'; '.join(fout)}; zet er een andere foto voor in de plaats (runbook "
            "analyse, stap 4)"
        )


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
    gegevens zeggen (P180, P275, P6216; voor de eerste keus ook P31, P7482, de rang van P180 en of
    er een uitspraak is op P6243, P195 of P217) en welke SJABLONEN de bestandspagina gebruikt.

    Twee vragen, zoals altijd: imageinfo en wbgetentities. De sjablonen komen mee in de eerste
    (prop=imageinfo|templates, met tltemplates alleen SJABLONEN): geen vraag erbij. wbgetentities
    gaf altijd al de hele MediaInfo (elke uitspraak met zijn rang); de tool las er alleen minder
    van. Geeft de API de sjablonen niet in één keer (tlcontinue; met 50 pagina's en drie
    sjablonen hooguit 150 van de 500), dan zijn ze voor die pagina's onbekend: sjablonen_heel
    False, en dan is geen van die kandidaten eerste keus (_tweede_keus)."""
    if not pageids:
        return {}
    q = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "imageinfo|templates",
        "iiprop": "url|size|mime",
        "iiurlwidth": str(DUIM_BREEDTE),
        "tltemplates": "|".join(SJABLONEN),
        "tllimit": "max",
        "pageids": "|".join(map(str, pageids)),
    }
    doc = json_van(f"{COMMONS}?{urllib.parse.urlencode(q)}")
    verder = doc.get("continue")
    heel = not (isinstance(verder, dict) and "tlcontinue" in verder)
    uit: dict[int, dict] = {}
    for p in (doc.get("query") or {}).get("pages") or []:
        info = (p.get("imageinfo") or [None])[0]
        if isinstance(info, dict):
            sjablonen = {t.get("title") for t in p.get("templates") or [] if isinstance(t, dict)}
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
                "sjablonen": sjablonen & set(SJABLONEN),
                "sjablonen_heel": heel,
            }
    q = {"action": "wbgetentities", "format": "json", "ids": "|".join(f"M{i}" for i in uit)}
    ent = json_van(f"{COMMONS}?{urllib.parse.urlencode(q)}").get("entities") or {}
    for i, d in uit.items():
        e = ent.get(f"M{i}") or {}
        uitspraken = e.get("statements") or e.get("claims") or {}
        if not isinstance(uitspraken, dict):
            uitspraken = {}
        for eigenschap in ("P180", "P275", "P6216", "P31", "P7482"):
            d[eigenschap] = _waarden(uitspraken.get(eigenschap))
        d["P180_prominent"] = _waarden(uitspraken.get("P180"), rang="preferred")
        d["P180_aantal"] = _aantal(uitspraken.get("P180"))
        d["aanwezig"] = {p for p in (*KUNSTWERK_EIGENSCHAPPEN, *COLLECTIE) if uitspraken.get(p)}
    return uit


def _waarden(lijst: object, *, rang: str | None = None) -> set[str]:
    """De ids van de uitspraken met een waarde; met rang alleen die met die rang ("preferred":
    "mark as prominent" op Commons)."""
    uit = set()
    for s in lijst if isinstance(lijst, list) else []:
        try:
            if rang is not None and s.get("rank") != rang:
                continue
            if s["mainsnak"]["snaktype"] == "value":
                uit.add(s["mainsnak"]["datavalue"]["value"]["id"])
        except (AttributeError, KeyError, TypeError):
            continue
    return uit


def _aantal(lijst: object) -> int:
    """Hoeveel uitspraken met een waarde (voor het bewijs: een lange lijst P180 komt vaak van
    trefwoorden van een museum)."""
    n = 0
    for s in lijst if isinstance(lijst, list) else []:
        try:
            n += s["mainsnak"]["snaktype"] == "value"
        except (KeyError, TypeError):
            continue
    return n


def _licentie(d: dict) -> str | None:
    if "Q6938433" in d["P275"]:
        return "CC0"
    if "Q19652" in d["P6216"]:
        return "publiek domein"
    return None


def _is_duim(url: str) -> bool:
    """Het pad van een thumbnail van DUIM_BREEDTE breed (op thumb of, de oude vorm, op upload):
    .../thumb/<pad>/<naam>/1280px-<naam>."""
    deel = urllib.parse.urlsplit(url).path.split("/")
    return "thumb" in deel[:-2] and deel[-1].startswith(f"{DUIM_BREEDTE}px-")


def _soort(url: str) -> str:
    """Wat set haalt, voor het log: de thumbnail of het origineel zelf."""
    return f"een thumbnail van {DUIM_BREEDTE} px" if _is_duim(url) else "een origineel"


def _genoeg(breedte: int, hoogte: int) -> bool:
    """Is de korte kant van de 1024.webp die app.render.image van een foto van deze maat maakt
    (lange kant hooguit PREVIEW, nooit vergroot), minstens INVOER? Zie INVOER."""
    return min(breedte, hoogte) * PREVIEW >= INVOER * max(breedte, hoogte, PREVIEW)


# Waarom set een kandidaat niet neemt (_waarom), een vaste reeks; in de regels van een tekort met
# hun aantal (_afgevallen). "thumbnail op een andere host" krijgt de host erbij (_hostnaam)
GEEN_DUIM = f"geen thumbnail van {DUIM_BREEDTE} px"
ANDERE_HOST = "thumbnail op een andere host"
REDENEN = (
    "geen image/jpeg",  # mime
    "origineel niet op upload",  # url
    "geen bestandspagina",  # bron
    "te klein",  # de korte kant onder MIN_ZIJDE, of 0 bytes
    "te groot",  # meer dan MAX_PIXELS, of een origineel als thumbnail groter dan MAX_FOTO
    "mens erop",  # P180 = mens
    "licentie",  # geen CC0 en geen publiek domein
    GEEN_DUIM,  # geen thumburl van 1280 breed (ook: een breder origineel als thumburl)
    ANDERE_HOST,  # een thumburl buiten thumb en upload
    "te langgerekt",  # de thumbnail geeft een 1024.webp met een korte kant onder INVOER
    "onleesbare gegevens",  # een veld dat geen getal is, of een URL met spaties
    "al gekozen",  # stel_voor: al in de set (een combinatie of een andere categorie)
    UITGESLOTEN,  # stel_voor en de reserves: de bron staat in beeld_niet.tsv
    "beeldt een categorie af",  # stel_voor: een andere foto die een van de twaalf afbeeldt
)


def _waarom_duim(d: dict) -> str | None:
    """Waarom set van een foto niets kan ophalen (een van REDENEN), of None: dan geeft _duim wat
    hij ophaalt. Een thumbnail van DUIM_BREEDTE breed op thumb.wikimedia.org (de normale vorm
    sinds T427465, zie THUMB) of op upload (de oude vorm), met het pad van _is_duim en thumbwidth
    1280. Is het origineel niet breder, dan geeft MediaWiki het origineel zelf als thumburl: alleen
    op upload, en niet groter dan MAX_FOTO. Hoe breed MediaWiki dan in thumbwidth zegt, telt niet:
    de maten van het origineel gelden."""
    try:
        url = d.get("duim")
        if not isinstance(url, str):
            return GEEN_DUIM
        if not re.fullmatch(r"[!-~]+", url):
            return "onleesbare gegevens"
        if url == d["url"]:
            if not url.startswith(UPLOAD):
                return f"{ANDERE_HOST} ({_hostnaam(url)})"
            breedte, hoogte, n = int(d["breedte"]), int(d["hoogte"]), int(d["bytes"])
            if breedte > DUIM_BREEDTE:
                return GEEN_DUIM
            if n <= 0:
                return "te klein"
            if n > MAX_FOTO:
                return "te groot"
        else:
            if not url.startswith((THUMB, UPLOAD)):
                return f"{ANDERE_HOST} ({_hostnaam(url)})"
            if not _is_duim(url) or int(d.get("duim_breedte")) != DUIM_BREEDTE:
                return GEEN_DUIM
            breedte, hoogte = DUIM_BREEDTE, int(d.get("duim_hoogte"))
        return None if _genoeg(breedte, hoogte) else "te langgerekt"
    except (TypeError, ValueError):
        return "onleesbare gegevens"


def _duim(d: dict) -> tuple[str, int | None] | None:
    """Wat set van een foto ophaalt: (url, bytes als die vooraf bekend zijn), of None als dat niet
    kan (_waarom_duim zegt waarom). Meestal de thumbnail van DUIM_BREEDTE breed (bytes onbekend;
    haal stopt bij MAX_FOTO); van een origineel dat niet breder is het origineel zelf (de bytes
    zijn dan vooraf bekend). Een groot origineel met een thumbnail mag: set haalt alleen die
    thumbnail."""
    if _waarom_duim(d) is not None:
        return None
    url = d["duim"]
    return (url, int(d["bytes"])) if url == d["url"] else (url, None)


def _waarom(d: dict) -> str | None:
    """Waarom set een kandidaat niet neemt: de eerste reden uit REDENEN die geldt, of None als hij
    bruikbaar is. Op 45bb926 zei "maar 0 bruikbare foto's voor strand (400 kandidaten bekeken)"
    niet waarom; nu staan de redenen met hun aantal in die regel (_afgevallen)."""
    try:
        if d["mime"] != "image/jpeg":
            return "geen image/jpeg"
        if not isinstance(d["url"], str) or not d["url"].startswith(UPLOAD):
            return "origineel niet op upload"
        bron_ = d["bron"]
        if not isinstance(bron_, str) or not bron_.startswith(
            "https://commons.wikimedia.org/wiki/File:"
        ):
            return "geen bestandspagina"
        breedte, hoogte, n = int(d["breedte"]), int(d["hoogte"]), int(d["bytes"])
        if n <= 0 or min(breedte, hoogte) < MIN_ZIJDE:
            return "te klein"
        if breedte * hoogte > MAX_PIXELS:
            return "te groot"
        if MENS in d["P180"]:
            return "mens erop"
        if _licentie(d) is None:
            return "licentie"
    except (TypeError, ValueError):
        return "onleesbare gegevens"
    return _waarom_duim(d)


def _bruikbaar(d: dict) -> bool:
    return _waarom(d) is None


MAX_REDENEN = 4  # zoveel redenen hooguit in een regel van een tekort


def _afgevallen(redenen: dict[str, int], woord: str = "afgevallen") -> str:
    """Wat er afviel, voor de regel van een tekort: "; afgevallen: 371 thumbnail op een andere
    host (thumb.wikimedia.org), 29 te klein". De MAX_REDENEN redenen die het vaakst voorkwamen,
    de meeste eerst (bij een gelijk aantal op de tekst), en de rest samen als "N om een andere
    reden"; leeg als er niets afviel. woord: wat ervoor staat ("geen eerste keus" voor de redenen
    van TWEEDE_KEUS)."""
    if not redenen:
        return ""
    rij = sorted(redenen.items(), key=lambda r: (-r[1], r[0]))
    delen = [f"{n} {reden}" for reden, n in rij[:MAX_REDENEN]]
    rest = sum(n for _, n in rij[MAX_REDENEN:])
    if rest:
        delen.append(f"{rest} om een andere reden")
    return f"; {woord}: " + ", ".join(delen)


# Waarom een bruikbare kandidaat tweede keus is (_tweede_keus), de eerste die geldt, in deze
# volgorde. "sjablonen onvolledig": de API gaf de sjablonen niet in één keer (details)
TWEEDE_KEUS = ("kunstwerk", "collectie", "persoon", "sjablonen onvolledig")


def _kenmerken(d: dict) -> dict[str, list[str]]:
    """Per teken (kunstwerk, collectie, persoon) waar het uit blijkt, in een vaste volgorde: de
    eigenschap ("P6243", "P195"), de waarde ("P31 Q3305213", "P180 Q8441") of het sjabloon
    ("Artwork"). Alleen namen en ids uit de vaste lijsten van de tool, nooit tekst van Commons.
    Een veld dat ontbreekt (een kandidaat van vóór deze velden), telt als niets."""
    aanwezig = d.get("aanwezig") or set()
    sjablonen = sorted(d.get("sjablonen") or set())
    uit: dict[str, list[str]] = {
        "kunstwerk": [p for p in KUNSTWERK_EIGENSCHAPPEN if p in aanwezig],
        "collectie": [p for p in COLLECTIE if p in aanwezig],
        "persoon": [],
    }
    uit["kunstwerk"] += [f"P31 {q}" for q in sorted((d.get("P31") or set()) & set(KUNSTWERK_P31))]
    uit["persoon"] += [f"P180 {q}" for q in sorted((d.get("P180") or set()) & set(PERSOON_P180))]
    for titel in sjablonen:
        uit[SJABLONEN[titel]].append(titel.removeprefix("Template:"))
    return uit


def _tweede_keus(d: dict) -> str | None:
    """Waarom een bruikbare kandidaat tweede keus is (een van TWEEDE_KEUS), of None: eerste
    keus. Nooit een reden om af te vallen: een tweede keus vult aan (stel_voor)."""
    kenmerken = _kenmerken(d)
    for reden in ("kunstwerk", "collectie", "persoon"):
        if kenmerken[reden]:
            return reden
    if d.get("sjablonen_heel") is False:
        return "sjablonen onvolledig"
    return None


def _prominent(d: dict, qids: tuple[str, ...]) -> int:
    """Hoeveel van de qids van de zoekopdracht prominent zijn: P180 met rang preferred."""
    prominent = d.get("P180_prominent") or set()
    return sum(1 for q in qids if q in prominent)


def _voorkeur(d: dict, qids: tuple[str, ...]) -> tuple[int, bool]:
    """De volgorde binnen de eerste of de tweede keus: eerst meer qids prominent, dan eigen werk.
    Verder de volgorde van Commons (sorted is stabiel; _kandidaten)."""
    return (-_prominent(d, qids), EIGEN_WERK not in (d.get("P7482") or set()))


def _bewijs(d: dict, qids: tuple[str, ...], eerste: bool) -> str:
    """Wat het log per gekozen foto zegt (stel_voor): eerste of tweede keus, en de tekens, met
    waar ze uit blijken. Bijvoorbeeld "tweede keus; kunstwerk ja (P6243, Artwork), collectie ja
    (P195), persoon nee, eigen werk nee, prominent nee, P180 14"."""
    kenmerken = _kenmerken(d)
    delen = []
    for naam in ("kunstwerk", "collectie", "persoon"):
        bron_ = kenmerken[naam]
        delen.append(f"{naam} ja ({', '.join(bron_)})" if bron_ else f"{naam} nee")
    delen.append(f"eigen werk {'ja' if EIGEN_WERK in (d.get('P7482') or set()) else 'nee'}")
    n = _prominent(d, qids)
    prominent = "nee" if n == 0 else "ja" if n == len(qids) else f"{n} van {len(qids)}"
    delen.append(f"prominent {prominent}")
    delen.append(f"P180 {d.get('P180_aantal', len(d.get('P180') or ()))}")
    if d.get("sjablonen_heel") is False:
        delen.append("sjablonen onvolledig")
    return f"{'eerste' if eerste else 'tweede'} keus; {', '.join(delen)}"


# Hoeveel vragen een voorstel aan de API doet, en dus hoe lang het duurt op PAUZE_API_S (8 s).
# Per zoekopdracht (een combinatie van beeld_vragen.tsv, een categorie of een ander onderwerp) en
# per pagina van 50 drie vragen: zoek, imageinfo (met de sjablonen) en wbgetentities (details);
# een lege pagina één. Een zoekopdracht stopt na de pagina waarop hij genoeg eerste keus heeft
# (_tweede_keus), een combinatie na één, de rest na vijf, meestal op de eerste pagina van de
# eerste licentie. Pas als de eerste keus op de pagina's tot dan te weinig is, leest hij de
# volgende pagina, tot het uiterste van vroeger (2 licenties × max_pagina), en vult hij daarna aan
# met de tweede keus van alle pagina's die hij las: elke extra pagina is 3 vragen, maar nooit een
# pagina meer dan het uiterste, en een tekort alleen als vroeger ook. Vóór de eerste keus las hij
# soms een pagina meer (die kwam binnen voordat de lus zag dat hij klaar was); nu niet meer. Met 6
# combinaties, 12 categorieën en 12 andere onderwerpen zijn dat minstens 30 × 3 = 90 vragen, 12
# minuten (met contact 1,5 minuut); met tweede pagina's of tekorten meer. Hooguit 2 licenties ×
# max_pagina (4) × 3 = 24 per zoekopdracht, 720 samen, plus de reserves alleen bij een tekort (5
# paren, de eerste pagina per licentie: 30): 750 vragen, 100 minuten zonder contact, 12,5 met
# contact (PAUZE_S; zo draait de CI). De 120 thumbnails (PAUZE_S en BANDBREEDTE) lopen grotendeels
# door in de pauzes van de API. Of het past in timeout-minutes van de job beeld-model: zie de
# opmerking daar in ci.yml
def _paginas(qids: tuple[str, ...], *, max_pagina: int = 4):
    """Per pagina van 50 de bestanden die alle qids afbeelden, met CC0 of publiek domein, zonder
    mens, op pageid; per licentie tot max_pagina pagina's. Een pagina komt pas als de vorige op is
    (elke pagina is 3 vragen; zie hierboven)."""
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
            yield sorted(details(ids).items())


def _kandidaten(qids: tuple[str, ...], *, max_pagina: int = 4):
    """De bestanden van _paginas na elkaar (de reserves)."""
    for pagina in _paginas(qids, max_pagina=max_pagina):
        yield from pagina


class Voortgang:
    """Hoe ver set kwam: in de fout ("bij foto 37 van 120, een thumbnail van 1280 px, 12,3 MB
    opgehaald"; CI op 9ebf298 zei niet hoe ver de stap was) en, als het goed ging, in de regel van
    het blok (hoeveel originelen, bytes en tijd). Of de download die misging een thumbnail of een
    origineel was, zegt of thumbnails de 429 voorkomen (x3a.md, "Open na 9ebf298"). In beide
    ook het verkeer met Commons sinds het begin van set (verkeer)."""

    def __init__(self, totaal: int) -> None:
        self.totaal = totaal
        self.klaar = 0  # foto's die al goed op hun plek staan
        self.gekozen = False  # het voorstel koos alles wat Commons had (en kwam te kort)
        self.bezig: str | None = None  # wat haal nu ophaalt (_soort), of None tussen downloads
        self.originelen = 0  # downloads van een origineel in plaats van een thumbnail
        self._bytes = _opgehaald
        self._begin = time.monotonic()
        # wat de tellers al hadden: de regel zegt alleen wat set zelf vroeg
        self._voor = {d: (_teller(d).vragen, _teller(d).n429, _teller(d).n5xx) for d in DIENSTEN}

    def haal(self, url: str, doel: Path, **kw) -> tuple[str, int]:
        """haal als JPEG (jpeg: Accept: ACCEPT en de eerste bytes nagekeken), en onthoud wat er
        opgehaald wordt (voor de fout) en of het een origineel was."""
        self.bezig = _soort(url)
        uit = haal(url, doel, jpeg=True, **kw)
        self.bezig = None
        if not _is_duim(url):
            self.originelen += 1
        return uit

    def bytes(self) -> int:
        return _opgehaald - self._bytes

    def waar(self) -> str:
        if self.gekozen:
            gehaald = f"{self.klaar} van {self.totaal} foto's opgehaald, {_mb(self.bytes())}"
            return f"{gehaald}; {self.verkeer()}"
        n = min(self.klaar + 1, self.totaal)
        soort = f", {self.bezig}" if self.bezig else ""
        bij = f"bij foto {n} van {self.totaal}{soort}, {_mb(self.bytes())} opgehaald"
        return f"{bij}; {self.verkeer()}"

    def samen(self) -> str:
        """Hoeveel originelen, bytes en tijd, voor de regel van een goede set."""
        tijd = time.monotonic() - self._begin
        gehaald = f"{self.originelen} als origineel; {_mb(self.bytes())} in {tijd:.0f} s"
        return f"{gehaald}; {self.verkeer()}"

    def verkeer(self) -> str:
        """Per dienst van Commons (DIENSTEN: altijd alle drie, in die volgorde) de vragen sinds
        het begin van set (elke poging telt, ook een vraag na een omleiding), hoe vaak het
        antwoord 429 was (en 5xx, als dat er waren) en na hoeveel seconden de eerste 429 kwam, en
        dan of de User-Agent een contact had, nooit het contact zelf. Bijvoorbeeld "API: 23
        vragen, 17 keer 429, eerste 429 na 3 s; thumb: 0 vragen; upload: 0 vragen; contact:
        nee"."""
        delen = []
        for dienst in DIENSTEN:
            teller = _teller(dienst)
            vragen, n429, n5xx = self._voor[dienst]
            n = teller.vragen - vragen
            if not n:
                delen.append(f"{dienst}: 0 vragen")
                continue
            deel = f"{dienst}: {n} {'vraag' if n == 1 else 'vragen'}, {teller.n429 - n429} keer 429"
            if teller.n5xx > n5xx:
                deel += f", {teller.n5xx - n5xx} keer 5xx"
            if teller.n429 > n429:
                eerste = teller.momenten_429[n429] - self._begin
                deel += f", eerste 429 na {eerste:.0f} s"
            delen.append(deel)
        delen.append(f"contact: {'nee' if _contact is None else 'ja'}")
        return "; ".join(delen)


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB".replace(".", ",")


def stel_voor(
    werk: Werk,
    *,
    combis=None,
    voortgang: Voortgang | None = None,
    niet: dict[str, Niet] | None = None,
) -> list[Foto]:
    """120 foto's volgens Keuze 83, en ze meteen ophalen (de sha256 komt van de download): van
    elke foto de thumbnail van DUIM_BREEDTE breed (_duim), nooit een groot origineel.

    Per zoekopdracht eerst de eerste keus (_tweede_keus None), pagina voor pagina, en pas als die
    op is (tot het uiterste van _paginas) de tweede keus van alle pagina's die hij las; binnen elk
    van de twee op _voorkeur. Zo komt er door de voorkeur nooit een tekort bij: een kandidaat die
    vroeger bruikbaar was, is het nog. Het log (niet het blok) zegt per zoekopdracht hoeveel er
    tweede keus waren en waarom ("geen eerste keus: ..."), en per gekozen foto het bewijs
    (_bewijs); het blok één regel met alle tweede keus samen, als die er was.

    niet (beeld_niet.tsv; lees_niet): een kandidaat met een bron daaruit neemt hij nooit, ook
    niet als tweede keus of als reserve (reden UITGESLOTEN); de volgende in dezelfde volgorde
    krijgt de plek. Als er een lijst is, zegt het blok hoeveel foto's erop staan en hoeveel er
    als kandidaat langskwamen.

    Een tekort stopt de keuze niet: elk tekort komt met het aantal bekeken kandidaten en waarom
    ze afvielen (_afgevallen) in het blok, en pas daarna volgt de fout. Zo laat één run alles zien
    wat Commons niet heeft, en waarom."""
    combis = combis if combis is not None else combinaties()
    voortgang = voortgang or Voortgang(len(CATEGORIEEN) * PER_CATEGORIE + len(ANDERE) * PER_ANDER)
    werk.set.mkdir(exist_ok=True)
    gekozen: list[Foto] = []
    gebruikt: set[int] = set()
    tekort: list[str] = []
    combi_tekort = False
    tweede_keus: dict[str, int] = {}  # per zoekopdracht hoeveel er tweede keus waren
    lijst = niet or {}
    getroffen: set[str] = set()  # de bronnen uit beeld_niet.tsv die als kandidaat langskwamen

    def uitgesloten(d: dict) -> bool:
        if not _op_lijst(d, lijst):
            return False
        getroffen.add(_bron_sleutel(d["bron"]))
        return True

    def neem(pageid: int, d: dict, sleutel: str, cats: tuple[str, ...], bewijs: str) -> None:
        doel = werk.set / f"{sleutel}.jpg"
        url, grootte = _duim(d)
        sha, n = voortgang.haal(url, doel, max_bytes=MAX_FOTO, grootte=grootte)
        gekozen.append(Foto(sleutel, cats, _licentie(d), sha, n, url, d["bron"]))
        gebruikt.add(pageid)
        voortgang.klaar = len(gekozen)
        # Alleen in het log: niet in het blok, en niet in beeld.tsv (die houdt zijn 7 kolommen)
        print(f"BZ4 set: bewijs {sleutel}: {bewijs}", flush=True)

    def cats_van(d: dict, hoofd: tuple[str, ...]) -> tuple[str, ...]:
        rest = [c for c, q in CATEGORIEEN.items() if q in d["P180"] and c not in hoofd]
        return (*hoofd, *rest)

    alle_qids = set(CATEGORIEEN.values())

    def afval(pageid: int, d: dict, redenen: dict[str, int], *, anders: bool = False) -> bool:
        """Valt deze kandidaat af? Zo ja, dan telt zijn reden (REDENEN) in redenen."""
        if pageid in gebruikt:
            reden = "al gekozen"
        elif uitgesloten(d):
            reden = UITGESLOTEN
        else:
            reden = _waarom(d)
            if reden is None and anders and d["P180"] & alle_qids:
                reden = "beeldt een categorie af"
        if reden is None:
            return False
        redenen[reden] = redenen.get(reden, 0) + 1
        return True

    def zoek_en_neem(naam: str, qids: tuple[str, ...], nodig: int, plaats, *, anders=False):
        """Tot nodig foto's voor één zoekopdracht (naam in het log), elk meteen opgehaald;
        plaats(nr, d) geeft de sleutel en de categorieën van de nr-de. Geeft (genomen, bekeken,
        redenen): de kandidaten die het voorstel bekeek en waarom ze afvielen (REDENEN), voor de
        regel van een tekort.

        Een kandidaat van de tweede keus die op een volgende pagina terugkomt (onder de andere
        licentie), telt daar als "al gekozen": bij een tekort neemt hij hem altijd, zoals vroeger
        bij de eerste keer."""
        genomen = gezien = paginas = n_tweede = 0
        redenen: dict[str, int] = {}
        geen_eerste: dict[str, int] = {}  # waarom, per kandidaat van de tweede keus
        tweede: list[tuple[int, dict]] = []
        in_tweede: set[int] = set()

        def neem_uit(rij: list[tuple[int, dict]], eerste: bool) -> None:
            nonlocal genomen, n_tweede
            rij.sort(key=lambda r: _voorkeur(r[1], qids))
            for pageid, d in rij[: nodig - genomen]:
                genomen += 1
                n_tweede += not eerste
                sleutel, cats = plaats(genomen, d)
                neem(pageid, d, sleutel, cats, _bewijs(d, qids, eerste))

        if nodig <= 0:
            return 0, 0, redenen
        for pagina in _paginas(qids):
            paginas += 1
            eerste: list[tuple[int, dict]] = []
            for pageid, d in pagina:
                gezien += 1
                if pageid in in_tweede:
                    redenen["al gekozen"] = redenen.get("al gekozen", 0) + 1
                    continue
                if afval(pageid, d, redenen, anders=anders):
                    continue
                reden = _tweede_keus(d)
                if reden is None:
                    eerste.append((pageid, d))
                else:
                    tweede.append((pageid, d))
                    in_tweede.add(pageid)
                    geen_eerste[reden] = geen_eerste.get(reden, 0) + 1
            neem_uit(eerste, True)
            if genomen >= nodig:
                break
        else:
            neem_uit(tweede, False)
        if n_tweede:
            tweede_keus[naam] = n_tweede
        # Alleen in het log: per zoekopdracht hoeveel tweede keus, en waarom de bruikbare
        # kandidaten op de gelezen pagina's geen eerste keus waren
        op = f"{paginas} pagina" if paginas == 1 else f"{paginas} pagina's"
        print(
            f"BZ4 set: {naam}: {genomen} van {nodig} gekozen, {n_tweede} tweede keus ({gezien} "
            f"kandidaten bekeken op {op}{_afgevallen(geen_eerste, 'geen eerste keus')})",
            flush=True,
        )
        return genomen, gezien, redenen

    hoofd_telt = dict.fromkeys(CATEGORIEEN, 0)
    for a, b in combis:

        def combi(_nr: int, d: dict, a=a, b=b) -> tuple[str, tuple[str, ...]]:
            return f"{a}+{b}", cats_van(d, (a, b))

        genomen, gezien, redenen = zoek_en_neem(
            f"{a}+{b}", (CATEGORIEEN[a], CATEGORIEEN[b]), 1, combi
        )
        if genomen:
            hoofd_telt[a] += 1
        else:
            combi_tekort = True
            tekort.append(
                f"geen bruikbare foto voor {a}+{b} "
                f"({gezien} kandidaten bekeken{_afgevallen(redenen)})"
            )
    for naam, qid in CATEGORIEEN.items():

        def categorie(nr: int, d: dict, naam=naam) -> tuple[str, tuple[str, ...]]:
            return f"{naam}-{nr}", cats_van(d, (naam,))

        genomen, gezien, redenen = zoek_en_neem(
            naam, (qid,), PER_CATEGORIE - hoofd_telt[naam], categorie
        )
        hoofd_telt[naam] += genomen
        if hoofd_telt[naam] < PER_CATEGORIE:
            n = hoofd_telt[naam]
            tekort.append(
                f"maar {n} bruikbare foto's voor {naam} "
                f"({gezien} kandidaten bekeken{_afgevallen(redenen)})"
            )
    for naam, qid in ANDERE.items():

        def ander(nr: int, _d: dict, naam=naam) -> tuple[str, tuple[str, ...]]:
            return f"anders-{naam}-{nr}", ()

        nr, gezien, redenen = zoek_en_neem(f"anders-{naam}", (qid,), PER_ANDER, ander, anders=True)
        if nr < PER_ANDER:
            tekort.append(
                f"maar {nr} bruikbare foto's voor {naam} "
                f"(anders; {gezien} bekeken{_afgevallen(redenen)})"
            )
    if tweede_keus:
        samen = ", ".join(f"{naam} {n}" for naam, n in tweede_keus.items())
        werk.meld(
            f"BZ4 set: {sum(tweede_keus.values())} tweede keus ({samen}); de eerste keus was "
            "op; het bewijs per foto staat in het log"
        )
    if niet is not None:
        aantal = "1 foto" if len(lijst) == 1 else f"{len(lijst)} foto's"
        werk.meld(
            f"BZ4 set: beeld_niet.tsv: {aantal}, waarvan {len(getroffen)} als kandidaat "
            "langskwamen en overgeslagen werden"
        )
    if not tekort:
        return gekozen
    # De keuze is klaar: een fout vanaf hier (ook bij het tellen van de reserves) zegt hoeveel
    # foto's er kwamen, niet "bij foto ..."
    voortgang.gekozen = True
    for t in tekort:
        werk.meld(f"BZ4 set: Commons: {t}")
    if combi_tekort:
        al = {tuple(c) for c in combis}
        for a, b in RESERVE_COMBIS:
            if (a, b) in al:
                continue
            # elke kandidaat één keer (een foto kan onder beide licenties staan); "al gekozen"
            # telt hier niet: een reserve staat los van wat er al in de set zit
            goed: set[int] = set()
            bekeken: set[int] = set()
            redenen = {}
            for pageid, d in _kandidaten((CATEGORIEEN[a], CATEGORIEEN[b]), max_pagina=1):
                if pageid in bekeken:
                    continue
                bekeken.add(pageid)
                reden = UITGESLOTEN if uitgesloten(d) else _waarom(d)
                if reden is None:
                    goed.add(pageid)
                else:
                    redenen[reden] = redenen.get(reden, 0) + 1
            werk.meld(
                f"BZ4 reserve {a}+{b}: {len(goed)} bruikbare foto's "
                f"(eerste pagina per licentie{_afgevallen(redenen)})"
            )
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


def set_(werk: Werk, set_pad: Path, *, combis=None, niet_pad: Path | None = None) -> int:
    """Gaat het ophalen mis, dan zegt de fout ook hoe ver set kwam en wat hij ophaalde (een
    thumbnail of een origineel; Voortgang); in een voorstel is het totaal het doel van Keuze 83
    (120). De regel van een goede set zegt hoeveel er als origineel kwamen, de bytes en de tijd.
    Beide zeggen ook het verkeer met Commons (Voortgang.verkeer).

    niet_pad (standaard NIET): beeld_niet.tsv. Een voorstel neemt er nooit een foto uit; met
    beeld.tsv is een foto uit die lijst een fout, vóór er iets wordt opgehaald."""
    # Alleen in het log, niet in het blok: het tempo, en of er een contact meegaat (nooit welk)
    print(
        f"BZ4 set: de API van Commons hooguit één vraag per {_tussen('API'):.0f} s, thumb één "
        f"per {_tussen('thumb'):.0f} s, upload één per {_tussen('upload'):.0f} s; "
        f"contact: {'nee' if _contact is None else 'ja'}",
        flush=True,
    )
    combis = combis if combis is not None else combinaties()
    niet = _lees_niet_pad(niet_pad if niet_pad is not None else NIET)
    if set_pad.is_file():
        fotos = lees_set(set_pad.read_text(encoding="utf-8"), combis)
        _niet_in_set(fotos, niet or {}, "beeld.tsv")
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
                    voortgang.haal(f.url, doel, max_bytes=f.bytes, grootte=f.bytes, sha256=f.sha256)
                voortgang.klaar += 1
        except Fout as e:
            raise Fout(f"{e} ({voortgang.waar()})") from None
        gehaald = len(fotos) - uit_cache
        werk.meld(
            f"BZ4 set: {len(fotos)} foto's kloppen met beeld.tsv "
            f"({uit_cache} uit de cache, {gehaald} van Commons, {voortgang.samen()})"
        )
        return 0
    voortgang = Voortgang(len(CATEGORIEEN) * PER_CATEGORIE + len(ANDERE) * PER_ANDER)
    try:
        fotos = stel_voor(werk, combis=combis, voortgang=voortgang, niet=niet)
    except Fout as e:
        raise Fout(f"{e} ({voortgang.waar()})") from None
    tekst = set_tsv(fotos)
    # het voorstel moet zelf door de controle komen, ook die van beeld_niet.tsv
    _niet_in_set(lees_set(tekst, combis), niet or {}, "voorstel-beeld.tsv")
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
    p.add_argument("--niet", type=Path, default=NIET, help="beeld_niet.tsv (set)")
    p.add_argument("--uit", type=Path, action="append", default=[], help="een uitvoermap")
    p.add_argument("--log", type=Path, action="append", default=[], help="stderr van de omzetter")
    p.add_argument("--uit-fp32", type=Path, help="de uitvoermap van de omzetting met --fp32 (BZ4)")
    p.add_argument("--log-fp32", type=Path, help="stderr van de omzetting met --fp32")
    args = p.parse_args(argv)
    werk = Werk(args.werk)
    try:
        # Vóór de eerste vraag: een BEELD_CONTACT die niet klopt, gaat nooit weg
        _zet_contact(os.environ.get("BEELD_CONTACT"))
        if args.stap == "bereik":
            return bereik(werk)
        if args.stap == "bron":
            return bron(werk, args.lock)
        if args.stap == "vergelijk":
            return vergelijk(werk, args.lock, args.uit, args.log, args.uit_fp32, args.log_fp32)
        if args.stap == "set":
            return set_(werk, args.set, niet_pad=args.niet)
        return uitslag(werk)
    except (Fout, lock.LockError) as e:
        werk.meld(f"FOUT {args.stap}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
