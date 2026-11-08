"""Personen (fase 6, Z6; Keuze 37, 38, 41, 43, 44) in de browser, alleen met het toetsenbord:
Tab, Enter, Spatie, de pijlen en typen; nergens een klik (docs/fase-6/toetsenbord-z6.md).

- Een persoon toevoegen met een kader: de viewer (Enter), het infopaneel (I), "Persoon
  toevoegen", een nieuwe naam typen en met Enter kiezen, "Kader tekenen" (Spatie), het kader met
  de pijlen verplaatsen en met Shift groter maken, Toevoegen. Met het formulier open bladeren →
  en End niet door de viewer; zonder formulier wel, en dan staat de focus op het beeld.
- Zoeken op de naam: /fotos/personen, typen, Enter opent de persoon; ook de groep personen in
  GET /search. In de web van Z4 (sinds de samenvoeging van golf 2): Ctrl+K en de naam geven de
  persoon onder Personen in de suggesties; Enter zet het filter person op /zoeken, met de naam
  op de chip. Op /zoeken?q= staat de persoon in de groep bovenaan, en Enter zet hetzelfde filter
  (Keuze 38).
- Het filter op /fotos: de persoon kiezen (↓, Enter), alleen zijn foto; een soort zonder treffers
  (de lege staat), Filters wissen; een datum Vanaf typen geeft één vraag, bij Tab. Het filter
  blijft in de pagina, nooit in de adresbalk.
- "Vandaag, N jaar geleden" met een vaste dag: de test zet ?day= op het verzoek (nooit de klok
  van vandaag), opent een herinnering en verbergt de dag.
- Account → Foto's: gezichten en zoeken op beeld aanzetten kan nog niet (de API zegt 409); de
  reden staat onder het vinkje, het vinkje blijft uit en de rest van de pagina werkt door.

De foto's komen van een vaste dag (LATEST), niet van nu.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Locator, Page, expect

import seed

pytestmark = pytest.mark.zoek

PHOTOS = 4
STEP = timedelta(days=40)
# Vast: de nieuwste foto is van 19 mei 2026 (LATEST min één stap), de oudste van 19 januari
LATEST = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
FOCUSED = '[role=gridcell][tabindex="0"]'
ACTIEF = """() => {
  const a = document.activeElement;
  if (!a) return "-";
  const naam = (a.getAttribute("aria-label") || a.textContent || "").trim();
  return `${a.tagName} ${naam.slice(0, 40)}`;
}"""


def tab_to(page: Page, target: Locator, limit: int = 40, back: bool = False) -> int:
    """Tab (of Shift+Tab) tot target de focus heeft; geeft het aantal keer."""
    target.wait_for(state="attached")
    gezien = []
    for i in range(limit):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        if target.evaluate("(e) => e === document.activeElement"):
            return i + 1
        gezien.append(page.evaluate(ACTIEF))
    raise AssertionError(f"Tab kwam niet bij {target}: {gezien}")


def twee_frames(page: Page) -> None:
    """Wat een toets deed (een ander bestand in de viewer), staat er na twee frames."""
    page.evaluate(
        "() => new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)))"
    )


# Elke layout shift sinds het laden (zonder invoer vlak ervoor), zoals CLS hem telt
SCHUIVEN = """
window.__schuiven = [];
new PerformanceObserver((l) => l.getEntries().forEach((e) => {
  if (!e.hadRecentInput) window.__schuiven.push(e.value);
})).observe({ type: "layout-shift", buffered: true });
"""
# Boven de tijdlijn komen de filters en de herinneringen in dezelfde commit als de tijdlijn; voor
# golf 2 was /fotos 0 (review golf 2, web F: daarna 0,06 en met herinneringen 0,17)
MAX_SCHUIVEN = 0.01


def schuiven(page: Page) -> float:
    """De som van de layout shifts van deze pagina tot nu."""
    twee_frames(page)
    page.wait_for_timeout(300)  # de observer meldt pas na het frame
    return page.evaluate("() => window.__schuiven.reduce((a, b) => a + b, 0)")


def is_zoeken(r) -> bool:
    return "/api/v1/search?" in r.url and r.request.method == "GET"


def is_suggestie(r) -> bool:
    return "/api/v1/search/suggest?" in r.url and r.request.method == "GET"


def patch_instellingen(r) -> bool:
    return r.request.method == "PATCH" and r.url.endswith("/api/v1/me/photo-settings")


def toast(page: Page, tekst: str) -> Locator:
    """Een melding (niet de kopie voor de schermlezer)."""
    return page.get_by_role("region", name="Meldingen").get_by_text(tekst, exact=True)


def cell(page: Page, i: int) -> Locator:
    return page.locator(f'[role=gridcell][data-index="{i}"]')


@pytest.fixture
def fien(user_factory, seeded):
    """Fien met vier foto's (foto-00000.webp is de nieuwste, 19 mei 2026)."""
    fien = user_factory(name="Fien Personen")

    def fill(data_dir):
        blobs = seed.make_blobs(data_dir, PHOTOS)
        seed.seed_folder(
            data_dir, fien, PHOTOS, blobs, name="Familie", photos_only=True, step=STEP,
            latest=LATEST,
        )  # fmt: skip

    seeded(fill)
    return fien


def test_persoon_toevoegen_met_kader_zoeken_en_filteren(server, fien, context_as):
    items = server.call(fien, "GET", "/api/v1/photos", expect=200).json()["items"]
    assert [i["name"] for i in items] == [f"foto-0000{i}.webp" for i in range(PHOTOS)]
    eerste = items[0]
    page = context_as(fien).new_page()
    page.add_init_script(SCHUIVEN)
    page.goto("/fotos")
    expect(cell(page, 0)).to_have_attribute("aria-label", re.compile(r"^foto-00000\.webp"))

    # --- de viewer en het infopaneel ---
    tab_to(page, page.locator(FOCUSED), limit=80)
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="foto-00000.webp", exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press("i")
    personen = viewer.get_by_role("region", name="Personen", exact=True)
    expect(personen.get_by_text("Nog niemand op deze foto.", exact=True)).to_be_visible()
    toevoegen = personen.get_by_role("button", name="Persoon toevoegen", exact=True)
    tab_to(page, toevoegen)
    page.keyboard.press("Enter")

    # --- een nieuwe naam typen en kiezen ---
    naam = personen.get_by_role("combobox", name="Naam", exact=True)
    expect(naam).to_be_focused()
    page.keyboard.type("Oma Ria", delay=20)
    nieuw = personen.get_by_role("option", name="Nieuwe persoon: Oma Ria", exact=True)
    expect(nieuw).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(naam).to_have_value("Oma Ria")
    expect(personen.get_by_role("listbox")).to_be_hidden()

    # --- het kader: Spatie zet het aan, de pijlen verplaatsen, Shift maakt groter ---
    kader_aan = personen.get_by_label("Kader tekenen", exact=True)
    tab_to(page, kader_aan, limit=3)
    page.keyboard.press("Space")
    expect(kader_aan).to_be_checked()
    kader = personen.get_by_role("application", name="Kader op de foto", exact=True)
    tab_to(page, kader, limit=3)
    for _ in range(3):
        page.keyboard.press("ArrowRight")
    for _ in range(2):
        page.keyboard.press("Shift+ArrowDown")
    nu = "Kader 41 % van links, 25 % van boven, 30 % breed en 44 % hoog."
    expect(personen.get_by_text(nu, exact=True)).to_be_attached()
    # De pijlen bladerden niet door de viewer
    expect(viewer).to_be_visible()
    # Ook niet vanaf het vinkje of een knop van het formulier (review golf 2, web C): het
    # formulier blijft, met de focus waar hij stond
    tab_to(page, kader_aan, limit=3, back=True)
    for toets in ("ArrowRight", "End"):
        page.keyboard.press(toets)
        twee_frames(page)
        expect(kader_aan).to_be_focused()
    expect(viewer).to_be_visible()
    expect(naam).to_have_value("Oma Ria")

    # --- Toevoegen ---
    knop = personen.get_by_role("button", name="Toevoegen", exact=True)
    tab_to(page, knop, limit=5)
    page.keyboard.press("ArrowRight")
    twee_frames(page)
    expect(knop).to_be_focused()
    expect(viewer).to_be_visible()
    pad = f"/api/v1/files/{eerste['id']}/people"
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(pad)
    ) as gepost:
        page.keyboard.press("Enter")
    antwoord = gepost.value
    assert antwoord.status == 201, antwoord.text()
    expect(toast(page, "Oma Ria staat nu op deze foto.")).to_be_visible()
    op_foto = personen.get_by_role("link", name="Oma Ria", exact=True)
    expect(op_foto).to_be_visible()
    expect(personen.get_by_text("met kader", exact=True)).to_be_visible()
    # De focus staat weer op Persoon toevoegen
    expect(toevoegen).to_be_focused()

    erop = server.call(fien, "GET", f"/api/v1/files/{eerste['id']}/people", expect=200).json()
    assert [(p["person"]["name"], p["source"]) for p in erop["people"]] == [("Oma Ria", "hand")]
    assert erop["people"][0]["box"] == pytest.approx([0.41, 0.25, 0.3, 0.44], abs=1e-6)
    oma = erop["people"][0]["person"]["id"]

    # Zonder formulier bladert → ook vanaf de link naar de persoon. Die link is bij de volgende
    # foto weg; de focus gaat dan naar het beeld, niet naar de body (review golf 2, web C)
    tab_to(page, op_foto, limit=5, back=True)
    page.keyboard.press("ArrowRight")
    volgende = page.get_by_role("dialog", name="foto-00001.webp", exact=True)
    expect(volgende).to_be_visible()
    expect(volgende.locator(".viewer-stage")).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(viewer).to_be_visible()
    expect(viewer.locator(".viewer-stage")).to_be_focused()
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()

    # --- zoeken op de naam: de groep personen in /search en /fotos/personen ---
    gevonden = server.call(fien, "GET", "/api/v1/search", params={"q": "oma"}, expect=200).json()
    groep = gevonden["groups"]["people"]
    assert [(g["id"], g["name"], g["n"]) for g in groep] == [(oma, "Oma Ria", 1)]

    # --- het veld bovenaan (Z4): de persoon in de suggesties zet het filter person ---
    page.keyboard.press("Control+k")
    zoekveld = page.locator("#zoekveld")
    expect(zoekveld).to_be_focused()
    with page.expect_response(is_suggestie) as voorstel:
        page.keyboard.type("oma")
    assert [(p["id"], p["name"]) for p in voorstel.value.json()["people"]] == [(oma, "Oma Ria")]
    lijst = page.get_by_role("listbox", name="Suggesties")
    expect(lijst.get_by_role("group", name="Personen").get_by_role("option")).to_have_text(
        ["Oma Ria"]
    )
    expect(lijst.get_by_role("option")).to_have_text(
        ["Oma Ria", "Zoek 'oma' op wat er op je foto's staat"]
    )
    page.keyboard.press("ArrowDown")
    with page.expect_response(is_zoeken) as treffers:
        page.keyboard.press("Enter")
    namen = [h["node"]["name"] for h in treffers.value.json()["results"]]
    assert parse_qs(urlsplit(treffers.value.url).query)["person"] == [oma]
    assert namen == ["foto-00000.webp"]
    expect(page).to_have_url(re.compile(f"/zoeken\\?person={oma}$"))
    rijen = page.locator("#inhoud [role=grid] [role=row][aria-rowindex][data-index]")
    expect(rijen).to_have_count(1)
    expect(page.get_by_role("button", name="Filter weghalen: Oma Ria", exact=True)).to_be_visible()

    # --- /zoeken?q=oma: de groep personen bovenaan; Enter zet hetzelfde filter ---
    with page.expect_response(is_zoeken) as op_naam:
        page.goto("/zoeken?q=oma")
    assert [g["id"] for g in op_naam.value.json()["groups"]["people"]] == [oma]
    in_groep = page.get_by_role("region", name="Personen", exact=True).get_by_role(
        "link", name="Oma Ria (1)", exact=True
    )
    tab_to(page, in_groep, limit=20)
    with page.expect_response(is_zoeken) as op_persoon:
        page.keyboard.press("Enter")
    assert [h["node"]["name"] for h in op_persoon.value.json()["results"]] == ["foto-00000.webp"]
    expect(page).to_have_url(re.compile(f"/zoeken\\?person={oma}$"))

    page.goto("/fotos/personen")
    expect(page.get_by_role("heading", name="Personen", level=1)).to_be_visible()
    zoek = page.get_by_role("searchbox", name="Zoek een persoon", exact=True)
    tab_to(page, zoek, limit=60)
    page.keyboard.type("ria", delay=20)
    expect(page.get_by_role("link", name=re.compile(r"^Oma Ria"))).to_have_count(1)
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(f"/fotos/personen/{oma}$"))
    expect(page.get_by_role("heading", name="Oma Ria", level=1)).to_be_visible()
    raster = page.get_by_role("grid", name="Foto's van Oma Ria", exact=True)
    expect(raster.get_by_role("gridcell")).to_have_count(1)
    expect(raster.get_by_role("gridcell").first).to_have_attribute(
        "aria-label", re.compile(r"^foto-00000\.webp")
    )

    # --- het filter op /fotos: de persoon, een soort zonder treffers, wissen ---
    personen_vragen: list[str] = []
    page.on(
        "request", lambda r: personen_vragen.append(r.url) if "/api/v1/people" in r.url else None
    )
    page.goto("/fotos")
    tijdlijn = page.get_by_role("grid", name="Tijdlijn", exact=True)
    expect(tijdlijn.get_by_role("gridcell")).to_have_count(PHOTOS)
    filter_persoon = page.get_by_role("combobox", name="Persoon", exact=True)
    expect(filter_persoon).to_be_visible()
    # De balk Filters kwam met de tijdlijn: die schoof niet omlaag (review golf 2, web F)
    assert schuiven(page) <= MAX_SCHUIVEN, page.evaluate("() => window.__schuiven")
    tab_to(page, filter_persoon, limit=60)
    page.keyboard.type("oma", delay=20)
    expect(page.get_by_role("option", name="Oma Ria", exact=True)).to_be_visible()
    with page.expect_response(lambda r: "/api/v1/photos/months?" in r.url) as maanden:
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
    assert parse_qs(urlsplit(maanden.value.url).query)["person"] == [oma]
    assert sum(m["count"] for m in maanden.value.json()["months"]) == 1
    chip = page.get_by_role("button", name="Oma Ria uit het filter halen", exact=True)
    expect(chip).to_be_visible()
    expect(tijdlijn.get_by_role("gridcell")).to_have_count(1)
    expect(page.get_by_role("status").filter(has_text="1 filter aan")).to_be_visible()
    # Nooit in de adresbalk
    assert urlsplit(page.url).path == "/fotos" and not urlsplit(page.url).query

    soort = page.get_by_role("combobox", name="Soort", exact=True)
    tab_to(page, soort, limit=10)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    expect(soort).to_have_value("video")
    expect(page.get_by_text("Geen foto's die bij deze filters passen.", exact=True)).to_be_visible()
    wissen = page.get_by_role("button", name="Filters wissen", exact=True).first
    tab_to(page, wissen, limit=10)
    page.keyboard.press("Enter")
    expect(tijdlijn.get_by_role("gridcell")).to_have_count(PHOTOS)
    expect(filter_persoon).to_be_focused()
    assert not urlsplit(page.url).query
    # Alleen de getoonde personen: de verborgen pas als een gekozen persoon geen naam heeft (D)
    assert [urlsplit(u).query for u in personen_vragen] == ["hidden=0"], personen_vragen

    # --- een periode: een datum typen geeft één vraag, bij het verlaten van het veld (review
    # golf 2, web B; eerder één per cijfer van het jaar) ---
    vanaf = page.get_by_label("Vanaf", exact=True)
    tab_to(page, vanaf, limit=10)
    vragen: list[str] = []
    page.on("request", lambda r: vragen.append(r.url) if "/api/v1/photos/months" in r.url else None)
    page.keyboard.type("03032026")  # dag en maand gelijk: elke volgorde; de twee nieuwste foto's
    expect(vanaf).to_have_value("2026-03-03")
    assert vragen == []
    with page.expect_response(lambda r: "/api/v1/photos/months?" in r.url) as maanden:
        # In een datumveld gaat Tab eerst langs de delen (dag, maand, jaar); dan naar het
        # volgende veld (zoals in test_zoek_z4.py)
        for _ in range(4):
            if not vanaf.evaluate("(e) => e === document.activeElement"):
                break
            page.keyboard.press("Tab")
    assert parse_qs(urlsplit(maanden.value.url).query)["from"] == ["2026-03-03"]
    assert sum(m["count"] for m in maanden.value.json()["months"]) == 2
    expect(tijdlijn.get_by_role("gridcell")).to_have_count(2)
    expect(page.get_by_label("Tot en met", exact=True)).to_be_focused()
    assert len(vragen) == 1, vragen
    assert not urlsplit(page.url).query


def test_herinneringen_met_een_vaste_dag(server, fien, context_as):
    """De dag van de herinneringen komt van de test (?day=, een jaar na de nieuwste foto), nooit
    van de klok. Enter op een herinnering opent de viewer; "Deze dag verbergen" haalt hem weg."""
    nieuwste = server.call(fien, "GET", "/api/v1/photos", expect=200).json()["items"][0]
    d = nieuwste["taken_day"]
    dag = date(d // 10000, d // 100 % 100, d % 100)
    assert dag == date(2026, 5, 19)
    vandaag = dag.replace(year=dag.year + 1).isoformat()
    antwoord = server.call(
        fien, "GET", "/api/v1/photos/memories", params={"day": vandaag}, expect=200
    ).json()
    jaren = [(j["years_ago"], j["day"], [i["id"] for i in j["items"]]) for j in antwoord["years"]]
    assert jaren == [(1, dag.isoformat(), [nieuwste["id"]])]

    # page.route met een service worker ziet niet alles (Chrome 156): die staat hier uit
    context = context_as(fien, service_workers="block")

    def met_dag(route) -> None:
        url = urlsplit(route.request.url)
        if url.path == "/api/v1/photos/memories" and not url.query:
            route.continue_(url=f"{route.request.url}?day={vandaag}")
        else:
            route.continue_()

    context.route("**/api/v1/photos/memories*", met_dag)
    page = context.new_page()
    page.add_init_script(SCHUIVEN)
    vragen: list[str] = []
    page.on("request", lambda r: vragen.append(r.url) if "/photos/memories" in r.url else None)
    page.goto("/fotos")
    herinneringen = page.get_by_role("region", name="Herinneringen", exact=True)
    kop = herinneringen.get_by_role("heading", name="Vandaag, 1 jaar geleden", level=3)
    expect(kop).to_be_visible()
    expect(
        page.get_by_role("grid", name="Tijdlijn", exact=True).get_by_role("gridcell").first
    ).to_be_visible()
    # Filters en herinneringen kwamen met de tijdlijn: die schoof niet omlaag (review golf 2, F)
    assert schuiven(page) <= MAX_SCHUIVEN, page.evaluate("() => window.__schuiven")
    foto = herinneringen.get_by_role("button", name=re.compile(r"^foto-00000\.webp, "))
    # Het blok staat boven de tijdlijn en maakt die niet trager (e2e/perf/scroll.py): een jaar
    # buiten beeld kost niets, en een foto heeft een vaste maat zonder overflow: hidden (dat
    # maakt een beeld met afgeronde hoeken duur om te tekenen)
    stijl = foto.evaluate(
        "(b) => ({ jaar: getComputedStyle(b.closest('.memory-year')).contentVisibility,"
        " contain: getComputedStyle(b).contain, overflow: getComputedStyle(b).overflow })"
    )
    assert stijl == {"jaar": "auto", "contain": "strict", "overflow": "visible"}, stijl

    # Een filter verbergt het blok; zonder filter staat het er weer, zonder nieuwe vraag (review
    # golf 2, D: eerder vroeg het blok na Filters wissen alles opnieuw)
    soort = page.get_by_role("combobox", name="Soort", exact=True)
    tab_to(page, soort, limit=60)
    page.keyboard.press("ArrowDown")
    expect(soort).to_have_value("image")
    expect(herinneringen).to_have_count(0)
    page.keyboard.press("ArrowUp")
    expect(soort).to_have_value("")
    expect(kop).to_be_visible()
    assert len(vragen) == 1, vragen
    tab_to(page, foto, limit=60)
    page.keyboard.press("Enter")
    viewer = page.get_by_role("dialog", name="foto-00000.webp", exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    expect(foto).to_be_focused()

    verbergen = herinneringen.get_by_role("button", name="Deze dag verbergen", exact=True)
    page.keyboard.press("Shift+Tab")
    expect(verbergen).to_be_focused()
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith("/api/v1/photos/memories/hide")
    ) as verborgen:
        page.keyboard.press("Enter")
    assert verborgen.value.status in (200, 201, 204), verborgen.value.text()
    expect(toast(page, "Deze dag komt niet meer in je herinneringen.")).to_be_visible()
    expect(herinneringen).to_have_count(0)
    regels = server.call(fien, "GET", "/api/v1/photos/memories/hide", expect=200).json()["hides"]
    assert regels == [{"kind": "day", "ref": dag.isoformat()}]


def test_account_fotos_gezichten_en_beeld_nog_niet(server, user_factory, context_as):
    """Zonder X2 en X3 weigert de API gezichten en zoeken op beeld (409): de reden staat onder
    het vinkje, het vinkje blijft uit, en de rest van het blok werkt gewoon."""
    gebruiker = user_factory(name="Fien Instellingen")
    page = context_as(gebruiker).new_page()
    page.goto("/account")
    blok = page.get_by_role("region", name="Foto's", exact=True)
    gezichten = blok.get_by_label("Gezichten herkennen", exact=True)
    expect(gezichten).not_to_be_checked()
    tab_to(page, gezichten, limit=60)
    with page.expect_response(patch_instellingen) as r1:
        page.keyboard.press("Space")
    assert r1.value.status == 409, r1.value.text()
    code = r1.value.json()["detail"]["code"]
    assert code == "faces_unavailable"
    expect(blok.get_by_role("alert")).to_have_count(1)
    expect(gezichten).not_to_be_checked()

    beeld = blok.get_by_label("Zoeken op beeld", exact=True)
    tab_to(page, beeld, limit=5)
    with page.expect_response(patch_instellingen) as r2:
        page.keyboard.press("Space")
    assert r2.value.status == 409, r2.value.text()
    assert r2.value.json()["detail"]["code"] == "beeld_not_installed"
    expect(blok.get_by_role("alert")).to_have_count(2)
    expect(beeld).not_to_be_checked()

    # De rest van het blok werkt door: herinneringen uit en weer aan
    herinneringen = blok.get_by_label("Herinneringen tonen op Foto's", exact=True)
    expect(herinneringen).to_be_checked()
    tab_to(page, herinneringen, limit=5, back=True)
    with page.expect_response(patch_instellingen) as r3:
        page.keyboard.press("Space")
    assert r3.value.status == 200 and r3.value.json()["memories"] is False
    expect(herinneringen).not_to_be_checked()
    stand = server.call(gebruiker, "GET", "/api/v1/me/photo-settings", expect=200).json()
    assert (stand["faces"], stand["beeld"], stand["memories"]) == ("uit", "uit", False)


def test_de_knop_zoeken_op_beeld(fien, context_as):
    """Keuze 81: /fotos heeft de knop "Zoeken op beeld" naar /zoeken?mode=image; daar staat de
    focus in het veld (sinds de samenvoeging met de pagina van Z4), met de plaatshouder van de
    stand beeld."""
    page = context_as(fien).new_page()
    page.goto("/fotos")
    knop = page.get_by_role("link", name="Zoeken op beeld", exact=True)
    expect(knop).to_have_attribute("href", "/zoeken?mode=image")
    tab_to(page, knop, limit=60)
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/zoeken\?mode=image$"))
    veld = page.get_by_role("searchbox", name="Zoekwoorden", exact=True)
    expect(veld).to_be_focused()
    expect(veld).to_have_attribute(
        "placeholder", "Beschrijf de foto, bijvoorbeeld: hond op het strand"
    )
    expect(page.get_by_role("radio", name="Wat er op de foto staat", exact=True)).to_be_checked()
