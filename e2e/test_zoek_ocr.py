"""Tekst in beeld (fase 6, X1): OCR met de echte tesseract in de echte app, met alleen het
toetsenbord.

De lijst van het bouwontwerp (X1, Tests): een PDF zonder tekstlaag en een foto van een bon →
zoeken op een woord dat alleen in het beeld staat → de snippet → de viewer markeert het; een
kopie van Bram vindt de tekst uit Eva's foto niet.

Een eigen app met media aan en de lokale worker (media_harness.start_media): die draait de stap
ocr met de echte tesseract (marker ocr: de job ocr in CI, niet in de gewone run). Eva deelt haar
map Gedeeld met Bram (lezen) en zet er een scan in: de brief van tests/ocr/scans.py als PDF
zonder tekstlaag (A4 op 300 dpi, met "ocrwoordx"). Die leest de app vanzelf. Een foto van een bon
(scans.bon: een telefoonfoto met EXIF-draaiing 6, iets scheef) leest hij pas als Eva erom vraagt
met "Tekst herkennen" (Keuze 23); de tekst hoort dan bij haar node alleen (Keuze 25a).

Golf 4 (Z7 met X1): het blok OCR in de tab "Zoeken en foto's" op /beheer, met alleen het
toetsenbord: de stand, pauzeren en hervatten (een foto blijft in de rij en wordt daarna gelezen),
OCR uit (POST ocr geeft 503 ocr_off), "Alles opnieuw herkennen" en OCR weer aan (de pomp die voor
"uit" vijf minuten weg stond, komt meteen).

Elk antwoord van GET /search en van de woorden wordt meteen gelezen (expect_response). Zonder
tesseract (of zonder nld, eng of osd) of zonder DejaVu Sans faalt dit met een duidelijke melding;
overslaan doet het nooit.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from conftest import Account, Browsers, Server, _browsers_teardown, artifacts_dir

pytestmark = pytest.mark.ocr

API = "/api/v1"
ADMIN = f"{API}/admin/search"
ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"  # zonder de kopregel
PDF = "Brief gemeente.pdf"
PDF_WOORD = "ocrwoordx"  # staat alleen in het beeld van de scan (scans.LINES)
BON_WOORD = "bonwoordx"
KOPIE_WOORD = "spaarwoordx"
VINKJE_WOORD = "kapwoordx"
BEHEER_WOORD = "pauzewoordx"
WACHT_S = 90.0
# De markering ligt op het woord: het midden binnen 2% van de breedte en de hoogte van de foto
PLEK = 0.02
APT = (
    "apt install tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd "
    "fonts-dejavu-core"
)
NOG_NIET = "Nog niet gelezen. Alleen jij vindt de tekst straks met Zoeken."
GEVONDEN = "Tekst gevonden in deze foto. Alleen jij vindt hem met Zoeken."
PDF_GELEZEN = "De gescande pagina's zijn gelezen; de tekst is doorzoekbaar."


def is_zoeken(response) -> bool:
    return "/api/v1/search?" in response.url and response.request.method == "GET"


def is_woorden(node: str):
    return lambda r: f"/api/v1/files/{node}/pages/1/words?" in r.url


def patch_instellingen(r) -> bool:
    return r.request.method == "PATCH" and r.url.endswith("/api/v1/me/photo-settings")


@dataclass
class X1App:
    app: Any  # media_harness.MediaApp
    eva: Account
    bram: Account
    map: str
    pdf: str

    @property
    def server(self) -> Server:
        return self.app.server


def tesseract_en_dejavu() -> None:
    """Zoals tests/ocr/test_ocr_real.py: de worker van de app moet tesseract met de talen hebben."""
    from tests.ocr import scans

    from app.media.worker.ops import caps as caps_op
    from app.media.worker.ops import ocr

    if ocr.program() is None:
        pytest.fail(f"tesseract ontbreekt in de PATH ({APT})")
    if not caps_op.ocr():
        pytest.fail(f"tesseract kent nld, eng of osd niet ({APT})")
    scans.font()  # NoFont met de apt-regel als DejaVu Sans er niet is


def upload(server: Server, account: Account, parent: str, naam: str, data: bytes) -> str:
    return server.call(
        account,
        "PUT",
        f"{API}/files/{parent}/upload",
        params={"name": naam},
        content=data,
        expect=201,
    ).json()["id"]


def stand(server: Server, account: Account, node: str) -> dict[str, Any]:
    return server.call(account, "GET", f"{API}/files/{node}/analysis", expect=200).json()


def wacht_op_ocr(server: Server, account: Account, node: str) -> None:
    """Tot de stap ocr van dit bestand klaar is (done); een andere eindstand faalt meteen."""
    eind = time.monotonic() + WACHT_S
    while True:
        nu = stand(server, account, node)
        if nu["ocr"] == "done":
            return
        if nu["ocr"] in ("none", "failed"):
            raise AssertionError(f"OCR van {node} eindigde als {nu}")
        if time.monotonic() > eind:
            raise AssertionError(f"OCR van {node} niet klaar binnen {WACHT_S:.0f} s: {nu}")
        time.sleep(0.25)


def gevonden(server: Server, account: Account, woord: str) -> dict[str, str]:
    """GET /search: per node (id) de soort treffer."""
    r = server.call(account, "GET", f"{API}/search", params={"q": woord}, expect=200)
    return {h["node"]["id"]: h["match"] for h in r.json()["results"]}


@pytest.fixture(scope="module")
def x1(tmp_path_factory: pytest.TempPathFactory) -> Iterator[X1App]:
    """De app met media en de echte tesseract; Eva met de map Gedeeld (Bram mag lezen) en daarin
    de scan, al gelezen."""
    tesseract_en_dejavu()
    from tests.ocr import scans

    from media_harness import start_media

    app = start_media(Path(tmp_path_factory.mktemp("zoek-ocr")))
    try:
        eva = app.user(name="Eva Scan")
        bram = app.user(name="Bram Kopie")
        map_ = app.folder(eva, "Gedeeld")
        app.server.call(
            eva,
            "POST",
            f"{API}/files/{map_}/shares",
            json={"user_id": bram.id, "permission": "read"},
            expect=201,
        )
        pdf = upload(app.server, eva, map_, PDF, scans.pdf(0))
        wacht_op_ocr(app.server, eva, pdf)
        yield X1App(app, eva, bram, map_, pdf)
    finally:
        left = app.stop()
        if left:
            print(f"na het stoppen op pid weggehaald: {left}")


@pytest.fixture
def x1_browsers(
    browser: Any,
    x1: X1App,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Browsers]:
    """Contexts op de app van x1; elke melding laat de test falen."""
    b = Browsers(browser, x1.server)
    yield b
    problems = _browsers_teardown(request, b, artifacts_dir(tmp_path_factory))
    if problems:
        pytest.fail("Problemen in de browser:\n" + "\n".join(problems), pytrace=False)


def tab_to(page: Page, target: Locator, limit: int = 40) -> None:
    """Tab tot target de focus heeft."""
    target.wait_for(state="attached")
    for _ in range(limit):
        page.keyboard.press("Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return
    raise AssertionError(f"Tab kwam niet bij {target}")


def zoek(page: Page, woord: str) -> list[tuple[str, str]]:
    """Ctrl+K, het woord en Enter: naar /zoeken; geeft (id, match) van elke treffer."""
    page.keyboard.press("Control+k")
    expect(page.locator("#zoekveld")).to_be_focused()
    page.keyboard.type(woord)
    with page.expect_response(is_zoeken) as antwoord:
        page.keyboard.press("Enter")
    data = antwoord.value.json()
    expect(page).to_have_url(re.compile(rf"/zoeken\?q={woord}$"))
    return [(h["node"]["id"], h["match"]) for h in data["results"]]


def test_een_scan_zonder_tekstlaag_vinden_en_in_de_viewer_markeren(
    x1: X1App, x1_browsers: Browsers
) -> None:
    """Het woord staat alleen in het beeld van de scan: /zoeken vindt de PDF (match ocr) met het
    woord in <mark> in de snippet. Enter op de treffer opent de viewer; de woorden van pagina 1
    komen van OCR (source ocr) en het woord is gemarkeerd. Het infopaneel zegt dat de pagina's
    gelezen zijn."""
    page = x1_browsers.context_as(x1.eva).new_page()
    page.goto("/")
    expect(page.get_by_role("grid")).to_be_visible()
    assert zoek(page, PDF_WOORD) == [(x1.pdf, "ocr")]
    rij = page.locator(ROWS)
    expect(rij).to_have_count(1)
    expect(rij.locator(".snippet mark")).to_have_text([PDF_WOORD])

    tab_to(page, page.locator(f"{ROWS}[tabindex='0']"))
    with page.expect_response(is_woorden(x1.pdf)) as woorden:
        page.keyboard.press("Enter")
    antwoord = woorden.value
    assert antwoord.status == 200, antwoord.text()
    data = antwoord.json()
    assert data["source"] == "ocr", data["source"]
    assert PDF_WOORD in [w["t"] for w in data["words"]]
    expect(page).to_have_url(re.compile(rf"/zoeken\?q={PDF_WOORD}&bestand={x1.pdf}$"))
    viewer = page.get_by_role("dialog", name=PDF, exact=True)
    mark = viewer.locator(".text-layer mark")
    expect(mark).to_have_text([PDF_WOORD])
    expect(mark).to_be_in_viewport()

    page.keyboard.press("i")
    tekst = viewer.get_by_role("region", name="Tekst in beeld", exact=True)
    expect(tekst.get_by_role("status")).to_have_text(PDF_GELEZEN)
    expect(tekst.get_by_role("button", name="Opnieuw herkennen", exact=True)).to_be_visible()

    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    expect(rij).to_be_focused()


def test_een_foto_van_een_bon_herkennen_vinden_en_markeren(
    x1: X1App, x1_browsers: Browsers
) -> None:
    """Eva opent haar foto van een bon in de viewer. Het infopaneel (i) zegt dat hij nog niet
    gelezen is; Tab naar "Tekst herkennen" en Enter (202). Na search.ready zegt de rij dat er
    tekst is, en de focus staat nog op de knop (nu "Opnieuw herkennen"). Dan zoekt ze een woord
    van de bon: de foto (match ocr) met de snippet; Enter opent de viewer met de woorden van de
    foto over het beeld en het woord gemarkeerd, op zijn plek in de foto rechtop (EXIF 6)."""
    from tests.ocr import scans

    data, deel = scans.bon(BON_WOORD)
    naam = "bon bakker.jpg"
    foto = upload(x1.server, x1.eva, x1.map, naam, data)
    page = x1_browsers.context_as(x1.eva).new_page()
    page.goto(f"/map/{x1.map}?bestand={foto}")
    viewer = page.get_by_role("dialog", name=naam, exact=True)
    expect(viewer).to_be_visible()
    expect(viewer.locator("img.zoom-img")).to_be_visible()
    page.keyboard.press("i")
    tekst = viewer.get_by_role("region", name="Tekst in beeld", exact=True)
    expect(tekst.get_by_role("status")).to_have_text(NOG_NIET)
    knop = tekst.get_by_role("button", name="Tekst herkennen", exact=True)
    tab_to(page, knop)

    pad = f"{API}/files/{foto}/ocr"
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(pad)
    ) as gevraagd:
        page.keyboard.press("Enter")
    assert gevraagd.value.status == 202, gevraagd.value.text()
    expect(tekst.get_by_role("status")).to_have_text(GEVONDEN, timeout=WACHT_S * 1000)
    opnieuw = tekst.get_by_role("button", name="Opnieuw herkennen", exact=True)
    expect(opnieuw).to_be_focused()
    assert stand(x1.server, x1.eva, foto)["ocr"] == "done"

    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    assert zoek(page, BON_WOORD) == [(foto, "ocr")]
    rij = page.locator(ROWS)
    expect(rij.locator(".snippet mark")).to_have_text([BON_WOORD])
    tab_to(page, page.locator(f"{ROWS}[tabindex='0']"))
    with page.expect_response(is_woorden(foto)) as woorden:
        page.keyboard.press("Enter")
    antwoord = woorden.value
    assert antwoord.status == 200, antwoord.text()
    assert antwoord.json()["source"] == "ocr"
    # Het woord kwam niet in de adresbalk van de woorden (alleen het download-token)
    assert BON_WOORD not in antwoord.url and "q=" not in antwoord.url, antwoord.url
    viewer = page.get_by_role("dialog", name=naam, exact=True)
    mark = viewer.locator(".photo-words mark")
    expect(mark).to_have_text([BON_WOORD])
    expect(viewer.get_by_role("status").filter(has_text="gemarkeerd in de foto")).to_have_text(
        "1 woord van je zoekvraag gemarkeerd in de foto"
    )

    # Op zijn plek: het midden van de markering, als deel van het beeld zoals de viewer het toont
    beeld = viewer.locator("img.zoom-img").bounding_box()
    vak = mark.bounding_box()
    assert beeld and vak
    x = (vak["x"] + vak["width"] / 2 - beeld["x"]) / beeld["width"]
    y = (vak["y"] + vak["height"] / 2 - beeld["y"]) / beeld["height"]
    want = deel[BON_WOORD]
    print(f"X1 markering: midden ({x:.3f}, {y:.3f}), in de foto ({want[0]:.3f}, {want[1]:.3f})")
    assert abs(x - want[0]) <= PLEK and abs(y - want[1]) <= PLEK, ((x, y), want)

    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    expect(rij).to_be_focused()


def test_een_kopie_van_bram_vindt_de_tekst_uit_eva_s_foto_niet(
    x1: X1App, x1_browsers: Browsers
) -> None:
    """Eva's foto van een bon is gelezen; zij vindt hem op een woord van de bon. Bram ziet de
    foto via de deling en kopieert hem naar zijn eigen map. Hij vindt het woord niet (niet via
    de deling, niet via zijn kopie), en in de viewer heeft zijn kopie geen woorden (404) en geen
    markering; zijn eigen rij zegt "nog niet gelezen". De scan van Eva vindt hij wel: de tekst
    van een PDF hoort bij de inhoud, en hij mag de PDF lezen."""
    from tests.ocr import scans

    data, _ = scans.bon(KOPIE_WOORD)
    naam = "bon markt.jpg"
    foto = upload(x1.server, x1.eva, x1.map, naam, data)
    x1.server.call(x1.eva, "POST", f"{API}/files/{foto}/ocr", expect=202)
    wacht_op_ocr(x1.server, x1.eva, foto)
    assert gevonden(x1.server, x1.eva, KOPIE_WOORD) == {foto: "ocr"}
    kopie = x1.server.call(
        x1.bram,
        "POST",
        f"{API}/files/{foto}/copy",
        json={"parent_id": x1.bram.root_id},
        expect=201,
    ).json()["id"]
    assert stand(x1.server, x1.bram, kopie)["ocr"] == "skip"
    assert stand(x1.server, x1.bram, foto)["ocr"] == "skip"

    page = x1_browsers.context_as(x1.bram).new_page()
    page.goto("/")
    expect(page.get_by_role("grid")).to_be_visible()
    assert zoek(page, KOPIE_WOORD) == []
    expect(page.get_by_text("Niets gevonden.", exact=True)).to_be_visible()
    page.goto("/")
    expect(page.get_by_role("grid")).to_be_visible()
    assert zoek(page, PDF_WOORD) == [(x1.pdf, "ocr")]

    with page.expect_response(is_woorden(kopie)) as woorden:
        page.goto(f"/?bestand={kopie}&q={KOPIE_WOORD}")
    assert woorden.value.status == 404
    viewer = page.get_by_role("dialog", name=naam, exact=True)
    expect(viewer.locator("img.zoom-img")).to_be_visible()
    expect(viewer.locator(".photo-words")).to_have_count(0)
    page.keyboard.press("i")
    tekst = viewer.get_by_role("region", name="Tekst in beeld", exact=True)
    expect(tekst.get_by_role("status")).to_have_text(NOG_NIET)
    expect(viewer.locator(".photo-words")).to_have_count(0)


def test_tekst_in_foto_s_herkennen_met_het_toetsenbord(x1: X1App, x1_browsers: Browsers) -> None:
    """Keuze 23: het vinkje "Tekst in foto's herkennen" onder Account (standaard uit), met Tab en
    Spatie. Aan: een nieuwe foto van Fien wordt vanzelf gelezen, zonder "Tekst herkennen", en ze
    vindt hem op een woord van de bon. Weer Spatie zet het uit; de focus blijft op het vinkje."""
    from tests.ocr import scans

    fien = x1.app.user(name="Fien Vinkje")
    page = x1_browsers.context_as(fien).new_page()
    page.goto("/account")
    blok = page.get_by_role("region", name="Foto's", exact=True)
    vinkje = blok.get_by_label("Tekst in foto's herkennen", exact=True)
    expect(vinkje).not_to_be_checked()
    tab_to(page, vinkje, limit=60)
    with page.expect_response(patch_instellingen) as aan:
        page.keyboard.press("Space")
    assert aan.value.status == 200, aan.value.text()
    assert aan.value.json()["ocr_photos"] is True
    expect(vinkje).to_be_checked()

    data, _ = scans.bon(VINKJE_WOORD)
    foto = upload(x1.server, fien, str(fien.root_id), "bon kapper.jpg", data)
    wacht_op_ocr(x1.server, fien, foto)
    assert gevonden(x1.server, fien, VINKJE_WOORD) == {foto: "ocr"}

    with page.expect_response(patch_instellingen) as uit:
        page.keyboard.press("Space")
    assert uit.value.status == 200, uit.value.text()
    assert uit.value.json()["ocr_photos"] is False
    expect(vinkje).not_to_be_checked()
    expect(vinkje).to_be_focused()


# --- golf 4: het blok OCR op /beheer (Z7 met X1) -------------------------------------------------


def is_admin_search(method: str, suffix: str = ""):
    def match(r: Any) -> bool:
        path = r.url.split("?", 1)[0]
        return r.request.method == method and path.endswith(f"{ADMIN}{suffix}")

    return match


def toast(page: Page, tekst: str) -> Locator:
    """Een melding (niet de kopie voor de schermlezer)."""
    return page.get_by_role("region", name="Meldingen").get_by_text(tekst, exact=True)


def in_de_rij(n: int) -> str:
    """De regel "In de rij" zoals de tab hem schrijft (nl-NL)."""
    if n == 1:
        return "1 inhoud (een PDF of foto)"
    return f"{n:,}".replace(",", ".") + " inhouden (PDF's en foto's)"


def ocr_blok(x1: X1App) -> dict[str, Any]:
    return x1.server.call(x1.app.admin, "GET", ADMIN, expect=200).json()["ocr"]


def wacht_op_blok(x1: X1App, klaar: Any, wat: str) -> dict[str, Any]:
    eind = time.monotonic() + WACHT_S
    gezien: dict[str, Any] = {}
    while time.monotonic() < eind:
        gezien = ocr_blok(x1)
        if klaar(gezien):
            return gezien
        time.sleep(0.25)
    raise AssertionError(f"{wat} niet binnen {WACHT_S:.0f} s: {gezien}")


def pomp_later(x1: X1App) -> bool:
    """Staat de pomp van de rij ocr minstens een minuut later klaar (jobs.Defer van OCR uit)?
    Alleen lezen, uit de database van de draaiende app."""
    from app.analysis import pump_type

    db = x1.server.data_dir / "platform.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(
            "SELECT run_after FROM job WHERE type = ? AND status = 'queued'", (pump_type("ocr"),)
        ).fetchall()
    finally:
        con.close()
    grens = datetime.now(UTC) + timedelta(seconds=60)
    for (waarde,) in rows:
        moment = datetime.fromisoformat(str(waarde))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if moment > grens:
            return True
    return False


def tab_terug(page: Page, target: Locator, limit: int = 40) -> None:
    """Shift+Tab tot target de focus heeft."""
    target.wait_for(state="attached")
    for _ in range(limit):
        page.keyboard.press("Shift+Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return
    raise AssertionError(f"Shift+Tab kwam niet bij {target}")


def test_ocr_op_beheer_pauzeren_uitzetten_en_opnieuw_herkennen(
    x1: X1App, x1_browsers: Browsers
) -> None:
    """Golf 4: de beheerder opent met Alt+9 de tab "Zoeken en foto's". Het blok OCR zegt
    geïnstalleerd, aan en een lege rij, met de knoppen "OCR uitzetten" en "Alles opnieuw
    herkennen". Met de pauze blijft een foto waar Eva om vroeg in de rij (de tab telt hem);
    hervatten en hij wordt gelezen. OCR uit (geen vraag vooraf): POST ocr geeft 503 ocr_off.
    "Alles opnieuw herkennen": Esc in de vraag doet niets, Starten zet de scan terug in de rij
    (zoeken vindt de oude tekst nog), en de pomp wijkt voor "uit". OCR aanzetten vraagt eerst
    (Esc doet niets); daarna leest de app de scan meteen opnieuw, niet pas na vijf minuten."""
    from tests.ocr import scans

    wacht_op_blok(x1, lambda o: o["pages_queued"] == 0, "een lege rij ocr")
    page = x1_browsers.context_as(x1.app.admin).new_page()
    patches: list[str] = []
    posts: list[str] = []
    page.on(
        "request",
        lambda r: (
            (patches if r.method == "PATCH" else posts).append(r.url)
            if r.method in ("PATCH", "POST") and ADMIN in r.url
            else None
        ),
    )
    page.goto("/beheer")
    tabs = page.get_by_role("navigation", name="Onderdelen van beheer")
    expect(tabs.get_by_role("link", name="Zoeken en foto's", exact=True)).to_be_visible()
    with page.expect_response(is_admin_search("GET")) as eerste:
        page.keyboard.press("Alt+9")
    assert eerste.value.status == 200
    begin = eerste.value.json()
    assert begin["ocr"] == {"installed": True, "on": True, "pages_queued": 0}
    assert begin["paused"] is False
    expect(page).to_have_url(re.compile(r"/beheer/zoeken$"))

    blok = page.get_by_role("region", name="OCR", exact=True)
    rij = page.locator('[data-search="ocr-queued"]')
    aan = blok.locator("dl > div").filter(has=page.get_by_text("Aan", exact=True)).locator("dd")
    expect(
        blok.locator("dl > div")
        .filter(has=page.get_by_text("Geïnstalleerd", exact=True))
        .locator("dd")
    ).to_have_text("ja")
    expect(aan).to_have_text("ja")
    expect(rij).to_have_text(in_de_rij(0))
    expect(blok.get_by_text("OCR: niet geïnstalleerd", exact=False)).to_have_count(0)
    knop = page.locator('[data-search="ocr-button"]')
    opnieuw = page.locator('[data-search="rebuild-ocr"]')
    expect(knop).to_have_text("OCR uitzetten")
    # De tekst zegt wat de knop doet, dus geen aria-pressed (review golf 4, contract F1)
    assert knop.get_attribute("aria-pressed") is None
    expect(opnieuw).to_have_text("Alles opnieuw herkennen")

    # --- de pauze: een foto blijft in de rij, de tab telt hem; hervatten en hij wordt gelezen ---
    pauze = page.locator('[data-search="pause-button"]')
    tab_to(page, pauze)
    with page.expect_response(is_admin_search("PATCH")) as patch:
        page.keyboard.press("Enter")
    assert patch.value.status == 200
    assert patch.value.json()["paused"] is True
    expect(toast(page, "Het achtergrondwerk staat op pauze.")).to_be_visible()
    data, _ = scans.bon(BEHEER_WOORD)
    foto = upload(x1.server, x1.eva, x1.map, "bon pauze.jpg", data)
    x1.server.call(x1.eva, "POST", f"{API}/files/{foto}/ocr", expect=202)
    expect(rij).to_have_text(in_de_rij(1), timeout=20_000)  # de tab peilt elke 5 s
    time.sleep(3)
    assert stand(x1.server, x1.eva, foto)["ocr"] == "queued"
    assert ocr_blok(x1)["pages_queued"] == 1
    expect(pauze).to_be_focused()
    with page.expect_response(is_admin_search("PATCH")) as patch:
        page.keyboard.press("Enter")
    assert patch.value.status == 200
    assert patch.value.json()["paused"] is False
    expect(toast(page, "Het achtergrondwerk loopt weer.")).to_be_visible()
    wacht_op_ocr(x1.server, x1.eva, foto)
    assert gevonden(x1.server, x1.eva, BEHEER_WOORD) == {foto: "ocr"}
    expect(rij).to_have_text(in_de_rij(0), timeout=20_000)

    # --- OCR uitzetten: geen vraag vooraf; POST ocr geeft dan 503 ocr_off ---
    tab_to(page, knop)
    with page.expect_response(is_admin_search("PATCH")) as patch:
        page.keyboard.press("Enter")
    assert patch.value.status == 200
    assert patch.value.json()["ocr"] == {"installed": True, "on": False, "pages_queued": 0}
    expect(toast(page, "OCR staat uit.")).to_be_visible()
    expect(knop).to_have_text("OCR aanzetten")
    assert knop.get_attribute("aria-pressed") is None
    expect(knop).to_be_focused()
    expect(aan).to_have_text("nee")
    r = x1.server.call(x1.eva, "POST", f"{API}/files/{foto}/ocr", expect=503)
    assert r.json()["detail"]["code"] == "ocr_off"

    # --- Alles opnieuw herkennen: Esc doet niets, Starten zet de scan terug in de rij ---
    tab_to(page, opnieuw)
    vraag = page.get_by_role("dialog", name="Alles opnieuw herkennen?")
    page.keyboard.press("Enter")
    expect(vraag).to_be_visible()
    expect(vraag.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(vraag).to_have_count(0)
    expect(opnieuw).to_be_focused()
    assert posts == []
    page.keyboard.press("Enter")
    expect(vraag).to_be_visible()
    page.keyboard.press("Tab")
    expect(vraag.get_by_role("button", name="Starten", exact=True)).to_be_focused()
    with page.expect_response(is_admin_search("POST", "/rebuild")) as gestart:
        page.keyboard.press("Enter")
    assert gestart.value.status == 202, gestart.value.text()
    expect(toast(page, "Het opnieuw herkennen is gestart.")).to_be_visible()
    assert stand(x1.server, x1.eva, x1.pdf)["ocr"] == "queued"
    # De oude tekst blijft vindbaar tot de nieuwe er is
    assert gevonden(x1.server, x1.eva, PDF_WOORD) == {x1.pdf: "ocr"}
    rij_nu = ocr_blok(x1)["pages_queued"]
    assert rij_nu >= 1
    expect(rij).to_have_text(in_de_rij(rij_nu), timeout=20_000)
    # OCR staat uit: de pomp wijkt (jobs.Defer van vijf minuten), de scan blijft wachten
    eind = time.monotonic() + 20
    while not pomp_later(x1):
        assert time.monotonic() < eind, "de pomp van ocr week niet voor OCR uit"
        time.sleep(0.25)
    assert stand(x1.server, x1.eva, x1.pdf)["ocr"] == "queued"

    # --- OCR aanzetten: eerst de vraag (Esc doet niets), dan meteen weer lezen ---
    tab_terug(page, knop)
    vraag = page.get_by_role("dialog", name="OCR aanzetten?")
    page.keyboard.press("Enter")
    expect(vraag).to_be_visible()
    expect(vraag.get_by_role("button", name="Annuleren", exact=True)).to_be_focused()
    gezien = len(patches)
    page.keyboard.press("Escape")
    expect(vraag).to_have_count(0)
    expect(knop).to_be_focused()
    assert len(patches) == gezien
    page.keyboard.press("Enter")
    expect(vraag).to_be_visible()
    page.keyboard.press("Tab")
    expect(vraag.get_by_role("button", name="Aanzetten", exact=True)).to_be_focused()
    with page.expect_response(is_admin_search("PATCH")) as patch:
        page.keyboard.press("Enter")
    assert patch.value.status == 200
    assert patch.value.json()["ocr"]["on"] is True
    expect(toast(page, "OCR staat aan.")).to_be_visible()
    expect(knop).to_have_text("OCR uitzetten")
    expect(aan).to_have_text("ja")
    # Ruim binnen de vijf minuten van de pomp die voor "uit" weg stond
    wacht_op_ocr(x1.server, x1.eva, x1.pdf)
    assert gevonden(x1.server, x1.eva, PDF_WOORD) == {x1.pdf: "ocr"}
    wacht_op_blok(x1, lambda o: o["pages_queued"] == 0, "de rij ocr na opnieuw herkennen")
    expect(rij).to_have_text(in_de_rij(0), timeout=20_000)
