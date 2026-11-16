"""Gezichten automatisch (fase 6, X2b; Keuze 64–69) in de browser, alleen met het toetsenbord:
Tab, Enter, Spatie, Esc, de pijlen, J en N, Ctrl+Z, Ctrl+K en typen; nergens een klik
(docs/fase-6/toetsenbord-x2.md).

**Met de analyse (de nep-worker).** Een eigen app met PLATFORM_ANALYSE_ENABLED, een map met
sleutels buiten de datamap, en de nep-worker van tests/people/faces/schijven.py in een draad: de
echte socket, de echte handdruk, en "gezichten" die gekleurde schijven zijn (rood is Anna, blauw
is Cas). Eva:

1. uploadt acht foto's via de API: vier met een rode schijf, drie met een blauwe, één zonder;
2. zet onder Account → Foto's "Gezichten herkennen in mijn foto's" aan (Spatie; het dialoog met de
   toestemming, de focus eerst op Annuleren; Tab, Enter);
3. wacht tot de scan klaar is: 7 gezichten in 8 foto's, twee groepen; op /fotos/personen staat
   "Wie is dit?" twee keer en nog geen "Is dit …?";
4. benoemt in de viewer (Enter, I) met de hand Anna met een kader (Z6) op de foto met de grote
   rode schijf: het kader valt op het gevonden gezicht, dat is dan ja (de rij Gezichten toont
   Anna in plaats van "?");
5. na 30 s (VERDICT_WAIT) komt "Is dit Anna?": Enter opent het raster met de focus op het eerste
   gezicht; J, J, N; de pagina laadt opnieuw zonder Anna, maar het raster blijft open: Ctrl+Z
   maakt de N ongedaan, en N weer; Esc: de kaart van Anna is weg, de focus blijft in het blok
   gezichten;
6. zoekt "anna" (Ctrl+K, ↓ tot de persoon Anna, Enter): de drie foto's van Anna;
7. "Wie is dit?" → Naam "Cas" (een nieuwe persoon) → Bevestigen: drie gezichten;
8. Bram (met wie Eva één foto deelt) ziet niets: [] voor die foto, 404 voor de rest, geen
   personen, geen uitsnede, en zijn /fotos/personen vraagt geen route van gezichten;
9. zet gezichten uit (het dialoog "Gezichten uitzetten?"): 0 gezichten (API), de sleutel is weg,
   Anna en Cas blijven op de bevestigde foto's;
10. de beheerder ziet op /beheer → Zoeken en foto's het blok Gezichten (de analyse werkt, de
    kwaliteit is niet gemeten); Spatie op "Gezichtsherkenning toestaan" vraagt eerst (Esc laat
    alles zoals het was), "Verbieden en wissen" verbiedt het (Eva ziet dat onder Account), en
    Spatie staat het meteen weer toe, zonder dat Eva daardoor aan staat.

Eerst de foto's, dan aanzetten: groeperen (full) gaat zodra de rij van de eigenaar leeg is
(group.maybe_full), hooguit één keer per uur. Zo ziet de eerste ronde alle zeven gezichten; wat na
een full binnenkomt, groepeert de ronde van elk uur (docs/fase-6/x2b.md, Vormen), en zo lang wacht
deze test niet.

Daarna **/over** (I2; Keuze 29 en K1-13) met de analyse aan, voor Bram: Account → Tab tot
"Bronnen en licenties" → Enter. De pagina noemt de bronnen van de kaart en de modellen van de
analyse (YuNet, SFace, SigLIP 2) en de pakketten in het image, elk met de licentie, en vraagt de
API niets.

**Zonder de analyse** (de server van de sessie, zoals 0.8.0): de gebruiker en de beheerder zien
niets nieuws, en de web vraagt geen enkele route van gezichten.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect
from tests.people.faces import schijven

from conftest import (
    Account,
    Browsers,
    Server,
    _browsers_teardown,
    artifacts_dir,
    create_user,
    make_admin,
)

pytestmark = pytest.mark.zoek

API = "/api/v1"
SCAN_S = 120.0  # de scan van acht foto's met de nep (de plan-job wacht hooguit 60 s op de index)
VOORSTEL_S = 90.0  # VERDICT_WAIT (30 s) en de rij analyse
WIS_S = 60.0
FOCUSED = '[role=gridcell][tabindex="0"]'
ACTIEF = """() => {
  const a = document.activeElement;
  if (!a) return "-";
  const naam = (a.getAttribute("aria-label") || a.textContent || "").trim();
  return `${a.tagName} ${naam.slice(0, 40)}`;
}"""

# (naam, schijven): persoon, midden x, midden y, straal (zie schijven.foto). anna-1 komt als laatste
# binnen en staat dus vooraan in de tijdlijn; zijn schijf valt precies op het kader dat Z6 voorstelt
# (35 % van links, 25 % van boven, 30 % breed en 40 % hoog)
FOTOS: list[tuple[str, tuple[tuple[int, float, float, float], ...]]] = [
    ("anna-2.jpg", ((1, 0.30, 0.50, 0.10),)),
    ("cas-1.jpg", ((2, 0.60, 0.50, 0.10),)),
    ("anna-3.jpg", ((1, 0.70, 0.40, 0.12),)),
    ("leeg.jpg", ()),
    ("cas-2.jpg", ((2, 0.35, 0.45, 0.11),)),
    ("anna-4.jpg", ((1, 0.45, 0.60, 0.09),)),
    ("cas-3.jpg", ((2, 0.55, 0.35, 0.09),)),
    ("anna-1.jpg", ((1, 0.50, 0.45, 0.15),)),
]


@dataclass
class Wereld:
    server: Server
    nep: schijven.NepInDraad
    sleutels: Path
    admin: Account
    eva: Account
    bram: Account


@pytest.fixture(scope="module")
def wereld(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Wereld]:
    """Een eigen app met de analyse aan, tegen de nep-worker in een draad."""
    base = Path(tmp_path_factory.mktemp("gezichten"))
    sleutels = base / "sleutels"  # buiten de datamap (Keuze 68)
    sleutels.mkdir(mode=0o700)
    nep = schijven.NepInDraad(base / "run" / "worker.sock").start()
    server = Server(
        base / "data",
        env={
            "PLATFORM_ANALYSE_ENABLED": "true",
            "PLATFORM_ANALYSE_WORKER": str(nep.socket),
            "PLATFORM_ANALYSE_WORKER_KEY": nep.key,
            "PLATFORM_FACES_KEYS": str(sleutels),
        },
    )
    try:
        server.start()
        admin = make_admin(server)
        eva = create_user(server, admin, name="Eva Gezichten")
        bram = create_user(server, admin, name="Bram Gezichten")
        yield Wereld(server, nep, sleutels, admin, eva, bram)
    finally:
        server.stop()
        nep.stop()


@pytest.fixture
def aan(
    browser: Any,
    wereld: Wereld,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Browsers]:
    """Contexts op de app met de analyse; elke melding van de bewaking laat de test falen."""
    b = Browsers(browser, wereld.server)
    yield b
    problems = _browsers_teardown(request, b, artifacts_dir(tmp_path_factory))
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)


# --- hulp -------------------------------------------------------------------------------------


def tab_to(page: Page, target: Locator, limit: int = 60) -> int:
    """Tab tot target de focus heeft; geeft het aantal keer."""
    target.wait_for(state="attached")
    gezien = []
    for i in range(limit):
        page.keyboard.press("Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return i + 1
        gezien.append(page.evaluate(ACTIEF))
    raise AssertionError(f"Tab kwam niet bij {target}: {gezien}")


def toast(page: Page, tekst: str) -> Locator:
    """Een melding (niet de kopie voor de schermlezer)."""
    return page.get_by_role("region", name="Meldingen").get_by_text(tekst, exact=True)


def cell(page: Page, i: int) -> Locator:
    return page.locator(f'[role=gridcell][data-index="{i}"]')


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
    """Tot elke foto zijn 1024 heeft (die leest de scan)."""
    eind = time.monotonic() + SCAN_S
    stand: dict[str, Any] = {}
    while time.monotonic() < eind:
        stand = server.call(
            account, "POST", f"{API}/previews", json={"node_ids": ids}, expect=200
        ).json()
        if all(
            stand.get(i, {}).get("state") == "ready" and "/1024.webp" in (stand[i]["large"] or "")
            for i in ids
        ):
            return
        time.sleep(0.25)
    raise AssertionError(f"de previews kwamen niet binnen {SCAN_S:.0f} s: {stand}")


def wacht(server: Server, account: Account, pad: str, klaar: Callable[[Any], bool], wat: str,
          seconden: float) -> Any:  # fmt: skip
    eind = time.monotonic() + seconden
    gezien: Any = None
    while time.monotonic() < eind:
        gezien = server.call(account, "GET", f"{API}{pad}", expect=200).json()
        if klaar(gezien):
            return gezien
        time.sleep(0.25)
    raise AssertionError(f"{wat} niet binnen {seconden:.0f} s: {gezien}")


def faces_van(server: Server, account: Account, node: str) -> list[dict[str, Any]]:
    return server.call(account, "GET", f"{API}/files/{node}/faces", expect=200).json()


def personen(server: Server, account: Account) -> dict[str, int]:
    """Naam → aantal foto's (GET /people)."""
    lijst = server.call(account, "GET", f"{API}/people", expect=200).json()["people"]
    return {p["name"]: p["count"] for p in lijst}


def is_route_van_gezichten(url: str) -> bool:
    pad = url.split("?", 1)[0]
    return "/api/v1/people/faces" in pad or bool(re.search(r"/api/v1/files/[^/]+/faces", pad))


class Verkeer:
    """De verzoeken van een pagina (zonder de stroom /api/v1/events, die altijd openstaat): welke
    routes van gezichten er gevraagd zijn, en wanneer het stil is. networkidle wacht ook op die
    stroom en komt dus nooit."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.open: set[Any] = set()
        self.gezichten: list[str] = []
        self.laatst = time.monotonic()
        page.on("request", self._begin)
        page.on("requestfinished", self._eind)
        page.on("requestfailed", self._eind)

    def _begin(self, r: Any) -> None:
        if is_route_van_gezichten(r.url):
            self.gezichten.append(r.url)
        if r.url.split("?", 1)[0].endswith("/api/v1/events"):
            return
        self.open.add(r)
        self.laatst = time.monotonic()

    def _eind(self, r: Any) -> None:
        self.open.discard(r)
        self.laatst = time.monotonic()

    def stil(self, rust: float = 1.0, seconden: float = 20.0) -> None:
        """Tot er rust seconden geen verzoek liep."""
        eind = time.monotonic() + seconden
        while time.monotonic() < eind:
            self.page.wait_for_timeout(100)
            if not self.open and time.monotonic() - self.laatst >= rust:
                return
        raise AssertionError(f"de pagina werd niet stil: {sorted(r.url for r in self.open)}")


def patch_instellingen(r: Any) -> bool:
    return r.request.method == "PATCH" and r.url.endswith("/api/v1/me/photo-settings")


def patch_beheer(r: Any) -> bool:
    return r.request.method == "PATCH" and r.url.endswith("/api/v1/admin/search")


def post_naar(pad: str) -> Callable[[Any], bool]:
    def match(r: Any) -> bool:
        return r.request.method == "POST" and r.url.split("?", 1)[0].endswith(pad)

    return match


def is_zoeken(r: Any) -> bool:
    return "/api/v1/search?" in r.url and r.request.method == "GET"


def is_suggestie(r: Any) -> bool:
    return "/api/v1/search/suggest?" in r.url and r.request.method == "GET"


def is_voorstellen(r: Any) -> bool:
    """GET /people/faces/suggestions zonder person: de kaarten "Is dit …?" (de pagina laadt ze na
    elke keuze opnieuw)."""
    pad = r.url.split("?", 1)[0]
    return r.request.method == "GET" and pad.endswith("/api/v1/people/faces/suggestions")


def wacht_op_lading(page: Page, geladen: list[Any], klaar: Callable[[Any], bool], wat: str) -> None:
    """Wacht tot de pagina een antwoord kreeg (uit geladen, gevuld door page.on) waarvoor klaar
    waar is."""
    eind = time.monotonic() + 15
    gezien = 0
    while True:
        while gezien < len(geladen):
            if klaar(geladen[gezien].json()):
                return
            gezien += 1
        assert time.monotonic() < eind, f"{wat}: de pagina laadde niet opnieuw"
        page.wait_for_timeout(50)


def schakel(page: Page, aan: bool) -> None:
    """De schakelaar onder Account → Foto's, met zijn dialoog: Spatie, de focus op Annuleren, Tab
    naar de knop die het doet, Enter."""
    page.goto("/account")
    schakelaar = page.get_by_role("checkbox", name="Gezichten herkennen in mijn foto's", exact=True)
    expect(schakelaar).to_be_checked(checked=not aan)
    # Het vinkje van Z6 (zonder de analyse) staat er niet naast
    expect(page.get_by_role("checkbox", name="Gezichten herkennen", exact=True)).to_have_count(0)
    tab_to(page, schakelaar, limit=80)
    page.keyboard.press("Space")
    titel, knop = (
        ("Gezichten herkennen in mijn foto's", "Aanzetten")
        if aan
        else ("Gezichten uitzetten?", "Uitzetten en wissen")
    )
    dialoog = page.get_by_role("dialog", name=titel)
    expect(dialoog).to_be_visible()
    if aan:
        # De toestemming van Keuze 66: wat er bewaard wordt, en dat uit alles wist
        expect(dialoog).to_contain_text("biometrische data")
        expect(dialoog).to_contain_text("Zet je dit uit, dan wissen we de gezichten")
    expect(dialoog.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(dialoog.get_by_role("button", name=knop, exact=True)).to_be_focused()
    with page.expect_response(patch_instellingen) as gezet:
        page.keyboard.press("Enter")
    assert gezet.value.status == 200, gezet.value.text()
    assert gezet.value.json()["faces"] == ("aan" if aan else "uit")
    tekst = (
        "Gezichten herkennen staat aan."
        if aan
        else "Gezichten herkennen staat uit; de gezichten worden gewist."
    )
    expect(toast(page, tekst)).to_be_visible()
    expect(schakelaar).to_be_checked(checked=aan)
    expect(schakelaar).to_be_focused()


# --- 1. met de analyse ------------------------------------------------------------------------


@pytest.mark.analyse
def test_gezichten_met_alleen_het_toetsenbord(wereld: Wereld, aan: Browsers) -> None:
    server, eva, bram = wereld.server, wereld.eva, wereld.bram
    sleutel = wereld.sleutels / f"{uuid.UUID(eva.id).hex}.key"

    # --- de foto's, via de API ---
    ids: dict[str, str] = {}
    for i, (naam, schijf) in enumerate(FOTOS):
        ids[naam] = upload(server, eva, naam, schijven.foto(schijf, seed=i))
    wacht_op_previews(server, eva, list(ids.values()))
    items = server.call(eva, "GET", f"{API}/photos", expect=200).json()["items"]
    assert items[0]["name"] == "anna-1.jpg"
    assert sorted(i["name"] for i in items) == sorted(ids)
    # Uit: de app vraagt de nep niets
    assert wereld.nep.schijven.fotos == 0
    assert not sleutel.exists()

    # --- aanzetten, met het dialoog ---
    page = aan.context_as(eva).new_page()
    schakel(page, aan=True)

    # --- de scan: 7 gezichten in 8 foto's, twee groepen ---
    stand = wacht(
        server, eva, "/people/faces/status",
        lambda s: s["photos"]["waiting"] == 0 and s["faces"]["groups"] == 2,
        "de scan en de groepen", SCAN_S,
    )  # fmt: skip
    assert stand["setting"] == "aan" and stand["analyse"] == "ok" and not stand["wiping"]
    assert stand["photos"] == {"total": 8, "done": 8, "waiting": 0, "failed": 0, "skipped": 0}
    assert stand["faces"] == {"found": 7, "named": 0, "open": 7, "groups": 2}
    assert wereld.nep.schijven.fotos == 8 and wereld.nep.schijven.gezichten == 7
    assert sleutel.is_file() and sleutel.stat().st_mode & 0o777 == 0o600
    # De kaders van de nep: de schijf van anna-1, op de 1024
    gevonden = faces_van(server, eva, ids["anna-1.jpg"])
    assert len(gevonden) == 1 and gevonden[0]["person"] is None
    assert gevonden[0]["box"] == pytest.approx(list(schijven.kader(0.5, 0.45, 0.15)), abs=0.02)
    assert faces_van(server, eva, ids["leeg.jpg"]) == []

    # --- /fotos/personen: twee keer "Wie is dit?", nog geen "Is dit …?" ---
    page.goto("/fotos/personen")
    expect(page.get_by_role("heading", name="Personen", level=1)).to_be_visible()
    expect(page.locator('[data-faces="status"]')).to_have_text("Klaar: 7 gezichten in 8 foto's")
    expect(page.get_by_role("heading", name="Wie is dit?", level=2)).to_be_visible()
    expect(page.get_by_role("button", name="Wie is dit?", exact=True)).to_have_count(2)
    expect(page.get_by_role("heading", name="Is dit …?", level=2)).to_have_count(0)

    # --- de viewer: Anna met de hand, met een kader (Z6) ---
    page.goto("/fotos")
    expect(cell(page, 0)).to_have_attribute("aria-label", re.compile(r"^anna-1\.jpg"))
    tab_to(page, page.locator(FOCUSED), limit=80)
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="anna-1.jpg", exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press("i")
    gezichten = viewer.get_by_role("region", name="Gezichten", exact=True)
    onbekend = gezichten.get_by_role("button", name="Wie is dit? (1/1)", exact=True)
    expect(onbekend).to_be_visible()
    personen_rij = viewer.get_by_role("region", name="Personen", exact=True)
    toevoegen = personen_rij.get_by_role("button", name="Persoon toevoegen", exact=True)
    tab_to(page, toevoegen)
    page.keyboard.press("Enter")
    naam = personen_rij.get_by_role("combobox", name="Naam", exact=True)
    expect(naam).to_be_focused()
    page.keyboard.type("Anna", delay=20)
    nieuw = personen_rij.get_by_role("option", name="Nieuwe persoon: Anna", exact=True)
    expect(nieuw).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(naam).to_have_value("Anna")
    kader_aan = personen_rij.get_by_label("Kader tekenen", exact=True)
    tab_to(page, kader_aan, limit=3)
    page.keyboard.press("Space")
    expect(kader_aan).to_be_checked()
    kader = "Kader 35 % van links, 25 % van boven, 30 % breed en 40 % hoog."
    expect(personen_rij.get_by_text(kader, exact=True)).to_be_attached()
    knop = personen_rij.get_by_role("button", name="Toevoegen", exact=True)
    tab_to(page, knop, limit=5)
    with page.expect_response(post_naar(f"/files/{ids['anna-1.jpg']}/people")) as gepost:
        page.keyboard.press("Enter")
    assert gepost.value.status == 201, gepost.value.text()
    expect(toast(page, "Anna staat nu op deze foto.")).to_be_visible()
    # Het kader viel op het gevonden gezicht: dat is nu Anna (ja), ook in de rij Gezichten
    expect(gezichten.get_by_role("link", name="Anna", exact=True)).to_be_visible()
    expect(onbekend).to_have_count(0)
    # Het formulier is dicht en de focus staat weer op Persoon toevoegen (Z6); pas dan sluit Esc
    # de viewer (met het formulier open sluit Esc eerst het formulier)
    expect(toevoegen).to_be_focused()
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    anna_gezicht = faces_van(server, eva, ids["anna-1.jpg"])
    assert [f["person"]["name"] for f in anna_gezicht] == ["Anna"]
    anna = anna_gezicht[0]["person"]["id"]

    # --- "Is dit Anna?" komt (na VERDICT_WAIT) ---
    wacht(
        server, eva, "/people/faces/suggestions",
        lambda s: [(p["person"]["name"], p["count"]) for p in s["people"]] == [("Anna", 3)],
        "de voorstellen voor Anna", VOORSTEL_S,
    )  # fmt: skip
    page.goto("/fotos/personen")
    expect(page.get_by_role("heading", name="Is dit …?", level=2)).to_be_visible()
    kaart = page.get_by_role("button", name="Is dit Anna?", exact=True)
    expect(kaart).to_have_attribute("aria-describedby", re.compile(".+"))
    tab_to(page, kaart, limit=40)
    page.keyboard.press("Enter")
    raster = page.get_by_role("grid", name="Is dit Anna?", exact=True)
    cellen = raster.get_by_role("gridcell")
    expect(cellen).to_have_count(3)
    # De focus staat meteen in het raster (de knop van de kaart is weg)
    expect(cellen.nth(0)).to_be_focused()
    for i, toets in enumerate(("j", "j")):
        with page.expect_response(post_naar("/people/faces/confirm")) as ja:
            page.keyboard.press(toets)
        assert ja.value.status == 204
        assert ja.value.request.post_data_json["person_id"] == anna
        expect(cellen.nth(i)).to_have_attribute("aria-label", f"Gezicht {i + 1} van 3: ja")
        expect(cellen.nth(i + 1)).to_be_focused()
    expect(toast(page, "1 gezicht bevestigd als Anna.").first).to_be_visible()
    geladen: list[Any] = []
    page.on("response", lambda r: geladen.append(r) if is_voorstellen(r) else None)
    with page.expect_response(post_naar("/people/faces/reject")) as nee:
        page.keyboard.press("n")
    assert nee.value.status == 204
    expect(cellen.nth(2)).to_have_attribute("aria-label", "Gezicht 3 van 3: nee")
    expect(toast(page, "Niet Anna.")).to_be_visible()
    # Na elke keuze laadt de pagina opnieuw; na deze staat Anna niet meer in de voorstellen. Het
    # raster blijft toch open, met de keuzes en Ctrl+Z, tot Esc (de lading haalt het niet weg)
    wacht_op_lading(page, geladen, lambda s: s["people"] == [], "na de laatste keuze")
    page.wait_for_timeout(500)  # de tijd om de nieuwe lading te tekenen
    expect(raster).to_have_count(1)
    expect(cellen.nth(2)).to_be_focused()
    with page.expect_response(post_naar("/people/faces/reset")) as terug:
        page.keyboard.press("Control+z")
    assert terug.value.status == 204
    expect(cellen.nth(2)).to_have_attribute("aria-label", "Gezicht 3 van 3")
    expect(cellen.nth(2)).to_be_focused()
    with page.expect_response(post_naar("/people/faces/reject")) as nee:
        page.keyboard.press("n")
    assert nee.value.status == 204
    expect(cellen.nth(2)).to_have_attribute("aria-label", "Gezicht 3 van 3: nee")
    expect(toast(page, "Niet Anna.").first).to_be_visible()
    page.keyboard.press("Escape")
    expect(raster).to_have_count(0)
    # Dit was het laatste voorstel: de kaart van Anna is weg, en de focus met hem. Hij staat dan
    # op de eerste kaart die er nog is (of op de knop wissen, of de regel), nooit op de body
    expect(kaart).to_have_count(0)
    expect(page.locator(".people-faces :focus")).to_have_count(1)

    # Twee van de drie rode zijn nu Anna, de derde niet (en niet meer voorgesteld)
    namen = {
        n: [f["person"]["name"] if f["person"] else None for f in faces_van(server, eva, ids[n])]
        for n in ("anna-2.jpg", "anna-3.jpg", "anna-4.jpg")
    }
    ja_fotos = sorted(n for n, v in namen.items() if v == ["Anna"])
    assert len(ja_fotos) == 2 and sorted(v[0] or "" for v in namen.values()) == ["", "Anna", "Anna"]
    assert personen(server, eva) == {"Anna": 3}

    # --- zoeken op "anna" vindt ze (Ctrl+K, ↓ tot Anna, Enter) ---
    page.keyboard.press("Control+k")
    zoekveld = page.locator("#zoekveld")
    expect(zoekveld).to_be_focused()
    with page.expect_response(is_suggestie) as voorstel:
        page.keyboard.type("anna")
    assert [(p["id"], p["name"]) for p in voorstel.value.json()["people"]] == [(anna, "Anna")]
    lijst = page.get_by_role("listbox", name="Suggesties")
    optie = lijst.get_by_role("group", name="Personen").get_by_role("option")
    expect(optie).to_have_text(["Anna"])
    # De bestandsnamen (anna-1.jpg …) staan er ook: ↓ tot de persoon gekozen is
    opties = lijst.get_by_role("option")
    plek = opties.all_inner_texts().index("Anna")
    for _ in range(plek + 1):
        page.keyboard.press("ArrowDown")
    expect(optie).to_have_attribute("aria-selected", "true")
    with page.expect_response(is_zoeken) as treffers:
        page.keyboard.press("Enter")
    gevonden_namen = sorted(h["node"]["name"] for h in treffers.value.json()["results"])
    assert gevonden_namen == sorted(["anna-1.jpg", *ja_fotos])
    expect(page).to_have_url(re.compile(f"/zoeken\\?person={anna}$"))
    rijen = page.locator("#inhoud [role=grid] [role=row][aria-rowindex][data-index]")
    expect(rijen).to_have_count(3)

    # --- "Wie is dit?" → Cas ---
    page.goto("/fotos/personen")
    groep = page.get_by_role("button", name="Wie is dit?", exact=True)
    expect(groep).to_have_count(1)  # de rode groep heeft nog één open gezicht: geen groep meer
    expect(page.get_by_role("heading", name="Is dit …?", level=2)).to_have_count(0)
    tab_to(page, groep, limit=40)
    page.keyboard.press("Enter")
    raster = page.get_by_role("grid", name="Wie is dit?", exact=True)
    cellen = raster.get_by_role("gridcell")
    expect(cellen).to_have_count(3)
    expect(cellen.nth(0)).to_be_focused()
    for i in range(3):
        expect(cellen.nth(i)).to_have_attribute("aria-selected", "true")
    expect(page.get_by_role("status").filter(has_text="3 van 3 geselecteerd")).to_be_visible()
    naam = page.get_by_role("combobox", name="Naam", exact=True)
    tab_to(page, naam, limit=3)
    page.keyboard.type("Cas", delay=20)
    expect(page.get_by_role("option", name="Nieuwe persoon: Cas", exact=True)).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(naam).to_have_value("Cas")
    bevestigen = page.get_by_role("button", name="Bevestigen", exact=True)
    tab_to(page, bevestigen, limit=3)
    with page.expect_response(post_naar("/people/faces/confirm")) as bevestigd:
        page.keyboard.press("Enter")
    assert bevestigd.value.status == 204, bevestigd.value.text()
    gestuurd = bevestigd.value.request.post_data_json
    assert gestuurd["name"] == "Cas" and len(gestuurd["faces"]) == 3
    expect(toast(page, "3 gezichten bevestigd als Cas.")).to_be_visible()
    for i in range(3):
        expect(cellen.nth(i)).to_have_attribute("aria-label", f"Gezicht {i + 1} van 3: ja")
    assert personen(server, eva) == {"Anna": 3, "Cas": 3}
    for n in ("cas-1.jpg", "cas-2.jpg", "cas-3.jpg"):
        assert [f["person"]["name"] for f in faces_van(server, eva, ids[n])] == ["Cas"]

    # --- Bram ziet niets ---
    server.call(
        eva, "POST", f"{API}/files/{ids['anna-1.jpg']}/shares",
        json={"user_id": bram.id, "permission": "read"}, expect=201,
    )  # fmt: skip
    assert faces_van(server, bram, ids["anna-1.jpg"]) == []
    assert server.call(bram, "GET", f"{API}/files/{ids['anna-2.jpg']}/faces").status_code == 404
    assert server.call(bram, "GET", f"{API}/people", expect=200).json() == {"people": []}
    uitsnede = f"{API}/people/faces/{anna_gezicht[0]['id']}/crop"
    assert server.call(bram, "GET", uitsnede).status_code == 404
    weg = server.call(bram, "GET", f"{API}/people/faces/suggestions")
    assert (weg.status_code, weg.json()["detail"]["code"]) == (409, "faces_off")
    bram_page = aan.context_as(bram).new_page()
    bram_verkeer = Verkeer(bram_page)
    bram_page.goto("/fotos/personen")
    expect(bram_page.get_by_role("heading", name="Personen", level=1)).to_be_visible()
    expect(bram_page.get_by_text("Gezichten herkennen staat uit.", exact=True)).to_be_visible()
    expect(bram_page.get_by_role("heading", name="Wie is dit?")).to_have_count(0)
    bram_verkeer.stil()
    assert bram_verkeer.gezichten == []

    # --- uitzetten: 0 gezichten, de sleutel weg, Anna en Cas blijven ---
    schakel(page, aan=False)
    stand = wacht(
        server, eva, "/people/faces/status",
        lambda s: s["faces"]["found"] == 0 and not s["wiping"],
        "het wissen", WIS_S,
    )  # fmt: skip
    assert stand["setting"] == "uit"
    assert stand["faces"] == {"found": 0, "named": 0, "open": 0, "groups": 0}
    assert not sleutel.exists()
    assert personen(server, eva) == {"Anna": 3, "Cas": 3}
    assert faces_van(server, eva, ids["anna-2.jpg"]) == []
    op_foto = server.call(eva, "GET", f"{API}/files/{ids['anna-1.jpg']}/people", expect=200).json()
    assert [(p["person"]["name"], p["source"]) for p in op_foto["people"]] == [("Anna", "hand")]
    page.goto("/fotos/personen")
    expect(page.get_by_text("Gezichten herkennen staat uit.", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=re.compile(r"^Anna"))).to_have_count(1)
    expect(page.get_by_role("link", name=re.compile(r"^Cas"))).to_have_count(1)
    expect(page.get_by_role("heading", name="Wie is dit?")).to_have_count(0)

    # --- /beheer: het blok Gezichten, verbieden en weer toestaan (Keuze 67) ---
    beheer = aan.context_as(wereld.admin).new_page()
    beheer.goto("/beheer/zoeken")
    blok = beheer.locator("section[aria-labelledby=admin-faces-title]")
    expect(blok.locator('[data-search="faces-analyse"]')).to_have_text("werkt")
    expect(blok.locator('[data-search="faces-quality"]')).to_have_text("niet gemeten")
    expect(blok.locator('[data-search="faces-users"]')).to_have_text("0")
    toestaan = blok.get_by_role("checkbox", name="Gezichtsherkenning toestaan", exact=True)
    expect(toestaan).to_be_checked()
    tab_to(beheer, toestaan, limit=80)
    beheer.keyboard.press("Space")
    verbieden = beheer.get_by_role("dialog", name="Gezichtsherkenning verbieden?")
    expect(verbieden).to_be_visible()
    expect(verbieden.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    beheer.keyboard.press("Escape")  # niets veranderd
    expect(verbieden).to_be_hidden()
    expect(toestaan).to_be_checked()
    expect(toestaan).to_be_focused()
    beheer.keyboard.press("Space")
    expect(verbieden).to_be_visible()
    beheer.keyboard.press("Tab")
    expect(verbieden.get_by_role("button", name="Verbieden en wissen", exact=True)).to_be_focused()
    with beheer.expect_response(patch_beheer) as verboden:
        beheer.keyboard.press("Enter")
    assert verboden.value.status == 200, verboden.value.text()
    assert verboden.value.json()["faces"]["allowed"] is False
    expect(toestaan).not_to_be_checked()
    expect(toestaan).to_be_focused()
    eva_stand = server.call(eva, "GET", f"{API}/me/photo-settings", expect=200).json()
    assert (eva_stand["faces"], eva_stand["faces_forbidden"]) == ("uit", True)
    page.goto("/account")
    schakelaar = page.get_by_role("checkbox", name="Gezichten herkennen in mijn foto's", exact=True)
    expect(schakelaar).not_to_be_checked()
    verbod = "De beheerder heeft gezichtsherkenning uitgezet"
    expect(schakelaar).to_have_accessible_description(verbod)
    # Weer toestaan: zonder dialoog, en niemand staat daardoor aan
    with beheer.expect_response(patch_beheer) as toegestaan:
        beheer.keyboard.press("Space")
    assert toegestaan.value.status == 200, toegestaan.value.text()
    assert toegestaan.value.json()["faces"]["allowed"] is True
    expect(beheer.get_by_role("dialog")).to_have_count(0)
    expect(toestaan).to_be_checked()
    expect(toestaan).to_be_focused()
    eva_stand = server.call(eva, "GET", f"{API}/me/photo-settings", expect=200).json()
    assert (eva_stand["faces"], eva_stand["faces_forbidden"]) == ("uit", False)


# De regels op /over, in de volgorde van de pagina (web/src/about/sources.ts; about.test.ts legt
# elke naam, versie en licentie naast haar bron in de repo)
OVER = (
    "Plaatsnamen: GeoNames cities1000 (als rg_cities1000.csv uit reverse_geocoder 1.5.1), "
    "CC BY 4.0.",
    "Landen: Natural Earth 1:10m admin-0 v5.1.2 (publiek domein).",
    "De wereldkaart: Natural Earth 1:110m admin-0 (publiek domein).",
    "Gezichten vinden: YuNet 2026may van opencv_zoo, MIT, Copyright (c) 2020 Shiqi Yu.",
    "Gezichten herkennen: SFace 2021dec van opencv_zoo, Apache-2.0.",
    "Zoeken op beeld: SigLIP 2 B/32-256 van Google (google/siglip2-base-patch32-256), Apache-2.0.",
    "onnxruntime 1.31.1: MIT",
    "numpy 2.5.4: BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
    "protobuf 7.36.2: BSD-3-Clause",
    "flatbuffers 25.12.19: Apache-2.0",
    "packaging 26.3: Apache-2.0 OR BSD-2-Clause",
)


@pytest.mark.analyse
def test_over_met_alleen_het_toetsenbord(wereld: Wereld, aan: Browsers) -> None:
    """Account → Tab tot "Bronnen en licenties" (onderaan, na Apparaten en sessies) → Enter: /over
    met de focus op de kop, de regels van de kaart, de modellen en de pakketten, de plek van de
    licentieteksten, en geen vraag aan de API (vaste gegevens; ook geen route van gezichten)."""
    page = aan.context_as(wereld.bram).new_page()
    verkeer = Verkeer(page)
    page.goto("/account")
    link = page.get_by_role("region", name="Over dit platform", exact=True).get_by_role(
        "link", name="Bronnen en licenties", exact=True
    )
    expect(link).to_have_attribute("href", "/over")
    verkeer.stil()
    tab_to(page, link, limit=80)
    gevraagd: list[str] = []

    def vraag(r: Any) -> None:
        pad = r.url.split("?", 1)[0]
        if "/api/" in pad and not pad.endswith("/api/v1/events"):
            gevraagd.append(f"{r.method} {pad}")

    page.on("request", vraag)
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/over$"))
    kop = page.get_by_role("heading", name="Over dit platform", level=1)
    expect(kop).to_be_focused()
    expect(page).to_have_title("Over dit platform · Platform")
    main = page.get_by_role("main")
    expect(main.get_by_role("listitem").filter(has_not=page.get_by_role("link"))).to_have_text(
        list(OVER)
    )
    expect(main).to_contain_text(
        "De volledige licentieteksten staan naast de modellen (standaard in "
        "/opt/platform/modellen) en in de broncode, in api/app/analyse/licenties/."
    )
    for naam in ("Kaart en plaatsen", "Gezichten en zoeken op beeld", "Pakketten van de analyse"):
        expect(main.get_by_role("region", name=naam, exact=True)).to_be_visible()
    verkeer.stil()
    assert gevraagd == [] and verkeer.gezichten == []


# --- 2. zonder de analyse: precies 0.8.0 ------------------------------------------------------


def test_zonder_analyse_ziet_niemand_iets_nieuws(
    server: Server, admin: Account, user_factory: Callable[..., Account], context_as: Callable
) -> None:
    lisa = user_factory(name="Lisa Zonder")
    node = upload(server, lisa, "zonder.jpg", schijven.foto([(1, 0.5, 0.45, 0.15)], seed=99))
    wacht_op_previews(server, lisa, [node])
    instellingen = server.call(lisa, "GET", f"{API}/me/photo-settings", expect=200).json()
    assert instellingen["faces"] == "uit"
    assert instellingen["faces_available"] is False and instellingen["faces_forbidden"] is False
    # Zoals de stub van 0.8.0 (de samenvoeging van golf 6; api/tests/uit080/)
    weg = server.call(lisa, "GET", f"{API}/people/faces/status")
    assert (weg.status_code, weg.json()["detail"]) == (
        501,
        {"message": "Nog niet gebouwd (X2)", "code": "not_built", "params": {"package": "X2"}},
    )
    assert server.call(admin, "GET", f"{API}/admin/search", expect=200).json()["faces"] is None

    page = context_as(lisa).new_page()
    verkeer = Verkeer(page)
    # Account → Foto's: het vinkje van Z6 met "Nog niet beschikbaar", geen schakelaar van X2
    page.goto("/account")
    oud = page.get_by_role("checkbox", name="Gezichten herkennen", exact=True)
    expect(oud).to_be_visible()
    expect(oud).not_to_be_checked()
    hint = page.get_by_text("Nog niet beschikbaar op deze server.", exact=True)
    expect(hint.first).to_be_visible()
    expect(page.get_by_role("checkbox", name="Gezichten herkennen in mijn foto's")).to_have_count(0)
    # /fotos/personen: geen regel, geen blokken
    page.goto("/fotos/personen")
    expect(page.get_by_role("heading", name="Personen", level=1)).to_be_visible()
    verkeer.stil()
    expect(page.locator(".people-faces, .faces-status")).to_have_count(0)
    expect(page.get_by_role("heading", name="Wie is dit?")).to_have_count(0)
    # De viewer met het infopaneel: Personen wel, Gezichten niet
    page.goto("/fotos")
    tab_to(page, page.locator(FOCUSED), limit=80)
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="zonder.jpg", exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press("i")
    expect(viewer.get_by_role("region", name="Personen", exact=True)).to_be_visible()
    verkeer.stil()
    expect(viewer.get_by_role("region", name="Gezichten", exact=True)).to_have_count(0)

    # De beheerder: het blok van 0.8.0, zonder schakelaar of knop; de zin zegt sinds 0.8.1 (I2) dat
    # het niet draait en waar de beheerder ziet waarom (review I2, U2)
    beheer = context_as(admin).new_page()
    beheer_verkeer = Verkeer(beheer)
    beheer.goto("/beheer/zoeken")
    expect(beheer.get_by_role("heading", name="Zoeken en foto's", level=2)).to_be_visible()
    blok = beheer.locator("section[aria-labelledby=admin-faces-title]")
    expect(blok).to_have_text(
        "Gezichten"
        "Gezichten herkennen draait niet op deze server: de analyse staat uit, of is niet "
        "helemaal geïnstalleerd. Waarom staat in de eindtekst van het installatiescript, bij "
        "Gezichten (de container analyse). Stond daar ANALYSE=uit, draai het script dan opnieuw "
        "met ANALYSE=aan; daarna zet ieder het zelf aan bij Account → Foto's. "
        "Zie docs/runbooks/analyse.md."
    )
    expect(beheer.get_by_role("checkbox", name="Gezichtsherkenning toestaan")).to_have_count(0)
    expect(beheer.locator('[data-search^="faces"]')).to_have_count(0)
    beheer_verkeer.stil()
    assert verkeer.gezichten == [] and beheer_verkeer.gezichten == []
