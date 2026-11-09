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

Elk antwoord van GET /search en van de woorden wordt meteen gelezen (expect_response). Zonder
tesseract (of zonder nld, eng of osd) of zonder DejaVu Sans faalt dit met een duidelijke melding;
overslaan doet het nooit.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from conftest import Account, Browsers, Server, _browsers_teardown, artifacts_dir

pytestmark = pytest.mark.ocr

API = "/api/v1"
ROWS = "#inhoud [role=grid] [role=row][aria-rowindex][data-index]"  # zonder de kopregel
PDF = "Brief gemeente.pdf"
PDF_WOORD = "ocrwoordx"  # staat alleen in het beeld van de scan (scans.LINES)
BON_WOORD = "bonwoordx"
KOPIE_WOORD = "spaarwoordx"
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
