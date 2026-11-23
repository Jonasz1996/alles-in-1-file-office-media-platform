"""Met de analyse uit is golf 6 onzichtbaar in de web (fase 6, de samenvoeging van golf 6): elke
pagina ziet er voor Eva, de gast Gijs, de beheerder Jonas en wie niet is ingelogd uit zoals met de
web van 0.8.0, en vraagt dezelfde routes van de API.

De wandeling: een eigen app zonder PLATFORM_ANALYSE_ENABLED (zoals 0.8.0), met Eva's foto, een
PDF, een map, een album en een persoon (zoals api/tests/uit080/wandel.py). Per wie, per pagina
(elke route van de web, elke tab van /beheer, /zoeken met en zonder vraag en in de stand beeld,
de viewer met het infopaneel): de toegankelijkheidsboom van de pagina (aria_snapshot, met alle
knoppen, vinkjes, koppen en teksten) en de routes van de API die de pagina vroeg (methode en pad,
zonder de stroom /api/v1/events). Dat vergelijkt de test met uit080_web.json: dezelfde wandeling
met de web van 0.8.0 (`3fca440`) op deze app. De API zelf antwoordt met de analyse uit als 0.8.0
(api/tests/uit080/test_wandeling.py); zo blijft over wat de web van golf 6 zou tonen of vragen.

Wat per run verschilt, is gelijkgemaakt: ids, getallen, maanden en dagen (de datum van de
upload) en "… geleden". De bewaking van de browser (CSP, i18n, fouten) staat aan zoals overal.

Elke boom en elke titel is gelijk aan die van 0.8.0, op precies ANDERS en ERIN na: wat 0.8.1 (I2)
met de analyse uit bewust anders toont (de zinnen dat gezichten en zoeken op beeld uitstaan, en op
/account de link naar /over). De routes ook, op precies ERBIJ na: daar vraagt de web van golf 6
twee routes die 0.8.0 al had, en toont er niets van (zie ERBIJ). Tot slot /over zelf, voor de gast
Gijs met de analyse uit: de bronnen van de kaart en de modellen, zonder een vraag aan de API
buiten die van de schil.

De boom is het formaat van aria_snapshot van Playwright (opgenomen met 1.65.2: rollen, namen,
[level], [checked], [selected], [disabled], [pressed] en /url). Verandert een nieuwe Playwright
dat formaat, dan verschilt elke pagina, ook die van 0.8.0: neem dan opnieuw op. Opnieuw opnemen
(alleen met de web van 0.8.0, nooit met die van nu):

    git archive 3fca440 web | tar -x -C /tmp/w080 && ln -s "$PWD/web/node_modules" /tmp/w080/web/
    (cd /tmp/w080/web && npx vite build)
    cd api && E2E_WEB_DIST=/tmp/w080/web/dist E2E_UIT080_OPNEMEN=1 \\
        uv run --with playwright python -m pytest ../e2e/test_uit080.py
"""

from __future__ import annotations

import difflib
import io
import json
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from conftest import (
    WEB_DIST,
    Account,
    Browsers,
    Server,
    _browsers_teardown,
    artifacts_dir,
    create_user,
    make_admin,
)

API = "/api/v1"
OPNAME = Path(__file__).with_name("uit080_web.json")
OPNEMEN = os.environ.get("E2E_UIT080_OPNEMEN") == "1"
WIE = ("niemand", "eva", "gijs", "jonas")
# De pagina's; {map}, {album} en {persoon} uit de wereld
PAGINAS = (
    "/",
    "/map/{map}",
    "/zoeken",
    "/zoeken?q=strand",
    "/zoeken?q=strand&mode=image",
    "/gedeeld",
    "/gedeeld/door-mij",
    "/prullenbak",
    "/fotos",
    "/albums",
    "/album/{album}",
    "/fotos/kaart",
    "/fotos/personen",
    "/fotos/personen/{persoon}",
    "/dubbel",
    "/beheer",
    "/beheer/gebruikers",
    "/beheer/opslag",
    "/beheer/taken",
    "/beheer/groepen",
    "/beheer/beveiliging",
    "/beheer/office",
    "/beheer/media",
    "/beheer/zoeken",
    "/account",
    "/media",
    "/muziek",
    "/bestaat-niet",
)
VIEWER = "/fotos (viewer, i)"
BEELD = "GET /api/v#/photos/beeld"
INSTELLINGEN = "GET /api/v#/me/photo-settings"
# Wat de web van golf 6 met de analyse uit meer vraagt dan die van 0.8.0, per pagina, precies. Het
# zijn routes van 0.8.0 die hetzelfde antwoorden (api/tests/uit080): zoeken op beeld (X3b) vraagt
# GET /photos/beeld, de stub van Z0b, die met de analyse uit 501 not_built("X3") geeft en de
# schakelaar en het raster verbergt; gezichten (X2b) lezen faces_available uit
# GET /me/photo-settings, dat met de analyse uit false is. Geen van beide toont iets: de bomen zijn
# gelijk. ANTWOORD zegt wat die routes in deze wandeling antwoorden.
ERBIJ: dict[str, list[str]] = {
    "eva /account": [BEELD],
    "jonas /account": [BEELD],
    f"eva {VIEWER}": [INSTELLINGEN, BEELD],
    "eva /fotos/personen": [INSTELLINGEN],
    "eva /fotos/personen/{persoon}": [INSTELLINGEN],
    "jonas /fotos/personen": [INSTELLINGEN],
    "eva /zoeken?q=strand&mode=image": [BEELD],
    "gijs /zoeken?q=strand&mode=image": [BEELD],
    "jonas /zoeken?q=strand&mode=image": [BEELD],
}
ANTWOORD = {BEELD: {501}, INSTELLINGEN: {200}}
# Wat de web van 0.8.1 (I2) met de analyse uit anders toont dan die van 0.8.0, per pagina, precies:
# (regel van 0.8.0, regel van nu). Gezichten en zoeken op beeld zeggen dat ze op deze server niet
# draaien, en op /beheer waar de beheerder ziet waarom, in plaats van "komt later"
# (web/src/i18n/areas/beeld.nl.ts, search.nl.ts en photos_faces.nl.ts; review I2, V2 en U2). Elke
# regel van 0.8.0 staat precies één keer in zijn boom.
LATER_080 = "  - paragraph: Zoeken op beeld komt in een volgende versie."
LATER = "  - paragraph: Zoeken op beeld staat uit op deze server. De beheerder kan het aanzetten."
UIT_BEHEER = (
    "draait niet op deze server: de analyse staat uit, of is niet helemaal geïnstalleerd. Waarom "
    "staat in de eindtekst van het installatiescript, bij Gezichten (de container analyse). Stond "
    "daar ANALYSE=uit, draai het script dan opnieuw met ANALYSE=aan; daarna zet ieder het zelf aan "
    "bij Account → Foto's. Zie docs/runbooks/analyse.md."
)
ANDERS: dict[str, list[tuple[str, str]]] = {
    "eva /zoeken?q=strand&mode=image": [(LATER_080, LATER)],
    "gijs /zoeken?q=strand&mode=image": [(LATER_080, LATER)],
    "jonas /zoeken?q=strand&mode=image": [(LATER_080, LATER)],
    "jonas /beheer/zoeken": [
        (
            "    - paragraph: Gezichten herkennen komt later in fase #.",
            f'    - paragraph: "Gezichten herkennen {UIT_BEHEER}"',
        ),
        (
            "    - paragraph: Zoeken op beeld komt later in fase #.",
            f'    - paragraph: "Zoeken op beeld {UIT_BEHEER}"',
        ),
    ],
}
# Wat de web van 0.8.1 (I2) erbij toont, per pagina: (de regel waar het vóór staat, de regels).
# /account: onderaan, na Apparaten en sessies, de link naar /over (Keuze 29 en K1-13), voor
# iedereen die is ingelogd, met de analyse aan of uit (web/src/Account.tsx).
OVER = [
    '  - region "Over dit platform":',
    '    - heading "Over dit platform" [level=#]',
    "    - paragraph:",
    '      - link "Bronnen en licenties":',
    "        - /url: /over",
    # Fase 11: de downloadpagina van de client-apps (web/src/Account.tsx)
    "    - paragraph:",
    '      - link "Apps voor je tv en telefoon":',
    "        - /url: /apps",
]
ERIN: dict[str, tuple[str, list[str]]] = {
    "eva /account": ('- region "Meldingen"', OVER),
    "gijs /account": ('- region "Meldingen"', OVER),
    "jonas /account": ('- region "Meldingen"', OVER),
}

_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}")
_GELEDEN = re.compile(
    r"\b(\d+|een|één)\s+(seconde|seconden|minuut|minuten|uur|uren|dag|dagen)\s+geleden\b"
)
_MAAND = re.compile(
    r"\b(januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|"
    r"december|jan|feb|mrt|apr|jun|jul|aug|sep|sept|okt|nov|dec)\b\.?",
    re.I,
)
_DAG = re.compile(r"\b(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\b", re.I)
_GETAL = re.compile(r"\d+")


def gelijk(tekst: str) -> str:
    """Wat per run verschilt gelijk: ids, "… geleden", maanden, dagen en getallen."""
    tekst = _ID.sub("<id>", tekst)
    tekst = _GELEDEN.sub("<geleden>", tekst)
    tekst = _MAAND.sub("<maand>", tekst)
    tekst = _DAG.sub("<dag>", tekst)
    return _GETAL.sub("#", tekst)


def jpeg() -> bytes:
    """Een kleine, vaste JPEG (zoals api/tests/uit080/wandel.py)."""
    from PIL import Image

    img = Image.new("RGB", (64, 48))
    img.putdata([((x * 4) % 256, (y * 5) % 256, 128) for y in range(48) for x in range(64)])
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80)
    return out.getvalue()


PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


@dataclass
class Wereld:
    server: Server
    wie: dict[str, Account]
    waarden: dict[str, str]


@pytest.fixture(scope="module")
def wereld(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Wereld]:
    """Een eigen app met de analyse uit (zoals 0.8.0) en vaste namen en adressen."""
    base = Path(tmp_path_factory.mktemp("uit080"))
    server = Server(base / "data", web_dist=WEB_DIST)
    try:
        server.start()
        jonas = make_admin(server, email="jonas@voorbeeld.nl", name="Jonas")
        eva = create_user(server, jonas, name="Eva", email="eva@voorbeeld.nl")
        gijs = create_user(server, jonas, "guest", name="Gijs", email="gijs@voorbeeld.nl")
        foto = upload(server, eva, "strand.jpg", jpeg())
        upload(server, eva, "verslag.pdf", PDF)
        vakantie = server.call(
            eva, "POST", f"{API}/files/{eva.root_id}/folders", json={"name": "Vakantie"},
            expect=201,
        ).json()  # fmt: skip
        album = server.call(eva, "POST", f"{API}/albums", json={"name": "Zomer"}, expect=201).json()
        server.call(
            eva, "POST", f"{API}/albums/{album['id']}/items", json={"node_ids": [foto]},
            expect=200,
        )  # fmt: skip
        server.call(eva, "POST", f"{API}/files/{foto}/people", json={"name": "Anna"}, expect=201)
        anna = server.call(eva, "GET", f"{API}/people", expect=200).json()["people"][0]["id"]
        wacht_op_previews(server, eva, [foto])
        wacht_op_rust(server, jonas)
        yield Wereld(
            server,
            {"eva": eva, "gijs": gijs, "jonas": jonas},
            {"map": vakantie["id"], "album": album["id"], "persoon": anna},
        )
    finally:
        server.stop()


@pytest.fixture
def browsers(
    browser: Any,
    wereld: Wereld,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Browsers]:
    """Contexts op de app van de wandeling; elke melding van de bewaking laat de test falen."""
    b = Browsers(browser, wereld.server)
    yield b
    problems = _browsers_teardown(request, b, artifacts_dir(tmp_path_factory))
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)


def upload(server: Server, account: Account, naam: str, data: bytes) -> str:
    r = server.call(
        account,
        "PUT",
        f"{API}/files/{account.root_id}/upload",
        params={"name": naam},
        content=data,
        expect=201,
    )
    return r.json()["id"]


def wacht_op_previews(server: Server, account: Account, ids: list[str]) -> None:
    eind = time.monotonic() + 120
    stand: dict[str, Any] = {}
    while time.monotonic() < eind:
        stand = server.call(
            account, "POST", f"{API}/previews", json={"node_ids": ids}, expect=200
        ).json()
        if all(stand.get(i, {}).get("state") == "ready" for i in ids):
            return
        time.sleep(0.25)
    raise AssertionError(f"de previews kwamen niet binnen 120 s: {stand}")


def wacht_op_rust(server: Server, jonas: Account) -> None:
    """Tot er 2 s geen job loopt en niets verandert (/beheer/taken toont dan elke keer hetzelfde;
    analysis.ocr blijft klaarstaan, zonder media is er niemand die hem draait, zoals in 0.8.0)."""
    eind = time.monotonic() + 120
    vorige: Any = None
    sinds = time.monotonic()
    while time.monotonic() < eind:
        stand = server.call(jonas, "GET", f"{API}/admin/jobs", expect=200).json()
        if stand["running"] or stand["by_type"] != vorige:
            vorige, sinds = stand["by_type"], time.monotonic()
        elif time.monotonic() - sinds >= 2:
            return
        time.sleep(0.25)
    raise AssertionError(f"de jobs werden niet stil binnen 120 s: {vorige}")


class Verkeer:
    """De routes van de API die een pagina vraagt (zonder de stroom /api/v1/events, die altijd
    openstaat), en wanneer het stil is."""

    def __init__(self, page: Page, base: str) -> None:
        self.page = page
        self.base = base
        self.open: set[Any] = set()
        self.routes: set[str] = set()
        self.antwoorden: dict[str, set[int]] = {}
        self.laatst = time.monotonic()
        page.on("request", self._begin)
        page.on("response", self._antwoord)
        page.on("requestfinished", self._eind)
        page.on("requestfailed", self._eind)

    def _route(self, r: Any) -> str | None:
        pad, _, query = r.url.removeprefix(self.base).partition("?")
        if pad == f"{API}/events" or not pad.startswith(f"{API}/"):
            return None
        keys = sorted({p.split("=", 1)[0] for p in query.split("&") if p})
        return gelijk(f"{r.method} {pad}" + (f"?{'&'.join(keys)}" if keys else ""))

    def _begin(self, r: Any) -> None:
        if r.url.removeprefix(self.base).partition("?")[0] == f"{API}/events":
            return
        route = self._route(r)
        if route is not None:
            self.routes.add(route)
        self.open.add(r)
        self.laatst = time.monotonic()

    def _antwoord(self, r: Any) -> None:
        route = self._route(r.request)
        if route in ANTWOORD:
            self.antwoorden.setdefault(route, set()).add(r.status)

    def _eind(self, r: Any) -> None:
        self.open.discard(r)
        self.laatst = time.monotonic()

    def stil(self, rust: float = 1.0, seconden: float = 20.0) -> None:
        eind = time.monotonic() + seconden
        while time.monotonic() < eind:
            self.page.wait_for_timeout(100)
            if not self.open and time.monotonic() - self.laatst >= rust:
                return
        raise AssertionError(f"de pagina werd niet stil: {sorted(r.url for r in self.open)}")

    def neem(self) -> list[str]:
        routes, self.routes = sorted(self.routes), set()
        return routes


def boom(waar: Locator) -> list[str]:
    return gelijk(waar.aria_snapshot()).splitlines()


def loop(
    wereld: Wereld, browsers: Browsers, wie: str, antwoorden: dict[str, set[int]]
) -> dict[str, Any]:
    if wie == "niemand":
        context = browsers.new_context()
    else:
        context = browsers.context_as(wereld.wie[wie])
    page = context.new_page()
    verkeer = Verkeer(page, wereld.server.url)
    uit: dict[str, Any] = {}
    for sjabloon in PAGINAS:
        page.goto(sjabloon.format(**wereld.waarden))
        verkeer.stil()
        uit[f"{wie} {sjabloon}"] = {
            "titel": gelijk(page.title()),
            "pad": gelijk(page.evaluate("location.pathname + location.search")),
            "boom": boom(page.locator("body")),
            "api": verkeer.neem(),
        }
    if wie in ("eva", "jonas"):
        uit[f"{wie} {VIEWER}"] = viewer(page, verkeer)
    context.close()
    for route, statussen in verkeer.antwoorden.items():
        antwoorden.setdefault(route, set()).update(statussen)
    return uit


def viewer(page: Page, verkeer: Verkeer) -> dict[str, Any]:
    """Op /fotos de eerste foto (Tab tot de cel, Enter) en dan het infopaneel (i)."""
    page.goto("/fotos")
    verkeer.stil()
    cel = page.locator('[role=gridcell][tabindex="0"]')
    if cel.count() == 0:  # Jonas heeft geen foto's: de lege pagina is de uitkomst
        verkeer.neem()
        return {"boom": boom(page.locator("body")), "api": []}
    for _ in range(80):
        page.keyboard.press("Tab")
        if cel.evaluate("(e) => e === document.activeElement"):
            break
    verkeer.neem()
    page.keyboard.press("Enter")
    dialoog = page.get_by_role("dialog", name="strand.jpg", exact=True)
    dialoog.wait_for()
    page.keyboard.press("i")
    verkeer.stil()
    return {"boom": boom(dialoog), "api": verkeer.neem()}


# Onder het inlogformulier staat sinds reset-account (app.cli) de knop "Kan niet inloggen?" in
# plaats van de zin van 0.8.0; op elke inlogpagina hetzelfde.
VERGETEN_080 = "  - paragraph: Wachtwoord vergeten? Vraag je beheerder om een nieuw wachtwoord."
KAN_NIET = ["  - paragraph:", '    - button "Kan niet inloggen?"']


def met_081(key: str, boom: list[str]) -> list[str]:
    """De boom van 0.8.0 met precies wat 0.8.1 anders toont (ANDERS) en erbij toont (ERIN)."""
    if VERGETEN_080 in boom:
        i = boom.index(VERGETEN_080)
        boom = boom[:i] + KAN_NIET + boom[i + 1 :]
    for regel, nieuw in ANDERS.get(key, []):
        assert boom.count(regel) == 1, (key, regel)
        boom = [nieuw if r == regel else r for r in boom]
    if key in ERIN:
        voor, regels = ERIN[key]
        assert boom.count(voor) == 1, (key, voor)
        i = boom.index(voor)
        boom = boom[:i] + regels + boom[i:]
    return boom


def verschil(oud: dict[str, Any], nu: dict[str, Any]) -> str:
    regels: list[str] = []
    for key in sorted(set(oud) | set(nu)):
        if oud.get(key) == nu.get(key):
            continue
        a = json.dumps(oud.get(key), indent=1, ensure_ascii=False).splitlines()
        b = json.dumps(nu.get(key), indent=1, ensure_ascii=False).splitlines()
        regels += [f"== {key}"] + list(difflib.unified_diff(a, b, "0.8.0", "nu", lineterm=""))
    return "\n".join(regels[:400])


def test_met_de_analyse_uit_elke_pagina_zoals_in_080(wereld: Wereld, browsers: Browsers) -> None:
    nu: dict[str, Any] = {}
    antwoorden: dict[str, set[int]] = {}
    for wie in WIE:
        nu.update(loop(wereld, browsers, wie, antwoorden))
    if OPNEMEN:
        assert os.environ.get("E2E_WEB_DIST"), "opnemen alleen met de web van 0.8.0"
        OPNAME.write_text(
            json.dumps({"bron": "3fca440", "paginas": nu}, indent=1, ensure_ascii=False) + "\n"
        )
    oud = json.loads(OPNAME.read_text())["paginas"]
    assert len(oud) == len(PAGINAS) * len(WIE) + 2
    assert set(nu) == set(oud)
    # Opgenomen met de web van 0.8.0 is er niets erbij; met die van nu precies ERBIJ, ANDERS en ERIN
    erbij = {} if OPNEMEN else ERBIJ
    verwacht = {
        key: {**pagina, "api": sorted(set(pagina["api"]) | set(erbij.get(key, [])))}
        for key, pagina in oud.items()
    }
    if not OPNEMEN:
        assert set(ANDERS) | set(ERIN) <= set(oud)
        for key, pagina in verwacht.items():
            pagina["boom"] = met_081(key, pagina["boom"])
    assert not any(set(erbij[key]) & set(oud[key]["api"]) for key in erbij)
    assert nu == verwacht, "anders dan de web van 0.8.0:\n" + verschil(verwacht, nu)
    # De routes erbij antwoorden zoals in 0.8.0: 501 not_built("X3") en de instellingen
    assert {r: s for r, s in antwoorden.items() if r in set().union(*erbij.values())} == {
        r: ANTWOORD[r] for r in set().union(*erbij.values())
    }


def test_over_met_de_analyse_uit_voor_de_gast(wereld: Wereld, browsers: Browsers) -> None:
    """/over (I2; Keuze 29 en K1-13) met de analyse uit, voor de gast Gijs: de bronnen van de kaart
    (de plaatsen van GeoNames met CC BY 4.0) en de modellen en pakketten van de analyse, elk met
    hun licentie. De pagina vraagt de API niets buiten wat de schil op elke pagina vraagt: precies
    de routes van "gijs /bestaat-niet" in de opname van 0.8.0."""
    page = browsers.context_as(wereld.wie["gijs"]).new_page()
    verkeer = Verkeer(page, wereld.server.url)
    page.goto("/over")
    verkeer.stil()
    main = page.get_by_role("main")
    expect(page).to_have_title("Over dit platform · Platform")
    expect(main.get_by_role("heading", name="Over dit platform", level=1)).to_be_visible()
    for regel in (
        "Plaatsnamen: GeoNames cities1000 (als rg_cities1000.csv uit reverse_geocoder 1.5.1), "
        "CC BY 4.0.",
        "Landen: Natural Earth 1:10m admin-0 v5.1.2 (publiek domein).",
        "Gezichten vinden: YuNet 2026may van opencv_zoo, MIT, Copyright (c) 2020 Shiqi Yu.",
        "Gezichten herkennen: SFace 2021dec van opencv_zoo, Apache-2.0.",
        "Zoeken op beeld: SigLIP 2 B/32-256 van Google (google/siglip2-base-patch32-256), "
        "Apache-2.0.",
        "onnxruntime 1.31.1: MIT",
    ):
        expect(main.get_by_role("listitem").filter(has_text=regel)).to_have_text(regel)
    oud = json.loads(OPNAME.read_text())["paginas"]
    assert verkeer.neem() == oud["gijs /bestaat-niet"]["api"]
